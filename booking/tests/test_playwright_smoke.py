"""REAL Playwright, REAL headful browser, SYNTHETIC local pages only. Nothing here contacts Booksy or any external host.

Mock-only tests cannot show that the threading design works, so this file runs the actual adapter in a real browser and drives it the way the voice
endpoint does: reads are started from per-request worker threads (VoiceTools._driver_read), and requests arrive on real loopback HTTP threads.

Covered: launch (headful), a real read of a synthetic multi-staff calendar through the real capture script and parser, repeated reads, many caller
threads, the voice-tools thread path, the HTTP endpoint, timeout and busy recovery, sign-out, a page that never finishes loading, and cleanup.

The browser is Playwright's own Chromium if installed, else the installed Chrome/Edge (``channel``). Neither available -> the file is skipped with the reason.
LOCAL timings printed with ``-s`` are local synthetic page timings: not live Booksy latency and not end-to-end voice latency.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest

from aria_booking.catalog.bookable import BookableRegistry, BookableService
from aria_booking.config import Config
from aria_booking.driver import DriverError, DriverUnavailable, SignInRequired
from aria_booking.playwright_driver import PlaywrightBooksyDriver, PlaywrightSession
from aria_booking.voice.launch import build_endpoint
from aria_booking.voice.retell_http import RetellEndpoint, make_http_server
from aria_booking.voice.tools import VoiceTools

import html_pages
from multistaff_pages import BASE, REAL_CONTROLS, build_page, roster_items
from test_launch import KEY, TS, signed

pytest.importorskip("playwright.sync_api")
if os.environ.get("ARIA_SKIP_BROWSER_TESTS"):
    pytest.skip("ARIA_SKIP_BROWSER_TESTS is set: the real-browser smoke tests are skipped", allow_module_level=True)

TZ = ZoneInfo("America/New_York")
DAY = date(2026, 10, 12)
NOW = datetime(2026, 10, 8, 12, tzinfo=TZ)
PEOPLE = [
    {"id": "1001", "name": "Lily Chen", "hours": "10AM-8PM", "nonworking": [(540, 600)], "appointments": [(840, 930, "Deep Tissue")]},
    {"id": "1002", "name": "Maya Ortiz", "hours": "10AM-8PM", "nonworking": [(540, 600)]},
    {"id": "1003", "name": "Noor Haddad", "hours": None},
]
ROSTER = [*REAL_CONTROLS, *roster_items([("1001", "Lily Chen"), ("1002", "Maya Ortiz"), ("1003", "Noor Haddad")], select_all=False)]
CALL = "call_smoke"
SERVICE = "massage-deep-tissue-massage-60"


# ---------------------------------------------------------------- a local synthetic "Booksy": one loopback server, per-request behaviours


class Site:
    def __init__(self):
        self.requests: list[str] = []
        self.in_flight = 0
        self.peak = 0
        self.lock = threading.Lock()
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                with site.lock:
                    site.requests.append(self.path)
                    site.in_flight += 1
                    site.peak = max(site.peak, site.in_flight)
                try:
                    if float(query.get("slow", 0)):
                        time.sleep(float(query["slow"]))
                    if parsed.path == "/login":
                        body = "<html><body><h1>Sign in</h1></body></html>"
                        self.send_response(200)
                    elif query.get("signedout"):
                        self.send_response(302)  # what a signed-out visitor gets: sent to the login page, which has no business id in its path
                        self.send_header("Location", "/login")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    else:
                        body = html_pages.page(
                            build_page(PEOPLE), ROSTER, loader_ms=int(query.get("loader", 0)), loader_forever=bool(query.get("forever")),
                        )
                        self.send_response(200)
                    data = body.encode("utf-8")
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with site.lock:
                        site.in_flight -= 1

            def log_message(self, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.extra = ""  # appended to every day URL: e.g. "&slow=2"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url_for_day(self, day_text: str) -> str:
        return f"{self.base}/pro/en-us/1234567/calendar?date={day_text}{self.extra}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(scope="module")
def site():
    s = Site()
    yield s
    s.stop()


def _probe_channel(tmp_path_factory):
    """The browser to use: ARIA_BROWSER_CHANNEL if set, else Playwright's own Chromium, else installed Chrome, else Edge. None = nothing launches."""
    candidates = [os.environ.get("ARIA_BROWSER_CHANNEL") or None] if os.environ.get("ARIA_BROWSER_CHANNEL") else [None, "chrome", "msedge"]
    for channel in candidates:
        cfg = Config(business_id="1234567", local_dir=tmp_path_factory.mktemp("probe"), browser_channel=channel)
        session = LoopbackOnlySession(cfg)
        runner_driver = PlaywrightBooksyDriver(cfg, session_factory=lambda s=session: s, notify=lambda m: None, load_timeout_seconds=15)
        try:
            runner_driver._run(lambda page: page.goto("about:blank"), 30)
            return channel
        except DriverUnavailable:
            continue
        except DriverError:
            continue
        finally:
            runner_driver.close()
    return "NONE"


