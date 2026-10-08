"""Parsing of the times and dates the Booksy pages display. Pure functions, no I/O.

Rule everywhere: if the text is not understood, return None. Never guess.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

_CLOCK_FORMATS = ("%I:%M %p", "%I:%M%p", "%H:%M")
_DATE_FORMATS = (
    "%a, %d %b", "%a, %b %d", "%A, %d %B", "%A, %B %d",
    "%a, %d %b %Y", "%a, %b %d, %Y", "%A, %B %d, %Y",
    "%d %b %Y", "%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%m/%d/%Y",
)
_TIME_IN_LABEL = re.compile(r"\d{1,2}:\d{2}")
_HOUR_LABEL = re.compile(r"^\s*(\d{1,2})\s*(am|pm)\s*$", re.IGNORECASE)
_WEEKDAY_PREFIX = re.compile(r"^\s*([A-Za-z]{3,9}),")


def parse_clock(text: str) -> Optional[tuple[int, int]]:
    cleaned = (text or "").strip().upper().replace(" ", " ").replace("\xa0", " ")
    for fmt in _CLOCK_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return parsed.hour, parsed.minute
        except ValueError:
            continue
    return None


def parse_hour_label(text: str) -> Optional[int]:
    """The timeline's labels: '10 am' -> 10, '12 pm' -> 12, '12 am' -> 0, '1 pm' -> 13."""
    match = _HOUR_LABEL.match((text or "").replace(" ", " ").replace("\xa0", " "))
    if not match:
        return None
    hour, suffix = int(match.group(1)), match.group(2).lower()
    if not 1 <= hour <= 12:
        return None
    return (hour % 12) + (12 if suffix == "pm" else 0)


def parse_date_text(text: str, today: date) -> Optional[date]:
    """Understand a displayed date. None means 'cannot verify'."""
    cleaned = (text or "").strip().replace("\xa0", " ")
    low = cleaned.casefold()
    if low == "today":
        return today
    if low == "tomorrow":
        return today + timedelta(days=1)
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        if "%Y" in fmt:
            return parsed.date()
        best: Optional[date] = None  # no year shown: use the year that puts the date nearest to `today`
        for year in (today.year - 1, today.year, today.year + 1):
            try:
                candidate = date(year, parsed.month, parsed.day)
            except ValueError:
                continue
            if best is None or abs((candidate - today).days) < abs((best - today).days):
                best = candidate
        return best
    return None


def parse_month_label(text: str) -> Optional[tuple[int, int]]:
    cleaned = (text or "").strip()
    for fmt in ("%B %Y", "%b %Y"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return parsed.year, parsed.month
        except ValueError:
            continue
    return None


def day_from_label(label: str, today: date) -> Optional[date]:
    """'Mon, 12 Oct 10:00 AM - 7:00 PM' -> that date: the part before the first clock time."""
    head = _TIME_IN_LABEL.split(label or "", maxsplit=1)[0]
    return parse_date_text(head.strip(), today)


def weekday_in_label(label: str) -> Optional[int]:
    """0=Mon..6=Sun from a leading weekday name/abbreviation ('Mon,', 'Monday,'), else None."""
    match = _WEEKDAY_PREFIX.match(label or "")
    if not match:
        return None
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    prefix = match.group(1)[:3].lower()
    return names.index(prefix) if prefix in names else None


def hours_range_in_label(text: str) -> Optional[tuple[tuple[int, int], tuple[int, int]]]:
    """'10:00 AM - 7:00 PM' -> ((10, 0), (19, 0)). None if it is not exactly two clock times."""
    parts = re.split(r"\s[-–—]\s", (text or "").strip())
    if len(parts) != 2:
        return None
    start, end = parse_clock(parts[0]), parse_clock(parts[1])
    return (start, end) if start and end else None
