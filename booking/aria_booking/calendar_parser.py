"""Pure parser: the Booksy calendar DAY VIEW's page structure -> a DaySnapshot. No browser, no I/O.

Input is the list of structure nodes produced by the discovery script (tag, depth, classes, attribute
NAMES, test id, own text, box). Written against real captured pages (see tests/fixtures).

Principles (unknown is never free):
  * The page must be the day we asked for (the label names the weekday and date; it has no year).
  * The page must be quiet: no loading overlay, no open form, drawer or dialog.
  * Every card in the staff column must be understood. A card that is neither an appointment nor a
    non-working block makes TIME OFF unknown for the day; an appointment whose times cannot be read, or
    whose card text disagrees with its position on the hour axis, makes APPOINTMENTS unknown.
  * Working hours = the visible grid minus the non-working blocks, limited to the day's displayed hours.
  * Which staff member a column belongs to is NOT shown on the page. The reader refuses unless there is
    exactly one column AND the caller states it has verified that this single column is the configured staff.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from .driver import DriverError
from .models import Appointment, DaySnapshot, Interval, StaffDay, to_utc
from .timeparse import day_from_label, hours_range_in_label, parse_clock, parse_hour_label, weekday_in_label

Node = dict[str, Any]

CARD_PREFIX = "calendar-content-calendar-grid-calendar-card-"
TOLERANCE_MINUTES = 5
MAX_AXIS_DRIFT_PX = 2.0


class CalendarParseError(DriverError):
    """The page could not be turned into a trustworthy snapshot. Nothing may be assumed free."""


def normalize_nodes(raw: Iterable[dict[str, Any]]) -> list[Node]:
    """Live discovery output (x, y, w, h) -> the stored report shape (box). Text is kept unredacted."""
    return [
        {
            "tag": n.get("tag"), "depth": n.get("depth"), "cls": list(n.get("cls") or []),
            "attrs": list(n.get("attrs") or []), "role": n.get("role"), "testid": n.get("testid"),
            "aria": n.get("aria"), "text": n.get("text"), "box": [n.get("x"), n.get("y"), n.get("w"), n.get("h")],
        }
        for n in raw
    ]


# ---------------------------------------------------------------- small helpers
def _has_prefix(node: Node, prefix: str) -> bool:
    return any(c.startswith(prefix) for c in node["cls"])


def _subtree(nodes: list[Node], index: int) -> list[Node]:
    depth = nodes[index]["depth"]
    out = []
    for node in nodes[index + 1:]:
        if node["depth"] <= depth:
            break
        out.append(node)
    return out


def _first(nodes: Iterable[Node], predicate) -> Optional[Node]:
    return next((n for n in nodes if predicate(n)), None)


# ---------------------------------------------------------------- page-level checks
def _require_quiet_page(nodes: list[Node]) -> None:
    testids = {n["testid"] for n in nodes if n["testid"]}
    if "app-loader" in testids:
        raise CalendarParseError("the calendar is still loading")
    if testids & {"drawer-appointment-body", "discard-modal"}:
        raise CalendarParseError("a form, drawer or dialog is open over the calendar; close it first")
    if any("_isEdited_" in " ".join(n["cls"]) for n in nodes):
        raise CalendarParseError("a calendar card is in an editing state (a form is open)")


def _require_requested_day(nodes: list[Node], day: date) -> tuple[Optional[Interval], str]:
    """Verify the page shows `day`. Returns the day's displayed hours (as clock tuples) if present."""
    index = next((i for i, n in enumerate(nodes) if n["testid"] == "date-switcher-label"), None)
    if index is None:
        raise CalendarParseError("the date label was not found, so the day shown cannot be verified")
    texts = [n["text"] for n in _subtree(nodes, index) if n["text"]]
    label = " ".join(texts)
    shown = day_from_label(label, day)
    weekday = weekday_in_label(label)
    if shown is None or weekday is None:
        raise CalendarParseError(f"cannot read the date label {label!r}")
    if shown != day or weekday != day.weekday():
        raise CalendarParseError(f"the page shows {label!r}, which is not {day.isoformat()}")
    hours = next((hours_range_in_label(t) for t in texts if hours_range_in_label(t)), None)
    return hours, label


# ---------------------------------------------------------------- the hour axis
class _Axis:
    """Linear map between a vertical pixel position and the time of day, read from the hour labels."""

    def __init__(self, nodes: list[Node], day: date, tz: ZoneInfo):
        labels = []
        for n in nodes:
            if _has_prefix(n, "_hourText_") and n["text"] and n["box"][1] is not None:
                hour = parse_hour_label(n["text"])
                if hour is not None:
                    labels.append((hour, n["box"][1] + n["box"][3] / 2))
        labels.sort()
        if len(labels) < 3:
            raise CalendarParseError("the hour axis was not found (need at least three hour labels)")
        steps = [(labels[i + 1][1] - labels[i][1]) / (labels[i + 1][0] - labels[i][0]) for i in range(len(labels) - 1)]
        self.px_per_hour = median(steps)
        if self.px_per_hour <= 0 or max(abs(s - self.px_per_hour) for s in steps) > MAX_AXIS_DRIFT_PX:
            raise CalendarParseError("the hour labels are not evenly spaced; refusing to read times from positions")
        self.y_of_hour_zero = labels[0][1] - labels[0][0] * self.px_per_hour
        self._midnight = datetime.combine(day, time(0, 0), tzinfo=tz)

    def minutes_at(self, y: float) -> int:
        raw = (y - self.y_of_hour_zero) / self.px_per_hour * 60
        return int(round(raw / TOLERANCE_MINUTES) * TOLERANCE_MINUTES)

    def at(self, minutes: int) -> datetime:
        return self._midnight + timedelta(minutes=minutes)

    def interval(self, y: float, height: float) -> Optional[Interval]:
        start, end = self.minutes_at(y), self.minutes_at(y + height)
        return Interval(self.at(start), self.at(end)) if end > start else None


