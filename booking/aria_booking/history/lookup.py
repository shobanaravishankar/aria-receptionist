"""last_completed_visit: "when was my last <service category> appointment?" Offline design; not exposed anywhere.

What it will say: the date of the most recent COMPLETED visit in the requested category, the service, and the technician only if asked. What it
will never say: a phone number, a name, a record id, how many records matched, anything about another client, notes, medical or payment data.
It never guesses: a missing, ambiguous, unreadable, incomplete or uncertain case becomes "I can't confirm" (or a request for the one thing
that would settle it).
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Callable, Optional

from ..voice.speech import spoken_day
from .classify import Classifier
from .models import (
    CANCELLED, COMPLETED, NO_SHOW, UPCOMING, ClientHistory, ClientRef, HistorySource, HistoryUnavailable, Visit,
)
from .phone import normalize_phone

MAX_ATTEMPTS_PER_CALL = 3  # a caller cannot try number after number to discover who is a client
CONTACT = "Please contact the salon directly."


def _response(status: str, speak: str, **data: Any) -> dict:
    return {"status": status, "ok": status == "found", "speak": speak, **data}


def _unable(reason: str, speak: str) -> dict:
    return _response("unable_to_confirm", speak, reason=reason)


def _norm_name(value: Optional[str]) -> str:
    return " ".join((value or "").casefold().split())


class HistoryLookup:
    def __init__(self, source: HistorySource, clock: Callable[[], datetime], *, classifier: Optional[Classifier] = None,
                 max_attempts_per_call: int = MAX_ATTEMPTS_PER_CALL):
        self._source, self._clock = source, clock
        self._classifier = classifier or Classifier()
        self._max_attempts = max_attempts_per_call
        self._attempts: dict[str, int] = {}
        self._lock = threading.Lock()

    def last_completed_visit(self, call_id: str, args: dict) -> dict:
        args = args or {}
        if not isinstance(call_id, str) or not call_id.strip():
            return _response("needs_clarification", "I could not identify this call, so I can't look anything up.", reason="no_call_id")
        category = self._classifier.category_of_request(args.get("service_category") if isinstance(args.get("service_category"), str) else None)
        if category is None:
            return _response("needs_clarification", "Which kind of appointment do you mean, for example lashes, a massage or a facial?", reason="service_category")
        raw_phone = args.get("phone")
        if raw_phone is None or (isinstance(raw_phone, str) and not raw_phone.strip()):
            return _response("needs_clarification", "Could you give me the complete phone number on your account, including the area code?", reason="phone_required")
        phone = normalize_phone(raw_phone)
        if phone is None:
            return _response(
                "invalid_phone", "That doesn't sound like a complete phone number. Could you give me the whole number, including the area code?", reason="phone_incomplete"
            )
        with self._lock:
            used = self._attempts.get(call_id, 0)
            if used >= self._max_attempts:
                return _response("refused", f"I can't look up any more numbers on this call. {CONTACT}", reason="too_many_attempts")
            self._attempts[call_id] = used + 1

        try:
            clients = self._source.find_clients(phone)
        except HistoryUnavailable:
            return _unable("source_unavailable", "I can't reach the appointment records right now, so I can't confirm that. " + CONTACT)
        except Exception:  # never leak internals
            return _unable("source_error", "I couldn't read the appointment records, so I can't confirm that. " + CONTACT)
        client = self._choose(clients, args.get("name") if isinstance(args.get("name"), str) else None)
        if isinstance(client, dict):
            return client
        try:
            history = self._source.read_history(client)
        except HistoryUnavailable:
            return _unable("source_unavailable", "I can't reach the appointment records right now, so I can't confirm that. " + CONTACT)
        except Exception:  # HistoryUnreadable or anything unexpected: never guess, never leak
            return _unable("history_unreadable", "I couldn't read that appointment history clearly, so I can't confirm it. " + CONTACT)
        return self._answer(history, category, wants_technician=args.get("include_technician") is True)

    # ---- which client record -------------------------------------------------------------------
    @staticmethod
    def _choose(clients: list[ClientRef], name: Optional[str]) -> "ClientRef | dict":
        """Exactly one record, or a response that asks for the one thing that would settle it. Never lists or counts records."""
        if not clients:
            return _response("not_found", "I can't find an appointment history for that number. " + CONTACT, reason="no_match")
        if len(clients) == 1:
            return clients[0]
        wanted = _norm_name(name)
        if not wanted:
            return _response(
                "needs_clarification", "I can't settle on a single record from the number alone. Could you tell me the full name on the booking?", reason="name_needed"
            )
        named = [c for c in clients if _norm_name(c.name) == wanted]  # a record with no name can never match
        if len(named) == 1:
            return named[0]
        return _unable("record_ambiguous", "I can't confirm a single record from that. " + CONTACT)

    # ---- what the history says -----------------------------------------------------------------
    def _answer(self, history: ClientHistory, category: str, *, wants_technician: bool) -> dict:
        if not history.completion_known:
            return _unable(
                "completion_unavailable",
                "I can see past appointments but not whether they were completed, so I can't confirm your last one. " + CONTACT,
            )
        now = self._clock()
        best: Optional[tuple[Visit, str]] = None  # (visit, the matching service name)
        newest_uncertain: Optional[datetime] = None
        uncertain_without_date = False
        for visit in history.visits:
            if visit.status in (CANCELLED, NO_SHOW, UPCOMING):
                continue  # these did not happen (yet), whatever the date says
            kinds = [self._classifier.categories_of(s) for s in visit.services]
            matching = [s for s, k in zip(visit.services, kinds) if category in k]
            unplaced = not visit.services or any(not k for k in kinds)
            if not matching and not unplaced:
                continue  # a different kind of service
            happened = visit.status == COMPLETED and visit.start is not None and visit.start < now
            if happened and matching:
                if best is None or visit.start > best[0].start:
                    best = (visit, matching[0])
                continue
            # Relevant, but we cannot be sure whether it counts: an unknown or unrecognised status, an unreadable date, a "completed" visit
            # dated in the future, or a completed visit whose service we could not place. It blocks any older answer.
            if visit.start is None:
                uncertain_without_date = True
            elif newest_uncertain is None or visit.start > newest_uncertain:
                newest_uncertain = visit.start
        if uncertain_without_date or (newest_uncertain is not None and (best is None or newest_uncertain > best[0].start)):
            return _unable("status_uncertain", "I can't tell for sure which of your appointments was the last one, so I can't confirm it. " + CONTACT)
        if not history.complete and not (best is not None and history.newest_first):
            return _unable("history_incomplete", "I couldn't see all of your appointment history, so I can't confirm your last one. " + CONTACT)
        label = category.replace("-", " ")
        if best is None:
            return _response("no_completed_visit", f"I don't see a completed {label} appointment in your history.", reason="none_in_history")
        visit, service = best
        local = visit.start
        who = f" with {visit.technician}" if wants_technician and visit.technician else ""
        data: dict[str, Any] = {"date": local.date().isoformat(), "service": service}
        if wants_technician and visit.technician:
            data["technician"] = visit.technician
        return _response(
            "found", f"Your last completed {label} appointment was {service} on {spoken_day(local)}{who}.", **data
        )
