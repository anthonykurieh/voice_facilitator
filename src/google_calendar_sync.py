import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_engine() -> Engine:
    host = os.getenv("DB_HOST") or os.getenv("MYSQL_HOST", "localhost")
    port = os.getenv("DB_PORT") or os.getenv("MYSQL_PORT", "3306")
    user = os.getenv("DB_USER") or os.getenv("MYSQL_USER", "root")
    password = os.getenv("DB_PASSWORD") or os.getenv("MYSQL_PASSWORD", "")
    db = os.getenv("DB_NAME") or os.getenv("MYSQL_DATABASE", "voice_assistant")
    return create_engine(f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}")


def fetch_df(engine: Engine, query: str, params=None):
    if isinstance(params, list):
        params = tuple(params)
    return pd.read_sql(text(query), con=engine, params=params)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _token_path() -> Path:
    return _repo_root() / ".gcal_token.json"


def _event_map_path() -> Path:
    return _repo_root() / ".gcal_event_map.json"


def _client_secret_path() -> Path:
    return Path(os.getenv("GOOGLE_CLIENT_SECRET", _repo_root() / "client_secret.json"))


def get_calendar_service():
    token_path = _token_path()
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_secret = _client_secret_path()
            if not client_secret.exists():
                raise FileNotFoundError(
                    "Missing Google OAuth client secret file. Set GOOGLE_CLIENT_SECRET or add client_secret.json"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def load_event_map() -> dict:
    path = _event_map_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def save_event_map(event_map: dict) -> None:
    path = _event_map_path()
    path.write_text(json.dumps(event_map, indent=2, sort_keys=True))


def load_appointments(engine: Engine, start: date, end: date) -> pd.DataFrame:
    return fetch_df(
        engine,
        """
        SELECT a.id, a.appointment_date, a.appointment_time, a.duration_minutes, a.status,
               s.name AS service_name, s.duration_minutes AS service_duration,
               st.name AS staff_name
        FROM appointments a
        LEFT JOIN services s ON a.service_id = s.id
        LEFT JOIN staff st ON a.staff_id = st.id
        WHERE a.appointment_date BETWEEN %s AND %s
        """,
        [start, end],
    )


def _build_event(row, tzinfo):
    if pd.isna(row["appointment_date"]):
        return None
    appt_date = pd.to_datetime(row["appointment_date"]).date()
    appt_time = row.get("appointment_time")
    if pd.isna(appt_time):
        return None
    appt_time = pd.to_datetime(appt_time, errors="coerce")
    if pd.isna(appt_time):
        return None
    start = datetime.combine(appt_date, appt_time.time()).replace(tzinfo=tzinfo)
    duration = pd.to_numeric(row.get("duration_minutes"), errors="coerce")
    if pd.isna(duration):
        duration = pd.to_numeric(row.get("service_duration"), errors="coerce")
    if pd.isna(duration):
        duration = 30
    end = start + timedelta(minutes=float(duration))
    service_name = row.get("service_name") or "Service"
    staff_name = row.get("staff_name") or "Unassigned"
    return {
        "summary": f"{service_name} ({staff_name})",
        "description": f"Appointment ID: {row.get('id')}",
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }


def sync_appointments(calendar_id: str = "primary", start: date | None = None, end: date | None = None):
    engine = get_engine()
    start = start or (date.today() - timedelta(days=7))
    end = end or (date.today() + timedelta(days=60))
    appts = load_appointments(engine, start, end)

    service = get_calendar_service()
    event_map = load_event_map()
    tzinfo = datetime.now().astimezone().tzinfo

    for _, row in appts.iterrows():
        appt_id = str(row.get("id"))
        status = (row.get("status") or "").lower()
        event_id = event_map.get(appt_id)

        if status in {"cancelled", "no_show"}:
            if event_id:
                try:
                    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
                except Exception:
                    pass
                event_map.pop(appt_id, None)
            continue

        if status != "scheduled":
            continue

        event_body = _build_event(row, tzinfo)
        if not event_body:
            continue

        if event_id:
            try:
                service.events().update(calendarId=calendar_id, eventId=event_id, body=event_body).execute()
                continue
            except Exception:
                event_map.pop(appt_id, None)

        created = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        event_map[appt_id] = created.get("id")

    save_event_map(event_map)
    return {
        "synced": len(event_map),
        "range": f"{start} to {end}",
    }
