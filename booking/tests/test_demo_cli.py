"""demo-serve: a demo that can never be confused with, or fall back to, the real calendar. No browser, no network."""

from __future__ import annotations

import ast
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aria_booking import cli
from aria_booking.demo import DEMO_BUSINESS_ID, DEMO_LABEL
from aria_booking.demo import server as demo_server
from aria_booking.driver import SignInRequired
from aria_booking.voice import retell_http
from aria_booking.voice.retell_http import READ_ONLY_ROUTES

from test_launch import KEY

BOOKING = Path(__file__).resolve().parent.parent
ENV = {"ARIA_RETELL_API_KEY": KEY}  # note: no business id, no ARIA_LIVE_BOOKSY


class FakeServer:
    served = closed = False

    def __init__(self, *a, **k):
        FakeServer.served = FakeServer.closed = False

    def serve_forever(self):
        FakeServer.served = True
        raise KeyboardInterrupt

    def shutdown(self):
        pass

    def server_close(self):
        FakeServer.closed = True


class FakeSite(FakeServer):
    started = False

    def serve_forever(self):  # the page server runs on a thread in the real command
        FakeSite.started = True


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    built = {}
    monkeypatch.setattr(demo_server, "make_site", lambda dataset, port, host="127.0.0.1": built.setdefault("site", FakeSite()))
    monkeypatch.setattr(retell_http, "make_http_server", lambda endpoint, port, host="127.0.0.1": built.setdefault("endpoint", endpoint) and FakeServer())

    def boom(*args, **kwargs):
        raise AssertionError("the demo must never build a Booksy browser driver")

    monkeypatch.setattr(cli, "SeleniumBooksyDriver", boom)
    monkeypatch.setattr(cli, "PlaywrightBooksyDriver", boom)
    FakeSite.started = False
    return built


def run(argv, env=ENV):
    lines = []
    code = cli.main(argv, environ=env, clock=lambda: datetime(2026, 10, 13, 12, tzinfo=ZoneInfo("America/New_York")), out=lines.append)
    return code, "\n".join(lines)


# ---------------------------------------------------------------- refusals before anything starts


def test_a_key_is_required_and_is_never_printed():
    code, text = run(["demo-serve"], env={})
    assert code == cli.EXIT_REFUSED and "ARIA_RETELL_API_KEY" in text and KEY not in text


@pytest.mark.parametrize("argv", [["--port", "0"], ["--port", "70000"], ["--site-port", "0"], ["--port", "9000", "--site-port", "9000"]])
def test_the_two_ports_must_be_valid_and_different(argv):
    code, text = run(["demo-serve", *argv])
    assert code == cli.EXIT_REFUSED and "must be different numbers" in text


def test_a_bad_demo_date_is_refused():
    code, text = run(["demo-serve", "--today", "tomorrow"])
    assert code == cli.EXIT_REFUSED and "--today must look like" in text


def test_the_booking_approvals_do_not_exist_on_this_command():
    for flag in cli.BOOK_APPROVALS:
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["demo-serve", f"--approve-{flag}"])


# ---------------------------------------------------------------- what starting says


def test_it_starts_without_a_business_id_or_the_live_switch_and_says_plainly_what_it_is(isolated):
    code, text = run(["demo-serve", "--today", "2026-10-13"])
    assert code == cli.EXIT_OK and FakeServer.served and FakeSite.started is False or FakeServer.served
    assert DEMO_LABEL.upper() in text and "NOT Booksy" in text and "nothing here can book, hold, change or cancel" in text
    assert f"/pro/en-us/{DEMO_BUSINESS_ID}/calendar" in text and "the demo dataset directly" in text
    assert "demo date 2026-10-13; scripted days 2026-10-14 and 2026-10-15" in text
    assert KEY not in text


def test_the_bearer_token_from_the_environment_reaches_the_endpoint(isolated):
    run(["demo-serve", "--today", "2026-10-13"], env={**ENV, "ARIA_RETELL_TOOL_TOKEN": "  env-token  "})
    assert isolated["endpoint"]._bearer == "env-token"


