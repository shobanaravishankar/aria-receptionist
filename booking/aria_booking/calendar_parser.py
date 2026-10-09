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
    exactly one column AND the caller has verified (from the Staff page, see staff_census) that the account's
    only staff member is the configured one.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

import re

from .driver import DriverError
from .models import Appointment, DaySnapshot, Interval, StaffDay, to_utc
from .roster import Roster, normalise_name
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
            "res": n.get("res"),
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
            f"the page does not name the staff member of its single column, and the staff check has not confirmed "
            f"that the account's only staff member is {staff!r}"
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


# ======================================================================================================================
# Several staff columns (any number), each bound to a staff member by its own stable id.
#
# Evidence (read-only look at a live multi-staff day view, by Sol): under the calendar grid there are TWO sibling grids. The OVERLAY
# grid has one div._calendarColumn_ per staff member shown, each carrying data-resource="<numeric staff id>"; the appointment cards are
# its descendants. The LAYOUT grid has blank columns with no data-resource and is NOT a staff set. Every data-resource matched the
# staff filter's ids, and each column's header (data-testid="resource" > resource-content > span._name_ / span._hours_) matched the
# filter's name. A header may show no hours. Binding is by ID; names and geometry are only consistency checks.
# ======================================================================================================================

_HOURS_TEXT = re.compile(
    r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*[-\u2013\u2014]\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*$", re.IGNORECASE
)


def parse_hours_text(text: Optional[str]) -> Optional[tuple[tuple[int, int], tuple[int, int]]]:
    """'10AM-8PM' / '10:00 AM - 7:00 PM' -> ((10, 0), (20, 0)). None when absent or not understood: unknown, NEVER 'available'."""
    match = _HOURS_TEXT.match(text or "")
    if not match:
        return None
    h1, m1, a1, h2, m2, a2 = match.groups()

    def clock(hour: str, minute: Optional[str], meridiem: str) -> Optional[tuple[int, int]]:
        h, m = int(hour), int(minute or 0)
        if not (1 <= h <= 12 and 0 <= m <= 59):
            return None
        h = h % 12 + (12 if meridiem.lower() == "p" else 0)
        return h, m

    start, end = clock(h1, m1, a1), clock(h2, m2, a2)
    if start is None or end is None or end <= start:
        return None
    return start, end


def _column_end(nodes: list[Node], index: int) -> int:
    depth = nodes[index]["depth"]
    for j in range(index + 1, len(nodes)):
        if nodes[j]["depth"] <= depth:
            return j
    return len(nodes)


def _header_for(nodes: list[Node], headers: list[int], column: Node) -> Optional[tuple[Optional[str], Optional[str]]]:
    """(name, hours text) of the header sitting over this column, or None if there is none. Raises if the match is ambiguous."""
    cx0, cw = column["box"][0], column["box"][2]
    if cx0 is None or cw is None:
        return None
    found = []
    for hi in headers:
        hx, hw = nodes[hi]["box"][0], nodes[hi]["box"][2]
        if hx is not None and hw is not None and cx0 - 2 <= hx + hw / 2 <= cx0 + cw + 2:
            found.append(hi)
    if not found:
        return None
    if len(found) > 1:
        raise CalendarParseError("more than one staff header sits over one calendar column; refusing to guess")
    sub = _subtree(nodes, found[0])
    name = _first(sub, lambda n: _has_prefix(n, "_name_") and n["text"])
    hours = _first(sub, lambda n: _has_prefix(n, "_hours_") and n["text"])
    return (name["text"] if name else None, hours["text"] if hours else None)


