"""Issue #3 review direction (Sol): the read-only path is bounded, fails fast, and never turns unknown into available.

Real threads and real loopback HTTP are used where the behaviour IS concurrency; everything else uses the deterministic fakes.
All data is synthetic. Nothing here touches Booksy.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from aria_booking import cli
from aria_booking.catalog.render import AVAILABILITY_TIMEOUTS_MS
from aria_booking.config import Config
from aria_booking.driver import DriverError
from aria_booking.selenium_driver import SeleniumBooksyDriver
from aria_booking.voice import launch
from aria_booking.voice.launch import AVAILABILITY_LOCK_WAIT_SECONDS, READ_DEADLINE_SECONDS, build_endpoint, build_tools
from aria_booking.voice.retell_http import RetellEndpoint, make_http_server

from fakes import FakeMultiCalendar
from test_launch import KEY, TS, signed
from test_speed import CALL, DAY, NOW, SID, Clock, Ticker, ask, make, registry
from test_multistaff_driver import Browser, multi_browser, DAY as DRIVER_DAY

TZ = ZoneInfo("America/New_York")
DRAFT = __import__("pathlib").Path(__file__).resolve().parent.parent / "voice_agent_draft"


class HangingCalendar(FakeMultiCalendar):
    """read_day blocks until released: a browser page that never finishes."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.release = threading.Event()
        self.started = threading.Event()
        self.hang = True
        self.attempts = 0  # reads that reached the driver (read_calls only counts reads that finished)

    def read_day(self, day, include_notes=False):
        self.attempts += 1
        self.started.set()
        if self.hang:
            self.release.wait(10)
        return super().read_day(day, include_notes)


def hanging(tmp_path, **kw):
    tools, calendar = make(tmp_path, **kw)
    hung = HangingCalendar(TZ, ["Lily"], business_id="9999999", now=NOW)
    for offset in range(6):
        hung.set_all_hours(date(2026, 10, 12 + offset))
    tools.driver = hung
    return tools, hung


# ---------------------------------------------------------------- a hard deadline around the calendar read


def test_a_read_that_misses_its_deadline_fails_closed_with_a_short_spoken_fallback(tmp_path):
    tools, hung = hanging(tmp_path, read_deadline_seconds=0.2)
    began = time.monotonic()
    reply = ask(tools)
    assert time.monotonic() - began < 2
    assert reply["status"] == "unknown" and reply["reason"] == "read_timeout" and "options" not in reply
    assert "can't confirm" in reply["speak"] and "available" not in reply["speak"].replace("unavailable", "")
    hung.release.set()


def test_while_the_abandoned_read_still_holds_the_browser_new_requests_fail_at_once(tmp_path):
    tools, hung = hanging(tmp_path, read_deadline_seconds=0.2)
    ask(tools)
    began = time.monotonic()
    second = ask(tools)
    assert time.monotonic() - began < 0.15, "no second deadline wait: the browser is known to be occupied"
    assert second["status"] == "unknown" and second["reason"] == "read_timeout"
    hung.release.set()


def test_after_the_hung_read_finishes_the_next_request_reads_normally_and_the_late_result_is_never_used(tmp_path):
    tools, hung = hanging(tmp_path, read_deadline_seconds=0.2, read_cache_seconds=60)
    assert ask(tools)["status"] == "unknown"
    hung.hang = False
    hung.release.set()
    tools._abandoned.join(2)
    reads = hung.read_calls
    reply = ask(tools)
    assert reply["status"] == "available" and hung.read_calls == reads + 1, "the late read was not cached, so this one read the calendar itself"


def test_the_deadline_does_not_disturb_a_normal_read(tmp_path):
    tools, calendar = make(tmp_path, read_deadline_seconds=5)
    assert ask(tools)["status"] == "available" and calendar.read_calls == 1


def test_a_driver_error_inside_the_worker_still_reaches_the_caller_as_the_same_error(tmp_path):
    tools, calendar = make(tmp_path, read_deadline_seconds=5)
    calendar.read_failures = 1
    assert ask(tools)["status"] == "unknown" and ask(tools).get("reason") != "read_timeout"
    calendar.sign_in_required = True
    assert ask(tools)["status"] == "system_unavailable"


