from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Any, Dict, List, Optional, Tuple, Union

from app.backend.db import db_cursor, get_service_by_name_or_code, get_staff_name


@dataclass
class SlotSuggestion:
    time_hhmm: str
    staff_id: int
    staff_name: str


@dataclass
class CheckSlotResult:
    ok: bool
    reason: str  # available | closed | booked | no_staff | invalid_service
    staff_id: Optional[int] = None
    staff_name: Optional[str] = None
    alternatives: Optional[List[Dict[str, Any]]] = None


def _dow(dt: datetime) -> int:
    return dt.weekday()  # Mon=0 .. Sun=6


def _time_from_mysql(v: Union[time, timedelta]) -> time:
    # ✅ FIX: PyMySQL frequently returns TIME as timedelta
    if isinstance(v, time):
        return v
    if isinstance(v, timedelta):
        base = datetime(2000, 1, 1) + v
        return base.time()
    raise TypeError(f"Unexpected TIME type from MySQL: {type(v)}")


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
            JOIN staff_services ss
              ON ss.staff_id=s.id AND ss.business_id=s.business_id
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
    search_days: int = 14,
    step_min: int = 15,
    max_suggestions: int = 5,
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
    max_suggestions: int = 5,
):
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


def _hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def find_earliest_availability(
    business_id: int,
    service_name: str,
    date_str: str,
    preferred_staff: Optional[str] = None,
    *,
    step_min: int = 15,
):
    svc = get_service_by_name_or_code(business_id, service_name)
    if not svc:
        return CheckSlotResult(ok=False, reason="invalid_service", alternatives=[])

    service_id = int(svc["id"])
    duration_min = int(svc["duration_min"])

    # start scanning from opening time
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    anchor = datetime.combine(target_date, time(0, 0))
    hours = _get_business_hours(business_id, anchor)
    if not hours:
        return CheckSlotResult(ok=False, reason="closed", alternatives=[])

    open_t, close_t, is_closed = hours
    if is_closed:
        return CheckSlotResult(ok=False, reason="closed", alternatives=[])

    start = datetime.combine(target_date, open_t)
    end_day = datetime.combine(target_date, close_t)
    step = timedelta(minutes=step_min)

    # restrict candidate staff if preferred_staff provided
    staff_ids = _staff_candidates_for_service(business_id, service_id)
    if not staff_ids:
        return CheckSlotResult(ok=False, reason="no_staff", alternatives=[])

    if preferred_staff:
        pref = preferred_staff.strip().lower()
        with db_cursor() as cur:
            cur.execute(
                "SELECT id FROM staff WHERE business_id=%s AND active=1 AND LOWER(name) LIKE %s LIMIT 1",
                (business_id, f"%{pref}%"),
            )
            row = cur.fetchone()
            if row and int(row["id"]) in staff_ids:
                staff_ids = [int(row["id"])]

    while start + timedelta(minutes=duration_min) <= end_day:
        end = start + timedelta(minutes=duration_min)
        for sid in staff_ids:
            if not _has_overlap(sid, start, end):
                return CheckSlotResult(
                    ok=True,
                    reason="available",
                    staff_id=sid,
                    staff_name=get_staff_name(sid) or "Staff",
                    alternatives=[{"time": _hhmm(start), "staff_id": sid, "staff_name": get_staff_name(sid) or "Staff"}],
                )
        start += step

    return CheckSlotResult(ok=False, reason="booked", alternatives=[])


def check_slot_and_suggest(
    business_id: int,
    service_name: str,
    date_str: str,
    time_hhmm: str,
    preferred_staff: Optional[str] = None,
    *,
    max_alternatives: int = 3,
):
    svc = get_service_by_name_or_code(business_id, service_name)
    if not svc:
        return CheckSlotResult(ok=False, reason="invalid_service", alternatives=[])

    service_id = int(svc["id"])
    duration_min = int(svc["duration_min"])

    target_dt = datetime.strptime(f"{date_str} {time_hhmm}", "%Y-%m-%d %H:%M")

    ok, reason, staff_id, start_time, end_time, suggestions = find_slot_or_suggest(
        business_id, service_id, target_dt, duration_min, max_suggestions=10
    )

    if ok and staff_id:
        return CheckSlotResult(
            ok=True,
            reason="available",
            staff_id=staff_id,
            staff_name=get_staff_name(staff_id) or "Staff",
            alternatives=[],
        )

    alternatives: List[Dict[str, Any]] = []
    for s_start, s_end, sid in (suggestions or [])[:max_alternatives]:
        alternatives.append(
            {"time": _hhmm(s_start), "staff_id": sid, "staff_name": get_staff_name(sid) or "Staff"}
        )

    return CheckSlotResult(ok=False, reason=reason, staff_id=None, staff_name=None, alternatives=alternatives)