@pytest.fixture(scope="module")
def channel(tmp_path_factory):
    found = _probe_channel(tmp_path_factory)
    if found == "NONE":
        pytest.skip("no browser Playwright can launch here (run `python -m playwright install chromium`, or set ARIA_BROWSER_CHANNEL=chrome)")
    return found


BLOCKED: list[str] = []  # every request the browser tried to make to anything other than this machine; it is aborted and must stay empty


class LoopbackOnlySession(PlaywrightSession):
    """The real session, plus a guard: the browser can reach ONLY 127.0.0.1 (and about:/data: pages). A test can never touch Booksy or any other site."""

    def _launch(self):
        super()._launch()

        def guard(route):
            url = route.request.url
            if url.startswith(("http://127.0.0.1:", "about:", "data:", "blob:")):
                route.continue_()
            else:
                BLOCKED.append(url.split("?")[0])
                route.abort()

        self._context.route("**/*", guard)


def new_driver(tmp_path, site, channel, **kw):
    cfg = Config(business_id="1234567", local_dir=tmp_path, browser_channel=channel)
    kw.setdefault("load_timeout_seconds", 8)
    driver = PlaywrightBooksyDriver(cfg, session_factory=lambda: LoopbackOnlySession(cfg), notify=lambda m: None, url_for_day=site.url_for_day, **kw)
    return driver, cfg


@pytest.fixture(autouse=True)
def never_left_this_machine():
    yield
    assert BLOCKED == [], f"the browser tried to leave this machine: {BLOCKED}"


@pytest.fixture(scope="module")
def shared(tmp_path_factory, site, channel):
    driver, _cfg = new_driver(tmp_path_factory.mktemp("shared"), site, channel)
    yield driver
    driver.close()


def registry():
    return BookableRegistry([BookableService(SERVICE, "Deep Tissue Massage 60", 60, eligible_staff=frozenset({"lily chen", "maya ortiz", "noor haddad"}), verified=True, catalog_item=SERVICE)])


def make_tools(driver, **kw):
    cfg = Config(business_id="1234567")
    tools = VoiceTools(cfg, driver, lambda *a: (_ for _ in ()).throw(AssertionError("no writer")), lambda: NOW, registry=registry(), require_service_id=True,
                       availability_only=True, **kw)
    return tools


def check(tools, staff="Lily", time_="16:00"):
    return tools.check_slot(CALL, {"service_id": SERVICE, "date": DAY.isoformat(), "time": time_, "staff": staff})


# ---------------------------------------------------------------- launch and one real read


def test_the_browser_really_starts_headful_and_the_synthetic_page_is_captured_exactly(shared):
    agent = shared._run(lambda page: page.evaluate("navigator.userAgent"), 20)
    assert "HeadlessChrome" not in agent, "a visible (headful) browser window"
    assert shared._run(lambda page: page.evaluate("window.outerWidth > 0 && window.outerHeight > 0"), 20) is True
    shared.read_day(DAY)
    raw = shared._run(lambda page: page.evaluate(__import__("aria_booking.playwright_driver", fromlist=["x"]).as_function(
        __import__("aria_booking.discover", fromlist=["x"]).DISCOVERY_JS)), 20)
    source = build_page(PEOPLE)
    keyed = lambda n: (n["tag"], n["depth"], n.get("testid"), n.get("res"), tuple(n["box"]) if "box" in n else (n["x"], n["y"], n["w"], n["h"]))  # noqa: E731
    assert [keyed(n) for n in raw] == [keyed(n) for n in source], "the synthetic HTML reproduces the recorded page structure exactly"


def test_a_real_read_returns_the_multi_staff_calendar_by_staff_id(shared):
    snapshot = shared.read_day(DAY)
    assert [(sd.staff, sd.staff_id) for sd in snapshot.staff_days] == [("Lily Chen", "1001"), ("Maya Ortiz", "1002"), ("Noor Haddad", "1003")]
    assert snapshot.staff_days[2].working is None, "a header with no hours stays unknown"
    assert [a.interval.start.hour for a in snapshot.staff_days[0].appointments] == [14]


def test_the_phase_timings_come_from_native_waits_and_the_owner_hand_off(shared):
    shared.read_day(DAY)
    ms = shared.last_read_ms
    assert {"navigate", "page_ready", "paint_wait", "capture", "roster", "parse", "launch", "runner_wait"} <= set(ms)
    assert ms["paint_wait"] < 3000, "the page settled by a native wait, not by a fixed pause"


