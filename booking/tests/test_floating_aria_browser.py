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
from aria_booking.showcase.config import load_showcase
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
CONTACT_PHONE, CONTACT_TEL = "(212) 555-0111", "+12125550111"   # fictional: a reserved 555-01xx number
TRY_LATER = "Aria is currently unavailable. Please try again in a little while, or contact the salon directly."
WITH_NUMBER = f"Aria is currently unavailable. Please call Serenity Spa directly at {CONTACT_PHONE}."
MICROPHONE = "Microphone blocked. Allow it for this site in your browser, then press Call again."
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
    configured, bare, numbered = tmp_path_factory.mktemp("aria-configured"), tmp_path_factory.mktemp("aria-bare"), tmp_path_factory.mktemp("aria-numbered")
    build_site(configured, settings=SETTINGS)
    build_site(bare)
    build_site(numbered, showcase=load_showcase(overrides={"contact_phone": CONTACT_PHONE}), settings=SETTINGS)   # the same page, with a salon number in the shared showcase configuration
    servers = [serve(configured), serve(bare), serve(numbered)]
    yield {"configured": f"http://127.0.0.1:{servers[0].server_address[1]}/", "bare": f"http://127.0.0.1:{servers[1].server_address[1]}/",
           "numbered": f"http://127.0.0.1:{servers[2].server_address[1]}/"}
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
        for selector in ('.site-header [data-voice-start]', '[data-testid="hero-call"]', '[data-testid="hero-call"]'):
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


RAW_FAILURES = [   # what an SDK or the network may say: none of it may reach a visitor
    "Insufficient credits (402): add funds at https://dashboard.retellai.com/billing for account acct_8f3a12",
    "402 Payment Required",
    "Origin not allowed (403)",
    "401 Unauthorized: invalid public key public_key_live_abc123",
    "Failed to fetch",
    "NetworkError when attempting to fetch resource.",
    "boom",
    "TypeError: Cannot read properties of undefined (reading 'x') at https://cdn.example/sdk.js:1:2",
]
LEAKS = ("402", "403", "401", "https", "dashboard", "acct_", "public_key", "TypeError", "boom", "fetch", "Insufficient", "Payment", "Origin", "Unauthorized", "undefined", "NetworkError", "retellai")


def visible(page) -> str:
    """Everything a visitor can read or hover around the widget: its visible text, the call bar, the toast, and every title, label and alt text inside them."""
    return page.evaluate("""() => {
        const parts = [document.getElementById('aria').innerText, document.getElementById('call-bar').innerText, document.getElementById('toast').innerText];
        for (const el of document.querySelectorAll('#aria *, #call-bar *, #toast')) for (const a of ['title', 'aria-label', 'alt']) if (el.getAttribute(a)) parts.push(el.getAttribute(a));
        return parts.join(' | ');
    }""")


def status_links(page) -> list:
    return page.evaluate("() => Array.from(document.querySelectorAll('#aria a')).map(a => ({href: a.getAttribute('href'), text: a.textContent, inStatus: !!a.closest('#voice-status')}))")


def fail_with(raw):
    def work(page):
        start(page)
        page.evaluate(f"window.__hooks.onError(new Error({raw!r}))")
        failed = {"snap": snapshot(page), "seen": visible(page), "links": status_links(page)}
        page.evaluate("window.__hooks.onEnd()")  # the 'ended' that follows an error must not replace its message
        kept = {"snap": snapshot(page), "links": status_links(page)}
        page.click('[data-testid="aria-call"]')
        page.wait_for_function("() => window.__created === 2")
        return failed, kept, {"snap": snapshot(page), "links": status_links(page)}

    return work


@pytest.mark.parametrize("raw", RAW_FAILURES)
def test_every_failure_that_is_not_the_microphone_says_the_same_calm_thing_and_offers_another_try_with_no_number_to_call(driver, sites, raw):
    failed, kept, retried = run(driver, sites["configured"], fail_with(raw))
    shown = failed["snap"]
    assert shown["state"] == "error" and shown["text"] == TRY_LATER and shown["call"] and not shown["hangup"] and shown["callLabel"] == "Try calling Aria again"
    assert failed["links"] == [] and kept["links"] == [], "no number is configured: no telephone link, not even an empty one"
    assert kept["snap"]["text"] == TRY_LATER and kept["snap"]["state"] == "error"
    for leak in LEAKS:
        assert leak.lower() not in failed["seen"].lower(), (leak, failed["seen"])
    assert retried["snap"]["created"] == 2 and retried["snap"]["state"] == "connecting" and retried["links"] == [], "another try starts a new call and clears the notice"


