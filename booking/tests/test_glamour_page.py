"""The Glamour Day Spa information assistant PAGE in a real (headless) browser, against a FAKE chat relay and a stubbed Retell SDK on this machine: nothing starts by itself, typed chat, voice
states and transcript lines, duplicate clicks, every failure in plain words, text-only rendering (no HTML injection), five pages at once without mixing, and a phone-sized layout. No network,
no Retell, no microphone, no paid anything. What it cannot show: how real speech, the real SDK's events and the real relay behave: that needs the supervised test."""

from __future__ import annotations

import functools
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from aria_booking.config import Config
from aria_booking.playwright_driver import PlaywrightBooksyDriver

from test_playwright_smoke import LoopbackOnlySession, channel, never_left_this_machine  # noqa: F401  (fixtures)

SITE = Path(__file__).resolve().parents[2] / "glamour_assistant" / "site"
SDK_URL = "https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.1/+esm"
SDK_STUB = (
    "window.__created = 0; window.__ended = 0; window.__options = [];"
    "export class RetellClient { constructor(o) { window.__key = o.key; }"
    " createWebCall(o) { window.__created++; window.__options.push(o); window.__hooks = o.hooks; return { end() { window.__ended++; }, startAudioPlayback() {} }; } }"
)
CONFIG = ("window.GLAMOUR = {retellPublicKey: 'public_key_test', voiceAgentId: 'agent_voice_test', voiceAgentVersion: null, chatApi: 'api/chat', salonPhone: '(908) 668-8828',"
          " bookOnline: 'https://www.warrenglamourdayspanj.com/book-online', sdkUrl: '" + SDK_URL + "', greeting: 'Hi, I am Aria. Ask me about services, hours or location.', chatTimeoutMs: 900};")
PHONE = "(908) 668-8828"


