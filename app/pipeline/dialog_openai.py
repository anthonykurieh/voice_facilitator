import json
from typing import Tuple, Dict, Any, List

from app.pipeline.types import SessionState
from app.pipeline.nlu_openai import NLUResult
from app.pipeline.openai_client import client
from app.config import DIALOG_MODEL, INTENT_CONFIDENCE_FALLBACK


SYSTEM_PROMPT_DIALOG = """\
You are an NLU + dialogue engine for a PHONE-BASED CUSTOMER SERVICE assistant for a BARBER SHOP.

Each turn, your job is to:
1) Read the entire conversation so far.
2) Analyze the LAST user message.
3) Decide the MAIN intent of that last user message.
4) Extract useful entities (slots) when possible.
5) Generate a natural, spoken-style reply for the caller.
6) Return everything as a single JSON object.

--------------------
ALLOWED INTENTS
--------------------
You MUST choose exactly ONE of these intents for each user turn:

- identify_customer          → user provides or confirms name/phone
- schedule_appointment       → user wants to book a service
- reschedule_appointment     → user wants to change an existing booking
- cancel_appointment         → user wants to cancel a booking
- business_info              → hours, location, staff, services, pricing, policies
- support_request            → generic help / assistance
- billing_issue              → payment, refund, invoice, overcharge
- order_status               → tracking, delivery status (if applicable)
- complaint                  → dissatisfaction or frustration
- provide_feedback           → feedback, rating, suggestions
- general_question           → neutral questions
- small_talk                 → greetings, chit-chat
- end_call                   → user wants to end the call
- escalate_to_human          → user asks for a human
- fallback                   → unclear / off-topic

--------------------
ENTITIES
--------------------
Always output an "entities" object. Use these keys when relevant:

Customer identity:
- "customer_name"
- "phone_number"

Scheduling (barber shop):
- "service"              (e.g. haircut, beard trim, fade)
- "date"                 (e.g. tomorrow, 2025-12-14)
- "time"                 (e.g. 4 PM, 16:00)
- "preferred_staff"      (e.g. "Ali", "Rami", "anyone")
- "booking_type"         one of ["NEW","RESCHEDULE","CANCELLATION","unspecified"]
- "confirmation"         one of ["yes","no","unspecified"] (detect confirmations like "yes", "confirm", "that's fine")

Monetary:
- "price_estimated"      numeric if clearly provided, else "unspecified"
- "currency"             (e.g. AED, USD) or "unspecified"

Sentiment:
- "sentiment"            one of ["neutral","frustrated","angry","positive"]

If relevant but unknown, set "unspecified".
Do NOT invent details.

--------------------
OUTPUT FORMAT
--------------------
Return ONLY a single JSON object:

{
  "intent": "<one allowed intent>",
  "confidence": <0..1>,
  "reply": "<spoken response>",
  "entities": { ... }
}

--------------------
DIALOGUE STYLE
--------------------
- Speak like a professional barber shop receptionist.
- Be concise, friendly, and clear.
- If booking: ask for missing details in this order:
  1) name + phone (if not known)
  2) service
  3) date
  4) time
  5) staff preference (optional)
  Then ask for confirmation.
- Do NOT mention intents/entities/internal reasoning.
"""


def _build_transcript(session: SessionState, last_user_text: str) -> str:
    lines: List[str] = []
    for msg in session.messages:
        speaker = "User" if msg.role == "user" else "Agent"
        lines.append(f"{speaker}: {msg.content}")

    lines.append(f"\n[Last user message to analyze]: {last_user_text}")

    transcript = "Conversation so far:\n" + "\n".join(lines)
    transcript += (
        "\n\nBased on this conversation, analyze the LAST user message and "
        "return the JSON object with intent, confidence, reply, and entities."
    )
    return transcript


class OpenAIDialogManager:
    def __init__(self) -> None:
        pass

    def handle_user_utterance(self, session: SessionState, text: str) -> Tuple[str, NLUResult]:
        session.add_message("user", text)

        transcript_prompt = _build_transcript(session, text)

        response = client.responses.create(
            model=DIALOG_MODEL,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT_DIALOG},
                {"role": "user", "content": transcript_prompt},
            ],
        )

        raw = response.output_text.strip()

        try:
            data: Dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            data = {
                "intent": "fallback",
                "confidence": 0.2,
                "reply": "Sorry, I didn’t quite catch that. Could you please rephrase?",
                "entities": {},
            }

        intent = str(data.get("intent", "fallback"))
        confidence = float(data.get("confidence", 0.5))
        reply = str(data.get("reply", "Alright."))
        entities = data.get("entities") or {}
        if not isinstance(entities, dict):
            entities = {}

        # Persist identity into session (if provided)
        cname = entities.get("customer_name")
        phone = entities.get("phone_number")
        if isinstance(cname, str) and cname and cname != "unspecified":
            session.customer_name = cname
        if isinstance(phone, str) and phone and phone != "unspecified":
            session.customer_phone = phone

        nlu_result = NLUResult(
            intent=intent,
            confidence=confidence,
            entities=entities,
        )

        if confidence < INTENT_CONFIDENCE_FALLBACK or intent == "fallback":
            lower_reply = reply.lower()
            clarification_phrases = (
                "could you clarify",
                "please clarify",
                "please rephrase",
                "not sure i understood",
                "didn't understand",
                "did not understand",
            )
            if not any(p in lower_reply for p in clarification_phrases):
                reply = "I’m not sure I fully understood that. Could you please clarify what you need help with?"

        session.add_message("assistant", reply)
        return reply, nlu_result