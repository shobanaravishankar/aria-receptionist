"""The simulated-lifecycle voice tools, end to end over a temporary store: find, latest completed, book, cancel, reschedule, the endpoint that serves them.

Every assertion about 'the calendar' looks at the shared plan the page draws, because that is what the voice and the page both read. Synthetic data only; no network, no
Booksy, no Retell.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from aria_booking.appointments.launch import build_lifecycle_demo
from aria_booking.appointments.lifecycle import open_lifecycle
from aria_booking.appointments.models import CANCELLED, COMPLETED, CONFIRMED, NewBooking, to_epoch
from aria_booking.appointments.store import StoreError
from aria_booking.appointments.tools import LIFECYCLE_ROUTES
from aria_booking.demo import SPA_TIMEZONE
from aria_booking.demo.page import render_page

TZ = ZoneInfo(SPA_TIMEZONE)
TODAY = date(2026, 10, 13)  # a Tuesday
TOMORROW = TODAY + timedelta(days=1)
WILLA_PHONE, WILLA = "(212) 555-0142", "Willa Hartwell"
ANSEL = "Ansel Hartwell"
CEDRIC_PHONE = "212-555-0177"
SWEDISH_60 = "massage-swedish-massage-60"
KEY = "test-key-not-a-real-secret"


class Env:
    def __init__(self, tmp_path, *, hour=10, seed=True, name="env.sqlite3", **tool_kwargs):
        self.now = datetime(TODAY.year, TODAY.month, TODAY.day, hour, 0, tzinfo=TZ)
        self.lc = open_lifecycle(tmp_path / name, today=TODAY, real_now=lambda: self.now, seed=seed)
        self.log: list[str] = []
        self.endpoint, self.driver = build_lifecycle_demo(KEY, self.lc, log=self.log.append, **tool_kwargs)
        self.tools = self.endpoint._tools

    def call(self, route: str, args: dict, call_id: str = "call-1") -> dict:
        return getattr(self.tools, route)(call_id, args)

    # ---- the one booking path a caller takes: read back, then confirm
    def book_args(self, hour=16, *, day=TOMORROW, staff="Sienna", name="Mira Quillfeather", phone="+1 212 555 0188", service=SWEDISH_60) -> dict:
        args = {"service_id": service, "date": day.isoformat(), "time": f"{hour:02d}:00", "client_name": name, "phone": phone}
        if staff:
            args["staff"] = staff
        return args

    def book(self, hour=16, call_id="call-1", **kw) -> dict:
        args = self.book_args(hour, **kw)
        first = self.call("book_appointment", args, call_id)
        assert first["status"] == "confirmation_required", first
        return self.call("book_appointment", {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]}, call_id)

    def upcoming(self, phone=WILLA_PHONE, name=WILLA, **extra) -> list[dict]:
        return self.call("find_appointments", {"phone": phone, "name": name, **extra})["appointments"]

    def shown(self, day=TOMORROW) -> list[tuple]:
        return sorted((v.reference, v.start, p.staff.name) for p in self.lc.dataset.day_plan(day).plans for v in p.visits if v.reference)

    def confirm(self, route: str, args: dict, call_id="call-1") -> dict:
        first = self.call(route, args, call_id)
        assert first["status"] == "confirmation_required", first
        return self.call(route, {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]}, call_id)

    def counts(self) -> dict:
        return self.lc.store.counts()


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def slot_text(env, reference: str) -> str:
    """How the voice says an appointment's time: from the stored start, in the spa's zone."""
    from aria_booking.voice.speech import spoken_slot

    return spoken_slot(env.lc.store.get(reference).start.astimezone(TZ))


def stamped(body: bytes, key=KEY, ts=1_790_000_000_000) -> str:
    return f"v={ts},d=" + hmac.new(key.encode(), body + str(ts).encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- FIND: upcoming appointments


def test_the_number_must_be_complete_and_is_never_echoed(env):
    assert env.call("find_appointments", {})["status"] == "needs_clarification"
    for bad in ("555-0142", "21255501", "call me", "+1 212"):
        out = env.call("find_appointments", {"phone": bad})
        assert out["status"] == "invalid_phone" and "555" not in out["speak"] and "0142" not in out["speak"]


def test_a_household_number_needs_a_name_and_reveals_nothing_until_it_has_one(env):
    out = env.call("find_appointments", {"phone": WILLA_PHONE})
    assert out["status"] == "needs_clarification" and out["reason"] == "name_needed" and "appointments" not in out
    text = json.dumps(out)
    assert not any(w in text for w in ("Willa", "Ansel", "Facial", "Waxing", "waxing", "Tuesday", "Friday", "Thursday"))
    wrong = env.call("find_appointments", {"phone": WILLA_PHONE, "name": "Someone Else"})
    assert wrong["status"] == "unable_to_confirm" and "Facial" not in wrong["speak"]


def test_the_asked_service_is_answered_first_then_the_rest_in_order(env):
    out = env.call("find_appointments", {"phone": WILLA_PHONE, "name": WILLA, "service": "my waxing appointment"})
    assert out["status"] == "found" and out["ok"] is True and out["count"] == 3
    speak = out["speak"]
    assert speak.startswith("Your Full Leg waxing is on Tuesday, October 20 at 2 PM with Rowan.")
    assert speak.index("Head Scalp Massage") < speak.index("Swedish Massage") and "Friday, October 16 at 11 AM with Sienna" in speak and "Sunday, October 25" in speak
    assert "Facial" not in speak, "another client's appointment is never mentioned"
    starts = [a["start"] for a in out["appointments"]]
    assert starts == sorted(starts) and [a["category"] for a in out["appointments"]] == ["head-spa", "waxing", "massage"]
    assert all({"reference", "version", "technician", "service"} <= set(a) for a in out["appointments"])


def test_all_upcoming_are_listed_chronologically_with_local_times_and_technicians(env):
    out = env.call("find_appointments", {"phone": WILLA_PHONE, "name": WILLA})
    assert out["speak"].startswith("You have 3 upcoming appointments: Head Scalp Massage on Friday, October 16 at 11 AM with Sienna, Full Leg waxing on Tuesday, October 20")
    other = env.call("find_appointments", {"phone": WILLA_PHONE, "name": ANSEL})
    assert other["count"] == 1 and "Express Facial" in other["speak"] and "Head Scalp" not in other["speak"]


def test_a_service_the_client_has_not_booked_says_so_and_still_lists_what_they_have(env):
    out = env.call("find_appointments", {"phone": WILLA_PHONE, "name": WILLA, "service": "facial"})
    assert out["speak"].startswith("I don't see an upcoming facial appointment. You do have Head Scalp Massage")


def test_no_match_no_upcoming_and_a_name_that_matches_nobody_are_explicit(env):
    assert env.call("find_appointments", {"phone": "212-555-0199"})["status"] == "not_found"
    none = env.call("find_appointments", {"phone": CEDRIC_PHONE})
    assert none["status"] == "no_upcoming" and none["appointments"] == [] and none["ok"] is False


def test_more_than_three_are_summarised_with_an_offer_for_the_rest(env):
    for hour in (13, 15, 17):
        assert env.book(hour, name=WILLA, phone=WILLA_PHONE, staff="Sienna", call_id=f"c{hour}")["status"] == "booked_verified"
    out = env.call("find_appointments", {"phone": WILLA_PHONE, "name": WILLA})
    assert out["count"] == 6 and ", and 3 more" in out["speak"] and "I can go through the rest" in out["speak"] and len(out["appointments"]) == 6


def test_looking_up_never_changes_anything_or_queues_a_message(env):
    before = (env.counts(), env.lc.store.get(env.upcoming()[0]["reference"]))
    for _ in range(3):
        env.call("find_appointments", {"phone": WILLA_PHONE, "name": WILLA, "service": "waxing"})
        env.call("last_completed_visit", {"service_category": "lash", "phone": WILLA_PHONE, "name": WILLA})
    assert (env.counts(), env.lc.store.get(env.upcoming()[0]["reference"])) == before and env.lc.store.outbox_rows() == []


def test_a_caller_cannot_try_number_after_number(env):
    for number in ("212-555-0150", "212-555-0151", "212-555-0152"):
        assert env.call("find_appointments", {"phone": number})["status"] == "not_found"
    assert env.call("find_appointments", {"phone": "212-555-0153"})["status"] == "refused"
    assert env.call("find_appointments", {"phone": "212-555-0152"})["status"] == "not_found", "a number already tried may be repeated"
    assert env.call("find_appointments", {"phone": CEDRIC_PHONE}, call_id="another-call")["status"] == "no_upcoming"


def test_the_phone_number_is_never_in_a_reply_or_the_operator_log(env):
    out = env.call("find_appointments", {"phone": WILLA_PHONE, "name": WILLA})
    env.endpoint.handle("POST", "/tools/find_appointments", {"x-retell-signature": stamped(b"{}")}, b"{}")
    blob = json.dumps(out) + " ".join(env.log)
    assert "2125550142" not in blob.replace("-", "").replace(" ", "").replace("(", "").replace(")", "")


# ---------------------------------------------------------------- HISTORY: the latest completed visit


def test_the_last_completed_visit_comes_from_the_store_by_category(env):
    lash = env.call("last_completed_visit", {"service_category": "lashes", "phone": WILLA_PHONE, "name": WILLA})
    assert lash["status"] == "found" and lash["service"] == "Eyelash Lifting" and lash["date"] == "2026-09-22", "the later CANCELLED lash is not a visit"
    assert lash["speak"] == "Your last completed lash appointment was Eyelash Lifting on Tuesday, September 22."
    massage = env.call("last_completed_visit", {"service_category": "massage", "phone": WILLA_PHONE, "name": WILLA, "include_technician": True})
    assert massage["date"] == "2026-09-29" and massage["technician"] == "Marisol" and "with Marisol" in massage["speak"]
    facial = env.call("last_completed_visit", {"service_category": "facial", "phone": WILLA_PHONE, "name": WILLA})
    assert facial["date"] == "2026-09-01", "the newer no-show is not the last completed facial"


def test_future_and_other_clients_visits_are_never_the_answer(env):
    assert env.call("last_completed_visit", {"service_category": "waxing", "phone": WILLA_PHONE, "name": WILLA}, "h1")["status"] == "no_completed_visit"
    assert env.call("last_completed_visit", {"service_category": "massage", "phone": WILLA_PHONE, "name": ANSEL}, "h2")["date"] == "2026-10-03"
    assert env.call("last_completed_visit", {"service_category": "facial", "phone": WILLA_PHONE}, "h3")["status"] == "needs_clarification"
    assert env.call("last_completed_visit", {"service_category": "facial", "phone": CEDRIC_PHONE}, "h4")["status"] == "found"
    assert env.call("last_completed_visit", {"service_category": "head spa", "phone": CEDRIC_PHONE}, "h4")["status"] == "no_completed_visit"


def test_an_unrecorded_past_appointment_is_unknown_and_keeps_blocking_an_older_answer_even_after_a_restart(tmp_path):
    e = Env(tmp_path, seed=True)
    svc = e.lc.dataset.registry.get(SWEDISH_60)
    row = e.lc.store.book(NewBooking(WILLA, "+12125550142", svc.service_id, svc.booksy_name, "massage", "910006", to_epoch(e.now - timedelta(days=1)), 60, 0, 15, origin="test")).appointment
    blocked = e.call("last_completed_visit", {"service_category": "massage", "phone": WILLA_PHONE, "name": WILLA})
    assert blocked["status"] == "unable_to_confirm" and blocked["reason"] == "status_uncertain"
    later = datetime(2026, 10, 14, 10, tzinfo=TZ)
    reopened = open_lifecycle(tmp_path / "env.sqlite3", today=later.date(), real_now=lambda: later)  # the demo is opened again the next day
    assert reopened.store.get(row.reference).status == CONFIRMED and reopened.store.get(row.reference).version == 1, "nobody observed it, so it is not invented as completed"
    again = Env.__new__(Env)
    again.now, again.lc = later, reopened
    again.log = []
    again.endpoint, again.driver = build_lifecycle_demo(KEY, reopened, log=again.log.append)
    again.tools = again.endpoint._tools
    still = again.call("last_completed_visit", {"service_category": "massage", "phone": WILLA_PHONE, "name": WILLA})
    assert still["status"] == "unable_to_confirm" and still["reason"] == "status_uncertain"
    assert [e["kind"] for e in reopened.store.events(row.reference)] == ["booked"] and reopened.store.outbox_rows() == []


# ---------------------------------------------------------------- BOOK


def test_a_booking_needs_a_read_back_and_a_clear_yes_and_the_read_back_saves_nothing(env):
    before = env.counts()
    args = env.book_args()
    first = env.call("book_appointment", args)
    assert first["status"] == "confirmation_required" and first["ok"] is False and first["technician"] == "Sienna"
    assert first["speak"] == "To confirm: Swedish Massage, 1 hour, with Sienna, on Wednesday, October 14 at 4 PM, for Mira Quillfeather. The listed price is $" + str(first["price_usd"]) + ". Shall I book it?"
    assert env.counts() == before and env.shown() == [] and env.lc.store.outbox_rows() == []
    unconfirmed = env.call("book_appointment", {**args, "confirmed": "yes", "confirmation_token": first["confirmation_token"]})
    assert unconfirmed["status"] == "confirmation_required" and env.counts() == before, "only the boolean true confirms"
    assert env.call("book_appointment", {**args, "confirmed": True})["status"] == "confirmation_required", "a confirmation without its token is a new read-back"


def test_a_confirmed_booking_is_saved_verified_visible_everywhere_and_queues_exactly_one_owner_alert(env):
    out = env.book()
    assert out["status"] == "booked_verified" and out["ok"] is True and "I checked the calendar and it's there" in out["speak"]
    ref = out["appointment"]["reference"]
    assert f"Your reference is {' '.join(ref)}." in out["speak"] and len(ref) == 6
    assert [(r, s, who) for r, s, who in env.shown()] == [(ref, 16 * 60, "Sienna")]
    page = render_page(env.lc.dataset, TOMORROW)
    assert f'data-appointment-id="{ref}"' in page and "Mira Q." in page and "Quillfeather" not in page
    assert env.call("check_slot", {"service_id": SWEDISH_60, "date": TOMORROW.isoformat(), "time": "16:00", "staff": "Sienna"})["status"] != "available"
    found = env.upcoming("+1 212 555 0188", "Mira Quillfeather")
    assert [a["reference"] for a in found] == [ref]
    rows = env.lc.store.outbox_rows()
    assert len(rows) == 1 and rows[0]["kind"] == "booked" and rows[0]["status"] == "pending"
    body = rows[0]["body"]
    assert body.startswith("[Serenity prototype alert] Booked: Mira Quillfeather, Swedish Massage, 1 hour with Sienna, Wed Oct 14 4:00PM-5:00PM") and ref in body
    assert "0188" not in body and "212" not in body, "no phone number in an alert"


def test_a_repeated_or_retried_confirmation_does_not_book_twice(env):
    args = env.book_args()
    first = env.call("book_appointment", args)
    confirm = {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]}
    one = env.call("book_appointment", confirm)
    two = env.call("book_appointment", confirm)
    assert one["status"] == "booked_verified" and two["status"] == "already_booked" and two["ok"] is True
    assert one["appointment"]["reference"] == two["appointment"]["reference"] and "I won't book it twice" in two["speak"]
    assert env.counts()["appointments"]["confirmed"] == 4 + 1 and len(env.lc.store.outbox_rows()) == 1


def test_the_same_confirmed_request_on_many_threads_books_once(env):
    args = env.book_args()
    first = env.call("book_appointment", args)
    confirm = {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]}
    results, gate = [], threading.Barrier(5)

    def go():
        gate.wait()
        results.append(env.call("book_appointment", confirm)["status"])

    threads = [threading.Thread(target=go) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("booked_verified") + results.count("already_booked") + results.count("busy") == 5 and results.count("booked_verified") == 1
    assert len(env.lc.store.outbox_rows()) == 1 and len(env.shown()) == 1


def test_changed_details_a_foreign_token_and_an_expired_token_each_get_a_fresh_read_back(env):
    args = env.book_args()

    def fresh() -> str:  # each probe gets its own read-back: a new read-back replaces the call's earlier one
        return env.call("book_appointment", args)["confirmation_token"]

    foreign = env.call("book_appointment", {**args, "confirmed": True, "confirmation_token": fresh()}, call_id="another-call")
    assert foreign["status"] == "confirmation_required" and env.shown() == [], "a token belongs to the call that was read to"
    other_time = env.call("book_appointment", {**env.book_args(17), "confirmed": True, "confirmation_token": fresh()})
    assert other_time["status"] == "confirmation_required" and "5 PM" in other_time["speak"] and env.shown() == []
    other_name = env.call("book_appointment", {**args, "client_name": "Someone Else", "confirmed": True, "confirmation_token": fresh()})
    assert other_name["status"] == "confirmation_required" and "Someone Else" in other_name["speak"] and env.shown() == []
    other_phone = env.call("book_appointment", {**args, "phone": "+1 212 555 0100", "confirmed": True, "confirmation_token": fresh()})
    assert other_phone["status"] == "confirmation_required" and env.shown() == []
    at_two = env.book_args(14)
    token = env.call("book_appointment", at_two)["confirmation_token"]  # read back with Sienna at 2 PM
    other_tech = env.call("book_appointment", {**at_two, "staff": "Tamsin", "confirmed": True, "confirmation_token": token})
    assert other_tech["status"] == "confirmation_required" and "with Tamsin" in other_tech["speak"] and env.shown() == [], "naming someone else is a new read-back"
    stale = fresh()
    env.now += timedelta(minutes=10)  # past the confirmation window
    expired = env.call("book_appointment", {**args, "confirmed": True, "confirmation_token": stale})
    assert expired["status"] == "confirmation_required" and env.shown() == []


def test_a_change_that_lands_between_the_tools_check_and_the_save_is_caught_by_the_store(env, monkeypatch):
    cancel = cancel_args(env, "massage")
    first = env.call("cancel_appointment", cancel)
    real = env.lc.store.cancel

    def moved_first(reference, **kw):  # someone moves it after the tool's own check but before its save
        env.lc.store.reschedule(reference, start_utc=env.lc.store.get(reference).start_utc - 900)
        return real(reference, **kw)

    monkeypatch.setattr(env.lc.store, "cancel", moved_first)
    out = env.call("cancel_appointment", {**cancel, "confirmed": True, "confirmation_token": first["confirmation_token"]})
    assert out["status"] == "confirmation_required" and out["speak"].startswith("That appointment has changed")
    assert env.lc.store.get(cancel["reference"]).status == CONFIRMED
    monkeypatch.undo()

    move = move_args(env, "head-spa", hour=17)
    read_back = env.call("reschedule_appointment", move)
    real_move = env.lc.store.reschedule

    def nudged_first(reference, **kw):
        real_move(reference, start_utc=env.lc.store.get(reference).start_utc + 900)
        return real_move(reference, **kw)

    monkeypatch.setattr(env.lc.store, "reschedule", nudged_first)
    moved = env.call("reschedule_appointment", {**move, "confirmed": True, "confirmation_token": read_back["confirmation_token"]})
    assert moved["status"] == "stale" and moved["ok"] is False and env.lc.store.get(move["reference"]).version == 2


def test_a_slot_taken_between_the_read_back_and_the_yes_is_refused_with_real_alternatives_and_the_other_booking_survives(env):
    args = env.book_args()
    first = env.call("book_appointment", args)
    rival = env.book(16, call_id="rival", name="Quick Caller", phone="+1 212 555 0101")
    assert rival["status"] == "booked_verified"
    late = env.call("book_appointment", {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]})
    assert late["status"] != "booked_verified" and late["ok"] is False and late["reason"] == "changed_before_save"
    assert late["speak"].startswith("I'm sorry, nothing was booked. Sienna is already booked at 4 PM on Wednesday, October 14.")
    assert len(env.shown()) == 1 and env.counts()["appointments"]["confirmed"] == 4 + 1


def test_two_callers_confirming_the_same_slot_at_the_same_moment_one_wins(env):
    tokens = {}
    for who in ("a", "b"):
        args = env.book_args(name=f"Caller {who.upper()}", phone=f"+1 212 555 010{ord(who) - 96}")
        tokens[who] = (args, env.call("book_appointment", args, call_id=f"call-{who}")["confirmation_token"])
    results, gate = {}, threading.Barrier(2)

    def go(who):
        gate.wait()
        args, token = tokens[who]
        results[who] = env.call("book_appointment", {**args, "confirmed": True, "confirmation_token": token}, call_id=f"call-{who}")["status"]

    threads = [threading.Thread(target=go, args=(w,)) for w in tokens]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results.values()).count("booked_verified") == 1 and len(env.shown()) == 1
    assert len(env.lc.store.outbox_rows()) == 1


def test_missing_or_odd_details_are_asked_for_not_guessed(env):
    args = env.book_args()
    assert env.call("book_appointment", {k: v for k, v in args.items() if k != "client_name"})["reason"] == "name_required"
    assert env.call("book_appointment", {**args, "client_name": "x" * 200})["reason"] == "name_required"
    assert env.call("book_appointment", {k: v for k, v in args.items() if k != "phone"})["reason"] == "phone_required"
    assert env.call("book_appointment", {**args, "phone": "555-0188"})["status"] == "invalid_phone"
    assert env.call("book_appointment", {k: v for k, v in args.items() if k != "time"})["status"] == "needs_clarification"
    assert env.call("book_appointment", {**args, "service_id": "no-such-service"})["status"] == "invalid_service"
    assert env.call("book_appointment", {**args, "time": "16:10"})["reason"] == "off_grid"
    assert env.counts()["appointments"]["confirmed"] == 4


def test_a_time_that_is_not_open_is_explained_with_alternatives_and_nothing_is_read_back(env):
    out = env.call("book_appointment", env.book_args(11))  # Sienna's scripted 11:30 visit
    assert out["status"] in ("alternatives", "no_alternatives") and "confirmation_token" not in out and out["ok"] is False
    named = env.call("book_appointment", env.book_args(16, staff="Marisol", service="facial-signature-facial-60"))
    assert named["status"] == "staff_unavailable" and "doesn't do" in named["speak"]


def test_the_same_client_cannot_be_booked_twice_at_overlapping_times(env):
    assert env.book(14, name=WILLA, phone=WILLA_PHONE)["status"] == "booked_verified"
    clash = env.book(14, staff="Tamsin", name=WILLA, phone=WILLA_PHONE, call_id="call-2")
    assert clash["status"] == "unavailable" and clash["reason"] == "client_already_booked" and "I haven't booked another" in clash["speak"]


def test_a_store_outage_is_said_plainly_and_an_unexpected_failure_is_never_called_a_booking(env, monkeypatch):
    args = env.book_args()
    first = env.call("book_appointment", args)
    confirm = {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]}
    monkeypatch.setattr(env.lc.store, "book", lambda *a, **k: (_ for _ in ()).throw(StoreError("locked")))
    out = env.call("book_appointment", confirm)
    assert out["status"] == "system_unavailable" and out["ok"] is False
    monkeypatch.setattr(env.lc.store, "book", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = env.call("book_appointment", confirm)
    assert out["status"] == "needs_review" and out["ok"] is False and "can't confirm" in out["speak"]
    assert env.shown() == []


def test_success_is_claimed_only_if_the_store_and_the_calendar_both_show_it(env, monkeypatch):
    args = env.book_args()
    first = env.call("book_appointment", args)
    confirm = {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]}
    real = env.lc.dataset.day_plan

    def hide(day, **kw):  # the calendar does not show the saved appointment
        plan = real(day, **kw)
        from dataclasses import replace

        return type(plan)(plan.day, tuple(replace(p, visits=tuple(v for v in p.visits if not v.reference)) for p in plan.plans))

    monkeypatch.setattr(env.lc.dataset, "day_plan", hide)
    out = env.call("book_appointment", confirm)
    assert out["status"] == "needs_review" and out["ok"] is False and out["reference"]


def test_one_call_can_only_change_so_much(env):
    for hour in (13, 15, 17):
        assert env.book(hour, name="Guest Caller", phone="+1 212 555 0113")["status"] == "booked_verified"
    refused = env.call("book_appointment", env.book_args(13, staff="Tamsin", name="Guest Caller", phone="+1 212 555 0113"))
    assert refused["status"] == "refused" and refused["reason"] == "one_change_limit"


# ---------------------------------------------------------------- CANCEL


def cancel_args(env, category="waxing", **extra) -> dict:
    row = next(a for a in env.upcoming() if a["category"] == category)
    return {"reference": row["reference"], "phone": WILLA_PHONE, "name": WILLA, **extra}


def test_a_cancellation_is_read_back_first_then_confirmed_and_frees_the_slot_everywhere(env):
    args = cancel_args(env)
    ref = args["reference"]
    first = env.call("cancel_appointment", args)
    assert first["status"] == "confirmation_required" and first["speak"].startswith("To confirm: cancel your Full Leg waxing appointment with Rowan on Tuesday, October 20 at 2 PM.")
    assert env.lc.store.get(ref).status == CONFIRMED and not any(w in first["speak"].lower() for w in ("fee", "charge", "policy", "penalty"))
    out = env.call("cancel_appointment", {**args, "confirmed": True, "confirmation_token": first["confirmation_token"], "reason": "Work got busy"})
    assert out["status"] == "cancelled" and out["ok"] is True and "that time is free again" in out["speak"]
    assert env.lc.store.get(ref).status == CANCELLED and env.lc.store.get(ref).cancel_reason == "Work got busy"
    assert all(a["reference"] != ref for a in env.upcoming()) and env.shown(date(2026, 10, 20)) == []
    rows = env.lc.store.outbox_rows()
    assert [r["kind"] for r in rows] == ["cancelled"] and rows[0]["body"].startswith("[Serenity prototype alert] Cancelled: Willa Hartwell, Full Leg") and "busy" not in rows[0]["body"]


def test_the_reason_is_optional_and_cancelling_does_not_require_another_slot(env):
    args = cancel_args(env, "massage")
    out = env.confirm("cancel_appointment", args)
    assert out["status"] == "cancelled" and env.lc.store.get(args["reference"]).cancel_reason is None


def test_a_repeated_cancellation_changes_nothing_else_and_sends_no_second_alert(env):
    args = cancel_args(env)
    first = env.call("cancel_appointment", args)
    confirm = {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]}
    assert env.call("cancel_appointment", confirm)["status"] == "cancelled"
    again = env.call("cancel_appointment", confirm)
    assert again["status"] == "already_cancelled" and again["ok"] is True
    assert env.call("cancel_appointment", args)["status"] == "already_cancelled"
    assert len(env.upcoming()) == 2 and len(env.lc.store.outbox_rows()) == 1


def test_a_wrong_number_a_wrong_reference_and_a_guess_are_all_just_not_found(env):
    args = cancel_args(env)
    wrong_number = env.call("cancel_appointment", {**args, "phone": "212-555-0177"})
    wrong_ref = env.call("cancel_appointment", {**args, "reference": "ZZZZZZ"})
    assert wrong_number["status"] == wrong_ref["status"] == "not_found" and wrong_number["speak"] == wrong_ref["speak"]
    assert env.call("cancel_appointment", {**args, "reference": "abc"})["status"] == "needs_clarification"
    for _ in range(3):
        env.call("cancel_appointment", {**args, "reference": "ZZZZZZ"})
    assert env.call("cancel_appointment", {**args, "reference": "YYYYYY"})["status"] == "refused"
    assert env.lc.store.get(args["reference"]).status == CONFIRMED


def test_the_reference_may_be_spoken_with_spaces_and_any_case(env):
    args = cancel_args(env)
    spoken = " ".join(args["reference"].lower())
    assert env.call("cancel_appointment", {**args, "reference": spoken})["status"] == "confirmation_required"


def test_on_a_shared_number_the_name_must_match_the_appointments_client(env):
    willa = cancel_args(env)
    ansel = env.call("find_appointments", {"phone": WILLA_PHONE, "name": ANSEL})["appointments"][0]["reference"]
    no_name = env.call("cancel_appointment", {"reference": ansel, "phone": WILLA_PHONE})
    assert no_name["status"] == "needs_clarification" and no_name["reason"] == "name_needed"
    assert env.call("cancel_appointment", {"reference": ansel, "phone": WILLA_PHONE, "name": WILLA})["status"] == "needs_clarification"
    assert env.call("cancel_appointment", {"reference": ansel, "phone": WILLA_PHONE, "name": ANSEL})["status"] == "confirmation_required"
    assert willa["reference"] != ansel and env.lc.store.get(ansel).status == CONFIRMED


def test_an_appointment_that_changed_after_the_read_back_is_read_back_again_not_cancelled(env):
    args = cancel_args(env, "massage")
    first = env.call("cancel_appointment", args)
    env.lc.store.reschedule(args["reference"], start_utc=env.lc.store.get(args["reference"]).start_utc + 3600)  # someone moved it meanwhile
    out = env.call("cancel_appointment", {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]})
    assert out["status"] == "confirmation_required" and out["speak"].startswith("That appointment has changed")
    assert env.lc.store.get(args["reference"]).status == CONFIRMED


def test_a_started_or_finished_appointment_cannot_be_cancelled(tmp_path):
    e = Env(tmp_path)
    svc = e.lc.dataset.registry.get(SWEDISH_60)
    soon = e.lc.store.book(NewBooking(WILLA, "+12125550142", svc.service_id, svc.booksy_name, "massage", "910006", to_epoch(e.now) + 1800, 60, 0, 15, origin="test")).appointment
    e.now += timedelta(hours=1)  # it has begun
    assert e.call("cancel_appointment", {"reference": soon.reference, "phone": WILLA_PHONE, "name": WILLA})["status"] == "not_cancellable"
    done = next(a for a in e.lc.store.for_client(1, (COMPLETED,)))
    assert e.call("cancel_appointment", {"reference": done.reference, "phone": WILLA_PHONE, "name": WILLA})["status"] == "not_cancellable"


# ---------------------------------------------------------------- RESCHEDULE


def move_args(env, category="massage", day=TOMORROW, hour=16, **extra) -> dict:
    row = next(a for a in env.upcoming() if a["category"] == category)
    return {"reference": row["reference"], "phone": WILLA_PHONE, "name": WILLA, "date": day.isoformat(), "time": f"{hour:02d}:00", **extra}


def test_a_move_is_read_back_confirmed_and_is_one_coherent_change(env):
    args = move_args(env)
    ref = args["reference"]
    old_start, old_text = env.lc.store.get(ref).start_utc, slot_text(env, ref)
    first = env.call("reschedule_appointment", args)
    assert first["status"] == "confirmation_required" and f"from {old_text} to Wednesday, October 14 at 4 PM" in first["speak"] and "Sienna" in first["speak"]
    assert env.lc.store.get(ref).start_utc == old_start, "the read-back moved nothing"
    out = env.call("reschedule_appointment", {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]})
    assert out["status"] == "rescheduled" and out["ok"] is True and out["appointment"]["reference"] == ref and "I checked the calendar" in out["speak"]
    moved = env.lc.store.get(ref)
    assert moved.start_utc == to_epoch(datetime(2026, 10, 14, 16, tzinfo=TZ)) and moved.version == 2 and moved.status == CONFIRMED
    assert [(r, s) for r, s, _ in env.shown()] == [(ref, 16 * 60)] and env.shown(date(2026, 10, 25)) == []
    rows = env.lc.store.outbox_rows()
    assert [r["kind"] for r in rows] == ["rescheduled"], "one alert for one move: not a cancellation plus a booking"
    assert rows[0]["body"].startswith("[Serenity prototype alert] Rescheduled: Willa Hartwell, Swedish Massage, 1 hour. Was Sun Oct 25 ")
    assert "; now Wed Oct 14 4:00PM-5:00PM with Sienna." in rows[0]["body"] and rows[0]["body"].endswith(f"Ref {ref}.")


def test_a_new_time_that_is_not_open_changes_nothing_and_offers_real_alternatives(env):
    args = move_args(env, hour=21)  # after closing: nobody can take it
    before = env.lc.store.get(args["reference"])
    out = env.call("reschedule_appointment", args)
    assert out["status"] in ("alternatives", "no_alternatives") and out["ok"] is False and f"stays on {slot_text(env, args['reference'])}" in out["speak"]
    assert env.lc.store.get(args["reference"]) == before and env.lc.store.outbox_rows() == []


def test_a_new_time_taken_after_the_read_back_leaves_the_original_exactly_as_it_was(env):
    args = move_args(env)
    before = env.lc.store.get(args["reference"])
    first = env.call("reschedule_appointment", args)
    assert env.book(16, call_id="rival", name="Quick Caller", phone="+1 212 555 0101")["status"] == "booked_verified"
    out = env.call("reschedule_appointment", {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]})
    assert out["ok"] is False and out["status"] in ("unavailable", "alternatives", "no_alternatives")
    assert env.lc.store.get(args["reference"]) == before and [r["kind"] for r in env.lc.store.outbox_rows()] == ["booked"]


def test_a_move_to_the_same_time_and_a_nudge_over_its_own_old_time(env):
    booked = env.book(16, name=WILLA, phone=WILLA_PHONE)
    ref = booked["appointment"]["reference"]
    args = {"reference": ref, "phone": WILLA_PHONE, "name": WILLA, "date": TOMORROW.isoformat(), "time": "16:00", "staff": "Sienna"}
    same = env.call("reschedule_appointment", args)
    assert same["status"] == "unchanged" and same["ok"] is True and "confirmation_token" not in same, "already there: nothing to confirm"
    assert env.lc.store.get(ref).version == 1 and [r["kind"] for r in env.lc.store.outbox_rows()] == ["booked"]
    nudge = env.confirm("reschedule_appointment", {**args, "time": "16:15"}, call_id="n")  # 16:15 overlaps its own old 16:00: not a conflict
    assert nudge["status"] == "rescheduled" and env.lc.store.get(ref).start_utc == to_epoch(datetime(2026, 10, 14, 16, 15, tzinfo=TZ))


def test_a_stale_move_and_a_named_technician_who_does_not_do_it(env):
    args = move_args(env)
    first = env.call("reschedule_appointment", args)
    env.lc.store.reschedule(args["reference"], start_utc=env.lc.store.get(args["reference"]).start_utc + 900)
    out = env.call("reschedule_appointment", {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]})
    assert out["status"] == "confirmation_required", "the appointment changed, so it is read back again"
    assert env.call("reschedule_appointment", {**move_args(env), "staff": "Priya"})["status"] == "staff_unavailable"
    assert env.call("reschedule_appointment", {**move_args(env), "staff": "Nobody"})["status"] == "staff_unavailable"


def test_a_move_uses_another_technician_only_when_the_same_one_cannot(env):
    args = move_args(env, "head-spa", day=TOMORROW, hour=17)  # head spa is Marisol's or Sienna's; Sienna is free at 5 PM
    first = env.call("reschedule_appointment", args)
    assert first["status"] == "confirmation_required" and "with Sienna" in first["speak"]


# ---------------------------------------------------------------- the endpoint: routes, signing and the absence of a Booksy route


def post(env, path: str, body: dict, *, sign=True) -> tuple[int, dict]:
    raw = json.dumps({"name": path.rsplit("/", 1)[1], "call": {"call_id": "c1"}, "args": body}).encode()
    headers = {"x-retell-signature": stamped(raw)} if sign else {}
    return env.endpoint.handle("POST", path, headers, raw)


def test_the_endpoint_serves_exactly_the_lifecycle_routes_and_has_no_booksy_route(env, monkeypatch):
    monkeypatch.setattr(env.endpoint, "_now", lambda: 1_790_000_000_000)
    assert set(LIFECYCLE_ROUTES.values()) == {
        "lookup_service", "check_slot", "find_alternatives", "find_appointments", "last_completed_visit", "book_appointment", "cancel_appointment", "reschedule_appointment",
    } and "book_slot" not in LIFECYCLE_ROUTES.values()
    status, body = post(env, "/tools/find_appointments", {"phone": CEDRIC_PHONE})
    assert status == 200 and body["status"] == "no_upcoming"
    assert post(env, "/tools/book_slot", {})[0] == 404
    assert post(env, "/tools/find_appointments", {}, sign=False)[0] == 401
    assert post(env, "/tools/book_appointment", {"service_id": SWEDISH_60})[1]["status"] == "needs_clarification"


def test_the_booksy_writer_is_refused_even_if_something_calls_it_directly(env):
    out = env.tools.book_slot("c1", {"option_id": "x", "confirmed": True})
    assert out["status"] == "refused" and out["reason"] == "no_booksy_writer" and env.counts()["appointments"]["confirmed"] == 4


def test_the_read_only_demo_builder_is_untouched_and_still_has_no_writer_route():
    from aria_booking.demo.launch import build_demo

    endpoint, _driver, _dataset = build_demo("k", today=TODAY)
    status, body = endpoint.handle("POST", "/tools/book_appointment", {}, b"{}")
    assert status == 404


def test_every_reply_carries_the_demo_source_label(env):
    assert env.call("find_appointments", {"phone": CEDRIC_PHONE})["source"] == "demo_calendar"
    assert env.call("book_appointment", env.book_args())["source"] == "demo_calendar"


# ---------------------------------------------------------------- what the lookup leaves out


def test_past_cancelled_and_other_clients_appointments_are_never_listed(tmp_path):
    e = Env(tmp_path)
    svc = e.lc.dataset.registry.get(SWEDISH_60)
    e.lc.store.book(NewBooking(WILLA, "+12125550142", svc.service_id, svc.booksy_name, "massage", "910006", to_epoch(e.now) - 7200, 60, 0, 15, origin="test"))  # in the past, never settled
    cancelled = e.lc.store.book(NewBooking(WILLA, "+12125550142", svc.service_id, svc.booksy_name, "massage", "910006", to_epoch(e.now) + 7 * 86400, 60, 0, 15, origin="test")).appointment
    e.lc.store.cancel(cancelled.reference)
    listed = e.upcoming()
    assert len(listed) == 3 and cancelled.reference not in {a["reference"] for a in listed}
    assert all(datetime.fromisoformat(a["start"]) > e.now for a in listed)


def test_opening_the_demo_settles_only_the_fictional_seed_fixtures_and_never_a_callers_booking_and_never_notifies(tmp_path):
    first = Env(tmp_path)
    svc = first.lc.dataset.registry.get(SWEDISH_60)
    caller = first.lc.store.book(NewBooking(WILLA, "+12125550142", svc.service_id, svc.booksy_name, "massage", "910006", to_epoch(first.now) + 7200, 60, 0, 15, origin="voice"), outbox=lambda s, p: None).appointment
    seeded = [r for r in first.lc.store.for_client(1, (CONFIRMED,)) if r.origin == "seed"]
    assert seeded, "the fixtures have upcoming rows"
    later_day = TODAY + timedelta(days=30)
    later = datetime(later_day.year, later_day.month, later_day.day, 10, tzinfo=TZ)
    reopened = open_lifecycle(tmp_path / "env.sqlite3", today=later_day, real_now=lambda: later)
    for row in seeded:
        assert reopened.store.get(row.reference).status == COMPLETED, "an upcoming fixture whose time has passed became a completed fixture visit"
        assert reopened.store.events(row.reference)[-1] == {"kind": "settled", "detail": {"start": row.start_utc, "as": "completed", "fixture": True}, "at": reopened.store.events(row.reference)[-1]["at"]}
    assert reopened.store.get(caller.reference).status == CONFIRMED, "a caller's own booking is never settled"
    assert reopened.store.outbox_rows() == [] and reopened.store.settle_past_seeds() == 0




# ---------------------------------------------------------------- LC1: an existing appointment's cleanup is time nobody can take


def test_the_minute_an_appointment_ends_is_not_offered_or_booked_while_its_cleanup_runs(env):
    ref = env.book(16)["appointment"]["reference"]  # Swedish 60 with Sienna 4-5 PM, 15 minutes of cleanup after
    inside = env.call("check_slot", {"service_id": SWEDISH_60, "date": TOMORROW.isoformat(), "time": "17:00", "staff": "Sienna"})
    assert inside["status"] != "available" and inside["unavailable_reason"] == "on_break"
    assert inside["speak"].startswith("Sienna needs until 5:15 PM to get ready after the 4 PM appointment, so 5 PM isn't available.") and inside["ready_at"] == "17:15"
    assert env.call("check_slot", {"service_id": SWEDISH_60, "date": TOMORROW.isoformat(), "time": "17:15", "staff": "Sienna"})["status"] == "available"
    refused = env.call("book_appointment", env.book_args(17, name="Second Person", phone="+1 212 555 0189"))
    assert refused["status"] != "confirmation_required" and "confirmation_token" not in refused and len(env.shown()) == 1, "never even read back"
    alternatives = env.call("find_alternatives", {"service_id": SWEDISH_60, "date": TOMORROW.isoformat(), "time": "17:00", "staff": "Sienna"})
    starts = [o["start"] for o in alternatives["options"]]
    assert starts and not any(s.startswith(f"{TOMORROW.isoformat()}T17:00") or s.startswith(f"{TOMORROW.isoformat()}T17:05") for s in starts), starts
    assert f"{TOMORROW.isoformat()}T17:15:00-04:00" in starts, "the first start after the cleanup is what is offered instead"
    assert env.lc.store.get(ref).status == CONFIRMED


def test_a_start_that_would_run_into_an_existing_appointment_or_its_cleanup_is_said_as_that(env):
    env.book(16)
    into = env.call("check_slot", {"service_id": SWEDISH_60, "date": TOMORROW.isoformat(), "time": "15:30", "staff": "Sienna"})
    assert into["unavailable_reason"] == "conflicts_next" and "would run into Sienna's next appointment at 4 PM" in into["speak"]
    no_time = env.call("check_slot", {"service_id": SWEDISH_60, "date": TOMORROW.isoformat(), "time": "15:00", "staff": "Sienna"})
    assert no_time["unavailable_reason"] == "conflicts_next" and "wouldn't be time for the 15 minutes of cleanup after it" in no_time["speak"]
    assert env.call("check_slot", {"service_id": SWEDISH_60, "date": TOMORROW.isoformat(), "time": "14:45", "staff": "Sienna"})["status"] == "available", "its own cleanup ends exactly as 4 PM begins"
    taken = env.call("check_slot", {"service_id": SWEDISH_60, "date": TOMORROW.isoformat(), "time": "16:00", "staff": "Sienna"})
    assert taken["unavailable_reason"] == "occupied" and taken["speak"].startswith("Sienna is already booked at 4 PM")


def test_a_move_into_another_appointments_cleanup_is_refused_and_one_into_its_own_old_cleanup_is_not(env):
    env.book(16)
    mine = env.book(18, name="Mover", phone="+1 212 555 0190", call_id="mover")["appointment"]["reference"]
    into_cleanup = env.call("reschedule_appointment", {"reference": mine, "phone": "+1 212 555 0190", "date": TOMORROW.isoformat(), "time": "17:00", "staff": "Sienna"})
    assert into_cleanup["status"] in ("alternatives", "no_alternatives") and into_cleanup["ok"] is False and "get ready" in into_cleanup["speak"]
    assert env.lc.store.get(mine).start_utc == to_epoch(datetime(2026, 10, 14, 18, tzinfo=TZ))
    later = env.confirm("reschedule_appointment", {"reference": mine, "phone": "+1 212 555 0190", "date": TOMORROW.isoformat(), "time": "18:15", "staff": "Sienna"}, call_id="mover")
    assert later["status"] == "rescheduled", "its own cleanup is never in its own way"


# ---------------------------------------------------------------- LC3: a repeated confirmed move is answered from what actually happened


def _moved(env):
    booked = env.book(16, name=WILLA, phone=WILLA_PHONE)
    args = {"reference": booked["appointment"]["reference"], "phone": WILLA_PHONE, "name": WILLA, "date": TOMORROW.isoformat(), "time": "18:00", "staff": "Sienna"}
    read_back = env.call("reschedule_appointment", args, "move")
    confirm = {**args, "confirmed": True, "confirmation_token": read_back["confirmation_token"]}
    assert env.call("reschedule_appointment", confirm, "move")["status"] == "rescheduled"
    return args["reference"], args, confirm


def test_the_same_confirmed_move_repeated_is_reported_as_already_done_with_one_event_and_one_alert(env):
    ref, args, confirm = _moved(env)
    for _ in range(3):
        again = env.call("reschedule_appointment", confirm, "move")
        assert again["status"] == "already_rescheduled" and again["ok"] is True and "confirmation_token" not in again
        assert again["speak"].startswith("That move was already made: your Swedish Massage, 1 hour appointment is on Wednesday, October 14 at 6 PM, with Sienna.")
    assert env.lc.store.get(ref).version == 2 and [e["kind"] for e in env.lc.store.events(ref)] == ["booked", "rescheduled"]
    assert [r["kind"] for r in env.lc.store.outbox_rows()] == ["booked", "rescheduled"]


def test_a_repeated_move_after_a_later_change_reports_the_current_state_and_approves_nothing_new(env):
    ref, args, confirm = _moved(env)
    env.lc.store.reschedule(ref, start_utc=to_epoch(datetime(2026, 10, 14, 19, tzinfo=TZ)))
    moved_again = env.call("reschedule_appointment", confirm, "move")
    assert moved_again["status"] == "stale" and moved_again["ok"] is False and moved_again["reason"] == "changed_after_the_move" and "it is now on Wednesday, October 14 at 7 PM" in moved_again["speak"]
    env.lc.store.cancel(ref)
    cancelled = env.call("reschedule_appointment", confirm, "move")
    assert cancelled["status"] == "stale" and "it is now cancelled" in cancelled["speak"]
    assert env.lc.store.get(ref).status == CANCELLED and len(env.lc.store.outbox_rows()) == 2, "repeating the move queued nothing"


def test_a_repeated_move_from_another_call_or_after_the_token_expired_never_re_approves_and_says_where_it_is(env):
    ref, args, confirm = _moved(env)
    other_call = env.call("reschedule_appointment", confirm, "someone-else")
    assert other_call["status"] == "unchanged" and "confirmation_token" not in other_call, "another call's token is nothing, and the appointment is already there"
    env.now += timedelta(minutes=10)
    expired = env.call("reschedule_appointment", confirm, "move")
    assert expired["status"] == "unchanged" and env.lc.store.get(ref).version == 2
    different = env.call("reschedule_appointment", {**args, "time": "18:30"}, "move")
    assert different["status"] == "confirmation_required", "a DIFFERENT move is a new read-back, never approved by the old token"


def test_a_repeated_booking_confirmation_after_the_appointment_changed_reports_that_and_books_nothing(env):
    args = env.book_args()
    first = env.call("book_appointment", args)
    confirm = {**args, "confirmed": True, "confirmation_token": first["confirmation_token"]}
    ref = env.call("book_appointment", confirm)["appointment"]["reference"]
    env.lc.store.cancel(ref)
    again = env.call("book_appointment", confirm)
    assert again["status"] == "stale" and again["reason"] == "changed_after_the_booking" and "it is now cancelled" in again["speak"] and again["ok"] is False
    assert len(env.shown()) == 0 and env.counts()["appointments"]["confirmed"] == 4
