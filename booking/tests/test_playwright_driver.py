"""The Playwright adapter's logic, with a FAKE page (no browser). It must apply the same fail-closed reading as the Selenium adapter, run every browser
operation on its one owner thread, use native waits instead of sleeps, stay read-only, and start headful with nothing that disguises automation.

The real-browser, real-thread checks are in test_playwright_smoke.py. All data is synthetic.
"""

from __future__ import annotations

import ast
import re
import threading
from datetime import date
from pathlib import Path

import pytest

from aria_booking.calendar_parser import CalendarParseError
from aria_booking.config import Config
from aria_booking.discover import DISCOVERY_JS, LOADER_GONE_JS
from aria_booking.driver import BeforeSaveError, DriverError, SignInRequired
from aria_booking.models import AppointmentSpec
from aria_booking.playwright_driver import (
    LOADER_GONE_FN, SETTLE_FN, VIEWPORT, PlaywrightBooksyDriver, PlaywrightError, PlaywrightSession, PlaywrightTimeout, as_function, launch_options,
)
from aria_booking.roster import ROSTER_JS
from aria_booking.timing import PHASES

from multistaff_pages import REAL_CONTROLS, build_page, roster_items, to_raw
from test_multistaff_driver import DAY, ROSTER5, STAFF
from test_staff_census import CAL, load, to_raw as legacy_raw

BOOKING = Path(__file__).resolve().parent.parent
SOURCE = (BOOKING / "aria_booking" / "playwright_driver.py").read_text(encoding="utf-8")


class FakeLocator:
    def __init__(self, page, testid, visible=1):
        self.page, self.testid, self._n = page, testid, visible

    def count(self):
        self.page.note("count")
        return self._n

    @property
    def first(self):
        return self

    def inner_text(self):
        return "Staff Members & Permissions"

    def get_attribute(self, name):
        return {"aria-label": "Staff Members & Permissions", "data-testid": self.testid}.get(name)

    def click(self, timeout=None):
        self.page.note("click")
        self.page.view = "staff"


class FakePage:
    """Records every call with the thread it came from. Serves a calendar page, a roster and (for the census) the real captured Staff page."""

    def __init__(self, nodes, roster, *, staff_nodes=None, url=CAL, signed_in=True):
        self.nodes, self.roster, self.staff_nodes, self._url, self.view = nodes, roster, staff_nodes, url, "calendar"
        self.calls: list[tuple[str, int]] = []
        self.timeouts = {"goto": False, "loader": False, "settle": False, "evaluate": False}
        self.signed_in = signed_in
        self.visited: list[str] = []
        self.args: list = []
        self.closed = False

    def note(self, name):
        self.calls.append((name, threading.get_ident()))

    @property
    def url(self):
        return self._url if self.signed_in else "https://example.invalid/login"

    def is_closed(self):
        return self.closed

    def goto(self, url, wait_until=None, timeout=None):
        self.note("goto")
        self.visited.append(url)
        self.args.append(("goto", wait_until, timeout))
        self.view = "calendar"
        if self.timeouts["goto"]:
            raise PlaywrightTimeout("goto timed out")

    def wait_for_function(self, expression, arg=None, timeout=None):
        self.note("wait_for_function")
        self.args.append(("wait_for_function", expression, arg, timeout))
        if expression == LOADER_GONE_FN and self.timeouts["loader"]:
            raise PlaywrightTimeout("loader")
        if expression == SETTLE_FN and self.timeouts["settle"]:
            raise PlaywrightTimeout("settle")

    def wait_for_selector(self, selector, timeout=None):
        self.note("wait_for_selector")

    def wait_for_url(self, pattern, timeout=None):
        self.note("wait_for_url")
        self.args.append(("wait_for_url", pattern, timeout))
        if self.timeouts.get("url"):
            raise PlaywrightTimeout("sign-in never happened")
        self.signed_in = True

    def locator(self, selector):
        self.note("locator")
        return FakeLocator(self, "staff")

    def evaluate(self, expression, arg=None):
        self.note("evaluate")
        if self.timeouts["evaluate"]:
            raise PlaywrightError("SECRET selector text https://booksy.invalid/pro/en-us/1234567/")
        if "createTreeWalker" in expression:
            return legacy_raw(self.staff_nodes) if self.view == "staff" else self.nodes
        if "resources-filter-by-staffers" in expression:
            return self.roster
        raise AssertionError("unexpected script")


