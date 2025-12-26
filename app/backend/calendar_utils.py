# app/backend/calendar_utils.py
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional


@dataclass
class DateResolveResult:
    resolved_date: Optional[str]  # ISO: YYYY-MM-DD
    is_ambiguous: bool
    clarification_prompt: str = ""


_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _strip_ordinal(s: str) -> str:
    # 27th -> 27, 1st -> 1, etc.
    return re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", s, flags=re.IGNORECASE)


def resolve_date(text: str, today: Optional[date] = None) -> DateResolveResult:
    """
    Resolve human date text to ISO YYYY-MM-DD.
    Never throws; returns is_ambiguous=True with a prompt on invalid/unclear.
    """
    today = today or date.today()
    raw = (text or "").strip().lower()
    if not raw:
        return DateResolveResult(None, True, "Sorry — what date did you mean? For example: December 27.")

    s = _strip_ordinal(raw)

    # Relative dates
    if "day after tomorrow" in s:
        return DateResolveResult((today + timedelta(days=2)).isoformat(), False, "")
    if "tomorrow" in s:
        return DateResolveResult((today + timedelta(days=1)).isoformat(), False, "")
    if "today" in s:
        return DateResolveResult(today.isoformat(), False, "")

    # Remove weekday words (avoid confusion)
    s = re.sub(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", "", s).strip()

    # Extract year if present
    year = today.year
    y = re.search(r"\b(20\d{2})\b", s)
    if y:
        year = int(y.group(1))

    # Pattern A: "27 december" or "27 of december"
    m = re.search(r"\b(\d{1,2})\s*(of\s+)?([a-z]+)\b", s)
    if m:
        dd = int(m.group(1))
        mon_word = m.group(3)
        mm = _MONTHS.get(mon_word, _MONTHS.get(mon_word[:3]))
        if not mm:
            return DateResolveResult(None, True, "Sorry — which month is that? For example: December 27.")

        try:
            d = date(year, mm, dd)
            return DateResolveResult(d.isoformat(), False, "")
        except ValueError:
            return DateResolveResult(None, True, "That date doesn’t look valid — can you repeat it? For example: December 27, 2025.")

    # Pattern B: "december 27"
    m = re.search(r"\b([a-z]+)\s+(\d{1,2})\b", s)
    if m:
        mon_word = m.group(1)
        dd = int(m.group(2))
        mm = _MONTHS.get(mon_word, _MONTHS.get(mon_word[:3]))
        if not mm:
            return DateResolveResult(None, True, "Sorry — which month is that? For example: December 27.")

        try:
            d = date(year, mm, dd)
            return DateResolveResult(d.isoformat(), False, "")
        except ValueError:
            return DateResolveResult(None, True, "That date doesn’t look valid — can you repeat it? For example: December 27, 2025.")

    # Pattern C: ISO already (YYYY-MM-DD)
    try:
        dt = datetime.strptime(s.strip(), "%Y-%m-%d").date()
        return DateResolveResult(dt.isoformat(), False, "")
    except Exception:
        pass

    return DateResolveResult(None, True, "Sorry — what date did you mean? For example: December 27.")


def parse_time_to_hhmm(text: str) -> Optional[str]:
    """
    Accepts: "4 pm", "4pm", "16:30", "1600", "sixteen hundred" (best-effort numeric)
    Returns "HH:MM" or None.
    """
    if not text:
        return None
    s = text.strip().lower()

    # 16:30
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", s)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        return f"{hh:02d}:{mm:02d}"

    # 1600
    m = re.search(r"\b([01]\d|2[0-3])([0-5]\d)\b", re.sub(r"\s+", "", s))
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        return f"{hh:02d}:{mm:02d}"

    # 4pm / 4 pm / 4 p.m.
    m = re.search(r"\b(\d{1,2})(?:\s*:\s*([0-5]\d))?\s*(am|pm)\b", s.replace(".", ""))
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2) or "00")
        ampm = m.group(3)
        if hh == 12:
            hh = 0
        if ampm == "pm":
            hh += 12
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"

    # Bare hour: "10" => 10:00
    m = re.search(r"\b(\d{1,2})\b", s)
    if m:
        hh = int(m.group(1))
        if 0 <= hh <= 23:
            return f"{hh:02d}:00"

    return None