def parse_day_multi(nodes: Iterable[Node], *, day: date, tz: ZoneInfo, roster: Roster, captured_at: datetime) -> DaySnapshot:
    """One StaffDay per calendar column, each bound to the roster by the column's data-resource id.

    Refuses (CalendarParseError) rather than guesses: a truncated capture, a page that is loading or has a form open, the wrong
    day, an incomplete roster, no overlay grid or columns, a column with no/duplicate/non-numeric/unknown staff id, a header count
    that disagrees with the column count, a header whose name disagrees with the roster, or more than one header over a column.
    Per staff: a header with no (or unreadable) hours makes WORKING HOURS unknown; an unrecognised card makes TIME OFF unknown; an
    unreadable appointment makes APPOINTMENTS unknown. Unknown is never free, and one person's unknowns never leak onto another."""
    nodes = list(nodes)
    if any(n.get("tag") == "__truncated__" for n in nodes):
        raise CalendarParseError("the page capture was cut short, so some staff or appointments may be missing")
    if not roster.complete:
        raise CalendarParseError("the staff roster is incomplete (" + ("; ".join(roster.problems) or "empty") + "); staff cannot be identified")
    _require_quiet_page(nodes)
    _require_requested_day(nodes, day)
    axis = _Axis(nodes, day, tz)

    overlay_index = next((i for i, n in enumerate(nodes) if _has_prefix(n, "_calendarGrid--overlay_")), None)
    wrapper = _first(nodes, lambda n: _has_prefix(n, "_calendarWrapper_"))
    if overlay_index is None or wrapper is None:
        raise CalendarParseError("the calendar grid was not found")
    overlay = nodes[overlay_index]
    column_indexes = [
        i for i in range(overlay_index + 1, _column_end(nodes, overlay_index))
        if _has_prefix(nodes[i], "_calendarColumn_") and nodes[i]["depth"] == overlay["depth"] + 1
    ]
    if not column_indexes:
        raise CalendarParseError("no staff columns were found on the calendar")
    grid = axis.interval(wrapper["box"][1], wrapper["box"][3])
    if grid is None:
        raise CalendarParseError("the calendar grid has no readable extent")

    headers = [i for i, n in enumerate(nodes) if n["testid"] == "resource"]
    if headers and len(headers) != len(column_indexes):
        raise CalendarParseError(f"{len(headers)} staff headers but {len(column_indexes)} columns; refusing to match them up")

    staff_days: list[StaffDay] = []
    seen: set[str] = set()
    for ci in column_indexes:
        column = nodes[ci]
        staff_id = column.get("res")
        if not isinstance(staff_id, str) or not staff_id.isdigit():
            raise CalendarParseError("a calendar column has no numeric staff id (data-resource); refusing to attribute it")
        if staff_id in seen:
            raise CalendarParseError(f"staff id {staff_id} appears on two columns; refusing to attribute either")
        seen.add(staff_id)
        member = roster.get(staff_id)
        if member is None:
            raise CalendarParseError(f"a calendar column belongs to staff id {staff_id}, who is not in the roster; refusing to guess")

        header = _header_for(nodes, headers, column) if headers else None
        hours_text: Optional[str] = None
        if header is not None:
            header_name, hours_text = header
            if header_name is not None and normalise_name(header_name).casefold() != member.name.casefold():
                raise CalendarParseError(f"the header over staff id {staff_id} names someone else than the roster does; refusing to guess")

        nonworking: list[Interval] = []
        appointments: list[Appointment] = []
        appointments_known = True
        unknown_cards = 0
        for idx in range(ci + 1, _column_end(nodes, ci)):
            node = nodes[idx]
            if not (node["testid"] or "").startswith(CARD_PREFIX):
                continue
            sub = _subtree(nodes, idx)
            if any(n["testid"] == "non-working-event" for n in sub):
                block = axis.interval(node["box"][1], node["box"][3])
                if block is None:
                    unknown_cards += 1
                else:
                    nonworking.append(block)
            elif "data-appointment-id" in node["attrs"]:
                appointment = _parse_appointment(nodes, idx, axis, day, tz, member.name)
                if appointment is None:
                    appointments_known = False
                else:
                    appointments.append(appointment)
            else:
                unknown_cards += 1  # e.g. time off or blocked time: not understood, so not assumed absent

        window = parse_hours_text(hours_text)
        working: Optional[tuple[Interval, ...]]
        if window is None:
            working = None  # no (readable) hours shown: unknown, never available
        else:
            (sh, sm), (eh, em) = window
            limit = Interval(datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz), datetime(day.year, day.month, day.day, eh, em, tzinfo=tz))
            working = tuple(_intersect(_complement(grid, nonworking), limit))
        staff_days.append(
            StaffDay(
                member.name,
                working,
                None if unknown_cards else (),
                tuple(sorted(appointments, key=lambda a: to_utc(a.interval.start))) if appointments_known else None,
                staff_id,
            )
        )
    return DaySnapshot(day, tuple(staff_days), captured_at)
