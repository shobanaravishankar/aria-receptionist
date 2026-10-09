"""Plain-English phrases for the voice agent. Pure functions; no personal data."""

from __future__ import annotations

from datetime import datetime


def spoken_time(moment: datetime) -> str:
    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"{hour} {suffix}" if moment.minute == 0 else f"{hour}:{moment.minute:02d} {suffix}"


def spoken_day(moment: datetime) -> str:
    return f"{moment:%A}, {moment:%B} {moment.day}"


def spoken_slot(moment: datetime) -> str:
    return f"{spoken_day(moment)} at {spoken_time(moment)}"


def spoken_duration(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if rest:
        parts.append(f"{rest} minutes")
    return " ".join(parts) or "0 minutes"


def join_choices(labels: list[str]) -> str:
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + ", or " + labels[-1]


def spoken_price(price_usd) -> str:
    return "the price isn't listed" if price_usd is None else f"${price_usd}"
