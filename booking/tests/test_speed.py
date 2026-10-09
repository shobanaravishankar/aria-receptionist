"""The speed slice: ONE page read answers a requested slot, timing is recorded as metadata only, and a reused read says how old it is.

Nothing here measures real time against Booksy. These tests prove the structure that makes a later MEASURED run meaningful; they say
nothing about whether 2-3 seconds is achievable.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from aria_booking.booking_service import BookingService
from aria_booking.catalog.bookable import BookableRegistry, BookableService
from aria_booking.config import Config
from aria_booking.ledger import Ledger
from aria_booking.timing import PHASES, PhaseTimer
from aria_booking.voice import launch
from aria_booking.voice.launch import READ_CACHE_SECONDS, build_endpoint, build_tools
from aria_booking.voice.retell_http import RetellEndpoint
from aria_booking.voice.tools import VoiceTools

from fakes import FakeMultiCalendar
from test_launch import KEY, TS, signed

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 8, 12, tzinfo=TZ)
DAY = date(2026, 10, 12)  # Monday
SID = "massage-deep-tissue-massage-60"
CALL = "call_S"


class Clock:
    def __call__(self):
        return NOW


class Ticker:
    """A controllable monotonic clock."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def registry():
    return BookableRegistry([BookableService(SID, "Deep Tissue Massage 60", 60, eligible_staff=frozenset({"lily"}), verified=True, catalog_item=SID)])


def make(tmp_path, *, availability_only=True, lily_busy_days=(), **kw):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    calendar = FakeMultiCalendar(TZ, ["Lily"], business_id="9999999", now=NOW)
    for offset in range(6):
        calendar.set_all_hours(date(2026, 10, 12 + offset))
    for busy in lily_busy_days:
        calendar.add_staff_appointment("Lily", busy, (9, 0), (20, 0))

    def factory(service_cfg):
        return BookingService(service_cfg, calendar, Ledger(cfg.ledger_path, clock=Clock()), clock=Clock(), sleep=lambda s: None)

    tools = VoiceTools(cfg, calendar, factory, Clock(), registry=registry(), require_service_id=True,
                       availability_only=availability_only, booking_enabled=True, **kw)
    return tools, calendar


def ask(tools, time="16:00", day=DAY):
    return tools.check_slot(CALL, {"service_id": SID, "date": day.isoformat(), "time": time, "staff": "Lily"})


FULL_WEEK = tuple(date(2026, 10, 12 + offset) for offset in range(6))


# ---------------------------------------------------------------- one read answers the asked slot


def test_a_requested_slot_that_is_taken_is_answered_from_one_page_read(tmp_path):
    tools, calendar = make(tmp_path, lily_busy_days=(DAY,), check_search_days=1)
    reply = ask(tools)
    assert calendar.read_calls == 1, "the requested day is read once; no other day is loaded to answer"
    assert reply["status"] == "no_alternatives" and reply["days_searched"] == 1
    assert "that day" in reply["speak"] and "next few days" not in reply["speak"], "one day must not be described as the next few days"
    assert "other technicians" in reply["speak"], "a named technician is offered other technicians, not a false claim about other days"


def test_without_a_named_technician_the_one_day_answer_offers_other_days(tmp_path):
    tools, calendar = make(tmp_path, lily_busy_days=(DAY,), check_search_days=1)
    reply = tools.check_slot(CALL, {"service_id": SID, "date": DAY.isoformat(), "time": "16:00"})
    assert calendar.read_calls == 1 and reply["status"] == "no_alternatives"
    assert "that day" in reply["speak"] and "other days" in reply["speak"]


def test_the_wider_search_remains_available_through_find_alternatives(tmp_path):
    tools, calendar = make(tmp_path, lily_busy_days=(DAY,), check_search_days=1)
    ask(tools)
    before = calendar.read_calls
    found = tools.find_alternatives(CALL, {"service_id": SID, "date": DAY.isoformat(), "staff": "Lily"})
    assert calendar.read_calls - before > 1, "the separate search looks at more than one day"
    assert found["status"] in {"alternatives", "offers", "ok"} or found.get("options"), found["status"]


