from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple

from app.backend.db import db_cursor


DEFAULT_OPEN_TIME = time(9, 0)
DEFAULT_CLOSE_TIME = time(18, 0)
SLOT_GRANULARITY_MIN = 15  # suggest times in 15-min increments


@dataclass
class AvailabilityResult:
    ok: bool
    reason: str
    staff_id: Optional[int] = None
    staff_name: Optional[str] = None
    start_hhmm: Optional[str] = None
    end_hhmm: Optional[str] = None
    alternatives: Optional[List[Dict]] = None  # list of {time, staff_name}


def _get_service(cur, business_id: int, service_name: str) -> Dict:
    cur.execute(
        """
        SELECT id, name, default_duration_min, base_price, currency
        FROM services
        WHERE business_id=%s AND LOWER(name)=LOWER(%s) AND is_active=1
        """,
        (business_id, service_name),
    )
    row = cur.fetchone()
    if not row:
        # fallback: treat unknown service as generic 30 mins, price unknown
        return {"id": None, "name": service_name, "default_duration_min": 30, "base_price": None, "currency": None}
    return row


def _get_staff(cur, business_id: int, preferred_staff: Optional[str]) -> List[Dict]:
    if preferred_staff:
        cur.execute(
            """
            SELECT id, name, specialization
            FROM staff
            WHERE business_id=%s AND is_active=1 AND LOWER(name)=LOWER(%s)
            """,
            (business_id, preferred_staff),
        )
        rows = cur.fetchall()
        return rows

    cur.execute(
        """
        SELECT id, name, specialization
        FROM staff
        WHERE business_id=%s AND is_active=1
        ORDER BY id ASC
        """,
        (business_id,),
    )
    return cur.fetchall()


def _get_working_hours(cur, staff_id: int, day_of_week: int) -> Tuple[time, time]:
    """
    Uses staff_working_hours if present, else defaults to 09:00–18:00
    day_of_week: 0=Mon..6=Sun
    """
    cur.execute(
        """
        SELECT start_time, end_time
        FROM staff_working_hours
        WHERE staff_id=%s AND day_of_week=%s
        """,
        (staff_id, day_of_week),
    )
    row = cur.fetchone()
    if not row:
        return DEFAULT_OPEN_TIME, DEFAULT_CLOSE_TIME
    return row["start_time"], row["end_time"]


def _get_bookings(cur, staff_id: int, date_str: str) -> List[Tuple[datetime, datetime]]:
    cur.execute(
        """
        SELECT appointment_time, duration_min
        FROM appointments
        WHERE staff_id=%s AND appointment_date=%s
          AND status IN ('PENDING','CONFIRMED')
        """,
        (staff_id, date_str),
    )
    rows = cur.fetchall()
    occupied: List[Tuple[datetime, datetime]] = []
    for r in rows:
        start = datetime.strptime(r["appointment_time"], "%H:%M")
        dur = int(r["duration_min"] or 0)
        end = start + timedelta(minutes=dur if dur > 0 else 30)
        occupied.append((start, end))
    return occupied


def _conflicts(start: datetime, end: datetime, occupied: List[Tuple[datetime, datetime]]) -> bool:
    # overlap if start < occ_end AND end > occ_start
    return any(start < occ_end and end > occ_start for occ_start, occ_end in occupied)


def _iter_slots(open_t: time, close_t: time, duration_min: int) -> List[Tuple[datetime, datetime]]:
    cursor = datetime.combine(datetime.today().date(), open_t)
    close_dt = datetime.combine(datetime.today().date(), close_t)
    out = []
    step = timedelta(minutes=SLOT_GRANULARITY_MIN)
    dur = timedelta(minutes=duration_min)

    while cursor + dur <= close_dt:
        out.append((cursor, cursor + dur))
        cursor += step
    return out