def test_without_a_token_the_endpoint_has_none_and_nothing_is_created(isolated, tmp_path):
    run(["demo-serve", "--today", "2026-10-13"])
    assert isolated["endpoint"]._bearer is None and list(tmp_path.iterdir()) == []


def test_the_key_and_a_stable_token_can_come_from_the_encrypted_file_folder_instead(isolated, monkeypatch, tmp_path):
    from aria_booking.demo import secret_store

    vault = secret_store.DpapiSecrets(tmp_path / "vault", crypt=lambda raw, encrypt: b"E" + raw if encrypt else raw[1:])
    vault.put("retell_api_key", KEY)
    monkeypatch.setattr(secret_store, "provider_from_environment", lambda environ=None: vault)
    code, text = run(["demo-serve", "--today", "2026-10-13"], env={"ARIA_SECRET_DIR": "x"})
    assert code == cli.EXIT_OK and KEY not in text
    first = isolated["endpoint"]._bearer
    assert first == vault.get("tool_token") and len(first) >= 40
    run(["demo-serve", "--today", "2026-10-13"], env={"ARIA_SECRET_DIR": "x"})
    assert isolated["endpoint"]._bearer == first, "the token is generated once and then reused, so the server and the agent agree"


def test_a_missing_key_in_the_encrypted_folder_is_the_same_refusal(monkeypatch, tmp_path):
    from aria_booking.demo import secret_store

    monkeypatch.setattr(secret_store, "provider_from_environment", lambda environ=None: secret_store.DpapiSecrets(tmp_path, crypt=lambda raw, encrypt: raw))
    code, text = run(["demo-serve"], env={"ARIA_SECRET_DIR": "x"})
    assert code == cli.EXIT_REFUSED and "ARIA_RETELL_API_KEY" in text and list(tmp_path.iterdir()) == [], "a refusal writes nothing, not even a token"


def test_the_endpoint_it_builds_is_read_only_and_labelled(isolated):
    run(["demo-serve", "--today", "2026-10-13"])
    endpoint = isolated["endpoint"]
    assert endpoint._routes == READ_ONLY_ROUTES and not endpoint._tools.booking_enabled
    assert endpoint._tools._source_label == "demo_calendar" and endpoint._tools._source_note is None, "labelled in the data, not announced aloud"
    assert endpoint._tools._providers is not None, "the label still gates the demo-only provider lookup"


def test_the_default_demo_date_is_the_real_today(isolated):
    run(["demo-serve"])
    assert isolated["endpoint"]._tools._now().date().isoformat() == datetime.now(ZoneInfo("America/New_York")).date().isoformat()


# ---------------------------------------------------------------- no Booksy, no fall-back, in either direction


class PageDriver:
    """Stands in for the Playwright reader pointed at the demo page."""

    instances: list = []
    fail = False

    def __init__(self, cfg, **kwargs):
        self.cfg, self.kwargs, self.closed = cfg, kwargs, 0
        PageDriver.instances.append(self)

    def verify_business(self):
        if PageDriver.fail:
            raise SignInRequired("the window never showed the calendar")
        return DEMO_BUSINESS_ID

    def close(self):
        self.closed += 1


def test_via_page_reads_the_demo_page_with_the_live_reader_in_the_demo_profile(monkeypatch, isolated):
    PageDriver.instances, PageDriver.fail = [], False
    monkeypatch.setattr(cli, "PlaywrightBooksyDriver", PageDriver)
    code, text = run(["demo-serve", "--via-page", "--site-port", "18790"])
    (driver,) = PageDriver.instances
    assert code == cli.EXIT_OK and driver.cfg.business_id == DEMO_BUSINESS_ID and driver.cfg.local_dir.name == "demo"
    assert driver.kwargs["url_for_day"]("2026-10-14").startswith("http://127.0.0.1:18790/pro/en-us/9000001/calendar?date=2026-10-14")
    assert "the demo PAGE through a Playwright window" in text and driver.closed == 1
    assert isolated["endpoint"]._tools.driver is driver


