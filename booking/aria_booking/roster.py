"""The staff roster, read from the calendar's staff-filter panel. Pure parsing; the page script is a constant.

Why the filter and not the column headers: the calendar only shows the staff the current view includes (six of eleven in one live
look), so counting columns is not a census. The staff filter lists everyone it knows, each as a checkbox whose test id carries the
STABLE numeric staff id and a label carrying the display name:

    input[type=checkbox][data-testid="filtersValue_<id>-input"]   label[data-testid="filtersValue_<id>"]   (label for= the input's id)

The panel is mounted even when closed, with a zero-size box, so the normal page capture (which skips zero-size elements) never sees
it: ROSTER_JS reads it directly. The panel also holds "Select All" and two filter-mode switches ("Only me", "Working Staff Members"); those
three, matched on their complete observed structure, are skipped. Any other entry that is not a recognisable staff checkbox makes the roster
incomplete.

Fail closed: anything that does not fit (no panel, a checkbox without its label, a label under the wrong id, an empty name, a repeated
id) makes the roster INCOMPLETE, and callers must then treat staff as unknown, never guess. A repeated DISPLAY NAME is allowed in the
roster (ids differ) but the voice tools refuse to attribute by name when names collide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

INPUT_RE = re.compile(r"^filtersValue_(\d+)-input$")

# The three non-staff controls the REAL filter panel contains (observed read-only, 2026-10-09): "Select All" and two filter-mode switches.
# Each is recognised only by its WHOLE observed triple (checkbox test id, label test id, visible name). A name alone, a partial triple or
# any other unknown entry is never skipped: it makes the roster incomplete.
OBSERVED_CONTROLS = frozenset({
    ("filtersValue_all-input", "filtersValue_all", "Select All"),
    ("filtersValue-input", "filtersValue", "Only me"),
    ("filtersValue-input", "filtersValue", "Working Staff Members"),
})

# Collected in the page. Returns null when the filter panel is not on the page at all.
ROSTER_JS = r"""
const root = document.querySelector('[data-testid="resources-filter-by-staffers"]');
if (!root) return null;
const labels = [...root.querySelectorAll('label[for]')];
const out = [];
root.querySelectorAll('input[type="checkbox"]').forEach(inp => {
  const label = inp.id ? labels.find(l => l.getAttribute('for') === inp.id) : null;
  out.push({
    testid: inp.getAttribute('data-testid'),
    label_testid: label ? label.getAttribute('data-testid') : null,
    name: label ? (label.textContent || '').replace(/\s+/g, ' ').trim() : null,
    checked: !!inp.checked
  });
});
return out;
"""


@dataclass(frozen=True)
class StaffMember:
    staff_id: str
    name: str
    checked: bool = False  # whether the calendar's current filter shows this person (not whether they work)


@dataclass(frozen=True)
class Roster:
    members: tuple[StaffMember, ...]
    problems: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return bool(self.members) and not self.problems

    def get(self, staff_id: str) -> Optional[StaffMember]:
        return next((m for m in self.members if m.staff_id == staff_id), None)

    def names(self) -> list[str]:
        return [m.name for m in self.members]


def normalise_name(text: Optional[str]) -> str:
    return " ".join((text or "").split())


def _is_observed_control(entry: dict[str, Any]) -> bool:
    """True only for one of the three observed non-staff controls, matched on its complete structure."""
    testid, label_testid = entry.get("testid"), entry.get("label_testid")
    if not isinstance(testid, str) or not isinstance(label_testid, str) or not isinstance(entry.get("name"), str):
        return False
    return (testid, label_testid, normalise_name(entry.get("name"))) in OBSERVED_CONTROLS


def parse_roster(items: Optional[Iterable[dict[str, Any]]]) -> Optional[Roster]:
    """None when the page has no filter panel (roster unavailable); otherwise a Roster, complete or not."""
    if items is None:
        return None
    members: list[StaffMember] = []
    problems: list[str] = []
    seen: set[str] = set()
    try:
        entries = list(items)
    except TypeError:
        return Roster((), ("the roster could not be read",))
    for entry in entries:
        if not isinstance(entry, dict):
            problems.append("an unreadable roster entry")
            continue
        testid = entry.get("testid")
        match = INPUT_RE.match(testid) if isinstance(testid, str) else None
        if match is None:
            if _is_observed_control(entry):
                continue
            # Not a staff checkbox and not one of the observed controls: it may be a staff member the page failed to identify, so the roster is NOT complete.
            problems.append("an entry in the staff filter is neither a recognised staff member nor a known filter control")
            continue
        staff_id = match.group(1)
        name = normalise_name(entry.get("name"))
        if staff_id in seen:
            problems.append(f"staff id {staff_id} appears more than once")
            continue
        seen.add(staff_id)
        if entry.get("label_testid") != f"filtersValue_{staff_id}":
            problems.append(f"staff id {staff_id} has no matching label")
            continue
        if not name:
            problems.append(f"staff id {staff_id} has an empty name")
            continue
        members.append(StaffMember(staff_id, name, bool(entry.get("checked"))))
    if not members and not problems:
        problems.append("the staff filter lists no staff members")
    return Roster(tuple(members), tuple(problems))
