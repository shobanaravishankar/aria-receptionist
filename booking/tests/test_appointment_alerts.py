"""The owner alerts: configuration, the dispatcher, the worker, and the promises around them, all with a FAKE sender. Nothing here can reach a network or a phone.

The rules under test: no text before the commit; no duplicate text from a retry; a failure never rolls back a saved appointment; success is never announced to the caller;
the only recipient is the configured owner number; and with no provider or number, nothing is sent and the alerts simply wait.
"""

from __future__ import annotations

import json
import threading

import pytest

from aria_booking.appointments.dispatcher import AlertWorker, OutboxDispatcher
from aria_booking.appointments.models import CONFIRMED, NewBooking, OutboxMessage, to_epoch
from aria_booking.appointments.sender import (
    OWNER_ENV, OWNER_FILE, FakeSender, NotConfiguredSender, OwnerDestinationError, SendResult, fictional, load_owner_destination,
)
from aria_booking.appointments.store import AppointmentStore, StoreError

from test_appointment_tools import Env, WILLA, WILLA_PHONE, SWEDISH_60, TOMORROW, cancel_args, move_args

OWNER = "+13105550000"  # a stand-in for the owner's own number: the tests never use a real one
OWNER_REAL_LOOKING = "+13105559876"


def make_env(tmp_path, sender=None, destination=OWNER, **dispatcher_kw):
    holder = {}
    env = Env(tmp_path, on_committed=lambda: holder["dispatcher"].dispatch())
    holder["dispatcher"] = OutboxDispatcher(env.lc.store, sender, destination, log=env.log.append, **dispatcher_kw)
    env.dispatcher = holder["dispatcher"]
    return env


# ---------------------------------------------------------------- where an alert goes


def test_the_owner_number_comes_from_local_config_only_and_is_normalised(tmp_path):
    assert load_owner_destination(tmp_path, env={}) is None
    (tmp_path / OWNER_FILE).write_text(json.dumps({"to": "(310) 555-9876"}), encoding="utf-8")
    assert load_owner_destination(tmp_path, env={}) == OWNER_REAL_LOOKING
    assert load_owner_destination(tmp_path, env={OWNER_ENV: "+44 20 7946 0958"}) == "+442079460958", "the environment wins over the file"
    (tmp_path / OWNER_FILE).write_text(json.dumps({"to": ""}), encoding="utf-8")
    assert load_owner_destination(tmp_path, env={}) is None


@pytest.mark.parametrize("bad", ["555-0100", "call me", "+1 212", "310 555 98"])
def test_an_incomplete_owner_number_is_refused_not_completed(tmp_path, bad):
    with pytest.raises(OwnerDestinationError, match="complete phone number"):
        load_owner_destination(tmp_path, env={OWNER_ENV: bad})


def test_a_fictional_or_unreadable_destination_is_refused(tmp_path):
    with pytest.raises(OwnerDestinationError, match="fictional"):
        load_owner_destination(tmp_path, env={OWNER_ENV: "+1 212 555 0142"})  # the seeded clients' range: a fixture can never receive a message
    (tmp_path / OWNER_FILE).write_text("{not json", encoding="utf-8")
    with pytest.raises(OwnerDestinationError, match="unreadable"):
        load_owner_destination(tmp_path, env={})
    assert fictional("+12125550142") and fictional("+12125550199") and not fictional("+12125550200") and not fictional("+13105559876")


# ---------------------------------------------------------------- nothing configured: nothing sent, alerts wait


def test_without_a_provider_or_a_number_nothing_is_claimed_or_sent_and_the_alerts_wait(tmp_path):
    for index, (sender, destination) in enumerate(((None, OWNER), (FakeSender(), None), (None, None))):
        env = make_env(tmp_path / f"case-{index}", sender, destination)
        assert env.book()["status"] == "booked_verified"
        rows = env.lc.store.outbox_rows()
        assert [(r["status"], r["attempts"]) for r in rows] == [("pending", 0)]
        report = env.dispatcher.dispatch()
        assert report.skipped and (report.sent, report.retry, report.failed, report.uncertain) == (0, 0, 0, 0)
        assert env.dispatcher.status() == {"configured": False, "outbox": {"pending": 1}}
    assert NotConfiguredSender().send("+1", "x", message_key="k").status == "rejected"


# ---------------------------------------------------------------- what is sent, to whom, and when


def test_a_booking_sends_one_alert_to_the_owner_only_after_it_has_committed(tmp_path):
    sender, seen = FakeSender(), []
    original = sender.send

    def checking(to, body, *, message_key):
        ref = body.split("Ref ")[1].rstrip(".")
        seen.append(env.lc.store.get(ref).status)  # at the moment of sending, the appointment is already saved
        return original(to, body, message_key=message_key)

    sender.send = checking
    env = make_env(tmp_path, sender)
    out = env.book(name="Mira Quillfeather", phone="+1 212 555 0188")
    assert out["status"] == "booked_verified" and seen == [CONFIRMED]
    (to, body, key), = sender.sent
    assert to == OWNER and body.startswith("[Serenity prototype alert] Booked: Mira Quillfeather, Swedish Massage, 1 hour with Sienna")
    assert "0188" not in body and key == f"booked:{out['appointment']['reference']}:1"
    assert [r["status"] for r in env.lc.store.outbox_rows()] == ["sent"]


