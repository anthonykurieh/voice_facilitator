"""Backend tools/actions for the agent to call."""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, date, time, timedelta
from dateutil import parser as date_parser
from dateutil.tz import gettz
import re
from src.config import APP_TIMEZONE

logger = logging.getLogger(__name__)


class BackendTools:
    """Collection of backend actions the agent can call."""
    
    def __init__(self, database: Any, config: Any):
        """Initialize tools.
        
        Args:
            database: Database instance
            config: ConfigLoader instance
        """
        self.db = database
        self.config = config
        self.business_id = 1  # Assuming single business for now
    
    def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an action.
        
        Args:
            action: Action name
            params: Action parameters
            
        Returns:
            Action result
        """
        logger.info(f"Executing action: {action} with params: {params}")
        try:
            if action == "check_availability":
                result = self.check_availability(params)
            elif action == "book_appointment":
                result = self.book_appointment(params)
            elif action == "cancel_appointment":
                result = self.cancel_appointment(params)
            elif action == "get_customer_appointments":
                result = self.get_customer_appointments(params)
            elif action == "reschedule_appointment":
                result = self.reschedule_appointment(params)
            elif action == "get_services":
                result = self.get_services()
            elif action == "get_staff":
                result = self.get_staff()
            else:
                logger.warning(f"Unknown action: {action}")
                return {"error": f"Unknown action: {action}"}
            
            logger.info(f"Action {action} completed: {result}")
            return result
        except Exception as e:
            logger.error(f"Error executing action {action}: {e}", exc_info=True)
            return {"error": str(e)}
    
    def _get_today(self) -> date:
        """Get today's date in the configured timezone."""
        tz = gettz(APP_TIMEZONE)
        if tz:
            return datetime.now(tz).date()
        return date.today()
    
    def _parse_date(self, date_str: str) -> Optional[date]:
        """Parse natural language date string.
        
        Handles:
        - "today", "tomorrow", "yesterday"
        - "Monday", "this Monday", "next Monday" -> upcoming Monday (including today if today is Monday)
        - Specific dates like "December 29th, 2025" or "12/29/2025"
        - Always uses current year unless explicitly specified
        """
        if not date_str:
            return None
        
        tz = gettz(APP_TIMEZONE)
        now = datetime.now(tz) if tz else datetime.now()
        today = self._get_today()
        date_str_lower = date_str.lower().strip()
        
        # Handle relative dates first (before dateutil parser)
        if "today" in date_str_lower:
            return today
        elif "tomorrow" in date_str_lower:
            return date.fromordinal(today.toordinal() + 1)
        elif "yesterday" in date_str_lower:
            return date.fromordinal(today.toordinal() - 1)
        
        # Handle day of week: "Monday", "this Monday", "next Monday"
        days = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6
        }
        
        for day_name, day_num in days.items():
            if day_name in date_str_lower:
                current_weekday = today.weekday()
                days_ahead = day_num - current_weekday
                
                # Handle "this [day]" or just "[day]" - upcoming occurrence (including today)
                if "this " in date_str_lower or days_ahead == 0:
                    # If today is that day, return today; otherwise return next occurrence
                    if days_ahead == 0:
                        return today
                    elif days_ahead < 0:
                        days_ahead += 7
                    return date.fromordinal(today.toordinal() + days_ahead)
                # Handle "next [day]" - explicitly next week
                elif "next " in date_str_lower:
                    if days_ahead <= 0:
                        days_ahead += 7
                    return date.fromordinal(today.toordinal() + days_ahead)
                # Default: upcoming occurrence (including today)
                else:
                    if days_ahead < 0:
                        days_ahead += 7
                    elif days_ahead == 0:
                        return today
                    return date.fromordinal(today.toordinal() + days_ahead)
        
        # Try dateutil parser for specific dates (e.g., "December 29th, 2025", "12/29/2025")
        try:
            # Check if year is explicitly mentioned in the date string
            year_match = re.search(r'\b(19|20)\d{2}\b', date_str)
            has_explicit_year = year_match is not None
            
            # Use today as default to ensure current year is used when not specified
            parsed = date_parser.parse(date_str, default=now)
            parsed_date = parsed.date()
            
            # If parsed date is in the past and no explicit year was given, adjust to current/future year
            if parsed_date < today and not has_explicit_year:
                # Try current year first
                parsed_date_current_year = parsed_date.replace(year=today.year)
                if parsed_date_current_year >= today:
                    return parsed_date_current_year
                # If current year is still in past, try next year
                parsed_date_next_year = parsed_date.replace(year=today.year + 1)
                return parsed_date_next_year
            
            # If explicit year was given, use it as-is (even if in past - user might want historical dates)
            # If date is in future, use as-is
            return parsed_date
        except (ValueError, TypeError, AttributeError):
            return None
    
    def _parse_time(self, time_str: str) -> Optional[time]:
        """Parse natural language time string."""
        if not time_str:
            return None
        
        tz = gettz(APP_TIMEZONE)
        now = datetime.now(tz) if tz else datetime.now()
        
        try:
            # Try dateutil parser
            parsed = date_parser.parse(time_str, default=now)
            return parsed.time()
        except (ValueError, TypeError, AttributeError):
            # Try common patterns
            pass
        
        try:
            time_str_lower = time_str.lower()
            
            # Handle "morning", "afternoon", "evening"
            if "morning" in time_str_lower:
                return time(10, 0)
            elif "afternoon" in time_str_lower:
                return time(14, 0)
            elif "evening" in time_str_lower:
                return time(18, 0)
            
            # Try to extract hour:minute
            match = re.search(r'(\d{1,2}):?(\d{2})?', time_str)
            if match:
                hour = int(match.group(1))
                minute = int(match.group(2)) if match.group(2) else 0
                
                # Handle 12-hour format
                if "pm" in time_str_lower and hour < 12:
                    hour += 12
                elif "am" in time_str_lower and hour == 12:
                    hour = 0
                
                return time(hour, minute)
            
            return None
        except (ValueError, TypeError, AttributeError, IndexError):
            return None
    
    def check_availability(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Check available time slots."""
        logger.debug(f"check_availability called with params: {params}")
        date_str = params.get('date')
        staff_id = params.get('staff_id')
        duration_minutes = params.get('duration_minutes', 30)
        
        appointment_date = self._parse_date(date_str) if date_str else self._get_today()
        logger.info(f"Checking availability for date: {appointment_date}, staff_id: {staff_id}, duration: {duration_minutes}min")
        
        if not appointment_date:
            return {"error": "Could not parse date"}
        
        # Check if business is open on this day
        day_of_week = appointment_date.weekday()
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        day_name = day_names[day_of_week]
        
        # Check business hours
        hours = self.config.get_hours()
        day_hours = hours.get(day_name.lower())
        is_closed = not day_hours or day_hours.get('open') is None or day_hours.get('close') is None
        
        if is_closed:
            logger.warning(f"Business is closed on {day_name} ({appointment_date})")
            return {
                "date": appointment_date.isoformat(),
                "day_name": day_name,
                "available_slots": [],
                "count": 0,
                "is_closed": True,
                "message": f"Sorry, we're closed on {day_name}s."
            }
        
        slots = self.db.get_available_slots(
            appointment_date, 
            staff_id=staff_id,
            duration_minutes=duration_minutes
        )
        
        return {
            "date": appointment_date.isoformat(),
            "day_name": day_name,
            "available_slots": [
                slot['time'].strftime("%H:%M") for slot in slots
            ],
            "count": len(slots),
            "is_closed": False
        }
    
    def book_appointment(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Book an appointment."""
        date_str = params.get('date')
        time_str = params.get('time')
        service_name = params.get('service')
        staff_name = params.get('staff')
        customer_phone = params.get('customer_phone')
        customer_name = params.get('customer_name')
        
        appointment_date = self._parse_date(date_str) if date_str else None
        appointment_time = self._parse_time(time_str) if time_str else None
        
        if not appointment_date or not appointment_time:
            return {"error": "Date and time are required"}
        
        # Check if business is open on this day
        day_of_week = appointment_date.weekday()
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        day_name = day_names[day_of_week]
        
        hours = self.config.get_hours()
        day_hours = hours.get(day_name.lower())
        is_closed = not day_hours or day_hours.get('open') is None or day_hours.get('close') is None
        
        if is_closed:
            logger.warning(f"Attempted to book on closed day: {day_name} ({appointment_date})")
            return {
                "error": f"We're closed on {day_name}s. Please choose a different day.",
                "is_closed": True
            }
        
        # Get or create customer
        customer_id = None
        if customer_phone:
            customer_id = self.db.create_or_update_customer(
                customer_phone, 
                name=customer_name
            )
        
        # Get service ID
        service_id = None
        if service_name:
            services = self.config.get_services()
            for service in services:
                if service['name'].lower() == service_name.lower():
                    duration_minutes = service.get('duration_minutes', 30)
                    break
            else:
                duration_minutes = 30
        else:
            duration_minutes = 30
        
        # Get staff ID
        staff_id = None
        if staff_name:
            staff_list = self.config.get_staff()
            # In a real system, you'd look up staff in DB
            # For now, we'll use None and let the system assign
        
        # CRITICAL: Check if the time slot is still available before booking
        available_slots = self.db.get_available_slots(
            appointment_date,
            staff_id=staff_id,
            duration_minutes=duration_minutes
        )
        
        # Check if the requested time is in the available slots
        requested_time_str = appointment_time.strftime("%H:%M")
        slot_available = False
        for slot in available_slots:
            slot_time_str = slot['time'].strftime("%H:%M")
            if slot_time_str == requested_time_str:
                slot_available = True
                break
        
        if not slot_available:
            logger.warning(
                f"Attempted to book unavailable slot: {appointment_date} at {requested_time_str} "
                f"(staff_id={staff_id}, duration={duration_minutes}min)"
            )
            available_times = [slot['time'].strftime("%H:%M") for slot in available_slots[:10]]
            return {
                "error": f"The requested time slot ({requested_time_str}) is no longer available.",
                "date": appointment_date.isoformat(),
                "requested_time": requested_time_str,
                "available_slots": available_times,
                "count": len(available_slots),
                "suggestion": f"Available times include: {', '.join(available_times) if available_times else 'None'}"
            }
        
        # Slot is available - proceed with booking
        # Note: create_appointment also checks for conflicts as a safety net
        try:
            appointment_id = self.db.create_appointment(
                business_id=self.business_id,
                customer_id=customer_id,
                staff_id=staff_id,
                service_id=service_id,
                appointment_date=appointment_date,
                appointment_time=appointment_time,
                duration_minutes=duration_minutes
            )
            
            logger.info(f"Successfully booked appointment {appointment_id} for {appointment_date} at {requested_time_str}")
            
            return {
                "success": True,
                "appointment_id": appointment_id,
                "date": appointment_date.isoformat(),
                "time": appointment_time.strftime("%H:%M")
            }
        except ValueError as e:
            # Database-level conflict detected (safety net)
            logger.error(f"Database conflict detected during booking: {e}")
            # Re-check availability to get current slots
            available_slots = self.db.get_available_slots(
                appointment_date,
                staff_id=staff_id,
                duration_minutes=duration_minutes
            )
            available_times = [slot['time'].strftime("%H:%M") for slot in available_slots[:10]]
            return {
                "error": str(e),
                "date": appointment_date.isoformat(),
                "requested_time": requested_time_str,
                "available_slots": available_times,
                "count": len(available_slots),
                "suggestion": f"Available times include: {', '.join(available_times) if available_times else 'None'}"
            }
    
    def cancel_appointment(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Cancel an appointment."""
        appointment_id = params.get('appointment_id')
        customer_phone = params.get('customer_phone')
        
        if appointment_id:
            success = self.db.cancel_appointment(appointment_id)
            return {"success": success}
        elif customer_phone:
            # Get customer's appointments and cancel the next one
            customer = self.db.get_customer_by_phone(customer_phone)
            if customer:
                appointments = self.db.get_customer_appointments(customer['id'], upcoming_only=True)
                if appointments:
                    success = self.db.cancel_appointment(appointments[0]['id'])
                    return {"success": success}
            return {"error": "No upcoming appointments found"}
        else:
            return {"error": "appointment_id or customer_phone required"}
    
    def get_customer_appointments(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get customer's appointments."""
        customer_phone = params.get('customer_phone')
        
        if not customer_phone:
            return {"error": "customer_phone required"}
        
        customer = self.db.get_customer_by_phone(customer_phone)
        if not customer:
            return {"appointments": []}
        
        appointments = self.db.get_customer_appointments(customer['id'], upcoming_only=True)
        
        result = {
            "appointments": []
        }
        
        for apt in appointments:
            # Format time properly - handle timedelta from MySQL TIME columns
            apt_time = apt['appointment_time']
            if isinstance(apt_time, timedelta):
                # Convert timedelta to time
                total_seconds = int(apt_time.total_seconds())
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                time_str = f"{hours:02d}:{minutes:02d}"
            elif isinstance(apt_time, time):
                time_str = apt_time.strftime("%H:%M")
            elif isinstance(apt_time, str):
                # If it's already a string, clean it up
                if ':' in apt_time:
                    parts = apt_time.split(':')
                    time_str = f"{parts[0].zfill(2)}:{parts[1].zfill(2)}"
                else:
                    time_str = apt_time
            else:
                time_str = str(apt_time)
            
            # Format date
            apt_date = apt['appointment_date']
            if isinstance(apt_date, date):
                date_str = apt_date.isoformat()
            else:
                date_str = str(apt_date)
            
            result["appointments"].append({
                "id": apt['id'],
                "date": date_str,
                "time": time_str,
                "service": apt.get('service_name'),
                "staff": apt.get('staff_name')
            })
        
        return result
    
    def reschedule_appointment(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Reschedule an appointment.
        
        Can use either appointment_id OR customer_phone to find the appointment.
        """
        appointment_id = params.get('appointment_id')
        customer_phone = params.get('customer_phone')
        new_date_str = params.get('new_date')
        new_time_str = params.get('new_time')
        
        if not new_date_str or not new_time_str:
            return {"error": "new_date and new_time are required"}
        
        # If no appointment_id provided, try to find it via customer_phone
        if not appointment_id and customer_phone:
            customer = self.db.get_customer_by_phone(customer_phone)
            if customer:
                appointments = self.db.get_customer_appointments(customer['id'], upcoming_only=True)
                if appointments:
                    appointment_id = appointments[0]['id']
                else:
                    return {"error": "No upcoming appointments found for this customer"}
            else:
                return {"error": "Customer not found"}
        
        if not appointment_id:
            return {"error": "appointment_id or customer_phone required"}
        
        # Validate appointment_id is a number, not a placeholder string
        try:
            appointment_id = int(appointment_id)
        except (ValueError, TypeError):
            return {"error": f"Invalid appointment_id: {appointment_id}. Must be a number."}
        
        new_date = self._parse_date(new_date_str)
        new_time = self._parse_time(new_time_str)
        
        if not new_date or not new_time:
            return {"error": "Could not parse new date or time"}
        
        # Get the existing appointment to preserve service/staff info
        # First, cancel the old appointment
        cancel_result = self.db.cancel_appointment(appointment_id)
        if not cancel_result:
            return {"error": "Could not cancel existing appointment"}
        
        # Note: The agent should call book_appointment separately with the new date/time
        # and the same service/staff from the original appointment
        return {
            "success": True,
            "message": "Original appointment cancelled. Please book the new appointment with book_appointment action.",
            "cancelled_appointment_id": appointment_id
        }
    
    def get_services(self) -> Dict[str, Any]:
        """Get list of services."""
        services = self.config.get_services()
        return {
            "services": [
                {
                    "name": s['name'],
                    "duration_minutes": s.get('duration_minutes', 30),
                    "price": s.get('price', 0)
                }
                for s in services
            ]
        }
    
    def get_staff(self) -> Dict[str, Any]:
        """Get list of staff."""
        staff = self.config.get_staff()
        return {
            "staff": [
                {"name": s['name']}
                for s in staff if s.get('available', True)
            ]
        }

