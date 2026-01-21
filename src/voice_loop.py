"""Main voice loop: Listen → Think → Act → Speak."""
import os
import sys
import logging
from typing import Optional
from src.stt import SpeechToText
from src.tts import TextToSpeech
from src.agent import Agent
from src.tools import BackendTools
from src.config_loader import ConfigLoader
from src.database import Database
from src.init_database import init_business_data

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


class VoiceLoop:
    """Main voice conversation loop."""
    
    def __init__(self):
        """Initialize voice loop components."""
        # Load environment
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            # dotenv is optional if environment is already set
            pass
        
        from src.config import OPENAI_API_KEY
        
        # Initialize components
        config_path = os.getenv('CONFIG_FILE', 'config/business_config.yaml')
        self.config = ConfigLoader(config_path)
        self.database = Database()
        self.tools = BackendTools(self.database, self.config)
        self.agent = Agent(OPENAI_API_KEY, self.config, self.database, self.tools)
        self.stt = SpeechToText(OPENAI_API_KEY)
        self.tts = TextToSpeech(OPENAI_API_KEY)
        self.call_id = None
        self.call_transcript = []
        self.call_outcome = None
        self.call_customer_id = None
        self.call_appointment_id = None
        
        # Initialize database schema
        logger.info("Initializing database...")
        print("Initializing database...")
        self.database.initialize_schema()
        try:
            init_business_data()
        except Exception as e:
            logger.warning(f"Business data initialization skipped: {e}")
        
        # Initialize business data if needed
        # (Run init_database.py separately for first-time setup)
        logger.info("Database ready.")
        print("Database ready.")
    
    def run(self):
        """Run the main voice loop."""
        print("\n" + "="*60)
        print("Voice Assistant Starting...")
        print("="*60 + "\n")

        # Start call record
        try:
            self.call_id = self.database.create_call(business_id=1)
            logger.info(f"Call started with id {self.call_id}")
        except Exception as e:
            logger.warning(f"Could not create call record: {e}")
        
        # Greeting
        greeting = self.agent.get_greeting()
        print(f"Assistant: {greeting}")
        self.tts.speak(greeting)
        self._log_turn("assistant", greeting)
        
        # Main loop
        conversation_complete = False
        turn_count = 0
        max_turns = 100  # Safety limit
        
        while not conversation_complete and turn_count < max_turns:
            try:
                # Listen
                logger.info("Listening for user input...")
                user_input = self.stt.listen()
                
                if not user_input.strip():
                    logger.warning("No input detected, continuing...")
                    print("(No input detected, continuing...)")
                    continue
                
                logger.info(f"User said: {user_input}")
                print(f"\nUser: {user_input}")
                self._log_turn("user", user_input)
                
                # Think
                logger.info("Processing user input with agent...")
                agent_decision = self.agent.process(user_input)
                logger.debug(f"Agent decision: {agent_decision}")
                
                # Extract response
                response_text = agent_decision.get('response', 
                    "I'm sorry, I didn't understand that.")
                
                logger.info(f"Assistant response: {response_text}")
                print(f"Assistant: {response_text}")
                self._log_turn("assistant", response_text)
                
                # Act (if action needed)
                action = agent_decision.get('action')
                if action:
                    action_params = agent_decision.get('action_params', {})
                    logger.info(f"Executing action: {action} with params: {action_params}")
                    print(f"[Executing action: {action}]")
                    if action_params:
                        print(f"[Action params: {action_params}]")
                    
                    try:
                        action_result = self.agent.execute_action(action, action_params)
                        logger.info(f"Action result: {action_result}")
                        print(f"[Action result: {action_result}]")
                        # Capture booking outcome
                        if action == 'book_appointment' and action_result.get('success'):
                            self.call_outcome = self.call_outcome or 'booked'
                            if action_result.get('customer_id'):
                                self.call_customer_id = action_result.get('customer_id')
                            if action_result.get('appointment_id'):
                                self.call_appointment_id = action_result.get('appointment_id')
                        if action == 'cancel_appointment' and action_result.get('success'):
                            self.call_outcome = self.call_outcome or 'cancelled'
                        if action == 'reschedule_appointment' and action_result.get('success'):
                            self.call_outcome = self.call_outcome or 'rescheduled'
                            if action_result.get('new_appointment_id'):
                                self.call_appointment_id = action_result.get('new_appointment_id')
                        
                        # Incorporate action result into conversation
                        if action_result.get('error'):
                            logger.error(f"Action error: {action_result['error']}")
                            # Special handling for booking errors (slot unavailable)
                            if action == 'book_appointment' and 'no longer available' in str(action_result.get('error', '')):
                                available_slots = action_result.get('available_slots', [])
                                suggestion = action_result.get('suggestion', '')
                                booking_error_prompt = (
                                    f"The booking failed because the time slot is no longer available. "
                                    f"Error: {action_result['error']}. "
                                    f"{suggestion} "
                                    f"Please inform the customer that the time slot was just taken and suggest alternative times from the available slots."
                                )
                                error_feedback = self.agent.process(booking_error_prompt)
                                response_text = error_feedback.get('response', response_text)
                                logger.info(f"Assistant response after booking error: {response_text}")
                                print(f"Assistant (booking error): {response_text}")
                            else:
                                # Generic error handling
                                error_feedback = self.agent.process(
                                    f"The {action} action returned an error: {action_result['error']}. Please inform the customer and suggest alternatives."
                                )
                                response_text = error_feedback.get('response', response_text)
                                logger.info(f"Assistant response after error: {response_text}")
                                print(f"Assistant (after error): {response_text}")
                        elif action_result.get('is_closed'):
                            logger.warning(f"Business is closed: {action_result.get('message', 'Business is closed')}")
                            # Explicitly tell agent about closed day and suggest alternatives
                            closed_message = action_result.get('message', 'We are closed on that day.')
                            day_name = action_result.get('day_name', 'that day')
                            closed_feedback = self.agent.process(
                                f"The check_availability action returned: {closed_message} "
                                f"The business is closed on {day_name}. "
                                f"Please inform the customer clearly and suggest alternative days when we are open. "
                                f"Do not check availability again for the same day. Suggest Monday through Saturday instead."
                            )
                            response_text = closed_feedback.get('response', response_text)
                            logger.info(f"Assistant response for closed day: {response_text}")
                            print(f"Assistant (closed day): {response_text}")
                        elif action_result.get('success') or action_result.get('appointments') or action_result.get('available_slots') or action == 'reschedule_appointment':
                            logger.info("Action completed successfully")
                            # If availability check returned slots, inform agent and prompt to book
                            if action == 'check_availability' and action_result.get('count', 0) > 0:
                                # Slots are available - inform agent and suggest booking
                                available_slots = action_result.get('available_slots', [])
                                requested_time = action_params.get('time') or 'the requested time'
                                date_str = action_result.get('date')
                                day_name = action_result.get('day_name', 'that day')
                                
                                # Check if requested time is in available slots
                                time_available = False
                                if requested_time and available_slots:
                                    # Normalize time format for comparison
                                    requested_normalized = requested_time.replace(':', '').replace(' ', '').lower()
                                    for slot in available_slots:
                                        slot_normalized = slot.replace(':', '').replace(' ', '').lower()
                                        if requested_normalized in slot_normalized or slot_normalized in requested_normalized:
                                            time_available = True
                                            break
                                
                                if time_available or (not requested_time and len(available_slots) > 0):
                                    # Time is available - prompt agent to proceed with booking
                                    # Extract time from conversation context
                                    time_to_book = None
                                    if '10' in str(action_params.get('time', '')) or '10:00' in str(available_slots):
                                        time_to_book = '10:00'
                                    elif available_slots:
                                        time_to_book = available_slots[0]  # Use first available if no specific time
                                    
                                    booking_prompt = (
                                        f"SUCCESS: Availability confirmed! The time slot {time_to_book or 'requested time'} is available on {day_name} ({date_str}). "
                                        f"Available slots: {', '.join(available_slots[:5])}. "
                                        f"IMPORTANT: You must now book the appointment. If you have the customer's name and phone from the conversation, use book_appointment action immediately with: "
                                        f"date='{date_str}', time='{time_to_book or available_slots[0]}', service='Haircut', staff='Tony'. "
                                        f"If you don't have customer info yet, ask for name and phone number NOW, then book immediately after getting it."
                                    )
                                    booking_feedback = self.agent.process(booking_prompt)
                                    response_text = booking_feedback.get('response', response_text)
                                    logger.info(f"Assistant response after availability confirmed: {response_text}")
                                    print(f"Assistant (availability confirmed): {response_text}")
                                else:
                                    # Time not available but other slots are
                                    slots_prompt = (
                                        f"Availability check shows {len(available_slots)} slots available on {day_name}, "
                                        f"but the requested time may not be available. Available times: {', '.join(available_slots[:5])}. "
                                        f"Please inform the customer and suggest one of these available times, or ask if they'd like to book a different time."
                                    )
                                    slots_feedback = self.agent.process(slots_prompt)
                                    response_text = slots_feedback.get('response', response_text)
                                    logger.info(f"Assistant response for alternative slots: {response_text}")
                                    print(f"Assistant (alternative slots): {response_text}")
                            elif action == 'check_availability' and action_result.get('count', 0) == 0 and not action_result.get('is_closed'):
                                # No slots available but not closed - inform agent
                                no_slots_feedback = self.agent.process(
                                    f"The availability check found no available slots for the requested time. "
                                    f"Please inform the customer and suggest alternative times or days."
                                )
                                response_text = no_slots_feedback.get('response', response_text)
                                logger.info(f"Assistant response for no slots: {response_text}")
                                print(f"Assistant (no slots): {response_text}")
                            elif action == 'book_appointment' and action_result.get('success'):
                                # Booking succeeded - confirm with customer
                                booking_confirmation = (
                                    f"Appointment successfully booked! Details: {action_result.get('date')} at {action_result.get('time')}. "
                                    f"Please confirm the booking details with the customer and thank them."
                                )
                                confirm_feedback = self.agent.process(booking_confirmation)
                                response_text = confirm_feedback.get('response', response_text)
                                logger.info(f"Assistant response after booking: {response_text}")
                                print(f"Assistant (booking confirmed): {response_text}")
                            elif action == 'reschedule_appointment':
                                if action_result.get('requires_new_slot'):
                                    apt = action_result.get('appointment', {})
                                    reschedule_prompt = (
                                        f"The customer wants to reschedule. Current appointment: "
                                        f"{apt.get('date')} at {apt.get('time')} "
                                        f"(Service: {apt.get('service')}, Staff: {apt.get('staff')}). "
                                        f"Ask the customer for a new date and time, or offer to check the closest availability."
                                    )
                                    reschedule_feedback = self.agent.process(reschedule_prompt)
                                    response_text = reschedule_feedback.get('response', response_text)
                                    logger.info(f"Assistant response requesting new slot: {response_text}")
                                    print(f"Assistant (reschedule prompt): {response_text}")
                                elif action_result.get('requires_new_time'):
                                    available_slots = action_result.get('available_slots', [])
                                    date_str = action_result.get('date')
                                    day_name = action_result.get('day_name', 'that day')
                                    slots_prompt = (
                                        f"Reschedule availability for {day_name} ({date_str}): "
                                        f"{', '.join(available_slots[:10]) if available_slots else 'No slots'}. "
                                        f"Ask the customer to pick a time, or offer to check another day."
                                    )
                                    slots_feedback = self.agent.process(slots_prompt)
                                    response_text = slots_feedback.get('response', response_text)
                                    logger.info(f"Assistant response with reschedule slots: {response_text}")
                                    print(f"Assistant (reschedule slots): {response_text}")
                                elif action_result.get('success'):
                                    reschedule_confirmation = (
                                        f"Appointment rescheduled. New time: {action_result.get('new_date')} at {action_result.get('new_time')}. "
                                        f"Confirm the change with the customer and thank them."
                                    )
                                    reschedule_feedback = self.agent.process(reschedule_confirmation)
                                    response_text = reschedule_feedback.get('response', response_text)
                                    logger.info(f"Assistant response after reschedule: {response_text}")
                                    print(f"Assistant (rescheduled): {response_text}")
                            elif action == 'get_customer_appointments' and action_result.get('appointments'):
                                # Customer appointments retrieved - inform agent to share details
                                appointments = action_result.get('appointments', [])
                                if len(appointments) > 0:
                                    # Format appointment details for agent
                                    apt_details = []
                                    for apt in appointments:
                                        date_str = apt.get('date', 'Unknown date')
                                        time_str = apt.get('time', 'Unknown time')
                                        # Clean up time format (remove seconds if present)
                                        if ':' in time_str and time_str.count(':') > 1:
                                            time_parts = time_str.split(':')
                                            time_str = f"{time_parts[0]}:{time_parts[1]}"
                                        service = apt.get('service', 'Not specified')
                                        staff = apt.get('staff', 'Not specified')
                                        apt_id = apt.get('id')
                                        apt_details.append(f"Appointment ID {apt_id}: {date_str} at {time_str} (Service: {service}, Staff: {staff})")
                                    
                                    appointments_prompt = (
                                        f"SUCCESS: Found {len(appointments)} appointment(s) for this customer. "
                                        f"Details: {'; '.join(apt_details)}. "
                                        f"Please inform the customer about their appointment(s) clearly, including the date and time. "
                                        f"If they want to modify/reschedule, ask what new date and time they prefer, then use reschedule_appointment action with appointment_id={appointments[0].get('id')}."
                                    )
                                    appointments_feedback = self.agent.process(appointments_prompt)
                                    response_text = appointments_feedback.get('response', response_text)
                                    logger.info(f"Assistant response after getting appointments: {response_text}")
                                    print(f"Assistant (appointments found): {response_text}")
                                else:
                                    # No appointments found
                                    no_appointments_prompt = (
                                        f"No appointments found for this customer. "
                                        f"Please inform the customer that no upcoming appointments were found."
                                    )
                                    no_appointments_feedback = self.agent.process(no_appointments_prompt)
                                    response_text = no_appointments_feedback.get('response', response_text)
                                    logger.info(f"Assistant response for no appointments: {response_text}")
                                    print(f"Assistant (no appointments): {response_text}")
                            pass
                    except Exception as e:
                        logger.error(f"Exception during action execution: {e}", exc_info=True)
                        print(f"[ERROR executing action: {e}]")
                        error_feedback = self.agent.process(
                            f"An error occurred: {str(e)}. Please inform the customer."
                        )
                        response_text = error_feedback.get('response', response_text)
                
                # Speak
                logger.info("Speaking response...")
                self.tts.speak(response_text)
                
                # Check if conversation is complete
                conversation_complete = agent_decision.get('conversation_complete', False)
                if conversation_complete:
                    logger.info("Conversation marked as complete")
                
                turn_count += 1
                logger.info(f"Turn {turn_count} completed")
                
            except KeyboardInterrupt:
                logger.info("Call ended by user (KeyboardInterrupt)")
                print("\n\nCall ended by user.")
                break
            except Exception as e:
                logger.error(f"Error in voice loop: {e}", exc_info=True)
                print(f"\nError in voice loop: {e}")
                import traceback
                traceback.print_exc()
                error_msg = "I'm sorry, I encountered an error. Let's continue."
                print(f"Assistant: {error_msg}")
                self.tts.speak(error_msg)
        
        # Closing
        closing = "Thank you for calling. Have a great day!"
        print(f"\nAssistant: {closing}")
        self.tts.speak(closing)
        self._log_turn("assistant", closing)
        
        # Cleanup
        self.cleanup()
        self._finalize_call(conversation_complete)
    
    def cleanup(self):
        """Clean up resources."""
        logger.info("Cleaning up resources...")
        print("\nCleaning up...")
        self.stt.cleanup()
        logger.info("Voice assistant stopped.")
        print("Voice assistant stopped.")

    def _log_turn(self, role: str, text: str):
        """Append a turn to call transcript."""
        if text is None:
            return
        self.call_transcript.append({"role": role, "text": text})

    def _finalize_call(self, conversation_complete: bool):
        """Persist call transcript and outcome."""
        if not self.call_id:
            return
        outcome = self.call_outcome or ("completed" if conversation_complete else "inquiry")
        try:
            import json
            transcript_str = json.dumps(self.call_transcript)
            self.database.finalize_call(
                self.call_id,
                outcome=outcome,
                transcript=transcript_str,
                customer_id=self.call_customer_id,
                appointment_id=self.call_appointment_id
            )
            logger.info(f"Call {self.call_id} finalized with outcome {outcome}")
        except Exception as e:
            logger.warning(f"Failed to finalize call {self.call_id}: {e}")


def main():
    """Main entry point."""
    try:
        logger.info("Starting Voice Assistant...")
        loop = VoiceLoop()
        loop.run()
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