class FakeSession:
    def __init__(self, page):
        self._page, self.closed_on = page, []
        self.page_calls = []

    def page(self):
        self.page_calls.append(threading.get_ident())
        return self._page

    def close(self):
        self.closed_on.append(threading.get_ident())


def make(page, **kw):
    session = FakeSession(page)
    cfg = Config(business_id="1234567")
    driver = PlaywrightBooksyDriver(cfg, session_factory=lambda: session, notify=lambda m: None, url_for_day=lambda d: f"https://example.invalid/day/{d}", **kw)
    driver.session = session
    return driver


def multi(**kw):
    return FakePage(to_raw(build_page(STAFF)), roster_items([("1001", "Lily Chen"), ("1002", "Maya Ortiz"), ("1003", "Noor Haddad")]), **kw)


# ---------------------------------------------------------------- headful, own profile, nothing disguised


def test_the_browser_is_started_headful_explicitly_in_its_own_profile(tmp_path):
    options = launch_options(Config(business_id="1234567", local_dir=tmp_path))
    assert options["headless"] is False, "stated explicitly, not left to a default"
    assert Path(options["user_data_dir"]) == tmp_path / "playwright-profile"
    assert options["user_data_dir"] != str(Config(local_dir=tmp_path).chrome_profile_dir), "never the Selenium profile"
    assert options["viewport"] == VIEWPORT and "channel" not in options


def test_an_installed_browser_is_used_only_when_asked_for(tmp_path):
    options = launch_options(Config(business_id="1234567", local_dir=tmp_path, browser_channel="chrome"))
    assert options["channel"] == "chrome" and options["headless"] is False


def test_nothing_disguises_the_automation_and_no_personal_profile_is_touched(tmp_path):
    options = launch_options(Config(business_id="1234567", local_dir=tmp_path, browser_channel="chrome"))
    assert set(options) <= {"user_data_dir", "headless", "viewport", "channel"}, "no args, user agent, stealth or ignore_default_args"
    code = ast.parse(SOURCE)
    for node in ast.walk(code):  # docstrings and comments may NAME the things that are not done; only the code itself is checked
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant):
            node.body = node.body[1:] or [ast.Pass()]
    lowered = ast.unparse(code).lower()
    for banned in ("ignore_default_args", "user_agent", "stealth", "automationcontrolled", "disable-blink-features", "add_init_script", "set_extra_http_headers",
                   "storage_state", "cookies(", "add_cookies", "connect_over_cdp", "appdata", "user data"):
        assert banned not in lowered, banned


def test_the_profile_folder_is_inside_the_git_ignored_local_dir():
    cfg = Config(business_id="1234567")
    assert cfg.playwright_profile_dir.parent == cfg.local_dir and cfg.playwright_profile_dir.name == "playwright-profile"
    assert ".local" in str(cfg.local_dir).replace("\\", "/")
    files = [f for f in (BOOKING.parent / ".gitignore", BOOKING / ".gitignore") if f.exists()]
    if not files:
        pytest.skip("this copy of the tests has no .gitignore to read")
    assert any(".local" in f.read_text(encoding="utf-8") for f in files)


def test_the_module_never_sleeps_and_waits_natively():
    tree = ast.parse(SOURCE)
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not any(c.endswith("sleep") for c in calls), "no time.sleep anywhere in the adapter"
    for native in ("page.goto", "page.wait_for_function", "page.wait_for_url", "page.wait_for_selector"):
        assert native in calls, native


def test_the_page_scripts_are_wrapped_as_functions_for_evaluate():
    wrapped = as_function(DISCOVERY_JS)
    assert wrapped.startswith("() => {") and wrapped.rstrip().endswith("}") and "return out;" in wrapped
    assert as_function(ROSTER_JS).startswith("() => {")
    assert LOADER_GONE_JS.startswith("return ") and LOADER_GONE_FN.startswith("() =>")


# ---------------------------------------------------------------- the same reading, the same refusals


