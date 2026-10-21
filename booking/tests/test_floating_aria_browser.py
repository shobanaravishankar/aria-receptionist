"""The floating Aria avatar in a REAL browser, with the voice SDK replaced by a local stub that records every call and every end: a compact avatar on a transparent
background with separate Call and Hang up icons, states that come only from the SDK, one call at a time, hiding that never ends a call, errors that say what is wrong,
keyboard use, reduced motion, and a phone layout. The browser can reach only this machine. No call, no network, nothing paid.
"""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aria_booking.config import Config
from aria_booking.playwright_driver import PlaywrightBooksyDriver
from aria_booking.showcase.site import build_site

from test_playwright_smoke import LoopbackOnlySession, channel, never_left_this_machine  # noqa: F401  (fixtures)

SDK_URL = "https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.1/+esm"
SDK_STUB = (
    "window.__created = 0; window.__ended = 0;"
    "export class RetellClient { constructor(o) {} createWebCall(o) { window.__created++; window.__hooks = o.hooks; window.__options = {agent_id: o.agent_id};"
    " return { end: async () => { window.__ended++; }, startAudioPlayback() {} }; } }"
)
SLOW_SDK_STUB = "await new Promise(r => setTimeout(r, 1500));" + SDK_STUB.replace("window.__created = 0; window.__ended = 0;", "window.__created = window.__created || 0; window.__ended = window.__ended || 0;")
SETTINGS = {"publicKey": "public_key_test", "agentId": "agent_test", "agentVersion": 0}
ERRORS: list = []


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        return


def serve(folder):
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    configured, bare = tmp_path_factory.mktemp("aria-configured"), tmp_path_factory.mktemp("aria-bare")
    build_site(configured, settings=SETTINGS)
    build_site(bare)
    servers = [serve(configured), serve(bare)]
    yield {"configured": f"http://127.0.0.1:{servers[0].server_address[1]}/", "bare": f"http://127.0.0.1:{servers[1].server_address[1]}/"}
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def driver(tmp_path_factory, channel):
    cfg = Config(business_id="1234567", local_dir=tmp_path_factory.mktemp("aria-browser"), browser_channel=channel)
    drv = PlaywrightBooksyDriver(cfg, session_factory=lambda: LoopbackOnlySession(cfg), notify=lambda m: None, load_timeout_seconds=15)
    yield drv
    drv.close()


def run(driver, url, work, *, width=1440, height=900, motion=None, sdk=SDK_STUB, sdk_hits=None):
    """Open the page fresh at a size with the stub SDK, then ``work(page)``. ``sdk_hits`` (a list) records every time the page asks for the SDK."""

    def go(page):
        if not getattr(page, "_ready", False):
            page.on("console", lambda m: ERRORS.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: ERRORS.append(str(e)))
            page._ready = True

        def stub(route):
            if sdk_hits is not None:
                sdk_hits.append(1)
            route.fulfill(status=200, content_type="application/javascript", headers={"access-control-allow-origin": "*"}, body=sdk)

        page.route(SDK_URL, stub)
        try:
            page.emulate_media(reduced_motion=motion or "no-preference")
            page.set_viewport_size({"width": width, "height": height})
            page.goto("about:blank")  # every test starts from a freshly loaded page
            page.goto(url, wait_until="load")
            page.wait_for_selector("#aria", state="attached")
            return work(page)
        finally:
            page.unroute(SDK_URL)

    return driver._run(go, 90)


def snapshot(page) -> dict:
    return page.evaluate("""() => {
        const el = id => document.getElementById(id);
        return {
            state: el('aria').dataset.state, speaking: el('aria').dataset.speaking || null, widget: !el('aria').hidden,
            text: el('voice-status').hidden ? null : el('voice-status').querySelector('.aria__text').textContent,
            call: !el('aria-call').hidden, hangup: !el('aria-hangup').hidden, bar: !el('call-bar').hidden,
            barText: el('call-bar-text').textContent, created: window.__created || 0, ended: window.__ended || 0,
            ariaCall: document.documentElement.dataset.ariaCall || null, callLabel: el('aria-call').getAttribute('aria-label'),
            toast: el('toast').hidden ? null : el('toast').textContent,
        };
    }""")


