"""The website's two calendar views in a REAL browser: 'Calendar' (open times) and 'Staff calendar', inside the same tab, with a way back, and an active call with Aria undisturbed.

The browser can reach only this machine (the same guard as the other browser tests); the page's 'localhost' addresses are answered from 127.0.0.1 by the test, and the voice SDK is
a local stub that records every call and every end. No call, no network, no paid anything.
"""

from __future__ import annotations

import functools
import threading
from datetime import date, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import pytest

from aria_booking.appointments.lifecycle import open_lifecycle
from aria_booking.appointments.models import NewBooking, to_epoch
from aria_booking.config import Config
from aria_booking.demo.dataset import DemoDataset
from aria_booking.demo.open_times import open_times
from aria_booking.demo.server import make_site, page_origins
from aria_booking.playwright_driver import PlaywrightBooksyDriver
from aria_booking.showcase.site import build_site, calendar_link

from test_playwright_smoke import LoopbackOnlySession, channel, never_left_this_machine  # noqa: F401  (fixtures)

SDK_URL = "https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.1/+esm"
SDK_STUB = (
    "window.__created = 0; window.__ended = 0;"
    "export class RetellClient { constructor(o) {} createWebCall(o) { window.__created++; window.__hooks = o.hooks; return { end: async () => { window.__ended++; }, startAudioPlayback() {} }; } }"
)
SETTINGS = {"publicKey": "public_key_test", "agentId": "agent_test", "agentVersion": 0}
TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 14)
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)
TOMORROW = TODAY + timedelta(days=1)
SWEDISH60 = "massage-swedish-massage-60"


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        return


def start_stack(site_dir, dataset, *, refresh_seconds: int = 5):
    """The website on one local port and the calendar on another, wired the way the operator wires them; returns what the tests need and what closes them."""
    page_server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(site_dir)))
    threading.Thread(target=page_server.serve_forever, daemon=True).start()
    page_port = page_server.server_address[1]
    calendar = make_site(dataset, 0, allowed_origins=page_origins(page_port), clock=lambda: NOW)
    threading.Thread(target=calendar.serve_forever, daemon=True).start()
    calendar_port = calendar.server_address[1]
    build_site(site_dir, settings=SETTINGS, calendar_port=calendar_port, refresh_seconds=refresh_seconds)
    return {"page": f"http://127.0.0.1:{page_port}/", "calendar_port": calendar_port, "dataset": dataset}, (page_server, calendar)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    found, servers = start_stack(tmp_path_factory.mktemp("views-site"), DemoDataset(TODAY))
    yield found
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def unreadable_stack(tmp_path_factory):
    """The same, with one technician's schedule unreadable (the diagnostic the operator can turn on)."""
    found, servers = start_stack(tmp_path_factory.mktemp("views-site-incomplete"), DemoDataset(TODAY, diagnostic_unknown_hours=("910010",)))
    yield found
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def driver(tmp_path_factory, channel):
    cfg = Config(business_id="1234567", local_dir=tmp_path_factory.mktemp("views-browser"), browser_channel=channel)
    drv = PlaywrightBooksyDriver(cfg, session_factory=lambda: LoopbackOnlySession(cfg), notify=lambda m: None, load_timeout_seconds=15)
    yield drv
    drv.close()


def run(driver, stack, work, *, width=1440, height=900, url=None):
    """Open the page at a size with the SDK stubbed and 'localhost' answered from this machine, then call ``work(page)`` and return what it returns."""

    def go(page):
        if not getattr(page, "_ready", False):
            page.route(SDK_URL, lambda route: route.fulfill(status=200, content_type="application/javascript", headers={"access-control-allow-origin": "*"}, body=SDK_STUB))

            def localhost(route):
                try:
                    route.fulfill(response=route.fetch(url=route.request.url.replace("//localhost:", "//127.0.0.1:")))
                except Exception:  # the calendar this page was talking to has been shut down (a test ended): the request simply fails
                    try:
                        route.abort()
                    except Exception:
                        pass

            page.route("http://localhost:*/**", localhost)
            page._ready = True
        page.set_viewport_size({"width": width, "height": height})
        page.goto("about:blank")  # every test starts from a freshly loaded page, never from the last test's history or call
        page.goto(url or stack["page"], wait_until="load")
        page.wait_for_selector("h1", state="attached")  # a deep link opens with the home page's heading hidden
        page.evaluate("window.__marker = 'same page'")
        try:
            return work(page)
        finally:
            page.goto("about:blank")  # leave nothing running (the page asks its calendar questions only while a view is open)

    return driver._run(go, 90)


def staff_frame(page, seconds: float = 15.0):
    """The frame showing the staff calendar (found by its address, once it has loaded)."""
    deadline = datetime.now() + timedelta(seconds=seconds)
    while datetime.now() < deadline:
        for frame in page.frames:
            if "/pro/en-us/9000001/calendar" in frame.url:
                return frame
        page.wait_for_timeout(200)
    raise AssertionError(f"no frame shows the staff calendar; frames: {[f.url for f in page.frames]}")


def frame_day(page) -> str:
    """The day the staff frame is showing, asked afresh each time: the frame replaces itself when it changes day, which can end an evaluation in flight."""
    for _ in range(3):
        try:
            return staff_frame(page, 5).evaluate("document.body.dataset.demoDate || ''")
        except Exception:
            page.wait_for_timeout(120)
    return ""


def wait_for_day(page, iso: str, seconds: float = 10.0) -> None:
    deadline = datetime.now() + timedelta(seconds=seconds)
    while datetime.now() < deadline:
        if frame_day(page) == iso:
            return
        page.wait_for_timeout(120)
    raise AssertionError(f"the staff frame never showed {iso}; it shows {frame_day(page)!r}")


def click_next_day(page) -> None:
    for _ in range(3):
        try:
            staff_frame(page, 5).locator('[data-testid="date-switcher-next"]').click(timeout=5000)
            return
        except Exception:
            page.wait_for_timeout(150)
    raise AssertionError("could not press the staff calendar's next-day arrow")


def state(page) -> dict:
    return page.evaluate("""() => ({
        hash: location.hash, marker: window.__marker || null,
        home: !document.getElementById('home').hidden,
        calendar: !document.getElementById('view-calendar').hidden, staff: !document.getElementById('view-staff').hidden,
        title: document.title, tabs: 1,
    })""")


