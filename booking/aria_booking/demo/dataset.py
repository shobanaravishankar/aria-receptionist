"""The one demo dataset: staff, services, eligibility and a deterministic multi-day schedule. Invented; no real customer data.

* Staff names are labels observed on the real calendar (reference only); ids are SYNTHETIC. The hidden internal test account that appears in the real
  filter is deliberately NOT included (it would be confused with the voice agent's own name). One person, like the real owner column, shows NO
  working hours, so her availability is UNKNOWN.
* Schedules are generated per (date, staff) from a fixed seed, so any run produces the same calendar for the same demo date. The window the dataset
  covers is explicit; any other date raises NotCovered and the voice answers "can't confirm", never "free".
* Two SCRIPTED days (tomorrow and the day after the demo date) pin the interesting cases so a demo is reliable: an available slot, an occupied slot, an
  off-duty person, a visit that would cross into the next appointment, a buffer clash, a break, and a person whose hours are unknown.
* Clients are fictional first names with a surname initial, chosen from fixed lists.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Iterable, Optional

from ..catalog.bookable import BookableRegistry, BookableService
from ..showcase.catalog import SHOWCASE_CATALOG
from ..showcase.roster import ROSTER_ROWS

GRID_START = 9 * 60  # the day view draws 9:00 AM ...
GRID_END = 21 * 60  # ... to 9:00 PM, 80 px per hour (as in the real capture)
COVERAGE_BEFORE_DAYS = 7
COVERAGE_AFTER_DAYS = 35
SEED = "aria-demo-v1"


class NotCovered(Exception):
    """The demo calendar has no data for that date. Nothing is known, so nothing may be offered."""


@dataclass(frozen=True)
class DemoStaff:
    staff_id: str
    name: str
    weekdays: frozenset  # Monday = 0
    hours: tuple[int, int]  # minutes since midnight
    categories: frozenset
    hours_known: bool = True  # False: her column shows no hours, so her availability is unknown (like the real owner column)


def _days(*numbers: int) -> frozenset:
    return frozenset(numbers)


ROSTER = tuple(
    DemoStaff(row.staff_id, row.name, _days(*row.weekdays), row.hours, frozenset(row.categories), hours_known=row.hours_known) for row in ROSTER_ROWS
)

# (category, family, buffer after in minutes): every duration variant of these website services is offered; all are DEMO mappings, not verified.
DEMO_FAMILIES = (
    ("massage", "Deep Tissue Massage", 15), ("massage", "Swedish Massage", 15), ("massage", "Hot Stone Massage", 15),
    ("massage", "Aromatherapy Massage", 15), ("massage", "Anti-Stress Massage", 15), ("massage", "Reflexology Massage", 15),
    ("facial", "Express Facial", 10), ("facial", "Signature Facial", 10), ("facial", "Back Facial", 10), ("facial", "Teen Facial", 10),
    ("facial", "Deep Hydration Facial", 10), ("facial", "Gentleman Facial", 10), ("facial", "Microdermabrasion Facial", 10),
    ("lash", "Eyelash Tinting", 0), ("lash", "Eyelash Lifting", 0), ("lash", "Eyebrow Tinting", 0),
    ("waxing", "Eyebrows", 0), ("waxing", "Lip", 0), ("waxing", "Full Face", 0), ("waxing", "Full Leg", 5), ("waxing", "Brazilian Line", 5),
    ("head-spa", "Head Scalp Massage", 5), ("head-spa", "Head Scalp Detoxification", 5),
)

FIRST_NAMES = (
    "Alex", "Blair", "Casey", "Dana", "Eden", "Frankie", "Gray", "Harper", "Indy", "Jules", "Kai", "Lane", "Morgan", "Nico", "Oakley", "Parker",
    "Quinn", "Reese", "Sage", "Taylor", "Uma", "Val", "Wren", "Xan", "Yael", "Zion", "Avery", "Briar", "Cleo", "Drew", "Ellis", "Finley",
)
INITIALS = "ABCDEFGHJKLMNPRSTW"


@dataclass(frozen=True)
class Visit:
    start: int
    end: int
    service: str
    category: str
    client: str


@dataclass(frozen=True)
class Break:
    start: int
    end: int
    label: str = "Break"


@dataclass(frozen=True)
class StaffPlan:
    staff: DemoStaff
    hours: Optional[tuple[int, int]]  # None: not shown today; for an unknown-hours person the nominal window is not displayed
    breaks: tuple[Break, ...] = ()
    visits: tuple[Visit, ...] = ()

    @property
    def shown_hours(self) -> Optional[tuple[int, int]]:
        """What the column header displays: None for a person whose hours the calendar does not show."""
        return self.hours if self.staff.hours_known else None


@dataclass(frozen=True)
class DayPlan:
    day: date
    plans: tuple[StaffPlan, ...]  # the staff SHOWN that day (the page's "working staff" filter), in roster order


def demo_services(roster: Iterable[DemoStaff] = ROSTER) -> list[tuple[BookableService, str]]:
    """(service, category) for every duration variant of the demo's website services. Eligibility is the synthetic category assignment above."""
    staff = tuple(roster)
    out = []
    for category, family, buffer_after in DEMO_FAMILIES:
        for item in SHOWCASE_CATALOG.items:
            if item.category == category and item.family == family and item.duration_minutes:
                ids = frozenset(s.staff_id for s in staff if category in s.categories)
                out.append((BookableService(
                    item.item_id, item.name, item.duration_minutes, buffer_after_minutes=buffer_after, eligible_staff_ids=ids,
                    verified=True, catalog_item=item.item_id, price_usd=item.price_usd,
                ), category))
    return out


