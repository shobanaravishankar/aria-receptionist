"""The voice tools of the SIMULATED appointment lifecycle: find, book, cancel, reschedule and last-completed history, over the demo's own store.

This is a separate, opt-in mode of the demo. It adds to the read-only tools the reviewed demo already has (``lookup_service``, ``check_slot``, ``find_alternatives``,
which it inherits unchanged and which now see stored appointments through the shared calendar) and it has NO route to the Booksy driver: the Booksy writer
(``book_slot``) is neither inherited as a route nor reachable, and the booking service factory it is given raises if anything ever calls it.

Rules enforced here, not left to the prompt:
  * Nothing is saved, cancelled or moved without a read-back that the caller confirms. The read-back issues a confirmation token bound to this call and to exactly what
    was read; a confirmed call whose details differ gets a fresh read-back, never a silent change. The save re-checks everything INSIDE the store's write transaction.
  * A retried or repeated confirmed request is answered from the first result and changes nothing (idempotency key = call + token).
  * ``ok`` is true only for a result that was re-read from the store and the calendar after the save. An unknown outcome is ``needs_review`` and says so.
  * A phone number identifies a DEMO record; it is not production authentication. It is never spoken back, logged or returned. Looking up a number never changes anything.
  * Only ONE appointment is ever touched: cancellation and moves go by the appointment's own reference AND the client's complete number; a wrong pair is simply "not found".
  * No cancellation fee, penalty or policy is ever stated: the tools hold none, so none is spoken.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from ..catalog.bookable import staff_key
from ..history.classify import Classifier
from ..history.lookup import HistoryLookup
from ..history.models import (
    CANCELLED as H_CANCELLED, COMPLETED as H_COMPLETED, NO_SHOW as H_NO_SHOW, UNKNOWN as H_UNKNOWN, UPCOMING as H_UPCOMING, ClientHistory, ClientRef,
    HistoryUnavailable, Visit as HistoryVisit,
)
from ..history.phone import normalize_phone
from ..catalog.render import CATEGORY_TITLES
from ..showcase.catalog import SHOWCASE_CATALOG
from ..showcase.discovery import make_group_answer
from ..voice.speech import join_names, spoken_day, spoken_duration, spoken_price, spoken_slot, spoken_time
from ..voice.tools import VoiceTools
from .lifecycle import Lifecycle
from .models import CANCELLED, COMPLETED, CONFIRMED, NO_SHOW, NewBooking, Placement, Refused, StoredAppointment, name_key, to_epoch
from .notify import outbox_builder
from .store import REFERENCE_ALPHABET, REFERENCE_LENGTH, StoreError

LIFECYCLE_ROUTES = {
    "/tools/lookup_service": "lookup_service",
    "/tools/check_slot": "check_slot",
    "/tools/find_alternatives": "find_alternatives",
    "/tools/find_appointments": "find_appointments",
    "/tools/last_completed_visit": "last_completed_visit",
    "/tools/book_appointment": "book_appointment",
    "/tools/cancel_appointment": "cancel_appointment",
    "/tools/reschedule_appointment": "reschedule_appointment",
}
LIFECYCLE_OK = frozenset({"booked_verified", "already_booked", "cancelled", "already_cancelled", "rescheduled", "already_rescheduled", "unchanged", "found"})
MAX_NUMBERS_PER_CALL = 3  # a caller cannot try number after number to find who is a client
MAX_FAILED_MATCHES_PER_CALL = 5
MAX_CHANGES_PER_CALL = 3
NAME_MAX = 80
SHOWN_UPCOMING = 3
CATEGORY_WORDS = {"waxing": "waxing", "lash": "lash", "body-scrub": "body scrub", "couples": "couples"}  # added to a service name only when the name does not say it
GENERIC_CONTACT = "contact the salon directly"  # what the reviewed tools say; replaced by the configured name and number when there is one


def _reply(status: str, speak: str, **data: Any) -> dict:
    return {"status": status, "ok": status in LIFECYCLE_OK, "speak": speak, **data}


@dataclass
class Pending:
    kind: str  # book / cancel / reschedule
    call_id: str
    payload: dict
    expires: datetime


class StoreHistorySource:
    """The reviewed history design's source interface, over the demo store. Past 'confirmed' rows were settled when the demo opened (see lifecycle.settle)."""

    def __init__(self, store, tz, clock: Callable[[], datetime], staff_name: Callable[[str], str]):
        self._store, self._tz, self._clock, self._staff_name = store, tz, clock, staff_name

    def find_clients(self, normalized_phone: str) -> list[ClientRef]:
        try:
            return [ClientRef(str(c.client_id), c.name) for c in self._store.clients_with_phone(normalized_phone)]
        except StoreError:
            raise HistoryUnavailable() from None

    def read_history(self, client: ClientRef) -> ClientHistory:
        try:
            rows = self._store.for_client(int(client.ref))
        except StoreError:
            raise HistoryUnavailable() from None
        now = to_epoch(self._clock())
        status_of = {COMPLETED: H_COMPLETED, CANCELLED: H_CANCELLED, NO_SHOW: H_NO_SHOW}
        visits = []
        for row in rows:
            if row.status == CONFIRMED:
                status = H_UPCOMING if row.start_utc > now else H_UNKNOWN  # in the past, never marked done: unknown, which blocks an older answer
            else:
                status = status_of.get(row.status, H_UNKNOWN)
            visits.append(HistoryVisit(row.start.astimezone(self._tz), (row.service_name,), status, self._staff_name(row.staff_id)))
        return ClientHistory(tuple(visits), completion_known=True, complete=True, newest_first=False)