def test_without_the_setting_the_earlier_multi_day_behaviour_is_unchanged(tmp_path):
    tools, calendar = make(tmp_path, lily_busy_days=FULL_WEEK)
    reply = ask(tools)
    assert calendar.read_calls > 1
    assert "next few days" in reply["speak"] and reply["status"] == "no_alternatives"


def test_a_same_day_alternative_comes_from_the_same_read(tmp_path):
    tools, calendar = make(tmp_path, check_search_days=1)
    calendar.add_staff_appointment("Lily", DAY, (15, 0), (17, 30))
    reply = ask(tools, "16:00")
    assert calendar.read_calls == 1
    assert reply["status"] != "available" and reply["options"], "an open time that same day is offered without another page load"


# ---------------------------------------------------------------- a reused read is bounded, read-only and said aloud


def test_a_read_is_reused_inside_the_window_and_not_after_it(tmp_path):
    ticker = Ticker()
    tools, calendar = make(tmp_path, read_cache_seconds=30, monotonic=ticker)
    ask(tools)
    ticker.advance(10)
    ask(tools)
    assert calendar.read_calls == 1
    ticker.advance(21)  # 31 s since the read
    ask(tools)
    assert calendar.read_calls == 2


def test_without_a_cache_window_every_question_reads_the_calendar(tmp_path):
    tools, calendar = make(tmp_path)
    ask(tools)
    ask(tools)
    assert calendar.read_calls == 2


def test_an_old_reused_read_says_how_old_it_is_and_a_fresh_one_says_nothing(tmp_path):
    ticker = Ticker()
    tools, _ = make(tmp_path, read_cache_seconds=60, monotonic=ticker)
    fresh = ask(tools)
    assert "as_of_seconds" not in fresh and "ago" not in fresh["speak"]
    ticker.advance(5)
    recent = ask(tools)
    assert recent.get("as_of_seconds") == 5 and "ago" not in recent["speak"], "a few seconds old is simply current"
    ticker.advance(20)
    old = ask(tools)
    assert old["as_of_seconds"] == 25 and "25 seconds ago" in old["speak"]


def test_a_failed_read_is_not_cached(tmp_path):
    ticker = Ticker()
    tools, calendar = make(tmp_path, read_cache_seconds=30, monotonic=ticker)
    calendar.read_failures = 1
    assert ask(tools)["status"] == "unknown"
    assert ask(tools)["status"] in {"available", "unavailable", "no_alternatives", "alternatives"}


def test_booking_always_re_reads_whatever_the_cache_holds(tmp_path):
    ticker = Ticker()
    tools, calendar = make(tmp_path, availability_only=False, read_cache_seconds=300, monotonic=ticker)
    offer = ask(tools)
    assert offer["status"] == "available", offer
    option = offer["options"][0]["option_id"]
    after_check = calendar.read_calls
    ticker.advance(5)
    tools.book_slot(CALL, {"option_id": option, "confirmed": False})
    tools.book_slot(CALL, {"option_id": option, "confirmed": True})
    assert calendar.read_calls > after_check, "booking verified against a fresh read, not the cached one"


# ---------------------------------------------------------------- the availability-only launch path is the fast one


