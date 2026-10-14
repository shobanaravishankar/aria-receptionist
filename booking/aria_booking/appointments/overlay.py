"""Stored appointments drawn onto the demo calendar: the ONE place the store and the generated schedule meet.

``DemoDataset.day_plan`` merges through ``merge_rows``, and both the calendar PAGE and the voice tools' read driver call ``day_plan``, so a booking, a
cancellation or a move is on the page and in every later voice answer by construction, with no second copy to keep in step.

Rules:
  * A stored (confirmed) appointment is authoritative. A generated filler visit that overlaps one is dropped, so the page never shows two people in one
    chair, and the stored appointment's time is blocked exactly once.
  * Cancelled, completed and no-show rows are not drawn and block nothing: a cancelled slot is free again immediately.
  * A stored appointment's cleanup time (the registry's buffers, persisted with it) is part of the time it occupies. It is drawn as a shaded "Cleanup" block next to the card,
    the same way a break is, so every reader that already treats breaks as time nobody can book (the page reader, the driver, ``check_slot``, the commit judge) refuses it
    without any change to the live reader's meaning of an appointment. The card itself, and every spoken length, stay the service time only.
  * If a technician has a stored appointment on a day she would otherwise not appear (her schedule moved after it was booked), she is drawn with her
    appointment but with NO hours shown. Her availability then reads as unknown, so nothing NEW can be booked with her that day, while the existing
    appointment stays visible and keeps blocking.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Protocol, Sequence
from zoneinfo import ZoneInfo

from .models import CONFIRMED, StoredAppointment, to_epoch


class RowSource(Protocol):
    def rows_for_day(self, day: date) -> Sequence[StoredAppointment]:
        """The committed, confirmed appointments whose service time falls on this (local) day."""


def display_name(full: str) -> str:
    """How the calendar card shows a client: 'Willa Hartwell' becomes 'Willa H.', a single name stays as it is."""
    parts = full.split()
    if len(parts) <= 1:
        return full.strip()
    return f"{parts[0]} {parts[-1][0].upper()}."


def day_bounds(day: date, tz: ZoneInfo) -> tuple[int, int]:
    """[start, end) of a local calendar day as epoch seconds (a day is 23 or 25 hours across a clock change; the wall clock decides, not 24 x 3600)."""
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    following = day + timedelta(days=1)
    return to_epoch(start), to_epoch(datetime(following.year, following.month, following.day, tzinfo=tz))


class StoreOverlay:
    """Reads the committed state of the store for one day. It never writes."""

    def __init__(self, store, tz: ZoneInfo):
        self._store, self._tz = store, tz

    def rows_for_day(self, day: date) -> list[StoredAppointment]:
        start, end = day_bounds(day, self._tz)
        return self._store.between(start, end, statuses=(CONFIRMED,))

    def apply(self, plan, roster: Sequence, rows: Optional[Iterable[StoredAppointment]] = None):
        """``plan`` with the committed appointments (or the given ``rows``) drawn in."""
        return merge_rows(plan, self.rows_for_day(plan.day) if rows is None else rows, self._tz, roster)


def _union(breaks) -> tuple:
    """Breaks with overlapping ones joined (the readers assume breaks never overlap one another). Touching breaks stay separate; a join of different labels is a plain Break."""
    from ..demo.dataset import Break

    merged: list = []
    for block in sorted(breaks, key=lambda b: (b.start, b.end)):
        if merged and block.start < merged[-1].end:
            last = merged[-1]
            merged[-1] = Break(last.start, max(last.end, block.end), last.label if last.label == block.label else "Break")
        else:
            merged.append(block)
    return tuple(merged)


def merge_rows(plan, rows: Iterable[StoredAppointment], tz: ZoneInfo, roster: Sequence):
    """``plan`` (a DayPlan) with the stored appointments drawn in. ``roster`` is the dataset's staff in display order."""
    from ..demo.dataset import Break, DayPlan, StaffPlan, Visit  # imported here: the dataset imports this package's overlay protocol, not the reverse

    rows = [r for r in rows if r.status == CONFIRMED]
    if not rows:
        return plan
    by_staff: dict[str, list[StoredAppointment]] = {}
    for row in rows:
        by_staff.setdefault(row.staff_id, []).append(row)
    known = {s.staff_id: s for s in roster}
    plans = {p.staff.staff_id: p for p in plan.plans}
    for staff_id, mine in by_staff.items():
        staff = known.get(staff_id)
        if staff is None:
            continue  # a technician who is no longer on the roster cannot be drawn; the store still holds the row
        base = plans.get(staff_id) or StaffPlan(staff, None, (), ())  # no hours shown: see the module note
        stored, cleanup = [], []
        for row in mine:
            local = row.start.astimezone(tz)
            first = local.hour * 60 + local.minute
            last = first + row.duration_minutes
            stored.append(Visit(first, last, row.service_name, row.category, display_name(row.client_name), row.reference))
            if row.buffer_before_minutes:
                cleanup.append(Break(max(0, first - row.buffer_before_minutes), first, "Cleanup"))
            if row.buffer_after_minutes:
                cleanup.append(Break(last, min(24 * 60, last + row.buffer_after_minutes), "Cleanup"))
        kept = [v for v in base.visits if not any(v.start < s.end and s.start < v.end for s in stored)]
        plans[staff_id] = replace(base, visits=tuple(sorted(kept + stored, key=lambda v: (v.start, v.end))), breaks=_union(tuple(base.breaks) + tuple(cleanup)))
    order = {s.staff_id: i for i, s in enumerate(roster)}
    return DayPlan(plan.day, tuple(sorted(plans.values(), key=lambda p: order.get(p.staff.staff_id, len(order)))))
