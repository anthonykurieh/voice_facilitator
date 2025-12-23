from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional
import re

from zoneinfo import ZoneInfo
from app.config import APP_TIMEZONE


@dataclass
class DateResolution:
    resolved_date: Optional[str]          # YYYY-MM-DD
    is_ambiguous: bool
    clarification_prompt: Optional[str] = None


def today_local() -> date:
    return datetime.now(ZoneInfo(APP_TIMEZONE)).date()


def resolve_date(date_text: str, today: Optional[date] = None) -> DateResolution:
    """
    Resolves:
    - "today", "tomorrow"
    - "next monday", "monday"
    - "december 22", "dec 22", "22 december"
    - "2025-12-22"
    Returns YYYY-MM-DD.
    """
    if not today:
        today = today_local()

    t = (date_text or "").strip().lower()
    if not t:
        return DateResolution(None, True, "Sorry — what date did you mean? For example: December 22.")

    # ISO date
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        d = datetime.strptime(t, "%Y-%m-%d").date()
        if d < today:
            return DateResolution(None, True, "That date already passed — what date would you like instead?")
        return DateResolution(d.isoformat(), False)

    if t in {"today"}:
        return DateResolution(today.isoformat(), False)

    if t in {"tomorrow"}:
        d = today + timedelta(days=1)
        return DateResolution(d.isoformat(), False)

    # weekday logic
    weekdays = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    for i, w in enumerate(weekdays):
        if w in t:
            # next monday vs monday
            delta = (i - today.weekday()) % 7
            if "next" in t:
                delta = delta if delta != 0 else 7
            else:
                # if they say "monday" and today is monday => ambiguous
                if delta == 0:
                    return DateResolution(None, True, f"Just to confirm — do you mean today ({today.isoformat()}) or next {w.title()}?")
            d = today + timedelta(days=delta)
            if d < today:
                return DateResolution(None, True, "That date already passed — what date would you like instead?")
            return DateResolution(d.isoformat(), False)

    # Month name + day (assume current year unless already passed -> next year)
    months = {
        "jan":1,"january":1,"feb":2,"february":2,"mar":3,"march":3,"apr":4,"april":4,"may":5,
        "jun":6,"june":6,"jul":7,"july":7,"aug":8,"august":8,"sep":9,"september":9,
        "oct":10,"october":10,"nov":11,"november":11,"dec":12,"december":12
    }

    # e.g. "december 22" / "dec 22"
    m = re.search(r"(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|september|oct|october|nov|november|dec|december)\s+(\d{1,2})", t)
    if m:
        month = months[m.group(1)]
        day = int(m.group(2))
        y = today.year
        try:
            d = date(y, month, day)
        except ValueError:
            return DateResolution(None, True, "That date doesn’t look valid — can you repeat it?")
        if d < today:
            # roll to next year
            d = date(y + 1, month, day)
        return DateResolution(d.isoformat(), False)

    # e.g. "22 december"
    m2 = re.search(r"(\d{1,2})\s+(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|september|oct|october|nov|november|dec|december)", t)
    if m2:
        day = int(m2.group(1))
        month = months[m2.group(2)]
        y = today.year
        try:
            d = date(y, month, day)
        except ValueError:
            return DateResolution(None, True, "That date doesn’t look valid — can you repeat it?")
        if d < today:
            d = date(y + 1, month, day)
        return DateResolution(d.isoformat(), False)

    return DateResolution(None, True, "Sorry — what date did you mean? For example: December 22.")


def parse_time_to_hhmm(time_text: str) -> Optional[str]:
    t = (time_text or "").strip().lower()
    if not t:
        return None

    # "16:00"
    if re.fullmatch(r"\d{1,2}:\d{2}", t):
        hh, mm = t.split(":")
        hh_i = int(hh)
        mm_i = int(mm)
        if 0 <= hh_i <= 23 and 0 <= mm_i <= 59:
            return f"{hh_i:02d}:{mm_i:02d}"
        return None

    # "4 pm", "4pm", "10am"
    m = re.fullmatch(r"(\d{1,2})(?:\s*)?(am|pm)", t)
    if m:
        hh = int(m.group(1))
        ap = m.group(2)
        if hh == 12:
            hh = 0 if ap == "am" else 12
        else:
            hh = hh if ap == "am" else hh + 12
        return f"{hh:02d}:00"

    # "4"
    if re.fullmatch(r"\d{1,2}", t):
        hh = int(t)
        if 0 <= hh <= 23:
            return f"{hh:02d}:00"

    return None