def check_slot_and_suggest(
    business_id: int,
    service_name: str,
    date_str: str,
    time_hhmm: str,
    preferred_staff: Optional[str] = None,
    max_alternatives: int = 3,
) -> AvailabilityResult:
    """
    Check if requested slot is available.
    If not, suggest alternatives across staff and nearby times.
    """
    with db_cursor() as cur:
        service = _get_service(cur, business_id, service_name)
        duration = int(service.get("default_duration_min") or 30)

        staff_list = _get_staff(cur, business_id, preferred_staff)

        # If they requested a barber that doesn't exist, treat as no preference (but mention it)
        if preferred_staff and not staff_list:
            staff_list = _get_staff(cur, business_id, None)

        requested_start = datetime.strptime(time_hhmm, "%H:%M")
        requested_end = requested_start + timedelta(minutes=duration)

        day_of_week = datetime.strptime(date_str, "%Y-%m-%d").date().weekday()  # 0=Mon..6=Sun

        # Try exact requested time across staff_list
        for s in staff_list:
            open_t, close_t = _get_working_hours(cur, s["id"], day_of_week)
            open_dt = datetime.combine(requested_start.date(), open_t)
            close_dt = datetime.combine(requested_start.date(), close_t)

            # normalize requested time onto "today date" base used above
            req_start = datetime.combine(datetime.today().date(), requested_start.time())
            req_end = datetime.combine(datetime.today().date(), requested_end.time())

            if req_start < open_dt or req_end > close_dt:
                continue

            occupied = _get_bookings(cur, s["id"], date_str)
            if not _conflicts(req_start, req_end, occupied):
                return AvailabilityResult(
                    ok=True,
                    reason="available",
                    staff_id=s["id"],
                    staff_name=s["name"],
                    start_hhmm=time_hhmm,
                    end_hhmm=(requested_start + timedelta(minutes=duration)).strftime("%H:%M"),
                    alternatives=[],
                )

        # Not available — suggest alternatives
        alternatives: List[Dict] = []
        for s in staff_list:
            open_t, close_t = _get_working_hours(cur, s["id"], day_of_week)
            occupied = _get_bookings(cur, s["id"], date_str)
            for start_dt, end_dt in _iter_slots(open_t, close_t, duration):
                if not _conflicts(start_dt, end_dt, occupied):
                    alternatives.append({"time": start_dt.strftime("%H:%M"), "staff_name": s["name"], "staff_id": s["id"]})
                    if len(alternatives) >= max_alternatives:
                        break
            if len(alternatives) >= max_alternatives:
                break

        return AvailabilityResult(
            ok=False,
            reason="taken",
            alternatives=alternatives,
        )


def find_earliest_availability(
    business_id: int,
    service_name: str,
    date_str: str,
    preferred_staff: Optional[str] = None,
) -> AvailabilityResult:
    """
    Returns the earliest available slot on a given date (calendar-like).
    """
    with db_cursor() as cur:
        service = _get_service(cur, business_id, service_name)
        duration = int(service.get("default_duration_min") or 30)

        staff_list = _get_staff(cur, business_id, preferred_staff)
        if preferred_staff and not staff_list:
            # no such staff -> fallback to anyone
            staff_list = _get_staff(cur, business_id, None)

        day_of_week = datetime.strptime(date_str, "%Y-%m-%d").date().weekday()

        best = None  # (time, staff)
        for s in staff_list:
            open_t, close_t = _get_working_hours(cur, s["id"], day_of_week)
            occupied = _get_bookings(cur, s["id"], date_str)

            for start_dt, end_dt in _iter_slots(open_t, close_t, duration):
                if not _conflicts(start_dt, end_dt, occupied):
                    candidate = (start_dt.strftime("%H:%M"), s)
                    if best is None or candidate[0] < best[0]:
                        best = candidate
                    break  # earliest for this staff

        if not best:
            return AvailabilityResult(ok=False, reason="no_availability", alternatives=[])

        t, s = best
        end_t = (datetime.strptime(t, "%H:%M") + timedelta(minutes=duration)).strftime("%H:%M")
        return AvailabilityResult(
            ok=True,
            reason="earliest",
            staff_id=s["id"],
            staff_name=s["name"],
            start_hhmm=t,
            end_hhmm=end_t,
            alternatives=[],
        )