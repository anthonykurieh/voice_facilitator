from app.backend.db import init_db
from app.backend.seed_data import seed_all

def main():
    init_db()
    business_id = seed_all()
    print(f"✅ DB initialized + seeded. Demo business_id={business_id}")

if __name__ == "__main__":
    main()