class LifecycleTools(VoiceTools):
    def __init__(self, lifecycle: Lifecycle, cfg, driver, clock: Callable[[], datetime], *, contact: Optional[str] = None,
                 on_committed: Optional[Callable[[], None]] = None, **kwargs: Any):
        def no_writer(*args: Any, **kw: Any):
            raise AssertionError("the simulated lifecycle has no Booksy writer")

        kwargs.setdefault("group_answer", make_group_answer(SHOWCASE_CATALOG, CATEGORY_TITLES))
        kwargs.setdefault("check_search_days", 1)
        kwargs.setdefault("read_cache_seconds", 0.0)  # a booking must be in the very next answer
        kwargs.setdefault("lock_timeout_seconds", 2.0)
        kwargs.setdefault("read_deadline_seconds", 14.0)
        kwargs.setdefault("request_budget_seconds", 16.0)
        super().__init__(
            cfg, driver, no_writer, clock, registry=lifecycle.dataset.registry, catalog=SHOWCASE_CATALOG, require_service_id=True,
            availability_only=False, booking_enabled=False, source_label="demo_calendar", providers=lifecycle.dataset.providers, **kwargs,
        )
        self._read_only = False  # this line can book: its own, simulated way (no Booksy route exists, see LIFECYCLE_ROUTES)
        self._lc = lifecycle
        self._contact = contact  # e.g. "contact Serenity Spa at 2 1 2, ...": None until the owner supplies a number
        self._on_committed = on_committed  # called AFTER a change has committed, to wake the alert worker; never before, never on a refusal
        self._classifier = Classifier(SHOWCASE_CATALOG)
        self._history = HistoryLookup(StoreHistorySource(lifecycle.store, self._tz, clock, self._staff_name), clock, classifier=self._classifier)
        self._outbox = outbox_builder(self._staff_name, self._tz)
        self._tokens: dict[str, Pending] = {}
        self._numbers: dict[str, set] = {}
        self._failed: dict[str, int] = {}
        self._changes: dict[str, int] = {}
        self._state_lock = threading.Lock()

    def _why_not_one(self, snapshot, start: datetime, service, staff: str):
        """The reviewed explanation, except when a STORED appointment is the cause. Its cleanup time is part of the time it occupies (the calendar draws it as a shaded
        block), so the reviewed text would call it a break or the technician 'off'. Here it is said as what it is: already taken, getting ready, or running into the next one."""
        reason, text, extra = super()._why_not_one(snapshot, start, service, staff)
        person = next((s for s in self._lc.dataset.roster if s.name == staff), None)
        if reason not in ("on_break", "conflicts_next", "occupied") or person is None:
            return reason, text, extra
        spec, moment = self._spec(service), to_epoch(start)
        try:
            rows = self._lc.store.occupied_between(
                moment - spec.buffer_before_minutes * 60, moment + (spec.duration_minutes + spec.buffer_after_minutes) * 60, staff_id=person.staff_id,
            )
        except StoreError:
            return reason, text, extra
        if not rows:
            return reason, text, extra
        if any(r.start_utc <= moment < r.end_utc for r in rows):
            return "occupied", f"{spoken_time(start)} on {spoken_day(start)} is already taken.", {}
        before = [r for r in rows if r.start_utc < moment]
        if before:
            last = max(before, key=lambda r: r.end_utc)
            ready = self._local(last) + timedelta(minutes=last.duration_minutes + last.buffer_after_minutes)
            return "on_break", (
                f"{staff} needs until {spoken_time(ready)} to get ready after the {spoken_time(self._local(last))} appointment, so {spoken_time(start)} isn't available."
            ), {"ready_at": ready.strftime("%H:%M")}
        following = min(rows, key=lambda r: r.start_utc)
        at, length = self._local(following), spoken_duration(service.duration_minutes)
        if following.start_utc < moment + spec.duration_minutes * 60:
            return "conflicts_next", f"The {length} service starting at {spoken_time(start)} would run into {staff}'s next appointment at {spoken_time(at)}.", {"next_appointment": at.strftime("%H:%M")}
        return "conflicts_next", (
            f"The {length} service would fit before {staff}'s {spoken_time(at)} appointment, but there wouldn't be time for the {spec.buffer_after_minutes} minutes of cleanup after it."
        ), {"next_appointment": at.strftime("%H:%M")}

    def book_slot(self, call_id: str, args: dict) -> dict:
        """The Booksy writer of the live line. It is not a route here and is refused outright if anything ever calls it."""
        return _reply("refused", self._contacting("Booking isn't available that way on this line."), reason="no_booksy_writer")

    # ---- small helpers ----------------------------------------------------------------------------------
    def _issue(self, call_id: str, start: datetime, service, staff: str) -> dict:
        """An availability answer, not an offer: nothing here can be booked by an id. A booking goes through ``book_appointment`` and its read-back."""
        return {
            "label": f"{spoken_slot(start)} with {staff}", "start": start.isoformat(), "service_id": service.service_id, "service": service.booksy_name,
            "duration_minutes": service.duration_minutes, "price_usd": self._price(service), "technician": staff,
        }

    def _staff_name(self, staff_id: str) -> str:
        return next((s.name for s in self._lc.dataset.roster if s.staff_id == staff_id), "a technician")

    def _local(self, row: StoredAppointment) -> datetime:
        return row.start.astimezone(self._tz)

    def _label(self, row: StoredAppointment) -> str:
        name, extra = row.service_name, CATEGORY_WORDS.get(row.category)
        return f"{name} {extra}" if extra and extra not in name.lower() else name

    def _slot(self, row: StoredAppointment) -> str:
        return spoken_slot(self._local(row))

    def _view(self, row: StoredAppointment) -> dict:
        return {
            "reference": row.reference, "service_id": row.service_id, "service": row.service_name, "category": row.category,
            "technician": self._staff_name(row.staff_id), "start": self._local(row).isoformat(), "duration_minutes": row.duration_minutes, "version": row.version,
        }

    @staticmethod
    def _spoken_reference(reference: str) -> str:
        return " ".join(reference)

    @staticmethod
    def _described(name: str, minutes: int) -> str:
        """The service and its length, saying the length once: 'Swedish Massage, 1 hour' already does, 'Full Leg' does not."""
        length = spoken_duration(minutes)
        return name if length.lower() in name.lower() else f"{name}, {length}"

    def _branded(self, reply: dict) -> dict:
        """With a configured number, every 'contact the salon directly' (also in the reviewed tools' own answers) names the salon and gives the number."""
        if self._contact and isinstance(reply.get("speak"), str):
            reply["speak"] = reply["speak"].replace(GENERIC_CONTACT, self._contact)
        return reply

    def _labelled(self, call_id: str, reply: dict) -> dict:
        return self._branded(super()._labelled(call_id, reply))

    def lookup_service(self, call_id: str, args: dict) -> dict:
        return self._branded(super().lookup_service(call_id, args))

    def _contacting(self, text: str) -> str:
        return f"{text} Please {self._contact or GENERIC_CONTACT}."

    def _token_key(self, kind: str, call_id: str, token: str) -> str:
        return hashlib.sha256(f"{kind}|{call_id}|{token}".encode("utf-8")).hexdigest()[:32]

    def _new_pending(self, kind: str, call_id: str, payload: dict) -> str:
        token = secrets.token_urlsafe(6)
        with self._state_lock:
            now = self._now()
            self._tokens = {k: p for k, p in self._tokens.items() if p.expires > now}
            if len(self._tokens) >= 256:
                self._tokens.pop(min(self._tokens, key=lambda k: self._tokens[k].expires))
            self._tokens = {k: p for k, p in self._tokens.items() if not (p.call_id == call_id and p.kind == kind)}  # a new read-back replaces this call's earlier one
            self._tokens[token] = Pending(kind, call_id, payload, now + self._confirm_ttl)
        return token

    def _take_pending(self, kind: str, call_id: str, token: Any) -> Optional[Pending]:
        with self._state_lock:
            pending = self._tokens.get(token) if isinstance(token, str) else None
            if pending is None or pending.kind != kind or pending.call_id != call_id or pending.expires <= self._now():
                return None
            return pending

    def _committed(self) -> None:
        if self._on_committed is not None:
            try:
                self._on_committed()
            except Exception:  # waking the worker must never turn a saved change into an error
                pass

    def _changed_count(self, call_id: str) -> int:
        with self._state_lock:
            return self._changes.get(call_id, 0)

    def _count_change(self, call_id: str) -> None:
        with self._state_lock:
            if len(self._changes) >= 512:
                self._changes.clear()
            self._changes[call_id] = self._changes.get(call_id, 0) + 1

    # ---- who is asking about: the complete phone number (a DEMO record match, not authentication) ----------
    def _phone(self, call_id: str, args: dict) -> tuple[Optional[str], Optional[dict]]:
        raw = args.get("phone")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None, _reply("needs_clarification", "Could you give me the complete phone number on your account, including the area code?", reason="phone_required")
        phone = normalize_phone(raw)
        if phone is None:
            return None, _reply(
                "invalid_phone", "That doesn't sound like a complete phone number. Could you give me the whole number, including the area code?", reason="phone_incomplete",
            )
        with self._state_lock:
            if len(self._numbers) >= 512:
                self._numbers.clear()
            tried = self._numbers.setdefault(call_id, set())
            if phone not in tried and len(tried) >= MAX_NUMBERS_PER_CALL:
                return None, _reply("refused", self._contacting("I can't look up any more numbers on this call."), reason="too_many_numbers")
            tried.add(phone)
        return phone, None

    def _client(self, call_id: str, args: dict):
        phone, problem = self._phone(call_id, args)
        if problem:
            return None, None, problem
        try:
            found = self._lc.store.clients_with_phone(phone)
        except StoreError:
            return None, None, self._unavailable()
        choice = HistoryLookup._choose([ClientRef(str(c.client_id), c.name) for c in found], args.get("name") if isinstance(args.get("name"), str) else None)
        if isinstance(choice, dict):
            return None, None, choice
        return next(c for c in found if str(c.client_id) == choice.ref), phone, None

    def _unavailable(self) -> dict:
        return _reply("system_unavailable", self._contacting("I can't reach the appointment records right now, so I can't confirm that."), reason="store_unavailable")

    @staticmethod
    def _reference(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        cleaned = re.sub(r"[\s\-]", "", value).upper()
        return cleaned if len(cleaned) == REFERENCE_LENGTH and set(cleaned) <= set(REFERENCE_ALPHABET) else None

    def _owned(self, call_id: str, args: dict) -> tuple[Optional[StoredAppointment], Optional[dict]]:
        """The ONE appointment this reference names, if the complete number given is the client's. A wrong pair is just 'not found': it says nothing about why."""
        phone, problem = self._phone(call_id, args)
        if problem:
            return None, problem
        reference = self._reference(args.get("reference"))
        if reference is None:
            return None, _reply("needs_clarification", "I need the appointment's reference. Let me look up your upcoming appointments first.", reason="reference_required")
        with self._state_lock:
            if self._failed.get(call_id, 0) >= MAX_FAILED_MATCHES_PER_CALL:
                return None, _reply("refused", self._contacting("I can't look up any more appointments on this call."), reason="too_many_attempts")
        try:
            row = self._lc.store.get(reference)
            client = self._lc.store.client(row.client_id) if row is not None else None
        except StoreError:
            return None, self._unavailable()
        if row is None or client is None or client.phone != phone:
            with self._state_lock:
                self._failed[call_id] = self._failed.get(call_id, 0) + 1
            return None, _reply("not_found", self._contacting("I can't find that appointment for that number."), reason="no_match")
        try:
            sharing = self._lc.store.clients_with_phone(phone)
        except StoreError:
            return None, self._unavailable()
        if len(sharing) > 1 and name_key(args.get("name") if isinstance(args.get("name"), str) else "") != name_key(client.name):
            return None, _reply("needs_clarification", "That number is on more than one record. Could you tell me the full name on the booking?", reason="name_needed")
        return row, None

    # ---- FIND: upcoming appointments -------------------------------------------------------------------------
    def find_appointments(self, call_id: str, args: dict) -> dict:
        return self._labelled(call_id, self._with_lock(call_id, lambda: self._find(call_id, args or {})))

    def _find(self, call_id: str, args: dict) -> dict:
        client, _phone, problem = self._client(call_id, args)
        if problem:
            return problem
        try:
            rows = self._lc.store.for_client(client.client_id, (CONFIRMED,))
        except StoreError:
            return self._unavailable()
        now = to_epoch(self._now())
        upcoming = sorted((r for r in rows if r.start_utc > now), key=lambda r: (r.start_utc, r.appt_id))
        if not upcoming:
            return _reply("no_upcoming", "I don't see any upcoming appointments for that number.", appointments=[], count=0)
        wanted = args.get("service")
        category = self._classifier.category_of_request(wanted) if isinstance(wanted, str) and wanted.strip() else None
        focus = [r for r in upcoming if category is not None and r.category == category]
        rest = [r for r in upcoming if r not in focus]

        def when(row: StoredAppointment) -> str:
            return f"on {self._slot(row)} with {self._staff_name(row.staff_id)}"

        def brief(rows: list[StoredAppointment]) -> str:
            shown = [f"{self._label(r)} {when(r)}" for r in rows[:SHOWN_UPCOMING]]
            more = len(rows) - len(shown)
            return join_names(shown) + (f", and {more} more" if more > 0 else "")

        if category is not None and focus:
            lead = f"Your {self._label(focus[0])} is {when(focus[0])}."
            if len(focus) > 1:
                lead = f"Your next {self._label(focus[0])} is {when(focus[0])}, and you have {len(focus) - 1} more of that kind."
            tail = f" You also have {brief(rest)}." if rest else ""
            speak = lead + tail
        elif category is not None:
            speak = f"I don't see an upcoming {category.replace('-', ' ')} appointment. You do have {brief(upcoming)}."
        else:
            noun = "appointment" if len(upcoming) == 1 else "appointments"
            speak = f"You have {len(upcoming)} upcoming {noun}: {brief(upcoming)}."
            if len(upcoming) > SHOWN_UPCOMING:
                speak += " I can go through the rest if you like."
        return _reply("found", speak, appointments=[self._view(r) for r in upcoming[:8]], count=len(upcoming))

    # ---- HISTORY: the latest COMPLETED visit ---------------------------------------------------------------------
    def last_completed_visit(self, call_id: str, args: dict) -> dict:
        return self._labelled(call_id, self._with_lock(call_id, lambda: self._history.last_completed_visit(call_id, args or {})))

    # ---- BOOK ----------------------------------------------------------------------------------------------------
    def book_appointment(self, call_id: str, args: dict) -> dict:
        return self._labelled(call_id, self._with_lock(call_id, lambda: self._book(call_id, args or {})))

    @staticmethod
    def _clean_name(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        cleaned = " ".join("".join(ch for ch in value if ch.isprintable()).split())
        return cleaned if 0 < len(cleaned) <= NAME_MAX else None

    def _book(self, call_id: str, args: dict) -> dict:
        service, named, problem = self._select(args)
        if problem:
            return problem
        day, start, problem = self._parse_when(args, need_time=True)
        if problem:
            return problem
        name = self._clean_name(args.get("client_name"))
        if name is None:
            return _reply("needs_clarification", "May I have your full name for the booking?", reason="name_required")
        phone, problem = self._phone(call_id, {"phone": args.get("phone")})
        if problem:
            return problem
        spec = {"service_id": service.service_id, "start_utc": to_epoch(start), "name": name, "phone": phone, "named": staff_key(named) if named else None, "asked": named}
        pending = self._take_pending("book", call_id, args.get("confirmation_token")) if args.get("confirmed") is True else None
        if pending is not None and self._same(pending.payload, spec):
            return self._commit_book(call_id, args["confirmation_token"], pending, service, day, start)
        if self._changed_count(call_id) >= MAX_CHANGES_PER_CALL:
            return _reply("refused", self._contacting("I've made as many changes as I can on one call."), reason="one_change_limit")
        reply = self._check_slot(call_id, {k: v for k, v in args.items() if k in ("service_id", "date", "time", "staff")})
        if reply["status"] != "available":
            return reply  # it already says why not, and offers real alternatives
        technician = reply["options"][0]["technician"]
        staff = next(s for s in self._lc.dataset.roster if s.name == technician)
        full = {**spec, "staff_id": staff.staff_id, "staff_name": technician}
        token = self._new_pending("book", call_id, full)
        price = f" The listed price is {spoken_price(self._price(service))}." if self._price(service) is not None else ""
        return _reply(
            "confirmation_required",
            f"To confirm: {self._described(service.booksy_name, service.duration_minutes)}, with {technician}, on {spoken_slot(start)}, for {name}.{price} Shall I book it?",
            confirmation_token=token, service_id=service.service_id, service=service.booksy_name, duration_minutes=service.duration_minutes, technician=technician,
            start=start.isoformat(), price_usd=self._price(service),
            instruction="Read this back, wait for the caller's clear yes, then call book_appointment again with the same details, the same confirmation_token and confirmed=true.",
        )

    @staticmethod
    def _same(payload: dict, spec: dict) -> bool:
        """Does what was read back match what is now being confirmed? (The technician is the one the read-back named, unless the caller named another.)"""
        return all(payload.get(k) == spec[k] for k in ("service_id", "start_utc", "name", "phone")) and spec["named"] in (None, staff_key(payload.get("staff_name", "")))

    def _commit_book(self, call_id: str, token: str, pending: Pending, service, day, start) -> dict:
        p = pending.payload
        spec = NewBooking(
            p["name"], p["phone"], service.service_id, service.booksy_name, self._lc.dataset.category_of(service.service_id), p["staff_id"], p["start_utc"],
            service.duration_minutes, service.buffer_before_minutes, service.buffer_after_minutes, request_key=self._token_key("book", call_id, token),
        )
        try:
            outcome = self._lc.store.book(spec, validate=self._lc.judge.validator(), outbox=self._outbox)
        except StoreError:
            return self._unavailable()
        except Exception:  # an unforeseen failure: the transaction rolled back, but never claim either way
            return _reply("needs_review", self._contacting("Something went wrong and I can't confirm whether the booking went through. Please don't rely on it."), reason="exception")
        if outcome.status == "replayed" and (outcome.appointment.status, outcome.appointment.start_utc, outcome.appointment.staff_id) != (CONFIRMED, p["start_utc"], p["staff_id"]):
            now = f"it is now on {self._slot(outcome.appointment)}" if outcome.appointment.status == CONFIRMED else f"it is now {outcome.appointment.status.replace('_', ' ')}"
            return _reply("stale", f"That booking was made earlier, but the appointment has changed since: {now}. I haven't booked anything again.", reason="changed_after_the_booking",
                          appointment=self._view(outcome.appointment))
        if outcome.status in ("booked", "replayed"):  # the token stays until it expires, so a retry of this very request is answered from the first result
            return self._booked_reply(outcome, repeat=outcome.status == "replayed", call_id=call_id)
        if outcome.reason == "client_already_booked" and outcome.appointment is not None:
            return _reply(
                "unavailable", f"You already have {self._label(outcome.appointment)} on {self._slot(outcome.appointment)} then, so I haven't booked another.", reason="client_already_booked",
            )
        if outcome.status in ("unavailable", "unknown"):
            args = {"service_id": service.service_id, "date": day.isoformat(), "time": f"{start.hour:02d}:{start.minute:02d}"}
            if p.get("asked"):
                args["staff"] = p["asked"]  # the caller named someone: answer about that person, as they asked
            again = self._check_slot(call_id, args)
            again["speak"] = "I'm sorry, nothing was booked. " + again["speak"] if again["status"] != "available" else "I'm sorry, that time was just taken, so nothing was booked."
            again["reason"] = "changed_before_save"
            if again["status"] == "available":
                again["status"] = "unavailable"
            return again
        return _reply("refused", self._contacting("I'm not able to book that one, so nothing was booked."), reason=outcome.reason or outcome.status)

    def _booked_reply(self, outcome, *, repeat: bool, call_id: str) -> dict:
        row = outcome.appointment
        check = self._verify(row, expect_status=CONFIRMED)
        if check is not None:
            return check
        if not repeat:
            self._count_change(call_id)
            self._committed()
        what = f"{self._described(row.service_name, row.duration_minutes)}, with {self._staff_name(row.staff_id)}, on {self._slot(row)}"
        if repeat:
            return _reply("already_booked", f"That's already booked for you: {what}. I won't book it twice. Your reference is {self._spoken_reference(row.reference)}.", appointment=self._view(row))
        return _reply("booked_verified", f"You're booked: {what}. I checked the calendar and it's there. Your reference is {self._spoken_reference(row.reference)}.", appointment=self._view(row))

    def _verify(self, row: StoredAppointment, *, expect_status: str, moved_from: Optional[StoredAppointment] = None) -> Optional[dict]:
        """Re-read the store and the shared calendar after a save. None when both say what the save claims; otherwise a needs_review reply (never a success)."""
        try:
            saved = self._lc.store.get(row.reference)
            here = self._shown(self._local(row).date(), row.reference)
            elsewhere = self._shown(self._local(moved_from).date(), row.reference) if moved_from is not None else None
        except Exception:
            saved, here, elsewhere = None, None, None
        minutes = self._local(row).hour * 60 + self._local(row).minute
        good = saved is not None and saved.status == expect_status and (saved.service_id, saved.staff_id, saved.start_utc) == (row.service_id, row.staff_id, row.start_utc)
        good = good and here is not None and (here == [minutes] if expect_status == CONFIRMED else here == [])
        if good and moved_from is not None and self._local(moved_from).date() != self._local(row).date():
            good = elsewhere == []  # the old day no longer shows it
        if good:
            return None
        return _reply(
            "needs_review", self._contacting("I made the change but when I checked the calendar again I can't confirm it. Please don't rely on it."), reason="reverification_failed",
            reference=row.reference,
        )

    def _shown(self, day, reference: str) -> list:
        """The start minutes at which the shared calendar draws this appointment on that day (the same plan the page and the voice read)."""
        return [v.start for p in self._lc.dataset.day_plan(day).plans for v in p.visits if v.reference == reference]

    # ---- CANCEL ------------------------------------------------------------------------------------------------------
    def cancel_appointment(self, call_id: str, args: dict) -> dict:
        return self._labelled(call_id, self._with_lock(call_id, lambda: self._cancel(call_id, args or {})))

    def _cancel(self, call_id: str, args: dict) -> dict:
        row, problem = self._owned(call_id, args)
        if problem:
            return problem
        if row.status == CANCELLED:
            return _reply("already_cancelled", f"That {self._label(row)} appointment on {self._slot(row)} was already cancelled. Nothing else was changed.", appointment=self._view(row))
        if row.status != CONFIRMED or row.start_utc <= to_epoch(self._now()):
            return _reply("not_cancellable", self._contacting("I can't cancel that one, because it has already taken place or begun."), reason="not_upcoming")
        pending = self._take_pending("cancel", call_id, args.get("confirmation_token")) if args.get("confirmed") is True else None
        if pending is not None and pending.payload["reference"] == row.reference:
            if pending.payload["version"] != row.version:  # it changed after the read-back: read it back again
                return self._cancel_readback(call_id, row, stale=True)
            return self._commit_cancel(call_id, args["confirmation_token"], row, args.get("reason"))
        return self._cancel_readback(call_id, row)

    def _cancel_readback(self, call_id: str, row: StoredAppointment, *, stale: bool = False) -> dict:
        token = self._new_pending("cancel", call_id, {"reference": row.reference, "version": row.version})
        lead = "That appointment has changed since I read it out. " if stale else ""
        return _reply(
            "confirmation_required",
            f"{lead}To confirm: cancel your {self._label(row)} appointment with {self._staff_name(row.staff_id)} on {self._slot(row)}. Shall I go ahead and cancel it?",
            confirmation_token=token, appointment=self._view(row),
            instruction=(
                "Read this back and wait for a clear yes. You may ask why they are cancelling, but the reason is optional. If they are busy or unavailable, offer another day or time "
                "(then use reschedule_appointment); they may still cancel without choosing one. Then call cancel_appointment again with the same reference and number, the same "
                "confirmation_token, confirmed=true and the reason if they gave one. Never mention a fee or policy."
            ),
        )

    def _commit_cancel(self, call_id: str, token: str, row: StoredAppointment, reason: Any) -> dict:
        try:
            outcome = self._lc.store.cancel(
                row.reference, expected_version=row.version, reason=reason if isinstance(reason, str) else None, request_key=self._token_key("cancel", call_id, token),
                outbox=self._outbox,
            )
        except StoreError:
            return self._unavailable()
        except Exception:
            return _reply("needs_review", self._contacting("Something went wrong and I can't confirm whether it was cancelled. Please don't rely on it."), reason="exception")
        if outcome.status in ("cancelled", "replayed", "already_cancelled"):
            done = outcome.appointment
            check = self._verify(done, expect_status=CANCELLED)
            if check is not None:
                return check
            if outcome.status == "already_cancelled":
                return _reply("already_cancelled", "That appointment was already cancelled. Nothing else was changed.", appointment=self._view(done))
            if outcome.status == "cancelled":
                self._count_change(call_id)
                self._committed()
            return _reply("cancelled", f"Your {self._label(done)} appointment on {self._slot(done)} is cancelled, and that time is free again.", appointment=self._view(done))
        if outcome.status == "stale" and outcome.appointment is not None:
            return self._cancel_readback(call_id, outcome.appointment, stale=True)
        return _reply("refused", self._contacting("I couldn't cancel that, so nothing was changed."), reason=outcome.reason or outcome.status)

    # ---- RESCHEDULE ------------------------------------------------------------------------------------------------------
    def reschedule_appointment(self, call_id: str, args: dict) -> dict:
        return self._labelled(call_id, self._with_lock(call_id, lambda: self._reschedule(call_id, args or {})))

    def _repeat_of_a_move(self, call_id: str, args: dict, row: StoredAppointment) -> Optional[dict]:
        """The same confirmed move arriving again (a retry, a repeated yes). If THIS request already moved the appointment, the answer is what is true now: already moved,
        or moved and then changed since. Either way nothing is changed again and no second alert is queued. None when this request never happened."""
        pending = self._take_pending("reschedule", call_id, args.get("confirmation_token")) if args.get("confirmed") is True else None
        if pending is None or pending.payload.get("reference") != row.reference:
            return None
        if self._lc.store.request_reference(self._token_key("reschedule", call_id, args["confirmation_token"]), "reschedule") != row.reference:
            return None
        intended = (pending.payload["start_utc"], pending.payload["staff_id"])
        if row.status == CONFIRMED and (row.start_utc, row.staff_id) == intended:
            check = self._verify(row, expect_status=CONFIRMED)
            if check is not None:
                return check
            return _reply(
                "already_rescheduled", f"That move was already made: your {self._label(row)} appointment is on {self._slot(row)}, with {self._staff_name(row.staff_id)}. I haven't changed anything again.",
                appointment=self._view(row),
            )
        now = f"it is now on {self._slot(row)} with {self._staff_name(row.staff_id)}" if row.status == CONFIRMED else f"it is now {row.status.replace('_', ' ')}"
        return _reply(
            "stale", f"That move was made earlier, but the appointment has changed since: {now}. I haven't changed anything more.", reason="changed_after_the_move", appointment=self._view(row),
        )

    def _reschedule(self, call_id: str, args: dict) -> dict:
        row, problem = self._owned(call_id, args)
        if problem:
            return problem
        replay = self._repeat_of_a_move(call_id, args, row)
        if replay is not None:
            return replay
        if row.status != CONFIRMED or row.start_utc <= to_epoch(self._now()):
            return _reply("not_reschedulable", self._contacting("I can't move that one, because it isn't an upcoming appointment."), reason="not_upcoming")
        service = self._lc.dataset.registry.get(row.service_id)
        if service is None:
            return _reply("not_reschedulable", self._contacting("I can't move that one, because that service isn't offered now."), reason="service_not_offered")
        day, start, problem = self._parse_when(args, need_time=True)
        if problem:
            return problem
        asked = args.get("staff")
        named = asked.strip() if isinstance(asked, str) and asked.strip() else None
        target, refusal = self._place_move(row, service, start, named)
        pending = self._take_pending("reschedule", call_id, args.get("confirmation_token")) if args.get("confirmed") is True else None
        if target is None:
            return self._cannot_move(call_id, row, service, day, start, named, refusal)
        if (target, to_epoch(start)) == (row.staff_id, row.start_utc):  # already there: nothing to confirm and nothing to change
            return _reply("unchanged", f"Your {self._label(row)} appointment is already on {self._slot(row)} with {self._staff_name(row.staff_id)}. Nothing was changed.", appointment=self._view(row))
        want = {"reference": row.reference, "version": row.version, "start_utc": to_epoch(start)}
        if pending is not None and all(pending.payload.get(k) == v for k, v in want.items()) and (named is None or pending.payload["staff_id"] == target):
            return self._commit_move(call_id, args["confirmation_token"], row, pending.payload["staff_id"], to_epoch(start))
        if self._changed_count(call_id) >= MAX_CHANGES_PER_CALL:
            return _reply("refused", self._contacting("I've made as many changes as I can on one call."), reason="one_change_limit")
        token = self._new_pending("reschedule", call_id, {**want, "staff_id": target})
        who = self._staff_name(target)
        return _reply(
            "confirmation_required",
            f"To confirm: move your {self._label(row)} appointment from {self._slot(row)} to {spoken_slot(start)}, with {who}. Shall I make that change?",
            confirmation_token=token, appointment=self._view(row), new_start=start.isoformat(), technician=who,
            instruction="Read this back and wait for a clear yes, then call reschedule_appointment again with the same reference, number and new date and time, the same confirmation_token and confirmed=true.",
        )

    def _place_move(self, row: StoredAppointment, service, start: datetime, named: Optional[str]):
        """(staff id, None) for the technician who can take the new time, preferring the same one; or (None, the first refusal)."""
        roster = self._lc.dataset.roster
        if named:
            wanted = staff_key(named)
            people = [s for s in roster if staff_key(s.name) == wanted] or [s for s in roster if wanted and set(wanted.split()) <= set(staff_key(s.name).split())]
            candidates = [s.staff_id for s in people][:1] if len(people) == 1 else []
            if not candidates:
                return None, Refused("staff_unavailable", "unknown_staff", asked=named)
        else:
            others = [s.staff_id for s in roster if s.staff_id != row.staff_id and service.eligible(s.name, s.staff_id) and s.hours_known]
            candidates = [row.staff_id, *others]
        first: Optional[Refused] = None
        for staff_id in candidates:
            placement = Placement(staff_id, to_epoch(start), service.duration_minutes, service.buffer_before_minutes, service.buffer_after_minutes, service.service_id, row.category, row.reference)
            try:
                self._lc.judge.require(placement, self._lc.store.between)
                return staff_id, None
            except Refused as refusal:
                first = first or refusal
        return None, first

    def _cannot_move(self, call_id: str, row, service, day, start, named, refusal: Optional[Refused]) -> dict:
        lead = f"Your {self._label(row)} appointment stays on {self._slot(row)}. "
        reason = refusal.reason if refusal is not None else "unknown"
        if reason in ("unknown_staff", "staff_not_eligible"):
            return _reply("staff_unavailable", f"I can't do that with {named or 'that technician'}. {lead}", reason=reason)
        snapshot, who = (refusal.detail.get("snapshot"), refusal.detail.get("staff")) if refusal else (None, None)
        if reason == "not_working" and who:
            text = f"{who} isn't working on {spoken_day(start)}."
        elif reason == "not_free" and snapshot is not None:
            _code, text, _extra = self._why_not(snapshot, start, service, [who], named)
        elif reason in ("calendar_unknown", "not_covered"):
            return _reply("unknown", self._contacting(f"I couldn't confirm that time on the calendar, so I haven't changed anything. {lead}".rstrip()), reason=reason)
        else:
            return _reply("refused", self._contacting(f"I can't move it to then. {lead}"), reason=reason)
        slots, unreadable, _error, _age, cut_short = self._alternatives(day, start.hour * 60 + start.minute, None, service, named)
        offered = self._offer(call_id, slots, unreadable, service, named, lead=f"{text} {lead}", cut_short=cut_short)
        offered["reason"] = "new_time_unavailable"
        return offered

    def _commit_move(self, call_id: str, token: str, row: StoredAppointment, staff_id: str, start_utc: int) -> dict:
        try:
            outcome = self._lc.store.reschedule(
                row.reference, start_utc=start_utc, staff_id=staff_id, expected_version=row.version, validate=self._lc.judge.validator(),
                request_key=self._token_key("reschedule", call_id, token), outbox=self._outbox,
            )
        except StoreError:
            return self._unavailable()
        except Exception:
            return _reply("needs_review", self._contacting("Something went wrong and I can't confirm whether it was moved. Please don't rely on it."), reason="exception")
        if outcome.status in ("rescheduled", "replayed", "unchanged"):
            done = outcome.appointment
            check = self._verify(done, expect_status=CONFIRMED, moved_from=outcome.previous)
            if check is not None:
                return check
            if outcome.status == "unchanged":
                return _reply("unchanged", f"Your {self._label(done)} appointment is already on {self._slot(done)} with {self._staff_name(done.staff_id)}. Nothing was changed.", appointment=self._view(done))
            if outcome.status == "rescheduled":
                self._count_change(call_id)
                self._committed()
            return _reply(
                "rescheduled", f"Done. Your {self._label(done)} appointment is now on {self._slot(done)}, with {self._staff_name(done.staff_id)}. I checked the calendar and it's there.",
                appointment=self._view(done),
            )
        if outcome.status == "stale" and outcome.appointment is not None:
            return _reply("stale", f"That appointment changed while we were talking, so I haven't moved anything. It is now {self._label(outcome.appointment)} on {self._slot(outcome.appointment)}.", appointment=self._view(outcome.appointment), reason="changed_since_read_back")
        if outcome.status in ("unavailable", "unknown"):
            current = self._lc.store.get(row.reference)
            where = f" Your {self._label(current)} appointment is still on {self._slot(current)}." if current is not None and current.status == CONFIRMED else ""
            return _reply("unavailable", f"I'm sorry, that new time was just taken, so I haven't changed anything.{where} Shall I look for other times?", reason="changed_before_save")
        return _reply("refused", self._contacting("I couldn't move that, so nothing was changed."), reason=outcome.reason or outcome.status)
