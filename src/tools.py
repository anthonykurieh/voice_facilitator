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
        # Cache config services/staff for fallback
        self._config_services = {s.get("name", "").lower(): s for s in self.config.get_services()}
        self._config_staff = [s for s in self.config.get_staff() if s.get("available", True)]
    
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
        days = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6
        }
        desired_weekday = next((num for name, num in days.items() if name in date_str_lower), None)
        
        # Handle relative dates first (before dateutil parser)
        if "today" in date_str_lower:
            logger.info("parse_date resolved relative", extra={"input": date_str, "result": today.isoformat()})
            return today
        elif "tomorrow" in date_str_lower:
            logger.info("parse_date resolved relative", extra={"input": date_str, "result": (today.toordinal() + 1)})
            return date.fromordinal(today.toordinal() + 1)
        elif "yesterday" in date_str_lower:
            logger.info("parse_date resolved relative", extra={"input": date_str, "result": (today.toordinal() - 1)})
            return date.fromordinal(today.toordinal() - 1)
        
        # Handle day of week: "Monday", "this Monday", "next Monday"
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
                parsed_date = parsed_date_next_year
            
            # Align to requested weekday if provided and no explicit year (common STT mismatch)
            if desired_weekday is not None and not has_explicit_year:
                if parsed_date.weekday() != desired_weekday:
                    days_ahead = (desired_weekday - parsed_date.weekday()) % 7
                    parsed_date = date.fromordinal(parsed_date.toordinal() + days_ahead)
                    logger.info(
                        "parse_date adjusted to match weekday",
                        extra={"input": date_str, "adjusted": parsed_date.isoformat(), "desired_weekday": desired_weekday}
                    )
            
            logger.info(
                "parse_date parsed",
                extra={
                    "input": date_str,
                    "result": parsed_date.isoformat(),
                    "has_explicit_year": has_explicit_year,
                    "desired_weekday": desired_weekday
                }
            )
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
            logger.info("parse_time parsed", extra={"input": time_str, "result": parsed.time().isoformat()})
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

    def _get_service_from_config(self, service_name: Optional[str]) -> Optional[Dict[str, Any]]:
        """Return service dict from config by name (case-insensitive)."""
        if not service_name:
            return None
        service = self._config_services.get(service_name.lower())
        if service:
            return service
        # fallback exact match search
        for svc in self.config.get_services():
            if svc.get('name', '').lower() == service_name.lower():
                return svc
        return None

    def _resolve_service(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve service id/name/price/duration from params or config."""
        service_id = params.get('service_id')
        service_name = params.get('service') or params.get('service_name')
        duration_minutes = params.get('duration_minutes')
        price = None

        # If service_id provided, fetch from DB
        if service_id:
            try:
                service_id_int = int(service_id)
                svc = self.db.get_service_by_id(service_id_int)
                if svc:
                    return {
                        "service_id": svc["id"],
                        "service_name": svc.get("name"),
                        "price": svc.get("price"),
                        "duration": duration_minutes or svc.get("duration_minutes")
                    }
            except Exception:
                pass

        # Try by name
        svc = self._get_service_from_config(service_name) if service_name else None
        if not svc and service_name:
            db_svc = self.db.get_service_by_name(service_name)
            if db_svc:
                svc = {"name": db_svc.get("name"), "duration_minutes": db_svc.get("duration_minutes"), "price": db_svc.get("price"), "id": db_svc.get("id")}

        # Default to first configured service if none provided
        if not svc:
            services = self.config.get_services()
            if services:
                svc = services[0]

        return {
            "service_id": svc.get("id") if svc else None,
            "service_name": svc.get("name") if svc else (service_name or "Unknown"),
            "price": price if price is not None else (svc.get("price") if svc else None),
            "duration": duration_minutes or (svc.get("duration_minutes") if svc else 30)
        }

    def _resolve_staff(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve staff id/name from params or config, honoring availability."""
        staff_id = params.get('staff_id')
        staff_name = params.get('staff')

        if staff_id:
            try:
                staff_id_int = int(staff_id)
                staff = self.db.get_staff_by_id(staff_id_int)
                if staff:
                    if not staff.get("available", True):
                        return {"error": f"Staff {staff.get('name')} is not available."}
                    return {"staff_id": staff["id"], "staff_name": staff.get("name")}
            except Exception:
                pass

        if staff_name:
            db_staff = self.db.get_staff_by_name(staff_name)
            if db_staff:
                if not db_staff.get("available", True):
                    return {"error": f"Staff {db_staff.get('name')} is not available."}
                return {"staff_id": db_staff["id"], "staff_name": db_staff.get("name")}

        # Fallback to first available staff in config
        available_staff = self.db.get_available_staff()
        if available_staff:
            first_staff = available_staff[0]
            return {"staff_id": first_staff.get("id"), "staff_name": first_staff.get("name")}
        if self._config_staff:
            staff = self._config_staff[0]
            return {"staff_id": None, "staff_name": staff.get("name")}
        return {"staff_id": None, "staff_name": staff_name or "Unassigned"}
    
    def check_availability(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Check available time slots."""
        logger.debug(f"check_availability called with params: {params}")
        date_str = params.get('date')
        staff_id = params.get('staff_id')
        service_name = params.get('service')
        duration_minutes = params.get('duration_minutes')

        # Use service duration from config if provided and no explicit duration override
        if duration_minutes is None and service_name:
            service = self._get_service_from_config(service_name)
            if service:
                duration_minutes = service.get('duration_minutes')
        if duration_minutes is None:
            duration_minutes = 30
        
        appointment_date = self._parse_date(date_str) if date_str else self._get_today()
        logger.info(
            "check_availability.start",
            extra={
                "input_date": date_str,
                "parsed_date": appointment_date.isoformat() if appointment_date else None,
                "staff_id": staff_id,
                "duration_minutes": duration_minutes,
                "service": service_name
            }
        )
        
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
        service_info = self._resolve_service(params)
        service_name = service_info.get("service_name")
        staff_info = self._resolve_staff(params)
        if staff_info.get("error"):
            return {"error": staff_info.get("error")}
        staff_name = staff_info.get("staff_name")
        staff_id = staff_info.get("staff_id")
        customer_phone = params.get('customer_phone')
        customer_name = params.get('customer_name')
        
        # Always require customer identity for booking
        missing_fields = []
        if not customer_phone:
            missing_fields.append("customer_phone")
        if not customer_name:
            missing_fields.append("customer_name")
        if missing_fields:
            return {
                "error": "Missing required customer information.",
                "missing_fields": missing_fields,
                "message": "Please provide the customer's name and phone number to book."
            }
        
        appointment_date = self._parse_date(date_str) if date_str else None
        appointment_time = self._parse_time(time_str) if time_str else None
        
        if not appointment_date or not appointment_time:
            return {"error": "Date and time are required"}
        
        logger.info(
            "book_appointment.start",
            extra={
                "input_date": date_str,
                "parsed_date": appointment_date.isoformat() if appointment_date else None,
                "input_time": time_str,
                "parsed_time": appointment_time.strftime("%H:%M") if appointment_time else None,
                "service": service_name,
                "staff": staff_name,
                "customer_phone": customer_phone,
                "customer_name": customer_name
            }
        )
        
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

        # Service/staff resolution
        service_id = service_info.get("service_id")
        duration_minutes = service_info.get("duration") or params.get('duration_minutes') or 30
        service_price = service_info.get("price")
        if staff_id is None and staff_name:
            db_staff = self.db.get_staff_by_name(staff_name)
            if db_staff:
                staff_id = db_staff['id']
        
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
                duration_minutes=duration_minutes,
                service_name=service_name,
                service_price=service_price
            )
            
            logger.info(f"Successfully booked appointment {appointment_id} for {appointment_date} at {requested_time_str}")
            
            return {
                "success": True,
                "appointment_id": appointment_id,
                "date": appointment_date.isoformat(),
                "time": appointment_time.strftime("%H:%M"),
                "customer_id": customer_id,
                "service_id": service_id,
                "service_name": service_name,
                "staff_id": staff_id,
                "staff_name": staff_name
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
        If new_date/new_time are not provided, returns the current appointment info
        so the agent can ask the customer for a new slot.
        """
        appointment_id = params.get('appointment_id')
        customer_phone = params.get('customer_phone')
        new_date_str = params.get('new_date')
        new_time_str = params.get('new_time')
        
        # Locate the existing appointment
        target_appointment = None
        
        if appointment_id:
            try:
                appointment_id = int(appointment_id)
            except (ValueError, TypeError):
                return {"error": f"Invalid appointment_id: {appointment_id}. Must be a number."}
            target_appointment = self.db.get_appointment_by_id(appointment_id)
        
        if not target_appointment and customer_phone:
            customer = self.db.get_customer_by_phone(customer_phone)
            if not customer:
                return {"error": "Customer not found"}
            appointments = self.db.get_customer_appointments(customer['id'], upcoming_only=True)
            if appointments:
                target_appointment = appointments[0]
            else:
                return {"error": "No upcoming appointments found for this customer"}
        
        if not target_appointment:
            return {"error": "appointment_id or customer_phone required"}
        
        # Prepare current appointment info for the agent/customer
        current_time = target_appointment.get('appointment_time')
        if isinstance(current_time, timedelta):
            total_seconds = int(current_time.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            current_time_str = f"{hours:02d}:{minutes:02d}"
        elif isinstance(current_time, time):
            current_time_str = current_time.strftime("%H:%M")
        else:
            current_time_str = str(current_time)
        
        current_date = target_appointment.get('appointment_date')
        current_date_str = current_date.isoformat() if isinstance(current_date, date) else str(current_date)
        
        # If no new slot specified, return current appointment so the agent can ask for a new date/time
        if not new_date_str and not new_time_str:
            return {
                "appointment": {
                    "id": target_appointment['id'],
                    "date": current_date_str,
                    "time": current_time_str,
                    "service": target_appointment.get('service_name'),
                    "staff": target_appointment.get('staff_name')
                },
                "message": "Provide new_date and new_time to reschedule.",
                "requires_new_slot": True
            }
        
        # Parse new date/time inputs
        new_date = self._parse_date(new_date_str) if new_date_str else None
        new_time = self._parse_time(new_time_str) if new_time_str else None
        
        if new_time and not new_date:
            return {"error": "new_date is required when providing new_time"}
        
        # If only date provided, surface availability for that date (for “closest availability” requests)
        duration_minutes = (
            target_appointment.get('duration_minutes') or
            target_appointment.get('service_duration') or
            30
        )
        if new_date and not new_time:
            day_name = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][new_date.weekday()]
            available_slots = self.db.get_available_slots(
                new_date,
                staff_id=target_appointment.get('staff_id'),
                duration_minutes=duration_minutes
            )
            logger.info(
                "reschedule_appointment.availability",
                extra={
                    "appointment_id": target_appointment.get('id'),
                    "new_date": new_date.isoformat(),
                    "available_slots": [slot['time'].strftime("%H:%M") for slot in available_slots],
                    "count": len(available_slots),
                    "staff_id": target_appointment.get('staff_id'),
                    "duration_minutes": duration_minutes
                }
            )
            return {
                "appointment": {
                    "id": target_appointment['id'],
                    "date": current_date_str,
                    "time": current_time_str,
                    "service": target_appointment.get('service_name'),
                    "staff": target_appointment.get('staff_name')
                },
                "date": new_date.isoformat(),
                "day_name": day_name,
                "available_slots": [slot['time'].strftime("%H:%M") for slot in available_slots],
                "count": len(available_slots),
                "requires_new_time": True,
                "message": "Select a new time from available_slots to complete reschedule."
            }
        
        if not new_date or not new_time:
            return {"error": "Could not parse new date or time"}
        
        # Check business hours/closure for the new date
        day_of_week = new_date.weekday()
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        day_name = day_names[day_of_week]
        hours = self.config.get_hours()
        day_hours = hours.get(day_name.lower())
        is_closed = not day_hours or day_hours.get('open') is None or day_hours.get('close') is None
        
        if is_closed:
            return {
                "error": f"We're closed on {day_name}s. Please choose a different day.",
                "is_closed": True,
                "day_name": day_name,
                "date": new_date.isoformat()
            }
        
        # Check availability for the requested new slot
        available_slots = self.db.get_available_slots(
            new_date,
            staff_id=target_appointment.get('staff_id'),
            duration_minutes=duration_minutes
        )
        requested_time_str = new_time.strftime("%H:%M")
        time_available = any(slot['time'].strftime("%H:%M") == requested_time_str for slot in available_slots)
        logger.info(
            "reschedule_appointment.check_slot",
            extra={
                "appointment_id": target_appointment.get('id'),
                "new_date": new_date.isoformat(),
                "requested_time": requested_time_str,
                "time_available": time_available,
                "available_slots": [slot['time'].strftime("%H:%M") for slot in available_slots],
                "staff_id": target_appointment.get('staff_id'),
                "duration_minutes": duration_minutes
            }
        )
        
        if not time_available:
            available_times = [slot['time'].strftime("%H:%M") for slot in available_slots[:10]]
            return {
                "error": f"The requested time slot ({requested_time_str}) is not available.",
                "date": new_date.isoformat(),
                "requested_time": requested_time_str,
                "available_slots": available_times,
                "count": len(available_slots),
                "suggestion": f"Available times include: {', '.join(available_times) if available_times else 'None'}"
            }
        
        # Book new appointment first to avoid losing the original if booking fails
        try:
            new_appointment_id = self.db.create_appointment(
                business_id=self.business_id,
                customer_id=target_appointment.get('customer_id'),
                staff_id=target_appointment.get('staff_id'),
                service_id=target_appointment.get('service_id'),
                appointment_date=new_date,
                appointment_time=new_time,
                duration_minutes=duration_minutes
            )
            logger.info(
                "reschedule_appointment.booked_new",
                extra={
                    "old_appointment_id": target_appointment.get('id'),
                    "new_appointment_id": new_appointment_id,
                    "new_date": new_date.isoformat(),
                    "new_time": requested_time_str
                }
            )
        except ValueError as e:
            available_times = [slot['time'].strftime("%H:%M") for slot in available_slots[:10]]
            return {
                "error": str(e),
                "date": new_date.isoformat(),
                "requested_time": requested_time_str,
                "available_slots": available_times,
                "count": len(available_slots),
                "suggestion": f"Available times include: {', '.join(available_times) if available_times else 'None'}"
            }
        
        # Cancel the old appointment now that the new one is secured
        self.db.cancel_appointment(target_appointment['id'])
        
        return {
            "success": True,
            "message": "Appointment rescheduled successfully.",
            "old_appointment_id": target_appointment['id'],
            "new_appointment_id": new_appointment_id,
            "new_date": new_date.isoformat(),
            "new_time": requested_time_str
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