def start(page):
    page.click('[data-testid="aria-call"]')
    page.wait_for_function("() => window.__hooks !== undefined")


def go_live(page):
    start(page)
    page.evaluate("window.__hooks.onStatus('live')")


# ---------------------------------------------------------------- at rest


def test_nothing_is_called_by_loading_the_page_or_waiting_and_only_the_call_icon_is_offered(driver, sites):
    def work(page):
        page.wait_for_timeout(1500)
        return snapshot(page)

    shown = run(driver, sites["configured"], work)
    assert shown["created"] == 0 and shown["ariaCall"] is None, "no call exists until a visitor clicks"
    assert shown["widget"] and shown["call"] and not shown["hangup"] and not shown["bar"] and shown["text"] is None and shown["state"] == "idle"


def test_the_avatar_is_on_a_genuinely_transparent_background_with_no_card_scrim_or_scroll_lock(driver, sites):
    def work(page):
        page.click('[data-testid="aria-call"]')
        page.wait_for_function("() => window.__hooks !== undefined")
        return page.evaluate("""async () => {
            const img = document.querySelector('[data-testid=aria-avatar]');
            await img.decode();
            const c = document.createElement('canvas'); c.width = c.height = 100;
            const x = c.getContext('2d'); x.drawImage(img, 0, 0, 100, 100);
            const alpha = (px, py) => x.getImageData(px, py, 1, 1).data[3];
            const clear = el => { const s = getComputedStyle(el); return s.backgroundColor === 'rgba(0, 0, 0, 0)' && s.backgroundImage === 'none' && s.boxShadow === 'none' && s.backdropFilter === 'none'; };
            const covering = [...document.querySelectorAll('body *')].filter(e => { const s = getComputedStyle(e); const r = e.getBoundingClientRect();
                return ['fixed', 'absolute'].includes(s.position) && r.width >= innerWidth * 0.9 && r.height >= innerHeight * 0.9 && s.backgroundColor !== 'rgba(0, 0, 0, 0)' && s.display !== 'none'; })
                .map(e => e.tagName + '.' + e.className);
            const box = document.querySelector('#aria').getBoundingClientRect();
            return {corner: alpha(1, 1), centre: alpha(50, 60), widget: clear(document.getElementById('aria')), stage: clear(document.querySelector('.aria__stage')),
                    covering, overflow: document.body.style.overflow, bodyOverflow: getComputedStyle(document.body).overflow, role: document.getElementById('aria').getAttribute('role'),
                    dialogs: document.querySelectorAll('[role=dialog], [aria-modal=true], dialog').length, share: [box.width / innerWidth, box.height / innerHeight]};
        }""")

    out = run(driver, sites["configured"], work)
    assert out["corner"] == 0 and out["centre"] > 200, "the picture itself has real transparency around the figure"
    assert out["widget"] and out["stage"], "the widget and its stage paint nothing of their own"
    assert out["covering"] == [] and out["dialogs"] == 0 and out["role"] == "region", "no scrim, no dialog, no full-page overlay"
    assert out["overflow"] == "" and out["bodyOverflow"] != "hidden", "the page keeps scrolling during a call"
    assert out["share"][0] < 0.3 and out["share"][1] < 0.45, "compact, not a takeover"


# ---------------------------------------------------------------- one call, real states


def test_the_call_icon_starts_exactly_one_call_and_no_other_click_can_start_a_second(driver, sites):
    def work(page):
        start(page)
        first = snapshot(page)
        for selector in ('.site-header [data-voice-start]', '.hero .btn-light', '.hero .btn-light'):
            page.click(selector)
        page.wait_for_timeout(200)
        return first, snapshot(page)

    first, later = run(driver, sites["configured"], work)
    assert first["created"] == 1 and first["state"] == "connecting" and first["text"] == "Connecting to Aria…"
    assert first["hangup"] and not first["call"] and first["ariaCall"] == "1"
    assert later["created"] == 1, "repeated clicks anywhere never start another call"
    assert later["toast"] == "A call is already connecting."