@pytest.mark.parametrize("raw", RAW_FAILURES)
def test_with_a_salon_number_configured_the_same_failures_offer_it_as_a_click_to_call_link(driver, sites, raw):
    failed, kept, retried = run(driver, sites["numbered"], fail_with(raw))
    shown = failed["snap"]
    assert shown["state"] == "error" and shown["text"] == WITH_NUMBER and shown["callLabel"] == "Try calling Aria again"
    assert failed["links"] == [{"href": f"tel:{CONTACT_TEL}", "text": CONTACT_PHONE, "inStatus": True}]
    assert kept["snap"]["text"] == WITH_NUMBER and kept["links"] == failed["links"], "the 'ended' that follows keeps the notice and its link"
    for leak in LEAKS:
        assert leak.lower() not in failed["seen"].lower(), (leak, failed["seen"])
    assert retried["links"] == [] and retried["snap"]["state"] == "connecting"


@pytest.mark.parametrize("raw", [
    "Permission denied",
    "NotAllowedError: Permission denied by system",
    "Permission dismissed",
    "NotAllowedError: The request is not allowed by the user agent or the platform in the current context.",
])
@pytest.mark.parametrize("site", ["configured", "numbered"])
def test_an_actual_microphone_refusal_keeps_its_own_plain_guidance_and_never_mentions_a_number(driver, sites, raw, site):
    failed, kept, _retried = run(driver, sites[site], fail_with(raw))
    assert failed["snap"]["text"] == MICROPHONE and kept["snap"]["text"] == MICROPHONE
    assert failed["links"] == [] and "unavailable" not in failed["seen"]


@pytest.mark.parametrize("raw", [
    "Domain not allowed",
    "Forbidden: origin not allowed (403)",
    "403 Forbidden: permission denied for this origin",
    "401 Unauthorized: permission denied",
    "Request failed with status 402: permission denied, insufficient balance",
    "Payment required: permission denied until credits are added",
])
def test_an_address_sign_in_or_payment_problem_is_never_taken_for_a_blocked_microphone(driver, sites, raw):
    failed, kept, _retried = run(driver, sites["configured"], fail_with(raw))
    assert failed["snap"]["text"] == TRY_LATER and kept["snap"]["text"] == TRY_LATER and "Microphone" not in failed["seen"]


@pytest.mark.parametrize("site,expected", [("configured", TRY_LATER), ("numbered", WITH_NUMBER)])
def test_a_call_that_ended_without_ever_connecting_gets_the_same_notice_and_nothing_technical(driver, sites, site, expected):
    def work(page):
        start(page)
        page.evaluate("window.__hooks.onEnd()")
        return snapshot(page), visible(page), status_links(page)

    shown, seen, links = run(driver, sites[site], work)
    assert shown["state"] == "error" and shown["text"] == expected and shown["call"] and shown["ariaCall"] is None
    assert "Microphone blocked" not in seen and "allowed domains" not in seen and "Retell" not in seen
    assert [link["text"] for link in links] == ([CONTACT_PHONE] if site == "numbered" else [])


def test_a_call_that_connected_and_then_failed_still_says_so_calmly_and_the_ended_event_cannot_replace_it(driver, sites):
    def work(page):
        go_live(page)
        page.evaluate("window.__hooks.onError(new Error('Failed to fetch'))")
        page.evaluate("window.__hooks.onEnd(); window.__hooks.onEnd();")
        return snapshot(page), status_links(page)

    shown, links = run(driver, sites["numbered"], work)
    assert shown["state"] == "error" and shown["text"] == WITH_NUMBER and len(links) == 1 and shown["ariaCall"] is None