def demo_registry(roster: Iterable[DemoStaff] = ROSTER) -> BookableRegistry:
    """The services the demo can check, from the reviewed website knowledge. ``verified`` here means 'part of the DEMO mapping', nothing more."""
    return BookableRegistry([service for service, _category in demo_services(roster)], catalog=SHOWCASE_CATALOG)


def _rng(*parts: object) -> random.Random:
    return random.Random(int.from_bytes(hashlib.sha256("|".join(str(p) for p in (SEED, *parts)).encode()).digest()[:8], "big"))


def _client(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(INITIALS)}."


class DemoDataset:
    def __init__(self, today: date, *, extra_staff: Iterable[DemoStaff] = ()):
        self.today = today
        self.roster: tuple[DemoStaff, ...] = ROSTER + tuple(extra_staff)
        pairs = demo_services(self.roster)
        self._registry = BookableRegistry([service for service, _category in pairs], catalog=SHOWCASE_CATALOG)
        self._services: dict[str, list[BookableService]] = {}
        for service, category in pairs:
            self._services.setdefault(category, []).append(service)
        self._category = {service.service_id: category for service, category in pairs}

    # ---- what is covered -------------------------------------------------------------------------
    @property
    def first_day(self) -> date:
        return self.today - timedelta(days=COVERAGE_BEFORE_DAYS)

    @property
    def last_day(self) -> date:
        return self.today + timedelta(days=COVERAGE_AFTER_DAYS)

    def covers(self, day: date) -> bool:
        return self.first_day <= day <= self.last_day

    @property
    def registry(self) -> BookableRegistry:
        return self._registry

    def providers(self, service: BookableService) -> list[str]:
        """Who does this service in the DEMO: the roster members whose synthetic category assignment covers it, in roster order. Demo-only facts, not the salon's."""
        return [staff.name for staff in self.roster if staff.staff_id in service.eligible_staff_ids]

    @property
    def scripted_days(self) -> tuple[date, date]:
        return self.today + timedelta(days=1), self.today + timedelta(days=2)

    # ---- the schedule -------------------------------------------------------------------------------
    def day_plan(self, day: date) -> DayPlan:
        if not self.covers(day):
            raise NotCovered(f"the demo calendar covers {self.first_day.isoformat()} to {self.last_day.isoformat()} only")
        plans = []
        for staff in self.roster:
            entry = self._script_entry(day, staff)
            if entry == "off":
                continue
            plan = self._scripted(day, staff, entry) if entry is not None else self._generated(day, staff)
            if plan is not None:
                plans.append(plan)
        return DayPlan(day, tuple(plans))

    def _generated(self, day: date, staff: DemoStaff) -> Optional[StaffPlan]:
        rng = _rng(day.isoformat(), staff.staff_id)
        if day.weekday() not in staff.weekdays or rng.random() < 0.07:  # a rostered day off, or an occasional day away
            return None
        start, end = staff.hours
        breaks: list[Break] = []
        if rng.random() < 0.5:
            at = rng.choice((750, 780, 810, 840))
            breaks.append(Break(at, at + rng.choice((30, 60)), "Lunch"))
        visits: list[Visit] = []
        if staff.hours_known:
            utilisation = 0.7 if day.weekday() >= 5 else 0.5
            # canonical order (categories sorted, services in the registry's fixed order): iterating the frozenset would reorder with Python's per-process hash seed
            pool = [s for c in sorted(staff.categories) for s in self._services.get(c, [])]
            cursor = start
            while pool and cursor + 30 <= end:
                blocked = next((b for b in breaks if b.start <= cursor < b.end), None)
                if blocked:
                    cursor = blocked.end
                    continue
                if rng.random() < utilisation:
                    long = [x for x in pool if x.duration_minutes >= 45]
                    service = rng.choice(long if long and rng.random() < 0.7 else pool)  # mostly full treatments, some quick add-ons
                    finish = cursor + service.duration_minutes
                    if finish <= end and not any(cursor < b.end and b.start < finish for b in breaks):
                        visits.append(Visit(cursor, finish, service.booksy_name, self._category[service.service_id], _client(rng)))
                        cursor = -(-(finish + rng.choice((0, 0, 15, 30))) // 15) * 15  # the next appointment starts on a quarter hour
                        continue
                cursor += rng.choice((15, 30, 45, 60))
        return StaffPlan(staff, (start, end), tuple(breaks), tuple(visits))

    # ---- the two scripted days ----------------------------------------------------------------------
    def _script_entry(self, day: date, staff: DemoStaff):
        first, second = self.scripted_days
        if day not in (first, second):
            return None
        return (_SCRIPT_FIRST if day == first else _SCRIPT_SECOND).get(staff.staff_id)  # None: this person follows the generated schedule that day

    def _scripted(self, day: date, staff: DemoStaff, entry) -> StaffPlan:
        hours, breaks, visits = entry
        rng = _rng(day.isoformat(), staff.staff_id, "scripted")
        built = tuple(Visit(s, e, label, category, _client(rng)) for s, e, label, category in visits)
        return StaffPlan(staff, hours, tuple(Break(s, e, label) for s, e, label in breaks), built if staff.hours_known else ())

    def describe_day(self, day: date) -> list[str]:
        """Human-readable summary for the operator: who works, and what is booked. Fictional data only."""
        plan = self.day_plan(day)
        lines = [f"{day.isoformat()} ({day.strftime('%A')})"]
        for p in plan.plans:
            hours = f"{_hm(p.hours[0])}-{_hm(p.hours[1])}" if p.shown_hours else "hours not shown"
            busy = ("booked " + ", ".join(f"{_hm(v.start)}-{_hm(v.end)}" for v in p.visits)) if p.visits else "nothing booked"
            off = ", ".join(f"{_hm(b.start)}-{_hm(b.end)} {b.label}" for b in p.breaks)
            lines.append(f"  {p.staff.name}: {hours}; {busy}" + (f"; {off}" if off else ""))
        shown = {p.staff.name for p in plan.plans}
        lines.append("  not working: " + (", ".join(s.name for s in self.roster if s.name not in shown) or "nobody"))
        return lines


# Scripted days. Each entry: (hours, breaks, visits) or "off". Visits are (start, end, service label, category), minutes since midnight.
# Keyed by staff ID (names are display only).  FIRST day (tomorrow):  910006 (Sienna) free 3-8 PM (available at 4);  910001 (Marisol) booked 3:45-5:15
# (occupied at 4);  910002 (Tamsin) free until a 5:00 PM visit (a 60-minute massage at 4:00 fits the gap only without its 15-minute buffer, a 90-minute one
# crosses the visit);  910007 (Rowan) on a 3-5 PM break;  910008 (Imogen) is off;  910010 (Delphine): hours not shown (unknown).
_SCRIPT_FIRST: dict = {
    "910006": ((600, 1200), (), [(690, 750, "Swedish Massage, 1 hour", "massage")]),
    "910001": ((600, 1200), (), [(600, 660, "Hot Stone Massage, 1 hour", "massage"), (945, 1035, "Deep Tissue Massage, 1 hour 30 minutes", "massage")]),
    "910002": ((600, 1200), (), [(660, 720, "Signature Facial", "facial"), (1020, 1080, "Back Facial", "facial")]),
    "910007": ((600, 1200), ((900, 1020, "Break"),), [(600, 615, "Eyebrows", "waxing")]),
    "910008": "off",
    "910010": ((600, 1200), (), []),
}
# SECOND day: a different mix so a follow-up question has other answers.
_SCRIPT_SECOND: dict = {
    "910006": ((600, 1200), (), [(600, 690, "Deep Tissue Massage, 1 hour 30 minutes", "massage"), (900, 960, "Aromatherapy Massage, 1 hour", "massage")]),
    "910001": ((600, 1200), ((780, 840, "Lunch"),), [(870, 960, "Swedish Massage, 1 hour 30 minutes", "massage")]),
    "910008": ((600, 1200), (), [(1020, 1065, "Eyelash Lifting", "lash")]),
}


def _hm(minutes: int) -> str:
    hour, minute = divmod(minutes, 60)
    return f"{(hour % 12) or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


hm = _hm
