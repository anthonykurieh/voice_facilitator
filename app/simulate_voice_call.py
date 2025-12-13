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
)
from app.business.profile import load_business_profile


def print_banner() -> None:
    print("=== AI Voice Facilitator — Voice Call Simulator ===")
    print("Mode: AUTO-LISTEN")
    print("Instructions:")
    print("  - The system will automatically listen for your voice each turn.")
    print("  - Speak after the agent finishes talking; pause to let it detect silence.")
    print("  - Say 'bye' or 'goodbye' to end the call.")
    print("  - Press CTRL + C at any time to force-exit.\n")


def run_voice_call() -> None:
    # ---------- DB & business setup ----------
    init_db()
    business_profile = load_business_profile()
    business_id = get_or_create_business(business_profile)

    # ---------- Session & components ----------
    session = SessionState()
    stt = OpenAISTT()
    tts = OpenAITTS()
    dialog = OpenAIDialogManager()

    # Create DB call record
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

    # In-memory log
    session.add_message("assistant", greeting)
    print(f"Agent: {greeting}")
    tts.speak(greeting)

    # Log greeting as first message
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

    # Next user message index
    turn_index += 1

    print("\n[STT] Listening... (start speaking when you're ready)")

    # ---------- Main auto-listen loop ----------
    while True:
        # 1) Listen for the caller (record until silence)
        transcript = stt.record_and_transcribe()
        if not transcript:
            print("[Simulator] No speech detected. Waiting again...")
            continue

        print(f"Caller (STT): {transcript}")
        session.add_message("user", transcript)

        lowered = transcript.lower().strip()

        # Fast-path: user explicitly ends the call verbally
        if lowered in ("bye", "goodbye", "thanks, bye", "thank you, bye"):
            # Log user goodbye
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

        # 2) NLU + dialog: get reply + intent/entities
        reply, nlu = dialog.handle_user_utterance(session, transcript)

        # Extract a couple of common fields from entities (optional, for future use)
        entities = nlu.entities or {}
        service_name = entities.get("service")
        amount = entities.get("amount")
        currency = entities.get("currency")
        sentiment = entities.get("sentiment")

        # Make sure amount is numeric if it's something usable
        amount_value = None
        if isinstance(amount, (int, float)):
            amount_value = float(amount)
        elif isinstance(amount, str):
            # very light parsing safeguard, e.g. "49.99" -> 49.99
            try:
                amount_value = float(amount)
            except ValueError:
                amount_value = None

        entities_json = json.dumps(entities, ensure_ascii=False)

        # 3) Log user message with NLU entities
        add_call_message(
            call_id=call_id,
            turn_index=turn_index,
            role="user",
            text=transcript,
            intent=nlu.intent,
            entities_json=entities_json,
            timestamp=datetime.utcnow(),
            service_name=service_name if isinstance(service_name, str) else None,
            amount=amount_value,
            currency=currency if isinstance(currency, str) else None,
            sentiment=sentiment if isinstance(sentiment, str) else None,
        )

        # Debug
        try:
            conf_str = f"{nlu.confidence:.2f}"
        except Exception:
            conf_str = str(nlu.confidence)

        print(f"[Intent: {nlu.intent} (conf={conf_str})]")
        print(f"Agent: {reply}")

        # 4) Speak agent reply
        tts.speak(reply)

        # 5) Log assistant reply
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

        # 6) If NLU says end_call, respect it
        if nlu.intent == "end_call":
            break

        # Prepare index for next user message
        turn_index += 1
        print("\n[STT] Listening... (you can speak again)")

    # ---------- End of call: store customer + close call ----------
    # Attach customer if we captured phone
    customer_id = None
    if session.customer_phone:
        customer_id = get_or_create_customer(
            business_id=business_id,
            phone=session.customer_phone,
            name=session.customer_name,
        )
        update_call_customer(call_id=call_id, customer_id=customer_id)

    # Basic end-of-call analytics (you can expand later)
    started = None
    ended = datetime.utcnow()
    num_turns = len(session.messages)
    total_duration_sec = None

    # Very rough duration if we want: from first to last timestamp
    if session.messages:
        started = session.messages[0].timestamp
        if started and ended:
            total_duration_sec = int((ended - started).total_seconds())

    end_call(
        call_id=call_id,
        ended_at=ended,
        outcome=None,              # can set 'BOOKED', 'INFO_ONLY', etc. later
        primary_intent=None,       # can infer later based on NLU stats per call
        primary_service=None,      # can infer from service_name mentions / appointments
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
    if customer_id:
        print(f"DB customer_id: {customer_id}")
    else:
        print("DB customer_id: N/A (no phone captured)")

    print("Transcript:")
    for msg in session.messages:
        print(f"[{msg.role}]: {msg.content}")


if __name__ == "__main__":
    try:
        run_voice_call()
    except KeyboardInterrupt:
        print("\nInterrupted by user, exiting.")
        sys.exit(0)