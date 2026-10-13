"""Review PW1 (Sol): a URL is never proof of sign-in. The first two tests are Sol's reproduction, ported unchanged in substance (fake pages, no browser,
no network); the rest extend it. Success needs AFFIRMATIVE evidence that the signed-in business calendar is on screen, for the CONFIGURED business;
anything else is refused or waits honestly for a person. Appointment creation stays disabled throughout.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aria_booking.config import Config
from aria_booking.driver import AccessDenied, DriverError, SignInRequired
from aria_booking.playwright_driver import AUTHENTICATED_FN, PlaywrightBooksyDriver, PlaywrightTimeout, refuse_if_denied

from test_playwright_driver import FakePage, FakeSession, make, multi


class UnauthenticatedPage(FakePage):  # Sol's
    def __init__(self, status, body):
        super().__init__([], None, url="https://example.invalid/pro/en-us/1234567/calendar")
        self.status, self.body = status, body

    def goto(self, *args, **kwargs):
        super().goto(*args, **kwargs)
        return SimpleNamespace(status=self.status, ok=self.status < 400)

    def content(self):
        return self.body

    def wait_for_function(self, *args, **kwargs):
        raise PlaywrightTimeout("No authenticated calendar ever appears")

    def wait_for_url(self, *args, **kwargs):
        raise PlaywrightTimeout("No authenticated navigation ever occurs")


@pytest.mark.parametrize("status,body", [
    (200, '<html><body><div id="app">Starting application...</div></body></html>'),
    (403, '<html><body>Request blocked by anti-bot protection</body></html>'),
])
def test_business_url_without_authenticated_calendar_is_not_a_successful_login(status, body):  # Sol's
    driver = make(UnauthenticatedPage(status, body))
    try:
        with pytest.raises(DriverError):
            driver.verify_business()
    finally:
        driver.close()


# ---------------------------------------------------------------- what is refused, and how


class Scripted(FakePage):
    """A page whose navigation answers with a given status and whose authenticated evidence can be switched."""

    def __init__(self, status=200, *, evidence=True, url="https://example.invalid/pro/en-us/1234567/calendar"):
        super().__init__([], None, url=url)
        self.status, self.evidence = status, evidence

    def goto(self, *args, **kwargs):
        super().goto(*args, **kwargs)
        return SimpleNamespace(status=self.status)

    def wait_for_function(self, expression, arg=None, timeout=None):
        self.note("wait_for_function")
        self.args.append(("wait_for_function", expression, arg, timeout))
        if expression == AUTHENTICATED_FN and not self.evidence:
            raise PlaywrightTimeout("no calendar")


def driver_for(page, **config):
    return PlaywrightBooksyDriver(Config(business_id="1234567", **config), session_factory=lambda: FakeSession(page), notify=lambda m: None,
                                  url_for_day=lambda d: "https://example.invalid/x")


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503])
def test_an_http_error_is_refused_at_once_without_waiting_or_retrying(status):
    page = Scripted(status)
    driver = driver_for(page)
    with pytest.raises(AccessDenied, match=f"HTTP {status}"):
        driver.verify_business()
    names = [n for n, _t in page.calls]
    assert names.count("goto") == 1 and "wait_for_function" not in names and "bring_to_front" not in names, "no waiting, no second attempt"
    driver.close()


def test_the_refusal_says_nothing_is_retried_and_nothing_is_worked_around():
    with pytest.raises(AccessDenied) as info:
        refuse_if_denied(SimpleNamespace(status=403))
    text = str(info.value)
    assert "bot protection" in text and "Nothing was retried" in text and "nothing is worked around" in text and "1234567" not in text


@pytest.mark.parametrize("response", [None, SimpleNamespace(status=200), SimpleNamespace(status=204), SimpleNamespace(status=302), SimpleNamespace()])
def test_a_normal_or_unknown_response_is_not_a_refusal(response):
    refuse_if_denied(response)


def test_a_business_url_with_a_200_app_shell_waits_the_whole_window_and_then_times_out_honestly():
    page = Scripted(200, evidence=False)
    messages = []
    driver = PlaywrightBooksyDriver(Config(business_id="1234567", sign_in_wait_seconds=45), session_factory=lambda: FakeSession(page), notify=messages.append,
                                    url_for_day=lambda d: "https://example.invalid/x")
    with pytest.raises(SignInRequired, match="signed-in calendar never appeared"):
        driver.verify_business()
    waits = [a[3] for a in page.args if a[0] == "wait_for_function" and a[1] == AUTHENTICATED_FN]
    assert waits == [8000, 45_000], "a short first look, then the person's window; keeps the manual sign-in possible"
    assert len(messages) == 1 and page.fronted == 1
    driver.close()


def test_the_evidence_needs_the_calendar_itself_not_only_the_url():
    assert "app-loader" in AUTHENTICATED_FN and "side-menu" in AUTHENTICATED_FN and "calendar-grid-day" in AUTHENTICATED_FN
    assert AUTHENTICATED_FN.count("&&") == 2 and AUTHENTICATED_FN.lstrip().startswith("() =>")


def test_evidence_on_a_page_that_is_not_a_business_page_is_not_success():
    page = Scripted(200, url="https://example.invalid/login")
    driver = driver_for(page)
    with pytest.raises(SignInRequired, match="not on a business page"):
        driver.verify_business()
    driver.close()


def test_a_different_business_is_refused_even_with_the_calendar_showing_and_no_id_is_printed():
    page = Scripted(200, url="https://example.invalid/pro/en-us/7654321/calendar")
    driver = driver_for(page)
    with pytest.raises(DriverError, match="DIFFERENT business") as info:
        driver.verify_business()
    assert "7654321" not in str(info.value) and "1234567" not in str(info.value)
    driver.close()


def test_the_configured_business_with_the_calendar_showing_succeeds():
    driver = driver_for(Scripted(200))
    assert driver.verify_business() == "1234567"
    driver.close()


def test_a_missing_business_id_in_the_configuration_is_refused_before_any_browser_work():
    page = Scripted(200)
    driver = PlaywrightBooksyDriver(Config(business_id=""), session_factory=lambda: FakeSession(page), notify=lambda m: None, url_for_day=lambda d: "x")
    from aria_booking.config import ConfigError

    with pytest.raises(ConfigError):  # the CLI reports it as a configuration refusal
        driver.verify_business()
    assert page.calls == []
    driver.close()


# ---------------------------------------------------------------- a calendar READ also refuses an error page by name


def test_a_day_read_that_gets_an_http_error_says_so_instead_of_reporting_a_parse_failure():
    page = multi()
    page.goto = lambda url, wait_until=None, timeout=None: (page.note("goto"), SimpleNamespace(status=403))[1]
    driver = make(page)
    with pytest.raises(AccessDenied, match="HTTP 403"):
        driver.read_day(__import__("datetime").date(2026, 10, 12))
    assert "evaluate" not in [n for n, _t in page.calls], "nothing was captured from a refused page"
    driver.close()


# ---------------------------------------------------------------- still read-only


def test_appointment_creation_is_still_refused_before_any_ui_action_after_a_successful_sign_in():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from aria_booking.driver import BeforeSaveError
    from aria_booking.models import AppointmentSpec

    page = Scripted(200)
    driver = driver_for(page)
    driver.verify_business()
    before = list(page.calls)
    spec = AppointmentSpec("Lily Chen", "Aria Salon", datetime(2026, 10, 12, 11, 0, tzinfo=ZoneInfo("America/New_York")), 150, "n")
    with pytest.raises(BeforeSaveError, match="read-only"):
        driver.create_appointment(spec)
    assert page.calls == before
    driver.close()


def test_the_first_look_is_never_longer_than_the_page_load_budget():
    page = Scripted(200, evidence=False)
    driver = PlaywrightBooksyDriver(Config(business_id="1234567", sign_in_wait_seconds=20), session_factory=lambda: FakeSession(page), notify=lambda m: None,
                                    url_for_day=lambda d: "x", load_timeout_seconds=2)
    with pytest.raises(SignInRequired):
        driver.verify_business()
    assert [a[3] for a in page.args if a[1] == AUTHENTICATED_FN] == [2000, 20_000]
    driver.close()
