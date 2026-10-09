"""The live driver's two read paths, with a fake browser: ID-bound multi-staff reads, and every fallback that must fail closed."""

from __future__ import annotations

import json
import re
from datetime import date

import pytest

from aria_booking.calendar_parser import CalendarParseError
from aria_booking.config import Config
from aria_booking.discover import DISCOVERY_JS, LOADER_GONE_JS, build_report
from aria_booking.driver import BeforeSaveError, DriverError
from aria_booking.models import AppointmentSpec
from aria_booking.roster import ROSTER_JS
from aria_booking.safety import build_note
from aria_booking.selenium_driver import SeleniumBooksyDriver

from conftest import TZ
from multistaff_pages import build_page, roster_items, to_raw
from test_staff_census import CAL, FakeElement, load, to_raw as legacy_raw

DAY = date(2026, 10, 12)
STAFF = [
    {"id": "1001", "name": "Lily Chen", "hours": "10AM-8PM", "nonworking": [(540, 600)], "appointments": [(840, 930, "Deep Tissue")]},
    {"id": "1002", "name": "Maya Ortiz", "hours": "10AM-8PM", "nonworking": [(540, 600)]},
    {"id": "1003", "name": "Noor Haddad", "hours": None},
]
ROSTER5 = [("1001", "Lily Chen"), ("1002", "Maya Ortiz"), ("1003", "Noor Haddad"), ("1004", "Priya Nair", False), ("1005", "Sam Ellis", False)]


class Browser:
    """Serves one calendar page, one roster, and (for the single-staff path) the real captured Staff page."""

    def __init__(self, nodes, roster, *, staff_nodes=None, roster_raises=False):
        self.nodes, self.roster, self.staff_nodes, self.roster_raises = nodes, roster, staff_nodes, roster_raises
        self.page, self.visited, self.clicks, self.title, self.current_url = "calendar", [], [], "Calendar", CAL
        self.scripts = []

    def get(self, url):
        self.visited.append(url)
        self.page = "calendar"

    def find_elements(self, by, css):
        if css == '[data-testid="staff"]' and self.staff_nodes is not None:
            return [FakeElement(self, "staff", aria="Staff Members & Permissions")]
        if css == '[data-testid="resources-list"]':
            return [FakeElement(self, "resources-list")] if self.page == "staff" else []
        return []

    def execute_script(self, js):
        self.scripts.append("loader" if js == LOADER_GONE_JS else "discovery" if js == DISCOVERY_JS else "roster" if js == ROSTER_JS else "other")
        if js == LOADER_GONE_JS:
            return True
        if js == DISCOVERY_JS:
            return legacy_raw(self.staff_nodes) if self.page == "staff" else self.nodes
        if js == ROSTER_JS:
            if self.roster_raises:
                raise RuntimeError("script failed")
            return self.roster
        return None

    def quit(self):
        pass


def make(browser, **kw):
    cfg = Config(business_id="1234567")
    return SeleniumBooksyDriver(cfg, webdriver_factory=lambda c: browser, notify=lambda m: None, sleep=lambda s: None, monotonic=lambda: 0.0, **kw)


def multi_browser(staff=STAFF, roster=ROSTER5, **page):
    return Browser(to_raw(build_page(staff, **page)), roster_items(roster))


# ---------------------------------------------------------------- the multi-staff path


def test_a_complete_roster_of_several_people_is_read_by_staff_id_with_no_staff_page_visit():
    browser = multi_browser()
    snap = make(browser).read_day(DAY)
    assert [(sd.staff, sd.staff_id) for sd in snap.staff_days] == [("Lily Chen", "1001"), ("Maya Ortiz", "1002"), ("Noor Haddad", "1003")]
    assert browser.clicks == [] and len(browser.visited) == 1, "one page load, no navigation to the Staff page"
    assert "date=2026-10-12" in browser.visited[0]
    assert snap.staff_days[2].working is None, "the header with no hours stays unknown"


