from dataclasses import dataclass
from datetime import datetime, timedelta, time, date
from typing import Any, Dict, List, Optional, Tuple, Union

from app.backend.db import db_cursor, get_service_by_name_or_code


@dataclass
class AvailabilityResult:
    ok: bool
    reason: str  # "available" | "closed" | "no_staff" | "booked"
    staff_id: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    suggestions: Optional[List[Tuple[datetime, datetime, int]]] = None  # (start, end, staff_id)


@dataclass
class EarliestAvailability:
    ok: bool
    staff_id: Optional[int] = None
    staff_name: Optional[str] = None
    start_hhmm: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


@dataclass
class SlotCheckResult:
    ok: bool
    staff_id: Optional[int] = None
    staff_name: Optional[str] = None
    alternatives: Optional[List[Dict[str, Any]]] = None  # [{"time": "17:00", "staff_id": 1, "staff_name": "Karim"}]
    reason: str = "booked"


def _dow(dt: datetime) -> int:
    return dt.weekday()  # Mon=0..Sun=6


def _time_from_mysql(v: Union[time, timedelta]) -> time:
    """
    PyMySQL often returns TIME columns as datetime.timedelta.
    Convert safely to datetime.time.
    """
    if isinstance(v, time):
        return v
    if isinstance(v, timedelta):
        secs = int(v.total_seconds())
        secs = max(0, secs)
        hours = (secs // 3600) % 24
        minutes = (secs % 3600) // 60
        seconds = secs % 60
        return time(hours, minutes, seconds)
    raise TypeError(f"Unsupported TIME value type: {type(v)}")


def _get_business_hours(business_id: int, dt: datetime) -> Optional[Tuple[time, time, bool]]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT open_time, close_time, is_closed FROM business_hours WHERE business_id=%s AND dow=%s",
            (business_id, _dow(dt)),
        )
        row = cur.fetchone()
        if not row:
            return None
        open_t = _time_from_mysql(row["open_time"])
        close_t = _time_from_mysql(row["close_time"])
        return (open_t, close_t, bool(row["is_closed"]))


def _staff_candidates_for_service(business_id: int, service_id: int) -> List[int]:
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT s.id
            FROM staff s
            JOIN staff_services ss ON ss.staff_id=s.id AND ss.business_id=s.business_id
            WHERE s.business_id=%s AND s.active=1 AND ss.service_id=%s
            """,
            (business_id, service_id),
        )
        return [int(r["id"]) for r in cur.fetchall()]


def _staff_name(staff_id: int) -> str:
    with db_cursor() as cur:
        cur.execute("SELECT name FROM staff WHERE id=%s LIMIT 1", (staff_id,))
        row = cur.fetchone()
        return row["name"] if row and row.get("name") else "Staff"


def _has_overlap(staff_id: int, start: datetime, end: datetime) -> bool:
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM appointments
            WHERE staff_id=%s
              AND status IN ('confirmed','completed')
              AND NOT (end_time <= %s OR start_time >= %s)
            LIMIT 1
            """,
            (staff_id, start, end),
        )
        return cur.fetchone() is not None


def find_slot_or_suggest(
    business_id: int,
    service_id: int,
    requested_start: datetime,
    duration_min: int,
    *,
    search_days: int = 14,
    step_min: int = 15,
    max_suggestions: int = 5
) -> AvailabilityResult:
    hours = _get_business_hours(business_id, requested_start)
    if not hours:
        return AvailabilityResult(ok=False, reason="closed", suggestions=[])

    open_t, close_t, is_closed = hours
    if is_closed:
        return AvailabilityResult(ok=False, reason="closed", suggestions=[])

    day_open = datetime.combine(requested_start.date(), open_t)
    day_close = datetime.combine(requested_start.date(), close_t)

    end = requested_start + timedelta(minutes=duration_min)
    if requested_start < day_open or end > day_close:
        suggestions = _suggest_slots(
            business_id, service_id, duration_min, requested_start, search_days, step_min, max_suggestions
        )
        return AvailabilityResult(ok=False, reason="closed", suggestions=suggestions)

    staff_ids = _staff_candidates_for_service(business_id, service_id)
    if not staff_ids:
        return AvailabilityResult(ok=False, reason="no_staff", suggestions=[])

    for sid in staff_ids:
        if not _has_overlap(sid, requested_start, end):
            return AvailabilityResult(ok=True, reason="available", staff_id=sid, start_time=requested_start, end_time=end, suggestions=[])

    suggestions = _suggest_slots(
        business_id, service_id, duration_min, requested_start, search_days, step_min, max_suggestions
    )
    return AvailabilityResult(ok=False, reason="booked", suggestions=suggestions)