def test_every_state_and_the_speaking_glow_come_from_the_sdk_and_nowhere_else(driver, sites):
    def work(page):
        seen = {}
        start(page)
        seen["connecting"] = snapshot(page)
        page.evaluate("window.__hooks.onStatus('live')")
        seen["live"] = snapshot(page)
        page.evaluate("window.__hooks.onAgentStartTalking()")
        seen["speaking"] = snapshot(page)
        page.evaluate("window.__hooks.onAgentStopTalking()")
        seen["listening"] = snapshot(page)
        page.wait_for_timeout(1200)
        seen["quiet"] = snapshot(page)
        return seen

    seen = run(driver, sites["configured"], work)
    assert seen["connecting"]["speaking"] is None and seen["connecting"]["state"] == "connecting"
    assert seen["live"]["state"] == "live" and seen["live"]["text"] == "Listening…" and seen["live"]["speaking"] is None, "live is not speaking"
    assert seen["speaking"]["text"] == "Aria is speaking…" and seen["speaking"]["speaking"] == "true"
    assert seen["listening"]["text"] == "Listening…" and seen["listening"]["speaking"] is None
    assert seen["quiet"]["speaking"] is None, "nothing animates by itself"


def test_hanging_up_ends_the_call_once_and_the_call_icon_returns_when_the_sdk_says_it_ended(driver, sites):
    def work(page):
        go_live(page)
        page.click('[data-testid="aria-hangup"]')
        page.wait_for_function("() => window.__ended === 1")
        during = snapshot(page)
        page.evaluate("window.__hooks.onEnd()")
        return during, snapshot(page)

    during, after = run(driver, sites["configured"], work)
    assert during["ended"] == 1
    assert after["state"] == "ended" and after["text"] == "Call ended." and after["call"] and not after["hangup"] and after["ariaCall"] is None and after["created"] == 1


@pytest.mark.parametrize("failure,expect", [
    ("Failed to fetch", "Network problem reaching Aria"),
    ("Origin not allowed (403)", "allowed domains"),
    ("Permission denied", "Microphone blocked"),
    ("boom", "Couldn’t start the call: boom"),
])
def test_an_error_says_what_is_wrong_and_the_call_icon_offers_another_try(driver, sites, failure, expect):
    def work(page):
        start(page)
        page.evaluate(f"window.__hooks.onError(new Error({failure!r}))")
        failed = snapshot(page)
        page.evaluate("window.__hooks.onEnd()")  # the 'ended' that follows an error must not replace its message
        kept = snapshot(page)
        page.click('[data-testid="aria-call"]')
        page.wait_for_function("() => window.__created === 2")
        return failed, kept, snapshot(page)

    failed, kept, retried = run(driver, sites["configured"], work)
    assert failed["state"] == "error" and expect in failed["text"] and failed["call"] and not failed["hangup"] and failed["callLabel"] == "Try calling Aria again"
    assert kept["text"] == failed["text"]
    assert retried["created"] == 2 and retried["state"] == "connecting"


def test_a_call_that_never_connected_says_so(driver, sites):
    def work(page):
        start(page)
        page.evaluate("window.__hooks.onEnd()")
        return snapshot(page)

    shown = run(driver, sites["configured"], work)
    assert shown["state"] == "error" and shown["text"].startswith("The call didn’t connect") and shown["call"] and shown["ariaCall"] is None


def test_without_voice_settings_the_widget_says_so_and_never_loads_the_sdk(driver, sites):
    hits: list = []

    def work(page):
        page.click('[data-testid="aria-call"]')
        page.wait_for_timeout(500)
        return snapshot(page)

    shown = run(driver, sites["bare"], work, sdk_hits=hits)
    assert shown["state"] == "error" and shown["text"] == "Voice isn’t set up on this page yet." and shown["created"] == 0 and hits == []


