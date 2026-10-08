from __future__ import annotations

import json
from datetime import date

import pytest

from aria_booking import cli
from aria_booking.config import Config
from aria_booking.discover import DISCOVERY_JS, LOADER_GONE_JS, build_report, mask_url, redact_text, sanitize_nodes, write_report
from aria_booking.driver import BeforeSaveError, DriverError, DriverUnavailable, SignInRequired
from aria_booking.selenium_driver import SeleniumBooksyDriver, build_chrome
from aria_booking.models import AppointmentSpec

from conftest import NOW, TZ

# ---------------------------------------------------------------- redaction


def test_redaction_keeps_times_prices_and_ui_words_but_masks_names_phones_and_emails():
    assert redact_text("Aria Salon 3:15 PM - 5:45 PM $200") == "Aria Salon 3:15 PM - 5:45 PM $200"
    masked = redact_text("Jane Doe 908-668-8828 jane@example.com")
    assert masked == "xxxx xxx xxx-xxx-xxxx xxxx@xxxxxxx.xxx"
    for secret in ("Jane", "Doe", "908", "668", "8828", "jane@example.com"):
        assert secret not in masked


def test_long_digit_runs_are_masked_even_though_small_numbers_are_kept():
    assert redact_text("150 min") == "150 min"
    assert redact_text("9086688828") == "xxxxxxxxxx"


def test_redaction_preserves_length_and_punctuation_shape_for_parser_writing():
    assert redact_text("Priya,") == "xxxxx,"


def test_mask_url_hides_business_id_and_query_values():
    url = "https://booksy.com/pro/en-us/4242424/calendar?date=2026-10-12&view=day&staffers=working"
    masked = mask_url(url)
    assert "4242424" not in masked and "2026-10-12" not in masked
    assert "<business-id>" in masked and "date" in masked and "view" in masked


def test_sanitized_nodes_never_carry_attribute_values_or_raw_text():
    raw = [{
        "tag": "div", "depth": 4, "cls": ["appt", "blk"], "attrs": ["data-id", "style-x"], "role": "button",
        "testid": "appointment", "aria": "Maria Lopez 3:15 PM", "text": "Maria Lopez", "x": 1, "y": 2, "w": 3, "h": 4,
        "data-id": "SECRET-123",
    }]
    (node,) = sanitize_nodes(raw)
    blob = json.dumps(node)
    assert "Maria" not in blob and "Lopez" not in blob and "SECRET-123" not in blob
    assert node["attrs"] == ["data-id", "style-x"]  # names only
    assert "3:15" in node["aria"]  # times survive so parsers can be written


def test_report_is_written_locally_and_masked(tmp_path):
    report = build_report("https://booksy.com/pro/en-us/4242424/calendar?date=today", "Calendar Maria", [], day="today")
    path = write_report(tmp_path / "evidence", report)
    text = path.read_text(encoding="utf-8")
    assert "4242424" not in text and "Maria" not in text and path.parent.name == "evidence"


# ---------------------------------------------------------------- driver


class FakeBrowser:
    def __init__(self, urls):
        self.urls = list(urls)  # successive values of current_url
        self.visited = []
        self.title = "Calendar"
        self.quit_called = False

    @property
    def current_url(self):
        return self.urls.pop(0) if len(self.urls) > 1 else self.urls[0]

    def get(self, url):
        self.visited.append(url)

    def quit(self):
        self.quit_called = True

    loader_polls = 0  # how many readiness checks report "still loading" before the overlay clears
    scripts = None

    def find_elements(self, by, css):
        return []  # this fake page has no side menu

    def execute_script(self, js):
        if self.scripts is None:
            self.scripts = []
        self.scripts.append("loader" if js == LOADER_GONE_JS else "discovery" if js == DISCOVERY_JS else "other")
        if js == LOADER_GONE_JS:
            if self.loader_polls > 0:
                self.loader_polls -= 1
                return False
            return True
        return []


CAL = "https://booksy.com/pro/en-us/1234567/calendar?date=today"
LOGIN = "https://booksy.com/pro/en-us/login"


