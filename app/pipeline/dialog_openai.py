import json
from typing import Tuple, Dict, Any, List

from app.pipeline.types import SessionState
from app.pipeline.nlu_openai import NLUResult
from app.pipeline.openai_client import client
from app.config import DIALOG_MODEL
from app.business.profile import load_business_profile, BusinessProfile
from app.backend.knowledge_service import KnowledgeService, KBResult


SYSTEM_PROMPT_DIALOG = """\
You are an NLU + dialogue engine for a PHONE-BASED CUSTOMER SERVICE assistant.

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

- schedule_appointment       → user wants to book a service or callback
- reschedule_appointment     → user wants to change an existing booking
- cancel_appointment         → user wants to cancel a booking
- support_request            → generic help / technical / product support
- billing_issue              → payment, refund, invoice, overcharge
- order_status               → “where is my order?”, tracking, delivery status
- complaint                  → user is expressing dissatisfaction or frustration
- provide_feedback           → user gives feedback, rating, suggestions
- business_info              → user asks about business info (hours, location, services, staff, policies, contact)
- general_question           → neutral questions not clearly about support or the business
- small_talk                 → greetings, chit-chat, “just testing”, etc.
- end_call                   → user wants to end the call, say goodbye, hang up
- escalate_to_human          → user explicitly asks for a human or says you can’t help
- fallback                   → user is unclear / off-topic / you genuinely cannot classify

You MUST pick the intent that best describes the LAST user message.

--------------------
ENTITIES
--------------------
Always output an "entities" object. Use these keys when relevant:

Caller identity:
- "customer_name":   caller's name, e.g. "Anthony", "Omar Kurieh", or "unspecified"
- "phone_number":    caller's phone / mobile number in any format, e.g. "+971509876543" or "unspecified"

For scheduling-related intents (schedule_*, reschedule_*, cancel_appointment):
- "service":           the service the caller wants, e.g. "haircut", "consultation", "massage", "checkup"
- "date":              e.g. "2025-12-02", "tomorrow", "next Monday"
- "time":              e.g. "16:00", "4 PM", "morning", "afternoon"
- "preferred_barber":  name of a specific staff member if requested, e.g. "Omar", or "unspecified"
- "appointment_notes": any extra details the caller gives, e.g. "I have curly hair", "I prefer a female doctor", or "unspecified"

For orders and billing:
- "order_id":      any order/booking number mentioned
- "amount":        e.g. "120", "200", "unspecified"
- "currency":      e.g. "AED", "USD", "EUR", "unspecified"
- "issue_type":    short label like "payment_failed", "overcharge", "missing_item"

For support / complaint / feedback:
- "issue_type":    e.g. "service_quality", "staff_behavior", "billing", "technical", "other"
- "product":       service or product mentioned, if any
- "sentiment":     one of ["neutral", "frustrated", "angry", "positive"]

For information-seeking questions (business_info, general_question, order_status, billing_issue, support_request):
- "topic":             short label like "opening_hours", "location", "services", "pricing", "refund_policy", "payment_methods", "staff", "other"
- "needs_kb_lookup":   true/false, true if this question should consult the business knowledge base.

If a field is relevant but not known, set it to "unspecified".
Do NOT invent precise details the user did not provide.

If no entities are useful, you may return {} but prefer filling something meaningful.

--------------------
OUTPUT FORMAT
--------------------
Return ONLY a single JSON object, with NO explanation or extra text.

Schema (must be strictly followed):
{
  "intent": "<one allowed intent>",
  "confidence": <number between 0 and 1>,
  "reply": "<what you would say to the caller in a friendly, spoken style>",
  "entities": {
    ... entity fields as described above ...
  }
}

Examples of valid "confidence" values: 0.55, 0.82, 0.99.
If you are very uncertain about the intent, use a lower confidence and consider "fallback".

--------------------
DIALOGUE STYLE
--------------------
- Speak like a professional customer service agent for this business on the phone.
- Be concise, friendly, and clear.

- Early in the call, if you have not yet learned the caller's name and phone number from the conversation,
  politely ask for them, for example:
  "Before we continue, may I have your name and mobile number so I can save your booking?"
  Once the caller has given their name and phone number, do NOT keep asking for them again.

- If you need more information to complete an action, ASK a concrete follow-up question.
  For example:
  - If intent is schedule_appointment but "date" is "unspecified", ask: "Which day works best for you?"
  - If intent is schedule_appointment but "service" is "unspecified", ask: "Which service would you like to book?"
  - If intent is billing_issue but there is no "amount" or "order_id", ask: "Do you remember the amount or have a booking or order reference?"

- Do NOT mention that you are an AI model or that this is a simulation.
- Do NOT mention intents, entities, or internal reasoning to the caller.
- Use the conversation context (previous turns) to stay consistent (e.g. remember what was just booked).
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
        self.business_profile: BusinessProfile = load_business_profile()
        self.knowledge_service = KnowledgeService(self.business_profile)

    def _build_system_prompt(self) -> str:
        business_context = self.business_profile.to_prompt_string()
        return (
            SYSTEM_PROMPT_DIALOG
            + "\n\n--------------------\nBUSINESS CONTEXT\n--------------------\n"
            + business_context
        )

    def _maybe_apply_kb(self, user_text: str, base_reply: str, nlu: NLUResult) -> str:
        entities = nlu.entities or {}
        needs_kb = entities.get("needs_kb_lookup", False)
        topic = entities.get("topic")

        if not needs_kb:
            return base_reply

        kb_result: KBResult = self.knowledge_service.query(question=user_text, topic=topic)
        if not kb_result.snippets:
            return base_reply

        kb_context = "\n\n".join(kb_result.snippets)

        refinement_prompt = (
            "You are refining a customer service reply using the business knowledge below.\n\n"
            "BUSINESS KNOWLEDGE SNIPPETS:\n"
            f"{kb_context}\n\n"
            "ORIGINAL REPLY:\n"
            f"{base_reply}\n\n"
            "CALLER QUESTION:\n"
            f"{user_text}\n\n"
            "TASK:\n"
            "Generate a final spoken reply that is:\n"
            "- consistent with the business knowledge\n"
            "- clear and concise\n"
            "- suitable for a phone call\n"
            "Do NOT mention that you used any knowledge snippets. Just answer naturally."
        )

        response = client.responses.create(
            model=DIALOG_MODEL,
            input=[
                {"role": "system", "content": "You are a careful customer service assistant for this business."},
                {"role": "user", "content": refinement_prompt},
            ],
        )
        refined = response.output_text.strip()
        return refined if refined else base_reply

    def handle_user_utterance(self, session: SessionState, text: str) -> Tuple[str, NLUResult]:
        # 1) Save user message
        session.add_message("user", text)

        # 2) Build prompt
        transcript_prompt = _build_transcript(session, text)
        system_prompt = self._build_system_prompt()

        # 3) NLU + base reply
        response = client.responses.create(
            model=DIALOG_MODEL,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": transcript_prompt},
            ],
        )

        raw = response.output_text.strip()

        # 4) Parse JSON
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

        nlu_result = NLUResult(
            intent=intent,
            confidence=confidence,
            entities=entities,
        )

        # 5) Update session identity
        customer_name = entities.get("customer_name")
        phone_number = entities.get("phone_number")

        if isinstance(customer_name, str) and customer_name and customer_name != "unspecified":
            session.customer_name = customer_name

        if isinstance(phone_number, str) and phone_number and phone_number != "unspecified":
            session.customer_phone = phone_number

        # 6) Low-confidence clarification
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

        # 7) Knowledge refinement if needed
        reply = self._maybe_apply_kb(user_text=text, base_reply=reply, nlu=nlu_result)

        # 8) Save assistant reply
        session.add_message("assistant", reply)

        return reply, nlu_result