"""The public bundle can be built SAME-ORIGIN: the page's calendar and open times are on the address it is served from, whatever that is, so one build serves a draft and the live site (a
build pinned to an origin would send a draft's requests to the live site). Checked on the built files and in a REAL browser served from one local port that also answers the calendar's
paths, with the page's own origin the only one it talks to. No call, no network, no paid anything."""

from __future__ import annotations

import functools
import threading
import urllib.request
from datetime import date, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import pytest

from aria_booking.config import Config
from aria_booking.demo.dataset import DemoDataset
from aria_booking.demo.open_times import open_times
from aria_booking.demo.server import make_site
from aria_booking.playwright_driver import PlaywrightBooksyDriver
from aria_booking.showcase.site import SAME_ORIGIN, build_site, calendar_link, calendar_origin

from test_playwright_smoke import LoopbackOnlySession, channel, never_left_this_machine  # noqa: F401  (fixtures)
from test_showcase_views_browser import SDK_STUB, SDK_URL, SETTINGS, SWEDISH60, open_calendar, pick

TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 14)
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)
TOMORROW = TODAY + timedelta(days=1)


def test_the_helpers_give_an_empty_origin_and_relative_links_for_a_same_origin_build():
    assert calendar_origin(8790, SAME_ORIGIN) == ""
    assert calendar_link(8790, SAME_ORIGIN).startswith("/pro/en-us/9000001/calendar?")
    assert calendar_origin(8790, "https://example.netlify.app") == "https://example.netlify.app"  # a pinned build is unchanged
    assert calendar_origin(8790) == "http://localhost:8790"  # and so is the local demo


def test_a_same_origin_bundle_points_at_no_address_at_all(tmp_path):
    out = tmp_path / "site"
    build_site(out, settings=SETTINGS, origin=SAME_ORIGIN, static_host=True, poll_mode="call")
    html = (out / "index.html").read_text(encoding="utf-8")
    assert '"calendarOrigin": ""' in html and 'data-src="/pro/en-us/9000001/calendar' in html
    assert '"pollMode": "call"' in html
    for path in out.rglob("*"):
        if path.is_file() and path.suffix in {".html", ".js", ".css"}:
            text = path.read_text(encoding="utf-8")
            assert "localhost" not in text and "127.0.0.1" not in text, path.name
    views = (out / "views.js").read_text(encoding="utf-8")
    assert "if (!data.calendarOrigin) data.calendarOrigin = window.location.origin;" in views


def test_the_builder_defaults_to_same_origin_and_still_refuses_a_bad_address(tmp_path, capsys):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("build_site_script", Path(__file__).resolve().parents[1] / "netlify_showcase" / "scripts" / "build_site.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    assert script.main(["--out", str(tmp_path / "a"), "--public-key", "public_key_x", "--agent-id", "agent_x"]) == 0
    assert '"calendarOrigin": ""' in (tmp_path / "a" / "index.html").read_text(encoding="utf-8")
    assert script.main(["--out", str(tmp_path / "b"), "--public-key", "public_key_x", "--agent-id", "agent_x", "--origin", "https://x.netlify.app"]) == 0
    assert '"calendarOrigin": "https://x.netlify.app"' in (tmp_path / "b" / "index.html").read_text(encoding="utf-8")
    assert script.main(["--out", str(tmp_path / "c"), "--public-key", "public_key_x", "--agent-id", "agent_x", "--origin", "http://x.netlify.app"]) == 2
    assert script.main(["--out", str(tmp_path / "d"), "--public-key", "private_key_x", "--agent-id", "agent_x"]) == 2


class OneOrigin(SimpleHTTPRequestHandler):
    """Static files and, under /demo and /pro, the calendar server's answers: one address, as on the host."""

    calendar_port = 0

    def log_message(self, *args):
        return

    def do_GET(self):  # noqa: N802
        if self.path.startswith(("/demo/", "/pro/")):
            with urllib.request.urlopen(f"http://127.0.0.1:{self.calendar_port}{self.path}", timeout=10) as upstream:
                body = upstream.read()
                self.send_response(upstream.status)
                self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/octet-stream"))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            return
        super().do_GET()


@pytest.fixture(scope="module")
def one_origin(tmp_path_factory):
    site = tmp_path_factory.mktemp("same-origin-site")
    dataset = DemoDataset(TODAY)
    calendar = make_site(dataset, 0, allowed_origins=[], clock=lambda: NOW)
    threading.Thread(target=calendar.serve_forever, daemon=True).start()
    handler = type("Handler", (OneOrigin,), {"calendar_port": calendar.server_address[1]})
    build_site(site, settings=SETTINGS, origin=SAME_ORIGIN, static_host=True, poll_mode="call")
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(handler, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {"page": f"http://127.0.0.1:{server.server_address[1]}/", "port": server.server_address[1], "dataset": dataset}
    for s in (server, calendar):
        s.shutdown()
        s.server_close()


@pytest.fixture(scope="module")
def driver(tmp_path_factory, channel):
    cfg = Config(business_id="1234567", local_dir=tmp_path_factory.mktemp("same-origin-browser"), browser_channel=channel)
    drv = PlaywrightBooksyDriver(cfg, session_factory=lambda: LoopbackOnlySession(cfg), notify=lambda m: None, load_timeout_seconds=15)
    yield drv
    drv.close()


def test_in_a_browser_the_page_asks_only_its_own_address_and_shows_the_calendars_open_times(driver, one_origin):
    expected = open_times(one_origin["dataset"], SWEDISH60, TOMORROW, tz=TZ, now=NOW)
    assert expected["status"] == "open"
    seen: list = []

    def go(page):
        page.route(SDK_URL, lambda route: route.fulfill(status=200, content_type="application/javascript", headers={"access-control-allow-origin": "*"}, body=SDK_STUB))
        page.on("request", lambda request: seen.append(request.url))
        page.goto("about:blank")
        page.goto(one_origin["page"], wait_until="load")
        page.wait_for_selector("h1", state="attached")
        assert page.evaluate("window.SERENITY_BOOKABLE.calendarOrigin === window.location.origin"), "the empty origin in the page's data became the page's own address when it ran"
        open_calendar(page)
        shown = pick(page, "Swedish Massage", "1 hr · $85", TOMORROW.isoformat())
        origin = page.evaluate("window.location.origin")
        page.locator('[data-testid="nav-staff"]').click() if page.locator('[data-testid="nav-staff"]').count() else None
        page.wait_for_timeout(500)
        return shown, origin, page.evaluate("document.getElementById('staff-frame') ? (document.getElementById('staff-frame').src || document.getElementById('staff-frame').dataset.src || '') : ''")

    (shown, origin, frame_src) = driver._run(go, 90)
    assert shown["state"] == "open"
    assert shown["slots"] == [[t["label"], ", ".join(t["technicians"])] for t in expected["times"]]
    assert origin == f"http://127.0.0.1:{one_origin['port']}"
    own = [u for u in seen if u.startswith(origin)]
    assert any("/demo/open-times?" in u for u in own), "the open times were asked of the page's own address"
    assert not [u for u in seen if not u.startswith(origin) and not u.startswith("data:") and u != SDK_URL and not u.startswith("about:")], "and nothing was asked of any other address"
    assert frame_src == "" or frame_src.startswith(origin) or frame_src.startswith("/pro/"), frame_src
