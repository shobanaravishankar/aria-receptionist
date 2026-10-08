"""Who is the single calendar column? Established from the Staff page (a real captured page), never assumed."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from aria_booking.calendar_parser import CalendarParseError
from aria_booking.config import Config
from aria_booking.discover import DISCOVERY_JS, LOADER_GONE_JS
from aria_booking.selenium_driver import SeleniumBooksyDriver
from aria_booking.staff_census import confirms_single_staff, parse_staff_list

FIXTURES = Path(__file__).parent / "fixtures"
CAL = "https://booksy.com/pro/en-us/1234567/calendar?date=2026-10-12&view=day&staffers=working"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["nodes"]


def to_raw(nodes):
    """Stored shape (box) -> the shape the live script returns (x, y, w, h)."""
    return [{**{k: v for k, v in n.items() if k != "box"}, "x": n["box"][0], "y": n["box"][1], "w": n["box"][2], "h": n["box"][3]} for n in nodes]


# ---------------------------------------------------------------- pure parsing on the real Staff page


def test_the_real_staff_page_lists_exactly_one_member_named_shobs():
    names = parse_staff_list(load("staff_page_one_member.json"))
    assert len(names) == 1 and names[0].split()[0] == "Shobs"
    assert confirms_single_staff(names, "Shobs")


@pytest.mark.parametrize("names, configured, expected", [
    (["Shobs S"], "Shobs", True), (["shobs"], "Shobs", True), (["Shobs S"], " shobs ", True),
    ([], "Shobs", False), ([""], "Shobs", False),
    (["Shobs S", "Someone Else"], "Shobs", False),      # two staff: the single-column inference no longer holds
    (["Someone Else"], "Shobs", False), (["Shobsy S"], "Shobs", False),  # a different first name that merely starts the same
])
def test_only_exactly_one_member_with_the_configured_first_name_confirms(names, configured, expected):
    assert confirms_single_staff(names, configured) is expected


def test_a_page_without_the_staff_list_yields_no_names():
    nodes = [n for n in load("staff_page_one_member.json") if n["testid"] != "resources-list"]
    assert parse_staff_list(nodes) == []
    assert parse_staff_list(load("empty_day_mon_12_oct.json")) == []


def test_a_second_staff_member_is_noticed():
    nodes = load("staff_page_one_member.json")
    second = copy.deepcopy(next(n for n in nodes if (n["testid"] or "").startswith("resources-list-item-name-")))
    second["testid"], second["text"] = "resources-list-item-name-1", "Someone Else"
    nodes.append(second)
    names = parse_staff_list(nodes)
    assert len(names) == 2 and not confirms_single_staff(names, "Shobs")


# ---------------------------------------------------------------- the driver: census, then read


class FakeElement:
    def __init__(self, browser, testid, text="", aria=None):
        self.browser, self.testid, self.text, self._aria = browser, testid, text, aria

    def is_displayed(self):
        return True

    def get_attribute(self, name):
        return {"aria-label": self._aria, "data-testid": self.testid}.get(name)

    def click(self):
        if self.testid == "staff":
            self.browser.page = "staff"
            self.browser.clicks.append("staff")


class ReaderBrowser:
    """Serves the real captured calendar page or Staff page, depending on what was last loaded/clicked."""

    def __init__(self, staff_nodes, calendar_nodes, *, staff_menu=True):
        self.staff_nodes, self.calendar_nodes, self.staff_menu = staff_nodes, calendar_nodes, staff_menu
        self.page, self.visited, self.clicks, self.title = "calendar", [], [], "Calendar"
        self.current_url = CAL

    def get(self, url):
        self.visited.append(url)
        self.page = "calendar"

    def find_elements(self, by, css):
        if css == '[data-testid="staff"]':
            return [FakeElement(self, "staff", aria="Staff Members & Permissions")] if self.staff_menu else []
        if css == '[data-testid="resources-list"]':
            return [FakeElement(self, "resources-list")] if self.page == "staff" else []
        return []

    def execute_script(self, js):
        if js == LOADER_GONE_JS:
            return True
        if js == DISCOVERY_JS:
            return to_raw(self.staff_nodes if self.page == "staff" else self.calendar_nodes)
        return None

    def quit(self):
        pass


def make_driver(browser):
    cfg = Config(business_id="1234567")
    return SeleniumBooksyDriver(cfg, webdriver_factory=lambda c: browser, notify=lambda m: None, sleep=lambda s: None, monotonic=lambda: 0.0)


def test_the_driver_confirms_the_staff_from_the_staff_page_and_remembers_it():
    browser = ReaderBrowser(load("staff_page_one_member.json"), load("empty_day_mon_12_oct.json"))
    drv = make_driver(browser)
    assert drv.verify_single_staff() is True
    assert browser.clicks == ["staff"]
    assert drv.verify_single_staff() is True and browser.clicks == ["staff"], "decided once per session, not re-checked"


def test_reading_a_day_runs_the_staff_check_first_then_parses_the_calendar():
    browser = ReaderBrowser(load("staff_page_one_member.json"), load("empty_day_mon_12_oct.json"))
    snapshot = make_driver(browser).read_day(date(2026, 10, 12))
    (staff_day,) = snapshot.staff_days
    assert staff_day.staff == "Shobs" and staff_day.appointments == () and staff_day.time_off == ()
    assert [w.start.strftime("%H:%M") + "-" + w.end.strftime("%H:%M") for w in staff_day.working] == ["10:00-19:00"]
    assert browser.clicks == ["staff"] and "date=2026-10-12" in browser.visited[-1]


def test_a_busy_day_is_read_with_its_appointment_through_the_driver():
    browser = ReaderBrowser(load("staff_page_one_member.json"), load("busy_day_thu_8_oct.json"))
    snapshot = make_driver(browser).read_day(date(2026, 10, 8))
    (appointment,) = snapshot.staff_days[0].appointments
    assert appointment.interval.label() == "15:15-17:45"


def test_a_second_staff_member_makes_every_read_refuse():
    nodes = load("staff_page_one_member.json")
    second = copy.deepcopy(next(n for n in nodes if (n["testid"] or "").startswith("resources-list-item-name-")))
    second["testid"], second["text"] = "resources-list-item-name-1", "Someone Else"
    browser = ReaderBrowser(nodes + [second], load("empty_day_mon_12_oct.json"))
    with pytest.raises(CalendarParseError, match="staff check has not confirmed"):
        make_driver(browser).read_day(date(2026, 10, 12))


def test_a_missing_staff_menu_entry_stops_the_read_instead_of_assuming():
    browser = ReaderBrowser(load("staff_page_one_member.json"), load("empty_day_mon_12_oct.json"), staff_menu=False)
    with pytest.raises(CalendarParseError, match="Staff entry"):
        make_driver(browser).read_day(date(2026, 10, 12))