def test_a_multi_staff_page_is_read_by_staff_id_through_the_playwright_adapter():
    driver = make(multi())
    snapshot = driver.read_day(DAY)
    assert [(sd.staff, sd.staff_id) for sd in snapshot.staff_days] == [("Lily Chen", "1001"), ("Maya Ortiz", "1002"), ("Noor Haddad", "1003")]
    assert snapshot.staff_days[2].working is None, "a header with no hours stays unknown"
    driver.close()


def test_the_live_shaped_filter_with_its_three_controls_is_accepted():
    page = FakePage(to_raw(build_page(STAFF)), [*REAL_CONTROLS, *roster_items([("1001", "Lily Chen"), ("1002", "Maya Ortiz"), ("1003", "Noor Haddad")], select_all=False)])
    driver = make(page)
    assert len(driver.read_day(DAY).staff_days) == 3
    driver.close()


def test_an_incomplete_roster_is_refused_and_never_falls_back_to_a_stale_identity():
    page = FakePage(legacy_raw(load("empty_day_mon_12_oct.json")), None, staff_nodes=load("staff_page_one_member.json"))
    driver = make(page)
    assert driver.read_day(DAY).staff_days[0].staff == "Shobs"
    page.nodes = to_raw(build_page([{"id": "1002", "name": "Maya Ortiz", "hours": "10AM-8PM"}]))
    page.roster = roster_items([("1002", "Maya Ortiz")]) + [{"testid": "filtersValue_1001-input", "label_testid": None, "name": None, "checked": False}]
    with pytest.raises(CalendarParseError, match="not read as one complete entry"):
        driver.read_day(DAY)
    assert driver._staff_confirmed is None
    driver.close()


def test_the_single_staff_census_runs_once_through_native_calls():
    page = FakePage(legacy_raw(load("empty_day_mon_12_oct.json")), None, staff_nodes=load("staff_page_one_member.json"))
    driver = make(page)
    assert driver.read_day(DAY).staff_days[0].staff == "Shobs"
    driver.read_day(DAY)
    assert [name for name, _t in page.calls].count("click") == 1
    driver.close()


def test_a_one_person_filter_keeps_its_verified_id():
    page = FakePage(to_raw(build_page([{"id": "1001", "name": "Shobs", "hours": "10AM-8PM"}])), roster_items([("1001", "Shobs")]), staff_nodes=load("staff_page_one_member.json"))
    driver = make(page)
    assert driver.read_day(DAY).staff_days[0].staff_id == "1001"
    driver.close()


def test_a_page_that_is_signed_out_raises_sign_in_required():
    driver = make(multi(signed_in=False))
    with pytest.raises(SignInRequired):
        driver.read_day(DAY)
    driver.close()


@pytest.mark.parametrize("which,match", [("goto", "did not load"), ("loader", "still loading"), ("settle", "never settled")])
def test_a_page_that_does_not_load_or_settle_is_refused(which, match):
    page = multi()
    page.timeouts[which] = True
    driver = make(page, load_timeout_seconds=7)
    with pytest.raises(CalendarParseError, match=match):
        driver.read_day(DAY)
    driver.close()


def test_the_page_waits_share_one_budget_not_one_each():
    page = multi()
    driver = make(page, load_timeout_seconds=7)
    driver.read_day(DAY)
    waits = [a[3] if a[0] == "wait_for_function" else a[2] for a in page.args if a[0] in ("goto", "wait_for_function")]
    assert waits and all(w <= 7000 for w in waits), "every native wait is capped by the one load budget"
    driver.close()


def test_a_playwright_error_never_leaks_page_text_or_the_url():
    page = multi()
    page.timeouts["evaluate"] = True
    driver = make(page)
    with pytest.raises(DriverError) as info:
        driver.read_day(DAY)
    assert "SECRET" not in str(info.value) and "1234567" not in str(info.value) and "PlaywrightError" in str(info.value) or "Error" in str(info.value)
    driver.close()


# ---------------------------------------------------------------- every browser call is on the one owner thread