# ---------------------------------------------------------------- repeated reads and many callers


def owners() -> int:
    return sum(1 for t in threading.enumerate() if t.name == "playwright-owner")


def test_repeated_reads_reuse_one_browser_one_window_and_one_thread(tmp_path, site, channel):
    before = owners()
    driver, _ = new_driver(tmp_path, site, channel)
    try:
        for _i in range(8):
            assert len(driver.read_day(DAY).staff_days) == 3
        assert driver._session.launches == 1
        assert driver._session._context is not None and len(driver._session._context.pages) == 1
        assert owners() - before == 1, "one owner thread for this driver, however many reads"
        assert driver.runner_stats["run"] >= 8
    finally:
        driver.close()


def test_reads_started_from_many_threads_all_succeed_one_at_a_time(tmp_path, site, channel):
    driver, _ = new_driver(tmp_path, site, channel)
    site.peak = 0
    results, errors = [], []

    def read():
        try:
            results.append(len(driver.read_day(DAY).staff_days))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    try:
        threads = [threading.Thread(target=read) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert not errors and results == [3] * 8
        assert site.peak == 1, "the one browser never loaded two pages at once"
    finally:
        driver.close()


# ---------------------------------------------------------------- the voice endpoint's own thread path


def test_reads_started_the_way_the_voice_tools_start_them_work_on_a_real_browser(tmp_path, site, channel):
    driver, _ = new_driver(tmp_path, site, channel)
    tools = make_tools(driver, read_deadline_seconds=20)
    seen = []
    original = driver.read_day
    driver.read_day = lambda day, include_notes=False: (seen.append(threading.current_thread().name), original(day, include_notes))[1]
    try:
        first = check(tools)
        second = check(tools, "Maya")
        assert first["status"] == "available" and first["options"][0]["technician"] == "Lily Chen", first
        assert second["status"] == "available" and second["options"][0]["technician"] == "Maya Ortiz", second
        assert seen and set(seen) == {"calendar-read"}, "each read was started from VoiceTools' own worker thread, not the caller's or the owner's"
        busy = check(tools, "Lily", "14:30")  # Lily is booked 14:00-15:30
        assert busy["status"] == "alternatives" and busy["unavailable_reason"] == "occupied"
        noor = check(tools, "Noor")
        assert noor["status"] == "unknown", "no hours shown for Noor: unknown, never available"
    finally:
        driver.close()


def test_the_http_endpoint_serves_concurrent_requests_through_the_real_browser(tmp_path, site, channel):
    driver, _ = new_driver(tmp_path, site, channel)
    tools = make_tools(driver, read_deadline_seconds=20, lock_timeout_seconds=30)
    endpoint = RetellEndpoint(tools, KEY, now_ms=lambda: TS)
    server = make_http_server(endpoint, 0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    replies, errors = [], []

    def post(route, args):
        body = json.dumps({"call": {"call_id": CALL}, "args": args}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{port}/tools/{route}", data=body, headers={**signed(body), "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())

    def worker(who):
        try:
            replies.append(post("check_slot", {"service_id": SERVICE, "date": DAY.isoformat(), "time": "16:00", "staff": who}))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    try:
        threads = [threading.Thread(target=worker, args=(who,)) for who in ("Lily", "Maya", "Lily", "Maya")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(90)
        assert not errors and len(replies) == 4 and all(r["status"] == "available" for r in replies), replies
    finally:
        server.shutdown()
        server.server_close()
        driver.close()


# ---------------------------------------------------------------- native waits


def test_a_loading_overlay_that_clears_is_waited_for_natively(tmp_path, site, channel):
    driver, _ = new_driver(tmp_path, site, channel)
    site.extra = "&loader=700"
    try:
        began = time.monotonic()
        assert len(driver.read_day(DAY).staff_days) == 3
        assert driver.last_read_ms["page_ready"] >= 500 and time.monotonic() - began < 8
    finally:
        site.extra = ""
        driver.close()


def test_a_page_that_never_finishes_loading_is_refused_within_the_load_budget(tmp_path, site, channel):
    from aria_booking.calendar_parser import CalendarParseError

    driver, _ = new_driver(tmp_path, site, channel, load_timeout_seconds=2)
    site.extra = "&forever=1"
    try:
        began = time.monotonic()
        with pytest.raises(CalendarParseError, match="still loading"):
            driver.read_day(DAY)
        assert time.monotonic() - began < 6
        site.extra = ""
        assert len(driver.read_day(DAY).staff_days) == 3, "and the same browser reads normally afterwards"
    finally:
        site.extra = ""
        driver.close()


def test_a_signed_out_page_is_reported_as_sign_in_required(tmp_path, site, channel):
    driver, _ = new_driver(tmp_path, site, channel)
    site.extra = "&signedout=1"
    try:
        with pytest.raises(SignInRequired):
            driver.read_day(DAY)
    finally:
        site.extra = ""
        driver.close()


# ---------------------------------------------------------------- timeout and busy recovery, through the voice tools' thread path


def test_a_slow_page_times_out_cleanly_blocks_new_reads_then_recovers_with_no_late_data(tmp_path, site, channel):
    driver, _ = new_driver(tmp_path, site, channel, load_timeout_seconds=2.5)
    tools = make_tools(driver, read_deadline_seconds=1.0, request_budget_seconds=1.0, read_cache_seconds=60)
    site.extra = "&slow=3"
    site.peak = 0
    try:
        began = time.monotonic()
        late = check(tools)
        assert time.monotonic() - began < 2.5
        assert late["status"] == "unknown" and late["reason"] == "read_timeout" and "options" not in late
        again = check(tools)  # the abandoned read still holds the one browser
        assert again["reason"] == "read_timeout"
        assert tools._abandoned is not None
        tools._abandoned.join(15)  # the driver's own native timeout ends it
        assert not tools._abandoned.is_alive()
        assert tools._day_cache == {}, "nothing from the abandoned read was cached"
        site.extra = ""
        ok = check(tools)
        assert ok["status"] == "available", ok
        assert site.peak <= 2 and driver.runner_stats["expired"] == 0
    finally:
        site.extra = ""
        driver.close()


def test_overlapping_requests_get_busy_and_the_next_request_succeeds(tmp_path, site, channel):
    driver, _ = new_driver(tmp_path, site, channel)
    tools = make_tools(driver, read_deadline_seconds=20, lock_timeout_seconds=0.2)
    site.extra = "&loader=1200"
    first_reply = []
    try:
        first = threading.Thread(target=lambda: first_reply.append(check(tools)))
        first.start()
        deadline = time.monotonic() + 10
        while not any("loader=1200" in r for r in site.requests[-3:]) and time.monotonic() < deadline:
            time.sleep(0.02)
        started = time.monotonic()
        second = check(tools, "Maya")
        assert second["status"] == "busy" and time.monotonic() - started < 1.0
        first.join(30)
        assert first_reply and first_reply[0]["status"] == "available"
        site.extra = ""
        assert check(tools, "Maya")["status"] == "available"
    finally:
        site.extra = ""
        driver.close()


# ---------------------------------------------------------------- cleanup


def test_close_stops_the_owner_thread_and_the_browser_and_is_idempotent(tmp_path, site, channel):
    before = owners()
    driver, _ = new_driver(tmp_path, site, channel)
    driver.read_day(DAY)
    session = driver._session
    assert owners() - before == 1
    driver.close()
    driver.close()
    assert owners() == before, "its owner thread has exited"
    assert session._context is None and session._pw is None, "context and Playwright were closed on the owner thread"
    with pytest.raises(DriverError):
        driver.read_day(DAY)


def test_the_profile_lives_in_the_apps_own_folder_and_the_sign_in_survives_a_restart(tmp_path, site, channel):
    first, cfg = new_driver(tmp_path, site, channel)
    first.read_day(DAY)
    first._run(lambda page: page.evaluate("document.cookie = 'aria_smoke=1; max-age=3600; path=/'"), 20)
    first.close()
    assert (tmp_path / "playwright-profile").is_dir()
    second = PlaywrightBooksyDriver(cfg, session_factory=lambda: LoopbackOnlySession(cfg), notify=lambda m: None, url_for_day=site.url_for_day, load_timeout_seconds=8)
    try:
        second.read_day(DAY)
        assert "aria_smoke=1" in second._run(lambda page: page.evaluate("document.cookie"), 20), "the app-owned profile persisted its own state"
    finally:
        second.close()


# ---------------------------------------------------------------- LOCAL timings (synthetic page; not live Booksy, not end-to-end voice)


def test_local_synthetic_timings_are_reported_separately_from_live_latency(shared, capsys):
    cold = []
    warm = []
    for index in range(5):
        began = time.monotonic()
        shared.read_day(DAY)
        (cold if index == 0 else warm).append((time.monotonic() - began) * 1000)
    median = sorted(warm)[len(warm) // 2]
    with capsys.disabled():
        print(f"\nLOCAL synthetic page read (NOT live Booksy, NOT end-to-end voice): first-in-module {cold[0]:.0f} ms, "
              f"later reads median {median:.0f} ms, phases {shared.last_read_ms}")
    assert median < 5000
