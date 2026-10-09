"""Review R2 (Sol): a per-read deadline does not bound a multi-day request. The WHOLE read-only request now has a time budget (lock wait +
every day read + processing) below Retell's tool timeout; no read is started once it is spent, the answer is honest about what was not
checked, and the late-read protections still hold.

A controllable clock stands in for time: each fake read advances it, so 6-second reads cost no real seconds. No Booksy.
"""

from __future__ import annotations

import threading
import time
from datetime import date

from aria_booking.catalog.render import AVAILABILITY_TIMEOUTS_MS
from aria_booking.config import Config
from aria_booking.voice import tools as tools_module
from aria_booking.voice.launch import (
    AVAILABILITY_LOCK_WAIT_SECONDS, READ_DEADLINE_SECONDS, REQUEST_BUDGET_SECONDS, build_tools,
)

from fakes import FakeMultiCalendar
from test_readonly_hardening import TZ, hanging
from test_speed import CALL, DAY, FULL_WEEK, NOW, SID, Clock, Ticker, ask, make, registry


def slow(calendar, clock, seconds):
    """Every read takes `seconds` of (controllable) time and returns the real answer."""
    original = calendar.read_day
    days = []

    def read_day(day, include_notes=False):
        days.append(day)
        clock.advance(seconds)
        return original(day, include_notes)

    calendar.read_day = read_day
    return days


def find(tools, time_="16:00"):
    return tools.find_alternatives(CALL, {"service_id": SID, "date": DAY.isoformat(), "time": time_, "staff": "Lily"})


# ---------------------------------------------------------------- Sol's reproduction, in controllable time


def test_four_slow_but_individually_fine_reads_no_longer_run_past_the_tool_timeout(tmp_path):  # Sol's scenario (6 s per day, four busy days)
    clock = Ticker()
    tools, calendar = make(tmp_path, lily_busy_days=FULL_WEEK, read_deadline_seconds=14.0, request_budget_seconds=16.0,
                           check_search_days=1, read_cache_seconds=0, monotonic=clock)
    days = slow(calendar, clock, 6.0)
    began = clock.now
    reply = find(tools)
    assert clock.now - began < AVAILABILITY_TIMEOUTS_MS["find_alternatives"] / 1000, "fake seconds spent"
    assert len(days) == 3, "the fourth day was never requested: the budget was spent"
    assert reply["status"] == "unknown" and reply["reason"] == "search_incomplete" and not reply["options"]


def test_the_same_scenario_with_the_budget_explicitly_off_still_reads_every_day(tmp_path):
    clock = Ticker()
    tools, calendar = make(tmp_path, lily_busy_days=FULL_WEEK, read_deadline_seconds=14.0, request_budget_seconds=0, check_search_days=1,
                           read_cache_seconds=0, monotonic=clock)
    days = slow(calendar, clock, 6.0)
    assert find(tools)["status"] == "no_alternatives" and len(days) == 4 and clock.now - 1000.0 >= 24, "0 means explicitly unbounded"


def test_a_search_cut_short_never_claims_the_range_has_no_openings(tmp_path):
    clock = Ticker()
    tools, calendar = make(tmp_path, lily_busy_days=FULL_WEEK, read_deadline_seconds=14.0, request_budget_seconds=16.0, read_cache_seconds=0, monotonic=clock)
    slow(calendar, clock, 6.0)
    reply = find(tools)
    spoken = reply["speak"].lower()
    assert "ran out of time" in spoken and "can't tell you" in spoken
    assert "don't see any other openings" not in spoken and reply["status"] != "no_alternatives"


def test_a_fully_checked_search_still_says_there_is_nothing_else(tmp_path):
    clock = Ticker()
    tools, calendar = make(tmp_path, lily_busy_days=FULL_WEEK, read_deadline_seconds=14.0, request_budget_seconds=16.0, read_cache_seconds=0,
                           monotonic=clock, search_days=2)
    days = slow(calendar, clock, 1.0)
    assert find(tools)["status"] == "no_alternatives" and len(days) == 2


def test_openings_found_before_the_budget_ran_out_are_offered_with_a_plain_caveat(tmp_path):
    clock = Ticker()
    tools, calendar = make(tmp_path, lily_busy_days=(DAY,), read_deadline_seconds=14.0, request_budget_seconds=16.0, read_cache_seconds=0,
                           monotonic=clock, search_days=4)
    days = slow(calendar, clock, 9.0)  # day one is busy (9 s); day two is free (9 s); day three would start with a negative budget
    reply = find(tools)
    assert len(days) == 2 and reply["status"] == "alternatives" and reply["options"]
    assert reply["search_incomplete"] is True and "only had time to check part of the range" in reply["speak"]
    assert all(o["start"].startswith("2026-10-13") for o in reply["options"]), "only real openings from a real read"