def test_without_a_deadline_the_driver_is_called_directly_as_before(tmp_path):
    tools, calendar = make(tmp_path)
    names = []
    original = calendar.read_day
    calendar.read_day = lambda day, include_notes=False: (names.append(threading.current_thread().name), original(day, include_notes))[1]
    ask(tools)
    assert names == [threading.current_thread().name]


# ---------------------------------------------------------------- overlapping requests: quick 'busy', no queueing


def test_an_overlapping_request_gets_a_quick_busy_instead_of_queueing(tmp_path):
    tools, hung = hanging(tmp_path, read_deadline_seconds=5, lock_timeout_seconds=0.2)
    first = threading.Thread(target=lambda: ask(tools), daemon=True)
    first.start()
    assert hung.started.wait(2)
    began = time.monotonic()
    second = ask(tools)
    assert second["status"] == "busy" and time.monotonic() - began < 1.5
    assert "moment" in second["speak"] and second["ok"] is False
    hung.release.set()
    first.join(3)


def test_lookup_service_never_waits_for_a_busy_browser(tmp_path):
    tools, hung = hanging(tmp_path, read_deadline_seconds=5, lock_timeout_seconds=0.2)
    first = threading.Thread(target=lambda: ask(tools), daemon=True)
    first.start()
    assert hung.started.wait(2)
    began = time.monotonic()
    reply = tools.lookup_service(CALL, {"service_id": SID})
    assert time.monotonic() - began < 0.5 and reply["status"] != "busy"
    hung.release.set()
    first.join(3)


def test_each_request_thread_keeps_its_own_timing(tmp_path):
    tools, hung = hanging(tmp_path, read_deadline_seconds=5, lock_timeout_seconds=0.1)
    seen = {}

    def slow():
        ask(tools)
        seen["slow"] = dict(tools.last_timing)

    thread = threading.Thread(target=slow, daemon=True)
    thread.start()
    assert hung.started.wait(2)
    assert ask(tools)["status"] == "busy"
    assert set(tools.last_timing) == {"lock_wait", "total"}, "the busy request logged its own timing"
    hung.release.set()
    thread.join(3)
    assert "read" in seen["slow"], "and the slow request's timing was not overwritten by it"


