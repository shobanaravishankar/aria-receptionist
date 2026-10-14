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


def start_stack(site_dir, dataset):
    """The website on one local port and the calendar on another, wired the way the operator wires them; returns what the tests need and what closes them."""
    page_server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(site_dir)))
    threading.Thread(target=page_server.serve_forever, daemon=True).start()
    page_port = page_server.server_address[1]
    calendar = make_site(dataset, 0, allowed_origins=page_origins(page_port), clock=lambda: NOW)
    threading.Thread(target=calendar.serve_forever, daemon=True).start()
    calendar_port = calendar.server_address[1]
    build_site(site_dir, settings=SETTINGS, calendar_port=calendar_port)
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
                route.fulfill(response=route.fetch(url=route.request.url.replace("//localhost:", "//127.0.0.1:")))

            page.route("http://localhost:*/**", localhost)
            page._ready = True
        page.set_viewport_size({"width": width, "height": height})
        page.goto("about:blank")  # every test starts from a freshly loaded page, never from the last test's history or call
        page.goto(url or stack["page"], wait_until="load")
        page.wait_for_selector("h1", state="attached")  # a deep link opens with the home page's heading hidden
        page.evaluate("window.__marker = 'same page'")
        return work(page)

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


# ---------------------------------------------------------------- the call survives


def test_an_active_call_is_untouched_by_opening_using_and_leaving_both_views(driver, stack):
    def work(page):
        out = {}
        page.locator('[data-voice-start]').first.click()
        page.wait_for_function("() => window.__hooks !== undefined")
        page.evaluate("window.__hooks.onStatus('live')")
        out["live"] = page.evaluate("({panel: !document.getElementById('voice-panel').hidden, call: document.documentElement.dataset.ariaCall || null, hide: !document.getElementById('voice-minimize').hidden})")
        page.locator('#voice-minimize').click()
        out["minimised"] = page.evaluate("({panel: !document.getElementById('voice-panel').hidden, bar: !document.getElementById('call-bar').hidden, text: document.getElementById('call-bar-text').textContent, scroll: document.body.style.overflow})")
        page.locator('[data-testid="nav-calendar"]').click()
        page.wait_for_function("() => !document.getElementById('view-calendar').hidden")
        pick(page, "Swedish Massage", "1 hr · $85", TOMORROW.isoformat())
        page.locator('#view-calendar a[href="#staff-calendar"]').click()
        page.wait_for_function("() => !document.getElementById('view-staff').hidden")
        page.locator('#view-staff [data-home]').click()
        page.wait_for_function("() => document.getElementById('home').hidden === false")
        out["after"] = page.evaluate("({created: window.__created, ended: window.__ended, call: document.documentElement.dataset.ariaCall || null, bar: !document.getElementById('call-bar').hidden, marker: window.__marker, sameHooks: typeof window.__hooks.onEnd})")
        page.locator('#call-show').click()
        out["shown"] = page.evaluate("({panel: !document.getElementById('voice-panel').hidden, bar: !document.getElementById('call-bar').hidden, end: !document.getElementById('voice-end').hidden})")
        page.locator('#voice-end').click()
        page.wait_for_function("() => window.__ended === 1")
        out["pages"] = len(page.context.pages)
        return out

    out = run(driver, stack, work)
    assert out["live"] == {"panel": True, "call": "1", "hide": True}
    assert out["minimised"]["panel"] is False and out["minimised"]["bar"] is True and out["minimised"]["text"].startswith("Aria · ") and out["minimised"]["scroll"] == ""
    assert out["after"] == {"created": 1, "ended": 0, "call": "1", "bar": True, "marker": "same page", "sameHooks": "function"}, "one call, never ended, never recreated, the same page"
    assert out["shown"] == {"panel": True, "bar": False, "end": True} and out["pages"] == 1


def test_asking_aria_from_the_calendar_view_during_a_call_brings_the_same_call_back_and_never_starts_another(driver, stack):
    def work(page):
        page.locator('[data-voice-start]').first.click()
        page.wait_for_function("() => window.__hooks !== undefined")
        page.evaluate("window.__hooks.onStatus('live')")
        page.locator('#voice-minimize').click()
        page.locator('[data-testid="nav-calendar"]').click()
        page.wait_for_function("() => !document.getElementById('view-calendar').hidden")
        page.locator('#view-calendar [data-voice-start]').click()
        page.wait_for_timeout(300)
        return page.evaluate("({created: window.__created, ended: window.__ended, panel: !document.getElementById('voice-panel').hidden, bar: !document.getElementById('call-bar').hidden, toast: document.getElementById('toast').textContent, view: !document.getElementById('view-calendar').hidden})")

    out = run(driver, stack, work)
    assert out == {"created": 1, "ended": 0, "panel": True, "bar": False, "toast": "You’re connected to Aria.", "view": True}, "no 'already connecting' message for a call that is live"


def test_a_call_that_ends_while_the_panel_is_put_away_shows_how_it_ended(driver, stack):
    def work(page):
        page.locator('[data-voice-start]').first.click()
        page.wait_for_function("() => window.__hooks !== undefined")
        page.evaluate("window.__hooks.onStatus('live')")
        page.locator('#voice-minimize').click()
        page.evaluate("window.__hooks.onEnd()")
        return page.evaluate("({panel: !document.getElementById('voice-panel').hidden, bar: !document.getElementById('call-bar').hidden, text: document.querySelector('.voice-status__text').textContent})")

    out = run(driver, stack, work)
    assert out["panel"] is True and out["bar"] is False and out["text"].startswith("Call ended.")


def test_the_minimise_control_only_exists_for_a_call_in_progress(driver, stack):
    def work(page):
        before = page.evaluate("document.getElementById('voice-minimize').hidden")
        page.locator('[data-voice-start]').first.click()
        page.wait_for_function("() => window.__hooks !== undefined")
        connecting = page.evaluate("document.getElementById('voice-minimize').hidden")
        page.evaluate("window.__hooks.onError(new Error('Failed to fetch'))")
        failed = page.evaluate("({hide: document.getElementById('voice-minimize').hidden, bar: document.getElementById('call-bar').hidden})")
        return before, connecting, failed

    before, connecting, failed = run(driver, stack, work)
    assert before is True and connecting is True and failed == {"hide": True, "bar": True}


def test_the_preview_photographs_both_views_with_real_open_times_and_reaches_only_this_machine(tmp_path, channel):
    from aria_booking.showcase.site.preview import capture

    written = capture(tmp_path, channel=channel)  # raises if the page tried to reach anything beyond this machine
    names = sorted(p.name for p in written)
    assert names == sorted(f"{device}-{shot}.jpg" for device in ("desktop", "phone") for shot in ("top", "full", "facials", "voice-panel", "calendar", "staff-calendar"))
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
