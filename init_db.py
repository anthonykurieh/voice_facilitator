from app.backend.db import init_db

def main():
    init_db()
    print("Tables created (or already existed).")

if __name__ == "__main__":
    main()