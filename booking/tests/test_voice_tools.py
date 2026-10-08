"""The voice tools against the in-memory calendar: Sol's eleven acceptance scenarios plus the honesty invariants.

Dates: NOW is Thu 8 Oct 2026 12:00; DAY is Mon 12 Oct 2026 (09:00-20:00 in the default fixture, 150-minute service).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from aria_booking.availability import validate_slot
from aria_booking.booking_service import BookingService
from aria_booking.driver import DriverError
from aria_booking.ledger import Ledger, State
from aria_booking.voice.tools import SUCCESS_STATUSES, VoiceTools

from conftest import DAY, NOW, TZ

CALL = "call_A"
CALL_B = "call_B"
BOOKED_PHRASES = ("you're booked", "i checked the calendar and it's there", "already booked for you")


def make_tools(cfg, calendar, clock, ledger, **kwargs):
    def factory():
        return BookingService(cfg, calendar, Ledger(cfg.ledger_path, clock=clock), clock=clock, sleep=lambda s: None)

    kwargs.setdefault("booking_enabled", True)
    return VoiceTools(cfg, calendar, factory, clock, **kwargs)


@pytest.fixture
def tools(cfg, calendar, clock, ledger):
    return make_tools(cfg, calendar, clock, ledger)


def assert_honest(resp):
    """The invariant that must hold for EVERY response."""
    assert resp["ok"] is (resp["status"] in SUCCESS_STATUSES)
    assert isinstance(resp["speak"], str)
    if not resp["ok"]:
        low = resp["speak"].lower()
        assert not any(p in low for p in BOOKED_PHRASES), f"{resp['status']} claims a booking: {resp['speak']}"
    return resp


def ask(tools, time, day=DAY, call=CALL):
    return assert_honest(tools.check_slot(call, {"date": day.isoformat(), "time": time}))


def book(tools, option_id, call=CALL, confirmed=True):
    return assert_honest(tools.book_slot(call, {"option_id": option_id, "confirmed": confirmed}))


def open_days(calendar, *offsets, start=9, end=20):
    for offset in offsets:
        calendar.set_hours(DAY + timedelta(days=offset), start, end)


def option_starts(resp):
    return [datetime.fromisoformat(o["start"]) for o in resp["options"]]


def assert_genuinely_free(calendar, cfg, resp, now=NOW):
    """Every offered option must pass the availability rules against a fresh read of the calendar."""
    from aria_booking.models import ServiceSpec

    service = ServiceSpec(cfg.service_name, cfg.service_duration_minutes)
    assert resp["options"], "expected options"
    for start in option_starts(resp):
        snapshot = calendar.read_day(start.date())
        assert validate_slot(snapshot, service, cfg.staff_name, start, now=now).ok, f"{start} is not free"


# ---------------------------------------------------------------- 1. after hours


def test_after_hours_is_explained_then_real_alternatives_are_offered(tools, calendar, cfg):
    open_days(calendar, 1, 2)
    resp = ask(tools, "21:00")
    assert resp["status"] == "alternatives" and resp["unavailable_reason"] == "outside_hours"
    assert "outside our hours" in resp["speak"] and "9 AM to 8 PM" in resp["speak"]
    assert resp["opens"] == "09:00" and resp["closes"] == "20:00"
    assert_genuinely_free(calendar, cfg, resp)


def test_before_opening_is_also_outside_hours(tools, calendar):
    resp = ask(tools, "07:00")
    assert resp["unavailable_reason"] == "outside_hours"


def test_a_day_with_no_working_hours_is_not_taking_appointments(tools, calendar):
    resp = ask(tools, "11:00", day=DAY + timedelta(days=6))  # a day with no hours set: known not working
    assert resp["unavailable_reason"] == "not_working" and "aren't taking appointments" in resp["speak"]


# ---------------------------------------------------------------- 2. starts before closing but ends after it


def test_a_start_before_closing_whose_full_service_ends_after_closing_is_refused_with_the_latest_start(tools, calendar, cfg):
    open_days(calendar, 1)
    resp = ask(tools, "18:30")  # 18:30 + 2h30 = 21:00 > 20:00
    assert resp["unavailable_reason"] == "ends_after_closing"
    assert resp["latest_start"] == "17:30" and resp["closes"] == "20:00"
    assert "2 hours 30 minutes" in resp["speak"] and "latest start" in resp["speak"] and "5:30 PM" in resp["speak"]
    assert resp["status"] == "alternatives"
    assert_genuinely_free(calendar, cfg, resp)
    assert not any(s.hour * 60 + s.minute > 17 * 60 + 30 for s in option_starts(resp) if s.date() == DAY)


def test_the_last_fitting_start_is_available_and_one_quarter_hour_later_is_not(tools, calendar):
    assert ask(tools, "17:30")["status"] == "available"
    assert ask(tools, "17:45")["unavailable_reason"] == "ends_after_closing"


# ---------------------------------------------------------------- 3. occupied slot


def test_an_occupied_slot_says_so_without_revealing_anything_and_offers_free_times(tools, calendar, cfg):
    open_days(calendar, 1)
    calendar.add_appointment(DAY, (10, 0), (12, 30), note="Someone Private")
    resp = ask(tools, "11:00")
    assert resp["unavailable_reason"] == "occupied" and "already taken" in resp["speak"]
    assert "Private" not in str(resp) and "Someone" not in str(resp)
    assert_genuinely_free(calendar, cfg, resp)
    starts = option_starts(resp)
    assert all(not (s.date() == DAY and s < calendar.at(DAY, 12, 30) and s + timedelta(minutes=150) > calendar.at(DAY, 10)) for s in starts)


def test_a_slot_touching_an_existing_appointment_is_available(tools, calendar):
    calendar.add_appointment(DAY, (9, 0), (11, 0))
    assert ask(tools, "11:00")["status"] == "available"


def test_too_soon_is_explained(tools, calendar, clock):
    today = NOW.date()
    calendar.set_hours(today, 9, 20)
    clock.value = datetime(2026, 10, 8, 15, 0, tzinfo=TZ)
    resp = ask(tools, "15:30", day=today)
    assert resp["unavailable_reason"] == "too_soon" and "notice" in resp["speak"]


# ---------------------------------------------------------------- 4. two or three genuinely available alternatives


def test_two_or_three_alternatives_come_from_the_current_calendar(tools, calendar, cfg):
    open_days(calendar, 1, 2, 3)
    calendar.add_appointment(DAY, (9, 0), (20, 0))  # the requested day is full
    resp = ask(tools, "11:00")
    assert resp["status"] == "alternatives" and 2 <= len(resp["options"]) <= 3
    assert_genuinely_free(calendar, cfg, resp)
    assert len({o["option_id"] for o in resp["options"]}) == len(resp["options"])
    assert all(o["label"] in resp["speak"] for o in resp["options"]), "every option is read out"


def test_same_day_alternatives_are_the_nearest_before_and_after_the_request(tools, calendar):
    calendar.add_appointment(DAY, (12, 0), (15, 0))
    resp = ask(tools, "13:00")
    starts = [s.strftime("%H:%M") for s in option_starts(resp) if s.date() == DAY]
    assert starts == ["15:00", "09:30"]  # nearest after (touching 15:00) and nearest before (ends exactly 12:00)


def test_find_alternatives_searches_from_a_preferred_day_and_time(tools, calendar, cfg):
    open_days(calendar, 1, 2)
    resp = assert_honest(tools.find_alternatives(CALL, {"date": DAY.isoformat(), "time": "14:00"}))
    assert resp["status"] == "alternatives" and 2 <= len(resp["options"]) <= 3
    assert_genuinely_free(calendar, cfg, resp)
    first = option_starts(resp)[0]
    assert first.strftime("%H:%M") == "14:00"


def test_find_alternatives_without_a_time_is_allowed_and_without_a_date_is_not(tools, calendar):
    assert tools.find_alternatives(CALL, {"date": DAY.isoformat()})["status"] == "alternatives"
    assert tools.find_alternatives(CALL, {})["status"] == "needs_clarification"


# ---------------------------------------------------------------- 5. no alternatives / unreadable calendar


def test_no_alternatives_is_said_plainly(tools, calendar):
    calendar.add_appointment(DAY, (9, 0), (20, 0))  # full; no other day has hours
    resp = ask(tools, "11:00")
    assert resp["status"] == "no_alternatives" and resp["options"] == []
    assert "already taken" in resp["speak"] and "any other openings" in resp["speak"]


@pytest.mark.parametrize("switch", ["unknown_appointments", "unknown_working", "unknown_time_off"])
def test_unknown_data_is_never_treated_as_free(tools, calendar, switch):
    setattr(calendar, switch, True)
    resp = ask(tools, "11:00")
    assert resp["status"] == "unknown" and "can't tell you" in resp["speak"] and "options" not in resp


def test_an_unreadable_calendar_is_unknown_not_free(tools, calendar):
    calendar.read_failures = 5
    resp = ask(tools, "11:00")
    assert resp["status"] == "unknown" and "options" not in resp


def test_when_other_days_cannot_be_read_the_agent_does_not_claim_there_are_none(tools, calendar):
    calendar.add_appointment(DAY, (9, 0), (20, 0))
    original = calendar.read_day

    def flaky(day, include_notes=False):
        if day != DAY:
            raise DriverError("unreadable")
        return original(day, include_notes)

    calendar.read_day = flaky
    resp = ask(tools, "11:00")
    assert resp["status"] == "unknown" and resp["options"] == []
    assert "already taken" in resp["speak"], "what IS known about the requested time is still said"


def test_a_signed_out_session_is_reported_as_the_system_needing_attention(tools, calendar):
    calendar.sign_in_required = True
    resp = ask(tools, "11:00")
    assert resp["status"] == "system_unavailable" and "needs attention" in resp["speak"]


# ---------------------------------------------------------------- 6. ambiguous date / time is clarified, not guessed


@pytest.mark.parametrize("args, reason", [
    ({}, "missing"),
    ({"date": "2026-10-12"}, "missing"),
    ({"time": "11:00"}, "missing"),
    ({"date": "next Tuesday", "time": "11:00"}, "bad_date"),
    ({"date": "2026-02-30", "time": "11:00"}, "bad_date"),
    ({"date": 20261012, "time": "11:00"}, "bad_date"),
    ({"date": "2026-10-12", "time": "afternoon"}, "bad_time"),
    ({"date": "2026-10-12", "time": "25:00"}, "bad_time"),
    ({"date": "2026-10-12", "time": "11:75"}, "bad_time"),
    ({"date": "2026-10-12", "time": "2pm"}, "bad_time"),
    ({"date": "2026-10-12", "time": "11:10"}, "off_grid"),
    ({"date": "2026-10-07", "time": "11:00"}, "past_date"),
    ({"date": "2027-12-01", "time": "11:00"}, "too_far"),
])
def test_an_unclear_request_gets_a_question_and_never_touches_the_calendar(tools, calendar, args, reason):
    resp = assert_honest(tools.check_slot(CALL, args))
    assert resp["status"] == "needs_clarification" and resp["reason"] == reason
    assert calendar.read_calls == 0 and "options" not in resp


@pytest.mark.parametrize("call_id", ["", "   ", None])
def test_a_request_without_a_call_id_is_refused(tools, calendar, call_id):
    resp = tools.check_slot(call_id, {"date": DAY.isoformat(), "time": "11:00"})
    assert resp["status"] == "needs_clarification" and calendar.read_calls == 0


# ---------------------------------------------------------------- 7-10. choosing, confirming, rechecking, booking once


def test_the_caller_picks_an_offered_option_confirms_twice_and_exactly_one_verified_booking_exists(tools, calendar, ledger, cfg):
    offered = ask(tools, "11:00")
    assert offered["status"] == "available"
    (option,) = offered["options"]

    readback = book(tools, option["option_id"], confirmed=False)
    assert readback["status"] == "confirmation_required" and "Shall I book it?" in readback["speak"]
    assert "Monday, October 12 at 11 AM" in readback["speak"] and "2 hours 30 minutes" in readback["speak"]
    assert calendar.create_calls == [], "nothing is saved until the caller has confirmed"

    done = book(tools, option["option_id"], confirmed=True)
    assert done["status"] == "booked_verified" and done["ok"] is True
    assert "You're booked" in done["speak"] and done["reference"].startswith("ARIA-")
    assert len(calendar.create_calls) == 1 and len(calendar.saved_by_aria()) == 1
    entry = ledger.all_entries()[0]
    assert entry.state == State.VERIFIED and entry.observed is True


def test_confirmed_true_on_the_very_first_call_still_only_asks_for_confirmation(tools, calendar):
    (option,) = ask(tools, "11:00")["options"]
    resp = book(tools, option["option_id"], confirmed=True)
    assert resp["status"] == "confirmation_required" and calendar.create_calls == []


@pytest.mark.parametrize("not_true", [False, "true", "yes", 1, "True", None, [], {}])
def test_only_the_boolean_true_counts_as_confirmation(tools, calendar, not_true):
    (option,) = ask(tools, "11:00")["options"]
    book(tools, option["option_id"], confirmed=False)  # the read-back
    resp = book(tools, option["option_id"], confirmed=not_true)
    assert resp["status"] == "confirmation_required" and calendar.create_calls == []


def test_a_confirmation_for_a_different_option_does_not_carry_over(tools, calendar):
    open_days(calendar, 1, 2)
    calendar.add_appointment(DAY, (9, 0), (20, 0))
    offered = ask(tools, "11:00")["options"]
    first, second = offered[0], offered[1]
    book(tools, first["option_id"], confirmed=False)
    resp = book(tools, second["option_id"], confirmed=True)
    assert resp["status"] == "confirmation_required" and resp["option_id"] == second["option_id"]
    assert calendar.create_calls == []


def test_the_pending_confirmation_expires(tools, calendar, clock):
    (option,) = ask(tools, "11:00")["options"]
    book(tools, option["option_id"], confirmed=False)
    clock.advance(minutes=6)
    resp = book(tools, option["option_id"], confirmed=True)
    assert resp["status"] == "confirmation_required" and calendar.create_calls == []


def test_the_calendar_is_rechecked_just_before_saving(tools, calendar):
    (option,) = ask(tools, "11:00")["options"]
    book(tools, option["option_id"], confirmed=False)
    calendar.add_appointment(DAY, (10, 0), (12, 30), note="booked by someone else meanwhile")
    resp = book(tools, option["option_id"], confirmed=True)
    assert resp["status"] == "unavailable" and resp["ok"] is False and "no longer available" in resp["speak"]
    assert calendar.create_calls == []


def test_a_slot_that_became_unknown_before_saving_is_not_saved(tools, calendar):
    (option,) = ask(tools, "11:00")["options"]
    book(tools, option["option_id"], confirmed=False)
    calendar.unknown_appointments = True
    resp = book(tools, option["option_id"], confirmed=True)
    assert resp["status"] == "unknown" and calendar.create_calls == []


# ---------------------------------------------------------------- 11. repeats, retries, and one booking per call


def test_a_repeated_or_retried_book_request_returns_the_earlier_answer_and_never_saves_again(tools, calendar):
    (option,) = ask(tools, "11:00")["options"]
    book(tools, option["option_id"], confirmed=False)
    first = book(tools, option["option_id"], confirmed=True)
    for _ in range(3):  # Retell may retry on a timeout; the agent may also simply repeat itself
        again = book(tools, option["option_id"], confirmed=True)
        assert again == first
    assert len(calendar.create_calls) == 1 and len(calendar.saved_by_aria()) == 1


def test_the_reference_is_stable_for_the_same_slot(tools, calendar):
    (option,) = ask(tools, "11:00")["options"]
    book(tools, option["option_id"], confirmed=False)
    ref = book(tools, option["option_id"], confirmed=True)["reference"]
    assert ref == book(tools, option["option_id"], confirmed=True)["reference"]


def test_a_second_booking_in_the_same_call_is_refused(tools, calendar):
    open_days(calendar, 1)
    (option,) = ask(tools, "11:00")["options"]
    book(tools, option["option_id"], confirmed=False)
    book(tools, option["option_id"], confirmed=True)
    other = ask(tools, "11:00", day=DAY + timedelta(days=1))["options"][0]
    resp = book(tools, other["option_id"], confirmed=False)
    assert resp["status"] == "refused" and resp["reason"] == "one_booking_per_call"
    assert len(calendar.create_calls) == 1


def test_a_booked_slot_is_no_longer_offered_to_the_next_caller(tools, calendar):
    (option,) = ask(tools, "11:00")["options"]
    book(tools, option["option_id"], confirmed=False)
    book(tools, option["option_id"], confirmed=True)
    resp = ask(tools, "11:00", call=CALL_B)
    assert resp["unavailable_reason"] == "occupied"


# ---------------------------------------------------------------- options cannot be forged, shared or kept forever


def test_an_option_belongs_to_the_call_that_received_it(tools, calendar):
    (option,) = ask(tools, "11:00")["options"]
    resp = book(tools, option["option_id"], call=CALL_B, confirmed=False)
    assert resp["status"] == "invalid_option" and calendar.create_calls == []
    assert book(tools, option["option_id"], call=CALL_B, confirmed=True)["status"] == "invalid_option"


@pytest.mark.parametrize("option_id", ["", "opt_forged", None, 7, "11:00"])
def test_invented_option_ids_are_refused(tools, calendar, option_id):
    resp = tools.book_slot(CALL, {"option_id": option_id, "confirmed": True})
    assert resp["status"] == "invalid_option" and resp["ok"] is False and calendar.create_calls == []


def test_an_option_expires(tools, calendar, clock):
    (option,) = ask(tools, "11:00")["options"]
    clock.advance(minutes=16)
    assert book(tools, option["option_id"], confirmed=False)["status"] == "invalid_option"


def test_booking_can_be_switched_off(cfg, calendar, clock, ledger):
    tools = make_tools(cfg, calendar, clock, ledger, booking_enabled=False)
    (option,) = ask(tools, "11:00")["options"]
    resp = book(tools, option["option_id"], confirmed=False)
    assert resp["status"] == "refused" and resp["reason"] == "booking_disabled" and calendar.create_calls == []
    assert ask(tools, "11:00")["status"] == "available", "checking still works read-only"


def test_a_server_wide_booking_cap_is_enforced(cfg, calendar, clock, ledger):
    tools = make_tools(cfg, calendar, clock, ledger, max_bookings_total=1)
    open_days(calendar, 1)
    (first,) = ask(tools, "11:00", call="c1")["options"]
    book(tools, first["option_id"], call="c1", confirmed=False)
    assert book(tools, first["option_id"], call="c1", confirmed=True)["status"] == "booked_verified"
    (second,) = ask(tools, "11:00", day=DAY + timedelta(days=1), call="c2")["options"]
    resp = book(tools, second["option_id"], call="c2", confirmed=False)
    assert resp["status"] == "refused" and resp["reason"] == "session_limit"
    assert len(calendar.create_calls) == 1


# ---------------------------------------------------------------- expired session / uncertain outcomes never claim success


def confirm(tools, calendar, **setup):
    (option,) = ask(tools, "11:00")["options"]
    book(tools, option["option_id"], confirmed=False)
    for name, value in setup.items():
        setattr(calendar, name, value)
    return option, book(tools, option["option_id"], confirmed=True)


def test_an_expired_session_at_booking_time_is_not_a_success(tools, calendar):
    option, resp = confirm(tools, calendar, sign_in_required=True)
    assert resp["status"] == "system_unavailable" and "needs attention" in resp["speak"]
    assert calendar.create_calls == []


def test_a_failure_before_the_save_click_says_nothing_was_saved(tools, calendar):
    option, resp = confirm(tools, calendar, create_mode="before_save_error")
    assert resp["status"] == "not_saved" and "nothing was saved" in resp["speak"]


def test_a_save_whose_outcome_is_unknown_and_not_visible_is_needs_review_and_not_retried(tools, calendar):
    option, resp = confirm(tools, calendar, create_mode="unknown_not_saved")
    assert resp["status"] == "needs_review" and resp["ok"] is False
    assert "can't confirm" in resp["speak"] and "don't treat it as booked" in resp["speak"]
    again = book(tools, option["option_id"], confirmed=True)
    assert again == resp and len(calendar.create_calls) == 1, "an uncertain outcome is not retried within the call"


def test_an_unknown_save_that_did_reach_the_calendar_is_confirmed_only_by_reading_it_back(tools, calendar):
    option, resp = confirm(tools, calendar, create_mode="unknown_but_saved")
    assert resp["status"] == "booked_verified" and len(calendar.saved_by_aria()) == 1


def test_a_record_saved_at_the_wrong_time_is_needs_review(tools, calendar):
    option, resp = confirm(tools, calendar, create_mode="saves_wrong_time")
    assert resp["status"] == "needs_review" and resp["ok"] is False


def test_a_booking_that_conflicts_after_saving_is_not_confirmed(tools, calendar):
    calendar.on_create = lambda cal, spec: cal.add_appointment(DAY, (12, 0), (13, 0))
    option, resp = confirm(tools, calendar)
    assert resp["status"] == "needs_review" and resp["ok"] is False


def test_an_unforeseen_exception_while_booking_never_claims_either_way(cfg, calendar, clock, ledger):
    def broken():
        raise RuntimeError("boom with secret details")

    tools = VoiceTools(cfg, calendar, broken, clock, booking_enabled=True)
    (option,) = ask(tools, "11:00")["options"]
    book(tools, option["option_id"], confirmed=False)
    resp = book(tools, option["option_id"], confirmed=True)
    assert resp["status"] == "needs_review" and "secret" not in str(resp)
    assert book(tools, option["option_id"], confirmed=True) == resp


def test_a_second_request_while_the_browser_is_busy_says_busy(cfg, calendar, clock, ledger):
    tools = make_tools(cfg, calendar, clock, ledger, lock_timeout_seconds=0.05)
    assert tools._lock.acquire()
    try:
        resp = ask(tools, "11:00")
    finally:
        tools._lock.release()
    assert resp["status"] == "busy" and calendar.read_calls == 0
    assert ask(tools, "11:00")["status"] == "available"


# ---------------------------------------------------------------- the only code path that can save


def test_only_book_slot_can_reach_the_save_path():
    """Structural: one call into BookingService.book, in the booking path; no driver write or click anywhere."""
    import ast
    import inspect

    from aria_booking.voice import tools as module

    def calls(node):
        return [n.func.attr for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]

    tree = ast.parse(inspect.getsource(module))
    assert calls(tree).count("book") == 1, "exactly one call into BookingService.book"
    assert not {"create_appointment", "click", "send_keys", "execute_script"} & set(calls(tree))
    for name in ("_check_slot", "_find_alternatives", "_alternatives", "_offer", "_why_not", "_parse_when"):
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        assert "book" not in calls(fn) and "_service_factory" not in ast.dump(fn), f"{name} must not be able to book"
    # and the driver is only ever READ from in this module
    driver_calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "driver"}
    assert driver_calls == {"read_day"}, driver_calls


def test_an_uncertain_outcome_is_not_even_attempted_again_within_the_call(cfg, calendar, clock, ledger):
    """The repeat must not reach the booking service or the calendar at all: an unknown outcome is final for the call."""
    made = []

    def factory():
        made.append(1)
        return BookingService(cfg, calendar, Ledger(cfg.ledger_path, clock=clock), clock=clock, sleep=lambda s: None)

    tools = VoiceTools(cfg, calendar, factory, clock, booking_enabled=True)
    (option,) = ask(tools, "11:00")["options"]
    book(tools, option["option_id"], confirmed=False)
    calendar.create_mode = "unknown_not_saved"
    first = book(tools, option["option_id"], confirmed=True)
    assert first["status"] == "needs_review"
    reads, services = calendar.read_calls, len(made)
    for _ in range(3):
        assert book(tools, option["option_id"], confirmed=True) == first
    assert len(made) == services == 1 and calendar.read_calls == reads and len(calendar.create_calls) == 1


def test_a_definite_non_booking_may_be_chosen_again_but_still_needs_a_fresh_confirmation(tools, calendar):
    (option,) = ask(tools, "11:00")["options"]
    book(tools, option["option_id"], confirmed=False)
    calendar.create_mode = "before_save_error"
    assert book(tools, option["option_id"], confirmed=True)["status"] == "not_saved"
    calendar.create_mode = "ok"
    again = book(tools, option["option_id"], confirmed=True)
    assert again["status"] == "confirmation_required", "the earlier confirmation was used up"
    assert book(tools, option["option_id"], confirmed=True)["status"] == "booked_verified"
    assert len(calendar.saved_by_aria()) == 1
