import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.backend.db import find_appointment_candidates, list_upcoming_appointments


_ID_RE = re.compile(r"(?:#|appointment\s*)(\d+)", re.IGNORECASE)


def parse_appointment_id_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    m = _ID_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


@dataclass
class PickAppointmentResult:
    ok: bool
    appointment_id: Optional[int] = None
    candidates: Optional[List[Dict[str, Any]]] = None
    reason: Optional[str] = None


def pick_appointment_for_action(
    business_id: int,
    customer_id: int,
    *,
    date_iso: Optional[str] = None,   # "YYYY-MM-DD"
    time_hhmm: Optional[str] = None,  # "HH:MM"
    limit: int = 20,
) -> PickAppointmentResult:
    if not date_iso:
        upcoming = list_upcoming_appointments(business_id, customer_id, now=datetime.now(), limit=10)
        if not upcoming:
            return PickAppointmentResult(ok=False, reason="no_upcoming")
        if len(upcoming) == 1:
            return PickAppointmentResult(ok=True, appointment_id=int(upcoming[0]["id"]))
        return PickAppointmentResult(ok=False, candidates=upcoming, reason="need_choice")

    day_start = datetime.strptime(date_iso, "%Y-%m-%d")
    day_end = day_start + timedelta(days=1)

    cands = find_appointment_candidates(
        business_id=business_id,
        customer_id=customer_id,
        date_start=day_start,
        date_end=day_end,
        status="confirmed",
        limit=limit,
    )

    if not cands:
        return PickAppointmentResult(ok=False, reason="none_that_day")

    if time_hhmm:
        target = datetime.strptime(f"{date_iso} {time_hhmm}", "%Y-%m-%d %H:%M")
        best: Optional[Tuple[int, float]] = None
        for a in cands:
            dt = a["start_time"]
            delta = abs((dt - target).total_seconds()) / 60.0
            if best is None or delta < best[1]:
                best = (int(a["id"]), delta)
        if best and best[1] <= 30:
            return PickAppointmentResult(ok=True, appointment_id=best[0])

    if len(cands) == 1:
        return PickAppointmentResult(ok=True, appointment_id=int(cands[0]["id"]))

    return PickAppointmentResult(ok=False, candidates=cands, reason="need_choice")


def format_appointment_choices(cands: List[Dict[str, Any]], max_items: int = 5) -> str:
    parts = []
    for a in cands[:max_items]:
        start = a["start_time"].strftime("%Y-%m-%d %H:%M")
        parts.append(f"#{a['id']} — {a['service_name']} with {a['staff_name']} at {start}")
    return "; ".join(parts)