def test_without_voice_settings_the_widget_is_calm_too_never_loads_the_sdk_and_names_no_setting(driver, sites):
    hits: list = []

    def work(page):
        page.click('[data-testid="aria-call"]')
        page.wait_for_timeout(500)
        titles = page.evaluate("() => Array.from(document.querySelectorAll('[data-voice-start]')).map(b => b.title)")
        return snapshot(page), titles

    shown, titles = run(driver, sites["bare"], work, sdk_hits=hits)
    assert shown["state"] == "error" and shown["text"] == TRY_LATER and shown["created"] == 0 and hits == []
    assert titles and all("public key" not in title.lower() and "demo-config" not in title and "agent id" not in title.lower() for title in titles)


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "tel:+12125550111", "12", "+1 212 555 0111", "", "+123456789012345678"])
def test_a_malformed_dialling_value_on_the_page_is_never_turned_into_a_link(driver, sites, bad):
    def work(page):
        page.evaluate(f"document.getElementById('aria').dataset.contactTel = {bad!r}")   # as if the page had been edited: the script trusts nothing it did not check
        start(page)
        page.evaluate("window.__hooks.onError(new Error('boom'))")
        return snapshot(page), status_links(page)

    shown, links = run(driver, sites["numbered"], work)
    assert shown["text"] == TRY_LATER and links == []


def test_the_number_is_a_real_link_a_keyboard_reaches_and_it_is_the_only_new_thing_on_screen(driver, sites):
    def work(page):
        start(page)
        page.evaluate("window.__hooks.onError(new Error('boom'))")
        page.focus("#voice-status a.aria__tel")
        focused = page.evaluate("() => document.activeElement.className + '|' + document.activeElement.getAttribute('href')")
        box = page.evaluate("() => { const r = document.querySelector('#voice-status a.aria__tel').getBoundingClientRect(); return {w: r.width, h: r.height}; }")
        return focused, box, snapshot(page)

    focused, box, shown = run(driver, sites["numbered"], work, width=390, height=800)
    assert focused == f"aria__tel|tel:{CONTACT_TEL}" and box["h"] >= 30 and box["w"] > 60, box
    assert shown["state"] == "error" and shown["call"] and not shown["hangup"] and shown["created"] == 1


def test_the_operator_can_tell_which_kind_of_failure_it_was_from_the_console_and_a_visitor_never_can(driver, sites):
    def work(page):
        seen: list = []

        def listener(message):
            seen.append((message.type, message.text))

        page.on("console", listener)
        try:
            for number, raw in enumerate(("402 Payment Required for account acct_8f3a12", "Origin not allowed (403)", "Failed to fetch", "boom", "Permission denied"), start=1):
                page.click('[data-testid="aria-call"]')   # a new attempt (after an error the Call icon is the retry control)
                page.wait_for_function(f"() => window.__created === {number}")
                page.evaluate(f"window.__hooks.onError(new Error({raw!r}))")
        finally:
            page.remove_listener("console", listener)
        return [text for kind, text in seen if text.startswith("[aria-voice]")], [text for kind, text in seen]

    diagnostics, everything = run(driver, sites["configured"], work)
    assert diagnostics[:5] == ["[aria-voice] billing", "[aria-voice] domain-or-key", "[aria-voice] network", "[aria-voice] unknown", "[aria-voice] microphone"], diagnostics
    for text in everything:
        for leak in ("acct_", "402", "403", "boom", "Payment", "Origin"):
            assert leak not in text, (leak, text)


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
    assert current["state"] == "error" and current["text"] == TRY_LATER and "SDK" not in current["text"] and current["call"] and not current["hangup"]


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
    assert shown["text"] == MICROPHONE and shown["ended"] == 1 and shown["created"] == 1 and shown["call"] and not shown["hangup"]


# ---------------------------------------------------------------- tearing a call down is asynchronous too: the end of an old call finishes whenever it finishes

DEFERRED_END_SDK = """
window.__created = 0; window.__ended = 0; window.__ends = [];
export class RetellClient {
  createWebCall(o) {
    window.__created++; window.__hooks = o.hooks;
    return {
      end: () => { window.__ended++; return new Promise(resolve => window.__ends.push(resolve)); },
      startAudioPlayback() {}
    };
  }
}
"""  # end() stays pending until the test settles it


def successor_before_old_end_settles(page):
    """Call A is live and hung up (its end() still pending); A reports it ended; call B starts and goes live."""
    go_live(page)
    page.evaluate("window.__old = window.__hooks")
    page.click('[data-testid="aria-hangup"]')
    page.wait_for_function("() => window.__ends.length === 1")
    page.evaluate("window.__old.onEnd()")
    page.click('[data-testid="aria-call"]')
    page.wait_for_function("() => window.__created === 2")
    page.evaluate("window.__hooks.onStatus('live')")


