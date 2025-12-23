from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional
import re

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAYS_CAP = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

MONTHS = {
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

@dataclass
class DateResolution:
    resolved_date: Optional[str]  # "YYYY-MM-DD"
    is_ambiguous: bool
    clarification_prompt: Optional[str]


def _next_weekday(d: date, weekday_index: int) -> date:
    delta = (weekday_index - d.weekday()) % 7
    if delta == 0:
        delta = 7
    return d + timedelta(days=delta)


def _this_or_next_weekday(d: date, weekday_index: int) -> date:
    delta = (weekday_index - d.weekday()) % 7
    return d + timedelta(days=delta)


def _safe_date(y: int, m: int, dd: int) -> Optional[date]:
    try:
        return date(y, m, dd)
    except Exception:
        return None


def _infer_year_for_month_day(today: date, month: int, day: int) -> date:
    """
    If user says 'Dec 22' without year:
      - use current year if not in past
      - otherwise use next year
    """
    candidate = _safe_date(today.year, month, day)
    if candidate is None:
        # fallback: next year attempt
        candidate = _safe_date(today.year + 1, month, day)
        if candidate is None:
            # will be handled by caller as ambiguous
            return today
    if candidate < today:
        nxt = _safe_date(today.year + 1, month, day)
        if nxt:
            return nxt
    return candidate


def resolve_date(date_text: str, today: Optional[date] = None) -> DateResolution:
    """
    Deterministic date resolver:
    - supports ISO YYYY-MM-DD
    - supports today/tomorrow
    - supports weekdays with "next"/"this"/plain ambiguous weekday
    - supports month-day formats: "Dec 22", "December 22nd", "22 Dec", "22 December 2025"
    """
    if not today:
        today = date.today()

    raw = (date_text or "").strip().lower()
    if not raw:
        return DateResolution(None, True, "Could you tell me which date you had in mind?")

    raw = re.sub(r"[,\.\s]+", " ", raw).strip()

    # ISO date
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
        if parsed < today:
            return DateResolution(None, True, f"That date already passed. Did you mean {parsed.replace(year=today.year+1).isoformat()}?")
        return DateResolution(parsed.isoformat(), False, None)
    except Exception:
        pass

    if raw == "today":
        return DateResolution(today.isoformat(), False, None)

    if raw == "tomorrow":
        return DateResolution((today + timedelta(days=1)).isoformat(), False, None)

    # "next monday"
    if raw.startswith("next "):
        wd = raw.replace("next ", "", 1).strip()
        if wd in WEEKDAYS:
            idx = WEEKDAYS.index(wd)
            resolved = _next_weekday(today, idx)
            return DateResolution(resolved.isoformat(), False, None)

    # "this monday"
    if raw.startswith("this "):
        wd = raw.replace("this ", "", 1).strip()
        if wd in WEEKDAYS:
            idx = WEEKDAYS.index(wd)
            resolved = _this_or_next_weekday(today, idx)
            if resolved < today:
                # user said "this monday" but it's already passed
                prompt = f"Just to confirm — did you mean next {WEEKDAYS_CAP[idx]} ({_next_weekday(today, idx).strftime('%B %d, %Y')})?"
                return DateResolution(None, True, prompt)
            return DateResolution(resolved.isoformat(), False, None)

    # plain weekday -> ambiguous by design
    if raw in WEEKDAYS:
        idx = WEEKDAYS.index(raw)
        resolved = _this_or_next_weekday(today, idx)
        prompt = f"Just to confirm — do you mean this coming {WEEKDAYS_CAP[idx]}, {resolved.strftime('%B %d, %Y')}?"
        return DateResolution(None, True, prompt)

    # Month-day parsing
    # Patterns:
    # 1) "dec 22" / "dec 22 2025"
    m = re.match(r"^(?P<mon>[a-z]+)\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:\s+(?P<year>\d{4}))?$", raw)
    if m and m.group("mon") in MONTHS:
        month = MONTHS[m.group("mon")]
        day = int(m.group("day"))
        year = int(m.group("year")) if m.group("year") else None
        if year is None:
            resolved = _infer_year_for_month_day(today, month, day)
        else:
            resolved = _safe_date(year, month, day)
            if resolved is None:
                return DateResolution(None, True, "That date doesn’t look valid — could you repeat it?")
        if resolved < today:
            return DateResolution(None, True, f"That date already passed. Did you mean {resolved.replace(year=resolved.year+1).isoformat()}?")
        return DateResolution(resolved.isoformat(), False, None)

    # 2) "22 dec" / "22 dec 2025"
    m = re.match(r"^(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?P<mon>[a-z]+)(?:\s+(?P<year>\d{4}))?$", raw)
    if m and m.group("mon") in MONTHS:
        month = MONTHS[m.group("mon")]
        day = int(m.group("day"))
        year = int(m.group("year")) if m.group("year") else None
        if year is None:
            resolved = _infer_year_for_month_day(today, month, day)
        else:
            resolved = _safe_date(year, month, day)
            if resolved is None:
                return DateResolution(None, True, "That date doesn’t look valid — could you repeat it?")
        if resolved < today:
            return DateResolution(None, True, f"That date already passed. Did you mean {resolved.replace(year=resolved.year+1).isoformat()}?")
        return DateResolution(resolved.isoformat(), False, None)

    return DateResolution(None, True, "Could you clarify the date (for example: tomorrow, next Monday, or 2025-12-21)?")


def parse_time_to_hhmm(time_text: str) -> Optional[str]:
    """
    Parses common time strings into "HH:MM" 24-hour format.
    """
    if not time_text:
        return None

    raw = time_text.strip().lower()
    raw = raw.replace(".", "").replace("  ", " ")

    # Already HH:MM
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", raw)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        return f"{hh:02d}:{mm:02d}"

    # "4pm", "4 pm", "4:30pm"
    m = re.match(r"^([01]?\d|2[0-3])(?::([0-5]\d))?\s*(am|pm)$", raw)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2) or "0")
        ampm = m.group(3)
        if ampm == "pm" and hh != 12:
            hh += 12
        if ampm == "am" and hh == 12:
            hh = 0
        return f"{hh:02d}:{mm:02d}"

    # "morning/afternoon/evening"
    if raw in {"morning"}:
        return "10:00"
    if raw in {"afternoon"}:
        return "14:00"
    if raw in {"evening"}:
        return "18:00"

    return None