def test_hanging_up_while_the_sdk_is_still_loading_creates_no_call_at_all(driver, sites):
    def work(page):
        page.click('[data-testid="aria-call"]')
        page.wait_for_function("() => document.getElementById('aria').dataset.state === 'connecting'")
        page.click('[data-testid="aria-hangup"]')
        early = page.evaluate("({state: document.getElementById('aria').dataset.state, call: !document.getElementById('aria-call').hidden})")
        page.wait_for_timeout(2200)
        return early, page.evaluate("({created: window.__created || 0, state: document.getElementById('aria').dataset.state, ariaCall: document.documentElement.dataset.ariaCall || null})")

    early, late = run(driver, sites["configured"], work, sdk=SLOW_SDK_STUB)
    assert early == {"state": "idle", "call": True}
    assert late == {"created": 0, "state": "idle", "ariaCall": None}


# ---------------------------------------------------------------- one attempt at a time: nothing from an older call can touch a newer one

GATED_SDK = "await new Promise(resolve => { window.__releaseSdk = resolve; });" + SDK_STUB  # the SDK arrives only when the test lets it


def test_cancelling_while_the_sdk_loads_and_calling_again_creates_one_call_not_two(driver, sites):
    def work(page):
        page.click('[data-testid="aria-call"]')
        page.wait_for_function("() => typeof window.__releaseSdk === 'function'")
        page.click('[data-testid="aria-hangup"]')
        page.click('[data-testid="aria-call"]')
        page.evaluate("window.__releaseSdk()")
        page.wait_for_function("() => window.__created >= 1")
        page.wait_for_timeout(200)
        return snapshot(page)

    shown = run(driver, sites["configured"], work, sdk=GATED_SDK)
    assert shown["created"] == 1 and shown["ended"] == 0 and shown["state"] == "connecting" and shown["hangup"] and shown["ariaCall"] == "1"


def test_several_call_and_hang_up_cycles_before_the_sdk_loads_still_create_exactly_one_call(driver, sites):
    def work(page):
        page.click('[data-testid="aria-call"]')
        page.wait_for_function("() => typeof window.__releaseSdk === 'function'")
        for _ in range(3):
            page.click('[data-testid="aria-hangup"]')
            page.click('[data-testid="aria-call"]')
        page.evaluate("window.__releaseSdk()")
        page.wait_for_function("() => window.__created >= 1")
        page.wait_for_timeout(200)
        return snapshot(page)

    shown = run(driver, sites["configured"], work, sdk=GATED_SDK)
    assert shown["created"] == 1 and shown["ended"] == 0 and shown["hangup"]


FAILING_SDK = "await new Promise(resolve => { window.__releaseSdk = resolve; }); throw new Error('the SDK could not be loaded');"


def test_an_sdk_that_fails_to_load_is_reported_for_the_current_attempt_and_for_nobody_else(driver, sites):
    def work(page):
        page.click('[data-testid="aria-call"]')
        page.wait_for_function("() => typeof window.__releaseSdk === 'function'")
        page.click('[data-testid="aria-hangup"]')   # this attempt is over before the failure arrives
        page.evaluate("window.__releaseSdk()")
        page.wait_for_timeout(400)
        cancelled = snapshot(page)
        page.click('[data-testid="aria-call"]')      # a new attempt: the SDK has already failed, so this one is told
        page.wait_for_function("() => document.getElementById('aria').dataset.state === 'error'")
        return cancelled, snapshot(page)

    cancelled, current = run(driver, sites["configured"], work, sdk=FAILING_SDK)
    assert cancelled["state"] == "idle" and cancelled["text"] is None and cancelled["call"], "nothing is said about an attempt that was already hung up"
    assert current["state"] == "error" and current["text"].startswith("Network problem reaching Aria") and "SDK could not be loaded" in current["text"] and current["call"] and not current["hangup"]


