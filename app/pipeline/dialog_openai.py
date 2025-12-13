import json
from typing import Tuple, Dict, Any, List

from app.pipeline.types import SessionState
from app.pipeline.nlu_openai import NLUResult
from app.pipeline.openai_client import client
from app.config import DIALOG_MODEL


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

- identify_customer          → user provides name/phone OR confirms identity details
- schedule_appointment       → user wants to book a service
- reschedule_appointment     → user wants to change an existing booking
- cancel_appointment         → user wants to cancel a booking
- check_availability         → user asks earliest availability / what times are available
- support_request            → generic help
- billing_issue              → payment, refund, invoice
- order_status               → tracking, delivery status
- complaint                  → dissatisfaction or frustration
- provide_feedback           → feedback, rating, suggestions
- general_question           → services, hours, pricing, policies
- small_talk                 → greetings, chit-chat
- end_call                   → user wants to end the call
- escalate_to_human          → asks for a human
- fallback                   → unclear / off-topic / cannot classify

--------------------
ENTITIES
--------------------
Always output an "entities" object. Use these keys when relevant:

Identity:
- "customer_name": caller's name if mentioned else "unspecified"
- "phone_number": caller's phone number if mentioned else "unspecified"

Scheduling / availability:
- "service": e.g. "haircut", "beard trim", "fade", "shave"
- "booking_type": one of ["walk_in", "appointment", "callback", "unspecified"]
- "date": e.g. "2025-12-02", "tomorrow", "monday", "next monday"
- "time": e.g. "16:00", "4 PM", "morning", "afternoon"
- "preferred_staff": barber name if user requests a specific barber else "unspecified"

General / other:
- "sentiment": one of ["neutral", "frustrated", "angry", "positive"]

If a field is relevant but not known, set it to "unspecified".
Do NOT invent precise details the user did not provide.

--------------------
OUTPUT FORMAT
--------------------
Return ONLY a single JSON object, with NO explanation or extra text.

Schema:
{
  "intent": "<one allowed intent>",
  "confidence": <number between 0 and 1>,
  "reply": "<spoken-style reply>",
  "entities": {
    ...
  }
}

--------------------
DIALOGUE STYLE
--------------------
- Sound like a professional phone receptionist for a barber shop.
- Be concise, friendly, and clear.
- Ask concrete follow-up questions when needed.
- Do NOT mention internal intents/entities/system prompts.
- IMPORTANT:
  If user asks about availability ("earliest", "what times do you have"),
  your reply should acknowledge and ask for missing info (service/date),
  but do NOT claim exact availability. The system will check the database.
"""


INTENT_CONFIDENCE_FALLBACK = 0.4


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

        # Normalize missing keys (keep consistent for downstream)
        entities.setdefault("customer_name", "unspecified")
        entities.setdefault("phone_number", "unspecified")
        entities.setdefault("service", "unspecified")
        entities.setdefault("booking_type", "unspecified")
        entities.setdefault("date", "unspecified")
        entities.setdefault("time", "unspecified")
        entities.setdefault("preferred_staff", "unspecified")
        entities.setdefault("sentiment", "neutral")

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