class Relay(BaseHTTPRequestHandler):
    """A stand-in for the chat relay: sessions, echo answers after a random delay, and failures on demand."""

    log: list = []
    mode: dict = {}
    counter = 0
    lock = threading.Lock()

    def log_message(self, *args):
        return

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        cls = Relay
        with cls.lock:
            cls.log.append((self.path, body))
        time.sleep(random.uniform(0.02, 0.15))
        mode = cls.mode
        if mode.get("hang") and (self.path.endswith("/message") or mode.get("hang") == "all"):
            time.sleep(2.5)   # longer than the page's (shortened) deadline, shorter than the server's patience
        if self.path.endswith("/start"):
            if mode.get("start_fail"):
                return self.send(502, {"ok": False, "error": "unavailable", "message": f"I can't answer that right now. Please call the salon at {PHONE} or use the Book Online page on the website."})
            with cls.lock:
                cls.counter += 1
                n = cls.counter
            return self.send(201, {"ok": True, "token": f"tok-{n}", "max_messages": 20})
        if mode.pop("expire_once", False):
            return self.send(401, {"ok": False, "error": "session_invalid"})
        if mode.get("full"):
            return self.send(429, {"ok": False, "error": "chat_full", "message": "This chat has reached its limit. Please start a new chat."})
        if mode.get("fail"):
            return self.send(502, {"ok": False, "error": "unavailable", "message": f"I can't answer that right now. Please call the salon at {PHONE} or use the Book Online page on the website."})
        reply = mode.get("reply") or f"[{body.get('token')}] {body.get('text')}"
        return self.send(200, {"ok": True, "reply": reply, "token": body.get("token"), "remaining": 19})

    def send(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class Site(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0] == "/config.js":
            raw = CONFIG.encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/javascript")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        super().do_GET()

    def do_POST(self):  # noqa: N802
        Relay.do_POST(self)  # the same server answers the relay's paths

    send = Relay.send


@pytest.fixture(scope="module")
def page_url():
    handler = type("Handler", (Site,), {})
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(handler, directory=str(SITE)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def fresh_relay():
    Relay.log, Relay.mode, Relay.counter = [], {}, 0


@pytest.fixture(scope="module")
def driver(tmp_path_factory, channel):
    cfg = Config(business_id="1234567", local_dir=tmp_path_factory.mktemp("glamour-browser"), browser_channel=channel)
    drv = PlaywrightBooksyDriver(cfg, session_factory=lambda: LoopbackOnlySession(cfg), notify=lambda m: None, load_timeout_seconds=15)
    yield drv
    drv.close()


def open_page(page, url, *, width=1280, height=900):
    page.route(SDK_URL, lambda route: route.fulfill(status=200, content_type="application/javascript", headers={"access-control-allow-origin": "*"}, body=SDK_STUB))
    page.set_viewport_size({"width": width, "height": height})
    page.goto("about:blank")
    page.goto(url, wait_until="load")
    page.wait_for_function("() => window.__glamourReady === true")


def texts(page):
    return page.evaluate("() => [...document.querySelectorAll('#log .msg')].map(m => m.textContent)")


def wait_text(page, needle, seconds=8.0):
    page.wait_for_function("(n) => document.getElementById('log').textContent.includes(n)", arg=needle, timeout=seconds * 1000)


def run(driver, url, work, **kw):
    def go(page):
        open_page(page, url, **kw)
        try:
            return work(page)
        finally:
            page.goto("about:blank")

    return driver._run(go, 120)


# ---------------------------------------------------------------- nothing starts by itself
def test_loading_the_page_starts_nothing_and_shows_the_portrait_the_label_and_the_greeting(driver, page_url):
    def work(page):
        page.wait_for_timeout(600)
        return {
            "log": texts(page), "created": page.evaluate("window.__created"), "sdk": page.evaluate("typeof window.__key"), "portrait": page.locator('[data-testid="portrait"]').is_visible(),
            "who": page.locator(".who").inner_text(), "talk": page.locator('[data-testid="talk"]').inner_text(), "voice": page.evaluate("document.documentElement.dataset.voice"),
        }

    out = run(driver, page_url, work)
    assert Relay.log == [], "no chat session was started on load"
    assert out["created"] in (0, None) and out["sdk"] == "undefined", "the voice SDK was not even loaded, let alone a call made"
    assert out["log"] == ["Hi, I am Aria. Ask me about services, hours or location."]
    assert out["portrait"] and "Aria" in out["who"] and "virtual salon assistant" in out["who"]
    assert out["talk"] == "Talk to Aria" and out["voice"] == "idle"


# ---------------------------------------------------------------- typed chat
def test_a_typed_question_gets_an_answer_in_the_same_conversation_and_enter_works_too(driver, page_url):
    def work(page):
        page.fill('[data-testid="text"]', "What are your hours?")
        page.click('[data-testid="send"]')
        wait_text(page, "What are your hours?")
        wait_text(page, "[tok-1] What are your hours?")
        page.fill('[data-testid="text"]', "¿Dónde están?")
        page.press('[data-testid="text"]', "Enter")
        wait_text(page, "[tok-1] ¿Dónde están?")
        page.fill('[data-testid="text"]', "你们几点开门?")
        page.press('[data-testid="text"]', "Enter")
        wait_text(page, "[tok-1] 你们几点开门?")
        return texts(page), page.input_value('[data-testid="text"]')

    log, left = run(driver, page_url, work)
    assert [p for p, _ in Relay.log] == ["/api/chat/start", "/api/chat/message", "/api/chat/message", "/api/chat/message"], "one chat, then one request per question"
    assert log[1:] == ["What are your hours?", "[tok-1] What are your hours?", "¿Dónde están?", "[tok-1] ¿Dónde están?", "你们几点开门?", "[tok-1] 你们几点开门?"]
    assert left == ""


def test_a_double_click_or_double_enter_asks_once_and_send_is_disabled_while_waiting(driver, page_url):
    def work(page):
        page.fill('[data-testid="text"]', "one question")
        page.evaluate("() => { const f = document.getElementById('form'); f.requestSubmit(); f.requestSubmit(); document.getElementById('send').click(); document.getElementById('send').click(); }")
        disabled = page.locator('[data-testid="send"]').is_disabled()
        wait_text(page, "[tok-1] one question")
        page.wait_for_function("() => !document.getElementById('send').disabled")
        return disabled, texts(page)

    disabled, log = run(driver, page_url, work)
    assert disabled
    assert [p for p, _ in Relay.log].count("/api/chat/message") == 1
    assert log.count("one question") == 1


def test_a_second_question_sent_while_the_first_is_still_on_its_way_is_not_sent_and_is_not_lost(driver, page_url):
    def work(page):
        page.evaluate("""() => {
            const input = document.getElementById('text'), form = document.getElementById('form');
            input.value = 'first question'; form.requestSubmit();
            input.value = 'second question'; form.requestSubmit();   // the first has not been answered yet
        }""")
        kept = page.input_value('[data-testid="text"]')
        wait_text(page, "[tok-1] first question")
        page.wait_for_function("() => !document.getElementById('send').disabled")
        return kept, texts(page)

    kept, log = run(driver, page_url, work)
    assert [b["text"] for p, b in Relay.log if p.endswith("/message")] == ["first question"], "only the first question was sent"
    assert kept == "second question", "and what was typed second is still in the box"
    assert "second question" not in log


def test_a_failed_answer_says_so_plainly_with_the_phone_number_keeps_what_was_typed_and_works_again_later(driver, page_url):
    Relay.mode = {"fail": True}

    def work(page):
        page.fill('[data-testid="text"]', "Do you do eyelashes?")
        page.click('[data-testid="send"]')
        wait_text(page, "call the salon at (908) 668-8828")
        kept = page.input_value('[data-testid="text"]')
        Relay.mode = {}
        page.click('[data-testid="send"]')
        wait_text(page, "[tok-1] Do you do eyelashes?")
        return kept, texts(page)

    kept, log = run(driver, page_url, work)
    assert kept == "Do you do eyelashes?"
    assert not any("traceback" in t.lower() or "provider" in t.lower() for t in log)


def test_a_chat_connection_that_never_answers_gives_up_tells_the_truth_keeps_the_question_and_never_retries_by_itself(driver, page_url):
    def work(page):
        Relay.mode = {"hang": True}
        page.fill('[data-testid="text"]', "Do you do waxing?")
        page.click('[data-testid="send"]')
        wait_text(page, "can't tell whether my answer was lost", 8.0)
        state = {
            "send_disabled": page.locator('[data-testid="send"]').is_disabled(), "typing": page.locator("#log .msg__tag", has_text="Aria is typing").count(),
            "kept": page.input_value('[data-testid="text"]'), "log": texts(page), "busy": page.get_attribute("#form", "aria-busy"),
        }
        page.wait_for_timeout(1500)   # nothing is sent again by itself
        state["requests_after_wait"] = [p for p, _ in Relay.log]
        Relay.mode = {}
        page.click('[data-testid="send"]')   # the visitor's own choice to ask again
        wait_text(page, "[tok-1] Do you do waxing?")
        state["after"] = [p for p, _ in Relay.log]
        return state

    state = run(driver, page_url, work)
    assert state["send_disabled"] is False and state["typing"] == 0 and state["busy"] == "false", "the box works again"
    assert state["kept"] == "Do you do waxing?", "the question is kept"
    assert any("longer than it should" in t and PHONE in t for t in state["log"]), "a short truthful failure with the phone number"
    assert state["requests_after_wait"] == ["/api/chat/start", "/api/chat/message"], "no automatic second completion"
    assert state["after"].count("/api/chat/message") == 2


def test_a_start_that_never_answers_also_gives_up_and_does_not_start_a_second_chat_by_itself(driver, page_url):
    def work(page):
        Relay.mode = {"hang": "all"}
        page.fill('[data-testid="text"]', "hours?")
        page.click('[data-testid="send"]')
        wait_text(page, "longer than it should", 8.0)
        page.wait_for_timeout(1500)
        return page.locator('[data-testid="send"]').is_disabled(), page.input_value('[data-testid="text"]'), [p for p, _ in Relay.log]

    disabled, kept, requests = run(driver, page_url, work)
    assert disabled is False and kept == "hours?"
    assert requests == ["/api/chat/start"]


def test_a_voice_transcript_shows_only_spoken_lines_never_tool_results_or_injected_notes_and_updates_keep_their_place(driver, page_url):
    def work(page):
        page.click('[data-testid="talk"]')
        page.wait_for_function("() => window.__created === 1")
        page.evaluate("() => window.__hooks.onStatus('live')")
        page.evaluate("""() => window.__hooks.onTranscript([
            {role: 'user', content: 'What are your hours?', message_id: 'u1'},
            {role: 'tool_call_result', content: 'INTERNAL_TEST_RESULT', message_id: 't1'},
            {role: 'injected', content: 'INTERNAL_TEST_NOTE', message_id: 'i1'},
            {role: 'node_transition', content: 'INTERNAL_TEST_NODE', message_id: 'n1'},
            {role: 'agent', content: 'We are open daily', message_id: 'a1'}])""")
        page.evaluate("""() => window.__hooks.onTranscript([
            {role: 'user', content: 'What are your hours?', message_id: 'u1'},
            {role: 'tool_call_result', content: 'INTERNAL_TEST_RESULT', message_id: 't1'},
            {role: 'agent', content: 'We are open daily from nine thirty to eight.', message_id: 'a1'},
            {role: 'user', content: 'Thank you', message_id: 'u2'}])""")
        page.evaluate("""() => window.__hooks.onTranscript({role: 'agent', content: 'You are welcome.', message_id: 'a2'})""")
        before = page.evaluate("() => [...document.querySelectorAll('#log .msg')].map(m => m.className)")
        page.evaluate("""() => window.__hooks.onTranscript({role: 'agent', content: 'Thank you, anything else?', message_id: 'u2'})""")   # the same line, now attributed to Aria
        after = page.evaluate("() => [...document.querySelectorAll('#log .msg')].map(m => m.className)")
        assert after[-2] == "msg msg--aria" and before[-2] == "msg msg--you", (before, after)
        return texts(page), before

    log, classes = run(driver, page_url, work)
    voice = [t for t in log if "By voice" in t]
    assert voice == ["By voiceWhat are your hours?", "By voiceWe are open daily from nine thirty to eight.", "By voiceThank you, anything else?", "By voiceYou are welcome."]
    assert not any("INTERNAL_TEST" in t for t in log), "tool results, injected notes and node transitions are not speech"
    assert [c for c in classes if "msg--you" in c or "msg--aria" in c][-4:] == ["msg msg--you", "msg msg--aria", "msg msg--you", "msg msg--aria"], "each spoken line keeps its own side"


def test_when_the_relay_is_down_from_the_start_the_visitor_still_gets_the_phone_number(driver, page_url):
    Relay.mode = {"start_fail": True}
    log = run(driver, page_url, lambda page: (page.fill('[data-testid="text"]', "hi"), page.click('[data-testid="send"]'), wait_text(page, "call the salon"), texts(page))[-1])
    assert any(PHONE in t for t in log)


def test_an_expired_chat_is_replaced_with_a_new_one_and_the_question_is_answered(driver, page_url):
    def work(page):
        page.fill('[data-testid="text"]', "first")
        page.click('[data-testid="send"]')
        wait_text(page, "[tok-1] first")
        Relay.mode = {"expire_once": True}
        page.fill('[data-testid="text"]', "second")
        page.click('[data-testid="send"]')
        wait_text(page, "[tok-2] second")
        return texts(page)

    log = run(driver, page_url, work)
    assert any("had expired, so I started a new one" in t for t in log)


def test_a_full_chat_says_so_and_the_next_question_opens_a_fresh_chat(driver, page_url):
    def work(page):
        Relay.mode = {"full": True}
        page.fill('[data-testid="text"]', "q1")
        page.click('[data-testid="send"]')
        wait_text(page, "reached its limit")
        Relay.mode = {}
        page.click('[data-testid="send"]')
        wait_text(page, "[tok-")
        return [p for p, _ in Relay.log]

    paths = run(driver, page_url, work)
    assert paths.count("/api/chat/start") == 2


def test_answers_are_shown_as_text_only_and_only_the_salons_own_address_and_phone_become_links(driver, page_url):
    Relay.mode = {"reply": "<img src=x onerror=window.__pwned=1><b>bold</b> See https://www.warrenglamourdayspanj.com/book-online or https://evil.example/pay or call (908) 668-8828."}

    def work(page):
        page.fill('[data-testid="text"]', "book?")
        page.click('[data-testid="send"]')
        wait_text(page, "See https://www.warrenglamourdayspanj.com/book-online")
        return {
            "pwned": page.evaluate("window.__pwned"), "imgs": page.locator("#log img").count(), "bolds": page.locator("#log b").count(),
            "links": page.evaluate("() => [...document.querySelectorAll('#log a')].map(a => a.getAttribute('href'))"),
        }

    out = run(driver, page_url, work)
    assert out["pwned"] is None and out["imgs"] == 0 and out["bolds"] == 0, "markup in an answer is text, never HTML"
    assert out["links"] == ["https://www.warrenglamourdayspanj.com/book-online", "tel:+19086688828"], "no link to any other address"


# ---------------------------------------------------------------- voice
def drive(page, script):
    return page.evaluate(script)


def test_talk_to_aria_makes_exactly_one_call_with_the_voice_agent_and_a_second_click_cancels_instead_of_starting_another(driver, page_url):
    def work(page):
        page.click('[data-testid="talk"]')
        page.wait_for_function("() => window.__created === 1")
        first = page.locator('[data-testid="talk"]').inner_text()
        page.click('[data-testid="talk"]')  # while connecting: this cancels, it does not start a second call
        page.wait_for_function("() => window.__ended === 1")
        page.wait_for_timeout(300)
        return first, page.evaluate("window.__created"), page.evaluate("window.__ended"), page.evaluate("JSON.stringify(window.__options.map(o => [o.agent_id, o.transcript]))"), page.locator('[data-testid="talk"]').inner_text()

    first, created, ended, options, label = run(driver, page_url, work)
    assert first == "Cancel" and created == 1 and ended == 1 and label == "Talk to Aria"
    assert json.loads(options) == [["agent_voice_test", True]]
    assert Relay.log == [], "voice never touches the chat relay"


def test_a_live_call_shows_listening_speaking_and_transcript_lines_in_the_conversation_then_ends(driver, page_url):
    def work(page):
        page.click('[data-testid="talk"]')
        page.wait_for_function("() => window.__created === 1")
        page.evaluate("() => window.__hooks.onStatus('live')")
        live = (page.locator('[data-testid="talk"]').inner_text(), page.locator('[data-testid="status"]').inner_text())
        page.evaluate("() => window.__hooks.onAgentStartTalking()")
        speaking = page.locator('[data-testid="status"]').inner_text()
        page.evaluate("() => window.__hooks.onTranscript([{role:'user',content:'Hola, ¿qué horario tienen?'}])")
        page.evaluate("() => window.__hooks.onTranscript([{role:'user',content:'Hola, ¿qué horario tienen?'},{role:'agent',content:'Estamos abiertos todos los días de 9:30 a 20:00.'}])")
        page.evaluate("() => window.__hooks.onAgentStopTalking()")
        mid = texts(page)
        page.click('[data-testid="talk"]')  # End call
        page.wait_for_function("() => window.__ended === 1")
        return live, speaking, mid, page.locator('[data-testid="status"]').inner_text(), page.locator('[data-testid="talk"]').inner_text(), page.evaluate("document.documentElement.dataset.voice")

    live, speaking, mid, status, label, state = run(driver, page_url, work)
    assert live == ("End call", "Listening...") and speaking == "Aria is speaking..."
    voice_lines = [t for t in mid if "By voice" in t]
    assert voice_lines == ["By voiceHola, ¿qué horario tienen?", "By voiceEstamos abiertos todos los días de 9:30 a 20:00."], "an updated transcript replaces its lines instead of repeating them"
    assert any("separate conversations" in t for t in mid), "the page says typed chat and the call are separate"
    assert status == "Call ended." and label == "Talk to Aria" and state == "ended"


def test_a_blocked_microphone_and_a_failed_connection_each_say_so_in_plain_words_and_can_be_tried_again(driver, page_url):
    def work(page):
        page.click('[data-testid="talk"]')
        page.wait_for_function("() => window.__created === 1")
        page.evaluate("() => window.__hooks.onError(Object.assign(new Error('Permission denied'), {name: 'NotAllowedError'}))")
        wait_text(page, "couldn't use your microphone")
        page.click('[data-testid="talk"]')
        page.wait_for_function("() => window.__created === 2")
        page.evaluate("() => window.__hooks.onError(new Error('socket closed'))")
        wait_text(page, "couldn't connect the call")
        page.evaluate("() => window.__hooks.onEnd()")  # the SDK also reports the end after an error: nothing changes
        return texts(page), page.locator('[data-testid="talk"]').inner_text()

    log, label = run(driver, page_url, work)
    assert sum("microphone" in t for t in log) == 1 and sum("couldn't connect the call" in t for t in log) == 1
    assert all(PHONE in t for t in log if "couldn't" in t)
    assert label == "Talk to Aria"


def test_a_late_event_from_an_older_call_cannot_touch_the_newer_one(driver, page_url):
    def work(page):
        page.click('[data-testid="talk"]')
        page.wait_for_function("() => window.__created === 1")
        page.evaluate("() => { window.__old = window.__hooks; window.__old.onStatus('live'); }")
        page.click('[data-testid="talk"]')  # End call
        page.wait_for_function("() => window.__ended === 1")
        page.click('[data-testid="talk"]')
        page.wait_for_function("() => window.__created === 2")
        page.evaluate("() => { window.__old.onStatus('live'); window.__old.onTranscript([{role:'agent',content:'stale'}]); window.__old.onEnd(); }")
        page.wait_for_timeout(200)
        return page.locator('[data-testid="talk"]').inner_text(), page.evaluate("document.documentElement.dataset.voice"), texts(page)

    label, state, log = run(driver, page_url, work)
    assert label == "Cancel" and state == "connecting" and not any("stale" in t for t in log)


# ---------------------------------------------------------------- five at once, and a phone
def test_five_pages_at_once_each_chat_and_each_call_without_mixing(driver, page_url):
    def go(page):
        pages = [page] + [page.context.new_page() for _ in range(4)]
        for p in pages:
            open_page(p, page_url)
        for i, p in enumerate(pages):  # five typed questions in flight together, five calls started together
            p.fill('[data-testid="text"]', f"visitor {i} asks about massage")
            p.click('[data-testid="send"]')
            p.click('[data-testid="talk"]')
        results = []
        for i, p in enumerate(pages):
            wait_text(p, f"] visitor {i} asks about massage")   # the ANSWER, not just the visitor's own words
            p.wait_for_function("() => window.__created === 1")
            p.evaluate(f"() => {{ window.__hooks.onStatus('live'); window.__hooks.onTranscript([{{role:'user',content:'voice of visitor {i}'}}]); }}")
            results.append((texts(p), p.evaluate("window.__created"), p.locator('[data-testid="talk"]').inner_text()))
        for p in pages[1:]:
            p.close()
        return results

    results = driver._run(go, 180)
    tokens = []
    for i, (log, created, label) in enumerate(results):
        mine = " ".join(log)
        assert f"visitor {i} asks about massage" in mine and f"voice of visitor {i}" in mine
        assert not any(f"visitor {j} asks" in mine or f"voice of visitor {j}" in mine for j in range(5) if j != i), "nothing from another visitor"
        assert created == 1 and label == "End call"
        tokens.append(next(t for t in log if t.startswith("[tok-")).split("]")[0])
    assert len(set(tokens)) == 5, "five visitors, five separate chats"


def test_on_a_phone_the_portrait_and_header_sit_above_the_conversation_and_nothing_scrolls_sideways(driver, page_url):
    def work(page):
        page.wait_for_timeout(300)
        return page.evaluate("""() => {
            const r = (s) => document.querySelector(s).getBoundingClientRect();
            return {sw: document.documentElement.scrollWidth, iw: window.innerWidth, portrait: r('.portrait'), who: r('.who'), log: r('.log'), composer: r('.composer'), talk: r('#talk'), h1: r('h1')};
        }""")

    out = run(driver, page_url, work, width=390, height=844)
    assert out["sw"] <= out["iw"], "no horizontal scrolling"
    assert out["h1"]["top"] < out["portrait"]["top"] < out["log"]["top"] and out["who"]["bottom"] <= out["log"]["top"]
    assert out["composer"]["top"] > out["log"]["bottom"] - 1 and out["talk"]["width"] > 200
    assert out["portrait"]["width"] >= 64


def test_on_a_computer_the_portrait_sits_beside_the_label(driver, page_url):
    def work(page):
        return page.evaluate("""() => { const p = document.querySelector('.portrait').getBoundingClientRect(), t = document.querySelector('.who__text').getBoundingClientRect(); return {p, t}; }""")

    out = run(driver, page_url, work, width=1280, height=900)
    assert out["t"]["left"] >= out["p"]["right"] - 1 and abs((out["t"]["top"] + out["t"]["bottom"]) / 2 - (out["p"]["top"] + out["p"]["bottom"]) / 2) < 40
