from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import List, Optional, Tuple, Dict, Any

from app.backend.db import db_cursor, get_service_by_name_or_code


@dataclass
class AvailabilityResult:
    ok: bool
    reason: str  # "available" | "closed" | "no_staff" | "booked" | "unknown_service"
    staff_id: Optional[int] = None
    staff_name: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    suggestions: Optional[List[Tuple[datetime, datetime, int]]] = None  # (start, end, staff_id)


def _dow(dt: datetime) -> int:
    return dt.weekday()  # Mon=0..Sun=6


def _as_time(x) -> time:
    """
    ✅ PyMySQL often returns MySQL TIME as datetime.timedelta.
    Convert to datetime.time so datetime.combine works correctly.
    """
    if isinstance(x, time):
        return x
    if isinstance(x, timedelta):
        base = datetime(2000, 1, 1) + x
        return base.time()
    # fallback: attempt parse string "HH:MM:SS"
    s = str(x)
    hh, mm, ss = (s.split(":") + ["0", "0"])[:3]
    return time(int(hh), int(mm), int(float(ss)))


def _get_business_hours(business_id: int, dt: datetime) -> Optional[Tuple[time, time, bool]]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT open_time, close_time, is_closed FROM business_hours WHERE business_id=%s AND dow=%s",
            (business_id, _dow(dt)),
        )
        row = cur.fetchone()
        if not row:
            return None
        return (_as_time(row["open_time"]), _as_time(row["close_time"]), bool(row["is_closed"]))


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


def _staff_name(staff_id: int) -> Optional[str]:
    with db_cursor() as cur:
        cur.execute("SELECT name FROM staff WHERE id=%s", (staff_id,))
        row = cur.fetchone()
        return row["name"] if row else None


def _has_overlap(business_id: int, staff_id: int, start: datetime, end: datetime) -> bool:
    """
    Overlap check scoped to business_id.
    """
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM appointments
            WHERE business_id=%s
              AND staff_id=%s
              AND status IN ('confirmed','completed')
              AND NOT (end_time <= %s OR start_time >= %s)
            LIMIT 1
            """,
            (business_id, staff_id, start, end),
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
        suggestions = _suggest_slots(business_id, service_id, duration_min, requested_start, search_days, step_min, max_suggestions)
        return AvailabilityResult(ok=False, reason="closed", suggestions=suggestions)

    staff_ids = _staff_candidates_for_service(business_id, service_id)
    if not staff_ids:
        return AvailabilityResult(ok=False, reason="no_staff", suggestions=[])

    for sid in staff_ids:
        if not _has_overlap(business_id, sid, requested_start, end):
            return AvailabilityResult(
                ok=True,
                reason="available",
                staff_id=sid,
                staff_name=_staff_name(sid),
                start_time=requested_start,
                end_time=end,
                suggestions=[],
            )

    suggestions = _suggest_slots(business_id, service_id, duration_min, requested_start, search_days, step_min, max_suggestions)
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
                if not _has_overlap(business_id, sid, start, end):
                    suggestions.append((start, end, sid))
                    if len(suggestions) >= max_suggestions:
                        return suggestions

            cursor += step

    return suggestions


# ---- Convenience wrappers used by simulate_voice_call ----

def _hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def check_slot_and_suggest(
    business_id: int,
    service_name: str,
    date_str: str,
    time_hhmm: str,
    preferred_staff: Optional[str] = None,
    max_alternatives: int = 3,
) -> Dict[str, Any]:
    service = get_service_by_name_or_code(business_id, service_name)
    if not service:
        return {"ok": False, "reason": "unknown_service", "alternatives": []}

    start = datetime.strptime(f"{date_str} {time_hhmm}", "%Y-%m-%d %H:%M")
    res = find_slot_or_suggest(
        business_id=business_id,
        service_id=int(service["id"]),
        requested_start=start,
        duration_min=int(service["duration_min"]),
        max_suggestions=max_alternatives,
    )

    if res.ok:
        return {"ok": True, "staff_id": res.staff_id, "staff_name": res.staff_name}

    alts = []
    for (s, e, sid) in (res.suggestions or []):
        alts.append({"time": _hhmm(s), "staff_id": sid, "staff_name": _staff_name(sid)})

    return {"ok": False, "reason": res.reason, "alternatives": alts}


def find_earliest_availability(
    business_id: int,
    service_name: str,
    date_str: str,
    preferred_staff: Optional[str] = None,
) -> Dict[str, Any]:
    service = get_service_by_name_or_code(business_id, service_name)
    if not service:
        return {"ok": False, "reason": "unknown_service"}

    # Start searching from opening time that day
    day = datetime.strptime(date_str, "%Y-%m-%d")
    hours = _get_business_hours(business_id, day)
    if not hours or hours[2]:
        return {"ok": False, "reason": "closed"}

    open_t, _, _ = hours
    anchor = datetime.combine(day.date(), open_t)

    suggestions = _suggest_slots(
        business_id=business_id,
        service_id=int(service["id"]),
        duration_min=int(service["duration_min"]),
        anchor=anchor,
        search_days=0,
        step_min=15,
        max_suggestions=1,
    )
    if not suggestions:
        return {"ok": False, "reason": "booked"}

    s, e, sid = suggestions[0]
    return {"ok": True, "start_hhmm": _hhmm(s), "staff_id": sid, "staff_name": _staff_name(sid)}