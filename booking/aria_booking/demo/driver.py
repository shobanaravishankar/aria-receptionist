"""The demo calendar as a read-only DRIVER: the same ``read_day`` interface the voice tools use for the real calendar, answered from the dataset.

It reads nothing from the network or a browser. It builds exactly what the real reader would derive from the PAGE for the same day (a test proves
the two are equal), so the voice answers and the displayed calendar cannot disagree.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from ..calendar_parser import CalendarParseError
from ..driver import BeforeSaveError, DriverError
from ..models import Appointment, DaySnapshot, Interval, StaffDay
from ..roster import Roster, StaffMember
from . import DEMO_BUSINESS_ID
from .dataset import DayPlan, DemoDataset, NotCovered, StaffPlan


def _at(day: date, minutes: int, tz: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, minutes // 60, minutes % 60, tzinfo=tz)


def _working(plan: StaffPlan, day: date, tz: ZoneInfo) -> Optional[tuple[Interval, ...]]:
    """The header window minus its breaks (the real reader derives the same from the shaded 'non-working' blocks). None when no hours are shown."""
    window = plan.shown_hours
    if window is None:
        return None
    segments, cursor = [], window[0]
    for block in sorted(plan.breaks, key=lambda b: b.start):
        if block.start > cursor:
            segments.append((cursor, block.start))
        cursor = block.end  # breaks never overlap one another
    if cursor < window[1]:
        segments.append((cursor, window[1]))
    return tuple(Interval(_at(day, a, tz), _at(day, b, tz)) for a, b in segments)


def snapshot_from_plan(plan: DayPlan, tz: ZoneInfo, captured_at: datetime) -> DaySnapshot:
    days = []
    for p in plan.plans:
        appointments = tuple(Appointment(p.staff.name, Interval(_at(plan.day, v.start, tz), _at(plan.day, v.end, tz)), "", "", True) for v in p.visits)
        days.append(StaffDay(p.staff.name, _working(p, plan.day, tz), (), appointments, p.staff.staff_id))
    return DaySnapshot(plan.day, tuple(days), captured_at)


class DemoCalendarDriver:
    """Read-only. It cannot create anything: ``create_appointment`` refuses before doing anything, and it has no other way to change the dataset."""

    approvals = frozenset()
    allowed_services = frozenset()
    source = "demo_calendar"

    def __init__(self, dataset: DemoDataset, *, timezone: str = "America/New_York", clock: Optional[Callable[[], datetime]] = None):
        self.dataset = dataset
        self._tz = ZoneInfo(timezone)
        self._clock = clock or (lambda: datetime.now(self._tz))
        self.last_roster: Optional[Roster] = None
        self.last_read_ms: dict = {}
        self.reads = 0

    def verify_business(self) -> str:
        return DEMO_BUSINESS_ID

    def read_day(self, day: date, include_notes: bool = False) -> DaySnapshot:
        if include_notes:
            raise DriverError("the demo calendar has no appointment notes")
        try:
            plan = self.dataset.day_plan(day)
        except NotCovered as exc:
            raise CalendarParseError(f"no demo data for that date ({exc})") from None
        self.reads += 1
        self.last_roster = Roster(tuple(StaffMember(s.staff_id, s.name, any(p.staff.staff_id == s.staff_id for p in plan.plans)) for s in self.dataset.roster))
        return snapshot_from_plan(plan, self._tz, self._clock().astimezone(self._tz))

    def create_appointment(self, spec) -> None:
        raise BeforeSaveError("the demo calendar is read-only: nothing can be created")

    def close(self) -> None:
        return None