def test_a_new_live_call_has_a_working_hang_up_while_the_previous_calls_end_is_still_pending(driver, sites):
    def work(page):
        successor_before_old_end_settles(page)
        before = snapshot(page)
        enabled = page.locator('[data-testid="aria-hangup"]').is_enabled()
        page.click('[data-testid="aria-hangup"]')
        page.wait_for_function("() => window.__ends.length === 2")
        return before, enabled, snapshot(page)

    before, enabled, after = run(driver, sites["configured"], work, sdk=DEFERRED_END_SDK)
    assert before["state"] == "live" and before["hangup"] and enabled, "the old call's pending hang-up must not disable the new call's control"
    assert after["text"] == "Ending the call…" and after["created"] == 2


def test_the_previous_calls_end_finishing_late_cannot_cancel_the_new_calls_safety_net_or_install_its_own(driver, sites):
    def work(page):
        successor_before_old_end_settles(page)
        page.click('[data-testid="aria-hide"]')
        page.click("#call-end")  # the other Hang up control
        page.wait_for_function("() => window.__ends.length === 2")
        page.evaluate("window.__ends[1]()")   # the new call's end settles: its safety net is armed
        page.wait_for_timeout(50)
        page.evaluate("window.__ends[0]()")   # the OLD call's end settles last
        page.wait_for_timeout(1800)
        return snapshot(page)

    shown = run(driver, sites["configured"], work, sdk=DEFERRED_END_SDK)
    assert shown["ariaCall"] is None and shown["call"] and not shown["hangup"] and shown["text"] == "Call ended."


def test_the_old_calls_end_settling_after_the_new_call_is_over_changes_nothing(driver, sites):
    def work(page):
        successor_before_old_end_settles(page)
        page.click('[data-testid="aria-hangup"]')
        page.wait_for_function("() => window.__ends.length === 2")
        page.evaluate("window.__hooks.onEnd()")   # the new call ends normally
        page.evaluate("window.__ends[1](); window.__ends[0]();")
        page.wait_for_timeout(1800)
        return snapshot(page)

    shown = run(driver, sites["configured"], work, sdk=DEFERRED_END_SDK)
    assert shown["state"] == "ended" and shown["text"] == "Call ended." and shown["call"] and not shown["hangup"]


def test_a_second_hang_up_click_while_the_end_is_pending_does_nothing_more(driver, sites):
    def work(page):
        go_live(page)
        page.click('[data-testid="aria-hangup"]')
        page.wait_for_function("() => window.__ends.length === 1")
        disabled = page.locator('[data-testid="aria-hangup"]').is_disabled()
        page.click("#aria-hide")
        page.click("#call-end")                      # the other control, same call
        page.wait_for_timeout(300)
        return disabled, page.evaluate("({ends: window.__ends.length, ended: window.__ended})")

    disabled, counts = run(driver, sites["configured"], work, sdk=DEFERRED_END_SDK)
    assert disabled is True and counts == {"ends": 1, "ended": 1}, "one call is ended once, however many times Hang up is pressed"


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
        page.click('[data-testid="hero-call"]')
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


# ---------------------------------------------------------------- avatar-led call entries: the same one controller, nothing automatic


ENTRIES = [("speak-to-aria", 1440), ("hero-call", 1440), ("band-call", 1440), ("mobile-call", 390)]


def entry_state(page, testid):
    return page.evaluate("""(id) => {
        const el = document.querySelector(`[data-testid="${id}"]`);
        const img = el.querySelector('img'), face = el.querySelector('.call-entry__face'), r = el.getBoundingClientRect(), f = face.getBoundingClientRect();
        const bg = getComputedStyle(el).backgroundColor;
        return {loaded: img.complete && img.naturalWidth > 0, shown: r.width > 0 && r.height > 0, face: Math.round(f.width), height: Math.round(r.height), width: Math.round(r.width), bg,
                label: el.getAttribute('aria-label'), caption: el.querySelector('.call-entry__caption').textContent, src: img.getAttribute('src'), alt: img.getAttribute('alt'),
                floating: document.querySelector('.aria__img').getAttribute('src'), widgets: document.querySelectorAll('[data-testid="aria-widget"]').length,
                overflow: document.documentElement.scrollWidth - innerWidth, inView: r.left >= 0 && r.right <= innerWidth};
    }""", testid)


