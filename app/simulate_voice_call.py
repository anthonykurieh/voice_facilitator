import re
import uuid
from datetime import datetime, timedelta, date as _date

from app.pipeline.types import SessionState
from app.pipeline.stt_openai import OpenAISTT
from app.pipeline.tts_openai import OpenAITTS
from app.pipeline.dialog_openai import OpenAIDialogManager

from app.backend.calendar_utils import resolve_date, parse_time_to_hhmm
from app.backend.availability import find_earliest_availability, check_slot_and_suggest
from app.backend.db import (
    init_db,
    get_or_create_business,
    upsert_customer,
    log_call_start,
    log_call_end,
    log_message,
    get_service_by_name_or_code,
    create_appointment,
)

BUSINESS_NAME = "Downtown Barber Shop"


def _is_unspecified(v: str) -> bool:
    return not v or str(v).strip().lower() == "unspecified"


def _user_wants_hangup(text: str) -> bool:
    low = (text or "").lower()
    # catches: bye, bye bye, goodbye, ok bye, etc.
    return bool(re.search(r"\b(bye|goodbye|hang up|end call)\b", low))


def _user_confirms(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in ["yes", "yep", "yeah", "confirm", "book it", "okay", "sure", "please do", "go ahead"])


def run_voice_call():
    print("=== AI Voice Facilitator — Voice Call Simulator ===")
    print("Instructions:")
    print("  - Speak naturally. The mic will record until a pause.")
    print("  - Say 'bye' or 'goodbye' to end the call.\n")

    # ensure schema exists
    init_db()

    stt = OpenAISTT()
    tts = OpenAITTS()
    dialog_manager = OpenAIDialogManager()

    session = SessionState(session_id=str(uuid.uuid4()))
    business_id = get_or_create_business(BUSINESS_NAME)

    call_id = log_call_start(session.session_id, business_id=business_id)

    customer_id = None
    customer_name = None
    phone_number = None

    pending_proposed_slot = None  # dict with service_id/service_name/date_iso/time_hhmm/staff_id/staff_name/price/currency

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

        # Hangup detection (don’t require exact match)
        if _user_wants_hangup(user_text):
            goodbye = "You’re welcome. Have a great day!"
            print(f"Agent: {goodbye}")
            log_message(call_id, "assistant", goodbye)
            tts.speak(goodbye)
            break

        # If we have a pending slot, allow confirmation even if NLU intent is wrong
        if pending_proposed_slot and _user_confirms(user_text) and customer_id is not None:
            appt = pending_proposed_slot
            start_dt = datetime.fromisoformat(f"{appt['date_iso']} {appt['time_hhmm']}:00")
            end_dt = start_dt + timedelta(minutes=appt["duration_min"])

            create_appointment(
                business_id=business_id,
                customer_id=customer_id,
                staff_id=appt["staff_id"],
                service_id=appt["service_id"],
                booking_type="phone",
                start_time=start_dt,
                end_time=end_dt,
                quoted_price=float(appt["price"]),
                currency=str(appt["currency"]),
                notes=None,
            )
            pending_proposed_slot = None

            msg = (
                f"All set — you’re booked for a {appt['service_name']} at {appt['time_hhmm']} "
                f"with {appt['staff_name']} on {appt['date_iso']}. Anything else?"
            )
            print(f"Agent: {msg}")
            log_message(call_id, "assistant", msg)
            tts.speak(msg)
            continue

        reply, nlu = dialog_manager.handle_user_utterance(session, user_text)
        intent = nlu.intent
        entities = nlu.entities or {}

        # Identity gating
        if customer_id is None:
            extracted_name = entities.get("customer_name", "unspecified")
            extracted_phone = entities.get("phone_number", "unspecified")

            # very simple fallback
            if _is_unspecified(extracted_name) and len(user_text.split()) <= 3:
                extracted_name = user_text.strip()

            if not _is_unspecified(extracted_phone):
                phone_number = str(extracted_phone).strip()
            if not _is_unspecified(extracted_name):
                customer_name = str(extracted_name).strip()

            if not customer_name or not phone_number:
                ask = "Thanks — could you please tell me your name and phone number to proceed?"
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask)
                tts.speak(ask)
                continue

            customer_id = upsert_customer(business_id, customer_name, phone_number)

            ok = f"Perfect, thanks {customer_name}. How can I help you today?"
            print(f"Agent: {ok}")
            log_message(call_id, "assistant", ok)
            tts.speak(ok)
            continue

        # Handle availability checks (earliest slot)
        if intent == "check_availability":
            service_text = entities.get("service", "unspecified")
            date_text = entities.get("date", "unspecified")
            preferred_staff = entities.get("preferred_staff", "unspecified")
            preferred_staff = None if _is_unspecified(preferred_staff) else str(preferred_staff)

            if _is_unspecified(service_text):
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

            res = resolve_date(str(date_text), today=_date.today())
            if res.is_ambiguous:
                print(f"Agent: {res.clarification_prompt}")
                log_message(call_id, "assistant", res.clarification_prompt)
                tts.speak(res.clarification_prompt)
                continue

            date_iso = res.resolved_date

            # past-date guard (calendar behavior)
            if _date.fromisoformat(date_iso) < _date.today():
                msg = "That date has already passed. What future date would you like instead?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg)
                tts.speak(msg)
                continue

            earliest = find_earliest_availability(
                business_id=business_id,
                service_name=str(service_text),
                date_str=date_iso,
                preferred_staff=preferred_staff,
                now=datetime.now(),
            )

            if not earliest.ok:
                msg = "I’m sorry — we don’t have any availability on that day. Would you like to try another date?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg)
                tts.speak(msg)
                continue

            svc = get_service_by_name_or_code(business_id, str(service_text))
            if not svc:
                msg = "I can book that — which service did you want exactly?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg)
                tts.speak(msg)
                continue

            pending_proposed_slot = {
                "service_id": int(svc["id"]),
                "service_name": str(svc["name"]),
                "duration_min": int(svc["duration_min"]),
                "price": float(svc["price"]),
                "currency": str(svc["currency"]),
                "date_iso": date_iso,
                "time_hhmm": str(earliest.start_hhmm),
                "staff_id": int(earliest.staff_id),
                "staff_name": str(earliest.staff_name),
            }

            msg = (
                f"The earliest available slot for a {pending_proposed_slot['service_name']} is "
                f"{pending_proposed_slot['time_hhmm']} with {pending_proposed_slot['staff_name']} on {date_iso}. "
                f"Would you like me to book that?"
            )
            print(f"Agent: {msg}")
            log_message(call_id, "assistant", msg)
            tts.speak(msg)
            continue

        # Handle scheduling
        if intent == "schedule_appointment":
            service_text = entities.get("service", "unspecified")
            date_text = entities.get("date", "unspecified")
            time_text = entities.get("time", "unspecified")
            preferred_staff = entities.get("preferred_staff", "unspecified")
            preferred_staff = None if _is_unspecified(preferred_staff) else str(preferred_staff)

            if _is_unspecified(service_text):
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

            res = resolve_date(str(date_text), today=_date.today())
            if res.is_ambiguous:
                print(f"Agent: {res.clarification_prompt}")
                log_message(call_id, "assistant", res.clarification_prompt)
                tts.speak(res.clarification_prompt)
                continue

            date_iso = res.resolved_date

            # past-date guard
            if _date.fromisoformat(date_iso) < _date.today():
                msg = "That date has already passed. What future date would you like instead?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg)
                tts.speak(msg)
                continue

            svc = get_service_by_name_or_code(business_id, str(service_text))
            if not svc:
                msg = "Which service did you want exactly? For example: haircut, fade, beard trim, or combo."
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg)
                tts.speak(msg)
                continue

            if _is_unspecified(time_text):
                earliest = find_earliest_availability(
                    business_id=business_id,
                    service_name=str(service_text),
                    date_str=date_iso,
                    preferred_staff=preferred_staff,
                    now=datetime.now(),
                )
                if earliest.ok:
                    pending_proposed_slot = {
                        "service_id": int(svc["id"]),
                        "service_name": str(svc["name"]),
                        "duration_min": int(svc["duration_min"]),
                        "price": float(svc["price"]),
                        "currency": str(svc["currency"]),
                        "date_iso": date_iso,
                        "time_hhmm": str(earliest.start_hhmm),
                        "staff_id": int(earliest.staff_id),
                        "staff_name": str(earliest.staff_name),
                    }
                    msg = (
                        f"What time works for you? If you want, the earliest slot is "
                        f"{pending_proposed_slot['time_hhmm']} with {pending_proposed_slot['staff_name']}. "
                        f"Would you like that one?"
                    )
                else:
                    msg = "What time would you like? For example: 4 PM or 16:30."
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg)
                tts.speak(msg)
                continue

            time_hhmm = parse_time_to_hhmm(str(time_text))
            if not time_hhmm:
                ask = "Sorry — what time did you mean? For example: 4 PM or 16:30."
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask)
                tts.speak(ask)
                continue

            result = check_slot_and_suggest(
                business_id=business_id,
                service_name=str(service_text),
                date_str=date_iso,
                time_hhmm=time_hhmm,
                preferred_staff=preferred_staff,
                max_alternatives=3,
                now=datetime.now(),
            )

            if result.ok:
                pending_proposed_slot = {
                    "service_id": int(svc["id"]),
                    "service_name": str(svc["name"]),
                    "duration_min": int(svc["duration_min"]),
                    "price": float(svc["price"]),
                    "currency": str(svc["currency"]),
                    "date_iso": date_iso,
                    "time_hhmm": time_hhmm,
                    "staff_id": int(result.staff_id),
                    "staff_name": str(result.staff_name),
                }
                msg = (
                    f"Yes — I can do {time_hhmm} on {date_iso} with {pending_proposed_slot['staff_name']}. "
                    f"Would you like to confirm?"
                )
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

        # default
        print(f"Agent: {reply}")
        log_message(call_id, "assistant", reply, intent=intent, confidence=float(getattr(nlu, "confidence", 0.0)), entities=entities)
        tts.speak(reply)

    log_call_end(call_id)
    print("\n=== Call ended ===")
    print(f"Session ID: {session.session_id}")


if __name__ == "__main__":
    run_voice_call()