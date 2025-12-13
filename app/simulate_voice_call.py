import json
import sys
from datetime import datetime

from app.pipeline.types import SessionState
from app.pipeline.stt_openai import OpenAISTT
from app.pipeline.tts_openai import OpenAITTS
from app.pipeline.dialog_openai import OpenAIDialogManager
from app.backend.db import (
    init_db,
    get_or_create_business,
    get_or_create_customer,
    create_call,
    end_call,
    update_call_customer,
    add_call_message,
    create_appointment,
    update_appointment,
)
from app.business.profile import load_business_profile


def print_banner() -> None:
    print("=== AI Voice Facilitator — Voice Call Simulator ===")
    print("Mode: AUTO-LISTEN")
    print("Instructions:")
    print("  - The system will automatically listen each turn.")
    print("  - Pause briefly to end your sentence.")
    print("  - Say 'bye' or 'goodbye' to end the call.")
    print("  - CTRL + C to force-exit.\n")


def _safe_float(x):
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x)
        except ValueError:
            return None
    return None


def _normalize_unspecified(v):
    if v is None:
        return None
    if isinstance(v, str) and v.strip().lower() == "unspecified":
        return None
    return v


def run_voice_call() -> None:
    init_db()
    business_profile = load_business_profile()
    business_id = get_or_create_business(business_profile)

    session = SessionState()
    stt = OpenAISTT()
    tts = OpenAITTS()
    dialog = OpenAIDialogManager()

    call_id = create_call(
        session_id=session.session_id,
        business_id=business_id,
        customer_id=None,
        started_at=datetime.utcnow(),
        raw_meta=None,
        channel="phone",
    )

    print_banner()

    business_name = business_profile.name
    greeting = f"Hello, you’ve reached {business_name}. How can I help you today?"

    session.add_message("assistant", greeting)
    print(f"Agent: {greeting}")
    tts.speak(greeting)

    turn_index = 0
    add_call_message(
        call_id=call_id,
        turn_index=turn_index,
        role="assistant",
        text=greeting,
        intent=None,
        entities_json=None,
        timestamp=datetime.utcnow(),
    )
    turn_index += 1

    print("\n[STT] Listening...")

    while True:
        transcript = stt.record_and_transcribe()
        if not transcript:
            print("[Simulator] No speech detected. Listening again...")
            continue

        print(f"Caller (STT): {transcript}")

        lowered = transcript.lower().strip()
        if lowered in ("bye", "goodbye", "thanks, bye", "thank you, bye"):
            add_call_message(
                call_id=call_id,
                turn_index=turn_index,
                role="user",
                text=transcript,
                intent="end_call",
                entities_json=json.dumps({}, ensure_ascii=False),
                timestamp=datetime.utcnow(),
            )

            farewell = f"Thank you for calling {business_name}. Have a great day!"
            session.add_message("assistant", farewell)
            print(f"Agent: {farewell}")
            tts.speak(farewell)

            turn_index += 1
            add_call_message(
                call_id=call_id,
                turn_index=turn_index,
                role="assistant",
                text=farewell,
                intent="end_call",
                entities_json=json.dumps({}, ensure_ascii=False),
                timestamp=datetime.utcnow(),
            )
            break

        reply, nlu = dialog.handle_user_utterance(session, transcript)
        entities = nlu.entities or {}

        # Extract analytics fields
        service_name = _normalize_unspecified(entities.get("service"))
        amount = _safe_float(_normalize_unspecified(entities.get("price_estimated") or entities.get("amount")))
        currency = _normalize_unspecified(entities.get("currency"))
        sentiment = _normalize_unspecified(entities.get("sentiment"))

        entities_json = json.dumps(entities, ensure_ascii=False)

        # Log user message with extraction fields
        add_call_message(
            call_id=call_id,
            turn_index=turn_index,
            role="user",
            text=transcript,
            intent=nlu.intent,
            entities_json=entities_json,
            timestamp=datetime.utcnow(),
            service_name=service_name if isinstance(service_name, str) else None,
            amount=amount,
            currency=currency if isinstance(currency, str) else None,
            sentiment=sentiment if isinstance(sentiment, str) else None,
        )

        # If we captured phone, create/attach customer now (so appointments can link)
        customer_id = None
        if session.customer_phone:
            customer_id = get_or_create_customer(
                business_id=business_id,
                phone=session.customer_phone,
                name=session.customer_name,
            )
            update_call_customer(call_id=call_id, customer_id=customer_id)

        # ---- Booking capture logic ----
        # We create a PENDING appointment when enough fields exist.
        # Then, if user confirms, we update it to CONFIRMED.
        booking_type = _normalize_unspecified(entities.get("booking_type"))
        preferred_staff = _normalize_unspecified(entities.get("preferred_staff"))
        date = _normalize_unspecified(entities.get("date"))
        time = _normalize_unspecified(entities.get("time"))
        confirmation = _normalize_unspecified(entities.get("confirmation"))

        # Infer booking_type from intent if model didn’t provide it
        if not booking_type:
            if nlu.intent == "schedule_appointment":
                booking_type = "NEW"
            elif nlu.intent == "reschedule_appointment":
                booking_type = "RESCHEDULE"
            elif nlu.intent == "cancel_appointment":
                booking_type = "CANCELLATION"

        # Create / update appointment
        if customer_id and isinstance(service_name, str) and isinstance(date, str) and isinstance(time, str):
            if session.appointment_id is None and nlu.intent in ("schedule_appointment", "reschedule_appointment"):
                session.appointment_id = create_appointment(
                    business_id=business_id,
                    customer_id=customer_id,
                    call_id=call_id,
                    service_name=service_name,
                    appointment_date=date,
                    appointment_time=time,
                    booking_type=booking_type,
                    channel="phone",
                    price_estimated=amount,
                    currency=currency if isinstance(currency, str) else None,
                    preferred_staff=preferred_staff if isinstance(preferred_staff, str) else None,
                    notes=None,
                    status="PENDING",
                    source="assistant",
                    service_code=None,
                )
            elif session.appointment_id is not None:
                update_appointment(
                    session.appointment_id,
                    service_name=service_name,
                    appointment_date=date,
                    appointment_time=time,
                    booking_type=booking_type,
                    price_estimated=amount if amount is not None else None,
                    currency=currency if isinstance(currency, str) else None,
                    preferred_staff=preferred_staff if isinstance(preferred_staff, str) else None,
                )

            # Confirm if the user confirmed
            if session.appointment_id is not None and isinstance(confirmation, str) and confirmation == "yes":
                update_appointment(session.appointment_id, status="CONFIRMED")

        # ---- Speak + log assistant reply ----
        print(f"[Intent: {nlu.intent} (conf={nlu.confidence:.2f})]")
        print(f"Agent: {reply}")
        tts.speak(reply)

        turn_index += 1
        add_call_message(
            call_id=call_id,
            turn_index=turn_index,
            role="assistant",
            text=reply,
            intent=None,
            entities_json=None,
            timestamp=datetime.utcnow(),
        )

        if nlu.intent == "end_call":
            break

        turn_index += 1
        print("\n[STT] Listening...")

    ended = datetime.utcnow()
    num_turns = len(session.messages)
    total_duration_sec = None
    if session.messages:
        started = session.messages[0].timestamp
        total_duration_sec = int((ended - started).total_seconds())

    end_call(
        call_id=call_id,
        ended_at=ended,
        outcome=None,
        primary_intent=None,
        primary_service=None,
        total_duration_sec=total_duration_sec,
        num_turns=num_turns,
        total_estimated_value=None,
    )

    print("\n=== Call ended ===")
    print(f"Session ID: {session.session_id}")
    print(f"Captured name:  {session.customer_name or 'N/A'}")
    print(f"Captured phone: {session.customer_phone or 'N/A'}")
    print(f"DB business_id: {business_id}")
    print(f"DB call_id:     {call_id}")
    if session.appointment_id:
        print(f"DB appointment_id: {session.appointment_id}")


if __name__ == "__main__":
    try:
        run_voice_call()
    except KeyboardInterrupt:
        print("\nInterrupted by user, exiting.")
        sys.exit(0)