def test_every_page_call_and_the_session_run_on_one_thread_that_is_not_the_callers():
    page = multi()
    driver = make(page)
    driver.read_day(DAY)
    driver.read_day(DAY)
    threads = {t for _n, t in page.calls}
    assert len(threads) == 1 and threading.get_ident() not in threads
    assert set(driver.session.page_calls) == threads, "the session was asked for the page on that same thread"
    driver.close()


def test_reads_from_many_caller_threads_are_serialized_onto_that_thread():
    page = multi()
    driver = make(page)
    errors = []

    def read():
        try:
            driver.read_day(DAY)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=read) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len({t for _n, t in page.calls}) == 1
    assert driver.runner_stats["run"] == 6
    driver.close()


def test_a_read_leaves_its_phase_timings_including_the_hand_off():
    driver = make(multi())
    driver.read_day(DAY)
    names = set(driver.last_read_ms)
    assert {"launch", "navigate", "page_ready", "paint_wait", "capture", "roster", "parse", "runner_wait"} <= names <= PHASES
    driver.close()


# ---------------------------------------------------------------- sign-in is a person's job


def test_a_signed_out_browser_waits_for_a_person_with_a_native_wait_and_never_types_anything():
    page = multi(signed_in=False)
    messages = []
    driver = PlaywrightBooksyDriver(Config(business_id="1234567", sign_in_wait_seconds=90), session_factory=lambda: FakeSession(page), notify=messages.append,
                                    url_for_day=lambda d: "x")
    # after the person signs in the page reaches the business URL; the fake's wait_for_url flips it
    assert driver.verify_business() == "1234567"
    names = [n for n, _t in page.calls]
    assert "wait_for_url" in names and not {"fill", "type", "press", "click"} & set(names)
    assert any("sign in" in m.lower() for m in messages) and not any("password" in m.lower() and "type" in m.lower() for m in messages)
    waited = next(a for a in page.args if a[0] == "wait_for_url")
    assert waited[2] == 90_000 and isinstance(waited[1], re.Pattern)
    driver.close()


def test_giving_up_on_sign_in_says_so():
    page = multi(signed_in=False)
    page.timeouts["url"] = True
    driver = PlaywrightBooksyDriver(Config(business_id="1234567", sign_in_wait_seconds=10), session_factory=lambda: FakeSession(page), notify=lambda m: None,
                                    url_for_day=lambda d: "x")
    with pytest.raises(SignInRequired, match="timed out waiting for a person"):
        driver.verify_business()
    driver.close()


def test_an_already_signed_in_browser_returns_its_business_without_waiting():
    page = multi()
    driver = make(page)
    assert driver.verify_business() == "1234567" and "wait_for_url" not in [n for n, _t in page.calls]
    driver.close()


# ---------------------------------------------------------------- read-only


