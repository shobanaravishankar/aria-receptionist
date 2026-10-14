"""The prepared voice page, in a REAL browser against a STUB of the Retell SDK: no Retell account, no network, no microphone, no call.

Sol's finding: when a start failed, ``fail`` showed the specific reason, and Retell's "ended" event that follows then replaced it with the generic
"microphone or domain" message. These tests drive the exact hook sequences (error then end, live then error then end, plain end, retry) and read what is on screen.
The browser can reach only this machine (the same guard as the other browser tests); the CDN SDK and the web fonts are answered locally by the test.
"""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aria_booking.config import Config
from aria_booking.demo import agent_ops
from aria_booking.demo.agent_ops import OperatorConfig, State
from aria_booking.playwright_driver import PlaywrightBooksyDriver

from test_demo_agent_ops import FakeSecrets
from test_playwright_smoke import LoopbackOnlySession, channel, never_left_this_machine  # noqa: F401  (fixtures)

SDK_STUB = """
export class RetellClient {
  constructor(options) { window.__stub = { key: options.key }; }
  createWebCall(options) {
    window.__hooks = options.hooks; window.__options = options;
    return { end: async () => {}, startAudioPlayback() {} };
  }
}
"""
GENERIC = "The call didn’t connect."
DOMAIN = "This web address isn’t on the Retell key’s allowed domains yet"
MICROPHONE = "Microphone blocked."


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        return


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    cfg = OperatorConfig(tunnel_url="https://demo-tunnel.example", state_dir=tmp_path_factory.mktemp("voice-page") / "state")
    State(cfg.state_dir).write("agent.json", {"agent_id": "agent_test", "version": 0})
    assert agent_ops.prepare_web(cfg, FakeSecrets(), lambda m: None) == 0  # the page exactly as the operator prepares it
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(cfg.web_out)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def driver(tmp_path_factory, channel):
    cfg = Config(business_id="1234567", local_dir=tmp_path_factory.mktemp("voice-browser"), browser_channel=channel)
    drv = PlaywrightBooksyDriver(cfg, session_factory=lambda: LoopbackOnlySession(cfg), notify=lambda m: None, load_timeout_seconds=15)
    yield drv
    drv.close()


def run_scenario(driver, site, steps: str) -> dict:
    """Load the page, press the voice button, run ``steps`` (JavaScript that calls the SDK hooks), and report what the panel shows."""

    def work(page):
        if not getattr(page, "_stubbed", False):
            page.route("https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.1/+esm", lambda route: route.fulfill(
                status=200, content_type="application/javascript", headers={"access-control-allow-origin": "*"}, body=SDK_STUB))
            page.route("https://fonts.googleapis.com/**", lambda route: route.fulfill(status=200, content_type="text/css", body=""))
            page.route("https://fonts.gstatic.com/**", lambda route: route.fulfill(status=200, content_type="font/woff2", body=""))
            page._stubbed = True
        page.goto(site, wait_until="load")
        page.wait_for_selector("[data-voice-start]")
        page.click("[data-voice-start] >> nth=0")
        page.wait_for_function("() => window.__hooks !== undefined")
        page.evaluate(steps)
        return page.evaluate("""() => ({
            state: document.querySelector('.voice-status__ring').dataset.state,
            text: document.querySelector('.voice-status__text').textContent,
            retryHidden: document.getElementById('voice-retry').hidden,
            retryLabel: document.getElementById('voice-retry').textContent,
            endHidden: document.getElementById('voice-end').hidden,
            closeHidden: document.querySelector('[data-voice-close]').hidden,
            inCall: document.documentElement.dataset.ariaCall || null,
        })""")

    return driver._run(work, 40)


# ---------------------------------------------------------------- the finding


def test_a_domain_error_stays_on_screen_when_the_ended_event_follows(driver, site):
    shown = run_scenario(driver, site, "window.__hooks.onError(new Error('Forbidden: origin not allowed (403)')); window.__hooks.onEnd();")
    assert shown["state"] == "error" and shown["text"].startswith(DOMAIN) and "403" in shown["text"] and GENERIC not in shown["text"]
    assert shown["retryHidden"] is False and shown["retryLabel"] == "Try again" and shown["closeHidden"] is False and shown["endHidden"] is True and shown["inCall"] is None