@pytest.mark.parametrize("testid,width", ENTRIES)
def test_each_avatar_led_entry_shows_the_same_avatar_and_starts_exactly_one_call_through_the_one_controller(driver, sites, testid, width):
    def work(page):
        if testid == "mobile-call":   # the quick-action bar only appears once the visitor has scrolled past the top
            page.evaluate("window.scrollTo(0, 700)")
            page.wait_for_selector('.mobile-cta.show')
        page.locator(f'[data-testid="{testid}"]').scroll_into_view_if_needed()
        page.wait_for_function("(id) => { const i = document.querySelector(`[data-testid=\"${id}\"] img`); return i.complete && i.naturalWidth > 0; }", arg=testid)
        before = entry_state(page, testid)
        snap_before = snapshot(page)
        page.click(f'[data-testid="{testid}"]')
        page.wait_for_function("() => window.__created === 1")
        first = snapshot(page)
        page.click(f'[data-testid="{testid}"]')   # a second press on the entry, or on any other entry, never makes a second call
        page.click('.site-header [data-voice-start]')
        page.wait_for_timeout(250)
        return before, snap_before, first, snapshot(page), entry_state(page, testid)

    before, snap_before, first, later, after = run(driver, sites["configured"], work, width=width, height=900 if width > 600 else 844)
    assert before["loaded"] and before["shown"] and before["alt"] == "" and before["src"] == before["floating"], "decorative picture, the same file the floating widget shows"
    assert before["label"].startswith(before["caption"]) and "AI voice assistant" in before["label"] and "Starts a call" in before["label"], before
    assert before["widgets"] == 1 and after["widgets"] == 1, "no second widget is ever made"
    assert snap_before["created"] == 0 and snap_before["state"] == "idle", "the entry starts nothing by being there"
    assert first["created"] == 1 and first["state"] == "connecting" and first["hangup"] and not first["call"] and first["ariaCall"] == "1"
    assert later["created"] == 1 and later["hangup"], "pressing entries again changes nothing but bringing the same call forward"
    assert before["overflow"] <= 0 and before["inView"], before


@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_an_entry_works_from_the_keyboard_and_shows_a_focus_ring(driver, sites, key):
    def work(page):
        page.focus('[data-testid="hero-call"]')
        ring = page.evaluate("() => { const s = getComputedStyle(document.querySelector('[data-testid=\"hero-call\"]')); return [s.outlineStyle, s.outlineWidth]; }")
        page.keyboard.press(key)
        page.wait_for_function("() => window.__created === 1")
        return ring, snapshot(page)

    ring, shown = run(driver, sites["configured"], work)
    assert ring[0] != "none" and ring[1] == "3px", ring
    assert shown["created"] == 1 and shown["state"] == "connecting"


def test_after_a_failure_any_entry_tries_again_with_a_new_call_and_the_old_one_is_gone(driver, sites):
    def work(page):
        page.click('[data-testid="hero-call"]')
        page.wait_for_function("() => window.__created === 1")
        page.evaluate("window.__hooks.onError(new Error('boom'))")
        failed = snapshot(page)
        page.click('.site-header [data-voice-start]')
        page.wait_for_function("() => window.__created === 2")
        return failed, snapshot(page)

    failed, retried = run(driver, sites["configured"], work)
    assert failed["state"] == "error" and retried["created"] == 2 and retried["state"] == "connecting" and retried["hangup"] and retried["ariaCall"] == "1"


def test_the_hang_up_control_still_ends_a_call_that_began_at_an_entry_and_the_entries_come_back(driver, sites):
    def work(page):
        page.click('[data-testid="band-call"]')
        page.wait_for_function("() => window.__created === 1")
        page.evaluate("window.__hooks.onStatus('live')")
        live = snapshot(page)
        page.click('[data-testid="aria-hangup"]')
        page.wait_for_function("() => window.__ended === 1")
        page.evaluate("window.__hooks.onEnd()")
        return live, snapshot(page), entry_state(page, "band-call")

    live, ended, entry = run(driver, sites["configured"], work)
    assert live["state"] == "live" and live["hangup"] and ended["state"] == "ended" and ended["ended"] == 1 and ended["call"] and entry["shown"]