def make_driver(urls, *, wait=600, ticks=None):
    cfg = Config(business_id="1234567", sign_in_wait_seconds=wait)
    browser = FakeBrowser(urls)
    clock = iter(ticks) if ticks else None
    messages = []
    drv = SeleniumBooksyDriver(
        cfg,
        webdriver_factory=lambda c: browser,
        notify=messages.append,
        sleep=lambda s: None,
        monotonic=(lambda: next(clock)) if clock else (lambda: 0.0),
    )
    return drv, browser, messages


def test_verify_business_returns_the_id_when_already_signed_in():
    drv, browser, messages = make_driver([CAL])
    assert drv.verify_business() == "1234567"
    assert messages == [] and "1234567" in browser.visited[0]


def test_verify_business_recognises_any_signed_in_page_not_only_the_calendar():
    dashboard = "https://booksy.com/pro/en-us/1234567/dashboard"
    drv, _, _ = make_driver([LOGIN, dashboard])
    assert drv.verify_business() == "1234567"


def test_login_page_with_the_id_only_in_a_query_string_is_not_treated_as_signed_in():
    tricky = "https://booksy.com/pro/en-us/login?next=%2Fpro%2Fen-us%2F1234567%2Fcalendar"
    drv, _, _ = make_driver([tricky], wait=10, ticks=[0, 3, 6, 11])
    with pytest.raises(SignInRequired):
        drv.verify_business()


def test_discover_refuses_when_the_calendar_did_not_open():
    drv, _, _ = make_driver([LOGIN])
    with pytest.raises(SignInRequired):
        drv.discover("today")


def test_verify_business_waits_for_a_person_to_sign_in_and_tells_them_once():
    drv, _, messages = make_driver([LOGIN, LOGIN, LOGIN, CAL])
    assert drv.verify_business() == "1234567"
    assert len(messages) == 1 and "never asks for it" in messages[0]


def test_verify_business_times_out_as_sign_in_required():
    drv, _, _ = make_driver([LOGIN], wait=10, ticks=[0, 3, 6, 11])
    with pytest.raises(SignInRequired):
        drv.verify_business()


def test_creating_without_a_fresh_read_of_that_day_refuses_without_touching_the_page():
    drv, browser, _ = make_driver([CAL])
    spec = AppointmentSpec("Shobs", "Aria Salon", NOW, 150, "note")
    with pytest.raises(BeforeSaveError, match="not read just before saving"):
        drv.create_appointment(spec)
    assert browser.visited == [], "refusing must not even navigate"


def test_reading_a_page_that_cannot_be_understood_raises_instead_of_returning_a_guess():
    from aria_booking.calendar_parser import CalendarParseError

    drv, _, _ = make_driver([CAL])  # the fake browser's page has no calendar structure at all
    with pytest.raises(CalendarParseError):
        drv.read_day(date(2026, 10, 12))


def test_close_quits_the_browser_once():
    drv, browser, _ = make_driver([CAL])
    drv.verify_business()
    drv.close()
    drv.close()
    assert browser.quit_called


def test_chrome_is_not_started_without_a_driver_unless_download_is_explicitly_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: None)
    cfg = Config(business_id="1234567", local_dir=tmp_path)
    with pytest.raises(DriverUnavailable) as err:
        build_chrome(cfg)
    assert "ARIA_ALLOW_DRIVER_DOWNLOAD" in str(err.value)
    assert not cfg.chrome_profile_dir.exists(), "must fail before creating any profile"


# ---------------------------------------------------------------- cli gating


class SpyDriver:
    created = 0

    def __init__(self, cfg, business="1234567"):
        SpyDriver.created += 1
        self.business = business
        self.closed = False

    def verify_business(self):
        return self.business

    def close(self):
        self.closed = True

    def read_day(self, day, include_notes=False):
        raise DriverError("not verified")


def run_cli(argv, env, factory=None):
    lines = []
    code = cli.main(argv, environ=env, driver_factory=factory, clock=lambda: NOW, out=lines.append)
    return code, "\n".join(lines)