def _suggest_slots(
    business_id: int,
    service_id: int,
    duration_min: int,
    anchor: datetime,
    search_days: int,
    step_min: int,
    max_suggestions: int,
) -> List[Tuple[datetime, datetime, int]]:
    staff_ids = _staff_candidates_for_service(business_id, service_id)
    if not staff_ids:
        return []

    suggestions: List[Tuple[datetime, datetime, int]] = []
    step = timedelta(minutes=step_min)

    for d in range(0, search_days + 1):
        day = anchor.date() + timedelta(days=d)
        hours = _get_business_hours(business_id, datetime.combine(day, time(12, 0)))
        if not hours:
            continue
        open_t, close_t, is_closed = hours
        if is_closed:
            continue

        cursor = datetime.combine(day, open_t)
        day_close = datetime.combine(day, close_t)

        while cursor + timedelta(minutes=duration_min) <= day_close:
            start = cursor
            end = start + timedelta(minutes=duration_min)

            if d == 0 and end <= anchor:
                cursor += step
                continue

            for sid in staff_ids:
                if not _has_overlap(sid, start, end):
                    suggestions.append((start, end, sid))
                    if len(suggestions) >= max_suggestions:
                        return suggestions

            cursor += step

    return suggestions


# ----------------------------
# High-level helper functions
# ----------------------------

def _parse_date(date_str: str) -> date:
    # expects YYYY-MM-DD
    y, m, d = [int(x) for x in date_str.split("-")]
    return date(y, m, d)


def _fmt_hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def find_earliest_availability(
    business_id: int,
    service_name: str,
    date_str: str,
    preferred_staff: Optional[str] = None,
    *,
    step_min: int = 15,
    max_search_days: int = 0,
) -> EarliestAvailability:
    """
    Finds earliest availability ON THE GIVEN DATE (max_search_days=0 means that date only).
    """
    svc = get_service_by_name_or_code(business_id, service_name)
    if not svc:
        return EarliestAvailability(ok=False)

    service_id = int(svc["id"])
    duration_min = int(svc["duration_min"])

    target_date = _parse_date(date_str)
    anchor = datetime.combine(target_date, time(0, 0))

    hours = _get_business_hours(business_id, datetime.combine(target_date, time(12, 0)))
    if not hours:
        return EarliestAvailability(ok=False)
    open_t, close_t, is_closed = hours
    if is_closed:
        return EarliestAvailability(ok=False)

    day_open = datetime.combine(target_date, open_t)
    day_close = datetime.combine(target_date, close_t)

    staff_ids = _staff_candidates_for_service(business_id, service_id)
    if not staff_ids:
        return EarliestAvailability(ok=False)

    # If preferred_staff provided, try to filter to matching staff first
    if preferred_staff:
        p = preferred_staff.strip().lower()
        with db_cursor() as cur:
            cur.execute(
                "SELECT id FROM staff WHERE business_id=%s AND active=1 AND LOWER(name) LIKE %s",
                (business_id, f"%{p}%"),
            )
            rows = cur.fetchall()
            preferred_ids = [int(r["id"]) for r in rows]
        staff_ids = preferred_ids + [sid for sid in staff_ids if sid not in preferred_ids] if preferred_ids else staff_ids

    cursor = day_open
    step = timedelta(minutes=step_min)

    while cursor + timedelta(minutes=duration_min) <= day_close:
        start = cursor
        end = start + timedelta(minutes=duration_min)

        for sid in staff_ids:
            if not _has_overlap(sid, start, end):
                return EarliestAvailability(
                    ok=True,
                    staff_id=sid,
                    staff_name=_staff_name(sid),
                    start_hhmm=_fmt_hhmm(start),
                    start_time=start,
                    end_time=end,
                )

        cursor += step

    return EarliestAvailability(ok=False)


def check_slot_and_suggest(
    business_id: int,
    service_name: str,
    date_str: str,
    time_hhmm: str,
    preferred_staff: Optional[str] = None,
    *,
    max_alternatives: int = 3,
    step_min: int = 15,
) -> SlotCheckResult:
    svc = get_service_by_name_or_code(business_id, service_name)
    if not svc:
        return SlotCheckResult(ok=False, reason="no_service", alternatives=[])

    service_id = int(svc["id"])
    duration_min = int(svc["duration_min"])

    target_date = _parse_date(date_str)
    hh, mm = [int(x) for x in time_hhmm.split(":")]
    requested_start = datetime.combine(target_date, time(hh, mm))

    res = find_slot_or_suggest(
        business_id=business_id,
        service_id=service_id,
        requested_start=requested_start,
        duration_min=duration_min,
        search_days=14,
        step_min=step_min,
        max_suggestions=max(5, max_alternatives),
    )

    if res.ok:
        sid = int(res.staff_id)
        return SlotCheckResult(ok=True, staff_id=sid, staff_name=_staff_name(sid), alternatives=[], reason="available")

    # map suggestions to readable times + staff names (limit max_alternatives)
    alts: List[Dict[str, Any]] = []
    for (s, e, sid) in (res.suggestions or []):
        alts.append({"time": _fmt_hhmm(s), "staff_id": sid, "staff_name": _staff_name(sid)})
        if len(alts) >= max_alternatives:
            break

    return SlotCheckResult(ok=False, alternatives=alts, reason=res.reason)