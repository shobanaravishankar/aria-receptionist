from __future__ import annotations

from aria_booking.booking_service import BookingService, Status
from aria_booking.ledger import Ledger, State
from aria_booking.safety import NOTE_PREFIX, extract_ref

from conftest import DAY


def slot(calendar, hour=10, minute=0):
    return calendar.at(DAY, hour, minute)


# ---------------------------------------------------------------- happy path


def test_books_exactly_one_appointment_and_verifies_by_read_back(service, calendar, ledger):
    result = service.book(slot(calendar))
    assert result.status is Status.BOOKED_VERIFIED and result.ok
    assert len(calendar.create_calls) == 1
    (saved,) = calendar.saved_by_aria()
    assert saved.interval.minutes == 150
    assert ledger.get(_key(service, slot(calendar))).state == State.VERIFIED


def test_created_note_is_always_marked_as_a_fictional_test_with_a_reference(service, calendar):
    service.book(slot(calendar))
    (spec,) = calendar.create_calls
    assert spec.note.startswith(NOTE_PREFIX)
    assert extract_ref(spec.note) is not None
    assert spec.service_name == "Aria Salon" and spec.staff == "Shobs" and spec.duration_minutes == 150


# ---------------------------------------------------------------- duplicates


def test_second_request_for_the_same_slot_never_creates_a_second_appointment(service, calendar):
    first = service.book(slot(calendar))
    second = service.book(slot(calendar))
    assert first.status is Status.BOOKED_VERIFIED
    assert second.status is Status.ALREADY_BOOKED and second.ok
    assert len(calendar.create_calls) == 1 and len(calendar.saved_by_aria()) == 1


def test_duplicate_detection_survives_a_new_process(cfg, calendar, clock):
    first = BookingService(cfg, calendar, Ledger(cfg.ledger_path, clock=clock), clock=clock, sleep=lambda s: None)
    first.book(slot(calendar))
    fresh = BookingService(cfg, calendar, Ledger(cfg.ledger_path, clock=clock), clock=clock, sleep=lambda s: None)
    # fresh service has its own run counter, so only the ledger/calendar can stop a duplicate
    assert fresh.book(slot(calendar)).status is Status.ALREADY_BOOKED
    assert len(calendar.create_calls) == 1


# ---------------------------------------------------------------- notes are only visible when asked for

def test_the_reference_is_found_on_the_calendar_only_by_asking_for_notes(service, calendar):
    """On the real site the day view shows no notes, so both the read-back and the duplicate check must ask."""
    calendar.notes_need_include_flag = True
    first = service.book(slot(calendar))
    assert first.status is Status.BOOKED_VERIFIED, "the read-back must ask for notes"
    second = service.book(slot(calendar))
    assert second.status is Status.ALREADY_BOOKED, "the duplicate check must ask for notes"
    assert len(calendar.create_calls) == 1


def test_the_pre_save_availability_read_does_not_open_any_appointment(service, calendar):
    seen = []
    original = calendar.read_day
    calendar.read_day = lambda day, include_notes=False: (seen.append(include_notes), original(day, include_notes))[1]
    service.search(DAY, 1)
    assert seen == [False]


# ---------------------------------------------------------------- availability


def test_overlapping_an_existing_appointment_is_declined_without_touching_the_calendar(service, calendar):
    calendar.add_appointment(DAY, (11, 0), (12, 0))
    result = service.book(slot(calendar, 10, 0))  # 10:00-12:30 overlaps 11:00-12:00
    assert result.status is Status.SLOT_UNAVAILABLE
    assert any("overlaps existing appointment 11:00-12:00" in d for d in result.details)
    assert calendar.create_calls == []


def test_unknown_availability_is_not_treated_as_free(service, calendar):
    calendar.unknown_appointments = True
    result = service.book(slot(calendar))
    assert result.status is Status.UNKNOWN_AVAILABILITY
    assert calendar.create_calls == []


def test_booking_that_would_pass_closing_time_is_declined(service, calendar):
    result = service.book(slot(calendar, 18, 30))  # ends 21:00; day ends 20:00
    assert result.status is Status.SLOT_UNAVAILABLE
    assert any("outside working hours" in d for d in result.details)
    assert calendar.create_calls == []


def test_unreadable_calendar_at_booking_time_is_unknown_not_free(service, calendar):
    calendar.read_failures = 1
    result = service.book(slot(calendar))
    assert result.status is Status.UNKNOWN_AVAILABILITY and calendar.create_calls == []


# ---------------------------------------------------------------- races (not atomic)


def test_a_concurrent_booking_between_recheck_and_save_is_detected_afterwards(service, calendar):
    def competitor(cal, spec):
        cal.add_appointment(DAY, (11, 0), (12, 0), note="walk-in added by someone else")

    calendar.on_create = competitor
    result = service.book(slot(calendar, 10, 0))
    assert result.status is Status.BOOKED_CONFLICT_DETECTED and not result.ok
    assert any("overlaps another appointment 11:00-12:00" in d for d in result.details)
    assert len(calendar.saved_by_aria()) == 1, "nothing may be deleted automatically"


# ---------------------------------------------------------------- failures and uncertain saves


def test_failure_before_save_is_recorded_as_not_saved_and_a_retry_can_succeed(service, calendar, ledger):
    calendar.create_mode = "before_save_error"
    first = service.book(slot(calendar))
    assert first.status is Status.NOT_SAVED
    assert ledger.get(_key(service, slot(calendar))).state == State.FAILED

    calendar.create_mode = "ok"
    second = service.book(slot(calendar))
    assert second.status is Status.BOOKED_VERIFIED
    assert len(calendar.saved_by_aria()) == 1


