"""Review S1/M3 (Sol): freshness is disclosed on EVERY reply built from a reused read, and a verified one-person roster keeps its id.

The first test of each group is Sol's probe, ported unchanged in substance. All data is synthetic.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from aria_booking.catalog.bookable import BookableRegistry, BookableService
from aria_booking.config import Config
from aria_booking.voice.launch import build_endpoint

from multistaff_pages import build_page, roster_items, to_raw
from test_multistaff_driver import Browser, DAY, TZ, make as make_driver
from test_staff_census import load, to_raw as legacy_raw
from test_speed import CALL, SID, Ticker, ask, make as make_tools


# ---------------------------------------------------------------- S1: freshness on alternatives


def test_wider_alternatives_disclose_the_age_of_reused_calendar_data(tmp_path):  # Sol's probe
    clock = Ticker()
    tools, calendar = make_tools(tmp_path, read_cache_seconds=30, search_days=1, monotonic=clock)
    assert ask(tools)["status"] == "available"
    reads = calendar.read_calls
    clock.advance(25)
    result = tools.find_alternatives(CALL, {"service_id": SID, "date": DAY.isoformat(), "time": "16:00", "staff": "Lily"})
    assert result.get("options"), result
    assert calendar.read_calls == reads, "control: this response reused the earlier snapshot"
    assert result.get("as_of_seconds", 0) >= 25
    assert "25 seconds ago" in result["speak"]


def test_a_fresh_wider_search_says_nothing_about_age(tmp_path):
    tools, _ = make_tools(tmp_path, read_cache_seconds=30, search_days=1, monotonic=Ticker())
    result = tools.find_alternatives(CALL, {"service_id": SID, "date": DAY.isoformat(), "time": "16:00", "staff": "Lily"})
    assert result.get("options") and "as_of_seconds" not in result and "ago" not in result["speak"]


def test_a_mixed_age_search_discloses_the_oldest_read_it_used(tmp_path):
    clock = Ticker()
    tools, calendar = make_tools(tmp_path, read_cache_seconds=60, search_days=2, monotonic=clock, lily_busy_days=(DAY,))
    next_day = date(2026, 10, 13)
    tools.check_slot(CALL, {"service_id": SID, "date": next_day.isoformat(), "time": "16:00", "staff": "Lily"})  # primes the second day only
    clock.advance(40)
    reads = calendar.read_calls
    result = tools.find_alternatives(CALL, {"service_id": SID, "date": DAY.isoformat(), "time": "16:00", "staff": "Lily"})
    assert calendar.read_calls == reads + 1, "day one was read fresh, day two came from the earlier read"
    assert result["as_of_seconds"] == 40 and "40 seconds ago" in result["speak"], "the oldest contributing read is the one disclosed"


def test_check_slot_discloses_a_reused_alternative_read_older_than_its_own_read(tmp_path):
    clock = Ticker()
    tools, calendar = make_tools(tmp_path, read_cache_seconds=60, search_days=2, check_search_days=2, monotonic=clock, lily_busy_days=(DAY,))
    next_day = date(2026, 10, 13)
    tools.check_slot(CALL, {"service_id": SID, "date": next_day.isoformat(), "time": "16:00", "staff": "Lily"})
    clock.advance(45)
    reply = ask(tools, "16:00", DAY)  # day one is read now; the alternatives on day two are 45 s old
    assert reply["status"] in {"alternatives", "no_alternatives"} and reply.get("as_of_seconds") == 45 and "45 seconds ago" in reply["speak"]


def test_a_staff_unavailable_reply_from_a_reused_read_is_stamped_too(tmp_path):
    clock = Ticker()
    tools, calendar = make_tools(tmp_path, read_cache_seconds=60, monotonic=clock)
    tools.check_slot(CALL, {"service_id": SID, "date": DAY.isoformat(), "time": "16:00", "staff": "Lily"})
    clock.advance(30)
    reply = tools.find_alternatives(CALL, {"service_id": SID, "date": DAY.isoformat(), "time": "16:00", "staff": "Priya"})
    assert reply["status"] == "staff_unavailable" and reply.get("as_of_seconds") == 30 and "30 seconds ago" in reply["speak"]


def test_warm_answers_are_logged_as_cache_hits_not_as_booksy_reads(tmp_path):
    clock = Ticker()
    tools, calendar = make_tools(tmp_path, read_cache_seconds=60, monotonic=clock)
    ask(tools)
    assert "read" in tools.last_timing and "cache_hit" not in tools.last_timing
    clock.advance(5)
    ask(tools)
    assert "cache_hit" in tools.last_timing and "read" not in tools.last_timing, tools.last_timing
    assert "cache_hit=" in tools.last_timing_text and "read=" not in tools.last_timing_text


# ---------------------------------------------------------------- M3: the verified id survives the single-staff snapshot


def one_person_browser(roster=True, column_id="1001"):
    return Browser(
        to_raw(build_page([{"id": column_id, "name": "Shobs", "hours": "10AM-8PM"}])),
        roster_items([("1001", "Shobs")]) if roster else None,
        staff_nodes=load("staff_page_one_member.json"),
    )


def test_single_staff_snapshot_preserves_the_verified_id_for_service_eligibility():  # Sol's probe
    snapshot = make_driver(one_person_browser()).read_day(DAY)
    assert len(snapshot.staff_days) == 1 and snapshot.staff_days[0].staff == "Shobs"
    assert snapshot.staff_days[0].staff_id == "1001"


def test_the_id_is_kept_on_later_reads_too():
    driver = make_driver(one_person_browser())
    for _ in range(2):
        assert driver.read_day(DAY).staff_days[0].staff_id == "1001"


def test_an_id_seen_only_on_a_column_is_not_promoted_to_a_verified_id():
    driver = make_driver(one_person_browser(roster=False))
    assert driver.read_day(DAY).staff_days[0].staff_id == "", "no filter vouches for it, so it stays unknown"
    assert driver._single_staff_id == "1001", "but it is remembered, so a later change is still caught"


def test_the_legacy_page_without_any_id_keeps_an_unknown_id():
    browser = Browser(legacy_raw(load("empty_day_mon_12_oct.json")), None, staff_nodes=load("staff_page_one_member.json"))
    assert make_driver(browser).read_day(DAY).staff_days[0].staff_id == ""


def test_a_one_person_salon_can_use_id_based_service_mappings_end_to_end():
    driver = make_driver(one_person_browser())
    registry = BookableRegistry([
        BookableService("tx-60", "Treatment 60", 60, eligible_staff_ids=frozenset({"1001"}), verified=True),
        BookableService("tx-other", "Other 60", 60, eligible_staff_ids=frozenset({"9999"}), verified=True),
    ])
    tools = build_endpoint(Config(business_id="1234567"), driver, lambda *a: (_ for _ in ()).throw(AssertionError("no writer")),
                           lambda: datetime(2026, 10, 8, 12, tzinfo=TZ), "k", registry=registry, search_days=1)._tools
    ok = tools.check_slot("c", {"service_id": "tx-60", "date": DAY.isoformat(), "time": "16:00", "staff": "Shobs"})
    assert ok["status"] == "available", ok
    other = tools.check_slot("c", {"service_id": "tx-other", "date": DAY.isoformat(), "time": "16:00", "staff": "Shobs"})
    assert other["status"] == "staff_unavailable" and other["reason"] == "staff_not_eligible", other
