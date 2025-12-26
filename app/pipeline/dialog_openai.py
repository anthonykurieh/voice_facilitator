# app/pipeline/dialog_openai.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from openai import OpenAI

from app.pipeline.types import SessionState


@dataclass
class NLUResult:
    intent: str
    entities: Dict[str, Any]
    confidence: float = 0.0


class OpenAIDialogManager:
    """
    Uses Responses API but WITHOUT passing response_format (to avoid SDK mismatch).
    We force JSON by instruction and then json.loads() it.
    """
    def __init__(self, model: str = "gpt-4.1-mini"):
        self.client = OpenAI()
        self.model = model

    def handle_user_utterance(self, session: SessionState, user_text: str) -> Tuple[str, NLUResult]:
        system = (
            "You are a voice-call booking assistant for a barber shop.\n"
            "Return STRICT JSON ONLY with keys: reply, intent, confidence, entities.\n"
            "intents: schedule_appointment, check_availability, list_appointments, cancel_appointment, "
            "reschedule_appointment, modify_appointment, smalltalk, other.\n"
            "entities may include: customer_name, phone_number, service, date, time, preferred_staff, appointment_id.\n"
            "If user says 'tomorrow', keep entity date='tomorrow' (do not convert to a year).\n"
        )

        user = f"User said: {user_text}"

        resp = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

        # Try to extract text output
        text = None
        try:
            # Most SDK builds expose output_text
            text = resp.output_text
        except Exception:
            pass

        if not text:
            # fallback: attempt manual extraction
            try:
                parts = []
                for item in resp.output:
                    for c in item.content:
                        if getattr(c, "type", None) in ("output_text", "text"):
                            parts.append(getattr(c, "text", ""))
                text = "\n".join(parts).strip()
            except Exception:
                text = ""

        # Parse JSON safely
        try:
            data = json.loads(text)
        except Exception:
            # If the model didn't obey, degrade gracefully
            return (
                "Sorry — I didn’t catch that. Could you rephrase?",
                NLUResult(intent="other", entities={}, confidence=0.0),
            )

        reply = str(data.get("reply", "")).strip() or "Okay."
        intent = str(data.get("intent", "other")).strip() or "other"
        conf = float(data.get("confidence", 0.0) or 0.0)
        entities = data.get("entities") or {}
        if not isinstance(entities, dict):
            entities = {}

        return reply, NLUResult(intent=intent, entities=entities, confidence=conf)