from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from aria_booking.availability import describe, find_slots, validate_slot
from aria_booking.models import Appointment, DaySnapshot, Interval, ServiceSpec, StaffDay, add_minutes

from conftest import DAY, NOW, TZ

SERVICE = ServiceSpec("Aria Salon", 150)


def at(h, m=0, day=DAY):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)


def iv(h1, m1, h2, m2):
    return Interval(at(h1, m1), at(h2, m2))


def snapshot(working=((9, 0, 17, 0),), time_off=(), appointments=(), *, unknown=()):
    sd = StaffDay(
        "Shobs",
        None if "working" in unknown else tuple(iv(*w) for w in working),
        None if "time_off" in unknown else tuple(iv(*t) for t in time_off),
        None if "appointments" in unknown else tuple(Appointment("Shobs", iv(*a)) for a in appointments),
    )
    return DaySnapshot(DAY, (sd,), NOW)


def starts(search):
    return [s.service.start.strftime("%H:%M") for s in search.slots]


def run(snap, service=SERVICE, **kw):
    kw.setdefault("now", NOW)
    return find_slots(snap, service, "Shobs", **kw)


def test_open_day_offers_every_grid_start_that_ends_by_closing():
    result = run(snapshot())  # 09:00-17:00, 150 min => last start 14:30
    assert starts(result)[0] == "09:00"
    assert starts(result)[-1] == "14:30"
    assert len(result.slots) == 23  # 09:00..14:30 every 15 minutes
    assert not result.unknown


def test_existing_appointment_blocks_overlap_but_touching_is_allowed():
    # appointment 12:00-14:30 -> can end at 12:00 (start 09:30) and start at 14:30
    result = run(snapshot(appointments=[(12, 0, 14, 30)]))
    assert starts(result) == ["09:00", "09:15", "09:30", "14:30"]


def test_buffers_are_part_of_the_blocked_interval():
    service = ServiceSpec("Aria Salon", 150, buffer_before_minutes=15, buffer_after_minutes=15)
    result = run(snapshot(), service)
    # blocked must fit 09:00-17:00: earliest start 09:15, latest start 14:15
    assert starts(result)[0] == "09:15"
    assert starts(result)[-1] == "14:15"


def test_time_off_blocks_slots():
    result = run(snapshot(time_off=[(11, 0, 12, 0)]))
    assert starts(result) == ["12:00", "12:15", "12:30", "12:45", "13:00", "13:15", "13:30", "13:45", "14:00", "14:15", "14:30"]


def test_booking_that_would_run_past_closing_is_rejected():
    check = validate_slot(snapshot(), SERVICE, "Shobs", at(15, 0), now=NOW)  # would end 17:30
    assert not check.ok
    assert any("outside working hours" in r for r in check.reasons)


def test_slot_ending_exactly_at_closing_is_allowed():
    assert validate_slot(snapshot(), SERVICE, "Shobs", at(14, 30), now=NOW).ok


@pytest.mark.parametrize("missing", ["working", "time_off", "appointments"])
def test_unknown_data_is_never_treated_as_free(missing):
    snap = snapshot(unknown=(missing,))
    result = run(snap)
    assert result.slots == ()
    assert result.unknown, "unknown must be reported, not silently empty"
    assert describe(result).startswith("UNKNOWN (not free)")
    check = validate_slot(snap, SERVICE, "Shobs", at(10, 0), now=NOW)
    assert not check.ok and check.unknown


def test_known_not_working_is_distinct_from_unknown():
    result = run(snapshot(working=()))
    assert result.slots == () and not result.unknown
    assert "known not working" in result.notes[0]


def test_cancelled_appointments_do_not_block():
    sd = StaffDay("Shobs", (iv(9, 0, 17, 0),), (), (Appointment("Shobs", iv(9, 0, 11, 30), blocks_time=False),))
    result = run(DaySnapshot(DAY, (sd,), NOW))
    assert starts(result)[0] == "09:00"


def test_minimum_lead_time_and_grid_alignment():
    now = datetime(DAY.year, DAY.month, DAY.day, 10, 7, tzinfo=TZ)  # 10:07 + 60 min = 11:07
    result = run(snapshot(), now=now)
    assert starts(result)[0] == "11:15"  # next grid point at/after 11:07


def test_too_soon_is_refused_for_a_specific_slot():
    now = datetime(DAY.year, DAY.month, DAY.day, 9, 30, tzinfo=TZ)
    check = validate_slot(snapshot(), SERVICE, "Shobs", at(10, 0), now=now, min_lead_minutes=60)
    assert not check.ok and any("too soon" in r for r in check.reasons)


def test_other_staff_are_ignored_and_unknown_staff_is_reported():
    other = StaffDay("Someone Else", (iv(9, 0, 17, 0),), (), ())
    snap = DaySnapshot(DAY, (other,), NOW)
    result = run(snap)
    assert result.slots == () and "not found" in result.unknown[0]


def test_latest_end_limits_the_search():
    result = run(snapshot(), latest_end=at(12, 0))  # 150 min service must end by 12:00 => start <= 09:30
    assert starts(result) == ["09:00", "09:15", "09:30"]


def test_split_shift_is_respected():
    result = run(snapshot(working=((9, 0, 12, 0), (13, 0, 16, 0))))
    assert starts(result) == ["09:00", "09:15", "09:30", "13:00", "13:15", "13:30"]


def test_validate_reports_each_known_conflict_without_customer_data():
    snap = snapshot(time_off=[(9, 0, 9, 30)], appointments=[(10, 0, 11, 0)])
    check = validate_slot(snap, SERVICE, "Shobs", at(9, 15), now=NOW)
    assert not check.ok
    joined = " | ".join(check.reasons)
    assert "time off 09:00-09:30" in joined and "existing appointment 10:00-11:00" in joined


def test_add_minutes_counts_real_time_across_a_dst_change():
    # 2026-11-01 00:30 EDT + 120 real minutes = 01:30 EST (clocks fall back at 02:00 EDT)
    start = datetime(2026, 11, 1, 0, 30, tzinfo=ZoneInfo("America/New_York"))
    end = add_minutes(start, 120)
    assert (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() == 7200
    assert (end.hour, end.minute) == (1, 30)  # wall clock reads 01:30, but it is the second 01:30 (EST)
    assert end.utcoffset().total_seconds() == -5 * 3600
    assert Interval(start, end).minutes == 120
