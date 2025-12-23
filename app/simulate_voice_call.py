import re
import uuid
from datetime import date as _date, datetime

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
    get_service_by_name_or_code,
)


BUSINESS_NAME = "Downtown Barber Shop"


def _is_unspecified(v: str) -> bool:
    return not v or str(v).strip().lower() == "unspecified"


def _user_wants_hangup(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(x in t for x in ["bye", "goodbye", "hang up", "end the call", "that's all", "no thanks bye"])


def run_voice_call():
    print("=== AI Voice Facilitator — Voice Call Simulator ===")
    print("Instructions:")
    print("  - Speak naturally. The mic will record until a pause.")
    print("  - Say 'bye' or 'goodbye' to end the call.\n")

    stt = OpenAISTT()
    tts = OpenAITTS()
    dialog_manager = OpenAIDialogManager()

    session = SessionState(session_id=str(uuid.uuid4()))
    business_id = get_or_create_business(BUSINESS_NAME)
    call_id = log_call_start(session.session_id, business_id=business_id)

    customer_id = None
    customer_name = None
    phone_number = None

    pending_date_clarification = False
    pending_proposed_slot = None  # dict with service/date/time/staff

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

        if _user_wants_hangup(user_text):
            goodbye = "Perfect — thanks for calling. Have a great day!"
            print(f"Agent: {goodbye}")
            log_message(call_id, "assistant", goodbye, intent="end_call", confidence=1.0, entities={})
            tts.speak(goodbye)
            break

        reply, nlu = dialog_manager.handle_user_utterance(session, user_text)
        intent = getattr(nlu, "intent", "fallback")
        conf = float(getattr(nlu, "confidence", 0.0))
        entities = nlu.entities or {}

        # ✅ always log NLU metadata safely
        log_message(call_id, "assistant", reply, intent=intent, confidence=conf, entities=entities)

        # -----------------------------
        # 1) Identity gating
        # -----------------------------
        if customer_id is None:
            extracted_name = entities.get("customer_name", "unspecified")
            extracted_phone = entities.get("phone_number", "unspecified")

            if not _is_unspecified(extracted_name):
                customer_name = str(extracted_name).strip()
            if not _is_unspecified(extracted_phone):
                phone_number = str(extracted_phone).strip()

            if not customer_name or not phone_number:
                ask = "Thanks — I just need your name and phone number to proceed."
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask, intent="identify_customer", confidence=1.0, entities={})
                tts.speak(ask)
                continue

            customer_id = upsert_customer(business_id, customer_name, phone_number)

            ok = f"Perfect, thanks {customer_name}. How can I help you today?"
            print(f"Agent: {ok}")
            log_message(call_id, "assistant", ok, intent="identify_customer", confidence=1.0, entities={})
            tts.speak(ok)
            continue

        # -----------------------------
        # 2) Confirm previously proposed slot
        # -----------------------------
        low = user_text.lower()
        if pending_proposed_slot and re.search(r"\b(yes|confirm|book|okay|sure|do it)\b", low):
            appt = pending_proposed_slot

            svc = get_service_by_name_or_code(business_id, appt["service"])
            if not svc:
                msg = "Sorry — I can’t find that service in our system. Which service did you want?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent="schedule_appointment", confidence=1.0, entities={})
                tts.speak(msg)
                pending_proposed_slot = None
                continue

            start_dt = datetime.strptime(f"{appt['date_iso']} {appt['time_hhmm']}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + __import__("datetime").timedelta(minutes=int(svc["duration_min"]))

            create_appointment(
                business_id=business_id,
                customer_id=customer_id,
                staff_id=int(appt["staff_id"]),
                service_id=int(svc["id"]),
                booking_type="phone",
                start_time=start_dt,
                end_time=end_dt,
                quoted_price=float(svc["price"]),
                currency=str(svc["currency"]),
                notes=None,
            )

            pending_proposed_slot = None
            msg = f"All set — you’re booked for a {svc['name']} at {appt['time_hhmm']} with {appt['staff_name']} on {appt['date_iso']}. Anything else?"
            print(f"Agent: {msg}")
            log_message(call_id, "assistant", msg, intent="schedule_appointment", confidence=1.0, entities={})
            tts.speak(msg)
            continue

        # -----------------------------
        # 3) Check availability
        # -----------------------------
        if intent == "check_availability":
            service = entities.get("service", "unspecified")
            date_text = entities.get("date", "unspecified")

            if _is_unspecified(service):
                ask = "Sure — which service are you checking for? For example: haircut, fade, beard trim, or combo."
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask, intent=intent, confidence=1.0, entities={})
                tts.speak(ask)
                continue

            if _is_unspecified(date_text):
                ask = "Which day did you have in mind?"
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask, intent=intent, confidence=1.0, entities={})
                tts.speak(ask)
                continue

            res = resolve_date(date_text, today=_date.today())
            if res.is_ambiguous:
                pending_date_clarification = True
                print(f"Agent: {res.clarification_prompt}")
                log_message(call_id, "assistant", res.clarification_prompt, intent=intent, confidence=1.0, entities={})
                tts.speak(res.clarification_prompt)
                continue

            date_iso = res.resolved_date
            if date_iso < _date.today().isoformat():
                msg = "That date already passed — what date would you like instead?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=1.0, entities={})
                tts.speak(msg)
                continue

            earliest = find_earliest_availability(business_id, service, date_iso)
            if not earliest.ok:
                msg = "I’m sorry — we don’t have any availability on that day. Want to try another date?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=1.0, entities={})
                tts.speak(msg)
                continue

            pending_proposed_slot = {
                "service": service,
                "date_iso": date_iso,
                "time_hhmm": earliest.alternatives[0]["time"],
                "staff_id": earliest.alternatives[0]["staff_id"],
                "staff_name": earliest.alternatives[0]["staff_name"],
            }

            msg = f"The earliest slot for {service} is {pending_proposed_slot['time_hhmm']} with {pending_proposed_slot['staff_name']} on {date_iso}. Want me to book it?"
            print(f"Agent: {msg}")
            log_message(call_id, "assistant", msg, intent=intent, confidence=1.0, entities={})
            tts.speak(msg)
            continue

        # -----------------------------
        # 4) Schedule appointment (THE IMPORTANT ONE)
        # -----------------------------
        if intent == "schedule_appointment":
            service = entities.get("service", "unspecified")
            date_text = entities.get("date", "unspecified")
            time_text = entities.get("time", "unspecified")

            # If the user already said "haircut tomorrow at 10" → do NOT ask again.
            if _is_unspecified(service):
                ask = "Which service did you want exactly? For example: haircut, fade, beard trim, or combo."
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask, intent=intent, confidence=1.0, entities={})
                tts.speak(ask)
                continue

            if _is_unspecified(date_text):
                ask = "What day would you like to come in?"
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask, intent=intent, confidence=1.0, entities={})
                tts.speak(ask)
                continue

            res = resolve_date(date_text, today=_date.today())
            if res.is_ambiguous:
                pending_date_clarification = True
                print(f"Agent: {res.clarification_prompt}")
                log_message(call_id, "assistant", res.clarification_prompt, intent=intent, confidence=1.0, entities={})
                tts.speak(res.clarification_prompt)
                continue

            date_iso = res.resolved_date
            if date_iso < _date.today().isoformat():
                msg = "That date already passed — what date would you like instead?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=1.0, entities={})
                tts.speak(msg)
                continue

            if _is_unspecified(time_text):
                earliest = find_earliest_availability(business_id, service, date_iso)
                if earliest.ok and earliest.alternatives:
                    s = earliest.alternatives[0]
                    pending_proposed_slot = {
                        "service": service,
                        "date_iso": date_iso,
                        "time_hhmm": s["time"],
                        "staff_id": s["staff_id"],
                        "staff_name": s["staff_name"],
                    }
                    msg = f"What time works for you? The earliest slot is {s['time']} with {s['staff_name']}. Want me to book that?"
                else:
                    msg = "What time would you like? For example: 4 PM or 16:30."
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=1.0, entities={})
                tts.speak(msg)
                continue

            time_hhmm = parse_time_to_hhmm(time_text)
            if not time_hhmm:
                ask = "Sorry — what time did you mean? For example: 4 PM or 16:30."
                print(f"Agent: {ask}")
                log_message(call_id, "assistant", ask, intent=intent, confidence=1.0, entities={})
                tts.speak(ask)
                continue

            # ✅ ALWAYS check the DB for overlap
            result = check_slot_and_suggest(
                business_id=business_id,
                service_name=service,
                date_str=date_iso,
                time_hhmm=time_hhmm,
                preferred_staff=None,
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
                msg = f"Yes — {time_hhmm} on {date_iso} is available with {result.staff_name}. Want me to confirm the booking?"
                print(f"Agent: {msg}")
                log_message(call_id, "assistant", msg, intent=intent, confidence=1.0, entities={})
                tts.speak(msg)
                continue

            if result.alternatives:
                parts = [f"{a['time']} with {a['staff_name']}" for a in result.alternatives]
                msg = "That time is taken. I can do " + ", or ".join(parts) + ". Which one works?"
            else:
                msg = "That time is taken and I don’t see other open slots that day. Want another day?"
            print(f"Agent: {msg}")
            log_message(call_id, "assistant", msg, intent=intent, confidence=1.0, entities={})
            tts.speak(msg)
            continue

        # -----------------------------
        # default: just speak model reply
        # -----------------------------
        print(f"Agent: {reply}")
        tts.speak(reply)

    log_call_end(call_id)
    print("\n=== Call ended ===")
    print(f"Session ID: {session.session_id}")


if __name__ == "__main__":
    run_voice_call()