# ---------------------------------------------------------------- mixed cache and fresh reads


def test_cached_days_cost_no_budget(tmp_path):
    clock = Ticker()
    tools, calendar = make(tmp_path, lily_busy_days=(DAY,), read_deadline_seconds=14.0, request_budget_seconds=16.0, read_cache_seconds=300,
                           monotonic=clock, search_days=4)
    tools.check_slot(CALL, {"service_id": SID, "date": "2026-10-13", "time": "16:00", "staff": "Lily"})  # primes day two
    days = slow(calendar, clock, 9.0)
    reply = find(tools)
    assert days == [DAY, date(2026, 10, 14)], "day two was free from the cache; only days one and three cost budget, so day four was never requested"
    starts = {o["start"][:10] for o in reply["options"]}
    assert "2026-10-13" in starts and reply["search_incomplete"] is True


def test_cached_days_are_still_used_after_the_fresh_budget_is_spent(tmp_path):
    clock = Ticker()
    tools, calendar = make(tmp_path, lily_busy_days=(DAY, date(2026, 10, 13)), read_deadline_seconds=14.0, request_budget_seconds=16.0,
                           read_cache_seconds=300, monotonic=clock, search_days=4)
    tools.check_slot(CALL, {"service_id": SID, "date": "2026-10-14", "time": "16:00", "staff": "Lily"})  # primes day three (free)
    days = slow(calendar, clock, 9.0)
    reply = find(tools)
    assert days == [DAY, date(2026, 10, 13)], "two fresh reads spent the budget; the third day came from the cache"
    assert reply["status"] == "alternatives" and all(o["start"].startswith("2026-10-14") for o in reply["options"])


def test_the_oldest_cached_age_is_still_disclosed_on_a_bounded_search(tmp_path):
    clock = Ticker()
    tools, calendar = make(tmp_path, lily_busy_days=(DAY,), read_deadline_seconds=14.0, request_budget_seconds=16.0, read_cache_seconds=300,
                           monotonic=clock, search_days=4)
    tools.check_slot(CALL, {"service_id": SID, "date": "2026-10-13", "time": "16:00", "staff": "Lily"})
    clock.advance(40)
    slow(calendar, clock, 1.0)
    reply = find(tools)
    assert reply["status"] == "alternatives" and reply["as_of_seconds"] == 41 and "41 seconds ago" in reply["speak"]  # 40 s old + the 1 s day-one read before it was reused


# ---------------------------------------------------------------- the budget starts before the lock wait


def test_time_spent_waiting_for_the_lock_comes_out_of_the_same_budget(tmp_path):
    clock = Ticker()
    tools, calendar = make(tmp_path, read_deadline_seconds=14.0, request_budget_seconds=16.0, monotonic=clock)

    class SlowLock:
        def acquire(self, timeout=None):
            clock.advance(15.0)  # the wait consumed almost everything
            return True

        def release(self):
            pass

    tools._lock = SlowLock()
    reply = ask(tools)
    assert calendar.read_calls == 0, "with under 1.5 s left no read was started"
    assert reply["status"] == "unknown" and reply["reason"] == "read_timeout"


def test_each_request_gets_its_own_budget(tmp_path):
    clock = Ticker()
    tools, calendar = make(tmp_path, read_deadline_seconds=14.0, request_budget_seconds=16.0, read_cache_seconds=0, monotonic=clock)
    slow(calendar, clock, 10.0)
    assert ask(tools)["status"] == "available"
    assert ask(tools)["status"] == "available", "the second request did not inherit the first one's spent time"


def test_a_budget_is_per_request_thread(tmp_path):
    clock = Ticker()
    tools, _ = make(tmp_path, read_deadline_seconds=14.0, request_budget_seconds=16.0, monotonic=clock)
    seen = {}

    def work():
        seen["main"] = tools._budget_left()
        return {"status": "x", "ok": False, "speak": ""}

    tools._with_lock(CALL, work)
    thread = threading.Thread(target=lambda: seen.setdefault("other", tools._budget_left()))
    thread.start()
    thread.join()
    assert seen["main"] == 16.0 and seen["other"] is None


# ---------------------------------------------------------------- a read that is still running keeps its protections