def test_availability_only_launch_defaults_to_one_day_and_a_short_cache(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    calendar = FakeMultiCalendar(TZ, ["Lily"], business_id="9999999", now=NOW)
    tools = build_tools(cfg, calendar, lambda *a: None, Clock(), booking_approved=False, registry=registry())
    assert tools._check_days == 1 and tools._cache_seconds == READ_CACHE_SECONDS
    assert 0 < READ_CACHE_SECONDS <= 60


def test_booking_launch_keeps_the_cautious_defaults(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    calendar = FakeMultiCalendar(TZ, ["Lily"], business_id="9999999", now=NOW)
    tools = build_tools(cfg, calendar, lambda *a: None, Clock(), booking_approved=True, registry=registry())
    assert tools._cache_seconds == 0 and tools._check_days == tools._search_days


def test_a_caller_can_still_override_the_launch_defaults(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    calendar = FakeMultiCalendar(TZ, ["Lily"], business_id="9999999", now=NOW)
    tools = build_tools(cfg, calendar, lambda *a: None, Clock(), booking_approved=False, registry=registry(), check_search_days=3, read_cache_seconds=0)
    assert tools._check_days == 3 and tools._cache_seconds == 0


# ---------------------------------------------------------------- timing: phase names and milliseconds, nothing else


def test_the_timer_accepts_only_its_fixed_vocabulary():
    timer = PhaseTimer(Ticker())
    with pytest.raises(ValueError):
        with timer.phase("Lily Chen wants a massage"):
            pass
    with pytest.raises(ValueError):
        timer.add("anything else", 5)


def test_the_timer_sums_repeated_phases_and_reports_whole_milliseconds():
    ticker = Ticker()
    timer = PhaseTimer(ticker)
    for _ in range(2):
        with timer.phase("read"):
            ticker.advance(0.4)
    ticker.advance(0.2)
    assert timer.as_dict() == {"read": 800, "total": 1000}
    assert timer.summary() == "total=1000ms read=800ms(x2)"


def test_merging_another_timers_totals_cannot_overwrite_the_request_total_or_add_unknown_names():
    timer = PhaseTimer(Ticker())
    timer.merge({"navigate": 120, "total": 99999, "mystery": 5, "capture": "lots"})
    assert timer.as_dict() == {"navigate": 120, "total": 0}


def test_a_request_records_lock_wait_read_and_search_and_the_text_holds_no_content(tmp_path):
    ticker = Ticker()
    tools, calendar = make(tmp_path, lily_busy_days=(DAY,), check_search_days=1, monotonic=ticker)
    ask(tools)
    timing = tools.last_timing
    assert {"lock_wait", "read", "search", "total"} <= set(timing)
    assert set(timing) <= PHASES
    text = tools.last_timing_text
    for forbidden in ("Lily", "Deep", SID, "9999999", CALL, "2026"):
        assert forbidden not in text


def test_the_drivers_own_phases_are_folded_into_the_request_timing(tmp_path):
    ticker = Ticker()
    tools, calendar = make(tmp_path, monotonic=ticker)
    calendar.last_read_ms = {"navigate": 300, "page_ready": 700, "paint_wait": 1000, "capture": 50, "parse": 12}
    ask(tools)
    assert tools.last_timing["navigate"] == 300 and tools.last_timing["paint_wait"] == 1000 and tools.last_timing["parse"] == 12


def test_the_endpoint_log_line_carries_phase_timing_and_no_arguments(tmp_path):
    tools, _ = make(tmp_path)
    lines = []
    endpoint = RetellEndpoint(tools, KEY, now_ms=lambda: TS, log=lines.append)
    body = json.dumps({"call": {"call_id": CALL}, "args": {"service_id": SID, "date": DAY.isoformat(), "time": "16:00", "staff": "Lily"}}).encode()
    status, _payload = endpoint.handle("POST", "/tools/check_slot", signed(body), body)
    assert status == 200
    (line,) = [l for l in lines if l.startswith("check_slot")]
    assert "total=" in line and "read=" in line
    for forbidden in ("Lily", SID, CALL, "2026-10"):
        assert forbidden not in line


def test_tools_without_timing_still_log_normally(tmp_path):
    class Bare:
        booking_enabled = False

        def check_slot(self, call_id, args):
            return {"status": "unknown", "ok": True, "speak": "x"}

    lines = []
    endpoint = RetellEndpoint(Bare(), KEY, now_ms=lambda: TS, log=lines.append)
    body = json.dumps({"call": {"call_id": CALL}, "args": {}}).encode()
    endpoint.handle("POST", "/tools/check_slot", signed(body), body)
    assert len(lines) == 1 and lines[0].startswith("check_slot: unknown received_ms=") and "[" not in lines[0]


# ---------------------------------------------------------------- the live driver: phases recorded, no needless waiting

from aria_booking.selenium_driver import SeleniumBooksyDriver  # noqa: E402
from aria_booking.discover import LOADER_GONE_JS  # noqa: E402
from test_multistaff_driver import Browser, multi_browser  # noqa: E402


def timed_driver(browser, *, sleeps, clock, **kw):
    def sleep(seconds):
        sleeps.append(seconds)
        clock.advance(seconds)

    return SeleniumBooksyDriver(Config(business_id="1234567"), webdriver_factory=lambda c: browser, notify=lambda m: None, sleep=sleep,
                                monotonic=clock, timing_clock=clock, **kw)


def test_a_read_records_each_phase_in_milliseconds():
    clock, sleeps = Ticker(), []
    drv = timed_driver(multi_browser(), sleeps=sleeps, clock=clock)
    drv.read_day(DAY)
    assert set(drv.last_read_ms) <= PHASES - {"total"}
    assert {"navigate", "page_ready", "paint_wait", "capture", "roster", "parse"} <= set(drv.last_read_ms)
    assert drv.last_read_ms["paint_wait"] == 1000, "the pause after the overlay clears is unchanged until a measured run says otherwise"


def test_the_paint_pause_is_configurable_and_defaults_to_the_proven_one_second():
    clock, sleeps = Ticker(), []
    timed_driver(multi_browser(), sleeps=sleeps, clock=clock).read_day(DAY)
    assert sleeps == [1.0]
    clock, sleeps = Ticker(), []
    timed_driver(multi_browser(), sleeps=sleeps, clock=clock, paint_wait_seconds=0.3).read_day(DAY)
    assert sleeps == [0.3]


class SlowLoader(Browser):
    """The loading overlay stays up for a few checks."""

    def __init__(self, *a, checks=3, **kw):
        super().__init__(*a, **kw)
        self.checks = checks

    def execute_script(self, js):
        if js == LOADER_GONE_JS:
            self.checks -= 1
            return self.checks <= 0
        return super().execute_script(js)


def test_waiting_for_the_loading_overlay_checks_the_local_page_often_not_every_two_seconds():
    plain = multi_browser()
    clock, sleeps = Ticker(), []
    drv = timed_driver(SlowLoader(plain.nodes, plain.roster, checks=4), sleeps=sleeps, clock=clock)
    drv.read_day(DAY)
    waits = [s for s in sleeps if s != 1.0]
    assert waits == [0.25, 0.25, 0.25], waits
    assert drv.last_read_ms["page_ready"] == 750


def test_waiting_for_a_person_to_sign_in_keeps_its_slower_poll():
    clock, sleeps = Ticker(), []
    drv = timed_driver(multi_browser(), sleeps=sleeps, clock=clock)
    assert drv._poll == 2.0 and drv._ready_poll == 0.25


def test_the_phase_record_belongs_to_the_latest_read_and_survives_a_failed_one():
    clock, sleeps = Ticker(), []
    plain = multi_browser()
    broken = SlowLoader(plain.nodes, plain.roster, checks=10 ** 9)
    drv = timed_driver(broken, sleeps=sleeps, clock=clock)
    from aria_booking.calendar_parser import CalendarParseError
    with pytest.raises(CalendarParseError):
        drv.read_day(DAY)
    assert drv.last_read_ms.get("page_ready", 0) >= 40000, "a read that gave up still says where the time went"
    assert "capture" not in drv.last_read_ms


def test_the_timing_clock_is_separate_from_the_logic_clock():
    ticks = iter([0.0])
    clock = Ticker()
    drv = SeleniumBooksyDriver(Config(business_id="1234567"), webdriver_factory=lambda c: multi_browser(), notify=lambda m: None,
                               sleep=lambda s: None, monotonic=lambda: next(ticks), timing_clock=clock)
    drv.read_day(DAY)  # the logic clock would raise StopIteration if timing consumed it