def test_the_recipient_is_the_owner_never_the_client_or_anything_a_caller_said(tmp_path):
    sender = FakeSender()
    env = make_env(tmp_path, sender)
    env.book(name="Ten Digits", phone="+1 212 555 0188")
    env.book(14, name="Other Caller", phone=WILLA_PHONE, call_id="c2")
    assert {to for to, _b, _k in sender.sent} == {OWNER} and len(sender.sent) == 2


def test_cancelling_and_moving_each_send_one_alert_and_a_move_is_one_coherent_message(tmp_path):
    sender = FakeSender()
    env = make_env(tmp_path, sender)
    env.confirm("cancel_appointment", cancel_args(env, "waxing"))
    env.confirm("reschedule_appointment", move_args(env, "massage"), call_id="c2")
    assert [b.split(":")[0] for _t, b, _k in sender.sent] == ["[Serenity prototype alert] Cancelled", "[Serenity prototype alert] Rescheduled"]
    moved = sender.sent[1][1]
    assert "Was Sun Oct 25" in moved and "; now Wed Oct 14 4:00PM-5:00PM with Sienna." in moved and "Cancelled" not in moved and "Booked" not in moved


def test_refusals_lookups_seeding_and_settling_send_nothing(tmp_path):
    sender = FakeSender()
    env = make_env(tmp_path, sender)
    env.call("find_appointments", {"phone": WILLA_PHONE, "name": WILLA})
    env.call("last_completed_visit", {"service_category": "lash", "phone": WILLA_PHONE, "name": WILLA})
    env.call("book_appointment", env.book_args(11))  # a time that is not open: not even read back
    env.call("cancel_appointment", {"reference": "ZZZZZZ", "phone": WILLA_PHONE, "name": WILLA})
    assert sender.sent == [] and env.lc.store.outbox_rows() == [] and env.dispatcher.dispatch().sent == 0


def test_a_repeated_or_retried_request_never_texts_twice(tmp_path):
    sender = FakeSender()
    env = make_env(tmp_path, sender)
    args = env.book_args()
    first = env.call("book_appointment", args)
    confirm = {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]}
    assert env.call("book_appointment", confirm)["status"] == "booked_verified"
    assert env.call("book_appointment", confirm)["status"] == "already_booked"
    cancel = cancel_args(env, "waxing")
    read_back = env.call("cancel_appointment", cancel)
    done = {**cancel, "confirmed": True, "confirmation_token": read_back["confirmation_token"]}
    for _ in range(3):
        env.call("cancel_appointment", done)
    assert len(sender.sent) == 2 and env.dispatcher.dispatch().sent == 0


def test_many_dispatchers_racing_for_one_alert_send_it_once(tmp_path):
    sender = FakeSender()
    store = AppointmentStore(tmp_path / "a.sqlite3")
    store.book(NewBooking("Mira Quillfeather", "+12125550188", "s", "S", "massage", "910006", 2_000_000_000, 60), outbox=lambda saved, prev: OutboxMessage("booked", "one alert"))
    gate = threading.Barrier(8)
    reports = []

    def go():
        gate.wait()
        reports.append(OutboxDispatcher(store, sender, OWNER).dispatch())

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(sender.sent) == 1 and sum(r.sent for r in reports) == 1


# ---------------------------------------------------------------- failure never undoes an appointment and is never announced


def test_a_refused_text_keeps_the_appointment_is_retried_a_limited_number_of_times_and_never_reaches_the_caller(tmp_path):
    sender = FakeSender("rejected", "rejected", "rejected", "sent")
    env = make_env(tmp_path, sender)
    out = env.book()
    assert out["status"] == "booked_verified" and "text" not in out["speak"].lower() and "message" not in out["speak"].lower() and "alert" not in out["speak"].lower()
    ref = out["appointment"]["reference"]
    assert env.lc.store.get(ref).status == CONFIRMED
    row = env.lc.store.outbox_rows()[0]
    assert (row["status"], row["attempts"], row["last_error"]) == ("pending", 1, "scripted")
    assert env.dispatcher.dispatch().retry == 1 and env.dispatcher.dispatch().failed == 1  # attempts 2 (retried) and 3 (given up)
    row = env.lc.store.outbox_rows()[0]
    assert (row["status"], row["attempts"]) == ("failed", 3)
    assert env.dispatcher.dispatch().sent == 0 and len(sender.sent) == 3, "a failed alert is not tried again"
    assert env.lc.store.get(ref).status == CONFIRMED and [e["kind"] for e in env.lc.store.events(ref)] == ["booked"]


def test_an_unknown_outcome_is_never_sent_again_because_that_could_text_the_owner_twice(tmp_path):
    sender = FakeSender("raise")
    env = make_env(tmp_path, sender)
    assert env.book()["status"] == "booked_verified"
    assert [r["status"] for r in env.lc.store.outbox_rows()] == ["uncertain"]
    assert env.dispatcher.dispatch().sent == 0 and len(sender.sent) == 1
    explicit = FakeSender("uncertain")
    env2 = make_env(tmp_path / "second", explicit)
    env2.book()
    assert [r["status"] for r in env2.lc.store.outbox_rows()] == ["uncertain"]