def test_the_http_server_answers_a_local_lookup_while_a_calendar_read_is_stuck(tmp_path):
    tools, hung = hanging(tmp_path, read_deadline_seconds=3, lock_timeout_seconds=0.2)
    endpoint = RetellEndpoint(tools, KEY, now_ms=lambda: TS)
    server = make_http_server(endpoint, 0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def post(route, args):
        body = json.dumps({"call": {"call_id": CALL}, "args": args}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{port}/tools/{route}", data=body, headers={**signed(body), "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    try:
        slow = threading.Thread(target=lambda: post("check_slot", {"service_id": SID, "date": DAY.isoformat(), "time": "16:00", "staff": "Lily"}), daemon=True)
        slow.start()
        assert hung.started.wait(3)
        began = time.monotonic()
        assert post("lookup_service", {"service_id": SID})["status"] != "error"
        assert post("check_slot", {"service_id": SID, "date": DAY.isoformat(), "time": "16:00", "staff": "Lily"})["status"] == "busy"
        assert time.monotonic() - began < 2, "neither request waited behind the stuck read"
    finally:
        hung.release.set()
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------- the demo path is bounded by default


def test_the_availability_only_launch_path_is_bounded_and_fails_fast(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    tools = build_tools(cfg, FakeMultiCalendar(TZ, ["Lily"], business_id="9999999", now=NOW), lambda *a: None, Clock(), booking_approved=False, registry=registry())
    assert tools._lock_timeout == AVAILABILITY_LOCK_WAIT_SECONDS <= 3
    assert tools._read_deadline == READ_DEADLINE_SECONDS <= 15


def test_the_booking_launch_path_keeps_its_longer_waits(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    tools = build_tools(cfg, FakeMultiCalendar(TZ, ["Lily"], business_id="9999999", now=NOW), lambda *a: None, Clock(), booking_approved=True, registry=registry())
    assert tools._lock_timeout == 20.0 and tools._read_deadline is None


def test_retell_gives_up_on_a_tool_only_after_the_server_has_answered_but_far_before_thirty_seconds():
    tools = {t["name"]: t for t in json.loads((DRAFT / "tools_availability_only.json").read_text(encoding="utf-8"))["tools"]}
    worst_case_ms = (AVAILABILITY_LOCK_WAIT_SECONDS + READ_DEADLINE_SECONDS) * 1000
    for name in ("check_slot", "find_alternatives"):
        assert worst_case_ms < tools[name]["timeout_ms"] <= 25000, name
    assert tools["lookup_service"]["timeout_ms"] <= 5000
    assert {n: t["timeout_ms"] for n, t in tools.items()} == AVAILABILITY_TIMEOUTS_MS and "book_slot" not in tools


def test_the_voice_server_gives_up_on_a_loading_page_sooner_than_the_interactive_commands():
    assert cli.SERVE_LOAD_TIMEOUT <= 10 and cli.SERVE_LOAD_TIMEOUT + 5 < READ_DEADLINE_SECONDS + 5


def test_the_driver_bounds_a_hung_page_and_a_hung_script_and_a_page_that_never_clears():
    class Recorder(Browser):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.timeouts = []

        def set_page_load_timeout(self, seconds):
            self.timeouts.append(("page", seconds))

        def set_script_timeout(self, seconds):
            self.timeouts.append(("script", seconds))

    plain = multi_browser()
    browser = Recorder(plain.nodes, plain.roster)
    drv = SeleniumBooksyDriver(Config(business_id="1234567"), webdriver_factory=lambda c: browser, notify=lambda m: None, sleep=lambda s: None,
                               monotonic=lambda: 0.0, load_timeout_seconds=10)
    drv.read_day(DRIVER_DAY)
    assert browser.timeouts == [("page", 15), ("script", 15)]


def test_a_page_that_never_finishes_loading_is_refused_after_the_short_wait():
    from aria_booking.calendar_parser import CalendarParseError
    from aria_booking.discover import LOADER_GONE_JS

    class Forever(Browser):
        def execute_script(self, js):
            return False if js == LOADER_GONE_JS else super().execute_script(js)

    plain = multi_browser()
    clock = Ticker()
    drv = SeleniumBooksyDriver(Config(business_id="1234567"), webdriver_factory=lambda c: Forever(plain.nodes, plain.roster), notify=lambda m: None,
                               sleep=clock.advance, monotonic=clock, timing_clock=clock, load_timeout_seconds=10)
    with pytest.raises(CalendarParseError, match="still loading after 10 seconds"):
        drv.read_day(DRIVER_DAY)
    assert clock.now - 1000.0 < 11, "gave up after ~10 s of simulated waiting, not 40"



# ---------------------------------------------------------------- unknown never becomes available, reuse included


def test_an_unknown_read_stays_unknown_when_a_follow_up_reuses_it(tmp_path):
    clock = Ticker()
    tools, calendar = make(tmp_path, read_cache_seconds=30, search_days=1, monotonic=clock)
    calendar.unknown_time_off = True
    first = ask(tools)
    clock.advance(5)
    reads = calendar.read_calls
    second = tools.find_alternatives(CALL, {"service_id": SID, "date": DAY.isoformat(), "time": "16:00", "staff": "Lily"})
    assert calendar.read_calls == reads, "control: the follow-up reused the same snapshot"
    for reply in (first, second):
        assert reply["status"] not in {"available", "alternatives"} and not reply.get("options"), reply


def test_a_blocked_or_unreadable_time_off_card_is_never_read_as_free(tmp_path):
    tools, calendar = make(tmp_path)
    calendar.unknown_time_off = True
    reply = ask(tools)
    assert reply["status"] == "unknown" and not reply.get("options")


def test_unknown_working_hours_are_never_available(tmp_path):
    tools, calendar = make(tmp_path)
    calendar.unknown_working = True
    assert ask(tools)["status"] == "unknown"


def test_a_request_after_closing_or_running_past_closing_is_refused_with_the_reason(tmp_path):
    tools, _ = make(tmp_path)  # hours are 09:00-20:00 in the fixture; the service takes 60 minutes
    late = ask(tools, "20:30")
    assert late["status"] == "alternatives" and late["unavailable_reason"] == "outside_hours" and "8 PM" in late["speak"]
    for option in late["options"]:  # whatever is offered instead must finish by closing (20:00) on its own day
        start = datetime.fromisoformat(option["start"])
        assert start.hour * 60 + start.minute + 60 <= 20 * 60, option
    past = ask(tools, "19:30")
    assert past["status"] != "available" and past["unavailable_reason"] == "ends_after_closing"
    assert "run past the end of" in past["speak"].lower() and "latest start" in past["speak"].lower()


def test_session_expiry_is_a_clean_unavailable_not_a_guess(tmp_path):
    tools, calendar = make(tmp_path)
    calendar.sign_in_required = True
    reply = ask(tools)
    assert reply["status"] == "system_unavailable" and not reply.get("options")


def test_a_partial_or_unreadable_page_is_unknown(tmp_path):
    tools, calendar = make(tmp_path)
    calendar.read_failures = 1
    reply = ask(tools)
    assert reply["status"] == "unknown" and "not guess" in reply["speak"]


def test_a_roster_that_changes_between_reads_is_followed_not_remembered(tmp_path):
    tools, calendar = make(tmp_path)  # no reuse window: every question reads
    assert ask(tools)["status"] == "available"
    calendar.roster = ["Maya"]
    calendar.set_all_hours(DAY)
    gone = ask(tools)
    assert gone["status"] == "staff_unavailable" and gone["reason"] == "staff_not_on_schedule" and not gone.get("options")


# ---------------------------------------------------------------- retries, duplicates and mid-call changes are read-only and consistent


def test_a_duplicate_retell_request_gets_the_same_answer_and_nothing_can_be_written(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    calendar = FakeMultiCalendar(TZ, ["Lily"], business_id="9999999", now=NOW)
    calendar.set_all_hours(DAY)

    def writer(*a, **k):
        raise AssertionError("a booking service must never even be built on the availability-only line")

    tools = build_tools(cfg, calendar, writer, Clock(), booking_approved=False, registry=registry())
    first = ask(tools)
    again = ask(tools)
    for key in ("status", "speak", "ok", "options"):
        assert first.get(key) == again.get(key)
    assert tools.book_slot(CALL, {"option_id": "anything", "confirmed": True})["reason"] == "availability_only"
    assert calendar.appointments == []


def test_changing_the_service_or_staff_mid_call_answers_the_new_question(tmp_path):
    tools, calendar = make(tmp_path)
    calendar.add_staff_appointment("Lily", DAY, (15, 0), (17, 30))
    busy = ask(tools, "16:00")
    assert busy["status"] != "available"
    other_time = ask(tools, "18:00")
    assert other_time["status"] == "available"
    unmapped = tools.check_slot(CALL, {"service_id": "facial-signature-facial-60", "date": DAY.isoformat(), "time": "16:00", "staff": "Lily"})
    assert unmapped["status"] != "available" and not unmapped.get("options")


def test_the_committed_availability_tool_file_is_what_the_generator_produces():
    from aria_booking.catalog.render import build_availability_tools

    source = json.loads((DRAFT / "tools.json").read_text(encoding="utf-8"))
    committed = json.loads((DRAFT / "tools_availability_only.json").read_text(encoding="utf-8"))
    assert build_availability_tools(source) == committed, "regenerate with: python -m aria_booking.catalog.render --write"
    full = {t["name"]: t["timeout_ms"] for t in source["tools"]}
    assert full["check_slot"] == 90000 and full["book_slot"] == 180000, "the booking-test tool file keeps its longer waits"


def test_the_log_line_carries_wall_clock_marks_to_line_up_with_retells_own_call_log(tmp_path):
    ticks = iter([TS, TS + 1, TS + 1500, TS + 1501, TS + 9999])
    tools, _ = make(tmp_path)
    lines = []
    body = json.dumps({"call": {"call_id": CALL}, "args": {"service_id": SID, "date": DAY.isoformat(), "time": "16:00", "staff": "Lily"}}).encode()
    endpoint = RetellEndpoint(tools, KEY, now_ms=lambda: next(ticks, TS + 9999), log=lines.append)
    status, _ = endpoint.handle("POST", "/tools/check_slot", signed(body), body)
    assert status == 200
    (line,) = [l for l in lines if l.startswith("check_slot")]
    received, sent = (int(x) for x in __import__("re").search(r"received_ms=(\d+) sent_ms=(\d+)", line).groups())
    assert sent >= received >= TS
    for forbidden in ("Lily", SID, CALL):
        assert forbidden not in line
