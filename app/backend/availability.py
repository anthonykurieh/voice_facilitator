from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Any, Dict, List, Optional, Tuple

from app.backend.db import db_cursor, get_service_by_name_or_code, get_staff_name


def _to_time(x) -> time:
    """
    PyMySQL may return TIME columns as datetime.timedelta.
    Normalize to datetime.time.
    """
    if isinstance(x, time):
        return x
    if isinstance(x, timedelta):
        return (datetime.min + x).time()
    # fallback: try parsing
    if isinstance(x, str):
        # "10:00:00"
        hh, mm, ss = x.split(":")
        return time(int(hh), int(mm), int(ss))
    raise TypeError(f"Unsupported time type: {type(x)}")


@dataclass
class SlotCheckResult:
    ok: bool
    reason: str  # available | closed | no_staff | booked | invalid_service
    staff_id: Optional[int] = None
    staff_name: Optional[str] = None
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None
    alternatives: Optional[List[Dict[str, Any]]] = None  # list of {time, staff_id, staff_name}


@dataclass
class EarliestAvailabilityResult:
    ok: bool
    reason: str  # found | none | invalid_service
    staff_id: Optional[int] = None
    staff_name: Optional[str] = None
    start_hhmm: Optional[str] = None
    start_dt: Optional[datetime] = None


def _dow(dt: datetime) -> int:
    return dt.weekday()  # Monday=0..Sunday=6


def _get_business_hours(business_id: int, dt: datetime) -> Optional[Tuple[time, time, bool]]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT open_time, close_time, is_closed FROM business_hours WHERE business_id=%s AND dow=%s",
            (business_id, _dow(dt)),
        )
        row = cur.fetchone()
        if not row:
            return None
        return (_to_time(row["open_time"]), _to_time(row["close_time"]), bool(row["is_closed"]))


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


def _suggest_slots(
    business_id: int,
    service_id: int,
    duration_min: int,
    anchor: datetime,
    *,
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


def find_slot_or_suggest(
    business_id: int,
    service_id: int,
    requested_start: datetime,
    duration_min: int,
    *,
    search_days: int = 14,
    step_min: int = 15,
    max_suggestions: int = 5
) -> Tuple[bool, str, Optional[int], Optional[datetime], Optional[datetime], List[Tuple[datetime, datetime, int]]]:
    """
    Low-level slot finder.
    Returns: (ok, reason, staff_id, start_dt, end_dt, suggestions)
    """
    hours = _get_business_hours(business_id, requested_start)
    if not hours:
        return False, "closed", None, None, None, []

    open_t, close_t, is_closed = hours
    if is_closed:
        return False, "closed", None, None, None, []

    day_open = datetime.combine(requested_start.date(), open_t)
    day_close = datetime.combine(requested_start.date(), close_t)

    end = requested_start + timedelta(minutes=duration_min)
    if requested_start < day_open or end > day_close:
        suggestions = _suggest_slots(
            business_id, service_id, duration_min, requested_start,
            search_days=search_days, step_min=step_min, max_suggestions=max_suggestions
        )
        return False, "closed", None, None, None, suggestions

    staff_ids = _staff_candidates_for_service(business_id, service_id)
    if not staff_ids:
        return False, "no_staff", None, None, None, []

    for sid in staff_ids:
        if not _has_overlap(sid, requested_start, end):
            return True, "available", sid, requested_start, end, []

    suggestions = _suggest_slots(
        business_id, service_id, duration_min, requested_start,
        search_days=search_days, step_min=step_min, max_suggestions=max_suggestions
    )
    return False, "booked", None, None, None, suggestions


# ----------------------------
# Public functions used by simulate_voice_call
# ----------------------------

def check_slot_and_suggest(
    business_id: int,
    service_name: str,
    date_str: str,   # YYYY-MM-DD
    time_hhmm: str,  # HH:MM
    preferred_staff: Optional[str] = None,
    max_alternatives: int = 3,
) -> SlotCheckResult:
    svc = get_service_by_name_or_code(business_id, service_name)
    if not svc:
        return SlotCheckResult(ok=False, reason="invalid_service", alternatives=[])

    start_dt = datetime.strptime(f"{date_str} {time_hhmm}", "%Y-%m-%d %H:%M")
    duration_min = int(svc["duration_min"])
    ok, reason, staff_id, sdt, edt, suggestions = find_slot_or_suggest(
        business_id=business_id,
        service_id=int(svc["id"]),
        requested_start=start_dt,
        duration_min=duration_min,
        max_suggestions=max(5, max_alternatives),
    )

    if ok:
        staff_name = get_staff_name(int(staff_id)) if staff_id else None
        return SlotCheckResult(
            ok=True,
            reason="available",
            staff_id=int(staff_id),
            staff_name=staff_name,
            start_dt=sdt,
            end_dt=edt,
            alternatives=[],
        )

    # map suggestions -> alternatives
    alts: List[Dict[str, Any]] = []
    for (s, e, sid) in (suggestions or [])[:max_alternatives]:
        alts.append(
            {
                "time": s.strftime("%H:%M"),
                "staff_id": int(sid),
                "staff_name": get_staff_name(int(sid)) or f"Staff {sid}",
            }
        )

    return SlotCheckResult(
        ok=False,
        reason=reason,
        alternatives=alts,
    )


def find_earliest_availability(
    business_id: int,
    service_name: str,
    date_str: str,  # YYYY-MM-DD
    preferred_staff: Optional[str] = None,
) -> EarliestAvailabilityResult:
    svc = get_service_by_name_or_code(business_id, service_name)
    if not svc:
        return EarliestAvailabilityResult(ok=False, reason="invalid_service")

    # Start searching from opening time of that date
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    hours = _get_business_hours(business_id, datetime.combine(day, time(12, 0)))
    if not hours:
        return EarliestAvailabilityResult(ok=False, reason="none")
    open_t, close_t, is_closed = hours
    if is_closed:
        return EarliestAvailabilityResult(ok=False, reason="none")

    cursor = datetime.combine(day, open_t)
    duration_min = int(svc["duration_min"])

    ok, reason, staff_id, sdt, edt, _ = find_slot_or_suggest(
        business_id=business_id,
        service_id=int(svc["id"]),
        requested_start=cursor,
        duration_min=duration_min,
        search_days=0,
        step_min=15,
        max_suggestions=1,
    )

    if ok and staff_id and sdt:
        return EarliestAvailabilityResult(
            ok=True,
            reason="found",
            staff_id=int(staff_id),
            staff_name=get_staff_name(int(staff_id)) or f"Staff {staff_id}",
            start_hhmm=sdt.strftime("%H:%M"),
            start_dt=sdt,
        )

    # If the exact open time isn't free, walk the day in 15-min steps
    close_dt = datetime.combine(day, close_t)
    step = timedelta(minutes=15)
    while cursor + timedelta(minutes=duration_min) <= close_dt:
        ok, _, staff_id, sdt, _, _ = find_slot_or_suggest(
            business_id=business_id,
            service_id=int(svc["id"]),
            requested_start=cursor,
            duration_min=duration_min,
            search_days=0,
            step_min=15,
            max_suggestions=1,
        )
        if ok and staff_id and sdt:
            return EarliestAvailabilityResult(
                ok=True,
                reason="found",
                staff_id=int(staff_id),
                staff_name=get_staff_name(int(staff_id)) or f"Staff {staff_id}",
                start_hhmm=sdt.strftime("%H:%M"),
                start_dt=sdt,
            )
        cursor += step

    return EarliestAvailabilityResult(ok=False, reason="none")