@pytest.mark.parametrize("late", ["hangup_timer", "repeated_end", "late_error", "late_status", "late_speech"])
def test_nothing_late_from_the_previous_call_can_reset_or_change_the_new_one(driver, sites, late):
    def work(page):
        go_live(page)
        page.evaluate("window.__old = window.__hooks")
        if late == "hangup_timer":
            page.click('[data-testid="aria-hangup"]')  # its safety-net timer is now running
        page.evaluate("window.__old.onEnd()")
        page.click('[data-testid="aria-call"]')
        page.wait_for_function("() => window.__created === 2")
        page.evaluate("window.__hooks.onStatus('live')")
        if late == "hangup_timer":
            page.wait_for_timeout(1900)
        elif late == "repeated_end":
            page.evaluate("window.__old.onEnd()")
        elif late == "late_error":
            page.evaluate("window.__old.onError(new Error('Failed to fetch'))")
        elif late == "late_status":
            page.evaluate("window.__old.onStatus('connecting')")
        else:
            page.evaluate("window.__old.onAgentStartTalking()")
        return snapshot(page)

    shown = run(driver, sites["configured"], work)
    assert shown["ariaCall"] == "1" and shown["state"] == "live" and shown["hangup"] and not shown["call"] and shown["text"] == "Listening…" and shown["speaking"] is None, shown
    assert shown["created"] == 2 and shown["ended"] == (1 if late == "hangup_timer" else 0)


def test_a_repeated_end_after_the_call_ended_does_not_turn_call_ended_into_a_failure(driver, sites):
    def work(page):
        go_live(page)
        page.evaluate("window.__hooks.onEnd(); window.__hooks.onEnd();")
        return snapshot(page)

    shown = run(driver, sites["configured"], work)
    assert shown["state"] == "ended" and shown["text"] == "Call ended."


def test_the_hang_up_safety_net_still_recovers_when_the_sdk_never_says_it_ended(driver, sites):
    def work(page):
        go_live(page)
        page.click('[data-testid="aria-hangup"]')
        page.wait_for_function("() => window.__ended === 1")
        page.wait_for_timeout(1900)  # the SDK sends no end event at all
        return snapshot(page)

    shown = run(driver, sites["configured"], work)
    assert shown["state"] == "ended" and shown["text"] == "Call ended." and shown["call"] and not shown["hangup"] and shown["ariaCall"] is None and shown["ended"] == 1


def test_a_microphone_refusal_that_surfaces_as_an_unhandled_rejection_ends_the_call_it_belongs_to(driver, sites):
    def work(page):
        start(page)
        page.evaluate("setTimeout(() => { Promise.reject(Object.assign(new Error('Permission denied'), {name: 'NotAllowedError'})); }, 0)")  # nobody handles it, as in a browser
        page.wait_for_function("() => document.getElementById('aria').dataset.state === 'error'")
        return snapshot(page)

    shown = run(driver, sites["configured"], work)
    assert shown["text"].startswith("Microphone blocked.") and shown["ended"] == 1 and shown["created"] == 1 and shown["call"] and not shown["hangup"]


# ---------------------------------------------------------------- hiding never ends a call


def test_hiding_a_live_call_keeps_it_with_a_call_bar_that_can_bring_the_avatar_back_or_end_it(driver, sites):
    def work(page):
        go_live(page)
        page.click('[data-testid="aria-hide"]')
        hidden = snapshot(page)
        page.click('#call-show')
        shown = snapshot(page)
        page.click('[data-testid="aria-hide"]')
        page.click('#call-end')
        page.wait_for_function("() => window.__ended === 1")
        return hidden, shown, snapshot(page)

    hidden, shown, ended = run(driver, sites["configured"], work)
    assert not hidden["widget"] and hidden["bar"] and hidden["barText"] == "Aria · Listening…" and hidden["ended"] == 0 and hidden["created"] == 1 and hidden["ariaCall"] == "1"
    assert shown["widget"] and not shown["bar"] and shown["hangup"] and shown["ended"] == 0 and shown["created"] == 1
    assert ended["ended"] == 1 and ended["created"] == 1


def test_hiding_while_connecting_also_shows_the_call_bar(driver, sites):
    def work(page):
        start(page)
        page.click('[data-testid="aria-hide"]')
        return snapshot(page)

    shown = run(driver, sites["configured"], work)
    assert not shown["widget"] and shown["bar"] and shown["barText"].startswith("Aria · Connecting") and shown["ended"] == 0