def test_the_entries_are_sized_for_the_place_they_sit_in_and_none_is_a_white_box(driver, sites):
    def loaded(page, ids):
        for testid in ids:   # the pictures below the first screen load lazily, as they should: bring each into view before looking at it
            page.locator(f'[data-testid="{testid}"]').scroll_into_view_if_needed()
            page.wait_for_function("(id) => { const i = document.querySelector(`[data-testid=\"${id}\"] img`); return i.complete && i.naturalWidth > 0; }", arg=testid)

    def desktop(page):
        loaded(page, ("band-call", "hero-call", "speak-to-aria"))
        return {t: entry_state(page, t) for t in ("speak-to-aria", "hero-call", "band-call")}

    def phone(page):
        page.evaluate("window.scrollTo(0, 700)")
        page.wait_for_selector(".mobile-cta.show")
        loaded(page, ("mobile-call", "hero-call", "speak-to-aria"))
        return {t: entry_state(page, t) for t in ("speak-to-aria", "hero-call", "mobile-call")}

    wide = run(driver, sites["configured"], desktop)
    small = run(driver, sites["configured"], phone, width=390, height=844)
    assert wide["speak-to-aria"]["height"] <= 52 and wide["speak-to-aria"]["face"] <= 40, "the header entry is compact"
    assert 90 <= wide["hero-call"]["face"] <= 112 and 90 <= wide["band-call"]["face"] <= 116, "a portrait where it leads, not a huge one"
    assert small["hero-call"]["face"] <= 84 and small["speak-to-aria"]["face"] <= 40 and small["mobile-call"]["height"] >= 44, "smaller on a phone, and still a full-size touch target"
    for state in (wide["hero-call"], wide["band-call"], small["hero-call"]):
        assert state["bg"] in ("rgba(0, 0, 0, 0)", "transparent"), ("the portrait entries sit on the page, not in a box", state["bg"])
    for state in (wide["speak-to-aria"], small["mobile-call"]):
        assert state["bg"] == "rgb(15, 107, 104)", "the compact entries are the site's teal, never white"
    for state in (*wide.values(), *small.values()):
        assert state["overflow"] <= 0 and state["inView"] and state["loaded"], state


def test_the_default_illustration_still_fills_every_entry_when_no_personal_avatar_is_configured(driver, sites):
    def work(page):
        return page.evaluate("""() => [...document.querySelectorAll('.call-entry img')].map(i => i.getAttribute('src'))""")

    sources = run(driver, sites["configured"], work)
    assert len(sources) == 5 and set(sources) == {"assets/aria-avatar.svg"}


# ---------------------------------------------------------------- gaps found by the mutation checks


@pytest.mark.parametrize("raw", ["Access denied", "Call not allowed on this plan", "Insufficient permission for this agent", "Not allowed"])
def test_only_an_explicit_microphone_refusal_is_called_one_a_vague_denial_is_not(driver, sites, raw):
    failed, kept, _retried = run(driver, sites["configured"], fail_with(raw))
    assert failed["snap"]["text"] == TRY_LATER and kept["snap"]["text"] == TRY_LATER and "Microphone" not in failed["seen"]


@pytest.mark.parametrize("site,raw,expected", [("configured", "Permission denied", MICROPHONE), ("configured", "boom", TRY_LATER), ("numbered", "boom", WITH_NUMBER)])
def test_a_stray_request_to_end_the_call_after_a_failure_keeps_that_failures_own_message(driver, sites, site, raw, expected):
    def work(page):
        start(page)
        page.evaluate(f"window.__hooks.onError(new Error({raw!r}))")
        page.evaluate("document.getElementById('call-end').click()")   # the call bar's End control while nothing is running: it must not turn the failure into something else
        page.wait_for_timeout(150)
        return snapshot(page), status_links(page)

    shown, links = run(driver, sites[site], work)
    assert shown["state"] == "error" and shown["text"] == expected and shown["call"] and shown["ariaCall"] is None
    assert [link["text"] for link in links] == ([CONTACT_PHONE] if expected == WITH_NUMBER else [])