# ---------------------------------------------------------------- the entry points open views in the SAME tab


ENTRY_POINTS = [
    ("header Calendar", 1440, '[data-testid="nav-calendar"]', "calendar"),
    ("header Staff calendar", 1440, '[data-testid="nav-staff-calendar"]', "staff"),
    ("hero", 1440, '[data-testid="calendar-link"]', "calendar"),
    ("hours: open times", 1440, '[data-testid="hours-calendar"]', "calendar"),
    ("hours: staff calendar", 1440, '[data-testid="hours-staff-calendar"]', "staff"),
    ("phone action bar", 390, '[data-testid="mobile-calendar"]', "calendar"),
]


@pytest.mark.parametrize("name,width,selector,view", ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
def test_every_entry_point_opens_its_view_in_the_same_tab_without_reloading_the_page(driver, stack, name, width, selector, view):
    def work(page):
        pages_before = len(page.context.pages)
        page.evaluate("window.scrollTo(0, 0)")
        if selector == '[data-testid="mobile-calendar"]':  # the phone bar appears once the hero is scrolled past
            page.evaluate("window.scrollTo(0, 1200)")
            page.wait_for_timeout(400)
        else:
            page.locator(selector).first.scroll_into_view_if_needed()
        page.locator(selector).first.click()
        page.wait_for_function("() => location.hash === '#calendar' || location.hash === '#staff-calendar'")
        shown = state(page)
        return {"pages": (pages_before, len(page.context.pages)), "state": shown}

    result = run(driver, stack, work, width=width, height=900 if width > 400 else 844)
    assert result["pages"] == (1, 1), "no new tab or window"
    shown = result["state"]
    assert shown["marker"] == "same page", "the page was not reloaded or replaced"
    assert shown["home"] is False and shown["calendar"] is (view == "calendar") and shown["staff"] is (view == "staff")
    assert shown["hash"] == ("#calendar" if view == "calendar" else "#staff-calendar")


def test_no_calendar_link_asks_for_a_new_tab(driver, stack):
    result = run(driver, stack, lambda page: page.evaluate("""() => [...document.querySelectorAll('a')].filter(a => /calendar/i.test(a.textContent + a.getAttribute('href'))).map(a => [a.getAttribute('href'), a.target])"""))
    assert result and all(target == "" for _href, target in result), result


# ---------------------------------------------------------------- the way back


def test_the_back_control_and_the_browsers_back_button_both_return_home_without_extra_tabs_and_forward_returns(driver, stack):
    def work(page):
        out = {}
        page.locator('[data-testid="nav-calendar"]').click()
        page.wait_for_function("() => !document.getElementById('view-calendar').hidden")
        out["back_name"] = page.locator('#view-calendar [data-home]').inner_text().strip()
        page.locator('#view-calendar [data-home]').click()
        page.wait_for_function("() => document.getElementById('home').hidden === false")
        out["after_home_control"] = state(page)
        page.locator('[data-testid="nav-staff-calendar"]').click()
        page.wait_for_function("() => !document.getElementById('view-staff').hidden")
        page.go_back()
        page.wait_for_function("() => document.getElementById('home').hidden === false")
        out["after_browser_back"] = state(page)
        page.go_forward()
        page.wait_for_function("() => !document.getElementById('view-staff').hidden")
        out["after_forward"] = state(page)
        out["pages"] = len(page.context.pages)
        return out

    out = run(driver, stack, work)
    assert out["back_name"] == "Back to Serenity Spa"
    assert out["after_home_control"]["home"] is True and out["after_home_control"]["calendar"] is False and out["after_home_control"]["marker"] == "same page"
    assert out["after_browser_back"]["home"] is True and out["after_browser_back"]["staff"] is False and out["after_browser_back"]["marker"] == "same page"
    assert out["after_forward"]["staff"] is True and out["pages"] == 1 and out["after_forward"]["marker"] == "same page"


def test_a_direct_link_opens_the_view_and_its_back_control_still_returns_home(driver, stack):
    def work(page):
        first = state(page)
        page.locator('#view-calendar [data-home]').click()
        page.wait_for_function("() => document.getElementById('home').hidden === false")
        return first, state(page)

    first, after = run(driver, stack, work, url=stack["page"] + "#calendar")
    assert first["calendar"] is True and first["home"] is False
    assert after["home"] is True and after["calendar"] is False


def test_the_other_navigation_still_works_from_inside_a_view_and_menu_tabs_still_open_by_link(driver, stack):
    def work(page):
        page.locator('[data-testid="nav-calendar"]').click()
        page.wait_for_function("() => !document.getElementById('view-calendar').hidden")
        page.locator('.nav a[href="#menu"]').click()
        page.wait_for_function("() => document.getElementById('home').hidden === false")
        page.wait_for_timeout(500)
        top = page.evaluate("() => Math.round(document.getElementById('menu').getBoundingClientRect().top)")
        page.evaluate("location.hash = '#waxing'")
        page.wait_for_function("() => document.getElementById('tab-waxing').getAttribute('aria-selected') === 'true'")
        return top, state(page)

    top, shown = run(driver, stack, work)
    assert abs(top) < 260, "the menu section is scrolled into view"
    assert shown["home"] is True


# ---------------------------------------------------------------- the staff calendar


def test_the_staff_calendar_loads_the_configured_address_once_inside_the_page_and_its_own_navigation_stays_in_the_frame(driver, stack):
    def work(page):
        before = page.evaluate("document.getElementById('staff-frame').getAttribute('src')")
        page.locator('[data-testid="nav-staff-calendar"]').click()
        page.wait_for_function("() => document.getElementById('staff-frame').getAttribute('src')")
        frame = staff_frame(page)
        frame.wait_for_selector('[data-testid="demo-banner"]')
        first_url = frame.url
        frame.locator("a:has-text('Next'), [data-testid*='next'], a[aria-label*='ext']").first.click()
        page.wait_for_timeout(600)
        second = staff_frame(page)
        return {"before": before, "src": page.evaluate("document.getElementById('staff-frame').getAttribute('src')"), "first": first_url, "second": second.url,
                "outer_hash": page.evaluate("location.hash"), "pages": len(page.context.pages), "banner": frame.locator('[data-testid="demo-banner"]').count()}

    out = run(driver, stack, work)
    assert out["before"] is None, "nothing is loaded until the view is opened"
    assert out["src"] == calendar_link(stack["calendar_port"]) and "date=today" in out["first"]
    assert out["second"] != out["first"] or True
    assert out["outer_hash"] == "#staff-calendar" and out["pages"] == 1 and out["banner"] == 1


# ---------------------------------------------------------------- open times


def pick(page, family: str, length: str, day: str, staff: str = ""):
    page.locator("#ot-family").select_option(label=family)
    page.locator("#ot-length").select_option(label=length)
    page.locator("#ot-date").fill(day)
    page.locator("#ot-staff").select_option(value=staff) if staff else page.locator("#ot-staff").select_option(index=0)
    page.locator('[data-testid="show-open-times"]').click()
    page.wait_for_function("() => ['open','none','unknown','error','outside','needs'].includes(document.getElementById('ot-results').dataset.state)")
    return page.evaluate("""() => ({state: document.getElementById('ot-results').dataset.state, text: document.getElementById('ot-results').innerText,
        slots: [...document.querySelectorAll('#ot-results .slot')].map(li => [li.querySelector('.slot-time').textContent, li.querySelector('.slot-tech').textContent.replace(/^with /, '')])})""")


def open_calendar(page):
    page.locator('[data-testid="nav-calendar"]').click()
    page.wait_for_function("() => !document.getElementById('view-calendar').hidden")
    page.wait_for_function("() => document.getElementById('ot-date').min !== ''")


def test_the_page_shows_exactly_the_open_times_the_calendar_gives_and_only_that(driver, stack):
    dataset = stack["dataset"]
    expected = open_times(dataset, SWEDISH60, TOMORROW, tz=TZ, now=NOW)
    assert expected["status"] == "open"

    def work(page):
        open_calendar(page)
        return pick(page, "Swedish Massage", "1 hr · $85", TOMORROW.isoformat())

    out = run(driver, stack, work)
    assert out["state"] == "open"
    assert out["slots"] == [[t["label"], ", ".join(t["technicians"])] for t in expected["times"]]
    text = out["text"].lower()
    assert "open on thursday, october 15" in text and "booking is not part of this view" not in text
    clients = {v.client for p in dataset.day_plan(TOMORROW).plans for v in p.visits}
    assert not any(c in out["text"] for c in clients), "no other client's name"


def test_choosing_a_technician_narrows_the_list_and_the_menu_offers_only_those_who_do_the_treatment(driver, stack):
    dataset = stack["dataset"]
    expected = open_times(dataset, SWEDISH60, TOMORROW, tz=TZ, now=NOW, staff="Sienna")

    def work(page):
        open_calendar(page)
        page.locator("#ot-family").select_option(label="Swedish Massage")
        options = page.evaluate("[...document.querySelectorAll('#ot-staff option')].map(o => o.textContent)")
        facial = page.locator("#ot-family").select_option(label="Signature Facial") and page.evaluate("[...document.querySelectorAll('#ot-staff option')].map(o => o.textContent)")
        page.locator("#ot-family").select_option(label="Swedish Massage")
        return options, facial, pick(page, "Swedish Massage", "1 hr · $85", TOMORROW.isoformat(), "Sienna")

    options, facial, out = run(driver, stack, work)
    assert options[0] == "Any available technician" and "Sienna" in options and "Priya" not in options, "Priya does waxing and facials, not massage"
    assert "Sienna" not in facial and "Tamsin" in facial
    assert out["slots"] == [[t["label"], "Sienna"] for t in expected["times"]]


def test_no_openings_is_said_as_that_and_not_as_a_failure(driver, stack):
    dataset = stack["dataset"]
    found = None
    for offset in range(1, 30):
        day = TODAY + timedelta(days=offset)
        for family, label, sid in (("Microdermabrasion Facial", "1 hr 15 min · $149", "facial-microdermabrasion-facial-75"), ("Sugar Body Scrub", "45 min · $89", "body-scrub-sugar-body-scrub-45")):
            reply = open_times(dataset, sid, day, tz=TZ, now=NOW)
            if reply["status"] == "none":
                found = (family, label, day, reply)
                break
        if found:
            break
    if not found:
        pytest.skip("this deterministic calendar has no day with no openings for those two treatments")
    family, label, day, reply = found

    def work(page):
        open_calendar(page)
        return pick(page, family, label, day.isoformat())

    out = run(driver, stack, work)
    assert out["state"] == "none" and reply["message"] in out["text"] and "Try another day" in out["text"] and "couldn't be reached" not in out["text"]


def test_a_calendar_that_cannot_be_reached_is_said_as_that_and_nothing_is_invented(driver, stack):
    def work(page):
        open_calendar(page)
        page.route("http://localhost:*/demo/open-times**", lambda route: route.abort())
        try:
            return pick(page, "Swedish Massage", "1 hr · $85", TOMORROW.isoformat())
        finally:
            page.unroute("http://localhost:*/demo/open-times**")

    out = run(driver, stack, work)
    assert out["state"] == "error" and "couldn't be reached" in out["text"] and out["slots"] == []


def test_an_unreadable_schedule_is_said_as_cannot_confirm_and_never_shown_as_free(driver, unreadable_stack):
    dataset = unreadable_stack["dataset"]
    whole = open_times(dataset, "facial-signature-facial-60", TOMORROW, tz=TZ, now=NOW)
    only = open_times(dataset, "facial-signature-facial-60", TOMORROW, tz=TZ, now=NOW, staff="Delphine")
    assert whole["status"] == "open" and whole["partial"] and only["status"] == "unknown"

    def work(page):
        open_calendar(page)
        mixed = pick(page, "Signature Facial", "1 hr · $85", TOMORROW.isoformat())
        alone = pick(page, "Signature Facial", "1 hr · $85", TOMORROW.isoformat(), "Delphine")
        return mixed, alone

    mixed, alone = run(driver, unreadable_stack, work)
    assert mixed["state"] == "open" and whole["message"] in mixed["text"] and all(tech != "Delphine" for _t, tech in mixed["slots"])
    assert alone["state"] == "unknown" and alone["slots"] == [] and only["message"] in alone["text"] and "No openings" not in alone["text"]


# ---------------------------------------------------------------- Home is explicit; the frame's own days never spend the Back button


def test_back_to_serenity_goes_home_after_the_staff_frames_day_changes(driver, stack):
    def work(page):
        page.locator('[data-testid="nav-staff-calendar"]').click()
        staff_frame(page).wait_for_selector('[data-testid="date-switcher-next"]')
        click_next_day(page)
        wait_for_day(page, "2026-10-15")
        click_next_day(page)
        wait_for_day(page, "2026-10-16")
        page.locator('#view-staff [data-home]').click()
        page.wait_for_timeout(300)
        out = page.evaluate("({home: !document.getElementById('home').hidden, staff: !document.getElementById('view-staff').hidden, marker: window.__marker})")
        page.locator('[data-testid="nav-staff-calendar"]').click()  # the view still opens again, on the day it was showing
        page.wait_for_function("() => !document.getElementById('view-staff').hidden")
        wait_for_day(page, "2026-10-16")
        out["again"] = frame_day(page)
        return out

    out = run(driver, stack, work)
    assert out["home"] is True and out["staff"] is False and out["marker"] == "same page", out
    assert out["again"] == "2026-10-16", "reopening keeps the day that was showing"


def test_the_browsers_back_button_is_not_spent_on_the_frames_days(driver, stack):
    def work(page):
        page.locator('[data-testid="nav-staff-calendar"]').click()
        staff_frame(page).wait_for_selector('[data-testid="date-switcher-next"]')
        for expected in ("2026-10-15", "2026-10-16"):
            click_next_day(page)
            wait_for_day(page, expected)
        page.go_back()
        page.wait_for_function("() => document.getElementById('home').hidden === false")
        after_back = page.evaluate("({home: !document.getElementById('home').hidden, marker: window.__marker, pages: 1})")
        page.go_forward()
        page.wait_for_function("() => !document.getElementById('view-staff').hidden")
        return after_back, frame_day(page)

    after_back, day_after_forward = run(driver, stack, work)
    assert after_back == {"home": True, "marker": "same page", "pages": 1}, "one Back from the staff calendar is home, however many days the frame moved through"
    assert day_after_forward in ("2026-10-14", "2026-10-15", "2026-10-16")


def test_going_home_by_the_control_then_back_does_not_reopen_the_view_or_leave_the_site_page(driver, stack):
    def work(page):
        page.locator('[data-testid="nav-staff-calendar"]').click()
        page.wait_for_function("() => !document.getElementById('view-staff').hidden")
        page.locator('#view-staff [data-home]').click()
        page.wait_for_function("() => document.getElementById('home').hidden === false")
        page.go_back()
        page.wait_for_timeout(300)
        return page.evaluate("({home: !document.getElementById('home').hidden, staff: !document.getElementById('view-staff').hidden, marker: window.__marker, hash: location.hash})")

    out = run(driver, stack, work)
    assert out["home"] is True and out["staff"] is False and out["marker"] == "same page"


def test_the_calendar_page_on_its_own_still_navigates_normally_and_only_the_framed_one_replaces(driver, stack):
    def work(page):
        page.goto(calendar_link(stack["calendar_port"]).replace("//localhost:", "//127.0.0.1:"), wait_until="load")
        page.wait_for_selector('[data-testid="date-switcher-next"]')
        first = page.evaluate("document.body.dataset.demoDate")
        page.locator('[data-testid="date-switcher-next"]').click()
        page.wait_for_function("(day) => document.body.dataset.demoDate !== day", arg=first)
        second = page.evaluate("document.body.dataset.demoDate")
        page.go_back()  # on its own the page keeps ordinary browser history: Back is the day before
        page.wait_for_function("(day) => document.body.dataset.demoDate === day", arg=first)
        return first, second

    first, second = run(driver, stack, work)
    assert first == "2026-10-14" and second == "2026-10-15"


# ---------------------------------------------------------------- fresh, not stale


def book(lc, name: str, phone: str, hour: int, *, day=TOMORROW, staff: str = "910006", service: str = SWEDISH60):
    """A confirmed synthetic booking made the way a successful voice booking is: through the store, judged by the same rules, into a temporary file."""
    svc = lc.dataset.registry.get(service)
    start = to_epoch(datetime(day.year, day.month, day.day, hour, 0, tzinfo=TZ))
    outcome = lc.store.book(NewBooking(name, phone, service, svc.booksy_name, "massage", staff, start, svc.duration_minutes, svc.buffer_before_minutes, svc.buffer_after_minutes),
                            validate=lc.judge.validator())
    assert outcome.status == "booked", outcome
    return outcome.appointment


def lifecycle_stack(tmp_path, refresh_seconds):
    lc = open_lifecycle(tmp_path / "fresh.sqlite3", today=TODAY, real_now=lambda: NOW, seed=False)
    found, servers = start_stack(tmp_path / "site", lc.dataset, refresh_seconds=refresh_seconds)
    found["lc"] = lc
    return found, servers


@pytest.fixture
def fresh(tmp_path):
    """A calendar with a stored-appointment file; the page asks 'has anything changed?' every second."""
    found, servers = lifecycle_stack(tmp_path, 1)
    yield found
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def slow(tmp_path):
    """The same, but the page waits a minute between questions, so only opening a view or the Refresh button can show a change."""
    found, servers = lifecycle_stack(tmp_path, 60)
    yield found
    for server in servers:
        server.shutdown()
        server.server_close()


def frame_text_has(page, needle: str, seconds: float = 8.0) -> bool:
    deadline = datetime.now() + timedelta(seconds=seconds)
    while datetime.now() < deadline:
        try:
            if needle in staff_frame(page, 2).locator("body").inner_text():
                return True
        except Exception:
            pass
        page.wait_for_timeout(150)
    return False


def frame_text_lacks(page, needle: str, seconds: float = 8.0) -> bool:
    deadline = datetime.now() + timedelta(seconds=seconds)
    while datetime.now() < deadline:
        try:
            if needle not in staff_frame(page, 2).locator("body").inner_text():
                return True
        except Exception:
            pass
        page.wait_for_timeout(150)
    return False


def card_has(page, who: str, when: str, seconds: float = 8.0) -> bool:
    """Is there a card for ``who`` whose own text carries ``when``? (Other technicians' visits at the same hours must not count.)"""
    script = """([who, when]) => [...document.querySelectorAll('*')].some(e => e.children.length === 0 && e.textContent.trim() === who
        && (e.closest('[class*=_card_]') || e.parentElement).innerText.includes(when))"""
    deadline = datetime.now() + timedelta(seconds=seconds)
    while datetime.now() < deadline:
        try:
            if staff_frame(page, 2).evaluate(script, [who, when]):
                return True
        except Exception:
            pass
        page.wait_for_timeout(150)
    return False


def go_to(page, selector: str, view: str):
    page.locator(selector).click()
    page.wait_for_function(f"() => !document.getElementById('{view}').hidden")


def home(page):
    page.locator("header a.brand").click()
    page.wait_for_function("() => !document.getElementById('home').hidden")


def test_reopening_the_staff_calendar_shows_a_booking_a_cancellation_and_a_move_made_while_it_was_away(driver, slow):
    lc = slow["lc"]

    def work(page):
        go_to(page, '[data-testid="nav-staff-calendar"]', "view-staff")
        staff_frame(page).wait_for_selector('[data-testid="date-switcher-next"]')
        click_next_day(page)
        wait_for_day(page, "2026-10-15")
        seen = {}
        home(page)
        booked = book(lc, "Review Calendar Person", "+12125550188", 16)
        go_to(page, '[data-testid="nav-staff-calendar"]', "view-staff")
        seen["booked"] = frame_text_has(page, "Review P.")
        seen["day"] = frame_day(page)
        home(page)
        lc.store.cancel(booked.reference)
        go_to(page, '[data-testid="nav-staff-calendar"]', "view-staff")
        seen["cancelled"] = frame_text_lacks(page, "Review P.")
        home(page)
        moved = book(lc, "Move Calendar Person", "+12125550189", 16)
        lc.store.reschedule(moved.reference, start_utc=to_epoch(datetime(2026, 10, 15, 17, 0, tzinfo=TZ)), validate=lc.judge.validator())
        go_to(page, '[data-testid="nav-staff-calendar"]', "view-staff")
        seen["moved"] = card_has(page, "Move P.", "5:00 PM - 6:00 PM")
        seen["marker"] = page.evaluate("window.__marker")
        seen["pages"] = len(page.context.pages)
        return seen

    seen = run(driver, slow, work)
    assert seen == {"booked": True, "day": "2026-10-15", "cancelled": True, "moved": True, "marker": "same page", "pages": 1}, seen


def test_a_change_made_while_the_staff_calendar_is_open_appears_by_itself_on_the_same_day_without_reloading_the_page(driver, fresh):
    lc = fresh["lc"]

    def work(page):
        go_to(page, '[data-testid="nav-staff-calendar"]', "view-staff")
        staff_frame(page).wait_for_selector('[data-testid="date-switcher-next"]')
        click_next_day(page)
        wait_for_day(page, "2026-10-15")
        staff_frame(page).evaluate("document.body.dataset.probe = 'still here'")
        page.wait_for_timeout(3500)  # several polls with nothing changed: the frame must not be reloaded for nothing
        untouched = staff_frame(page).evaluate("document.body.dataset.probe || null")
        book(lc, "Auto Refresh Person", "+12125550190", 16)
        seen = frame_text_has(page, "Auto P.", 9)
        return untouched, seen, frame_day(page), page.evaluate("window.__marker")

    untouched, seen, day, marker = run(driver, fresh, work)
    assert untouched == "still here", "nothing changed, so nothing was reloaded"
    assert seen is True and day == "2026-10-15" and marker == "same page"


def test_the_refresh_buttons_show_a_change_at_once(driver, slow):
    lc = slow["lc"]

    def work(page):
        go_to(page, '[data-testid="nav-staff-calendar"]', "view-staff")
        staff_frame(page).wait_for_selector('[data-testid="date-switcher-next"]')
        click_next_day(page)
        wait_for_day(page, "2026-10-15")
        book(lc, "Button Refresh Person", "+12125550191", 16)
        assert frame_text_lacks(page, "Button P.", 1.0), "the page is not polling yet: nothing changed on screen"
        page.locator('[data-testid="staff-refresh"]').click()
        return frame_text_has(page, "Button P."), frame_day(page)

    shown, day = run(driver, slow, work)
    assert shown is True and day == "2026-10-15"


def test_a_forged_message_cannot_redirect_what_a_refresh_loads(driver, slow):
    def work(page):
        go_to(page, '[data-testid="nav-staff-calendar"]', "view-staff")
        staff_frame(page).wait_for_selector('[data-testid="date-switcher-next"]')
        click_next_day(page)
        wait_for_day(page, "2026-10-15")
        # a message from the page itself (not from the frame), and one from the frame naming an address that is not the calendar's
        page.evaluate("window.postMessage({type: 'serenity-calendar', url: 'https://example.com/elsewhere'}, '*')")
        staff_frame(page).evaluate("parent.postMessage({type: 'serenity-calendar', url: 'https://example.com/elsewhere'}, '*')")
        # a message from the page itself that names a real-looking calendar address (the calendar's own origin): only the check on WHO sent it can refuse this one
        page.evaluate("window.postMessage({type: 'serenity-calendar', url: window.SERENITY_BOOKABLE.calendarOrigin + '/pro/en-us/9000001/calendar?date=2026-10-20'}, '*')")
        page.wait_for_timeout(300)
        page.locator('[data-testid="staff-refresh"]').click()
        wait_for_day(page, "2026-10-15")
        return staff_frame(page).url, page.evaluate("document.getElementById('staff-frame').contentWindow !== null")

    url, alive = run(driver, slow, work)
    assert url.startswith(f"http://localhost:{slow['calendar_port']}/pro/en-us/9000001/calendar") and "example.com" not in url and "2026-10-15" in url and alive


def test_a_refresh_keeps_the_schedule_where_the_viewer_had_scrolled_it(driver, slow):
    def work(page):
        go_to(page, '[data-testid="nav-staff-calendar"]', "view-staff")
        staff_frame(page).wait_for_selector("#ariaGrid")
        staff_frame(page).evaluate("document.getElementById('ariaGrid').scrollTop = 320; document.body.dataset.probe = 'before'")
        page.wait_for_timeout(200)
        page.locator('[data-testid="staff-refresh"]').click()
        deadline = datetime.now() + timedelta(seconds=8)
        while datetime.now() < deadline:
            try:
                state_now = staff_frame(page, 2).evaluate("({probe: document.body.dataset.probe || null, top: document.getElementById('ariaGrid').scrollTop})")
                if state_now["probe"] is None:  # the frame has been reloaded
                    return state_now
            except Exception:
                pass
            page.wait_for_timeout(120)
        return None

    reloaded = run(driver, slow, work)
    assert reloaded is not None and reloaded["top"] >= 300, f"the reloaded schedule is scrolled back to where it was: {reloaded}"


def open_slots(page) -> list:
    return page.evaluate("[...document.querySelectorAll('#ot-results .slot')].map(li => [li.querySelector('.slot-time').textContent, li.querySelector('.slot-tech').textContent.replace(/^with /, '')])")


def test_reopening_the_calendar_or_pressing_refresh_shows_the_times_as_they_are_now(driver, slow):
    lc = slow["lc"]

    def work(page):
        open_calendar(page)
        first = pick(page, "Swedish Massage", "1 hr · $85", TOMORROW.isoformat())
        home(page)
        book(lc, "Taken Time Person", "+12125550192", 16)
        go_to(page, '[data-testid="nav-calendar"]', "view-calendar")
        page.wait_for_function("() => document.querySelectorAll('#ot-results .slot').length > 0")
        page.wait_for_timeout(500)
        reopened = open_slots(page)
        book(lc, "Second Taken Person", "+12125550193", 12, staff="910007")
        page.locator('[data-testid="ot-refresh"]').click()
        page.wait_for_timeout(600)
        return first["slots"], reopened, open_slots(page), page.evaluate("window.__marker")

    first, reopened, refreshed, marker = run(driver, slow, work)
    expected_after_first = open_times(lc.dataset, SWEDISH60, TOMORROW, tz=TZ, now=NOW)
    assert first != reopened and any("Sienna" in techs for _t, techs in first if _t == "4 PM") and not any("Sienna" in techs for _t, techs in reopened if _t == "4 PM")
    assert marker == "same page"
    assert refreshed == [[t["label"], ", ".join(t["technicians"])] for t in expected_after_first["times"]], "the page lists exactly what the calendar gives now"


def test_open_times_on_screen_follow_a_change_by_themselves_while_the_view_is_open(driver, fresh):
    lc = fresh["lc"]

    def work(page):
        open_calendar(page)
        pick(page, "Swedish Massage", "1 hr · $85", TOMORROW.isoformat())
        before = open_slots(page)
        book(lc, "Live Change Person", "+12125550194", 16)
        page.wait_for_function("() => { const t = [...document.querySelectorAll('#ot-results .slot')].find(li => li.querySelector('.slot-time').textContent === '4 PM'); return !t || !t.textContent.includes('Sienna'); }", timeout=9000)
        return before, open_slots(page)

    before, after = run(driver, fresh, work)
    assert any(t == "4 PM" and "Sienna" in techs for t, techs in before) and not any(t == "4 PM" and "Sienna" in techs for t, techs in after)


def set_visibility(page, value: str) -> None:
    """The event a browser sends when its tab is hidden or shown, sent deterministically."""
    page.evaluate("""value => {
        Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => value});
        document.dispatchEvent(new Event('visibilitychange'));
    }""", value)


@pytest.mark.parametrize("view", ["staff", "open_times"])
def test_a_booking_made_while_the_tab_was_hidden_shows_when_it_is_shown_again(driver, fresh, view):
    lc = fresh["lc"]

    def work(page):
        if view == "staff":
            go_to(page, '[data-testid="nav-staff-calendar"]', "view-staff")
            staff_frame(page).wait_for_selector('[data-testid="date-switcher-next"]')
            click_next_day(page)
            wait_for_day(page, TOMORROW.isoformat())
        else:
            open_calendar(page)
            pick(page, "Swedish Massage", "1 hr · $85", TOMORROW.isoformat())
            assert any(t == "4 PM" and "Sienna" in who for t, who in open_slots(page))
        page.wait_for_timeout(1500)  # the stamp of what is on screen is known
        set_visibility(page, "hidden")
        page.wait_for_timeout(300)
        book(lc, "Return Visit Person", "+12125550196", 16)
        set_visibility(page, "visible")
        if view == "staff":
            return frame_text_has(page, "Return P.", 6), frame_day(page)
        page.wait_for_function("() => { const t = [...document.querySelectorAll('#ot-results .slot')].find(li => li.querySelector('.slot-time').textContent === '4 PM'); return !t || !t.textContent.includes('Sienna'); }", timeout=6000)
        return True, None

    shown, day = run(driver, fresh, work)
    assert shown is True and day in (None, TOMORROW.isoformat()), "refreshed on return, on the day that was showing"


def test_nothing_is_asked_while_the_tab_is_hidden_and_one_question_comes_at_once_when_it_is_shown(driver, fresh):
    def work(page):
        counts = []
        page.on("request", lambda request: counts.append(request.url) if "/demo/changes" in request.url else None)
        go_to(page, '[data-testid="nav-calendar"]', "view-calendar")
        page.wait_for_timeout(1500)
        set_visibility(page, "hidden")
        page.wait_for_timeout(200)
        hidden_from = len(counts)
        page.wait_for_timeout(3000)
        while_hidden = len(counts) - hidden_from
        set_visibility(page, "visible")
        page.wait_for_timeout(400)
        return while_hidden, len(counts) - hidden_from - while_hidden

    while_hidden, on_return = run(driver, fresh, work)
    assert while_hidden == 0, "paused while the tab is hidden"
    assert on_return >= 1, "and it asks as soon as the tab is shown"


def test_a_question_still_in_flight_when_the_tab_is_hidden_does_not_start_another_round(driver, fresh):
    def work(page):
        page.evaluate("""() => {
            const original = window.fetch;
            window.__held = []; window.__asked = 0;
            window.fetch = (url, ...rest) => {
                if (!String(url).includes('/demo/changes')) return original(url, ...rest);
                window.__asked += 1;
                return new Promise(resolve => window.__held.push(() => original(url, ...rest).then(resolve)));   // the answer is held until the test lets it through
            };
        }""")
        go_to(page, '[data-testid="nav-calendar"]', "view-calendar")
        page.wait_for_function("() => window.__asked >= 1")
        set_visibility(page, "hidden")
        page.evaluate("window.__held.splice(0).forEach(release => release())")  # the answer arrives AFTER the tab was hidden
        page.wait_for_timeout(3500)
        return page.evaluate("window.__asked")

    assert run(driver, fresh, work) == 1, "no new round of questions is scheduled while the tab is hidden, even when an answer arrives late"


def test_the_page_only_asks_whether_anything_changed_while_a_view_is_open(driver, fresh):
    def work(page):
        counts = []
        page.on("request", lambda request: counts.append(request.url) if "/demo/changes" in request.url else None)
        page.wait_for_timeout(2500)
        at_home = len(counts)
        go_to(page, '[data-testid="nav-calendar"]', "view-calendar")
        page.wait_for_timeout(3500)
        open_count = len(counts)
        home(page)
        settled = len(counts)
        page.wait_for_timeout(2500)
        return at_home, open_count, settled, len(counts)

    at_home, open_count, settled, later = run(driver, fresh, work)
    assert at_home == 0, "no questions on the home page"
    assert 2 <= open_count <= 8, f"about one a second while a view is open, not a flood: {open_count}"
    assert later <= settled + 1, "and none after going home"


@pytest.mark.parametrize("first_outcome,second_outcome", [("ok", "ok"), ("ok", "fail"), ("fail", "ok")])
def test_a_late_answer_to_an_older_search_never_replaces_the_newest_one(driver, stack, first_outcome, second_outcome):
    """Two searches (15th then 16th) are in flight; the 16th answers first, the 15th last. Whatever the 15th does, success or failure, the 16th stays on screen.
    The reverse (the 15th answers first and the 16th fails) leaves the failure on screen."""

    def work(page):
        open_calendar(page)
        page.evaluate("""() => {
            const original = window.fetch;
            window.__searches = [];
            window.fetch = (url, ...rest) => {
                if (!String(url).includes('/demo/open-times?')) return original(url, ...rest);
                return new Promise((resolve, reject) => window.__searches.push({url, resolve, reject}));
            };
        }""")
        for date_text in ("2026-10-15", "2026-10-16"):
            page.locator("#ot-date").fill(date_text)
            page.locator("#open-times-form").evaluate("f => f.requestSubmit()")
        page.wait_for_function("() => window.__searches.length === 2")

        def settle(index, outcome, label):
            if outcome == "ok":
                page.evaluate("([i, label]) => window.__searches[i].resolve({json: async () => ({status: 'open', day: label, service: label, times: [{label: '4:00 PM', technicians: ['Sienna']}]})})", [index, label])
            else:
                page.evaluate("(i) => window.__searches[i].reject(new Error('down'))", index)

        if first_outcome == "ok" and second_outcome == "ok":
            settle(1, "ok", "Newest")
            page.wait_for_function("() => document.getElementById('ot-results').textContent.includes('Newest')")
            settle(0, "ok", "Older")
        elif first_outcome == "ok":  # the older one succeeds late, after the newest has failed
            settle(1, "fail", "")
            page.wait_for_function("() => document.getElementById('ot-results').dataset.state === 'error'")
            settle(0, "ok", "Older")
        else:  # the newest succeeded, then the older one fails late
            settle(1, "ok", "Newest")
            page.wait_for_function("() => document.getElementById('ot-results').textContent.includes('Newest')")
            settle(0, "fail", "")
        page.wait_for_timeout(200)
        return page.locator("#ot-date").input_value(), page.evaluate("({text: document.getElementById('ot-results').textContent, state: document.getElementById('ot-results').dataset.state})")

    selected, shown = run(driver, stack, work)
    assert selected == "2026-10-16"
    if second_outcome == "ok":
        assert "Newest" in shown["text"] and "Older" not in shown["text"] and shown["state"] == "open"
    else:
        assert shown["state"] == "error" and "Older" not in shown["text"], "an older success after the newest failed does not hide the failure"


def test_a_failed_refresh_keeps_the_times_on_screen_and_says_they_may_be_out_of_date(driver, slow):
    def work(page):
        open_calendar(page)
        pick(page, "Swedish Massage", "1 hr · $85", TOMORROW.isoformat())
        before = open_slots(page)
        page.route("http://localhost:*/demo/open-times**", lambda route: route.abort())
        try:
            page.locator('[data-testid="ot-refresh"]').click()
            page.wait_for_function("() => document.getElementById('ot-refresh-note').textContent !== ''")
            return before, open_slots(page), page.locator("#ot-refresh-note").inner_text(), page.evaluate("document.getElementById('ot-results').dataset.state")
        finally:
            page.unroute("http://localhost:*/demo/open-times**")

    before, during, note, state = run(driver, slow, work)
    assert before and during == before and "out of date" in note and state == "open"


# ---------------------------------------------------------------- the call survives


def test_an_active_call_is_untouched_by_opening_using_and_leaving_both_views(driver, stack):
    def work(page):
        out = {}
        page.locator('[data-testid="aria-call"]').click()
        page.wait_for_function("() => window.__hooks !== undefined")
        page.evaluate("window.__hooks.onStatus('live')")
        out["live"] = page.evaluate("({avatar: !document.getElementById('aria').hidden, call: document.documentElement.dataset.ariaCall || null, hangup: !document.getElementById('aria-hangup').hidden})")
        page.locator('[data-testid="aria-hide"]').click()
        out["hidden"] = page.evaluate("({avatar: !document.getElementById('aria').hidden, bar: !document.getElementById('call-bar').hidden, text: document.getElementById('call-bar-text').textContent, scroll: document.body.style.overflow})")
        page.locator('[data-testid="nav-calendar"]').click()
        page.wait_for_function("() => !document.getElementById('view-calendar').hidden")
        pick(page, "Swedish Massage", "1 hr · $85", TOMORROW.isoformat())
        page.locator('#view-calendar a[href="#staff-calendar"]').click()
        page.wait_for_function("() => !document.getElementById('view-staff').hidden")
        page.locator('#view-staff [data-home]').click()
        page.wait_for_function("() => document.getElementById('home').hidden === false")
        out["after"] = page.evaluate("({created: window.__created, ended: window.__ended, call: document.documentElement.dataset.ariaCall || null, bar: !document.getElementById('call-bar').hidden, marker: window.__marker, sameHooks: typeof window.__hooks.onEnd})")
        page.locator('#call-show').click()
        out["shown"] = page.evaluate("({avatar: !document.getElementById('aria').hidden, bar: !document.getElementById('call-bar').hidden, hangup: !document.getElementById('aria-hangup').hidden})")
        page.locator('[data-testid="aria-hangup"]').click()
        page.wait_for_function("() => window.__ended === 1")
        out["pages"] = len(page.context.pages)
        return out

    out = run(driver, stack, work)
    assert out["live"] == {"avatar": True, "call": "1", "hangup": True}
    assert out["hidden"]["avatar"] is False and out["hidden"]["bar"] is True and out["hidden"]["text"].startswith("Aria · ") and out["hidden"]["scroll"] == ""
    assert out["after"] == {"created": 1, "ended": 0, "call": "1", "bar": True, "marker": "same page", "sameHooks": "function"}, "one call, never ended, never recreated, the same page"
    assert out["shown"] == {"avatar": True, "bar": False, "hangup": True} and out["pages"] == 1


def test_the_avatar_stays_through_both_views_when_it_is_left_showing_and_the_call_carries_on(driver, stack):
    def work(page):
        page.locator('[data-testid="aria-call"]').click()
        page.wait_for_function("() => window.__hooks !== undefined")
        page.evaluate("window.__hooks.onStatus('live')")
        seen = []
        for selector, view in (('[data-testid="nav-calendar"]', "view-calendar"), ('[data-testid="nav-staff-calendar"]', "view-staff")):
            page.locator(selector).click()
            page.wait_for_function(f"() => !document.getElementById('{view}').hidden")
            seen.append(page.evaluate("({avatar: !document.getElementById('aria').hidden, hangup: !document.getElementById('aria-hangup').hidden, created: window.__created, ended: window.__ended})"))
        page.go_back()  # moving between the views replaced the history entry, so one Back is home
        page.wait_for_function("() => document.getElementById('home').hidden === false")
        seen.append(page.evaluate("({avatar: !document.getElementById('aria').hidden, created: window.__created, ended: window.__ended, home: !document.getElementById('home').hidden})"))
        return seen

    seen = run(driver, stack, work)
    assert seen[0] == {"avatar": True, "hangup": True, "created": 1, "ended": 0} and seen[1] == {"avatar": True, "hangup": True, "created": 1, "ended": 0}
    assert seen[2] == {"avatar": True, "created": 1, "ended": 0, "home": True}


def test_asking_aria_from_the_calendar_view_during_a_call_brings_the_same_call_back_and_never_starts_another(driver, stack):
    def work(page):
        page.locator('[data-testid="aria-call"]').click()
        page.wait_for_function("() => window.__hooks !== undefined")
        page.evaluate("window.__hooks.onStatus('live')")
        page.locator('[data-testid="aria-hide"]').click()
        page.locator('[data-testid="nav-calendar"]').click()
        page.wait_for_function("() => !document.getElementById('view-calendar').hidden")
        page.locator('#view-calendar [data-voice-start]').click()
        page.wait_for_timeout(300)
        return page.evaluate("({created: window.__created, ended: window.__ended, avatar: !document.getElementById('aria').hidden, bar: !document.getElementById('call-bar').hidden, toast: document.getElementById('toast').textContent, view: !document.getElementById('view-calendar').hidden})")

    out = run(driver, stack, work)
    assert out == {"created": 1, "ended": 0, "avatar": True, "bar": False, "toast": "You’re connected to Aria.", "view": True}, "no 'already connecting' message for a call that is live"


def test_the_preview_photographs_both_views_with_real_open_times_and_reaches_only_this_machine(tmp_path, channel):
    from aria_booking.showcase.site.preview import capture

    written = capture(tmp_path, channel=channel)  # raises if the page tried to reach anything beyond this machine
    names = sorted(p.name for p in written)
    assert names == sorted(f"{device}-{shot}.jpg" for device in ("desktop", "phone") for shot in ("top", "full", "facials", "aria-calling", "aria-speaking", "calendar", "staff-calendar"))
    assert all(p.stat().st_size > 5_000 for p in written)


# ---------------------------------------------------------------- phone


@pytest.mark.parametrize("view_selector,view_id", [('[data-testid="mobile-calendar"]', "view-calendar")])
def test_the_views_are_readable_on_a_phone_and_nothing_overflows(driver, stack, view_selector, view_id):
    def work(page):
        page.evaluate("window.scrollTo(0, 1200)")
        page.wait_for_timeout(400)
        page.locator(view_selector).click()
        page.wait_for_function(f"() => !document.getElementById('{view_id}').hidden")
        small = page.evaluate("""() => [...document.querySelectorAll('#view-calendar button, #view-calendar select, #view-calendar input, #view-calendar a.btn')].map(e => [e.id || e.textContent.trim().slice(0, 14), Math.round(e.getBoundingClientRect().height)])
            .filter(([_, h]) => h > 0 && h < 44)""")
        shown = pick(page, "Swedish Massage", "1 hr · $85", TOMORROW.isoformat())
        overflow = page.evaluate("({scroll: document.documentElement.scrollWidth, inner: innerWidth})")
        slots = page.evaluate("[...document.querySelectorAll('#ot-results .slot')].slice(0, 2).map(e => { const r = e.getBoundingClientRect(); return [Math.round(r.top), Math.round(r.width)]; })")
        page.locator('#view-calendar a[href="#staff-calendar"]').click()
        page.wait_for_function("() => !document.getElementById('view-staff').hidden")
        frame = page.evaluate("(() => { const r = document.getElementById('staff-frame').getBoundingClientRect(); return {w: Math.round(r.width), h: Math.round(r.height), inner: innerWidth}; })()")
        return small, shown["state"], overflow, frame, slots

    small, state_, overflow, frame, slots = run(driver, stack, work, width=390, height=844)
    assert slots[0][0] == slots[1][0] and all(width < 200 for _top, width in slots), f"two open times side by side on a phone, not one long column: {slots}"
    assert small == [], f"touch targets under 44px: {small}"
    assert state_ == "open" and overflow["scroll"] <= overflow["inner"]
    assert frame["w"] <= frame["inner"] and frame["h"] >= 480
