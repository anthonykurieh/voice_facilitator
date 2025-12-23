from dataclasses import dataclass
from datetime import datetime, timedelta, time as dtime
from typing import Any, Dict, List, Optional, Tuple, Union

from app.backend.db import db_cursor, get_service_by_name_or_code


# ----------------------------
# Types / results
# ----------------------------
@dataclass
class AvailabilityResult:
    ok: bool
    reason: str  # "available" | "closed" | "no_staff" | "booked" | "unknown_service"
    staff_id: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    suggestions: Optional[List[Tuple[datetime, datetime, int]]] = None  # (start, end, staff_id)


@dataclass
class EarliestAvailabilityResult:
    ok: bool
    reason: str  # "available" | "closed" | "no_staff" | "unknown_service" | "no_slots"
    staff_id: Optional[int] = None
    staff_name: Optional[str] = None
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None
    start_hhmm: Optional[str] = None


@dataclass
class CheckSlotResult:
    ok: bool
    reason: str  # "available" | "closed" | "no_staff" | "unknown_service" | "booked"
    staff_id: Optional[int] = None
    staff_name: Optional[str] = None
    alternatives: Optional[List[Dict[str, Any]]] = None  # [{"time": "16:15", "staff_id": 1, "staff_name": "Omar"}]


# ----------------------------
# Helpers
# ----------------------------
def _dow(dt: datetime) -> int:
    # Python: Monday=0 ... Sunday=6
    return dt.weekday()


def _to_time(val: Union[dtime, timedelta, str, None]) -> Optional[dtime]:
    """
    MySQL TIME can be returned as:
    - datetime.time
    - datetime.timedelta (common with PyMySQL depending on settings)
    - string like "10:00:00"
    Normalize to datetime.time.
    """
    if val is None:
        return None

    if isinstance(val, dtime):
        return val

    if isinstance(val, timedelta):
        total_seconds = int(val.total_seconds())
        if total_seconds < 0:
            total_seconds = 0
        total_seconds = total_seconds % (24 * 3600)
        hh = total_seconds // 3600
        mm = (total_seconds % 3600) // 60
        ss = total_seconds % 60
        return dtime(hour=hh, minute=mm, second=ss)

    if isinstance(val, str):
        s = val.strip()
        # accept HH:MM or HH:MM:SS
        parts = s.split(":")
        if len(parts) >= 2:
            hh = int(parts[0])
            mm = int(parts[1])
            ss = int(parts[2]) if len(parts) >= 3 else 0
            return dtime(hour=hh, minute=mm, second=ss)

    return None


def _get_business_hours(business_id: int, dt: datetime) -> Optional[Tuple[dtime, dtime, bool]]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT open_time, close_time, is_closed FROM business_hours WHERE business_id=%s AND dow=%s",
            (business_id, _dow(dt)),
        )
        row = cur.fetchone()
        if not row:
            return None

        open_t = _to_time(row["open_time"])
        close_t = _to_time(row["close_time"])
        is_closed = bool(row["is_closed"])

        # If times are missing or not parseable, treat as closed
        if open_t is None or close_t is None:
            return None

        return (open_t, close_t, is_closed)


