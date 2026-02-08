"""Helper script to initialize database with business data from config."""
import os
import sys
try:
    from dotenv import load_dotenv
except ImportError:
    from python_dotenv import load_dotenv
from src.config_loader import ConfigLoader
from src.database import Database
from datetime import datetime

load_dotenv()


def init_business_data():
    """Initialize database with business data from config."""
    
    config_path = os.getenv('CONFIG_FILE', 'config/business_config.yaml')
    
    # Check if config file exists, if not, try to create from example
    if not os.path.exists(config_path):
        example_path = config_path + '.example'
        if os.path.exists(example_path):
            print(f"Config file not found. Creating from example: {example_path}")
            import shutil
            shutil.copy(example_path, config_path)
            print(f"Created {config_path} from example. Please customize it for your business.")
        else:
            raise FileNotFoundError(
                f"Config file not found: {config_path}\n"
                f"Please create it or copy from an example file."
            )
    
    config = ConfigLoader(config_path)
    db = Database()
    
    # Initialize schema
    print("Creating database schema...")
    db.initialize_schema()
    
    # Insert or sync business data
    with db.get_connection() as conn:
        cursor = conn.cursor()

        # Upsert business
        business_name = config.get_business_name()
        business_type = config.get_business_type()
        business_phone = config.get('business.phone')
        business_timezone = config.get('business.timezone')
        business_address = config.get('business.address')
        business_website = config.get('business.website')

        cursor.execute("SELECT id FROM businesses WHERE id = 1")
        if cursor.fetchone():
            cursor.execute("""
                UPDATE businesses
                SET name = %s,
                    type = %s,
                    phone = %s,
                    timezone = %s,
                    address = %s,
                    website = %s
                WHERE id = 1
            """, (business_name, business_type, business_phone, business_timezone, business_address, business_website))
        else:
            cursor.execute("""
                INSERT INTO businesses (id, name, type, phone, timezone, address, website)
                VALUES (1, %s, %s, %s, %s, %s, %s)
            """, (business_name, business_type, business_phone, business_timezone, business_address, business_website))
        
        # Upsert services
        services = config.get_services()
        service_names = set()
        for service in services:
            name = service['name']
            service_names.add(name.lower())
            cursor.execute("""
                SELECT id FROM services WHERE business_id = 1 AND LOWER(name) = LOWER(%s)
            """, (name,))
            existing = cursor.fetchone()
            if existing:
                cursor.execute("""
                    UPDATE services
                    SET duration_minutes = %s, price = %s, active = TRUE
                    WHERE id = %s
                """, (
                    service.get('duration_minutes', 30),
                    service.get('price', 0),
                    existing['id']
                ))
            else:
                cursor.execute("""
                    INSERT INTO services (business_id, name, duration_minutes, price, active)
                    VALUES (1, %s, %s, %s, TRUE)
                """, (
                    name,
                    service.get('duration_minutes', 30),
                    service.get('price', 0)
                ))
        if service_names:
            placeholders = ", ".join(["%s"] * len(service_names))
            cursor.execute(
                f"UPDATE services SET active = FALSE WHERE business_id = 1 AND LOWER(name) NOT IN ({placeholders})",
                tuple(service_names)
            )
        else:
            cursor.execute("UPDATE services SET active = FALSE WHERE business_id = 1")
        
        # Upsert staff
        staff = config.get_staff()
        staff_names = set()
        for staff_member in staff:
            name = staff_member['name']
            staff_names.add(name.lower())
            cursor.execute("""
                SELECT id FROM staff WHERE business_id = 1 AND LOWER(name) = LOWER(%s)
            """, (name,))
            existing = cursor.fetchone()
            if existing:
                cursor.execute("""
                    UPDATE staff
                    SET available = %s, email = %s
                    WHERE id = %s
                """, (
                    staff_member.get('available', True),
                    staff_member.get('email'),
                    existing['id']
                ))
            else:
                cursor.execute("""
                    INSERT INTO staff (business_id, name, available, email)
                    VALUES (1, %s, %s, %s)
                """, (
                    name,
                    staff_member.get('available', True),
                    staff_member.get('email')
                ))
        if staff_names:
            placeholders = ", ".join(["%s"] * len(staff_names))
            cursor.execute(
                f"UPDATE staff SET available = FALSE WHERE business_id = 1 AND LOWER(name) NOT IN ({placeholders})",
                tuple(staff_names)
            )
        else:
            cursor.execute("UPDATE staff SET available = FALSE WHERE business_id = 1")
        
        # Upsert business hours
        hours = config.get_hours()
        day_map = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6
        }
        
        for day_name, day_num in day_map.items():
            day_hours = hours.get(day_name, {})
            open_time = day_hours.get('open')
            close_time = day_hours.get('close')
            is_closed = open_time is None or close_time is None
            cursor.execute("""
                SELECT id FROM business_hours WHERE business_id = 1 AND day_of_week = %s
            """, (day_num,))
            existing = cursor.fetchone()
            if existing:
                cursor.execute("""
                    UPDATE business_hours
                    SET open_time = %s, close_time = %s, is_closed = %s
                    WHERE id = %s
                """, (open_time, close_time, is_closed, existing['id']))
            else:
                cursor.execute("""
                    INSERT INTO business_hours 
                    (business_id, day_of_week, open_time, close_time, is_closed)
                    VALUES (1, %s, %s, %s, %s)
                """, (day_num, open_time, close_time, is_closed))
        
        conn.commit()
        print("Business data synced successfully!")
        print(f"  Business: {business_name}")
        print(f"  Services: {len(services)}")
        print(f"  Staff: {len(staff)}")
        print(f"  Hours: {len(hours)} days configured")


if __name__ == "__main__":
    try:
        init_business_data()
    except Exception as e:
        print(f"Error initializing database: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
