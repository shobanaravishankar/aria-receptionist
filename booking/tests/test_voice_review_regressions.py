"""Regression tests for the independent voice review of the Retell integration (V1-V3), plus the honesty of the wording.

Sol's probe scenarios are reproduced here with the same real BookingService / VoiceTools / ledger and the FakeCalendar,
and extended with restarts, unknown save results and speech assertions. No browser, account, network or key is used.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from aria_booking.booking_service import BookingService, Status
from aria_booking.config import Config
from aria_booking.ledger import Ledger, State, request_key
from aria_booking.models import Interval
from aria_booking.voice import retell_http
from aria_booking.voice import tools as tools_module
from aria_booking.voice.tools import SUCCESS_STATUSES, VoiceTools

from fakes import FakeCalendar

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 8, 12, tzinfo=TZ)
DAY = date(2026, 10, 12)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now = self.now + timedelta(**kw)


@pytest.fixture
def setup(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    calendar = FakeCalendar(TZ, business_id="9999999", now=NOW)
    calendar.set_hours(DAY, 9, 20)
    clock = Clock()

    def factory():
        return BookingService(cfg, calendar, Ledger(cfg.ledger_path, clock=clock), clock=clock, sleep=lambda _: None)

    def new_tools(**kw):
        return VoiceTools(cfg, calendar, factory, clock, booking_enabled=True, **kw)

    return new_tools(), calendar, factory, cfg, clock, new_tools


def offer(tools, call, time="10:00"):
    response = tools.check_slot(call, {"date": DAY.isoformat(), "time": time})
    assert response["status"] == "available", response
    return response["options"][0]["option_id"]


def confirm(tools, call, option):
    first = tools.book_slot(call, {"option_id": option, "confirmed": False})
    assert first["status"] == "confirmation_required", first
    return tools.book_slot(call, {"option_id": option, "confirmed": True})


def key_for(cfg, hour=10):
    return request_key(cfg.business_id, cfg.staff_name, cfg.service_name, datetime(2026, 10, 12, hour, 0, tzinfo=TZ), cfg.service_duration_minutes)


# ---------------------------------------------------------------- V1: independent callers do not share one booking


def test_v1_independent_callers_cannot_both_own_one_booking(setup):
    tools, calendar, *_ = setup
    option_a, option_b = offer(tools, "caller_A"), offer(tools, "caller_B")  # both offered 10:00 BEFORE either saves
    result_a = confirm(tools, "caller_A", option_a)
    result_b = confirm(tools, "caller_B", option_b)
    assert result_a["ok"] and result_a["status"] == "booked_verified"
    assert len(calendar.create_calls) == 1 and len(calendar.saved_by_aria()) == 1
    assert not result_b["ok"] and result_b["status"] == "unavailable"
    assert "no longer available" in result_b["speak"] and "nothing was booked" in result_b["speak"]
    assert "already booked for you" not in result_b["speak"].lower() and "you're booked" not in result_b["speak"].lower()


def test_v1_the_second_caller_is_offered_real_alternatives_afterwards(setup):
    tools, calendar, *_ = setup
    calendar.set_hours(DAY + timedelta(days=1), 9, 20)
    option_a, option_b = offer(tools, "caller_A"), offer(tools, "caller_B")
    confirm(tools, "caller_A", option_a)
    assert confirm(tools, "caller_B", option_b)["status"] == "unavailable"
    again = tools.check_slot("caller_B", {"date": DAY.isoformat(), "time": "10:00"})
    assert again["status"] == "alternatives" and again["unavailable_reason"] == "occupied"
    assert all(datetime.fromisoformat(o["start"]).hour != 10 or datetime.fromisoformat(o["start"]).date() != DAY for o in again["options"])


def test_v1_the_entry_records_an_opaque_owner_not_the_call_id(setup):
    tools, calendar, factory, cfg, clock, _ = setup
    confirm(tools, "caller_A", offer(tools, "caller_A"))
    entry = Ledger(cfg.ledger_path).get(key_for(cfg))
    assert entry.owner.startswith("call-") and "caller_A" not in entry.owner
    assert "caller_A" not in cfg.ledger_path.read_text(encoding="utf-8")


def test_v1_the_same_owner_keeps_idempotent_recovery_across_a_restart(setup):
    tools, calendar, factory, cfg, clock, new_tools = setup
    owner = VoiceTools._owner("caller_A")
    first = factory().book(datetime(2026, 10, 12, 10, 0, tzinfo=TZ), owner=owner)
    assert first.status is Status.BOOKED_VERIFIED
    restarted = factory()  # a new process: nothing in memory, only the ledger and the calendar
    again = restarted.book(datetime(2026, 10, 12, 10, 0, tzinfo=TZ), owner=owner)
    assert again.status is Status.ALREADY_BOOKED and again.ok
    assert len(calendar.create_calls) == 1
    other = restarted.book(datetime(2026, 10, 12, 10, 0, tzinfo=TZ), owner=VoiceTools._owner("caller_B"))
    assert other.status is Status.SLOT_UNAVAILABLE and not other.ok and len(calendar.create_calls) == 1


def test_v1_an_unknown_save_by_one_caller_blocks_another_but_not_the_same_caller_later(setup):
    tools, calendar, factory, cfg, clock, new_tools = setup
    start = datetime(2026, 10, 12, 10, 0, tzinfo=TZ)
    a, b = VoiceTools._owner("caller_A"), VoiceTools._owner("caller_B")
    calendar.create_mode = "unknown_not_saved"
    assert factory().book(start, owner=a).status is Status.UNCERTAIN_NEEDS_REVIEW
    calendar.create_mode = "ok"
    clock.advance(seconds=cfg.settle_seconds * 10)
    blocked = factory().book(start, owner=b)
    assert blocked.status is Status.SLOT_UNAVAILABLE and len(calendar.create_calls) == 1, "B must not create a possible duplicate of A's unknown save"
    retry = factory().book(start, owner=a)  # A's own never-observed save may be retried after the timeout, as before
    assert retry.ok and len(calendar.create_calls) == 2 and len(calendar.saved_by_aria()) == 1


def test_v1_a_booking_made_from_the_command_line_cannot_be_claimed_by_a_voice_caller(setup):
    tools, calendar, factory, cfg, clock, _ = setup
    start = datetime(2026, 10, 12, 10, 0, tzinfo=TZ)
    assert factory().book(start).ok  # owner None: the operator's own booking
    result = factory().book(start, owner=VoiceTools._owner("caller_A"))
    assert result.status is Status.SLOT_UNAVAILABLE and len(calendar.create_calls) == 1
    assert factory().book(start).status is Status.ALREADY_BOOKED, "the command line's own retry is unchanged"


def test_v1_a_known_not_saved_entry_does_not_block_another_caller(setup):
    tools, calendar, factory, cfg, clock, _ = setup
    start = datetime(2026, 10, 12, 10, 0, tzinfo=TZ)
    calendar.create_mode = "before_save_error"
    assert factory().book(start, owner=VoiceTools._owner("caller_A")).status is Status.NOT_SAVED
    calendar.create_mode = "ok"
    assert factory().book(start, owner=VoiceTools._owner("caller_B")).ok
    assert Ledger(cfg.ledger_path).get(key_for(cfg)).owner == VoiceTools._owner("caller_B")


def test_v1_the_owner_is_part_of_every_new_ledger_entry_from_voice_and_empty_from_the_command_line(setup):
    tools, calendar, factory, cfg, clock, _ = setup
    factory().book(datetime(2026, 10, 12, 10, 0, tzinfo=TZ))
    assert Ledger(cfg.ledger_path).get(key_for(cfg)).owner == ""


# ---------------------------------------------------------------- V2: a repeat may restate success only if it is still true


@pytest.mark.parametrize("change", ["cancelled", "deleted", "moved", "duplicated", "unreadable"])
def test_v2_a_voice_retry_rechecks_the_current_booking_state(setup, change):
    tools, calendar, factory, *_ = setup
    option = offer(tools, "caller_A")
    assert confirm(tools, "caller_A", option)["ok"]
    saved = calendar.appointments[0]
    if change == "cancelled":
        calendar.appointments[0] = replace(saved, blocks_time=False)
    elif change == "deleted":
        calendar.appointments.clear()
    elif change == "moved":
        calendar.appointments[0] = replace(saved, interval=Interval(saved.interval.start + timedelta(hours=3), saved.interval.end + timedelta(hours=3)))
    elif change == "duplicated":
        calendar.appointments.append(replace(saved))
    else:
        calendar.unknown_appointments = True
    reply = tools.book_slot("caller_A", {"option_id": option, "confirmed": True})
    assert len(calendar.create_calls) == 1, "a retry never recreates"
    assert not reply["ok"] and reply["status"] == "needs_review" and reply["reason"] == "reverification_failed"
    low = reply["speak"].lower()
    assert "can't confirm it is still there" in low and "haven't made another" in low
    assert "you're booked" not in low and "it's there" not in low


def test_v2_an_unchanged_booking_retry_still_succeeds_once_and_repeatedly(setup):
    tools, calendar, *_ = setup
    option = offer(tools, "caller_A")
    first = confirm(tools, "caller_A", option)
    assert first["ok"]
    for _ in range(3):
        retry = tools.book_slot("caller_A", {"option_id": option, "confirmed": True})
        assert retry == first and retry["ok"]
    assert len(calendar.create_calls) == 1 and len(calendar.saved_by_aria()) == 1


def test_v2_a_booking_restored_after_a_scare_is_confirmed_again_on_the_next_retry(setup):
    tools, calendar, *_ = setup
    option = offer(tools, "caller_A")
    first = confirm(tools, "caller_A", option)
    saved = calendar.appointments[0]
    calendar.appointments[0] = replace(saved, blocks_time=False)
    assert not tools.book_slot("caller_A", {"option_id": option, "confirmed": True})["ok"]
    calendar.appointments[0] = saved  # the cancellation was undone
    assert tools.book_slot("caller_A", {"option_id": option, "confirmed": True}) == first
    assert len(calendar.create_calls) == 1


def test_v2_the_reverification_is_read_only_and_cannot_create(setup):
    tools, calendar, factory, *_ = setup
    option = offer(tools, "caller_A")
    confirm(tools, "caller_A", option)
    calendar.appointments.clear()
    tools.book_slot("caller_A", {"option_id": option, "confirmed": True})
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(VoiceTools._reconfirm)))
    called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "verify" in called and not {"book", "create_appointment", "click", "create"} & called, called


def test_v2_a_failure_inside_the_reverification_is_an_honest_uncertainty(setup):
    tools, calendar, factory, cfg, clock, new_tools = setup
    option = offer(tools, "caller_A")
    first = confirm(tools, "caller_A", option)
    assert first["ok"]

    class Broken(VoiceTools):
        pass

    tools._service_factory = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    reply = tools.book_slot("caller_A", {"option_id": option, "confirmed": True})
    assert not reply["ok"] and reply["status"] == "needs_review" and "boom" not in str(reply)


# ---------------------------------------------------------------- V3: an uncertain outcome is never spoken as completed


def test_v3_an_uncertain_booking_does_not_become_already_done_on_another_option(setup):
    tools, calendar, *_ = setup
    first_option, second_option = offer(tools, "caller_A", "10:00"), offer(tools, "caller_A", "15:00")
    calendar.create_mode = "unknown_not_saved"
    outcome = confirm(tools, "caller_A", first_option)
    assert outcome["status"] == "needs_review" and not calendar.appointments
    reply = tools.book_slot("caller_A", {"option_id": second_option, "confirmed": True})
    assert not reply["ok"] and reply["status"] == "refused" and reply["reason"] == "earlier_outcome_unknown"
    assert len(calendar.create_calls) == 1, "a second uncertain write is not started"
    low = reply["speak"].lower()
    assert "already done" not in low and "is made" not in low and "i made one booking" not in low
    assert "couldn't confirm what happened" in low and "won't start another" in low and "don't treat anything as booked" in low


def test_v3_after_a_real_success_a_second_option_gets_a_true_one_per_call_statement(setup):
    tools, calendar, *_ = setup
    first_option, second_option = offer(tools, "caller_A", "10:00"), offer(tools, "caller_A", "15:00")
    assert confirm(tools, "caller_A", first_option)["ok"]
    reply = tools.book_slot("caller_A", {"option_id": second_option, "confirmed": False})
    assert reply["reason"] == "one_booking_per_call" and not reply["ok"]
    assert "i made one booking earlier in this call" in reply["speak"].lower() and len(calendar.create_calls) == 1


def test_v3_the_unknown_outcome_stays_unknown_when_the_same_option_is_repeated(setup):
    tools, calendar, *_ = setup
    option = offer(tools, "caller_A")
    calendar.create_mode = "unknown_not_saved"
    first = confirm(tools, "caller_A", option)
    assert tools.book_slot("caller_A", {"option_id": option, "confirmed": True}) == first
    assert len(calendar.create_calls) == 1


# ---------------------------------------------------------------- no promise of an alert or a callback that does not exist


FORBIDDEN = ("team member", "follow up", "follow-up", "call you back", "call back", "notified", "we will contact", "someone will", "text you", "alert")


def _strings(module):
    tree = ast.parse(inspect.getsource(module))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


@pytest.mark.parametrize("module", [tools_module, retell_http])
def test_no_spoken_text_promises_staff_follow_up_or_an_alert_until_that_path_exists(module):
    spoken = [s for s in _strings(module) if " " in s and ("I " in s or "I'" in s or "Please" in s)]
    assert spoken, "found no spoken strings to check"
    for text in spoken:
        for phrase in FORBIDDEN:
            assert phrase not in text.lower() or "instruction" in text.lower(), f"{phrase!r} promised in: {text}"


def test_the_draft_prompt_forbids_the_promise_and_makes_none():
    import re
    from pathlib import Path

    prompt = (Path(__file__).resolve().parent.parent / "voice_agent_draft" / "prompt_test_only.md").read_text(encoding="utf-8")
    assert "Do not promise follow-up" in prompt
    # the prompt may NAME the forbidden promises (in a prohibition) but must not make one
    without_prohibitions = re.sub(r"Never say a team member will call, check,\s+follow up or has been notified\.|ANY promise of a callback[^\n]*\n[^\n]*", "", prompt)
    lowered = without_prohibitions.lower()
    for sentence in ("a team member will follow up", "a team member will check", "a team member will help", "a team member will call me",
                     "we will call you back", "you will receive a text"):
        assert sentence not in lowered, sentence
    assert "contact the salon directly" in lowered


def test_every_non_success_response_stays_non_success_with_the_new_wording(setup):
    tools, calendar, *_ = setup
    option = offer(tools, "caller_A")
    confirm(tools, "caller_A", option)
    calendar.appointments.clear()
    for response in (
        tools.book_slot("caller_A", {"option_id": option, "confirmed": True}),
        tools.book_slot("caller_A", {"option_id": "opt_forged", "confirmed": True}),
        tools.check_slot("caller_B", {"date": "bad", "time": "10:00"}),
    ):
        assert response["ok"] is (response["status"] in SUCCESS_STATUSES)
        assert not response["ok"]


def test_v1_a_ledger_entry_written_before_owners_existed_belongs_to_nobody_and_cannot_be_claimed(setup):
    """The real first booking was made from the command line before this field existed; a voice caller must not own it."""
    import json

    tools, calendar, factory, cfg, clock, _ = setup
    start = datetime(2026, 10, 12, 10, 0, tzinfo=TZ)
    key = key_for(cfg)
    legacy = {key: {"key": key, "ref": "ARIA-" + key[:8].upper(), "state": "verified", "staff": cfg.staff_name, "service": cfg.service_name,
                    "start": start.isoformat(), "end": (start + timedelta(minutes=150)).isoformat(), "created_at": "c", "updated_at": "2026-10-08T12:00:00+00:00",
                    "detail": "d", "observed": True}}
    cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.ledger_path.write_text(json.dumps(legacy), encoding="utf-8")
    entry = Ledger(cfg.ledger_path).get(key)
    assert entry.owner == "" and entry.observed is True
    result = factory().book(start, owner=VoiceTools._owner("caller_A"))
    assert result.status is Status.SLOT_UNAVAILABLE and calendar.create_calls == []
