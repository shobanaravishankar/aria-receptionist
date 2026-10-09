"""Review M1/M2 (Sol): a stale single-staff identity must not label another person's column, and an unidentified staff entry must
make the roster incomplete. The first test of each group is Sol's probe, ported unchanged in substance; the rest extend it.

All data is synthetic.
"""

from __future__ import annotations

import pytest

from aria_booking.calendar_parser import CalendarParseError
from aria_booking.driver import DriverError
from aria_booking.roster import parse_roster

from multistaff_pages import build_page, roster_items, to_raw
from test_multistaff_driver import Browser, DAY, STAFF, make
from test_staff_census import load, to_raw as legacy_raw


def single_staff_browser(**kw):
    return Browser(legacy_raw(load("empty_day_mon_12_oct.json")), kw.pop("roster", None), staff_nodes=load("staff_page_one_member.json"), **kw)


MAYA = [{"id": "1002", "name": "Maya Ortiz", "hours": "10AM-8PM"}]


# ---------------------------------------------------------------- M1: the stale census


def test_incomplete_new_roster_cannot_reuse_old_single_staff_identity():  # Sol's probe
    browser = single_staff_browser()
    driver = make(browser)
    assert driver.read_day(DAY).staff_days[0].staff == "Shobs"
    browser.nodes = to_raw(build_page(MAYA))
    browser.roster = roster_items([("1002", "Maya Ortiz")]) + [
        {"testid": "filtersValue_1001-input", "label_testid": None, "name": None, "checked": False},
    ]
    try:
        snapshot = driver.read_day(DAY)
    except DriverError:
        return
    assert not any(
        sd.staff == "Shobs" and sd.working is not None and sd.time_off is not None and sd.appointments is not None
        for sd in snapshot.staff_days
    ), "Another technician's column was silently relabelled as Shobs"


def test_the_incomplete_roster_is_refused_and_the_old_census_is_forgotten():
    browser = single_staff_browser()
    driver = make(browser)
    driver.read_day(DAY)
    assert driver._staff_confirmed is True
    browser.nodes = to_raw(build_page(MAYA))
    browser.roster = roster_items([("1002", "Maya Ortiz")]) + [{"testid": "filtersValue_1001-input", "label_testid": None, "name": None, "checked": False}]
    with pytest.raises(CalendarParseError, match="not read as one complete entry"):
        driver.read_day(DAY)
    assert driver._staff_confirmed is None and driver._single_staff_id is None
    assert driver._appointment_counts == {}, "a count taken under the old identity must never let a save proceed"


def test_a_roster_that_lists_one_other_person_is_refused_even_when_complete():
    browser = single_staff_browser()
    driver = make(browser)
    driver.read_day(DAY)
    browser.nodes = to_raw(build_page(MAYA))
    browser.roster = roster_items([("1002", "Maya Ortiz")])
    with pytest.raises(CalendarParseError, match="someone other than the configured staff member"):
        driver.read_day(DAY)
    assert driver._staff_confirmed is None


def test_a_changed_staff_id_in_a_complete_roster_of_one_is_refused():
    page = to_raw(build_page([{"id": "1001", "name": "Shobs", "hours": "10AM-8PM"}]))
    browser = Browser(page, roster_items([("1001", "Shobs")]), staff_nodes=load("staff_page_one_member.json"))
    driver = make(browser)
    driver.read_day(DAY)
    assert driver._single_staff_id == "1001"
    browser.nodes = to_raw(build_page([{"id": "1002", "name": "Shobs", "hours": "10AM-8PM"}]))
    browser.roster = roster_items([("1002", "Shobs")])
    with pytest.raises(CalendarParseError, match="differs from the one seen before"):
        driver.read_day(DAY)
    assert driver._staff_confirmed is None and driver._single_staff_id is None


def test_a_column_id_that_contradicts_the_filter_is_refused():
    browser = Browser(to_raw(build_page([{"id": "1002", "name": "Shobs", "hours": "10AM-8PM"}])), roster_items([("1001", "Shobs")]),
                      staff_nodes=load("staff_page_one_member.json"))
    with pytest.raises(CalendarParseError, match="differs from the one seen before"):
        make(browser).read_day(DAY)


def test_with_no_filter_a_different_column_id_than_before_is_refused():
    browser = Browser(to_raw(build_page([{"id": "1001", "name": "Shobs", "hours": "10AM-8PM"}], headers=False)), None, staff_nodes=load("staff_page_one_member.json"))
    driver = make(browser)
    driver.read_day(DAY)
    browser.nodes = to_raw(build_page([{"id": "1002", "name": "Shobs", "hours": "10AM-8PM"}], headers=False))
    with pytest.raises(CalendarParseError, match="differs from the one seen before"):
        driver.read_day(DAY)
    assert driver._staff_confirmed is None


def test_with_no_filter_a_header_naming_someone_else_is_refused_and_forgets_the_census():
    browser = single_staff_browser()
    driver = make(browser)
    driver.read_day(DAY)
    browser.nodes = to_raw(build_page(MAYA))  # no ids in the old fixture, but now a Maya column and header
    with pytest.raises(CalendarParseError, match="names someone other than the configured staff member"):
        driver.read_day(DAY)
    assert driver._staff_confirmed is None