def test_book_is_refused_without_the_live_switch_and_no_browser_is_created():
    SpyDriver.created = 0
    code, text = run_cli(
        ["book", "--start", "2026-10-12 10:00", "--confirm-business-id", "1234567", "--approve-save", "--approve-note-typing", "--approve-tour-popups", "--approve-not-now", "--approve-note-readback"],
        {"ARIA_BOOKSY_BUSINESS_ID": "1234567"},
        SpyDriver,
    )
    assert code == cli.EXIT_REFUSED and "ARIA_LIVE_BOOKSY=1" in text and SpyDriver.created == 0


def test_book_is_refused_when_the_confirmation_does_not_match():
    SpyDriver.created = 0
    code, text = run_cli(
        ["book", "--start", "2026-10-12 10:00", "--confirm-business-id", "999999", "--approve-save", "--approve-note-typing", "--approve-tour-popups", "--approve-not-now", "--approve-note-readback"],
        {"ARIA_BOOKSY_BUSINESS_ID": "1234567", "ARIA_LIVE_BOOKSY": "1"},
        SpyDriver,
    )
    assert code == cli.EXIT_REFUSED and "confirm-business-id" in text and SpyDriver.created == 0


def test_book_rejects_a_malformed_start_before_any_browser():
    SpyDriver.created = 0
    code, text = run_cli(
        ["book", "--start", "tomorrow", "--confirm-business-id", "1234567", "--approve-save", "--approve-note-typing", "--approve-tour-popups", "--approve-not-now", "--approve-note-readback"],
        {"ARIA_BOOKSY_BUSINESS_ID": "1234567", "ARIA_LIVE_BOOKSY": "1"},
        SpyDriver,
    )
    assert code == cli.EXIT_REFUSED and SpyDriver.created == 0


def test_wrong_signed_in_business_stops_everything(tmp_path):
    drivers = []

    def factory(cfg):
        drivers.append(SpyDriver(cfg, business="7777777"))
        return drivers[0]

    code, text = run_cli(["find"], {"ARIA_BOOKSY_BUSINESS_ID": "1234567", "ARIA_LOCAL_DIR": str(tmp_path)}, factory)
    assert code == cli.EXIT_REFUSED and "DIFFERENT business" in text and drivers[0].closed


def test_find_with_an_unverified_driver_reports_unknown_not_free(tmp_path):
    code, text = run_cli(
        ["find", "--days", "2"],
        {"ARIA_BOOKSY_BUSINESS_ID": "1234567", "ARIA_LOCAL_DIR": str(tmp_path)},
        lambda cfg: SpyDriver(cfg),
    )
    assert code == cli.EXIT_OK
    assert text.count("UNKNOWN (not free)") == 2 and "no slots" not in text


def test_book_with_an_unverified_driver_creates_nothing_and_says_availability_is_unknown(tmp_path):
    code, text = run_cli(
        ["book", "--start", "2026-10-12 10:00", "--confirm-business-id", "1234567", "--approve-save", "--approve-note-typing", "--approve-tour-popups", "--approve-not-now", "--approve-note-readback"],
        {"ARIA_BOOKSY_BUSINESS_ID": "1234567", "ARIA_LIVE_BOOKSY": "1", "ARIA_LOCAL_DIR": str(tmp_path)},
        lambda cfg: SpyDriver(cfg),
    )
    assert code == cli.EXIT_REFUSED and "unknown_availability" in text


def test_config_command_masks_the_business_id():
    code, text = run_cli(["config"], {"ARIA_BOOKSY_BUSINESS_ID": "1234567"})
    assert code == cli.EXIT_OK and "1234567" not in text and "****567" in text


# ---------------------------------------------------------------- click-through discovery guards

from aria_booking.discover_interactive import ALLOWED_STEPS, DENY_RE, click_refusal, parse_allow


@pytest.mark.parametrize("text", ["SAVE", "Save changes", "Confirm", "Delete appointment", "Cancel appointment", "Check out", "Pay now",
                                  "Send message", "Submit", "Discard changes", "Create", "Book now", "Yes"])
def test_actions_that_change_things_are_refused(text):
    assert click_refusal(text, None, None) is not None


@pytest.mark.parametrize("text, aria, testid", [
    ("NEW APPOINTMENT", None, None), ("Aria Salon Walk-in", None, "calendar-content-calendar-grid-calendar-card-0-2"),
    (None, None, "close-icon"), (None, "Close", None), (None, None, "add-button"), ("next", None, "next-button"),
])
def test_navigation_and_close_clicks_are_allowed(text, aria, testid):
    assert click_refusal(text, aria, testid) is None


