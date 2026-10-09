"""Which browser adapter each command uses. The read-only commands (login, find, serve without booking) use Playwright by default; everything that
clicks or discovers stays on Selenium; booking can never be served by the read-only Playwright adapter. No browser is started here."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from aria_booking import cli
from aria_booking.voice import retell_http

from test_voice_http import ENV, FIVE, SERVE, StubServer

NOW = datetime(2026, 10, 8, 12, tzinfo=ZoneInfo("America/New_York"))


class Recorder:
    """Stands in for either driver class: records how it was built and used."""

    instances: list = []

    def __init__(self, cfg, *args, **kwargs):
        self.kind = type(self).__name__
        self.cfg, self.kwargs = cfg, kwargs
        self.closed = 0
        self.verified = 0
        Recorder.instances.append(self)

    def verify_business(self):
        self.verified += 1
        return "1234567"

    def close(self):
        self.closed += 1

    def read_day(self, day, include_notes=False):  # pragma: no cover - find is exercised through BookingService elsewhere
        raise AssertionError("not expected")


class FakePlaywright(Recorder):
    pass


class FakeSelenium(Recorder):
    def __init__(self, cfg, approvals=frozenset(), **kwargs):
        super().__init__(cfg, approvals=approvals, **kwargs)
        self.approvals = approvals


@pytest.fixture(autouse=True)
def patched(monkeypatch):
    Recorder.instances = []
    monkeypatch.setattr(cli, "PlaywrightBooksyDriver", FakePlaywright)
    monkeypatch.setattr(cli, "SeleniumBooksyDriver", FakeSelenium)
    monkeypatch.setattr(retell_http, "make_http_server", lambda endpoint, port, host="127.0.0.1": StubServer())
    StubServer.served = StubServer.closed = False


def run(argv, env=ENV):
    lines = []
    code = cli.main(argv, environ=env, clock=lambda: NOW, out=lines.append)
    return code, "\n".join(lines)


# ---------------------------------------------------------------- the defaults


def test_login_defaults_to_the_playwright_adapter_and_closes_it():
    code, text = run(["login"])
    (driver,) = Recorder.instances
    assert code == cli.EXIT_OK and driver.kind == "FakePlaywright" and driver.verified == 1 and driver.closed == 1
    assert "signed in" in text


def test_login_can_still_use_selenium_when_asked():
    run(["login", "--browser", "selenium"])
    assert [d.kind for d in Recorder.instances] == ["FakeSelenium"]


def test_read_only_serve_defaults_to_playwright_with_the_short_page_wait():
    code, text = run(SERVE)
    (driver,) = Recorder.instances
    assert code == cli.EXIT_OK and driver.kind == "FakePlaywright"
    assert driver.kwargs["load_timeout_seconds"] == cli.SERVE_LOAD_TIMEOUT
    assert "READ-ONLY" in text and "BOOKING ENABLED" not in text


def test_read_only_serve_can_be_forced_back_to_selenium_for_comparison():
    run(SERVE + ["--browser", "selenium"])
    assert [d.kind for d in Recorder.instances] == ["FakeSelenium"]


def test_find_defaults_to_playwright(monkeypatch):
    monkeypatch.setattr(cli, "BookingService", lambda *a, **k: type("S", (), {"search": lambda self, day, days: []})())
    run(["find", "--days", "1"])
    assert [d.kind for d in Recorder.instances] == ["FakePlaywright"]


# ---------------------------------------------------------------- booking and discovery are not migrated


def test_serving_with_booking_approvals_stays_on_selenium():
    code, text = run(SERVE + FIVE)
    (driver,) = Recorder.instances
    assert code == cli.EXIT_OK and driver.kind == "FakeSelenium" and "BOOKING ENABLED" in text
    assert driver.approvals == frozenset(cli.BOOK_APPROVALS)


def test_asking_for_playwright_on_a_booking_server_is_refused_before_any_browser_exists():
    code, text = run(SERVE + FIVE + ["--browser", "playwright"])
    assert code == cli.EXIT_REFUSED and "booking is not available on the Playwright adapter" in text
    assert Recorder.instances == [] and not StubServer.served


@pytest.mark.parametrize("argv", [
    ["discover"], ["discover-steps", "--allow", "tour"], ["verify", "--start", "2026-10-12 11:00", "--confirm-business-id", "1234567", "--approve-tour-popups",
                                                          "--approve-note-readback"],
])
def test_discovery_and_verify_stay_on_selenium(argv):
    try:
        run(argv)
    except Exception:  # the fake cannot discover; only the adapter chosen matters here
        pass
    assert Recorder.instances and {d.kind for d in Recorder.instances} == {"FakeSelenium"}


def test_these_commands_do_not_even_offer_a_browser_choice():
    for argv in (["discover", "--browser", "playwright"], ["book", "--start", "2026-10-12 10:00", "--confirm-business-id", "1234567", "--browser", "playwright"]):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(argv)


@pytest.mark.parametrize("argv,expected", [
    (["login"], "playwright"), (["find"], "playwright"), (["serve", "--confirm-business-id", "1", "--port", "1"], "playwright"),
    (["discover"], "selenium"), (["config"], "selenium"),
])
def test_browser_for_picks_the_adapter_by_command(argv, expected):
    args = cli.build_parser().parse_args(argv)
    args.booking_enabled = False
    assert cli._browser_for(args) == expected


def test_a_booking_serve_is_never_given_the_read_only_adapter():
    args = cli.build_parser().parse_args(["serve", "--confirm-business-id", "1", "--port", "1", "--browser", "playwright"])
    args.booking_enabled = True
    assert cli._browser_for(args) == "refused"
    args.browser = None
    assert cli._browser_for(args) == "selenium"


# ---------------------------------------------------------------- configuration


def test_the_browser_channel_comes_from_the_environment_and_shows_in_the_config_summary(tmp_path):
    from aria_booking.config import Config

    assert Config.from_env({"ARIA_BROWSER_CHANNEL": " chrome "}).browser_channel == "chrome"
    assert Config.from_env({}).browser_channel is None and Config.from_env({"ARIA_BROWSER_CHANNEL": "  "}).browser_channel is None
    assert "Playwright's own Chromium" in Config().redacted_summary()["playwright_browser"]
    assert Config(browser_channel="chrome").redacted_summary()["playwright_browser"] == "chrome"