def test_hiding_an_idle_avatar_leaves_nothing_on_screen_and_a_speak_button_brings_it_back_with_one_call(driver, sites):
    def work(page):
        page.click('[data-testid="aria-hide"]')
        hidden = snapshot(page)
        page.click('.site-header [data-voice-start]')
        page.wait_for_function("() => window.__created === 1")
        return hidden, snapshot(page)

    hidden, back = run(driver, sites["configured"], work)
    assert not hidden["widget"] and not hidden["bar"] and hidden["created"] == 0
    assert back["widget"] and back["created"] == 1 and back["hangup"]


def test_a_call_that_ends_while_the_avatar_is_hidden_brings_it_back_to_say_how_it_ended(driver, sites):
    def work(page):
        go_live(page)
        page.click('[data-testid="aria-hide"]')
        page.evaluate("window.__hooks.onEnd()")
        return snapshot(page)

    shown = run(driver, sites["configured"], work)
    assert shown["widget"] and not shown["bar"] and shown["text"] == "Call ended." and shown["call"]


def test_the_speak_button_on_a_live_call_brings_the_same_call_back_and_starts_no_second(driver, sites):
    def work(page):
        go_live(page)
        page.click('[data-testid="aria-hide"]')
        page.click('.hero .btn-light')
        page.wait_for_timeout(200)
        return snapshot(page)

    shown = run(driver, sites["configured"], work)
    assert shown["widget"] and not shown["bar"] and shown["created"] == 1 and shown["ended"] == 0 and shown["hangup"]
    assert shown["toast"] != "A call is already connecting."


# ---------------------------------------------------------------- keyboard, labels, motion


def test_the_controls_have_names_a_keyboard_reaches_them_and_the_focus_ring_shows(driver, sites):
    def work(page):
        names = page.evaluate("""() => ({
            region: document.getElementById('aria').getAttribute('aria-label'), live: document.getElementById('voice-status').getAttribute('aria-live'),
            alt: document.querySelector('[data-testid=aria-avatar]').alt,
            controls: ['aria-call', 'aria-hangup', 'aria-hide'].map(id => [document.getElementById(id).getAttribute('aria-label'), document.getElementById(id).title])})""")
        page.keyboard.press("Tab")  # keyboard use, so the focus ring is the keyboard one
        page.evaluate("document.getElementById('aria-call').focus()")
        ring = page.evaluate("(() => { const s = getComputedStyle(document.activeElement); return [s.outlineStyle, parseFloat(s.outlineWidth) >= 2]; })()")
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.__created === 1")
        moved = page.evaluate("document.activeElement.id")
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.__ended === 1")
        return names, ring, moved, snapshot(page)

    names, ring, moved, after = run(driver, sites["configured"], work)
    assert names["region"] and names["live"] == "polite" and names["alt"].startswith("Aria") and all(label and title for label, title in names["controls"])
    assert ring == ["solid", True], "a visible keyboard focus ring"
    assert moved == "aria-hangup", "focus follows the control that replaced the one pressed"
    assert after["created"] == 1 and after["ended"] == 1


@pytest.mark.parametrize("motion,expected", [("reduce", ("none", "none")), ("no-preference", ("pulse", "aria-glow"))])
def test_nothing_moves_when_reduced_motion_is_asked_for(driver, sites, motion, expected):
    def work(page):
        go_live(page)
        page.evaluate("window.__hooks.onAgentStartTalking()")
        return page.evaluate("[getComputedStyle(document.querySelector('.aria__dot')).animationName, getComputedStyle(document.querySelector('.aria__img')).animationName]")

    assert tuple(run(driver, sites["configured"], work, motion=motion)) == expected


# ---------------------------------------------------------------- phone


