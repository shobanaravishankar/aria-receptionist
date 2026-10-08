"""Plain data types for the booking prototype. No I/O in this module.

All times are timezone-aware. Comparisons and durations go through UTC so a daylight-saving
change can never skew an appointment length (Python compares two datetimes that share one
tzinfo by wall-clock, which is wrong across a DST change).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

UTC = timezone.utc


def to_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    return moment.astimezone(UTC)


def add_minutes(moment: datetime, minutes: int) -> datetime:
    """Add real elapsed minutes (not wall-clock minutes) and return in the original zone."""
    return (to_utc(moment) + timedelta(minutes=minutes)).astimezone(moment.tzinfo)


def fmt(moment: datetime) -> str:
    """Short, personal-data-free rendering used in messages and logs."""
    return moment.strftime("%Y-%m-%d %H:%M")


@dataclass(frozen=True)
class Interval:
    """Half-open time range [start, end). Touching intervals do not overlap."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if to_utc(self.end) <= to_utc(self.start):
            raise ValueError("Interval end must be after start")

    def overlaps(self, other: "Interval") -> bool:
        return to_utc(self.start) < to_utc(other.end) and to_utc(other.start) < to_utc(self.end)

    def contains(self, other: "Interval") -> bool:
        return to_utc(self.start) <= to_utc(other.start) and to_utc(other.end) <= to_utc(self.end)

    @property
    def minutes(self) -> int:
        return int((to_utc(self.end) - to_utc(self.start)).total_seconds() // 60)

    def label(self) -> str:
        return f"{self.start.strftime('%H:%M')}-{self.end.strftime('%H:%M')}"


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    duration_minutes: int
    buffer_before_minutes: int = 0
    buffer_after_minutes: int = 0


@dataclass(frozen=True)
class Appointment:
    """An existing calendar entry. ``blocks_time`` is False for entries the driver can
    positively identify as cancelled / no-show; anything uncertain must stay True."""

    staff: str
    interval: Interval
    service: str = ""
    note: str = ""
    blocks_time: bool = True


@dataclass(frozen=True)
class StaffDay:
    """What is known about one staff member on one day.

    ``None`` means UNKNOWN (could not be read); an empty tuple means KNOWN to be empty.
    Unknown data is never treated as free.
    """

    staff: str
    working: Optional[tuple[Interval, ...]]
    time_off: Optional[tuple[Interval, ...]]
    appointments: Optional[tuple[Appointment, ...]]


@dataclass(frozen=True)
class DaySnapshot:
    day: date
    staff_days: tuple[StaffDay, ...]
    captured_at: datetime

    def for_staff(self, staff: str) -> Optional[StaffDay]:
        wanted = staff.strip().casefold()
        for sd in self.staff_days:
            if sd.staff.strip().casefold() == wanted:
                return sd
        return None


@dataclass(frozen=True)
class Slot:
    staff: str
    service: Interval  # what the client occupies
    blocked: Interval  # service plus buffers


@dataclass(frozen=True)
class AppointmentSpec:
    """Everything the driver needs to create one appointment. Deliberately has no client
    contact fields: test bookings are walk-ins with an internal note only."""

    staff: str
    service_name: str
    start: datetime
    duration_minutes: int
    note: str

    @property
    def interval(self) -> Interval:
        return Interval(self.start, add_minutes(self.start, self.duration_minutes))
