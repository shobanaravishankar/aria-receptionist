"""Synthetic multi-staff calendar pages, built from REAL captured structure so nothing about the page chrome is invented.

The base is the real, redacted capture of an empty Monday (tests/fixtures/empty_day_mon_12_oct.json); an appointment card is cloned
from the real busy-day capture. What is added follows the structure a read-only look at a live multi-staff day view described:
  overlay grid -> one div._calendarColumn_ per staff member with data-resource="<staff id>" (cards are its descendants);
  a SECOND (layout) grid whose blank columns carry no staff id; header nodes [data-testid=resource] > resource-content > span._name_ /
  span._hours_ over each column; the staff filter lists every staff member as filtersValue_<id>-input / filtersValue_<id> pairs.
Staff names and ids here are made up. Nothing in this module comes from any real account.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Optional

from aria_booking.roster import Roster, parse_roster

FIXTURES = Path(__file__).parent / "fixtures"
COLUMN_WIDTH = 250
GRID_X = 347


def _load(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["nodes"]


BASE = _load("empty_day_mon_12_oct.json")
BUSY = _load("busy_day_thu_8_oct.json")


def y_of(minute: int) -> int:
    """Vertical pixel of a minute of the day on the Monday base page (9:00 is the top of the grid, 80 px per hour)."""
    return round(75 + (minute - 540) / 60 * 80)


def _index(nodes, predicate, start=0):
    return next(i for i in range(start, len(nodes)) if predicate(nodes[i]))


def _grid_range(nodes):
    g0 = _index(nodes, lambda n: n["testid"] == "calendar-grid-day")
    g1 = _index(nodes, lambda n: n["depth"] <= nodes[g0]["depth"], g0 + 1)
    return g0, g1


def _shift(node: dict, dx: int = 0, box: Optional[list] = None) -> dict:
    node = copy.deepcopy(node)
    if box is not None:
        node["box"] = box
    elif dx:
        node["box"][0] += dx
    return node


def _hm(minute: int) -> str:
    h, m = divmod(minute, 60)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def _appointment_nodes(x: int, start: int, end: int, service: str) -> list[dict]:
    """The real card structure (busy-day capture), repositioned and re-timed."""
    i0 = _index(BUSY, lambda n: "data-appointment-id" in n["attrs"])
    block = copy.deepcopy(BUSY[i0:_index(BUSY, lambda n: n["depth"] <= BUSY[i0]["depth"], i0 + 1)])
    top, height = y_of(start), y_of(end) - y_of(start)
    dx, dy = x + 11 - block[0]["box"][0], top - block[0]["box"][1]
    for node in block:
        if node["depth"] <= block[0]["depth"] + 1:  # the card wrapper and its inner card span the whole slot
            node["box"] = [x + 11, top, 228, height]
        else:  # everything inside keeps its size and moves with the card
            node["box"][0] += dx
            node["box"][1] += dy
        if "hour_from" in node["cls"]:
            node["text"] = _hm(start)
        elif "hour_till" in node["cls"]:
            node["text"] = _hm(end)
        elif any(c.startswith("_details_") for c in node["cls"]):
            node["text"] = service
    return block


def _nonworking_nodes(x: int, start: int, end: int, ordinal: int) -> list[dict]:
    i0 = _index(BASE, lambda n: n["testid"] == "non-working-event")
    wrap, inner = copy.deepcopy(BASE[i0 - 1]), copy.deepcopy(BASE[i0])
    top, height = y_of(start), y_of(end) - y_of(start)
    wrap["box"], inner["box"] = [x + 11, top, 228, height], [x + 11, top, 228, height]
    return [wrap, inner]


def build_page(
    staff: list[dict],
    *,
    headers: bool = True,
    truncated: bool = False,
    layout_columns: Optional[int] = None,
    header_count: Optional[int] = None,
) -> list[dict]:
    """Nodes (stored shape) for a day view with one column per entry of ``staff``, in that order.

    Each entry: id (str), name, hours (header text or None for 'no hours shown'), nonworking [(start_min, end_min)], appointments
    [(start_min, end_min, service)], cards_extra (int unrecognised cards), header_name (override, to test mismatches), res (override)."""
    g0, g1 = _grid_range(BASE)
    prefix, suffix = copy.deepcopy(BASE[:g0]), copy.deepcopy(BASE[g1:])
    grid, wrapper, overlay = (copy.deepcopy(BASE[g0 + k]) for k in range(3))
    width = COLUMN_WIDTH * max(len(staff), 1)
    for node in (wrapper, overlay):
        node["box"][2] = width
    column_template = copy.deepcopy(BASE[g0 + 3])

    body: list[dict] = [grid, wrapper, overlay]
    for i, spec in enumerate(staff):
        x = GRID_X + COLUMN_WIDTH * i
        column = copy.deepcopy(column_template)
        column["box"] = [x, 75, COLUMN_WIDTH, 880]
        column["res"] = spec.get("res", spec["id"])
        body.append(column)
        ordinal = 0
        for start, end in spec.get("nonworking", []):
            nodes = _nonworking_nodes(x, start, end, ordinal)
            nodes[0]["testid"] = f"calendar-content-calendar-grid-calendar-card-{i}-{ordinal}"
            body += nodes
            ordinal += 1
        for start, end, service in spec.get("appointments", []):
            nodes = _appointment_nodes(x, start, end, service)
            nodes[0]["testid"] = f"calendar-content-calendar-grid-calendar-card-{i}-{ordinal}"
            body += nodes
            ordinal += 1
        for _ in range(spec.get("cards_extra", 0)):  # a card that is neither an appointment nor a non-working block
            odd = copy.deepcopy(BASE[_index(BASE, lambda n: n["testid"] == "non-working-event") - 1])
            odd["box"] = [x + 11, y_of(720), 228, 80]
            odd["testid"] = f"calendar-content-calendar-grid-calendar-card-{i}-{ordinal}"
            body.append(odd)
            ordinal += 1

    # the layout grid: blank columns, NO staff id; must never be read as a staff set
    layout_template = [copy.deepcopy(n) for n in BASE[_index(BASE, lambda n: "_calendarGrid--layout_" in " ".join(n["cls"])):g1]]
    layout_grid = layout_template[0]
    layout_grid["box"][2] = width
    body.append(layout_grid)
    for i in range(layout_columns if layout_columns is not None else len(staff)):
        for node in layout_template[1:]:
            body.append(_shift(node, dx=COLUMN_WIDTH * i))

    tail: list[dict] = []
    if headers:
        depth = suffix[0]["depth"] + 1
        specs = staff if header_count is None else (staff + staff)[:header_count]
        for i, spec in enumerate(specs):
            x = GRID_X + COLUMN_WIDTH * i
            tail += [
                {"tag": "div", "depth": depth, "cls": ["_resource_x"], "attrs": ["data-testid"], "role": None, "testid": "resource", "aria": None, "text": None, "box": [x, 75, COLUMN_WIDTH, 64], "res": None},
                {"tag": "div", "depth": depth + 1, "cls": ["_avatar_x"], "attrs": [], "role": None, "testid": None, "aria": None, "text": "".join(w[0] for w in spec["name"].split())[:2].upper(), "box": [x + 8, 83, 40, 40], "res": None},
                {"tag": "div", "depth": depth + 1, "cls": ["flex-1"], "attrs": ["data-testid"], "role": None, "testid": "resource-content", "aria": None, "text": None, "box": [x + 56, 80, 190, 50], "res": None},
                {"tag": "div", "depth": depth + 2, "cls": ["flex-1"], "attrs": [], "role": None, "testid": None, "aria": None, "text": None, "box": [x + 56, 80, 190, 50], "res": None},
                {"tag": "span", "depth": depth + 3, "cls": ["_name_10ie7_43", "w-full"], "attrs": [], "role": None, "testid": None, "aria": None, "text": spec.get("header_name", spec["name"]), "box": [x + 56, 84, 150, 20], "res": None},
            ]
            if spec.get("hours"):
                tail.append({"tag": "span", "depth": depth + 3, "cls": ["_hours_10ie7_48"], "attrs": [], "role": None, "testid": None, "aria": None, "text": spec["hours"], "box": [x + 56, 106, 150, 18], "res": None})
    nodes = prefix + body + suffix + tail
    if truncated:
        nodes.append({"tag": "__truncated__", "depth": 0, "cls": [], "attrs": [], "role": None, "testid": None, "aria": None, "text": None, "box": [0, 0, 0, 0], "res": None})
    return nodes


def roster_items(members: list[tuple], *, select_all: bool = True) -> list[dict]:
    """What ROSTER_JS returns for a filter panel listing these (id, name[, checked]) members."""
    items = [{"testid": "filtersValue_selectAll-input", "label_testid": "filtersValue_selectAll", "name": "Select All", "checked": True}] if select_all else []
    for member in members:
        staff_id, name, *rest = member
        items.append({"testid": f"filtersValue_{staff_id}-input", "label_testid": f"filtersValue_{staff_id}", "name": name, "checked": bool(rest[0]) if rest else True})
    return items


def roster_of(members: list[tuple], **kw) -> Roster:
    return parse_roster(roster_items(members, **kw))


def to_raw(nodes: list[dict]) -> list[dict]:
    """Stored shape (box) -> the shape the live page script returns (x, y, w, h)."""
    return [{**{k: v for k, v in n.items() if k != "box"}, "x": n["box"][0], "y": n["box"][1], "w": n["box"][2], "h": n["box"][3]} for n in nodes]
