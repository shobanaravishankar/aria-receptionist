"""The website's customer-facing 'open times' view: which start times are genuinely open for a chosen treatment and day, derived from the SAME calendar the voice assistant reads.

Nothing here is a second source of truth. The day is the dataset's merged plan (the generated schedule plus any stored appointments and their cleanup), turned into the same
snapshot the voice tools read, and each technician's slots come from the same ``find_slots`` the voice's alternatives use: the whole service plus its cleanup must fit inside known
working hours, clear of breaks and appointments, no sooner than the minimum notice, on the quarter-hour grid. Only the eligible technicians (by stable id) are considered.

What a visitor may see is deliberately small: start times and technician first names. Never another client's name or appointment, never a count of anything hidden.

Honest outcomes:
  open     at least one time is open (``partial`` says some technician's schedule could not be read, so nothing is claimed about them)
  none     every eligible schedule was read and none can take the full service that day (or nobody who does it works that day)
  unknown  nothing is open and at least one eligible schedule could NOT be read, so 'no openings' cannot be claimed
  outside  the day is not one the demo calendar covers
Unknown is never free.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from ..availability import find_slots
from ..models import ServiceSpec, to_utc
from ..voice.speech import spoken_day, spoken_duration, spoken_time
from .dataset import DemoDataset, NotCovered
from .driver import snapshot_from_plan

MAX_TIMES = 48


def open_times(
    dataset: DemoDataset, service_id: str, day: date, *, tz: ZoneInfo, now: datetime, staff: Optional[str] = None, grid_minutes: int = 15, min_lead_minutes: int = 60,
) -> dict:
    service = dataset.registry.get(service_id)
    if service is None or not service.verified:
        return {"status": "error", "reason": "unknown_service", "message": "That treatment isn't on the list of treatments this calendar can check."}
    base = {"service_id": service.service_id, "service": service.booksy_name, "duration_minutes": service.duration_minutes, "date": day.isoformat(), "day": spoken_day(
        datetime(day.year, day.month, day.day, tzinfo=tz))}
    if not dataset.covers(day):
        return {**base, "status": "outside", "times": [], "message": f"The calendar only covers {dataset.first_day.isoformat()} to {dataset.last_day.isoformat()}, so times for that day can't be shown."}
    try:
        plan = dataset.day_plan(day)
    except NotCovered:
        return {**base, "status": "outside", "times": [], "message": "That day isn't on the demo calendar."}
    snapshot = snapshot_from_plan(plan, tz, now.astimezone(tz))
    eligible = [sd for sd in snapshot.staff_days if service.eligible(sd.staff, sd.staff_id)]
    if staff:
        chosen = [sd for sd in eligible if sd.staff.strip().casefold() == staff.strip().casefold()]
        if not chosen:
            who_does = [s.name for s in dataset.roster if service.eligible(s.name, s.staff_id)]
            if any(n.casefold() == staff.strip().casefold() for n in who_does):
                return {**base, "status": "none", "times": [], "technician": staff, "message": f"{staff} isn't working on {base['day']}."}
            return {**base, "status": "error", "reason": "staff_not_eligible", "message": f"{staff} doesn't do {service.booksy_name}."}
        eligible = chosen
    if not eligible:
        return {**base, "status": "none", "times": [], "message": f"Nobody who does {service.booksy_name} is working on {base['day']}."}
    spec = ServiceSpec(service.booksy_name, service.duration_minutes, service.buffer_before_minutes, service.buffer_after_minutes)
    by_start: dict[int, list[str]] = {}
    unreadable: list[str] = []
    for sd in eligible:
        found = find_slots(snapshot, spec, sd.staff, now=now.astimezone(tz), grid_minutes=grid_minutes, min_lead_minutes=min_lead_minutes)
        if found.unknown:
            unreadable.append(sd.staff)
            continue
        for slot in found.slots:
            by_start.setdefault(int(to_utc(slot.service.start).timestamp()), []).append(sd.staff)
    times = []
    for stamp in sorted(by_start)[:MAX_TIMES]:
        local = datetime.fromtimestamp(stamp, tz)
        times.append({"start": local.strftime("%H:%M"), "label": spoken_time(local), "technicians": by_start[stamp]})
    length = spoken_duration(service.duration_minutes)
    if times:
        note = f"Some schedules couldn't be read for this day ({', '.join(unreadable)}), so nothing is shown for them." if unreadable else ""
        return {**base, "status": "open", "times": times, "partial": bool(unreadable), "unreadable": unreadable, "message": note}
    if unreadable:
        return {**base, "status": "unknown", "times": [], "unreadable": unreadable,
                "message": f"We can't confirm times for the full {length} that day: {', '.join(unreadable)}'s schedule can't be read, so nothing is claimed about it."}
    return {**base, "status": "none", "times": [], "message": f"No openings for the full {length} of {service.booksy_name} on {base['day']}."}
