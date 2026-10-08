"""Pure availability rules: which slots fit, and why a slot is refused.

The rules, in order of importance:
  * UNKNOWN IS NOT FREE. If working hours, time off or appointments could not be read, the day
    is reported as unknown and no slot is offered.
  * A slot must lie fully inside a known working interval, including buffers, so a booking that
    would run past closing is rejected.
  * It must not overlap known time off or any appointment that blocks time (touching is fine).
  * It must start at least ``min_lead_minutes`` in the future and on the grid (e.g. :00/:15/:30/:45).
Reasons contain only times, never customer data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .models import (
    DaySnapshot,
    Interval,
    ServiceSpec,
    Slot,
    StaffDay,
    add_minutes,
    fmt,
    to_utc,
)


@dataclass(frozen=True)
class SlotSearch:
    slots: tuple[Slot, ...]
    unknown: tuple[str, ...] = ()  # reasons the day could not be fully evaluated
    notes: tuple[str, ...] = ()  # e.g. "known not working"


@dataclass(frozen=True)
class SlotCheck:
    ok: bool
    reasons: tuple[str, ...] = ()  # why a slot is refused (known facts)
    unknown: tuple[str, ...] = ()  # why it could not be judged


def unknown_reasons(staff_day: StaffDay) -> list[str]:
    reasons = []
    if staff_day.working is None:
        reasons.append("working hours unknown")
    if staff_day.time_off is None:
        reasons.append("time off unknown")
    if staff_day.appointments is None:
        reasons.append("existing appointments unknown")
    return reasons


def blocked_interval(start: datetime, service: ServiceSpec) -> Interval:
    return Interval(
        add_minutes(start, -service.buffer_before_minutes),
        add_minutes(start, service.duration_minutes + service.buffer_after_minutes),
    )


def service_interval(start: datetime, service: ServiceSpec) -> Interval:
    return Interval(start, add_minutes(start, service.duration_minutes))


def _conflicts(staff_day: StaffDay, blocked: Interval) -> list[str]:
    """Reasons a blocked interval is not free. Call only when nothing is unknown."""
    reasons = []
    if not any(w.contains(blocked) for w in (staff_day.working or ())):
        reasons.append(f"outside working hours ({blocked.label()} incl. buffers)")
    for off in staff_day.time_off or ():
        if off.overlaps(blocked):
            reasons.append(f"overlaps time off {off.label()}")
    for appt in staff_day.appointments or ():
        if appt.blocks_time and appt.interval.overlaps(blocked):
            reasons.append(f"overlaps existing appointment {appt.interval.label()}")
    return reasons


def _ceil_to_grid(moment: datetime, grid_minutes: int) -> datetime:
    """The first grid time (by the wall clock) at or after ``moment``; never earlier, so a lead time is never shortened."""
    base = moment.replace(second=0, microsecond=0)
    if moment.second or moment.microsecond:
        base = add_minutes(base, 1)  # round up to the next whole minute FIRST, then onto the grid
    minute_of_day = base.hour * 60 + base.minute
    return add_minutes(base, (-minute_of_day) % grid_minutes)


def validate_slot(
    snapshot: DaySnapshot,
    service: ServiceSpec,
    staff: str,
    start: datetime,
    *,
    now: datetime,
    min_lead_minutes: int = 60,
) -> SlotCheck:
    """Judge one specific start time against a fresh snapshot."""
    staff_day = snapshot.for_staff(staff)
    if staff_day is None:
        return SlotCheck(False, unknown=(f"staff {staff!r} not found in calendar snapshot",))
    unknown = unknown_reasons(staff_day)
    if unknown:
        return SlotCheck(False, unknown=tuple(unknown))

    reasons = []
    if to_utc(add_minutes(now, min_lead_minutes)) > to_utc(start):
        reasons.append(f"starts too soon (needs {min_lead_minutes} min lead)")
    reasons.extend(_conflicts(staff_day, blocked_interval(start, service)))
    return SlotCheck(not reasons, tuple(reasons))


def find_slots(
    snapshot: DaySnapshot,
    service: ServiceSpec,
    staff: str,
    *,
    now: datetime,
    earliest: Optional[datetime] = None,
    latest_end: Optional[datetime] = None,
    grid_minutes: int = 15,
    min_lead_minutes: int = 60,
) -> SlotSearch:
    """All start times on the grid that fit the service for one staff member on one day."""
    staff_day = snapshot.for_staff(staff)
    if staff_day is None:
        return SlotSearch((), unknown=(f"staff {staff!r} not found in calendar snapshot",))
    unknown = unknown_reasons(staff_day)
    if unknown:
        return SlotSearch((), unknown=tuple(unknown))
    if not staff_day.working:
        return SlotSearch((), notes=("known not working this day",))

    floor = add_minutes(now, min_lead_minutes)
    if earliest is not None and to_utc(earliest) > to_utc(floor):
        floor = earliest

    slots: list[Slot] = []
    for window in staff_day.working:
        candidate = _ceil_to_grid(max(window.start, floor, key=to_utc), grid_minutes)
        while True:
            blocked = blocked_interval(candidate, service)
            if to_utc(blocked.end) > to_utc(window.end):
                break
            svc = service_interval(candidate, service)
            if latest_end is not None and to_utc(svc.end) > to_utc(latest_end):
                break
            if to_utc(blocked.start) >= to_utc(window.start) and not _conflicts(staff_day, blocked):
                slots.append(Slot(staff_day.staff, svc, blocked))
            candidate = add_minutes(candidate, grid_minutes)
    return SlotSearch(tuple(slots))


def describe(search: SlotSearch) -> str:
    if search.unknown:
        return "UNKNOWN (not free): " + "; ".join(search.unknown)
    if not search.slots:
        return "no slots" + (f" ({'; '.join(search.notes)})" if search.notes else "")
    return ", ".join(fmt(s.service.start)[11:] for s in search.slots)