def test_uncertain_save_that_actually_saved_is_reconciled_not_duplicated(service, calendar, ledger):
    calendar.create_mode = "unknown_but_saved"
    result = service.book(slot(calendar))
    assert result.status is Status.BOOKED_VERIFIED
    assert "save outcome unknown" in result.message
    assert len(calendar.create_calls) == 1 and len(calendar.saved_by_aria()) == 1

    again = service.book(slot(calendar))
    assert again.status is Status.ALREADY_BOOKED
    assert len(calendar.create_calls) == 1


def test_uncertain_save_not_visible_is_reported_and_not_retried_immediately(service, calendar, ledger):
    calendar.create_mode = "unknown_not_saved"
    result = service.book(slot(calendar))
    assert result.status is Status.UNCERTAIN_NEEDS_REVIEW and not result.ok
    assert ledger.get(_key(service, slot(calendar))).state == State.UNCERTAIN

    calendar.create_mode = "ok"
    retry = service.book(slot(calendar))  # same clock => too recent to be sure it is absent
    assert retry.status is Status.UNCERTAIN_NEEDS_REVIEW
    assert len(calendar.create_calls) == 1, "must not create again while the earlier outcome is unresolved"


def test_retry_after_settle_time_creates_once_when_the_calendar_proves_it_is_absent(service, calendar, clock):
    calendar.create_mode = "unknown_not_saved"
    service.book(slot(calendar))
    calendar.create_mode = "ok"
    clock.advance(seconds=120)
    retry = service.book(slot(calendar))
    assert retry.status is Status.BOOKED_VERIFIED
    assert len(calendar.saved_by_aria()) == 1


def test_retry_with_an_unreadable_calendar_never_creates(service, calendar, clock):
    calendar.create_mode = "unknown_not_saved"
    service.book(slot(calendar))
    clock.advance(seconds=300)
    calendar.unknown_appointments = True
    calendar.create_mode = "ok"
    retry = service.book(slot(calendar))
    assert retry.status is Status.UNCERTAIN_NEEDS_REVIEW
    assert len(calendar.create_calls) == 1


def test_new_entry_that_appears_late_is_still_verified(service, calendar):
    calendar.hidden_reads_after_create = 2  # invisible for the first two read-backs
    result = service.book(slot(calendar))
    assert result.status is Status.BOOKED_VERIFIED
    assert len(calendar.create_calls) == 1


def test_read_back_mismatch_is_not_reported_as_success(service, calendar):
    calendar.create_mode = "saves_wrong_time"
    result = service.book(slot(calendar))
    assert result.status is Status.VERIFY_MISMATCH and not result.ok
    assert any("start" in d for d in result.details)


# ---------------------------------------------------------------- safety guards


def test_wrong_signed_in_business_is_refused_before_any_read_or_write(service, calendar):
    calendar.business_id = "1111111"
    result = service.book(slot(calendar))
    assert result.status is Status.REJECTED_BY_SAFETY
    assert any("business" in d for d in result.details)
    assert calendar.create_calls == [] and calendar.read_calls == 0


def test_slot_inside_the_minimum_lead_time_is_refused(service, calendar, clock):
    clock.value = calendar.at(DAY, 9, 30)  # only 30 min before a 10:00 start; the lead is 60
    result = service.book(slot(calendar, 10, 0))
    assert result.status is Status.REJECTED_BY_SAFETY
    assert any("future" in d for d in result.details)
    assert calendar.create_calls == []


def test_past_slot_is_refused(service, calendar):
    result = service.book(calendar.now.replace(hour=9))  # earlier today, before NOW
    assert result.status is Status.REJECTED_BY_SAFETY
    assert any("future" in d for d in result.details)


def test_run_limit_allows_only_one_booking_per_run(service, calendar):
    assert service.book(slot(calendar, 9, 0)).status is Status.BOOKED_VERIFIED
    second = service.book(slot(calendar, 14, 0))
    assert second.status is Status.REJECTED_BY_SAFETY
    assert any("run limit" in d for d in second.details)
    assert len(calendar.saved_by_aria()) == 1


def test_expired_sign_in_is_reported_as_sign_in_required(service, calendar):
    calendar.sign_in_required = True
    result = service.book(slot(calendar))
    assert result.status is Status.SIGN_IN_REQUIRED
    assert calendar.create_calls == []


def test_missing_business_id_is_refused(calendar, ledger, clock, tmp_path):
    from aria_booking.config import Config

    cfg = Config(business_id="", local_dir=tmp_path / "x")
    svc = BookingService(cfg, calendar, ledger, clock=clock, sleep=lambda s: None)
    assert svc.book(slot(calendar)).status is Status.REJECTED_BY_SAFETY


# ---------------------------------------------------------------- search


def test_search_reports_free_slots_and_unknown_days(service, calendar):
    free_day = DAY
    results = service.search(free_day, 2)  # DAY has hours; DAY+1 has none => known not working
    assert results[0].search.slots and not results[0].search.unknown
    assert results[1].search.slots == () and results[1].search.notes


def test_search_marks_a_day_unknown_when_the_calendar_cannot_be_read(service, calendar):
    calendar.read_failures = 1
    results = service.search(DAY, 1)
    assert results[0].search.slots == () and results[0].search.unknown


def _key(service, start):
    from aria_booking.ledger import request_key

    c = service.cfg
    return request_key(c.business_id, c.staff_name, c.service_name, start, c.service_duration_minutes)
