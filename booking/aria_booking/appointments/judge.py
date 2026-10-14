"""Is this placement really bookable? ONE judgement, used for the dry run before a confirmation and again INSIDE the write transaction at commit.

It rebuilds the day exactly as the calendar page and the voice read driver show it (the generated schedule plus the stored appointments, from the SAME
merge) and applies the SAME availability rules the voice's check_slot uses (``validate_slot``: known working hours, breaks, the whole service plus its
cleanup time, minimum notice, the quarter-hour grid). On top it checks what only a writer must: the service's length and buffers are the registry's (never
the caller's), the technician exists and is eligible by stable id, and the day is one the demo calendar covers. Anything it cannot establish is a refusal;
unknown is never free.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from ..availability import validate_slot
from ..catalog.bookable import BookableService
from ..models import DaySnapshot, ServiceSpec
from .models import CONFIRMED, Placement, Refused, from_epoch
from .overlay import day_bounds


@dataclass(frozen=True)
class Judgement:
    service: BookableService
    staff_id: str
    staff_name: str
    start: datetime  # local
    snapshot: DaySnapshot


class PlacementJudge:
    def __init__(self, dataset, *, tz: ZoneInfo, clock: Callable[[], datetime], min_lead_minutes: int = 60, grid_minutes: int = 15, horizon_days: int = 60):
        self.dataset, self._tz, self._clock = dataset, tz, clock
        self.min_lead, self.grid, self.horizon = min_lead_minutes, grid_minutes, horizon_days

    def now(self) -> datetime:
        return self._clock().astimezone(self._tz)

    def snapshot(self, day, rows) -> DaySnapshot:
        from ..demo.driver import snapshot_from_plan

        return snapshot_from_plan(self.dataset.day_plan(day, rows=rows), self._tz, self.now())

    def require(self, placement: Placement, rows_between) -> Judgement:
        """The judgement, or ``Refused``. ``rows_between`` is a transaction's (or the store's) reader, so commit-time judgement sees the transaction's own state."""
        service = self.dataset.registry.get(placement.service_id)
        if service is None or not service.verified:
            raise Refused("refused", "unknown_service")
        if (placement.duration_minutes, placement.buffer_before_minutes, placement.buffer_after_minutes) != (
            service.duration_minutes, service.buffer_before_minutes, service.buffer_after_minutes,
        ):
            raise Refused("refused", "service_facts_mismatch")  # the length and cleanup time are the registry's; a caller cannot shorten them
        staff = next((s for s in self.dataset.roster if s.staff_id == placement.staff_id), None)
        if staff is None:
            raise Refused("refused", "unknown_staff")
        if not service.eligible(staff.name, staff.staff_id):
            raise Refused("refused", "staff_not_eligible", staff=staff.name)
        start = from_epoch(placement.start_utc).astimezone(self._tz)
        day = start.date()
        if not self.dataset.covers(day):
            raise Refused("unknown", "not_covered")
        if start.minute % self.grid or start.second or start.microsecond:
            raise Refused("refused", "off_grid")
        if day > self.now().date() + timedelta(days=self.horizon):
            raise Refused("refused", "too_far")
        low, high = day_bounds(day, self._tz)
        rows = [r for r in rows_between(low, high, statuses=(CONFIRMED,)) if r.reference != placement.ignore_reference]
        snapshot = self.snapshot(day, rows)
        spec = ServiceSpec(service.booksy_name, service.duration_minutes, service.buffer_before_minutes, service.buffer_after_minutes)
        if snapshot.for_staff(staff.name) is None:  # not on that day's schedule at all (a day off), which is KNOWN, unlike a column that shows no hours
            raise Refused("unavailable", "not_working", snapshot=snapshot, staff=staff.name)
        verdict = validate_slot(snapshot, spec, staff.name, start, now=self.now(), min_lead_minutes=self.min_lead)
        if verdict.unknown:
            raise Refused("unknown", "calendar_unknown", snapshot=snapshot, staff=staff.name, unknown=verdict.unknown)
        if not verdict.ok:
            raise Refused("unavailable", "not_free", snapshot=snapshot, staff=staff.name, reasons=verdict.reasons)
        return Judgement(service, staff.staff_id, staff.name, start, snapshot)

    def validator(self) -> Callable:
        """The store's commit-time validator: judged against the transaction's own rows."""
        return lambda tx, placement: self.require(placement, tx.between)
