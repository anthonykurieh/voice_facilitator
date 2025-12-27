# app/backend/calendar_utils.py

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional


@dataclass
class ResolveDateResult:
    resolved_date: Optional[str]          # YYYY-MM-DD
    is_ambiguous: bool = False
    clarification_prompt: Optional[str] = None


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


def _normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _to_iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def resolve_date(text: str, today: date) -> ResolveDateResult:
    """
    Resolve human date strings into ISO YYYY-MM-DD.

    Examples:
      - "tomorrow"
      - "after tomorrow"
      - "Saturday 27th of December"
      - "27 December"
      - "Dec 27"
      - "2025-12-27"
      - "12/27/2025" or "27/12/2025" (may be ambiguous)
    """
    raw = _normalize_spaces(text).lower()
    raw = raw.replace(",", " ")
    raw = raw.replace("of", " ")
    raw = _strip_ordinal(raw)
    raw = _normalize_spaces(raw)

    if not raw:
        return ResolveDateResult(resolved_date=None, is_ambiguous=True,
                                 clarification_prompt="Sorry — what date did you mean? For example: December 27.")

    # Relative
    if raw in {"today"}:
        return ResolveDateResult(resolved_date=_to_iso(today))
    if raw in {"tomorrow"}:
        return ResolveDateResult(resolved_date=_to_iso(today + timedelta(days=1)))
    if raw in {"after tomorrow", "day after tomorrow"}:
        return ResolveDateResult(resolved_date=_to_iso(today + timedelta(days=2)))

    # ISO yyyy-mm-dd
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", raw)
    if m:
        y, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            d = date(y, mm, dd)
            return ResolveDateResult(resolved_date=_to_iso(d))
        except ValueError:
            return ResolveDateResult(
                resolved_date=None,
                is_ambiguous=True,
                clarification_prompt="That date doesn’t look valid — can you say it like “December 27, 2025”?"
            )

    # Slash formats: 12/27/2025 or 27/12/2025 (ambiguous if both <= 12)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))\b", raw)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        year = int(y)
        if year < 100:
            year += 2000

        # ambiguous when a<=12 and b<=12
        if a <= 12 and b <= 12:
            return ResolveDateResult(
                resolved_date=None,
                is_ambiguous=True,
                clarification_prompt="Did you mean month/day or day/month? For example: “December 27, 2025”."
            )

        # If first number > 12, it's day/month
        if a > 12 and b <= 12:
            dd, mm = a, b
        else:
            # otherwise assume month/day
            mm, dd = a, b

        try:
            d = date(year, mm, dd)
            return ResolveDateResult(resolved_date=_to_iso(d))
        except ValueError:
            return ResolveDateResult(
                resolved_date=None,
                is_ambiguous=True,
                clarification_prompt="That date doesn’t look valid — can you say it like “December 27, 2025”?"
            )

    # Patterns with month names:
    #  - "27 december 2025"
    #  - "december 27 2025"
    #  - "dec 27"
    tokens = raw.split()

    # Remove weekday words if present
    weekdays = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    tokens = [t for t in tokens if t not in weekdays]
    raw2 = " ".join(tokens)

    # day month [year]
    m = re.search(r"\b(\d{1,2})\s+([a-z]+)\s*(\d{4})?\b", raw2)
    if m and m.group(2) in _MONTHS:
        dd = int(m.group(1))
        mm = _MONTHS[m.group(2)]
        year = int(m.group(3)) if m.group(3) else today.year

        # If no year given and date already passed this year, roll to next year
        try:
            d = date(year, mm, dd)
        except ValueError:
            return ResolveDateResult(
                resolved_date=None,
                is_ambiguous=True,
                clarification_prompt="That date doesn’t look valid — can you repeat it?"
            )

        if not m.group(3) and d < today:
            try:
                d = date(today.year + 1, mm, dd)
            except ValueError:
                pass

        return ResolveDateResult(resolved_date=_to_iso(d))

    # month day [year]
    m = re.search(r"\b([a-z]+)\s+(\d{1,2})\s*(\d{4})?\b", raw2)
    if m and m.group(1) in _MONTHS:
        mm = _MONTHS[m.group(1)]
        dd = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year

        try:
            d = date(year, mm, dd)
        except ValueError:
            return ResolveDateResult(
                resolved_date=None,
                is_ambiguous=True,
                clarification_prompt="That date doesn’t look valid — can you repeat it?"
            )

        if not m.group(3) and d < today:
            try:
                d = date(today.year + 1, mm, dd)
            except ValueError:
                pass

        return ResolveDateResult(resolved_date=_to_iso(d))

    # If we still can't parse, ask explicitly
    return ResolveDateResult(
        resolved_date=None,
        is_ambiguous=True,
        clarification_prompt="Sorry — what date did you mean? For example: December 27."
    )


def parse_time_to_hhmm(text: str) -> Optional[str]:
    """
    Parse common time formats into HH:MM (24-hour).
    Examples:
      - "4pm" -> "16:00"
      - "4 pm" -> "16:00"
      - "16:30" -> "16:30"
      - "10" (ambiguous) -> None
      - "10 am" -> "10:00"
    """
    raw = _normalize_spaces(text).lower().replace(".", "")
    if not raw:
        return None

    # 24h HH:MM
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", raw)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        return f"{hh:02d}:{mm:02d}"

    # "4 pm", "4pm", "4:15 pm", etc.
    m = re.search(r"\b(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b", raw)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2)) if m.group(2) else 0
        mer = m.group(3)

        if hh == 12:
            hh = 0
        if mer == "pm":
            hh += 12

        if hh < 0 or hh > 23:
            return None

        return f"{hh:02d}:{mm:02d}"

    # "1600" or "0930"
    m = re.search(r"\b(\d{3,4})\b", raw)
    if m:
        num = m.group(1)
        if len(num) == 3:
            hh = int(num[0])
            mm = int(num[1:])
        else:
            hh = int(num[:2])
            mm = int(num[2:])
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"

    return None