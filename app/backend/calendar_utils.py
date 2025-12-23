from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re


@dataclass
class ResolveDateResult:
    resolved_date: str
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

_DOW = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def resolve_date(text: str, today: date) -> ResolveDateResult:
    t = (text or "").strip().lower()
    if not t:
        return ResolveDateResult(resolved_date=today.isoformat(), is_ambiguous=True, clarification_prompt="Which date did you mean?")

    if "today" in t:
        return ResolveDateResult(resolved_date=today.isoformat(), is_ambiguous=False)

    if "tomorrow" in t:
        d = today + timedelta(days=1)
        return ResolveDateResult(resolved_date=d.isoformat(), is_ambiguous=False)

    if "day after tomorrow" in t:
        d = today + timedelta(days=2)
        return ResolveDateResult(resolved_date=d.isoformat(), is_ambiguous=False)

    # "next monday"
    m = re.search(r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b", t)
    if m:
        target = _DOW[m.group(1)]
        delta = (target - today.weekday()) % 7
        delta = 7 if delta == 0 else delta
        d = today + timedelta(days=delta)
        return ResolveDateResult(resolved_date=d.isoformat(), is_ambiguous=False)

    # plain weekday "monday" (ambiguous: could be upcoming)
    m = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b", t)
    if m:
        target = _DOW[m.group(1)]
        delta = (target - today.weekday()) % 7
        d = today + timedelta(days=delta)
        if delta == 0:
            # same weekday: ambiguous (this week vs next week)
            prompt = f"Just to confirm — do you mean today ({today.isoformat()}) or next {m.group(1).title()}?"
            return ResolveDateResult(resolved_date=today.isoformat(), is_ambiguous=True, clarification_prompt=prompt)
        return ResolveDateResult(resolved_date=d.isoformat(), is_ambiguous=False)

    # formats:
    #  - "24/12/2025" or "24-12-2025"
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", t)
    if m:
        dd = int(m.group(1))
        mm = int(m.group(2))
        yy = m.group(3)
        year = int(yy) if yy else today.year
        if year < 100:
            year += 2000
        d = date(year, mm, dd)
        return ResolveDateResult(resolved_date=d.isoformat(), is_ambiguous=False)

    # "december 22" / "22 december" / optional year
    m = re.search(r"\b(\d{1,2})\s+(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)(?:\s+(\d{4}))?\b", t)
    if m:
        dd = int(m.group(1))
        mm = _MONTHS[m.group(2)]
        year = int(m.group(3)) if m.group(3) else today.year
        d = date(year, mm, dd)
        return ResolveDateResult(resolved_date=d.isoformat(), is_ambiguous=False)

    m = re.search(r"\b(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\s+(\d{1,2})(?:\s+(\d{4}))?\b", t)
    if m:
        mm = _MONTHS[m.group(1)]
        dd = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        d = date(year, mm, dd)
        return ResolveDateResult(resolved_date=d.isoformat(), is_ambiguous=False)

    # If nothing matches: ambiguous
    return ResolveDateResult(
        resolved_date=today.isoformat(),
        is_ambiguous=True,
        clarification_prompt="Sorry — what date did you mean? For example: December 22.",
    )


def parse_time_to_hhmm(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return ""

    # "16:30"
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", t)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"

    # "4 pm", "4pm", "10 am"
    m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", t)
    if m:
        hh = int(m.group(1))
        ampm = m.group(2)
        if hh == 12:
            hh = 0
        if ampm == "pm":
            hh += 12
        return f"{hh:02d}:00"

    # plain "16 hundred" / "1600"
    m = re.search(r"\b(\d{3,4})\b", t)
    if m:
        val = m.group(1)
        if len(val) == 3:
            val = "0" + val
        hh = int(val[:2])
        mm = int(val[2:])
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"

    return ""