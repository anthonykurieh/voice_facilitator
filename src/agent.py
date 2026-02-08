"""LLM Agent orchestrator - the brain of the voice assistant."""
import json
import os
from typing import Dict, Any, List, Optional
from openai import OpenAI
from datetime import datetime, date, time
import re
from dateutil.tz import gettz
from src.config import DIALOG_MODEL, APP_TIMEZONE, TRANSLATE_MODEL
from src.translation import Translator


class Agent:
    """LLM-powered agent that orchestrates conversation and decisions."""
    
    def __init__(self, api_key: str, config: Any, database: Any, tools: Any):
        """Initialize agent.
        
        Args:
            api_key: OpenAI API key
            config: ConfigLoader instance
            database: Database instance
            tools: BackendTools instance
        """
        self.client = OpenAI(api_key=api_key)
        self.config = config
        self.database = database
        self.tools = tools
        self.translator = Translator(self.client, TRANSLATE_MODEL)
        self.last_user_language = "en"
        
        self.conversation_history: List[Dict[str, str]] = []
        self.state: Dict[str, Any] = {
            'intent': None,
            'missing_info': [],
            'collected_info': {},
            'current_action': None
        }
        self.log_context: Dict[str, Any] = {
            "customer": {},
            "appointment": {},
            "call": {},
            "kpi_event": {}
        }
        self.log_requirements: Dict[str, Any] = {
            "customers": ["phone", "name"],
            "appointments": [
                "appointment_date",
                "appointment_time",
                "duration_minutes",
                "service_id_or_name",
                "staff_id_or_name_or_auto",
                "customer_phone",
                "customer_name"
            ],
            "calls": ["outcome", "transcript"],
            "kpi_events": ["event_type", "appointment_date", "appointment_time", "status"]
        }
        
        # Build system prompt from config
        self.system_prompt = self._build_system_prompt()
    
    def _build_system_prompt(self) -> str:
        """Build system prompt from business configuration."""
        business_name = self.config.get_business_name()
        business_type = self.config.get_business_type()
        services = self.config.get_services()
        staff = self.config.get_staff()
        hours = self.config.get_hours()
        personality = self.config.get_personality()
        tz = gettz(APP_TIMEZONE)
        now = datetime.now(tz) if tz else datetime.now()
        today = now.date()
        current_day_name = today.strftime("%A")
        current_date_str = today.isoformat()
        current_time_str = now.strftime("%H:%M")
        
        services_list = "\n".join([
            f"- {s['name']} ({s.get('duration_minutes', 30)} min, ${s.get('price', 0):.2f})"
            for s in services
        ])
        
        staff_list = "\n".join([
            f"- {s['name']}" for s in staff if s.get('available', True)
        ])
        
        hours_list = "\n".join([
            f"- {day}: {h.get('open', 'Closed')} - {h.get('close', 'Closed')}"
            for day, h in hours.items()
        ])
        
        tone = personality.get('tone', 'friendly and professional')
        
        prompt = f"""You are an AI voice assistant for {business_name}, a {business_type} business.

Your role is to act like a real front-desk employee. You are {tone}.

BUSINESS INFORMATION:
- Business Name: {business_name}
- Business Type: {business_type}

SERVICES OFFERED:
{services_list}

STAFF MEMBERS:
{staff_list}

BUSINESS HOURS:
{hours_list}

YOUR CAPABILITIES:
You can help customers with:
1. Booking appointments
2. Checking availability
3. Cancelling appointments
4. Rescheduling appointments
5. Listing upcoming appointments
6. Answering questions about services, staff, and hours

CONVERSATION GUIDELINES:
- Be natural and conversational
- Remember information from earlier in the conversation
- Don't repeat questions you've already asked
- Accept information in any order
- Handle corrections gracefully (e.g., "tomorrow actually", "no wait, make it Friday")
- If information is ambiguous, ask ONE clarifying question, then proceed
- Don't get stuck in clarification loops
- Handle messy, incomplete, or grammatically incorrect speech naturally
- Always respond in English. A translation layer will localize responses if needed
- Preserve accents and proper spelling; do not replace accented characters with numbers or ASCII-only text
- Always collect and confirm the customer's phone number and name before booking
- For rescheduling: ask for the customer's phone number, fetch their upcoming appointment, repeat the current slot, then ask for a new date/time or offer the nearest available slots before rescheduling
- For rescheduling: the customer can also change the service type; ask if they'd like to keep the same service or switch
- When availability is confirmed, PROCEED TO BOOK - don't keep checking availability
- Once you have: date, time, service, and customer info, BOOK THE APPOINTMENT immediately
- DO NOT check availability multiple times for the same request - if availability is confirmed, book it
- If the customer says "yes", "okay", or confirms, and you have availability, BOOK IT - don't check again
- If the customer says "haircut and beard trim", treat the service as "Full Service"

LIGHTWEIGHT GUARDRAILS (keep it friendly and flexible):
- If phone number is unclear, too short/long, or missing digits, ask them to repeat it slowly (accept spoken digits like "double two", "oh" = 0)
- If name is unclear, ask for a quick spelling or last name (but accept a first name if they insist)
- If date is vague ("sometime next week"), ask for a specific day; if time is vague ("morning"), offer 2-3 options
- If service is missing, suggest the top 2-3 common services and ask them to pick
- Always read back the final date/time in clear format and ask for confirmation before booking
- Do not check availability without a service type unless duration_minutes is explicitly provided

FLOW IMPROVEMENTS:
- Keep turns short and conversational; avoid long speeches
- Ask only one question at a time
- Use quick acknowledgements ("Got it", "Perfect") before the next question
- If the customer agrees to a suggested time ("that works", "sounds good"), treat it as confirmation
- When presenting options, give 2-3 choices max
- End with a concise recap and next steps after booking

DATE & TIME UNDERSTANDING:
- Today is {current_day_name}, {current_date_str}. Current local time: {current_time_str} ({APP_TIMEZONE}). Use this for interpreting "today/tomorrow/next Monday".
- Understand natural language: "tomorrow", "this Monday" (upcoming Monday, including today if today is Monday), "next week", "the 28th", "around 10", "in the morning"
- "this [day]" means the upcoming occurrence of that day (including today if today is that day)
- Dates without explicit years use the current year ({today.year})
- Parse dates and times from conversational speech
- When speaking times to customers, use 12-hour format like "4:30 PM" (not "16:30")
- If ambiguous, ask once, then make a reasonable assumption

RESPONSE FORMAT:
You must respond in JSON format with this structure:
{{
    "response": "What to say to the customer (natural, conversational)",
    "action": "action_name or null",
    "action_params": {{"param": "value"}} or null,
    "state_update": {{"key": "value"}} or null,
    "log_update": {{"key": "value"}} or null,
    "conversation_complete": false
}}

LOGGING REQUIREMENTS (match DB schema):
{json.dumps(self.log_requirements, indent=2)}
- Maintain a log_context dictionary with keys: customer, appointment, call, kpi_event.
- Before calling an action, ensure all required fields for that action are present in log_context.
- If any required fields are missing, ask ONE concise question to collect them, then update log_context via log_update.
- Only ask when necessary to proceed with a database action.

AVAILABLE ACTIONS:
- "check_availability": Check available time slots (params: date, service_id? or service?, staff_id?, duration_minutes?)
  Returns: {{date, day_name, available_slots (array), count, is_closed (boolean), message (string if closed)}}
  CRITICAL: If is_closed is true, you MUST immediately inform the customer clearly using the message field.
  Do NOT check availability again for the same closed day. Instead, suggest alternative days (Monday-Saturday).
  If count is 0 and is_closed is false, inform the customer no slots are available and suggest other times.
- "book_appointment": Book an appointment (params: date, time, service_id?, staff_id?, customer_phone?, customer_name?)
  IMPORTANT: Always check availability first. Do NOT book if is_closed is true or if no slots are available.
  Collect customer phone number AND name before booking; refuse to book if either is missing.
  NEVER book on a day when is_closed was true in the availability check.
  The system automatically prevents double-booking - if a slot is already taken, booking will fail with an error message.
- "cancel_appointment": Cancel an appointment (params: appointment_id or customer_phone)
- "get_customer_appointments": Get customer's appointments (params: customer_phone)
- "reschedule_appointment": Reschedule an appointment (params: appointment_id OR customer_phone, new_date?, new_time?, new_service?, new_service_id?)
  - If no new_date/time is provided, it returns the customer's current appointment so you can ask for a new slot.
  - If only new_date is provided, it returns available_slots for that date (using the same service duration/staff).
  - If new_date AND new_time are provided, it BOOKS the new slot first, then cancels the old appointment to avoid losing the booking.
  IMPORTANT: Use the actual appointment_id from get_customer_appointments result (not a placeholder). Keep the same service/staff unless the customer requests a change.
- "get_services": Get list of services (no params)
- "get_staff": Get list of staff (no params)
- null: No action needed, just conversation

IMPORTANT BOOKING RULES:
- Always check availability BEFORE booking
- If check_availability returns is_closed=true, DO NOT attempt to book. Instead, inform the customer and suggest alternative days.
- If check_availability returns count=0 and is_closed=false, inform the customer the time slot is not available and suggest alternatives.
- If check_availability returns count > 0 and the requested time is in available_slots, PROCEED TO BOOK immediately
- DO NOT check availability multiple times for the same request - once confirmed, book it
- When you have all required info (date, time, service, customer name, and customer phone), use book_appointment action IMMEDIATELY
- After booking succeeds, confirm the details with the customer
- If customer confirms with "yes", "okay", "sure" and you already checked availability, BOOK IT - don't check again
- For rescheduling, never cancel first: call reschedule_appointment to book the new slot and then cancel the old one once the new booking succeeds
- Remember: Checking availability is just to verify - the goal is to BOOK the appointment
- CRITICAL: The system prevents double-booking. If book_appointment returns an error saying the slot is unavailable, inform the customer and suggest alternative times from the available_slots in the error response.

IMPORTANT:
- Only return valid JSON
- The "response" field is what you'll say to the customer
- Use actions to interact with the backend
- Update state as you collect information
- Use log_update to keep log_context in sync
- Set conversation_complete to true when the call should end
- When an action returns is_closed=true, you MUST inform the customer immediately and suggest alternatives
- Do NOT repeatedly check availability for the same closed day - suggest different days instead
- Be proactive: if the customer wants a day that's closed, suggest the next available day
"""
        return prompt
    
    def process(self, user_input: str, internal_prompt: bool = False) -> Dict[str, Any]:
        """Process user input and return agent decision.
        
        Args:
            user_input: Transcribed user speech
            internal_prompt: True when caller passes internal orchestration text
            
        Returns:
            Agent decision with response, action, and state
        """
        # Detect language and translate to English for user-originated input.
        # Internal orchestration prompts must not overwrite the user's language.
        if not internal_prompt:
            detected_lang = self.translator.detect_language(user_input)
            self.last_user_language = detected_lang or "en"
        translated_input = user_input
        if not internal_prompt and self.last_user_language != "en":
            translated_input = self.translator.translate(user_input, "en")

        # Add user input to history
        self.conversation_history.append({
            "role": "user",
            "content": translated_input
        })
        
        # Build messages for API
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "system", "content": f"CURRENT_LOG_CONTEXT: {json.dumps(self.log_context, ensure_ascii=False)}"},
            *self.conversation_history
        ]
        
        # Get agent response
        try:
            response = self.client.chat.completions.create(
                model=DIALOG_MODEL,
                messages=messages,
                temperature=0.7,
                response_format={"type": "json_object"}
            )
            
            agent_response = json.loads(response.choices[0].message.content)

            if agent_response.get('log_update'):
                try:
                    for k, v in agent_response.get('log_update', {}).items():
                        if isinstance(self.log_context.get(k), dict) and isinstance(v, dict):
                            self.log_context[k].update(v)
                        else:
                            self.log_context[k] = v
                except Exception:
                    pass

            action = agent_response.get("action")
            action_params = agent_response.get("action_params") or {}

            def _get_log_value(group: str, key: str):
                if isinstance(self.log_context.get(group), dict):
                    return self.log_context[group].get(key)
                return None

            if action == "book_appointment":
                missing = []
                if not (action_params.get("date") or _get_log_value("appointment", "appointment_date")):
                    missing.append("date")
                if not (action_params.get("time") or _get_log_value("appointment", "appointment_time")):
                    missing.append("time")
                if not (
                    action_params.get("service_id")
                    or action_params.get("service")
                    or action_params.get("service_name")
                    or _get_log_value("appointment", "service")
                    or _get_log_value("appointment", "service_id")
                ):
                    missing.append("service")
                if not (action_params.get("customer_phone") or _get_log_value("customer", "phone")):
                    missing.append("phone number")
                if not (action_params.get("customer_name") or _get_log_value("customer", "name")):
                    missing.append("name")
                if missing:
                    agent_response["action"] = None
                    agent_response["action_params"] = None
                    agent_response["response"] = (
                        "To book the appointment, I still need your "
                        + ", ".join(missing)
                        + "."
                    )
            elif action in {"cancel_appointment", "reschedule_appointment"}:
                has_id = bool(action_params.get("appointment_id") or _get_log_value("appointment", "id"))
                has_phone = bool(action_params.get("customer_phone") or _get_log_value("customer", "phone"))
                if not (has_id or has_phone):
                    agent_response["action"] = None
                    agent_response["action_params"] = None
                    agent_response["response"] = (
                        "Could you provide the appointment ID or the phone number on the booking?"
                    )
            elif action == "get_customer_appointments":
                if not (action_params.get("customer_phone") or _get_log_value("customer", "phone")):
                    agent_response["action"] = None
                    agent_response["action_params"] = None
                    agent_response["response"] = "Could I have the phone number on the booking?"

            response_text = agent_response.get("response", "")
            translated_response = response_text
            if self.last_user_language != "en":
                translated_response = self.translator.translate(response_text, self.last_user_language)
            
            # Add assistant response to history
            history_response = dict(agent_response)
            history_response["response"] = response_text
            self.conversation_history.append({
                "role": "assistant",
                "content": json.dumps(history_response)
            })
            
            # Update state
            if agent_response.get('state_update'):
                self.state.update(agent_response['state_update'])
            
            agent_response["response"] = translated_response
            return agent_response
        
        except json.JSONDecodeError as e:
            print(f"Agent JSON decode error: {e}")
            # Try to extract text response even if JSON is malformed
            try:
                raw_content = response.choices[0].message.content
                # Fallback: use raw content as response
                return {
                    "response": raw_content if raw_content else "I'm sorry, I'm having trouble processing that.",
                    "action": None,
                    "action_params": None,
                    "state_update": None,
                    "conversation_complete": False
                }
            except:
                return {
                    "response": "I'm sorry, I'm having trouble processing that. Could you repeat?",
                    "action": None,
                    "action_params": None,
                    "state_update": None,
                    "conversation_complete": False
                }
        except Exception as e:
            print(f"Agent error: {e}")
            return {
                "response": "I'm sorry, I'm having trouble processing that. Could you repeat?",
                "action": None,
                "action_params": None,
                "state_update": None,
                "conversation_complete": False
            }
    
    def execute_action(self, action: str, action_params: Dict[str, Any]) -> Any:
        """Execute backend action.
        
        Args:
            action: Action name
            action_params: Action parameters
            
        Returns:
            Action result
        """
        return self.tools.execute(action, action_params)
    
    def get_greeting(self) -> str:
        """Get initial greeting from config."""
        personality = self.config.get_personality()
        greeting_template = personality.get('greeting', 
            'Hello! Thank you for calling {business_name}. How can I help you today?')
        
        business_name = self.config.get_business_name()
        return greeting_template.format(business_name=business_name)
    
    def reset(self):
        """Reset conversation state."""
        self.conversation_history = []
        self.state = {
            'intent': None,
            'missing_info': [],
            'collected_info': {},
            'current_action': None
        }