def test_creating_an_appointment_is_refused_before_any_ui_action():
    page = multi()
    driver = make(page)
    spec = AppointmentSpec("Lily Chen", "Aria Salon", __import__("datetime").datetime(2026, 10, 12, 11, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York")), 150, "n")
    with pytest.raises(BeforeSaveError, match="read-only"):
        driver.create_appointment(spec)
    assert page.calls == [] and driver.session.page_calls == [], "not even the browser was touched"
    driver.close()


def test_notes_discovery_and_raw_browser_access_are_unavailable():
    driver = make(multi())
    with pytest.raises(DriverError):
        driver.read_day(DAY, include_notes=True)
    with pytest.raises(DriverError):
        driver.discover("today")
    with pytest.raises(DriverError):
        driver.browser()
    assert driver.approvals == frozenset()
    driver.close()


def test_the_adapter_does_not_import_or_use_the_booking_machinery():
    tree = ast.parse(SOURCE)
    imported = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not {m for m in imported if "appointment_creator" in m or "form_rehearsal" in m or "selenium" in m}


# ---------------------------------------------------------------- lifecycle


def test_close_runs_on_the_owner_thread_once_and_is_idempotent():
    page = multi()
    driver = make(page)
    driver.read_day(DAY)
    owner = next(iter({t for _n, t in page.calls}))
    driver.close()
    driver.close()
    assert driver.session.closed_on == [owner]


def test_nothing_can_be_read_after_close():
    driver = make(multi())
    driver.close()
    with pytest.raises(DriverError):
        driver.read_day(DAY)


def test_closing_a_driver_that_never_read_starts_no_browser():
    driver = make(multi())
    driver.close()
    assert driver.session.page_calls == []


# ---------------------------------------------------------------- the real session object, with fake Playwright pieces


class FakePW:
    def __init__(self, fail_first=False, missing=False):
        self.launched, self.stopped = [], False
        self.chromium = self
        self._fail, self._missing = fail_first, missing

    def launch_persistent_context(self, **options):
        if self._missing:
            raise PlaywrightError("BrowserType.launch_persistent_context: Executable doesn't exist at C:\\x\\chrome.exe")
        self.launched.append(options)
        return FakeContext()

    def stop(self):
        self.stopped = True


class FakeContext:
    def __init__(self):
        self.pages = []
        self.closed = False
        self.dead = False

    def new_page(self):
        if self.dead:
            raise PlaywrightError("context closed")
        page = FakeContextPage()
        self.pages.append(page)
        return page

    def close(self):
        self.closed = True


class FakeContextPage:
    def __init__(self):
        self.closed = False

    def is_closed(self):
        return self.closed


def test_the_session_starts_one_browser_with_the_explicit_options_and_reuses_it(tmp_path):
    pw = FakePW()
    session = PlaywrightSession(Config(business_id="1234567", local_dir=tmp_path), starter=lambda: pw)
    page = session.page()
    assert session.page() is page and session.launches == 1
    assert pw.launched[0]["headless"] is False and (tmp_path / "playwright-profile").is_dir()
    session.close()
    assert pw.stopped


def test_a_closed_page_gets_a_new_page_in_the_same_window(tmp_path):
    pw = FakePW()
    session = PlaywrightSession(Config(business_id="1234567", local_dir=tmp_path), starter=lambda: pw)
    first = session.page()
    first.closed = True
    second = session.page()
    assert second is not first and session.launches == 1


def test_a_window_that_went_away_is_started_again_once(tmp_path):
    pw = FakePW()
    session = PlaywrightSession(Config(business_id="1234567", local_dir=tmp_path), starter=lambda: pw)
    first = session.page()
    first.closed = True
    session._context.dead = True
    session._context.pages.clear()
    session.page()
    assert session.launches == 2 and len(pw.launched) == 2


def test_a_missing_browser_says_how_to_get_one_without_downloading_anything(tmp_path):
    from aria_booking.driver import DriverUnavailable

    session = PlaywrightSession(Config(business_id="1234567", local_dir=tmp_path), starter=lambda: FakePW(missing=True))
    with pytest.raises(DriverUnavailable) as info:
        session.page()
    text = str(info.value)
    assert "playwright install chromium" in text and "ARIA_BROWSER_CHANNEL=chrome" in text and "C:\\x" not in text


def test_closing_a_session_that_never_started_is_harmless(tmp_path):
    PlaywrightSession(Config(business_id="1234567", local_dir=tmp_path), starter=lambda: FakePW()).close()


# ---------------------------------------------------------------- navigation stays on the injected addresses; the census click is guarded


def test_sign_in_and_the_census_navigate_to_the_injected_address_never_the_real_one():
    page = FakePage(legacy_raw(load("empty_day_mon_12_oct.json")), None, staff_nodes=load("staff_page_one_member.json"))
    driver = make(page)
    driver.verify_business()
    driver.read_day(DAY)
    assert page.visited and set(page.visited) <= {"https://example.invalid/day/today", "https://example.invalid/day/2026-10-12"}, page.visited
    assert not any("booksy" in url for url in page.visited)
    driver.close()


def test_the_census_will_not_click_something_that_looks_like_an_action():
    page = FakePage(legacy_raw(load("empty_day_mon_12_oct.json")), None, staff_nodes=load("staff_page_one_member.json"))

    class Dangerous(FakeLocator):
        def inner_text(self):
            return "Delete account"

    page.locator = lambda selector: Dangerous(page, "staff")
    driver = make(page)
    with pytest.raises(CalendarParseError):
        driver.read_day(DAY)
    assert "click" not in [n for n, _t in page.calls]
    driver.close()
