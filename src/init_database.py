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
    
    # Insert business data
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Check if business already exists
        cursor.execute("SELECT id FROM businesses WHERE id = 1")
        if cursor.fetchone():
            print("Business data already exists. Skipping initialization.")
            return
        
        # Insert business
        business_name = config.get_business_name()
        business_type = config.get_business_type()
        business_phone = config.get('business.phone')
        
        cursor.execute("""
            INSERT INTO businesses (id, name, type, phone)
            VALUES (1, %s, %s, %s)
        """, (business_name, business_type, business_phone))
        
        # Insert services
        services = config.get_services()
        for service in services:
            cursor.execute("""
                INSERT INTO services (business_id, name, duration_minutes, price, active)
                VALUES (1, %s, %s, %s, TRUE)
            """, (
                service['name'],
                service.get('duration_minutes', 30),
                service.get('price', 0)
            ))
        
        # Insert staff
        staff = config.get_staff()
        for staff_member in staff:
            cursor.execute("""
                INSERT INTO staff (business_id, name, available)
                VALUES (1, %s, %s)
            """, (
                staff_member['name'],
                staff_member.get('available', True)
            ))
        
        # Insert business hours
        hours = config.get_hours()
        day_map = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6
        }
        
        for day_name, day_hours in hours.items():
            day_num = day_map.get(day_name.lower())
            if day_num is not None:
                open_time = day_hours.get('open')
                close_time = day_hours.get('close')
                is_closed = open_time is None or close_time is None
                
                cursor.execute("""
                    INSERT INTO business_hours 
                    (business_id, day_of_week, open_time, close_time, is_closed)
                    VALUES (1, %s, %s, %s, %s)
                """, (day_num, open_time, close_time, is_closed))
        
        conn.commit()
        print("Business data initialized successfully!")
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