def test_the_deny_list_looks_at_aria_label_and_test_id_too():
    assert click_refusal(None, "Save", None) and click_refusal(None, None, "confirm-button")


def test_only_named_steps_can_be_approved():
    assert parse_allow("tour, appointment") == {"tour", "appointment"}
    for bad in ("", "  ", "all", "tour,delete"):
        with pytest.raises(ValueError):
            parse_allow(bad)
    assert set(ALLOWED_STEPS) == {"tour", "appointment", "notes-tab", "future-date", "new-form", "form-explore", "staff-list"}


def test_discover_steps_is_refused_without_explicit_approval_and_creates_no_browser():
    SpyDriver.created = 0
    for argv in (["discover-steps"], ["discover-steps", "--allow", ""], ["discover-steps", "--allow", "save"]):
        code, text = run_cli(argv, {"ARIA_BOOKSY_BUSINESS_ID": "1234567"}, SpyDriver)
        assert code == cli.EXIT_REFUSED and "refused" in text
    assert SpyDriver.created == 0


def test_our_own_test_note_stays_readable_but_other_text_is_still_masked():
    note = "ARIA TEST - fictional appointment, no real customer or payment. Ref: ARIA-0123ABCD"
    assert redact_text(note) == note
    assert "Maria" not in redact_text("Maria Lopez")


class FakeClickable:
    def __init__(self, text="", aria=None, testid=None):
        self.text, self._attrs, self.clicked = text, {"aria-label": aria, "data-testid": testid}, False

    def get_attribute(self, name):
        return self._attrs.get(name)

    def click(self):
        self.clicked = True


def test_the_click_wrapper_refuses_before_clicking():
    from aria_booking.discover_interactive import ClickRefused, InteractiveDiscovery

    runner = InteractiveDiscovery(object(), Config(business_id="1234567"), out=lambda s: None)
    risky = FakeClickable("SAVE")
    with pytest.raises(ClickRefused):
        runner._click(risky, "test")
    assert risky.clicked is False
    fine = FakeClickable(testid="close-icon")
    runner._click(fine, "test")
    assert fine.clicked is True


def test_dependent_steps_cannot_be_approved_alone():
    with pytest.raises(ValueError):
        parse_allow("notes-tab")  # needs the appointment step
    with pytest.raises(ValueError):
        parse_allow("form-explore,tour")  # needs new-form
    assert parse_allow("appointment,notes-tab,new-form,form-explore,future-date,tour")


@pytest.mark.parametrize("testid", ["appointment-button-save", "appointment-button-discard", "appointment-button-book-again",
                                    "appointment-button-checkout", "calendar-status-filter-submit"])
def test_every_dangerous_button_seen_in_the_live_structure_is_refused(testid):
    assert click_refusal(None, None, testid) is not None


@pytest.mark.parametrize("testid", ["add-button", "new-appointment-button", "subbooking-select-service", "notes-and-info",
                                    "select-input-toggle-booked_from", "header-close-button", "finish-button", "close-icon"])
def test_every_navigation_control_needed_for_discovery_is_allowed(testid):
    assert click_refusal(None, None, testid) is None


def test_future_dates_are_the_next_sunday_then_monday():
    from datetime import date as d
    from aria_booking.discover_interactive import future_dates

    assert future_dates(d(2026, 10, 8)) == [d(2026, 10, 11), d(2026, 10, 12)]  # Thursday
    assert future_dates(d(2026, 10, 11)) == [d(2026, 10, 18), d(2026, 10, 19)]  # a Sunday asks for the NEXT one
    assert future_dates(d(2026, 10, 10)) == [d(2026, 10, 11), d(2026, 10, 12)]  # Saturday


def test_the_service_search_cannot_match_the_calendar_grid_or_the_tour():
    from aria_booking.discover_interactive import InteractiveDiscovery

    js = InteractiveDiscovery.PICK_JS
    assert 'calendar-grid-day' in js and 'data-appointment-id' in js and 'step-0' in js
    assert js.count(".closest(") == 3 and js.count("!e.closest(") == 3