def test_a_microphone_error_stays_on_screen_when_the_ended_event_follows(driver, site):
    shown = run_scenario(driver, site, "window.__hooks.onError(new Error('NotAllowedError: Permission denied')); window.__hooks.onEnd();")
    assert shown["text"].startswith(MICROPHONE) and GENERIC not in shown["text"]


def test_an_error_after_the_call_went_live_also_survives_the_ended_event(driver, site):
    shown = run_scenario(driver, site, "window.__hooks.onStatus('live'); window.__hooks.onError(new Error('Failed to fetch')); window.__hooks.onEnd();")
    assert shown["state"] == "error" and shown["text"].startswith("Network problem reaching Aria") and "Failed to fetch" in shown["text"]


def test_a_repeated_ended_event_does_not_change_the_message_either(driver, site):
    shown = run_scenario(driver, site, "window.__hooks.onError(new Error('401 unauthorized')); window.__hooks.onEnd(); window.__hooks.onEnd();")
    assert shown["text"].startswith(DOMAIN)


@pytest.mark.parametrize("raw,expected", [
    ("Domain not allowed", DOMAIN),                     # contains 'not allowed': it is the key's domain list, not the microphone
    ("Forbidden: origin not allowed (403)", DOMAIN),
    ("Request failed with status 401", DOMAIN),
    ("NotAllowedError: Permission denied", MICROPHONE),  # the real microphone refusal still says so
    ("Permission dismissed", MICROPHONE),
    ("Failed to fetch", "Network problem reaching Aria"),
])
def test_each_kind_of_failure_gets_its_own_message(driver, site, raw, expected):
    shown = run_scenario(driver, site, f"window.__hooks.onError(new Error({raw!r})); window.__hooks.onEnd();")
    assert shown["text"].startswith(expected) and GENERIC not in shown["text"]


# ---------------------------------------------------------------- what must NOT change


def test_an_end_with_no_error_before_the_call_connected_still_gets_the_generic_message(driver, site):
    shown = run_scenario(driver, site, "window.__hooks.onEnd();")
    assert shown["state"] == "error" and shown["text"].startswith(GENERIC) and shown["retryHidden"] is False


def test_a_call_that_went_live_and_then_ended_normally_says_so(driver, site):
    shown = run_scenario(driver, site, "window.__hooks.onStatus('live'); window.__hooks.onEnd();")
    assert shown["state"] == "ended" and shown["text"].startswith("Call ended.") and shown["retryLabel"] == "Talk to Aria again"


def test_a_new_attempt_forgets_the_last_failure(driver, site):
    # fail once, press retry, then end WITHOUT an error: the generic message must come back, not the stale one
    run_scenario(driver, site,"window.__hooks.onError(new Error('Forbidden: origin not allowed (403)')); window.__hooks.onEnd();")

    def retry(page):
        page.click("#voice-retry")
        page.wait_for_function("() => document.querySelector('.voice-status__ring').dataset.state === 'connecting'")
        page.evaluate("window.__hooks.onEnd();")
        return page.evaluate("() => document.querySelector('.voice-status__text').textContent")

    text = driver._run(retry, 30)
    assert text.startswith(GENERIC) and DOMAIN not in text


def test_the_page_is_configured_with_public_settings_only_and_the_simulation_banner(driver, site):
    def work(page):
        page.goto(site, wait_until="load")
        return page.evaluate("() => ({cfg: window.RETELL_DEMO, banner: document.querySelector('[data-testid=\"demo-page-banner\"]')?.textContent})")

    shown = driver._run(work, 30)
    assert sorted(shown["cfg"]) == ["agentId", "agentVersion", "publicKey"] and shown["cfg"]["agentId"] == "agent_test"
    assert "Demo calendar: simulated availability · Booking disabled" in shown["banner"]