def test_the_driver_remembers_the_roster_it_saw_including_people_not_shown_today():
    browser = multi_browser()
    drv = make(browser)
    drv.read_day(DAY)
    assert [m.name for m in drv.last_roster.members] == [r[1] for r in ROSTER5]
    assert drv.last_roster.get("1004").checked is False


def test_notes_are_not_read_on_a_multi_staff_calendar_because_times_cannot_tell_people_apart():
    drv = make(multi_browser(), approvals=frozenset({"note-readback", "tour-popups"}))
    with pytest.raises(DriverError, match="not supported on a multi-staff calendar"):
        drv.read_day(DAY, include_notes=True)


def test_a_multi_staff_read_leaves_nothing_that_could_let_a_booking_proceed():
    drv = make(multi_browser())
    drv.read_day(DAY)
    spec = AppointmentSpec("Shobs", "Aria Salon", __import__("datetime").datetime(2026, 10, 12, 11, 0, tzinfo=TZ), 150, build_note("ARIA-0123ABCD"))
    with pytest.raises(BeforeSaveError, match="not read just before saving"):
        drv.create_appointment(spec)
    other = AppointmentSpec("Lily Chen", "Aria Salon", spec.start, 150, spec.note)
    with pytest.raises(BeforeSaveError, match="single verified staff member"):
        drv.create_appointment(other)


@pytest.mark.parametrize("problem", ["unknown_column", "dup", "name_mismatch", "truncated", "wrong_day"])
def test_a_page_that_cannot_be_attributed_is_refused_not_guessed(problem):
    if problem == "unknown_column":
        browser = multi_browser([STAFF[0], dict(STAFF[1], id="777")])
    elif problem == "dup":
        browser = multi_browser([STAFF[0], dict(STAFF[1], res="1001")])
    elif problem == "name_mismatch":
        browser = multi_browser([STAFF[0], dict(STAFF[1], header_name="Priya Nair")])
    elif problem == "truncated":
        browser = multi_browser(truncated=True)
    else:
        browser = multi_browser()
    day = date(2026, 10, 13) if problem == "wrong_day" else DAY
    with pytest.raises(CalendarParseError):
        make(browser).read_day(day)


# ---------------------------------------------------------------- everything else takes the proven single-staff path, which fails closed


def test_an_incomplete_roster_never_takes_the_multi_path_and_never_falls_back_to_the_single_staff_census():
    bad = roster_items(ROSTER5) + [{"testid": "filtersValue_1006-input", "label_testid": None, "name": None, "checked": True}]
    browser = Browser(to_raw(build_page(STAFF)), bad, staff_nodes=load("staff_page_one_member.json"))
    with pytest.raises(CalendarParseError, match="not read as one complete entry"):
        make(browser).read_day(DAY)
    assert browser.clicks == [], "an incomplete filter is refused outright; the Staff page is not even opened to vouch for it"


@pytest.mark.parametrize("roster", [[], "garbage"])
def test_an_unusable_but_present_filter_is_refused_not_ignored(roster):
    browser = Browser(to_raw(build_page(STAFF, headers=False)), roster, staff_nodes=load("staff_page_one_member.json"))
    with pytest.raises(CalendarParseError, match="not read as one complete entry"):
        make(browser).read_day(DAY)


def test_a_page_with_no_filter_at_all_takes_the_single_staff_path_and_refuses_a_multi_column_page():
    browser = Browser(to_raw(build_page(STAFF, headers=False)), None, staff_nodes=load("staff_page_one_member.json"))
    with pytest.raises(CalendarParseError, match="3 staff columns"):
        make(browser).read_day(DAY)


def test_a_page_with_no_filter_whose_headers_name_other_people_is_refused_before_the_census_is_trusted():
    browser = Browser(to_raw(build_page(STAFF)), None, staff_nodes=load("staff_page_one_member.json"))
    with pytest.raises(CalendarParseError, match="names someone other than the configured staff member"):
        make(browser).read_day(DAY)
    assert browser.clicks == []