def test_on_a_phone_the_controls_are_big_enough_in_view_and_clear_of_the_action_bar(driver, sites):
    def work(page):
        page.evaluate("window.scrollTo(0, 1500)")
        page.wait_for_timeout(500)
        start(page)
        page.evaluate("window.__hooks.onStatus('live')")
        return page.evaluate("""() => {
            const rect = id => { const r = document.getElementById(id).getBoundingClientRect(); return {w: Math.round(r.width), h: Math.round(r.height), l: r.left, t: r.top, r: r.right, b: r.bottom}; };
            const stage = document.querySelector('.aria__stage').getBoundingClientRect();
            const bar = document.querySelector('.mobile-cta').getBoundingClientRect();
            const status = document.getElementById('voice-status').getBoundingClientRect();
            return {hangup: rect('aria-hangup'), hide: rect('aria-hide'), inner: [innerWidth, innerHeight], scroll: document.documentElement.scrollWidth,
                    stage: {l: stage.left, t: stage.top, r: stage.right, b: stage.bottom}, bar: {t: bar.top, shown: document.querySelector('.mobile-cta').classList.contains('show')},
                    status: {l: status.left, r: status.right, t: status.top}};
        }""")

    out = run(driver, sites["configured"], work, width=390, height=844)
    assert out["bar"]["shown"] is True
    assert min(out["hangup"]["w"], out["hangup"]["h"]) >= 44 and min(out["hide"]["w"], out["hide"]["h"]) >= 44, "comfortable touch targets"
    assert out["stage"]["l"] >= 0 and out["stage"]["r"] <= out["inner"][0] and out["stage"]["b"] <= out["bar"]["t"], "inside the screen and above the action bar"
    assert out["status"]["l"] >= 0 and out["status"]["r"] <= out["inner"][0] and out["status"]["t"] >= 0
    assert out["scroll"] <= out["inner"][0], "no sideways scrolling"


# ---------------------------------------------------------------- the web-sized copy


def png_rgba(width: int, height: int, pixel) -> bytes:
    """A real RGBA PNG made by hand (no imaging library): ``pixel(x, y)`` gives (r, g, b, a)."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width)) for y in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_the_web_copy_is_small_square_webp_and_keeps_real_transparency(driver, tmp_path, channel):
    import base64

    from aria_booking.showcase.site.avatar import optimize_avatar

    source = tmp_path / "cutout.png"
    source.write_bytes(png_rgba(300, 200, lambda x, y: (11, 61, 59, 255) if (x - 150) ** 2 + (y - 100) ** 2 <= 80 ** 2 else (0, 0, 0, 0)))
    out = optimize_avatar(source, tmp_path / "web.webp", size=128, channel=channel)
    data = out.read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) < 20_000, "a small file"

    def look(page):
        return page.evaluate("""async (b64) => {
            const img = new Image(); img.src = 'data:image/webp;base64,' + b64; await img.decode();
            const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
            const x = c.getContext('2d'); x.drawImage(img, 0, 0);
            const a = (px, py) => x.getImageData(px, py, 1, 1).data[3];
            return {w: img.naturalWidth, h: img.naturalHeight, corner: a(1, 1), above: a(64, 20), centre: a(64, 86), bottomLeft: a(2, 126)};
        }""", base64.b64encode(data).decode("ascii"))

    seen = driver._run(look, 60)
    assert seen["w"] == seen["h"] == 128, "square"
    assert seen["corner"] == 0 and seen["above"] == 0 and seen["bottomLeft"] == 0, "transparent around the figure"
    assert seen["centre"] == 255, "opaque where the figure is, resting on the bottom edge"


@pytest.mark.parametrize("name,out_name,size,problem", [("a.gif", "o.webp", 512, ".png, .webp or .jpg"), ("missing.png", "o.webp", 512, "not found"),
                                                        ("a.png", "o.png", 512, "named .webp"), ("a.png", "o.webp", 20, "between 64 and 2048")])
def test_a_bad_resize_request_is_refused_before_any_browser_starts(tmp_path, name, out_name, size, problem):
    from aria_booking.showcase.site.avatar import optimize_avatar

    (tmp_path / "a.gif").write_bytes(b"GIF89a")
    (tmp_path / "a.png").write_bytes(png_rgba(2, 2, lambda x, y: (0, 0, 0, 0)))
    with pytest.raises(ValueError, match=problem):
        optimize_avatar(tmp_path / name, tmp_path / out_name, size=size)


def test_no_script_or_page_error_was_raised_in_any_of_this():
    assert ERRORS == [], ERRORS
