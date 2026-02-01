import argparse
from datetime import date

from src.google_calendar_sync import sync_appointments


def parse_date(value: str | None):
    if not value:
        return None
    return date.fromisoformat(value)


def main():
    parser = argparse.ArgumentParser(description="Sync appointments to Google Calendar (one-way)")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--calendar-id", default="primary", help="Google Calendar ID (default: primary)")
    args = parser.parse_args()

    result = sync_appointments(
        calendar_id=args.calendar_id,
        start=parse_date(args.start),
        end=parse_date(args.end),
    )
    print(f"Synced {result['synced']} events for {result['range']}")


if __name__ == "__main__":
    main()