def test_a_failing_roster_script_is_treated_as_no_roster():
    browser = Browser(to_raw(build_page(STAFF, headers=False)), roster_items(ROSTER5), staff_nodes=load("staff_page_one_member.json"), roster_raises=True)
    with pytest.raises(CalendarParseError, match="3 staff columns"):
        make(browser).read_day(DAY)


def test_a_roster_of_one_keeps_using_the_live_proven_single_staff_path():
    browser = Browser(legacy_raw(load("empty_day_mon_12_oct.json")), roster_items([("900009", "Shobs")]), staff_nodes=load("staff_page_one_member.json"))
    snap = make(browser).read_day(DAY)
    (sd,) = snap.staff_days
    assert sd.staff == "Shobs" and sd.staff_id == "" and browser.clicks == ["staff"]
    assert [w.label() for w in sd.working] == ["10:00-19:00"]


def test_the_single_staff_path_still_runs_its_census_only_once_per_session():
    browser = Browser(legacy_raw(load("empty_day_mon_12_oct.json")), None, staff_nodes=load("staff_page_one_member.json"))
    drv = make(browser)
    drv.read_day(DAY)
    drv.read_day(DAY)
    assert browser.clicks == ["staff"]


# ---------------------------------------------------------------- the page scripts only read what they must


def test_the_page_capture_reads_one_attribute_value_and_nothing_else_sensitive():
    names = set(re.findall(r"getAttribute\('([^']+)'\)", DISCOVERY_JS))
    assert names <= {"class", "role", "data-testid", "data-resource", "aria-label"}, names
    assert "getAttributeNames()" in DISCOVERY_JS and "data-resource" in DISCOVERY_JS
    for banned in (".click(", "dispatchEvent", "innerHTML =", "setAttribute", "fetch(", "XMLHttpRequest", "localStorage", "document.cookie"):
        assert banned not in DISCOVERY_JS and banned not in ROSTER_JS, banned


def test_the_capture_marks_a_cut_short_page_instead_of_silently_dropping_staff():
    assert "__truncated__" in DISCOVERY_JS and "12000" in DISCOVERY_JS
    # the sentinel is added ONLY when the limit was hit: the flag is set where the limit is checked, and tested where it is pushed
    limit_branch = DISCOVERY_JS.split("if (count >= 12000)")[1].split("}")[0]
    assert "truncated = true" in limit_branch and "break" in limit_branch
    assert "if (truncated) out.push" in DISCOVERY_JS and DISCOVERY_JS.count("__truncated__") == 1


def test_evidence_reports_never_carry_the_staff_id_value():
    nodes = to_raw(build_page(STAFF))
    assert any(n.get("res") == "1001" for n in nodes)
    text = json.dumps(build_report("https://booksy.com/pro/en-us/1234567/calendar", "Calendar", nodes, day="2026-10-12"))
    assert "1001" not in text and "1002" not in text and "res" not in json.loads(text)["nodes"][0]
    assert "Lily" not in text and "Chen" not in text, "staff names are redacted too"


# ---------------------------------------------------------------- the whole stack: real driver (fake browser) -> voice tools


def _stack(staff=STAFF, roster=ROSTER5):
    from datetime import datetime

    from aria_booking.catalog.bookable import BookableRegistry, BookableService
    from aria_booking.voice.launch import build_endpoint

    cfg = Config(business_id="1234567")
    browser = multi_browser(staff, roster)
    driver = make(browser)
    registry = BookableRegistry([
        BookableService("deep-60", "Deep Tissue Massage 60", 60, verified=True),
        BookableService("facial-60", "Facial 60", 60, eligible_staff=frozenset({"maya ortiz"}), verified=True),
    ])
    endpoint = build_endpoint(cfg, driver, lambda *a: (_ for _ in ()).throw(AssertionError("no writer")), lambda: datetime(2026, 10, 8, 12, tzinfo=TZ), "k",
                              registry=registry, search_days=1)
    return endpoint._tools, browser


def check(tools, staff, time, service="deep-60"):
    args = {"service_id": service, "date": "2026-10-12", "time": time}
    if staff:
        args["staff"] = staff
    return tools.check_slot("call", args)


