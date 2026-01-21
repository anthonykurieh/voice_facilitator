"""LLM Agent orchestrator - the brain of the voice assistant."""
import json
import os
from typing import Dict, Any, List, Optional
from openai import OpenAI
from datetime import datetime, date, time
import re
from dateutil.tz import gettz
from src.config import DIALOG_MODEL, APP_TIMEZONE


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
        
        self.conversation_history: List[Dict[str, str]] = []
        self.state: Dict[str, Any] = {
            'intent': None,
            'missing_info': [],
            'collected_info': {},
            'current_action': None
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
- Always collect and confirm the customer's phone number and name before booking
- For rescheduling: ask for the customer's phone number, fetch their upcoming appointment, repeat the current slot, then ask for a new date/time or offer the nearest available slots before rescheduling
- When availability is confirmed, PROCEED TO BOOK - don't keep checking availability
- Once you have: date, time, service, and customer info, BOOK THE APPOINTMENT immediately
- DO NOT check availability multiple times for the same request - if availability is confirmed, book it
- If the customer says "yes", "okay", or confirms, and you have availability, BOOK IT - don't check again

DATE & TIME UNDERSTANDING:
- Today is {current_day_name}, {current_date_str}. Current local time: {current_time_str} ({APP_TIMEZONE}). Use this for interpreting "today/tomorrow/next Monday".
- Understand natural language: "tomorrow", "this Monday" (upcoming Monday, including today if today is Monday), "next week", "the 28th", "around 10", "in the morning"
- "this [day]" means the upcoming occurrence of that day (including today if today is that day)
- Dates without explicit years use the current year ({today.year})
- Parse dates and times from conversational speech
- If ambiguous, ask once, then make a reasonable assumption

RESPONSE FORMAT:
You must respond in JSON format with this structure:
{{
    "response": "What to say to the customer (natural, conversational)",
    "action": "action_name or null",
    "action_params": {{"param": "value"}} or null,
    "state_update": {{"key": "value"}} or null,
    "conversation_complete": false
}}

AVAILABLE ACTIONS:
- "check_availability": Check available time slots (params: date, staff_id?, duration_minutes?)
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
- "reschedule_appointment": Reschedule an appointment (params: appointment_id OR customer_phone, new_date?, new_time?)
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
- Set conversation_complete to true when the call should end
- When an action returns is_closed=true, you MUST inform the customer immediately and suggest alternatives
- Do NOT repeatedly check availability for the same closed day - suggest different days instead
- Be proactive: if the customer wants a day that's closed, suggest the next available day
"""
        return prompt
    
    def process(self, user_input: str) -> Dict[str, Any]:
        """Process user input and return agent decision.
        
        Args:
            user_input: Transcribed user speech
            
        Returns:
            Agent decision with response, action, and state
        """
        # Add user input to history
        self.conversation_history.append({
            "role": "user",
            "content": user_input
        })
        
        # Build messages for API
        messages = [
            {"role": "system", "content": self.system_prompt},
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
            
            # Add assistant response to history
            self.conversation_history.append({
                "role": "assistant",
                "content": json.dumps(agent_response)
            })
            
            # Update state
            if agent_response.get('state_update'):
                self.state.update(agent_response['state_update'])
            
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