def _staff_candidates_for_service(business_id: int, service_id: int) -> List[int]:
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT s.id
            FROM staff s
            JOIN staff_services ss
              ON ss.staff_id=s.id AND ss.business_id=s.business_id
            WHERE s.business_id=%s AND s.active=1 AND ss.service_id=%s
            ORDER BY s.id
            """,
            (business_id, service_id),
        )
        return [int(r["id"]) for r in cur.fetchall()]


def _staff_name(staff_id: int) -> str:
    with db_cursor() as cur:
        cur.execute("SELECT name FROM staff WHERE id=%s LIMIT 1", (staff_id,))
        row = cur.fetchone()
        return str(row["name"]) if row else f"Staff #{staff_id}"


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


# ----------------------------
# Core: slot finding
# ----------------------------
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

    # clamp to business hours
    day_open = datetime.combine(requested_start.date(), open_t)
    day_close = datetime.combine(requested_start.date(), close_t)

    end = requested_start + timedelta(minutes=duration_min)
    if requested_start < day_open or end > day_close:
        suggestions = _suggest_slots(
            business_id, service_id, duration_min, requested_start,
            search_days, step_min, max_suggestions
        )
        return AvailabilityResult(ok=False, reason="closed", suggestions=suggestions)

    staff_ids = _staff_candidates_for_service(business_id, service_id)
    if not staff_ids:
        return AvailabilityResult(ok=False, reason="no_staff", suggestions=[])

    for sid in staff_ids:
        if not _has_overlap(sid, requested_start, end):
            return AvailabilityResult(
                ok=True,
                reason="available",
                staff_id=sid,
                start_time=requested_start,
                end_time=end,
                suggestions=[]
            )

    suggestions = _suggest_slots(
        business_id, service_id, duration_min, requested_start,
        search_days, step_min, max_suggestions
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

        hours = _get_business_hours(business_id, datetime.combine(day, dtime(12, 0)))
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

            # skip past times if searching same day
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
# Public wrappers used by your simulator
# ----------------------------
def find_earliest_availability(
    business_id: int,
    service_name: str,
    date_str: str,  # YYYY-MM-DD
    preferred_staff: Optional[str] = None,  # reserved for future: name matching
    *,
    step_min: int = 15,
) -> EarliestAvailabilityResult:
    svc = get_service_by_name_or_code(business_id, service_name)
    if not svc:
        return EarliestAvailabilityResult(ok=False, reason="unknown_service")

    service_id = int(svc["id"])
    duration_min = int(svc["duration_min"])

    day = datetime.fromisoformat(f"{date_str}T00:00:00")
    hours = _get_business_hours(business_id, day)
    if not hours:
        return EarliestAvailabilityResult(ok=False, reason="closed")
    open_t, close_t, is_closed = hours
    if is_closed:
        return EarliestAvailabilityResult(ok=False, reason="closed")

    staff_ids = _staff_candidates_for_service(business_id, service_id)
    if not staff_ids:
        return EarliestAvailabilityResult(ok=False, reason="no_staff")

    start_cursor = datetime.combine(day.date(), open_t)
    end_limit = datetime.combine(day.date(), close_t)
    step = timedelta(minutes=step_min)

    cursor = start_cursor
    while cursor + timedelta(minutes=duration_min) <= end_limit:
        end = cursor + timedelta(minutes=duration_min)
        for sid in staff_ids:
            if not _has_overlap(sid, cursor, end):
                hhmm = cursor.strftime("%H:%M")
                return EarliestAvailabilityResult(
                    ok=True,
                    reason="available",
                    staff_id=sid,
                    staff_name=_staff_name(sid),
                    start_dt=cursor,
                    end_dt=end,
                    start_hhmm=hhmm,
                )
        cursor += step

    return EarliestAvailabilityResult(ok=False, reason="no_slots")


def check_slot_and_suggest(
    business_id: int,
    service_name: str,
    date_str: str,
    time_hhmm: str,
    preferred_staff: Optional[str] = None,  # reserved for future
    *,
    max_alternatives: int = 3,
) -> CheckSlotResult:
    svc = get_service_by_name_or_code(business_id, service_name)
    if not svc:
        return CheckSlotResult(ok=False, reason="unknown_service")

    service_id = int(svc["id"])
    duration_min = int(svc["duration_min"])

    requested_start = datetime.fromisoformat(f"{date_str}T{time_hhmm}:00")
    res = find_slot_or_suggest(
        business_id=business_id,
        service_id=service_id,
        requested_start=requested_start,
        duration_min=duration_min,
        max_suggestions=max_alternatives,
    )

    if res.ok:
        return CheckSlotResult(
            ok=True,
            reason="available",
            staff_id=res.staff_id,
            staff_name=_staff_name(res.staff_id) if res.staff_id else None,
            alternatives=[],
        )

    # build alternatives from suggestions
    alternatives: List[Dict[str, Any]] = []
    if res.suggestions:
        for (s, _e, sid) in res.suggestions[:max_alternatives]:
            alternatives.append(
                {"time": s.strftime("%H:%M"), "staff_id": sid, "staff_name": _staff_name(sid)}
            )

    return CheckSlotResult(
        ok=False,
        reason=res.reason if res.reason else "booked",
        staff_id=None,
        staff_name=None,
        alternatives=alternatives,
    )