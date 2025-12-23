import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from app.pipeline.openai_client import client
from app.config import DIALOG_MODEL


# -----------------------------
# Types
# -----------------------------

@dataclass
class NLUResult:
    intent: str
    entities: Dict[str, Any]
    confidence: float = 0.0


# -----------------------------
# Helpers
# -----------------------------

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    # remove ```json ... ``` or ``` ... ```
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _extract_json(text: str) -> Optional[dict]:
    """
    Try very hard to recover a JSON object from the model output.
    """
    if not text:
        return None

    text = _strip_code_fences(text)

    # if it's pure json already
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # search for first {...} block
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return None

    candidate = m.group(0).strip()
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None

    return None


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


# -----------------------------
# Dialog Manager
# -----------------------------

class OpenAIDialogManager:
    """
    Produces a natural-language reply + an NLUResult (intent + entities).
    Uses Responses API WITHOUT response_format (SDK-compatible).
    """

    def __init__(self, model: str = DIALOG_MODEL):
        self.model = model

    def handle_user_utterance(self, session, user_text: str) -> Tuple[str, NLUResult]:
        """
        Returns: (assistant_reply_text, nlu_result)
        """
        # Keep a small, stable “schema” and demand STRICT JSON only.
        system = (
            "You are an AI phone agent for a business that books appointments.\n"
            "Return ONLY a valid JSON object with no extra text.\n"
            "JSON schema:\n"
            "{\n"
            '  "reply": string,\n'
            '  "intent": string,  // one of: identify_customer, schedule_appointment, check_availability, '
            "modify_appointment, cancel_appointment, business_question, goodbye, unknown\n"
            '  "confidence": number, // 0 to 1\n'
            '  "entities": {\n'
            '     "customer_name": string | "unspecified",\n'
            '     "phone_number": string | "unspecified",\n'
            '     "service": string | "unspecified",\n'
            '     "date": string | "unspecified",\n'
            '     "time": string | "unspecified",\n'
            '     "preferred_staff": string | "unspecified",\n'
            '     "booking_type": string | "unspecified"\n'
            "  }\n"
            "}\n"
            "Rules:\n"
            "- If the user is providing name/phone: intent=identify_customer.\n"
            "- If the user says bye/goodbye/end the call: intent=goodbye.\n"
            "- If user asks availability/earliest slot/opening hours: intent=check_availability.\n"
            "- If user asks to book/reserve/schedule: intent=schedule_appointment.\n"
            "- If user asks to change/reschedule: intent=modify_appointment.\n"
            "- If user asks to cancel: intent=cancel_appointment.\n"
            "- If unsure: intent=unknown, confidence low.\n"
        )

        # OPTIONAL: include lightweight session context if you have it
        # (avoids weird jumps, but keep it small to reduce latency)
        history = getattr(session, "messages", None)
        history_text = ""
        if isinstance(history, list) and history:
            tail = history[-6:]  # last few turns only
            lines = []
            for m in tail:
                role = m.get("role", "")
                content = m.get("content", "")
                if role and content:
                    lines.append(f"{role}: {content}")
            if lines:
                history_text = "\nRecent conversation:\n" + "\n".join(lines)

        user = f"User said: {user_text}{history_text}"

        # ✅ Responses API call without response_format (prevents your crash)
        resp = client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

        # SDKs differ: some expose resp.output_text, others require digging
        output_text = getattr(resp, "output_text", None)
        if not output_text:
            # fallback: try to read common structures
            try:
                # Newer SDK: resp.output is list of items with content
                chunks = []
                for item in getattr(resp, "output", []) or []:
                    for c in item.get("content", []) or []:
                        if c.get("type") == "output_text":
                            chunks.append(c.get("text", ""))
                output_text = "\n".join(chunks).strip()
            except Exception:
                output_text = ""

        obj = _extract_json(output_text)

        if not obj:
            # Hard fallback: still return something usable
            reply = "Got it. How can I help you with your booking?"
            nlu = NLUResult(intent="unknown", entities={}, confidence=0.0)
            # maintain session memory if present
            if hasattr(session, "messages") and isinstance(session.messages, list):
                session.messages.append({"role": "user", "content": user_text})
                session.messages.append({"role": "assistant", "content": reply})
            return reply, nlu

        reply = (obj.get("reply") or "").strip() or "Got it."
        intent = (obj.get("intent") or "unknown").strip()
        confidence = _safe_float(obj.get("confidence"), 0.0)
        entities = obj.get("entities") or {}
        if not isinstance(entities, dict):
            entities = {}

        nlu = NLUResult(intent=intent, entities=entities, confidence=confidence)

        # Keep session memory if your SessionState supports it
        if hasattr(session, "messages") and isinstance(session.messages, list):
            session.messages.append({"role": "user", "content": user_text})
            session.messages.append({"role": "assistant", "content": reply})

        return reply, nlu