def test_a_crash_after_claiming_leaves_an_uncertain_row_that_is_not_resent(tmp_path):
    store = AppointmentStore(tmp_path / "a.sqlite3")
    store.book(NewBooking("Mira Quillfeather", "+12125550188", "s", "S", "massage", "910006", 2_000_000_000, 60), outbox=lambda saved, prev: OutboxMessage("booked", "an alert"))
    assert len(store.outbox_claim(10)) == 1  # a dispatcher claims it and dies before recording anything
    sender = FakeSender()
    assert OutboxDispatcher(store, sender, OWNER, lease_seconds=3600).dispatch().sent == 0 and [r["status"] for r in store.outbox_rows()] == ["sending"], "still within its lease"
    assert OutboxDispatcher(store, sender, OWNER, lease_seconds=0).dispatch().sent == 0
    assert [r["status"] for r in store.outbox_rows()] == ["uncertain"] and sender.sent == []


def test_a_row_can_only_be_finished_by_the_claim_that_holds_it(tmp_path):
    store = AppointmentStore(tmp_path / "a.sqlite3")
    store.book(NewBooking("Mira Quillfeather", "+12125550188", "s", "S", "massage", "910006", 2_000_000_000, 60), outbox=lambda saved, prev: OutboxMessage("booked", "an alert"))
    row_id = store.outbox_rows()[0]["outbox_id"]
    store.outbox_finish(row_id, "sent")  # nobody has claimed it: nothing happens
    assert store.outbox_rows()[0]["status"] == "pending"
    with pytest.raises(ValueError):
        store.outbox_finish(row_id, "sending")


# ---------------------------------------------------------------- logs and the worker


def test_the_log_names_the_alert_and_the_result_but_never_the_message_the_number_or_a_name(tmp_path):
    sender = FakeSender()
    env = make_env(tmp_path, sender)
    env.book(name="Mira Quillfeather", phone="+1 212 555 0188")
    blob = " ".join(env.log)
    assert "alert booked #1: sent" in blob
    for secret in ("Mira", "Quillfeather", "0188", "3105550000", "Swedish", "Serenity prototype alert"):
        assert secret not in blob


def test_the_worker_sends_when_woken_by_a_commit_and_not_before_and_stops_cleanly(tmp_path):
    sender, done = FakeSender(), threading.Event()
    real = sender.send

    def signalling(to, body, *, message_key):
        result = real(to, body, message_key=message_key)
        done.set()
        return result

    sender.send = signalling
    env = Env(tmp_path)
    worker = AlertWorker(OutboxDispatcher(env.lc.store, sender, OWNER))
    worker.start()
    assert not done.wait(0.3) and sender.sent == [], "nothing to send, and no polling"
    env.lc.store.book(NewBooking("Mira Quillfeather", "+12125550188", "s", "S", "massage", "910006", 2_000_000_000, 60), outbox=lambda saved, prev: OutboxMessage("booked", "an alert"))
    assert not done.wait(0.3), "committed, but the worker has not been told"
    worker.trigger()
    assert done.wait(5) and len(sender.sent) == 1
    worker.stop()
    worker.stop()


def test_the_worker_survives_a_failure_and_a_worker_without_a_provider_never_starts(tmp_path, monkeypatch):
    env = Env(tmp_path)
    log = []
    dispatcher = OutboxDispatcher(env.lc.store, FakeSender(), OWNER)
    calls, ran = [], threading.Event()

    def failing(limit=10):
        calls.append(1)
        ran.set()
        raise StoreError("locked")

    monkeypatch.setattr(dispatcher, "dispatch", failing)
    worker = AlertWorker(dispatcher, log=log.append)
    worker.start()
    assert ran.wait(5)
    ran.clear()
    worker.trigger()
    assert ran.wait(5) and len(calls) >= 2 and any("dispatch failed (StoreError)" in line for line in log)
    worker.stop()
    idle = AlertWorker(OutboxDispatcher(env.lc.store, None, OWNER))
    idle.start()
    assert idle._thread is None
    idle.stop()


def test_a_start_up_pass_sends_what_was_waiting_from_before(tmp_path):
    store = AppointmentStore(tmp_path / "a.sqlite3")
    store.book(NewBooking("Mira Quillfeather", "+12125550188", "s", "S", "massage", "910006", 2_000_000_000, 60), outbox=lambda saved, prev: OutboxMessage("booked", "waiting from last time"))
    sender, done = FakeSender(), threading.Event()
    real = sender.send
    sender.send = lambda to, body, *, message_key: (real(to, body, message_key=message_key), done.set())[0]
    worker = AlertWorker(OutboxDispatcher(store, sender, OWNER))
    worker.start()
    assert done.wait(5) and [b for _t, b, _k in sender.sent] == ["waiting from last time"]
    worker.stop()