def test_after_a_timeout_the_remaining_days_are_not_requested_at_all(tmp_path):
    tools, hung = hanging(tmp_path, read_deadline_seconds=0.2, request_budget_seconds=5.0, search_days=4)
    for offset in range(4):
        hung.add_staff_appointment("Lily", date(2026, 10, 12 + offset), (9, 0), (20, 0))
    reply = find(tools)
    assert hung.attempts == 1, "only the first read reached the driver; the others failed fast without another request to Booksy"
    assert reply["status"] == "unknown"
    hung.release.set()


def test_the_read_wait_is_the_smaller_of_the_per_read_deadline_and_what_is_left(tmp_path):
    tools, hung = hanging(tmp_path, read_deadline_seconds=10.0, request_budget_seconds=1.6)
    began = time.monotonic()
    reply = ask(tools)
    assert time.monotonic() - began < 3, "waited about the 1.6 s left, not the 10 s per-read deadline"
    assert reply["status"] == "unknown" and reply["reason"] == "read_timeout"
    again = ask(tools)
    assert hung.attempts == 1 and again["reason"] == "read_timeout", "the abandoned read still blocks new ones"
    hung.release.set()


def test_a_late_result_after_budget_exhaustion_is_never_cached(tmp_path):
    tools, hung = hanging(tmp_path, read_deadline_seconds=10.0, request_budget_seconds=1.6, read_cache_seconds=60)
    ask(tools)
    hung.hang = False
    hung.release.set()
    tools._abandoned.join(2)
    assert tools._day_cache == {}


# ---------------------------------------------------------------- configuration: the whole request fits under the voice tool timeout


def test_the_launch_budget_sits_under_the_retell_tool_timeouts_with_margin(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    tools = build_tools(cfg, FakeMultiCalendar(TZ, ["Lily"], business_id="9999999", now=NOW), lambda *a: None, Clock(), booking_approved=False,
                        registry=registry())
    assert tools._request_budget == REQUEST_BUDGET_SECONDS
    for name in ("check_slot", "find_alternatives"):
        assert REQUEST_BUDGET_SECONDS + 3 <= AVAILABILITY_TIMEOUTS_MS[name] / 1000, "at least three seconds of margin for transport and speech"
    assert AVAILABILITY_LOCK_WAIT_SECONDS < REQUEST_BUDGET_SECONDS and READ_DEADLINE_SECONDS <= REQUEST_BUDGET_SECONDS


def test_booking_mode_is_not_given_a_budget(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    tools = build_tools(cfg, FakeMultiCalendar(TZ, ["Lily"], business_id="9999999", now=NOW), lambda *a: None, Clock(), booking_approved=True,
                        registry=registry())
    assert tools._request_budget is None and tools._read_deadline is None and tools._budget_left() is None


def test_a_budget_alone_still_uses_the_bounded_read_path(tmp_path):
    tools, calendar = make(tmp_path, request_budget_seconds=5.0)
    assert tools._read_deadline == 5.0
    assert ask(tools)["status"] == "available"


def test_a_tiny_budget_is_raised_to_the_minimum_that_can_complete_a_read(tmp_path):
    tools, _ = make(tmp_path, request_budget_seconds=0.01)
    assert tools._request_budget == tools_module.MIN_READ_SECONDS


# ---------------------------------------------------------------- Sol's exact construction (a read deadline and nothing else) is bounded too


def test_a_read_deadline_alone_bounds_the_whole_request(tmp_path):  # Sol's construction: read_deadline_seconds=14.0, no explicit budget
    clock = Ticker()
    tools, calendar = make(tmp_path, lily_busy_days=FULL_WEEK, read_deadline_seconds=14.0, check_search_days=1, read_cache_seconds=0, monotonic=clock)
    assert tools._request_budget == 14.0
    days = slow(calendar, clock, 6.0)
    began = clock.now
    reply = find(tools)
    assert clock.now - began < AVAILABILITY_TIMEOUTS_MS["find_alternatives"] / 1000
    assert len(days) == 3 and reply["status"] == "unknown" and reply["reason"] == "search_incomplete"


def test_in_real_time_a_slow_multi_day_search_stops_inside_its_budget(tmp_path):
    tools, calendar = make(tmp_path, lily_busy_days=FULL_WEEK, read_deadline_seconds=2.0, check_search_days=1, read_cache_seconds=0)
    original = calendar.read_day
    reads = []

    def slow_read(day, include_notes=False):
        reads.append(day)
        time.sleep(0.7)
        return original(day, include_notes)

    calendar.read_day = slow_read
    began = time.monotonic()
    reply = find(tools)
    assert time.monotonic() - began < 2.6 and len(reads) < 4
    assert reply["status"] == "unknown" and reply["reason"] == "search_incomplete"
