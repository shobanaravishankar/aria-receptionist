"""Review R1 (Sol): the real staff filter holds 11 staff plus THREE non-staff controls; those must not block the roster, and nothing else
may slip through. The first four tests are Sol's synthetic regression, ported unchanged in substance. No account or customer data.
"""

from __future__ import annotations

import pytest

from aria_booking.roster import OBSERVED_CONTROLS, ROSTER_JS, parse_roster

from multistaff_pages import REAL_CONTROLS, build_page, roster_items, to_raw
from test_multistaff_driver import Browser, DAY, STAFF, make


def staff_entry(staff_id="1001", name="Demo Technician"):
    return {"testid": f"filtersValue_{staff_id}-input", "label_testid": f"filtersValue_{staff_id}", "name": name, "checked": True}


def control(name):
    return {"testid": "filtersValue-input", "label_testid": "filtersValue", "name": name, "checked": name == "Working Staff Members"}


def test_real_filter_mode_controls_are_not_staff_or_incomplete_entries():  # Sol's
    entries = [
        control("Only me"),
        control("Working Staff Members"),
        {"testid": "filtersValue_all-input", "label_testid": "filtersValue_all", "name": "Select All", "checked": False},
        staff_entry(),
        staff_entry("1002", "Second Demo Technician"),
    ]
    roster = parse_roster(entries)
    assert roster.complete, roster.problems
    assert [member.staff_id for member in roster.members] == ["1001", "1002"]


def test_an_unknown_control_must_still_refuse_completeness():  # Sol's
    assert not parse_roster([staff_entry(), control("Unexpected staff filter")]).complete


def test_a_staff_shaped_entry_is_not_ignored_because_its_name_matches_a_control():  # Sol's
    malformed = {"testid": None, "label_testid": "filtersValue_1002", "name": "Working Staff Members", "checked": True}
    assert not parse_roster([staff_entry(), malformed]).complete


def test_control_text_without_verified_control_structure_must_not_hide_an_entry():  # Sol's
    malformed = {"testid": None, "label_testid": None, "name": "Only me", "checked": True}
    assert not parse_roster([staff_entry(), malformed]).complete


# ---------------------------------------------------------------- extensions


def test_exactly_three_controls_are_known():
    assert len(OBSERVED_CONTROLS) == 3 and {name for _t, _l, name in OBSERVED_CONTROLS} == {"Select All", "Only me", "Working Staff Members"}


def test_a_realistic_filter_of_eleven_staff_and_the_three_controls_is_complete():
    members = [(str(1000 + i), f"Tech {i}") for i in range(11)]
    roster = parse_roster([*REAL_CONTROLS, *roster_items(members, select_all=False)])
    assert roster.complete and len(roster.members) == 11 and not roster.problems


@pytest.mark.parametrize("entry", [
    {"testid": "filtersValue-input", "label_testid": "filtersValue", "name": "only me", "checked": False},  # wrong case
    {"testid": "filtersValue-input", "label_testid": "filtersValue", "name": "Only me and others", "checked": False},
    {"testid": "filtersValue-input", "label_testid": None, "name": "Only me", "checked": False},  # partial structure
    {"testid": None, "label_testid": "filtersValue", "name": "Only me", "checked": False},
    {"testid": "filtersValue-input", "label_testid": "filtersValue", "name": None, "checked": False},
    {"testid": "filtersValue_all-input", "label_testid": "filtersValue_all", "name": "Everyone", "checked": False},
    {"testid": "filtersValue_all-input", "label_testid": "filtersValue", "name": "Select All", "checked": False},  # mixed-up ids
    {"testid": "filtersValue_all-input", "label_testid": "filtersValue_all", "name": None, "checked": False},
    {"testid": None, "label_testid": None, "name": "Select All", "checked": False},  # a name alone is not enough, even for Select All
    {"testid": "filtersValue_selectAll-input", "label_testid": "filtersValue_selectAll", "name": "Select All", "checked": True},  # an earlier GUESS
    {"testid": 5, "label_testid": ["x"], "name": {"a": 1}, "checked": True},
])
def test_anything_that_is_not_an_exact_observed_control_makes_the_roster_incomplete(entry):
    roster = parse_roster([staff_entry(), entry])
    assert not roster.complete and [m.staff_id for m in roster.members] == ["1001"]


def test_the_problem_text_names_nobody():
    roster = parse_roster([staff_entry("4075333", "Hidden Name"), control("Unexpected staff filter")])
    text = " ".join(roster.problems)
    assert "Hidden" not in text and "4075333" not in text and "Unexpected" not in text


def test_the_page_script_still_reads_only_checkbox_structure():
    for banned in (".click(", "dispatchEvent", "setAttribute", "fetch(", "XMLHttpRequest", "localStorage", "checked ="):
        assert banned not in ROSTER_JS, banned


def test_the_live_shaped_filter_lets_the_multi_staff_reader_through_end_to_end():
    browser = Browser(to_raw(build_page(STAFF)), [*REAL_CONTROLS, *roster_items([("1001", "Lily Chen"), ("1002", "Maya Ortiz"), ("1003", "Noor Haddad")], select_all=False)])
    snap = make(browser).read_day(DAY)
    assert [sd.staff_id for sd in snap.staff_days] == ["1001", "1002", "1003"]
    assert snap.staff_days[2].working is None, "a header with no hours stays unknown"


def test_an_unknown_extra_control_blocks_the_end_to_end_read_instead_of_guessing():
    from aria_booking.calendar_parser import CalendarParseError

    odd = {"testid": "filtersValue-input", "label_testid": "filtersValue", "name": "Everyone else", "checked": False}
    browser = Browser(to_raw(build_page(STAFF)), [*REAL_CONTROLS, odd, *roster_items([("1001", "Lily Chen"), ("1002", "Maya Ortiz"), ("1003", "Noor Haddad")], select_all=False)],
                      staff_nodes=None)
    with pytest.raises(CalendarParseError):
        make(browser).read_day(DAY)
