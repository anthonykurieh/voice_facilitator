"""Database layer for MySQL persistence."""
import logging
import pymysql
import os
from typing import Dict, Any, List, Optional
from datetime import datetime, date, time, timedelta
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class Database:
    """MySQL database connection and schema management."""
    
    def __init__(self):
        """Initialize database connection from environment variables."""
        self.host = os.getenv('DB_HOST', os.getenv('MYSQL_HOST', 'localhost'))
        self.port = int(os.getenv('DB_PORT', os.getenv('MYSQL_PORT', 3306)))
        self.user = os.getenv('DB_USER', os.getenv('MYSQL_USER', 'root'))
        self.password = os.getenv('DB_PASSWORD', os.getenv('MYSQL_PASSWORD', ''))
        self.database = os.getenv('DB_NAME', os.getenv('MYSQL_DATABASE', 'voice_assistant'))
        self._connection = None
    
    @contextmanager
    def get_connection(self):
        """Get database connection context manager."""
        conn = pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        try:
            yield conn
        finally:
            conn.close()
    
    def initialize_schema(self):
        """Create database schema if it doesn't exist."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Businesses table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS businesses (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    type VARCHAR(100),
                    phone VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            """)
            
            # Customers table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS customers (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    phone VARCHAR(50) UNIQUE,
                    name VARCHAR(255),
                    email VARCHAR(255),
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            """)
            
            # Services table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS services (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    business_id INT,
                    name VARCHAR(255) NOT NULL,
                    duration_minutes INT NOT NULL,
                    price DECIMAL(10, 2),
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (business_id) REFERENCES businesses(id) ON DELETE CASCADE
                )
            """)
            
            # Staff table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS staff (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    business_id INT,
                    name VARCHAR(255) NOT NULL,
                    available BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (business_id) REFERENCES businesses(id) ON DELETE CASCADE
                )
            """)
            
            # Business hours table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS business_hours (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    business_id INT,
                    day_of_week INT NOT NULL,  -- 0=Monday, 6=Sunday
                    open_time TIME,
                    close_time TIME,
                    is_closed BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (business_id) REFERENCES businesses(id) ON DELETE CASCADE,
                    UNIQUE KEY unique_day (business_id, day_of_week)
                )
            """)
            
            # Appointments table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS appointments (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    business_id INT,
                    customer_id INT,
                    staff_id INT,
                    service_id INT,
                    appointment_date DATE NOT NULL,
                    appointment_time TIME NOT NULL,
                    duration_minutes INT NOT NULL,
                    status VARCHAR(50) DEFAULT 'scheduled',  -- scheduled, completed, cancelled, no_show
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (business_id) REFERENCES businesses(id) ON DELETE CASCADE,
                    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL,
                    FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE SET NULL,
                    FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE SET NULL,
                    INDEX idx_date_time (appointment_date, appointment_time),
                    INDEX idx_customer (customer_id),
                    INDEX idx_staff (staff_id)
                )
            """)
            
            # Calls/Conversations table (optional but encouraged)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS calls (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    business_id INT,
                    customer_id INT,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ended_at TIMESTAMP NULL,
                    outcome VARCHAR(255),  -- booked, cancelled, inquiry, etc.
                    transcript TEXT,
                    FOREIGN KEY (business_id) REFERENCES businesses(id) ON DELETE CASCADE,
                    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL
                )
            """)
            
            conn.commit()
    
    def get_available_slots(self, date: date, staff_id: Optional[int] = None, 
                           duration_minutes: int = 30) -> List[Dict[str, Any]]:
        """Get available time slots for a given date.
        
        Args:
            date: Date to check availability
            staff_id: Optional staff member ID to filter by
            duration_minutes: Duration of appointment in minutes
            
        Returns:
            List of available time slots
        """
        logger.info(f"Getting available slots for {date}, staff_id={staff_id}, duration={duration_minutes}min")
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get business hours for the day
            day_of_week = date.weekday()  # 0=Monday, 6=Sunday
            cursor.execute("""
                SELECT open_time, close_time, is_closed
                FROM business_hours
                WHERE business_id = 1 AND day_of_week = %s
            """, (day_of_week,))
            
            hours = cursor.fetchone()
            if not hours or hours['is_closed']:
                logger.warning(f"Business is closed on {date} (day of week: {day_of_week})")
                return []  # Return empty list - caller should check if business is closed
            
            open_time = hours['open_time']
            close_time = hours['close_time']
            logger.debug(f"Raw times from DB - open_time type: {type(open_time)}, value: {open_time}, close_time type: {type(close_time)}, value: {close_time}")
            
            # Convert timedelta to time if needed (MySQL TIME returns as timedelta)
            if isinstance(open_time, timedelta):
                total_seconds = int(open_time.total_seconds())
                hours_part = total_seconds // 3600
                minutes_part = (total_seconds % 3600) // 60
                open_time = time(hours_part, minutes_part)
                logger.debug(f"Converted open_time from timedelta to time: {open_time}")
            
            if isinstance(close_time, timedelta):
                total_seconds = int(close_time.total_seconds())
                hours_part = total_seconds // 3600
                minutes_part = (total_seconds % 3600) // 60
                close_time = time(hours_part, minutes_part)
                logger.debug(f"Converted close_time from timedelta to time: {close_time}")
            
            logger.info(f"Business hours: {open_time} - {close_time}")
            
            # Get existing appointments
            query = """
                SELECT appointment_time, duration_minutes
                FROM appointments
                WHERE appointment_date = %s AND status = 'scheduled'
            """
            params = [date]
            
            if staff_id:
                query += " AND staff_id = %s"
                params.append(staff_id)
            
            cursor.execute(query, params)
            appointments = cursor.fetchall()
            logger.info(f"Found {len(appointments)} existing appointments")
            
            # Calculate available slots
            slots = []
            current = datetime.combine(date, open_time)
            end_time = datetime.combine(date, close_time)
            logger.debug(f"Checking slots from {current} to {end_time}")
            
            while current + timedelta(minutes=duration_minutes) <= end_time:
                slot_end = current + timedelta(minutes=duration_minutes)
                slot_available = True
                
                # Check for conflicts
                for apt in appointments:
                    apt_time = apt['appointment_time']
                    # Convert timedelta to time if needed
                    if isinstance(apt_time, timedelta):
                        total_seconds = int(apt_time.total_seconds())
                        hours_part = total_seconds // 3600
                        minutes_part = (total_seconds % 3600) // 60
                        apt_time = time(hours_part, minutes_part)
                    apt_start = datetime.combine(date, apt_time)
                    apt_end = apt_start + timedelta(minutes=apt['duration_minutes'])
                    
                    if not (slot_end <= apt_start or current >= apt_end):
                        slot_available = False
                        break
                
                if slot_available:
                    slots.append({
                        'time': current.time(),
                        'datetime': current
                    })
                
                # Move to next 15-minute slot
                current += timedelta(minutes=15)
            
            logger.info(f"Found {len(slots)} available slots")
            return slots
    
    def create_appointment(self, business_id: int, customer_id: Optional[int],
                          staff_id: Optional[int], service_id: Optional[int],
                          appointment_date: date, appointment_time: time,
                          duration_minutes: int, notes: Optional[str] = None) -> int:
        """Create a new appointment.
        
        CRITICAL: This function checks for conflicts before booking to prevent double-booking.
        
        Returns:
            Appointment ID
            
        Raises:
            ValueError: If the time slot is already booked
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # CRITICAL: Check for overlapping appointments before inserting
            # Calculate appointment end time
            appointment_start = datetime.combine(appointment_date, appointment_time)
            appointment_end = appointment_start + timedelta(minutes=duration_minutes)
            
            # Check for conflicts with existing appointments
            conflict_query = """
                SELECT id, appointment_time, duration_minutes
                FROM appointments
                WHERE appointment_date = %s 
                  AND status = 'scheduled'
                  AND (
                    (staff_id = %s OR (%s IS NULL AND staff_id IS NULL))
                    OR staff_id IS NULL
                    OR %s IS NULL
                  )
            """
            cursor.execute(conflict_query, (
                appointment_date,
                staff_id, staff_id,  # Check same staff or both null
                staff_id
            ))
            existing_appointments = cursor.fetchall()
            
            # Check for time overlaps
            for existing in existing_appointments:
                existing_time = existing['appointment_time']
                # Convert timedelta to time if needed
                if isinstance(existing_time, timedelta):
                    total_seconds = int(existing_time.total_seconds())
                    hours_part = total_seconds // 3600
                    minutes_part = (total_seconds % 3600) // 60
                    existing_time = time(hours_part, minutes_part)
                
                existing_start = datetime.combine(appointment_date, existing_time)
                existing_end = existing_start + timedelta(minutes=existing['duration_minutes'])
                
                # Check if appointments overlap
                if not (appointment_end <= existing_start or appointment_start >= existing_end):
                    logger.error(
                        f"Appointment conflict detected: "
                        f"Requested {appointment_start} to {appointment_end} "
                        f"overlaps with existing appointment {existing['id']} "
                        f"({existing_start} to {existing_end})"
                    )
                    raise ValueError(
                        f"Time slot is already booked. The requested time ({appointment_time.strftime('%H:%M')}) "
                        f"conflicts with an existing appointment."
                    )
            
            # No conflicts - proceed with booking
            cursor.execute("""
                INSERT INTO appointments 
                (business_id, customer_id, staff_id, service_id, appointment_date, 
                 appointment_time, duration_minutes, notes, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'scheduled')
            """, (business_id, customer_id, staff_id, service_id, 
                  appointment_date, appointment_time, duration_minutes, notes))
            conn.commit()
            appointment_id = cursor.lastrowid
            logger.info(
                f"Successfully created appointment {appointment_id} "
                f"for {appointment_date} at {appointment_time}"
            )
            return appointment_id
    
    def get_customer_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Get customer by phone number."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM customers WHERE phone = %s
            """, (phone,))
            return cursor.fetchone()
    
    def create_or_update_customer(self, phone: str, name: Optional[str] = None,
                                  email: Optional[str] = None) -> int:
        """Create or update customer.
        
        Returns:
            Customer ID
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            customer = self.get_customer_by_phone(phone)
            
            if customer:
                cursor.execute("""
                    UPDATE customers 
                    SET name = COALESCE(%s, name), email = COALESCE(%s, email)
                    WHERE id = %s
                """, (name, email, customer['id']))
                conn.commit()
                return customer['id']
            else:
                cursor.execute("""
                    INSERT INTO customers (phone, name, email)
                    VALUES (%s, %s, %s)
                """, (phone, name, email))
                conn.commit()
                return cursor.lastrowid
    
    def get_customer_appointments(self, customer_id: int, 
                                  upcoming_only: bool = True) -> List[Dict[str, Any]]:
        """Get appointments for a customer."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT a.*, s.name as service_name, st.name as staff_name
                FROM appointments a
                LEFT JOIN services s ON a.service_id = s.id
                LEFT JOIN staff st ON a.staff_id = st.id
                WHERE a.customer_id = %s
            """
            if upcoming_only:
                query += " AND a.appointment_date >= CURDATE() AND a.status = 'scheduled'"
            query += " ORDER BY a.appointment_date, a.appointment_time"
            
            cursor.execute(query, (customer_id,))
            return cursor.fetchall()
    
    def cancel_appointment(self, appointment_id: int) -> bool:
        """Cancel an appointment."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE appointments 
                SET status = 'cancelled' 
                WHERE id = %s
            """, (appointment_id,))
            conn.commit()
            return cursor.rowcount > 0

