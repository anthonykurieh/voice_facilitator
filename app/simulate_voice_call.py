import uuid
from datetime import date as _date

from app.pipeline.types import SessionState
from app.pipeline.stt_openai import OpenAISTT
from app.pipeline.tts_openai import OpenAITTS
from app.pipeline.dialog_openai import OpenAIDialogManager

from app.backend.calendar_utils import resolve_date, parse_time_to_hhmm
from app.backend.availability import find_earliest_availability, check_slot_and_suggest

from app.backend.db import (
    log_call_start,
    log_call_end,
    log_message,
    upsert_customer,
    create_appointment,
    get_or_create_business,
)

BUSINESS_NAME = "Downtown Barber Shop"


def _is_unspecified(v: str) -> bool:
    return not v or str(v).strip().lower() == "unspecified"


def run_voice_call():
    print("=== AI Voice Facilitator — Voice Call Simulator ===")
    print("Instructions:")
    print("  - Speak naturally. The mic will record until a pause.")
    print("  - Say 'bye' or 'goodbye' to end the call.\n")

    stt = OpenAISTT()
    tts = OpenAITTS()
    dialog_manager = OpenAIDialogManager()

    session = SessionState(session_id=str(uuid.uuid4()))

    # ✅ Ensure business exists and we have the correct business_id
    business_id = get_or_create_business(BUSINESS_NAME)

    # ✅ Log call with business_id (so FK relations stay consistent)
    call_id = log_call_start(session.session_id, business_id=business_id)

    # Conversation state
    customer_id = None
    customer_name = None
    phone_number = None

    pending_date_human_prompt = None
    pending_proposed_slot = None  # {service, date_iso, time_hhmm, staff_id, staff_name}

    greeting = "Hello! Thanks for calling. Before we get started, can I have your name and phone number?"
    print(f"Agent: {greeting}")
    log_message(call_id, "assistant", greeting)
    tts.speak(greeting)

    while True:
        user_text = stt.record_and_transcribe()
        if not user_text:
            continue

        print(f"Caller (STT): {user_text}")
        log_message(call_id, "user", user_text)

        low = user_text.lower().strip()
        if low in {"q", "quit", "bye", "goodbye"}:
            goodbye = "You’re welcome. Have a great day!"
            print(f"Agent: {goodbye}")
            log_message(call_id, "assistant", goodbye)
            tts.speak(goodbye)
            break

        # If we recently asked for date clarification, let the new turn proceed normally
        if pending_date_human_prompt:
            pending_date_human_prompt = None

        reply, nlu = dialog_manager.handle_user_utterance(session, user_text)
        intent = nlu.intent
        entities = nlu.entities or {}

        # 1) Identity gating
        if customer_id is None:
            extracted_name = entities.get("customer_name", "unspecified")
            extracted_phone = entities.get("phone_number", "unspecified")

            if _is_unspecified(extracted_name) and len(user_text.split()) <= 3:
                extracted_name = user_text.strip()

            if not _is_unspecified(extracted_phone):
                phone_number = extracted_phone.strip()
            if not _is_unspecified(extracted_name):
                customer_name = extracted_name.strip()

            if not customer_name or not phone_number:
                ask = "Thanks — could you please tell me your name and phone number to proceed?"
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask)
                tts.speak(ask)
                continue

            # ✅ This will now work because the business exists
            customer_id = upsert_customer(business_id, customer_name, phone_number)

            ok = f"Perfect, thanks {customer_name}. How can I help you today?"
            print(f"Agent: {ok}")
            log_message(call_id, "assistant", ok)
            tts.speak(ok)
            continue

        # 2) Availability intent
        if intent == "check_availability":
            service = entities.get("service", "unspecified")
            date_text = entities.get("date", "unspecified")
            preferred_staff = entities.get("preferred_staff", "unspecified")
            if _is_unspecified(preferred_staff):
                preferred_staff = None

            if _is_unspecified(service):
                ask = "Sure — what service are you looking for? For example: haircut, beard trim, or fade."
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask)
                tts.speak(ask)
                continue

            if _is_unspecified(date_text):
                ask = "Got it. What day would you like to come in?"
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask)
                tts.speak(ask)
                continue

            res = resolve_date(date_text, today=_date.today())
            if res.is_ambiguous:
                print(f"Agent: {res.clarification_prompt}")
                log_message(call_id, "assistant", res.clarification_prompt)
                tts.speak(res.clarification_prompt)
                pending_date_human_prompt = res.clarification_prompt
                continue

            date_iso = res.resolved_date
            result = find_earliest_availability(
                business_id=business_id,
                service_name=service,
                date_str=date_iso,
                preferred_staff=preferred_staff,
            )

            if not result.ok:
                msg = "I’m sorry — we don’t have any availability on that day. Would you like to try another date?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg)
                tts.speak(msg)
                continue

            pending_proposed_slot = {
                "service": service,
                "date_iso": date_iso,
                "time_hhmm": result.start_hhmm,
                "staff_id": result.staff_id,
                "staff_name": result.staff_name,
            }

            msg = (
                f"The earliest available slot for a {service} is {result.start_hhmm} with {result.staff_name} "
                f"on {date_iso}. Would you like me to book that?"
            )
            print(f"Agent: {msg}")
            log_message(call_id, "assistant", msg)
            tts.speak(msg)
            continue

        # 3) Scheduling intent
        if intent == "schedule_appointment":
            service = entities.get("service", "unspecified")
            date_text = entities.get("date", "unspecified")
            time_text = entities.get("time", "unspecified")
            preferred_staff = entities.get("preferred_staff", "unspecified")
            booking_type = entities.get("booking_type", "unspecified")
            if _is_unspecified(preferred_staff):
                preferred_staff = None

            # Confirm a previously proposed slot
            if pending_proposed_slot and any(x in low for x in ["yes", "confirm", "book it", "okay", "sure"]):
                appt = pending_proposed_slot
                create_appointment(
                    business_id=business_id,
                    customer_id=customer_id,
                    service_name=appt["service"],
                    booking_type="appointment",
                    appointment_date=appt["date_iso"],
                    appointment_time=appt["time_hhmm"],
                    staff_id=appt["staff_id"],
                )
                pending_proposed_slot = None

                msg = f"All set — you’re booked for a {appt['service']} at {appt['time_hhmm']} with {appt['staff_name']} on {appt['date_iso']}. Anything else?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg)
                tts.speak(msg)
                continue

            if _is_unspecified(service):
                ask = "Sure — what service would you like to book? For example: haircut, beard trim, or fade."
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask)
                tts.speak(ask)
                continue

            if _is_unspecified(date_text):
                ask = "What day would you like to come in?"
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask)
                tts.speak(ask)
                continue

            res = resolve_date(date_text, today=_date.today())
            if res.is_ambiguous:
                print(f"Agent: {res.clarification_prompt}")
                log_message(call_id, "assistant", res.clarification_prompt)
                tts.speak(res.clarification_prompt)
                pending_date_human_prompt = res.clarification_prompt
                continue

            date_iso = res.resolved_date

            if _is_unspecified(time_text):
                earliest = find_earliest_availability(business_id, service, date_iso, preferred_staff)
                if earliest.ok:
                    pending_proposed_slot = {
                        "service": service,
                        "date_iso": date_iso,
                        "time_hhmm": earliest.start_hhmm,
                        "staff_id": earliest.staff_id,
                        "staff_name": earliest.staff_name,
                    }
                    msg = (
                        f"What time works for you? If you want, the earliest slot is {earliest.start_hhmm} with "
                        f"{earliest.staff_name}. Would you like that one?"
                    )
                else:
                    msg = "What time would you like? For example: 4 PM or 16:30."
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg)
                tts.speak(msg)
                continue

            time_hhmm = parse_time_to_hhmm(time_text)
            if not time_hhmm:
                ask = "Sorry — what time did you mean? For example: 4 PM or 16:30."
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask)
                tts.speak(ask)
                continue

            result = check_slot_and_suggest(
                business_id=business_id,
                service_name=service,
                date_str=date_iso,
                time_hhmm=time_hhmm,
                preferred_staff=preferred_staff,
                max_alternatives=3,
            )

            if result.ok:
                pending_proposed_slot = {
                    "service": service,
                    "date_iso": date_iso,
                    "time_hhmm": time_hhmm,
                    "staff_id": result.staff_id,
                    "staff_name": result.staff_name,
                }
                msg = f"Yes — I can do {time_hhmm} on {date_iso} with {result.staff_name}. Would you like to confirm?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg)
                tts.speak(msg)
                continue

            if result.alternatives:
                parts = [f"{a['time']} with {a['staff_name']}" for a in result.alternatives]
                msg = "I’m sorry — that time is taken. I can offer " + ", or ".join(parts) + ". Which one works for you?"
            else:
                msg = "I’m sorry — that time is taken, and I don’t see other open slots that day. Would you like another day?"
            print(f"Agent: {msg}")
            log_message(call_id, "assistant", msg)
            tts.speak(msg)
            continue

        # Default response
        print(f"Agent: {reply}")
        log_message(call_id, "assistant", reply)
        tts.speak(reply)

    log_call_end(call_id)
    print("\n=== Call ended ===")
    print(f"Session ID: {session.session_id}")


if __name__ == "__main__":
    run_voice_call()