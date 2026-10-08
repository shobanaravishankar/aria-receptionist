from __future__ import annotations

import json
from datetime import date

import pytest

from aria_booking import cli
from aria_booking.config import Config
from aria_booking.discover import build_report, mask_url, redact_text, sanitize_nodes, write_report
from aria_booking.driver import BeforeSaveError, DriverError, DriverUnavailable, SignInRequired
from aria_booking.selenium_driver import DiscoveryRequired, SeleniumBooksyDriver, build_chrome
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

    def execute_script(self, js):
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


def test_unverified_operations_refuse_instead_of_guessing():
    drv, browser, _ = make_driver([CAL])
    with pytest.raises(DiscoveryRequired):
        drv.read_day(date(2026, 10, 12))
    spec = AppointmentSpec("Shobs", "Aria Salon", NOW, 150, "note")
    with pytest.raises(DiscoveryRequired) as err:
        drv.create_appointment(spec)
    assert isinstance(err.value, BeforeSaveError), "must be classified as definitely-not-saved"
    assert browser.visited == [], "refusing must not even navigate"


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

    def read_day(self, day):
        raise DriverError("not verified")


def run_cli(argv, env, factory=None):
    lines = []
    code = cli.main(argv, environ=env, driver_factory=factory, clock=lambda: NOW, out=lines.append)
    return code, "\n".join(lines)


def test_book_is_refused_without_the_live_switch_and_no_browser_is_created():
    SpyDriver.created = 0
    code, text = run_cli(
        ["book", "--start", "2026-10-12 10:00", "--confirm-business-id", "1234567"],
        {"ARIA_BOOKSY_BUSINESS_ID": "1234567"},
        SpyDriver,
    )
    assert code == cli.EXIT_REFUSED and "ARIA_LIVE_BOOKSY=1" in text and SpyDriver.created == 0


def test_book_is_refused_when_the_confirmation_does_not_match():
    SpyDriver.created = 0
    code, text = run_cli(
        ["book", "--start", "2026-10-12 10:00", "--confirm-business-id", "999999"],
        {"ARIA_BOOKSY_BUSINESS_ID": "1234567", "ARIA_LIVE_BOOKSY": "1"},
        SpyDriver,
    )
    assert code == cli.EXIT_REFUSED and "confirm-business-id" in text and SpyDriver.created == 0


def test_book_rejects_a_malformed_start_before_any_browser():
    SpyDriver.created = 0
    code, text = run_cli(
        ["book", "--start", "tomorrow", "--confirm-business-id", "1234567"],
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
        ["book", "--start", "2026-10-12 10:00", "--confirm-business-id", "1234567"],
        {"ARIA_BOOKSY_BUSINESS_ID": "1234567", "ARIA_LIVE_BOOKSY": "1", "ARIA_LOCAL_DIR": str(tmp_path)},
        lambda cfg: SpyDriver(cfg),
    )
    assert code == cli.EXIT_REFUSED and "unknown_availability" in text


def test_config_command_masks_the_business_id():
    code, text = run_cli(["config"], {"ARIA_BOOKSY_BUSINESS_ID": "1234567"})
    assert code == cli.EXIT_OK and "1234567" not in text and "****567" in text