def test_is_lily_available_monday_at_4_for_a_deep_tissue_massage_through_the_real_reader():
    tools, browser = _stack()
    reply = check(tools, "Lily", "16:00")
    assert reply["status"] == "available" and reply["options"][0]["technician"] == "Lily Chen", reply
    assert "Lily Chen" in reply["speak"] and "can't book it" in reply["speak"] and reply["ok"] is False
    assert "option_id" not in reply["options"][0] and browser.clicks == []


def test_the_same_question_when_the_named_technician_has_an_appointment_gets_her_own_alternatives():
    tools, _ = _stack()
    reply = check(tools, "Lily", "15:00")  # Lily Chen is booked 14:00-15:30
    assert reply["status"] == "alternatives" and reply["unavailable_reason"] == "occupied" and "Lily Chen is already booked at 3 PM" in reply["speak"]
    assert {o["technician"] for o in reply["options"]} == {"Lily Chen"}


def test_someone_not_shown_on_that_days_calendar_is_not_invented():
    tools, _ = _stack()
    reply = check(tools, "Priya", "16:00")
    assert reply["status"] == "staff_unavailable" and reply["reason"] == "staff_not_on_schedule"


def test_a_person_whose_hours_are_not_shown_is_unknown_never_available():
    tools, _ = _stack()
    reply = check(tools, "Noor", "16:00")
    assert reply["status"] == "unknown", reply


def test_nobody_named_picks_the_first_eligible_person_who_is_actually_free():
    tools, _ = _stack()
    reply = check(tools, None, "14:30")  # Lily Chen is busy; Maya Ortiz is free; Noor's hours are unknown
    assert reply["status"] == "available" and reply["options"][0]["technician"] == "Maya Ortiz"


def test_service_eligibility_uses_the_same_roster_names_as_the_page():
    tools, _ = _stack()
    assert check(tools, None, "16:00", "facial-60")["options"][0]["technician"] == "Maya Ortiz"
    not_eligible = check(tools, "Lily", "16:00", "facial-60")
    assert not_eligible["status"] == "staff_unavailable" and not_eligible["reason"] == "staff_not_eligible"


def test_two_people_with_the_same_display_name_are_not_guessed_between():
    staff = [dict(STAFF[0], id="1001", name="Alex Kim"), dict(STAFF[1], id="1002", name="Alex Kim")]
    tools, _ = _stack(staff, [("1001", "Alex Kim"), ("1002", "Alex Kim")])
    reply = check(tools, "Alex", "16:00")
    assert reply["status"] == "unknown" and reply["reason"] == "ambiguous_roster"


def test_an_incomplete_roster_makes_the_whole_answer_unknown_and_nothing_is_offered():
    tools, _ = _stack(roster=ROSTER5)
    tools.driver._load_day = lambda day: (to_raw(build_page(STAFF)), None)  # the filter panel vanished
    tools.driver._staff_confirmed = False
    reply = check(tools, "Lily", "16:00")
    assert reply["status"] in {"unknown", "system_unavailable"} and "options" not in reply


def test_a_stale_appointment_count_from_an_earlier_read_never_survives_into_a_multi_staff_read():
    from datetime import datetime

    browser = Browser(legacy_raw(load("empty_day_mon_12_oct.json")), None, staff_nodes=load("staff_page_one_member.json"))
    drv = make(browser)
    drv.read_day(DAY)  # the single-staff path records "0 appointments that day"
    assert drv._appointment_counts[DAY] == 0
    browser.nodes, browser.roster = to_raw(build_page(STAFF)), roster_items(ROSTER5)  # the account is now seen as multi-staff
    drv.read_day(DAY)
    assert DAY not in drv._appointment_counts, "a count from another page must not let a save proceed"
    spec = AppointmentSpec("Shobs", "Aria Salon", datetime(2026, 10, 12, 11, 0, tzinfo=TZ), 150, build_note("ARIA-0123ABCD"))
    with pytest.raises(BeforeSaveError, match="not read just before saving"):
        drv.create_appointment(spec)
