"""Regression tests for the independent review of PR #1 (findings R1-R7, plus the five earlier cases).

Each test reproduces a failure path that was demonstrated against the previous code. They use only the in-memory
FakeCalendar and temporary ledgers; no browser or account is touched.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from aria_booking.appointment_creator import attach_notes
from aria_booking.availability import _ceil_to_grid, find_slots
from aria_booking.booking_service import BookingService, Status
from aria_booking.config import Config
from aria_booking.driver import DriverError
from aria_booking.ledger import Ledger, State, ref_from_key, request_key
from aria_booking.models import Appointment, DaySnapshot, Interval, StaffDay

from fakes import FakeCalendar

TZ = ZoneInfo("America/New_York")
DAY = date(2026, 10, 12)
NOW = datetime(2026, 10, 8, 12, tzinfo=TZ)


@pytest.fixture
def setup(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    calendar = FakeCalendar(TZ, now=NOW)
    calendar.set_hours(DAY, 9, 20)
    ledger = Ledger(cfg.ledger_path, clock=lambda: NOW)
    service = BookingService(cfg, calendar, ledger, clock=lambda: NOW, sleep=lambda _: None)
    return cfg, calendar, service, calendar.at(DAY, 10)


def state_of(service, cfg, start):
    key = request_key(cfg.business_id, cfg.staff_name, cfg.service_name, start, cfg.service_duration_minutes)
    return service.ledger.get(key).state


# ---------------------------------------------------------------- R1: duplicate references


def duplicate_on_create(calendar):
    calendar.on_create = lambda cal, spec: cal.appointments.append(Appointment(spec.staff, spec.interval, spec.service_name, spec.note))


def test_r1_two_records_with_one_reference_are_never_a_success_on_the_first_booking(setup):
    cfg, calendar, service, start = setup
    duplicate_on_create(calendar)
    first = service.book(start)
    assert len(calendar.appointments) == 2
    assert not first.ok and first.status is Status.VERIFY_MISMATCH
    assert state_of(service, cfg, start) == State.UNCERTAIN


def test_r1_nor_on_retry_nor_in_verify(setup):
    cfg, calendar, service, start = setup
    duplicate_on_create(calendar)
    service.book(start)
    retried, verified = service.book(start), service.verify(start)
    assert not retried.ok and not verified.ok
    assert retried.status is Status.VERIFY_MISMATCH and verified.status is Status.VERIFY_MISMATCH
    assert len(calendar.create_calls) == 1, "nothing was created again"


def test_r1_a_duplicate_that_appears_later_turns_an_earlier_success_into_a_non_success(setup):
    cfg, calendar, service, start = setup
    assert service.book(start).ok
    calendar.appointments.append(replace(calendar.appointments[0]))
    assert not service.book(start).ok and not service.verify(start).ok
    assert state_of(service, cfg, start) == State.UNCERTAIN


# ---------------------------------------------------------------- R2: an unresolved conflict is not forgotten


def overlap_on_create(calendar):
    calendar.on_create = lambda cal, spec: cal.add_appointment(DAY, (11, 0), (12, 0))


def test_r2_a_conflict_is_reported_and_the_ledger_is_not_left_verified(setup):
    cfg, calendar, service, start = setup
    overlap_on_create(calendar)
    first = service.book(start)
    assert first.status is Status.BOOKED_CONFLICT_DETECTED and not first.ok
    assert state_of(service, cfg, start) == State.UNCERTAIN


def test_r2_retry_and_verify_report_the_still_unresolved_conflict(setup):
    cfg, calendar, service, start = setup
    overlap_on_create(calendar)
    service.book(start)
    retried, verified = service.book(start), service.verify(start)
    assert retried.status is Status.BOOKED_CONFLICT_DETECTED and verified.status is Status.BOOKED_CONFLICT_DETECTED
    assert not retried.ok and not verified.ok
    assert len(calendar.create_calls) == 1
    assert state_of(service, cfg, start) == State.UNCERTAIN


def test_r2_once_the_conflict_is_really_gone_the_calendar_may_show_success_again(setup):
    cfg, calendar, service, start = setup
    overlap_on_create(calendar)
    service.book(start)
    calendar.appointments = [a for a in calendar.appointments if a.note]  # the other appointment was cancelled
    assert service.verify(start).ok
    assert state_of(service, cfg, start) == State.VERIFIED


def test_r2_a_conflict_that_appears_after_a_clean_success_is_caught_on_retry(setup):
    cfg, calendar, service, start = setup
    assert service.book(start).ok
    calendar.add_appointment(DAY, (11, 0), (12, 0))
    retried = service.book(start)
    assert retried.status is Status.BOOKED_CONFLICT_DETECTED and not retried.ok


# ---------------------------------------------------------------- R3: missing identity is not a match


@pytest.mark.parametrize("service_name, staff", [("", "Shobs"), ("Aria Salon", ""), ("  ", "Shobs")])
def test_r3_a_record_without_its_service_or_staff_is_unverified(setup, service_name, staff):
    cfg, calendar, service, start = setup
    calendar.create_mode = "unknown_not_saved"
    calendar.on_create = lambda cal, spec: cal.appointments.append(Appointment(staff, spec.interval, service_name, spec.note))
    result = service.book(start)
    assert not result.ok and result.status is Status.UNCERTAIN_NEEDS_REVIEW
    assert state_of(service, cfg, start) == State.UNCERTAIN


def test_r3_nor_on_retry_nor_in_verify(setup):
    cfg, calendar, service, start = setup
    calendar.create_mode = "unknown_not_saved"
    calendar.on_create = lambda cal, spec: cal.appointments.append(Appointment(spec.staff, spec.interval, "", spec.note))
    service.book(start)
    assert not service.book(start).ok and not service.verify(start).ok


def test_r3_a_different_service_or_staff_is_still_a_mismatch(setup):
    cfg, calendar, service, start = setup
    calendar.create_mode = "unknown_not_saved"
    calendar.on_create = lambda cal, spec: cal.appointments.append(Appointment(spec.staff, spec.interval, "Haircut", spec.note))
    assert service.book(start).status is Status.VERIFY_MISMATCH


# ---------------------------------------------------------------- R4: a cancelled / inactive record is not a booking


def test_r4_a_retained_inactive_record_is_not_confirmed_and_not_silently_rebooked(setup):
    cfg, calendar, service, start = setup
    assert service.book(start).ok
    calendar.appointments[0] = replace(calendar.appointments[0], blocks_time=False)
    retried, verified = service.book(start), service.verify(start)
    assert len(calendar.create_calls) == 1, "no automatic re-booking"
    assert not retried.ok and not verified.ok
    assert retried.status is Status.VERIFY_MISMATCH
    assert state_of(service, cfg, start) == State.UNCERTAIN


def test_r4_an_inactive_record_right_after_saving_is_not_a_success(setup):
    cfg, calendar, service, start = setup
    calendar.on_create = lambda cal, spec: None
    original = calendar.create_appointment

    def create_then_cancel(spec):
        original(spec)
        calendar.appointments[-1] = replace(calendar.appointments[-1], blocks_time=False)

    calendar.create_appointment = create_then_cancel
    assert not service.book(start).ok


# ---------------------------------------------------------------- R5: grid rounding with seconds


def at(h, m, s=0, us=0, day=DAY):
    return datetime(day.year, day.month, day.day, h, m, s, us, tzinfo=TZ)


def test_r5_the_review_scenario_first_slot_is_1015_and_at_least_an_hour_ahead(setup):
    cfg, calendar, service, start = setup
    now = at(9, 14, 30)
    result = find_slots(calendar.read_day(DAY), service.service, cfg.staff_name, now=now, min_lead_minutes=60, grid_minutes=15)
    first = result.slots[0].service.start
    assert first == at(10, 15)
    assert (first - now).total_seconds() >= 3600


@pytest.mark.parametrize("moment, expected", [
    (at(10, 15), at(10, 15)),              # already on the grid
    (at(10, 15, 0, 1), at(10, 30)),        # one microsecond past the grid
    (at(10, 14, 30), at(10, 15)),          # the review case: seconds carry the minute over
    (at(10, 14, 59, 999999), at(10, 15)),
    (at(10, 0), at(10, 0)),
    (at(10, 1), at(10, 15)),
    (at(10, 45, 1), at(11, 0)),            # rolls over the hour
    (at(23, 59, 30), at(0, 0, day=date(2026, 10, 13))),  # rolls over midnight
])
def test_r5_rounding_is_a_true_ceiling_on_the_grid(moment, expected):
    result = _ceil_to_grid(moment, 15)
    assert result == expected
    assert result >= moment, "rounding must never shorten a lead time"


def test_r5_across_the_spring_forward_gap_the_result_is_a_real_time_not_before_the_input():
    moment = datetime(2026, 3, 8, 1, 50, 30, tzinfo=TZ)  # 02:00-03:00 does not exist this day
    result = _ceil_to_grid(moment, 15)
    assert result >= moment
    assert result.astimezone(ZoneInfo("UTC")) - moment.astimezone(ZoneInfo("UTC")) <= timedelta(minutes=15)
    assert (result.hour, result.minute) == (3, 0)


def test_r5_across_the_fall_back_repeat_the_result_is_not_before_the_input():
    first = datetime(2026, 11, 1, 1, 14, 30, tzinfo=TZ, fold=0)
    second = datetime(2026, 11, 1, 1, 14, 30, tzinfo=TZ, fold=1)
    for moment in (first, second):
        result = _ceil_to_grid(moment, 15)
        assert result >= moment and (result.hour, result.minute) == (1, 15)


# ---------------------------------------------------------------- R6: an old verification is not current success


def test_r6_an_unreadable_calendar_never_turns_an_old_verification_into_current_success(setup):
    cfg, calendar, service, start = setup
    assert service.book(start).ok
    calendar.appointments.clear()  # cancelled elsewhere in the meantime
    calendar.read_failures = 1
    result = service.book(start)
    assert len(calendar.create_calls) == 1, "duplicate suppression is kept"
    assert not result.ok and result.status is Status.UNCERTAIN_NEEDS_REVIEW
    assert "unknown" in result.message


def test_r6_when_the_calendar_is_readable_again_the_truth_is_reported(setup):
    cfg, calendar, service, start = setup
    assert service.book(start).ok
    calendar.appointments.clear()
    result = service.book(start)
    assert not result.ok and len(calendar.create_calls) == 1


# ---------------------------------------------------------------- R7: notes cannot be matched by time alone


def two_same_time_appointments(calendar, cfg):
    same = Interval(calendar.at(DAY, 10), calendar.at(DAY, 12, 30))
    calendar.appointments = [
        Appointment(cfg.staff_name, same, cfg.service_name),
        Appointment(cfg.staff_name, same, "Other service"),
    ]


def test_r7_two_appointments_with_the_same_times_are_refused_not_given_one_note(setup):
    cfg, calendar, service, start = setup
    two_same_time_appointments(calendar, cfg)
    with pytest.raises(DriverError, match="same start and end"):
        attach_notes(calendar.read_day(DAY), {((10, 0), (12, 30)): "ARIA TEST Ref: ARIA-12345678"})


def test_r7_a_missing_note_is_incomplete_not_empty(setup):
    cfg, calendar, service, start = setup
    calendar.add_appointment(DAY, (10, 0), (12, 30))
    calendar.add_appointment(DAY, (14, 0), (15, 0))
    with pytest.raises(DriverError, match="incomplete"):
        attach_notes(calendar.read_day(DAY), {((10, 0), (12, 30)): "x"})


def test_r7_a_note_with_no_matching_appointment_is_incomplete(setup):
    cfg, calendar, service, start = setup
    calendar.add_appointment(DAY, (10, 0), (12, 30))
    with pytest.raises(DriverError, match="incomplete"):
        attach_notes(calendar.read_day(DAY), {((10, 0), (12, 30)): "x", ((14, 0), (15, 0)): "y"})


def test_r7_an_incomplete_note_read_is_never_proof_of_absence(setup):
    """Through the service: if notes cannot be read completely the day is unreadable, so a duplicate booking is not
    attempted and a missing reference is not declared absent."""
    cfg, calendar, service, start = setup
    assert service.book(start).ok
    original = calendar.read_day

    def incomplete(day, include_notes=False):
        if include_notes:
            raise DriverError("the notes read (1) do not match the appointments on the page (2); the read is incomplete")
        return original(day, include_notes)

    calendar.read_day = incomplete
    result = service.book(start)
    assert not result.ok and len(calendar.create_calls) == 1
    assert state_of(service, cfg, start) == State.VERIFIED, "an unreadable day does not rewrite history either"
