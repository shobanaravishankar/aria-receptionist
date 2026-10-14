"""The simulated appointment store: transactions, identity, idempotency, atomic moves and the notification outbox. Synthetic data, a temporary file, no network.

The concurrency tests use real threads against one SQLite file: the point is that the DATABASE serialises the writers, not that Python's scheduler is lucky.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from aria_booking.appointments.models import (
    CANCELLED, COMPLETED, CONFIRMED, NewBooking, OutboxMessage, Placement, Refused, to_epoch,
)
from aria_booking.appointments.store import SCHEMA_VERSION, AppointmentStore, StoreError

NOW = to_epoch(datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc))
HOUR = 3600
PHONE = "+12125550142"  # a reserved fictional number


class Clock:
    def __init__(self, now: int = NOW):
        self.now = now

    def __call__(self) -> int:
        return self.now


def make(tmp_path, clock=None, name="appointments.sqlite3") -> AppointmentStore:
    return AppointmentStore(tmp_path / name, clock=clock or Clock())


def booking(start_hours: float = 24, *, staff="910006", name="Willa Hartwell", phone=PHONE, minutes=60, buffer_after=15, key=None, service="massage-swedish-60") -> NewBooking:
    return NewBooking(
        client_name=name, phone=phone, service_id=service, service_name="Swedish Massage, 1 hour", category="massage", staff_id=staff,
        start_utc=NOW + int(start_hours * HOUR), duration_minutes=minutes, buffer_after_minutes=buffer_after, request_key=key,
    )


def kind_builder(kind):
    return lambda saved, previous: OutboxMessage(kind, f"{kind} {saved.reference}")


# ---------------------------------------------------------------- identity and persistence


def test_a_booking_is_saved_with_a_stable_reference_and_survives_a_restart(tmp_path):
    first = make(tmp_path)
    out = first.book(booking(), outbox=kind_builder("booked"))
    assert out.status == "booked" and out.ok and out.appointment.status == CONFIRMED and out.appointment.version == 1
    reference = out.appointment.reference
    assert len(reference) == 6 and set(reference) <= set("23456789ABCDEFGHJKMNPQRSTUVWXYZ")
    reopened = make(tmp_path)  # a new process: nothing was held in memory
    again = reopened.get(reference)
    assert again == out.appointment and again.client_name == "Willa Hartwell" and again.start_utc == NOW + 24 * HOUR
    assert reopened.counts()["appointments"] == {"confirmed": 1}


def test_a_reschedule_moves_the_same_row_and_keeps_its_reference(tmp_path):
    store = make(tmp_path)
    saved = store.book(booking(24)).appointment
    moved = store.reschedule(saved.reference, start_utc=NOW + 48 * HOUR, expected_version=1)
    assert moved.status == "rescheduled" and moved.appointment.reference == saved.reference
    assert moved.appointment.start_utc == NOW + 48 * HOUR and moved.appointment.version == 2 and moved.previous.start_utc == NOW + 24 * HOUR
    assert store.counts()["appointments"] == {"confirmed": 1}, "moved, not copied"
    assert [e["kind"] for e in store.events(saved.reference)] == ["booked", "rescheduled"]


def test_a_seed_keeps_one_stable_reference_and_is_never_made_twice_or_brought_back(tmp_path):
    store = make(tmp_path)
    seed = NewBooking("Willa Hartwell", PHONE, "massage-swedish-60", "Swedish Massage, 1 hour", "massage", "910006", NOW + 48 * HOUR, 60, origin="seed", seed_key="willa:tue-waxing:2026-10-22")
    first = store.book(seed)
    second = store.book(seed)
    assert first.status == "booked" and second.status == "replayed" and second.appointment.reference == first.appointment.reference
    assert store.counts()["appointments"] == {"confirmed": 1}
    elsewhere = make(tmp_path, name="other.sqlite3").book(seed)
    assert elsewhere.appointment.reference == first.appointment.reference, "the reference is derived from the seed key, so it is the same in a rebuilt file"
    assert store.cancel(first.appointment.reference).status == "cancelled"
    third = store.book(seed)
    assert third.status == "replayed" and third.appointment.status == CANCELLED, "a cancelled seed is not resurrected by seeding again"


def test_seeded_rows_never_write_a_notification(tmp_path):
    store = make(tmp_path)
    store.book(NewBooking("Willa Hartwell", PHONE, "s", "S", "massage", "910006", NOW + 24 * HOUR, 60, origin="seed", seed_key="k1"), outbox=kind_builder("booked"))
    assert store.outbox_rows() == []


def test_a_file_from_another_schema_version_is_refused_not_guessed(tmp_path):
    make(tmp_path)
    conn = sqlite3.connect(tmp_path / "appointments.sqlite3")
    conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 1),))
    conn.commit()
    conn.close()
    with pytest.raises(StoreError, match="schema version"):
        make(tmp_path)


# ---------------------------------------------------------------- the same slot, raced and repeated


def test_two_callers_racing_for_the_same_slot_one_wins_and_the_database_decides(tmp_path):
    store = make(tmp_path)
    results, gate = [], threading.Barrier(8)

    def attempt(index: int) -> None:
        gate.wait()
        results.append(store.book(booking(24, name=f"Guest {index}", phone=f"+1212555{index:04d}", key=f"req-{index}"), outbox=kind_builder("booked")))

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    statuses = sorted(r.status for r in results)
    assert statuses == ["booked"] + ["unavailable"] * 7, statuses
    assert store.counts()["appointments"] == {"confirmed": 1} and store.counts()["clients"] == 1, "a refused caller leaves no client record behind"
    assert len(store.outbox_rows()) == 1, "exactly one notification, for the one booking that exists"


def test_overlapping_but_not_identical_times_are_refused_and_touching_ones_are_allowed(tmp_path):
    store = make(tmp_path)
    assert store.book(booking(24, minutes=60)).ok
    assert store.book(booking(24.5, name="B", phone="+12125550101", minutes=60)).status == "unavailable"
    assert store.book(booking(23.5, name="C", phone="+12125550102", minutes=60)).status == "unavailable"
    assert store.book(booking(25, name="D", phone="+12125550103", minutes=60)).ok, "back to back is fine"
    assert store.book(booking(24, name="E", phone="+12125550104", staff="910001")).ok, "another technician, same time"


def test_a_repeated_request_with_the_same_key_returns_the_first_result_and_saves_nothing_more(tmp_path):
    store = make(tmp_path)
    first = store.book(booking(24, key="req-1"), outbox=kind_builder("booked"))
    again = store.book(booking(24, key="req-1"), outbox=kind_builder("booked"))
    assert again.status == "replayed" and again.appointment.reference == first.appointment.reference
    assert store.counts()["appointments"] == {"confirmed": 1} and len(store.outbox_rows()) == 1


def test_the_same_request_arriving_on_many_threads_makes_one_appointment(tmp_path):
    store = make(tmp_path)
    results, gate = [], threading.Barrier(10)

    def attempt() -> None:
        gate.wait()
        results.append(store.book(booking(24, key="same-request"), outbox=kind_builder("booked")))

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.status for r in results) == ["booked"] + ["replayed"] * 9
    assert len({r.appointment.reference for r in results}) == 1 and len(store.outbox_rows()) == 1


def test_a_key_reused_for_a_different_kind_of_request_is_refused(tmp_path):
    store = make(tmp_path)
    saved = store.book(booking(24, key="k")).appointment
    assert store.cancel(saved.reference, request_key="k").status == "request_key_reused"
    assert store.get(saved.reference).status == CONFIRMED


def test_the_same_client_cannot_hold_two_overlapping_appointments(tmp_path):
    store = make(tmp_path)
    assert store.book(booking(24)).ok
    again = store.book(booking(24.5, staff="910001"))  # another technician, but the same person at the same time
    assert again.status == "unavailable" and again.reason == "client_already_booked"
    assert store.book(booking(24.5, staff="910001", name="Ansel Hartwell")).ok, "a different client sharing the number is a different record"


# ---------------------------------------------------------------- refusals leave nothing behind


def test_a_validator_refusal_saves_nothing_creates_no_client_and_does_not_burn_the_request_key(tmp_path):
    store = make(tmp_path)

    def no(tx, placement):
        raise Refused("unavailable", "outside_hours")

    refused = store.book(booking(24, key="k"), validate=no, outbox=kind_builder("booked"))
    assert refused.status == "unavailable" and refused.reason == "outside_hours" and not refused.ok
    assert store.counts() == {"appointments": {}, "clients": 0, "outbox": {}}
    assert store.book(booking(24, key="k")).status == "booked", "a refusal is not remembered: the caller may ask again with the same key"


def test_an_unexpected_failure_inside_the_transaction_rolls_everything_back(tmp_path):
    store = make(tmp_path)

    def explode(saved, previous):
        raise RuntimeError("the notification could not be built")

    with pytest.raises(RuntimeError):
        store.book(booking(24, key="k"), outbox=explode)
    assert store.counts() == {"appointments": {}, "clients": 0, "outbox": {}}
    assert store.book(booking(24, key="k")).status == "booked"


def test_the_validator_sees_the_transactions_own_rows(tmp_path):
    store = make(tmp_path)
    store.book(booking(24))
    seen = []

    def watch(tx, placement):
        seen.append([a.reference for a in tx.between(placement.start_utc, placement.end_utc, staff_id=placement.staff_id)])

    store.book(booking(30, name="Other", phone="+12125550105"), validate=watch)
    store.book(booking(24.25, name="Third", phone="+12125550106"), validate=watch)
    assert seen[0] == [] and len(seen[1]) == 1


# ---------------------------------------------------------------- cancel


def test_cancelling_releases_the_slot_and_it_can_be_booked_again(tmp_path):
    store = make(tmp_path)
    saved = store.book(booking(24)).appointment
    assert store.book(booking(24, name="B", phone="+12125550101")).status == "unavailable"
    out = store.cancel(saved.reference, expected_version=1, reason="  Something   came up\n", request_key="c1", outbox=kind_builder("cancelled"))
    assert out.status == "cancelled" and out.appointment.status == CANCELLED and out.appointment.version == 2 and out.appointment.cancel_reason == "Something came up"
    assert store.book(booking(24, name="B", phone="+12125550101")).status == "booked"
    assert store.get(saved.reference).status == CANCELLED, "the cancelled record is kept (for history), only its slot is free"


def test_a_repeated_cancellation_changes_nothing_and_never_touches_another_appointment(tmp_path):
    store = make(tmp_path)
    one = store.book(booking(24)).appointment
    two = store.book(booking(30, name="B", phone="+12125550101")).appointment
    assert store.cancel(one.reference, expected_version=1, outbox=kind_builder("cancelled")).status == "cancelled"
    repeat = store.cancel(one.reference, expected_version=1, outbox=kind_builder("cancelled"))  # a retry that still carries the old version
    assert repeat.status == "already_cancelled" and repeat.appointment.version == 2
    assert store.get(two.reference).status == CONFIRMED and store.get(two.reference).version == 1
    assert [r["kind"] for r in store.outbox_rows()] == ["cancelled"], "one notification for one cancellation"


def test_cancel_refuses_an_unknown_reference_a_stale_confirmation_a_started_visit_and_a_finished_one(tmp_path):
    clock = Clock()
    store = make(tmp_path, clock)
    saved = store.book(booking(24)).appointment
    assert store.cancel("NOSUCH").status == "not_found"
    store.reschedule(saved.reference, start_utc=NOW + 26 * HOUR)  # version is now 2
    stale = store.cancel(saved.reference, expected_version=1)
    assert stale.status == "stale" and stale.appointment.start_utc == NOW + 26 * HOUR and store.get(saved.reference).status == CONFIRMED
    clock.now = NOW + 27 * HOUR  # the (moved) visit has begun
    assert store.cancel(saved.reference).status == "not_cancellable" and store.cancel(saved.reference).reason == "already_started"
    done = store.add_history(NewBooking("Willa Hartwell", PHONE, "s", "S", "massage", "910006", NOW - 24 * HOUR, 60, origin="test"), COMPLETED).appointment
    assert store.cancel(done.reference).status == "not_cancellable" and store.get(done.reference).status == COMPLETED


def test_the_reason_is_optional_bounded_and_printable_only(tmp_path):
    store = make(tmp_path)
    a = store.book(booking(24)).appointment
    b = store.book(booking(30, name="B", phone="+12125550101")).appointment
    assert store.cancel(a.reference).appointment.cancel_reason is None
    long = store.cancel(b.reference, reason="x" * 500).appointment.cancel_reason
    assert len(long) == 200 and set(long) == {"x"}
    c = store.book(booking(36, name="C", phone="+12125550102")).appointment
    assert store.cancel(c.reference, reason="a\x00b\x07c  d").appointment.cancel_reason == "abc d", "control characters are dropped, not stored"


# ---------------------------------------------------------------- reschedule is atomic


def test_a_refused_move_leaves_the_original_exactly_as_it_was(tmp_path):
    store = make(tmp_path)
    saved = store.book(booking(24)).appointment
    before = store.get(saved.reference)

    def no(tx, placement):
        raise Refused("unavailable", "conflicts_next")

    out = store.reschedule(saved.reference, start_utc=NOW + 48 * HOUR, validate=no, outbox=kind_builder("rescheduled"))
    assert out.status == "unavailable" and store.get(saved.reference) == before
    assert [e["kind"] for e in store.events(saved.reference)] == ["booked"] and store.outbox_rows() == []


def test_a_failure_while_saving_a_move_keeps_the_original_and_writes_no_notification(tmp_path):
    store = make(tmp_path)
    saved = store.book(booking(24)).appointment
    before = store.get(saved.reference)

    def explode(new, old):
        raise RuntimeError("could not build the message")

    with pytest.raises(RuntimeError):
        store.reschedule(saved.reference, start_utc=NOW + 48 * HOUR, outbox=explode)
    assert store.get(saved.reference) == before and [e["kind"] for e in store.events(saved.reference)] == ["booked"]


def test_a_move_may_overlap_the_appointments_own_old_time_but_not_anyone_elses(tmp_path):
    store = make(tmp_path)
    mine = store.book(booking(24)).appointment
    other = store.book(booking(26, name="B", phone="+12125550101")).appointment
    assert store.reschedule(mine.reference, start_utc=NOW + int(24.5 * HOUR)).status == "rescheduled", "a quarter-hour nudge overlaps itself, which is fine"
    clash = store.reschedule(mine.reference, start_utc=other.start_utc + 15 * 60)
    assert clash.status == "unavailable" and clash.reason == "slot_taken" and store.get(mine.reference).start_utc == NOW + int(24.5 * HOUR)


def test_a_move_to_the_same_time_is_a_no_op_and_a_repeated_move_is_not_applied_twice(tmp_path):
    store = make(tmp_path)
    saved = store.book(booking(24)).appointment
    assert store.reschedule(saved.reference, start_utc=saved.start_utc).status == "unchanged"
    first = store.reschedule(saved.reference, start_utc=NOW + 48 * HOUR, request_key="m1", outbox=kind_builder("rescheduled"))
    again = store.reschedule(saved.reference, start_utc=NOW + 48 * HOUR, request_key="m1", outbox=kind_builder("rescheduled"))
    assert first.status == "rescheduled" and again.status == "replayed" and store.get(saved.reference).version == 2 and len(store.outbox_rows()) == 1


def test_a_stale_move_a_cancelled_one_and_a_missing_one_are_refused(tmp_path):
    store = make(tmp_path)
    saved = store.book(booking(24)).appointment
    store.cancel(saved.reference)
    assert store.reschedule(saved.reference, start_utc=NOW + 48 * HOUR).status == "not_reschedulable"
    fresh = store.book(booking(30, name="B", phone="+12125550101")).appointment
    store.reschedule(fresh.reference, start_utc=NOW + 31 * HOUR)
    assert store.reschedule(fresh.reference, start_utc=NOW + 40 * HOUR, expected_version=1).status == "stale"
    assert store.reschedule("NOSUCH", start_utc=NOW + 40 * HOUR).status == "not_found"


def test_a_cancel_and_a_move_racing_for_one_appointment_end_in_one_consistent_state(tmp_path):
    for _ in range(5):
        store = make(tmp_path, name=f"race-{_}.sqlite3")
        saved = store.book(booking(24)).appointment
        results, gate = {}, threading.Barrier(2)

        def cancel():
            gate.wait()
            results["cancel"] = store.cancel(saved.reference, expected_version=1)

        def move():
            gate.wait()
            results["move"] = store.reschedule(saved.reference, start_utc=NOW + 48 * HOUR, expected_version=1)

        threads = [threading.Thread(target=cancel), threading.Thread(target=move)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [k for k, r in results.items() if r.ok]
        assert len(winners) == 1, results  # the loser carried a version that is now stale (or the row is no longer confirmed)
        final = store.get(saved.reference)
        assert (final.status == CANCELLED) == (winners == ["cancel"]) and final.version == 2


# ---------------------------------------------------------------- notifications and privacy


def test_notifications_are_written_with_the_change_deduplicated_and_never_before_it_commits(tmp_path):
    store = make(tmp_path)
    saved = store.book(booking(24, key="k"), outbox=kind_builder("booked")).appointment
    store.reschedule(saved.reference, start_utc=NOW + 48 * HOUR, outbox=kind_builder("rescheduled"))
    store.cancel(saved.reference, outbox=kind_builder("cancelled"))
    rows = store.outbox_rows()
    assert [r["kind"] for r in rows] == ["booked", "rescheduled", "cancelled"] and all(r["status"] == "pending" and r["attempts"] == 0 for r in rows)
    assert [r["dedupe_key"] for r in rows] == [f"booked:{saved.reference}:1", f"rescheduled:{saved.reference}:2", f"cancelled:{saved.reference}:3"]
    refused = store.book(booking(24, name="B", phone="+12125550101"), validate=lambda tx, p: (_ for _ in ()).throw(Refused("unavailable", "x")), outbox=kind_builder("booked"))
    assert refused.status == "unavailable" and len(store.outbox_rows()) == 3, "a refused request writes no message"


def test_the_event_log_and_counts_hold_no_names_or_phone_numbers(tmp_path):
    store = make(tmp_path)
    saved = store.book(booking(24, name="Willa Hartwell")).appointment
    store.reschedule(saved.reference, start_utc=NOW + 48 * HOUR)
    store.cancel(saved.reference, reason="a private reason")
    text = json.dumps(store.events(saved.reference)) + json.dumps(store.counts())
    for secret in ("Willa", "Hartwell", "2125550142", "private"):
        assert secret not in text


def test_history_rows_are_only_for_seeding_and_never_notify_or_block_the_future(tmp_path):
    store = make(tmp_path)
    with pytest.raises(ValueError):
        store.add_history(booking(24), COMPLETED)  # a voice-origin row cannot be added as history
    with pytest.raises(ValueError):
        store.add_history(NewBooking("A", PHONE, "s", "S", "massage", "910006", NOW - HOUR, 60, origin="test"), CONFIRMED)
    out = store.add_history(NewBooking("Willa Hartwell", PHONE, "s", "S", "massage", "910006", NOW - 24 * HOUR, 60, origin="test"), COMPLETED)
    assert out.appointment.status == COMPLETED and store.outbox_rows() == []
    assert store.book(booking(-24)).status == "booked", "a past completed visit does not block that time (it is not confirmed)"
