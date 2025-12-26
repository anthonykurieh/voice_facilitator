from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict

from app.backend.db import (
    db_cursor,
    get_service_by_name_or_code,
    create_appointment,
    reschedule_appointment,
)

# =========================
# Data structures
# =========================

@dataclass
class AppointmentChoice:
    appointment_id: int
    service_name: str
    staff_name: str
    start_time: datetime
    end_time: datetime
    status: str


# =========================
# Listing & lookup
# =========================

def list_upcoming_appointments(
    business_id: int,
    customer_id: int,
    limit: int = 5,
) -> List[AppointmentChoice]:
    """
    List upcoming appointments for a customer.
    """
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
              a.id AS appointment_id,
              s.name AS service_name,
              st.name AS staff_name,
              a.start_time,
              a.end_time,
              a.status
            FROM appointments a
            JOIN services s ON s.id = a.service_id
            JOIN staff st ON st.id = a.staff_id
            WHERE a.business_id=%s
              AND a.customer_id=%s
              AND a.start_time >= NOW()
              AND a.status IN ('confirmed','completed')
            ORDER BY a.start_time ASC
            LIMIT %s
            """,
            (business_id, customer_id, limit),
        )
        rows = cur.fetchall()

    return [
        AppointmentChoice(
            appointment_id=r["appointment_id"],
            service_name=r["service_name"],
            staff_name=r["staff_name"],
            start_time=r["start_time"],
            end_time=r["end_time"],
            status=r["status"],
        )
        for r in rows
    ]


def get_appointment_detail(appointment_id: int) -> Optional[Dict]:
    """
    Fetch full appointment details by id.
    """
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
              a.*,
              s.name AS service_name,
              st.name AS staff_name
            FROM appointments a
            JOIN services s ON s.id = a.service_id
            JOIN staff st ON st.id = a.staff_id
            WHERE a.id=%s
            """,
            (appointment_id,),
        )
        return cur.fetchone()


# =========================
# Formatting helpers (voice UX)
# =========================

def format_appointment_choices(choices: List[AppointmentChoice]) -> str:
    """
    Convert appointment list into a spoken-friendly list.
    """
    if not choices:
        return "You don’t have any upcoming appointments."

    lines = []
    for a in choices:
        dt = a.start_time.strftime("%A %B %d at %H:%M")
        lines.append(
            f"Appointment {a.appointment_id}: {a.service_name} with {a.staff_name} on {dt}"
        )

    return "Here are your upcoming appointments: " + "; ".join(lines)


def pick_appointment_for_action(
    business_id: int,
    customer_id: int,
) -> List[AppointmentChoice]:
    """
    Returns candidate appointments user can modify or cancel.
    """
    return list_upcoming_appointments(
        business_id=business_id,
        customer_id=customer_id,
        limit=5,
    )


# =========================
# Parsing helpers
# =========================

def parse_appointment_id_from_text(text: str) -> Optional[int]:
    """
    Extract an appointment id from free text.

    Examples:
      - "appointment 12"
      - "id 12"
      - "#12"
      - "12"
    """
    if not text:
        return None

    t = text.strip().lower()

    if t.isdigit():
        return int(t)

    import re

    patterns = [
        r"\bid\s*[:=]?\s*(\d+)\b",
        r"\b(?:appointment|appt)\s*[:#]?\s*(\d+)\b",
        r"#\s*(\d+)\b",
    ]

    for p in patterns:
        m = re.search(p, t)
        if m:
            return int(m.group(1))

    # last-resort: single standalone number (avoid times)
    nums = re.findall(r"\b\d+\b", t)
    if len(nums) == 1:
        return int(nums[0])

    return None


# =========================
# Write operations (CRUD)
# =========================

def cancel_appointment(appointment_id: int, note: Optional[str] = None) -> None:
    """
    Cancel an appointment.
    """
    with db_cursor() as cur:
        cur.execute(
            """
            UPDATE appointments
            SET status='cancelled',
                notes=COALESCE(%s, notes)
            WHERE id=%s
            """,
            (note, appointment_id),
        )


def update_appointment_time_and_staff(
    appointment_id: int,
    new_staff_id: int,
    new_start_time: datetime,
    new_end_time: datetime,
    note: Optional[str] = None,
) -> None:
    """
    Backwards-compatible wrapper used by simulate_voice_call.py.
    """
    reschedule_appointment(
        appointment_id=appointment_id,
        new_staff_id=new_staff_id,
        new_start_time=new_start_time,
        new_end_time=new_end_time,
        note=note,
    )