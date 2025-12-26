# app/backend/appointments.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.backend.db import list_upcoming_appointments


@dataclass
class PickResult:
    ok: bool
    reason: str  # "picked" | "no_upcoming" | "need_disambiguation"
    appointment_id: Optional[int] = None
    candidates: Optional[List[Dict[str, Any]]] = None


def parse_appointment_id_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"#\s*(\d+)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"\bappointment\s*(\d+)\b", text.lower())
    if m:
        return int(m.group(1))
    return None


def format_appointment_choices(cands: List[Dict[str, Any]]) -> str:
    # Expect dicts containing id, service_name, staff_name, start_time
    parts = []
    for a in cands[:5]:
        sid = a.get("id")
        svc = a.get("service_name", "service")
        staff = a.get("staff_name", "staff")
        st = a.get("start_time")
        st_s = st.strftime("%a %Y-%m-%d %H:%M") if hasattr(st, "strftime") else str(st)
        parts.append(f"#{sid} — {svc} with {staff} at {st_s}")
    return "; ".join(parts)


def pick_appointment_for_action(
    business_id: int,
    customer_id: int,
    *,
    date_iso: Optional[str] = None,
    time_hhmm: Optional[str] = None,
    limit: int = 10,
) -> PickResult:
    upcoming = list_upcoming_appointments(business_id, customer_id, now=None, limit=limit) or []

    if not upcoming:
        return PickResult(ok=False, reason="no_upcoming", candidates=[])

    # If user supplied date/time, filter candidates
    cands = upcoming
    if date_iso:
        cands = [a for a in cands if str(a["start_time"].date()) == date_iso]
    if time_hhmm:
        cands = [a for a in cands if a["start_time"].strftime("%H:%M") == time_hhmm]

    if len(cands) == 1:
        return PickResult(ok=True, reason="picked", appointment_id=int(cands[0]["id"]), candidates=cands)

    if len(cands) == 0:
        # if filtering led to none, fall back to showing upcoming
        cands = upcoming

    # If still multiple, ask user to choose
    return PickResult(ok=False, reason="need_disambiguation", candidates=cands)