def test_via_page_that_cannot_start_stops_instead_of_answering_from_the_dataset(monkeypatch, isolated):
    PageDriver.instances, PageDriver.fail = [], True
    monkeypatch.setattr(cli, "PlaywrightBooksyDriver", PageDriver)
    code, text = run(["demo-serve", "--via-page"])
    assert code == cli.EXIT_REFUSED and "stopped" in text and "never showed the calendar" in text
    assert "endpoint" not in isolated and not FakeServer.served, "no server was started, so nothing answered from anywhere"
    assert PageDriver.instances[0].closed == 1


def test_a_page_that_names_another_business_is_refused(monkeypatch, isolated):
    class Other(PageDriver):
        def verify_business(self):
            return "1234567"

    monkeypatch.setattr(cli, "PlaywrightBooksyDriver", Other)
    code, text = run(["demo-serve", "--via-page"])
    assert code == cli.EXIT_REFUSED and "did not identify itself as the demo business" in text and "endpoint" not in isolated


def test_the_demo_command_never_calls_a_booksy_driver_class():
    source = (BOOKING / "aria_booking" / "cli.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    demo = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_demo_serve")
    names = {n.id for n in ast.walk(demo) if isinstance(n, ast.Name)}
    assert "SeleniumBooksyDriver" not in names, "the demo has no way to build the live Selenium adapter"
    assert "PlaywrightBooksyDriver" in names, "the only browser it can build reads the DEMO page, and only with --via-page"
    assert "build_demo" in names


def test_the_live_serve_and_its_helpers_never_touch_the_demo():
    tree = ast.parse((BOOKING / "aria_booking" / "cli.py").read_text(encoding="utf-8"))
    for fn in ("_serve", "_run", "_browser_for"):
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn)
        text = ast.unparse(node)
        assert "demo" not in text.lower(), fn
    top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("demo" in (getattr(n, "module", "") or "") for n in top_level), "the demo package is imported only inside the demo commands"


def test_demo_data_cannot_reach_the_live_tool_wiring():
    for relative in ("aria_booking/voice/tools.py", "aria_booking/voice/retell_http.py", "aria_booking/voice/launch.py", "aria_booking/playwright_driver.py",
                     "aria_booking/selenium_driver.py"):
        tree = ast.parse((BOOKING / relative).read_text(encoding="utf-8"))
        modules = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(".demo" in m or m.startswith("demo") for m in modules), relative


# ---------------------------------------------------------------- the picture commands


def test_demo_compare_with_a_missing_reference_says_so_and_does_nothing_else():
    code, text = run(["demo-compare", "--reference", str(BOOKING / "does-not-exist.png")])
    assert code == cli.EXIT_REFUSED and "stopped" in text


def test_demo_screenshots_rejects_a_bad_date():
    code, text = run(["demo-screenshots", "--out", "x", "--today", "soon"])
    assert code == cli.EXIT_REFUSED and "--today must look like" in text


def test_demo_day_prints_the_scripted_day_and_refuses_what_is_not_covered():
    code, text = run(["demo-day", "--today", "2026-10-13"])
    assert code == cli.EXIT_OK and "2026-10-14 (Wednesday)" in text and "Sienna" in text and re.search(r"not working: .*Imogen", text)
    assert run(["demo-day", "--today", "2026-10-13", "--date", "2026-10-15"])[0] == cli.EXIT_OK
    code, text = run(["demo-day", "--today", "2026-10-13", "--date", "2027-01-01"])
    assert code == cli.EXIT_REFUSED and "covers" in text
    assert run(["demo-day", "--date", "soon"])[0] == cli.EXIT_REFUSED


def test_the_diagnostic_incomplete_calendar_flag_is_off_by_default_and_marks_one_column_when_chosen(isolated):
    run(["demo-serve", "--today", "2026-10-13"])
    assert isolated["endpoint"]._tools.driver.dataset.diagnostic_unknown_hours == frozenset(), "the normal demo's schedules are complete"
    isolated.clear()
    run(["demo-serve", "--today", "2026-10-13", "--diagnostic-incomplete-calendar"])
    assert isolated["endpoint"]._tools.driver.dataset.diagnostic_unknown_hours == frozenset({"910010"})
