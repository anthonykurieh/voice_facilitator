from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import List, Optional, Tuple, Dict, Any

from zoneinfo import ZoneInfo

from app.backend.db import db_cursor, get_service_by_name_or_code
from app.backend.calendar_utils import today_local
from app.config import APP_TIMEZONE


@dataclass
class AvailabilityResult:
    ok: bool
    reason: str  # "available" | "closed" | "no_staff" | "booked"
    staff_id: Optional[int] = None
    staff_name: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    suggestions: Optional[List[Tuple[datetime, datetime, int]]] = None  # (start, end, staff_id)


@dataclass
class EarliestAvailability:
    ok: bool
    start_hhmm: Optional[str] = None
    staff_id: Optional[int] = None
    staff_name: Optional[str] = None


@dataclass
class SlotCheckResult:
    ok: bool
    staff_id: Optional[int] = None
    staff_name: Optional[str] = None
    alternatives: Optional[List[Dict[str, Any]]] = None


def _dow(dt: datetime) -> int:
    return dt.weekday()


def _get_business_hours(business_id: int, dt: datetime) -> Optional[Tuple[time, time, bool]]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT open_time, close_time, is_closed FROM business_hours WHERE business_id=%s AND dow=%s",
            (business_id, _dow(dt)),
        )
        row = cur.fetchone()
        if not row:
            return None
        # Workbench can sometimes return TIME as timedelta in some configs; normalize:
        open_t = row["open_time"]
        close_t = row["close_time"]

        if isinstance(open_t, timedelta):
            open_t = (datetime.min + open_t).time()
        if isinstance(close_t, timedelta):
            close_t = (datetime.min + close_t).time()

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
        cur.execute("SELECT name FROM staff WHERE id=%s", (staff_id,))
        r = cur.fetchone()
        return r["name"] if r else "Staff"


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
    max_suggestions: int = 5,
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
        if not _has_overlap(sid, requested_start, end):
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


# ---------------------------
# Public wrappers used by simulate_voice_call.py
# ---------------------------

def find_earliest_availability(
    business_id: int,
    service_name: str,
    date_str: str,
    preferred_staff: Optional[str] = None,
) -> EarliestAvailability:
    svc = get_service_by_name_or_code(business_id, service_name)
    if not svc:
        return EarliestAvailability(ok=False)

    duration_min = int(svc["duration_min"])
    tz = ZoneInfo(APP_TIMEZONE)

    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    # prevent past dates
    if day < today_local():
        return EarliestAvailability(ok=False)

    # start search at opening time for that day (or "now" if today)
    base_dt = datetime(day.year, day.month, day.day, 0, 0, tzinfo=tz).replace(tzinfo=None)
    hours = _get_business_hours(business_id, base_dt)
    if not hours:
        return EarliestAvailability(ok=False)
    open_t, _, is_closed = hours
    if is_closed:
        return EarliestAvailability(ok=False)

    start = datetime.combine(day, open_t)
    res = find_slot_or_suggest(
        business_id=business_id,
        service_id=int(svc["id"]),
        requested_start=start,
        duration_min=duration_min,
        search_days=0,
        step_min=15,
        max_suggestions=1,
    )

    if res.ok and res.start_time:
        return EarliestAvailability(
            ok=True,
            start_hhmm=res.start_time.strftime("%H:%M"),
            staff_id=res.staff_id,
            staff_name=res.staff_name,
        )

    # fallback to first suggestion
    if res.suggestions:
        s0, _, sid = res.suggestions[0]
        return EarliestAvailability(ok=True, start_hhmm=s0.strftime("%H:%M"), staff_id=sid, staff_name=_staff_name(sid))

    return EarliestAvailability(ok=False)


def check_slot_and_suggest(
    business_id: int,
    service_name: str,
    date_str: str,
    time_hhmm: str,
    preferred_staff: Optional[str] = None,
    max_alternatives: int = 3,
) -> SlotCheckResult:
    svc = get_service_by_name_or_code(business_id, service_name)
    if not svc:
        return SlotCheckResult(ok=False, alternatives=[])

    duration_min = int(svc["duration_min"])
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    if day < today_local():
        return SlotCheckResult(ok=False, alternatives=[])

    hh, mm = [int(x) for x in time_hhmm.split(":")]
    requested_start = datetime(day.year, day.month, day.day, hh, mm)

    res = find_slot_or_suggest(
        business_id=business_id,
        service_id=int(svc["id"]),
        requested_start=requested_start,
        duration_min=duration_min,
        search_days=14,
        step_min=15,
        max_suggestions=max_alternatives,
    )

    if res.ok:
        return SlotCheckResult(ok=True, staff_id=res.staff_id, staff_name=res.staff_name, alternatives=[])

    alts = []
    if res.suggestions:
        for s, _, sid in res.suggestions[:max_alternatives]:
            alts.append({"time": s.strftime("%H:%M"), "staff_id": sid, "staff_name": _staff_name(sid)})

    return SlotCheckResult(ok=False, alternatives=alts)