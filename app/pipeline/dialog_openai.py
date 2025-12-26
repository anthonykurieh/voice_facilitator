import json
from typing import Tuple, Dict, Any, List

from app.pipeline.types import SessionState
from app.pipeline.nlu_openai import NLUResult
from app.pipeline.openai_client import client
from app.config import DIALOG_MODEL


SYSTEM_PROMPT_DIALOG = """
You are an NLU + dialogue engine for a PHONE-BASED BARBER SHOP assistant.

Return ONLY valid JSON. No markdown. No extra text.

Pick exactly ONE intent from:
- identify_customer
- check_availability
- schedule_appointment
- reschedule_appointment
- cancel_appointment
- modify_appointment
- list_appointments
- general_question
- complaint
- small_talk
- end_call
- fallback

Entities (include only what you are confident about):
- customer_name
- phone_number
- service
- date
- time
- preferred_staff
- booking_type (phone/walkin/web)
- notes
- appointment_id (if user mentions a specific id like "#12")

Output schema:
{
  "intent": "...",
  "confidence": 0.0,
  "reply": "...",
  "entities": { ... }
}
""".strip()

INTENT_CONFIDENCE_FALLBACK = 0.35


def _build_transcript(session: SessionState, last_user_text: str) -> str:
    lines: List[str] = []
    for msg in session.messages:
        speaker = "User" if msg.role == "user" else "Agent"
        lines.append(f"{speaker}: {msg.content}")
    lines.append(f"\n[Last user message]: {last_user_text}")
    return "Conversation so far:\n" + "\n".join(lines)


def _extract_first_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        chunk = text[start : end + 1]
        return json.loads(chunk)

    raise json.JSONDecodeError("No JSON object found", text, 0)


class OpenAIDialogManager:
    def handle_user_utterance(self, session: SessionState, text: str) -> Tuple[str, NLUResult]:
        session.add_message("user", text)
        transcript_prompt = _build_transcript(session, text)

        # ✅ Do not pass response_format (some installed SDKs don't support it)
        resp = client.responses.create(
            model=DIALOG_MODEL,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT_DIALOG},
                {"role": "user", "content": transcript_prompt},
            ],
        )

        raw = (resp.output_text or "").strip()

        try:
            data: Dict[str, Any] = _extract_first_json_object(raw)
        except Exception:
            data = {
                "intent": "fallback",
                "confidence": 0.2,
                "reply": "Sorry — I didn’t catch that. Could you repeat it?",
                "entities": {},
            }

        intent = str(data.get("intent", "fallback"))
        confidence = float(data.get("confidence", 0.5))
        reply = str(data.get("reply", "Alright."))
        entities = data.get("entities") or {}

        if not isinstance(entities, dict):
            entities = {}

        if confidence < INTENT_CONFIDENCE_FALLBACK:
            intent = "fallback"

        nlu_result = NLUResult(intent=intent, confidence=confidence, entities=entities)
        session.add_message("assistant", reply)
        return reply, nlu_result