# ---------------------------------------------------------------- cards
def _parse_appointment(nodes: list[Node], index: int, axis: _Axis, day: date, tz: ZoneInfo, staff: str) -> Optional[Appointment]:
    """None means the card could not be read with confidence (appointments become unknown)."""
    card = nodes[index]
    sub = _subtree(nodes, index)
    from_node = _first(sub, lambda n: "hour_from" in n["cls"])
    till_node = _first(sub, lambda n: "hour_till" in n["cls"])
    if not from_node or not till_node:
        return None
    start, end = parse_clock(from_node["text"] or ""), parse_clock(till_node["text"] or "")
    if start is None or end is None:
        return None
    start_dt = datetime(day.year, day.month, day.day, start[0], start[1], tzinfo=tz)
    end_dt = datetime(day.year, day.month, day.day, end[0], end[1], tzinfo=tz)
    if to_utc(end_dt) <= to_utc(start_dt):
        return None
    drawn = axis.interval(card["box"][1], card["box"][3])
    if drawn is None:
        return None
    tolerance = timedelta(minutes=TOLERANCE_MINUTES)
    if abs(to_utc(drawn.start) - to_utc(start_dt)) > tolerance or abs(to_utc(drawn.end) - to_utc(end_dt)) > tolerance:
        return None  # the card's text and its position on the grid disagree: do not trust either
    details = _first(sub, lambda n: _has_prefix(n, "_details_") and n["text"])
    return Appointment(staff, Interval(start_dt, end_dt), (details["text"] if details else "").strip(), "", True)


def _complement(grid: Interval, blocks: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    cursor = grid.start
    for block in sorted(blocks, key=lambda b: to_utc(b.start)):
        start = block.start if to_utc(block.start) > to_utc(grid.start) else grid.start
        end = block.end if to_utc(block.end) < to_utc(grid.end) else grid.end
        if to_utc(end) <= to_utc(start):
            continue
        if to_utc(start) > to_utc(cursor):
            out.append(Interval(cursor, start))
        if to_utc(end) > to_utc(cursor):
            cursor = end
    if to_utc(cursor) < to_utc(grid.end):
        out.append(Interval(cursor, grid.end))
    return out


def _intersect(segments: list[Interval], limit: Interval) -> list[Interval]:
    out = []
    for seg in segments:
        start = seg.start if to_utc(seg.start) > to_utc(limit.start) else limit.start
        end = seg.end if to_utc(seg.end) < to_utc(limit.end) else limit.end
        if to_utc(end) > to_utc(start):
            out.append(Interval(start, end))
    return out


# ---------------------------------------------------------------- the parser
def parse_day(
    nodes: Iterable[Node],
    *,
    day: date,
    tz: ZoneInfo,
    staff: str,
    staff_confirmed: bool,
    captured_at: datetime,
) -> DaySnapshot:
    nodes = list(nodes)
    _require_quiet_page(nodes)
    displayed_hours, _label = _require_requested_day(nodes, day)
    axis = _Axis(nodes, day, tz)

    overlay_index = next((i for i, n in enumerate(nodes) if _has_prefix(n, "_calendarGrid--overlay_")), None)
    wrapper = _first(nodes, lambda n: _has_prefix(n, "_calendarWrapper_"))
    if overlay_index is None or wrapper is None:
        raise CalendarParseError("the calendar grid was not found")
    overlay = nodes[overlay_index]
    columns = [n for n in _subtree(nodes, overlay_index) if _has_prefix(n, "_calendarColumn_") and n["depth"] == overlay["depth"] + 1]
    if len(columns) != 1:
        raise CalendarParseError(f"the calendar shows {len(columns)} staff columns; refusing to guess which one is {staff!r}")
    if not staff_confirmed:
        raise CalendarParseError(
            f"the page does not name the staff member of its single column; set ARIA_CONFIRM_SINGLE_STAFF=1 "
            f"only after verifying that column is {staff!r}"
        )

    grid = axis.interval(wrapper["box"][1], wrapper["box"][3])
    if grid is None:
        raise CalendarParseError("the calendar grid has no readable extent")

    nonworking: list[Interval] = []
    appointments: list[Appointment] = []
    appointments_known = True
    unknown_cards: list[str] = []
    for index, node in enumerate(nodes):
        if not (node["testid"] or "").startswith(CARD_PREFIX):
            continue
        sub = _subtree(nodes, index)
        if any(n["testid"] == "non-working-event" for n in sub):
            block = axis.interval(node["box"][1], node["box"][3])
            if block is None:
                unknown_cards.append(node["testid"])
            else:
                nonworking.append(block)
        elif "data-appointment-id" in node["attrs"]:
            appointment = _parse_appointment(nodes, index, axis, day, tz, staff)
            if appointment is None:
                appointments_known = False
            else:
                appointments.append(appointment)
        else:
            unknown_cards.append(node["testid"])  # e.g. time off / blocked time: not understood, so not assumed absent

    working = _complement(grid, nonworking)
    if displayed_hours:
        (sh, sm), (eh, em) = displayed_hours
        working = _intersect(
            working, Interval(datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz), datetime(day.year, day.month, day.day, eh, em, tzinfo=tz))
        )

    staff_day = StaffDay(
        staff,
        tuple(working),
        None if unknown_cards else (),
        tuple(sorted(appointments, key=lambda a: to_utc(a.interval.start))) if appointments_known else None,
    )
    return DaySnapshot(day, (staff_day,), captured_at)
