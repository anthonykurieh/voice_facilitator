from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Tuple
import re


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAYS_CAP = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class DateResolution:
    resolved_date: Optional[str]                 # "YYYY-MM-DD"
    is_ambiguous: bool
    clarification_prompt: Optional[str]


def _next_weekday(d: date, weekday_index: int) -> date:
    """Return the next occurrence of weekday_index (0=Mon..6=Sun), strictly in the future."""
    delta = (weekday_index - d.weekday()) % 7
    if delta == 0:
        delta = 7
    return d + timedelta(days=delta)


def _this_or_next_weekday(d: date, weekday_index: int) -> date:
    """Return this week's occurrence if still upcoming (incl today), else next week's."""
    delta = (weekday_index - d.weekday()) % 7
    return d + timedelta(days=delta)


def resolve_date(date_text: str, today: Optional[date] = None) -> DateResolution:
    """
    Resolves natural language date text into an ISO date string.
    Key behavior:
    - "monday" is ambiguous -> ask confirmation (because user could mean this coming or next)
    - "next monday" is unambiguous -> resolves to next week's monday
    - "this monday" resolves to this week's monday (even if in past -> ambiguity handled by caller)
    - "today"/"tomorrow" resolve cleanly
    - "YYYY-MM-DD" resolves cleanly
    """
    if not today:
        today = date.today()

    raw = (date_text or "").strip().lower()
    if not raw:
        return DateResolution(None, True, "Could you tell me which date you had in mind?")

    # ISO date
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
        return DateResolution(parsed.isoformat(), False, None)
    except Exception:
        pass

    if raw == "today":
        return DateResolution(today.isoformat(), False, None)

    if raw == "tomorrow":
        return DateResolution((today + timedelta(days=1)).isoformat(), False, None)

    # "next monday"
    if raw.startswith("next "):
        wd = raw.replace("next ", "").strip()
        if wd in WEEKDAYS:
            idx = WEEKDAYS.index(wd)
            resolved = _next_weekday(today, idx)
            return DateResolution(resolved.isoformat(), False, None)

    # "this monday"
    if raw.startswith("this "):
        wd = raw.replace("this ", "").strip()
        if wd in WEEKDAYS:
            idx = WEEKDAYS.index(wd)
            resolved = _this_or_next_weekday(today, idx)
            # Still can be past if user says "this monday" on Tuesday; we ask later at booking time if needed
            return DateResolution(resolved.isoformat(), False, None)

    # plain weekday -> ambiguous
    if raw in WEEKDAYS:
        idx = WEEKDAYS.index(raw)
        resolved = _this_or_next_weekday(today, idx)
        # Ambiguous by design
        prompt = f"Just to confirm — do you mean this coming {WEEKDAYS_CAP[idx]}, {resolved.strftime('%B %d')}?"
        return DateResolution(None, True, prompt)

    # fallback ambiguous
    return DateResolution(None, True, "Could you clarify the date (for example: tomorrow, next Monday, or 2025-12-21)?")


def parse_time_to_hhmm(time_text: str) -> Optional[str]:
    """
    Parses common time strings into "HH:MM" 24-hour format.
    Examples: "4 pm" -> "16:00", "16:30" -> "16:30", "9am" -> "09:00"
    """
    if not time_text:
        return None
    raw = time_text.strip().lower()

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

    # "morning/afternoon/evening" -> pick defaults (still deterministic)
    if raw in {"morning"}:
        return "10:00"
    if raw in {"afternoon"}:
        return "14:00"
    if raw in {"evening"}:
        return "18:00"

    return None