def test_after_a_refusal_the_next_good_read_re_checks_the_staff_page():
    browser = single_staff_browser()
    driver = make(browser)
    driver.read_day(DAY)
    assert browser.clicks == ["staff"]
    good_nodes = browser.nodes
    browser.nodes = to_raw(build_page(MAYA))
    with pytest.raises(CalendarParseError):
        driver.read_day(DAY)
    browser.nodes = good_nodes
    assert driver.read_day(DAY).staff_days[0].staff == "Shobs"
    assert browser.clicks == ["staff", "staff"], "the census was repeated because the earlier one had been invalidated"


def test_seeing_a_multi_staff_account_forgets_the_single_staff_identity():
    from test_multistaff_driver import ROSTER5

    browser = single_staff_browser()
    driver = make(browser)
    driver.read_day(DAY)
    assert driver._staff_confirmed is True
    browser.nodes, browser.roster = to_raw(build_page(STAFF)), roster_items(ROSTER5)
    driver.read_day(DAY)
    assert driver._staff_confirmed is None and driver._single_staff_id is None


def test_a_consistent_single_staff_account_keeps_working_and_remembers_its_id_once():
    page = to_raw(build_page([{"id": "1001", "name": "Shobs", "hours": "10AM-8PM"}]))
    browser = Browser(page, roster_items([("1001", "Shobs")]), staff_nodes=load("staff_page_one_member.json"))
    driver = make(browser)
    driver.read_day(DAY)
    driver.read_day(DAY)
    assert driver._single_staff_id == "1001" and browser.clicks == ["staff"]


def test_the_legacy_page_without_any_filter_or_ids_still_reads_as_before():
    browser = single_staff_browser()
    driver = make(browser)
    for _ in range(2):
        assert driver.read_day(DAY).staff_days[0].staff == "Shobs"
    assert browser.clicks == ["staff"] and driver._single_staff_id is None


# ---------------------------------------------------------------- M2: the unidentified staff checkbox


def test_roster_capture_with_unidentified_staff_checkbox_is_incomplete():  # Sol's probe
    items = roster_items([("1001", "Ana Test"), ("1002", "Ben Test")])
    items.append({"testid": None, "label_testid": "filtersValue_1003", "name": "Chris Test", "checked": True})
    roster = parse_roster(items)
    assert roster is not None and not roster.complete, "The parser silently dropped an unidentified staff checkbox and declared a complete roster"


def test_the_problem_names_no_one():
    items = roster_items([("1001", "Ana Test")]) + [{"testid": None, "label_testid": "filtersValue_1003", "name": "Chris Test", "checked": True}]
    roster = parse_roster(items)
    assert "Chris" not in " ".join(roster.problems) and "1003" not in " ".join(roster.problems)


@pytest.mark.parametrize("select_all", [
    {"testid": "filtersValue_selectAll-input", "label_testid": "filtersValue_selectAll", "name": "Select All", "checked": True},
    {"testid": "filtersValue_select_all-input", "label_testid": None, "name": None, "checked": False},
    {"testid": None, "label_testid": None, "name": "  select   ALL ", "checked": True},
    {"testid": "filtersValue_SelectAll-input", "label_testid": "filtersValue_SelectAll", "name": "Everyone", "checked": True},
])
def test_the_real_select_all_control_is_still_skipped(select_all):
    roster = parse_roster([select_all, *roster_items([("1001", "Ana Test")], select_all=False)])
    assert roster.complete and [m.staff_id for m in roster.members] == ["1001"]


@pytest.mark.parametrize("entry", [
    {"testid": None, "label_testid": "filtersValue_1003", "name": "Select All", "checked": True},  # staff-shaped, whatever it is called
    {"testid": None, "label_testid": None, "name": "Chris Test", "checked": True},
    {"testid": "something-else-input", "label_testid": "something-else", "name": "Mystery", "checked": False},
    {"testid": "filtersValue_1003", "label_testid": "filtersValue_1003", "name": "Chris Test", "checked": True},  # id shape without -input
    {"testid": "filtersValue_abc-input", "label_testid": "filtersValue_abc", "name": "Chris Test", "checked": True},
])
def test_any_other_entry_that_is_not_a_staff_checkbox_makes_the_roster_incomplete(entry):
    roster = parse_roster([*roster_items([("1001", "Ana Test"), ("1002", "Ben Test")]), entry])
    assert not roster.complete and [m.staff_id for m in roster.members] == ["1001", "1002"]


def test_a_roster_with_only_select_all_and_nothing_else_is_still_empty_and_incomplete():
    roster = parse_roster(roster_items([]))
    assert not roster.complete


# ---------------------------------------------------------------- each check must refuse on its own (found by mutation checks)


def test_a_filter_of_one_other_person_is_refused_even_when_the_page_shows_no_ids_or_headers():
    browser = single_staff_browser(roster=roster_items([("1002", "Maya Ortiz")]))
    with pytest.raises(CalendarParseError, match="filter lists someone other than the configured staff member"):
        make(browser).read_day(DAY)


def test_a_filter_whose_one_id_changes_is_refused_even_when_the_page_shows_no_column_ids():
    browser = single_staff_browser(roster=roster_items([("1001", "Shobs")]))
    driver = make(browser)
    driver.read_day(DAY)
    assert driver._single_staff_id == "1001"
    browser.roster = roster_items([("1002", "Shobs")])
    with pytest.raises(CalendarParseError, match="differs from the one seen before"):
        driver.read_day(DAY)
