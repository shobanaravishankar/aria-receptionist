"""The three functions a voice agent may call. Pure Python: no HTTP, no Retell, no browser of its own.

  check_slot(call_id, args)         READ-ONLY. Is this start time bookable for the full service? If not, why, plus
                                    genuinely free alternatives from the current calendar.
  find_alternatives(call_id, args)  READ-ONLY. Free options near a preferred day/time.
  book_slot(call_id, args)          The ONLY writer. Needs an option this call was offered AND a two-step explicit
                                    confirmation held on the server, then re-validates and goes through BookingService.

Honesty rules enforced here, not left to the prompt:
  * ``ok`` is True only for ``booked_verified`` / ``already_booked``. Unknown, conflict, mismatch, uncertain,
    signed-out and busy are never success, and their ``speak`` text says so.
  * Alternatives are only slots that ``find_slots`` returned from a calendar read in this request; nothing is invented.
  * An option id is bound to the call that received it and expires; it cannot be forged or reused by another call.
  * Booking needs ``confirmed`` to be exactly True on a SECOND call after a ``confirmation_required`` read-back.
  * One booking per call; a repeated or retried book request returns the earlier result and never saves again.
  * A single lock serialises the one dedicated browser; a request that cannot get it says "busy", it does not queue.
"""

from __future__ import annotations

import re
import secrets
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from ..availability import find_slots, validate_slot
from ..booking_service import BookingResult, BookingService, Status
from ..config import Config
from ..driver import DriverError, SignInRequired
from ..models import DaySnapshot, Interval, ServiceSpec, Slot, add_minutes, to_utc
from .speech import join_choices, spoken_day, spoken_duration, spoken_slot, spoken_time

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
SUCCESS_STATUSES = frozenset({"booked_verified", "already_booked"})


@dataclass
class Option:
    option_id: str
    call_id: str
    start: datetime
    created: datetime


def _response(status: str, speak: str, **data: Any) -> dict:
    return {"status": status, "ok": status in SUCCESS_STATUSES, "speak": speak, **data}


class VoiceTools:
    def __init__(
        self,
        cfg: Config,
        driver: Any,
        service_factory: Callable[[], BookingService],
        clock: Callable[[], datetime],
        *,
        booking_enabled: bool = False,
        option_ttl_seconds: int = 900,
        confirm_ttl_seconds: int = 300,
        search_days: int = 4,
        max_options: int = 3,
        horizon_days: int = 60,
        max_bookings_total: int = 3,
        lock_timeout_seconds: float = 20.0,
    ):
        self.cfg, self.driver, self._service_factory, self._clock = cfg, driver, service_factory, clock
        self.booking_enabled = booking_enabled
        self._option_ttl = timedelta(seconds=option_ttl_seconds)
        self._confirm_ttl = timedelta(seconds=confirm_ttl_seconds)
        self._search_days, self._max_options, self._horizon_days = search_days, max_options, horizon_days
        self._max_bookings_total, self._lock_timeout = max_bookings_total, lock_timeout_seconds
        self._lock = threading.Lock()
        self._options: dict[str, Option] = {}
        self._pending: dict[str, tuple[str, datetime]] = {}  # call_id -> (option_id, expires)
        self._booked_by_call: dict[str, tuple[str, datetime, dict]] = {}  # call_id -> (option_id, start, response)
        self._booked_total = 0
        self._tz = ZoneInfo(cfg.timezone)

    # ---- helpers ---------------------------------------------------------------------------
    @property
    def service(self) -> ServiceSpec:
        c = self.cfg
        return ServiceSpec(c.service_name, c.service_duration_minutes, c.buffer_before_minutes, c.buffer_after_minutes)

    def _now(self) -> datetime:
        return self._clock().astimezone(self._tz)

    def _prune(self) -> None:
        now = self._now()
        self._options = {k: o for k, o in self._options.items() if now - o.created <= self._option_ttl}
        self._pending = {k: v for k, v in self._pending.items() if v[1] > now}

    def _issue(self, call_id: str, start: datetime) -> dict:
        option = Option("opt_" + secrets.token_urlsafe(6), call_id, start, self._now())
        self._options[option.option_id] = option
        return {"option_id": option.option_id, "label": spoken_slot(start), "start": start.isoformat()}

    def _with_lock(self, call_id: str, work: Callable[[], dict]) -> dict:
        if not isinstance(call_id, str) or not call_id.strip():
            return _response("needs_clarification", "I could not identify this call, so I can't check the calendar.", reason="no_call_id")
        if not self._lock.acquire(timeout=self._lock_timeout):
            return _response("busy", "I'm still working on the previous request. Please give me a moment and I'll try again.")
        try:
            self._prune()
            return work()
        finally:
            self._lock.release()

    def _parse_when(self, args: dict, *, need_time: bool) -> tuple[Optional[date], Optional[datetime], Optional[dict]]:
        """(day, start, None) or (None, None, clarification response). Strict: the agent must pass structured values."""
        raw_day, raw_time = args.get("date"), args.get("time")
        missing = [name for name, value in (("date", raw_day), ("time", raw_time)) if not value and (name == "date" or need_time)]
        if missing:
            ask = " and ".join("which day" if m == "date" else "what time" for m in missing)
            return None, None, _response("needs_clarification", f"I need to know {ask} you would like.", reason="missing", missing=missing)
        if not isinstance(raw_day, str) or not DATE_RE.match(raw_day):
            return None, None, _response("needs_clarification", "Which date did you mean? Please give me the day.", reason="bad_date")
        try:
            day = date.fromisoformat(raw_day)
        except ValueError:
            return None, None, _response("needs_clarification", "That date doesn't exist. Which date did you mean?", reason="bad_date")
        today = self._now().date()
        if day < today:
            return None, None, _response("needs_clarification", "That date has already passed. Which day would you like instead?", reason="past_date")
        if day > today + timedelta(days=self._horizon_days):
            return None, None, _response("needs_clarification", "That's further ahead than I can book. Could you pick a nearer date?", reason="too_far")
        start = None
        if raw_time or need_time:
            if not isinstance(raw_time, str) or not TIME_RE.match(raw_time):
                return None, None, _response("needs_clarification", "What time did you mean? For example, 2:30 in the afternoon.", reason="bad_time")
            hour, minute = (int(part) for part in raw_time.split(":"))
            if hour > 23 or minute > 59:
                return None, None, _response("needs_clarification", "That time doesn't look right. What time did you mean?", reason="bad_time")
            if minute % self.cfg.grid_minutes:
                return None, None, _response(
                    "needs_clarification",
                    f"We book on the quarter hour. Would a time such as {spoken_time(datetime(day.year, day.month, day.day, hour, 0, tzinfo=self._tz))} "
                    "or a quarter past, half past or quarter to work?",
                    reason="off_grid",
                )
            start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=self._tz)
        return day, start, None

    # ---- explaining a refusal --------------------------------------------------------------
    def _why_not(self, snapshot: DaySnapshot, start: datetime) -> tuple[str, str, dict]:
        """(reason, spoken explanation, extra data) for a start time that cannot be booked. Known facts only."""
        sd = snapshot.for_staff(self.cfg.staff_name)
        service, now = self.service, self._now()
        length = spoken_duration(service.duration_minutes)
        windows = sorted(sd.working or (), key=lambda w: to_utc(w.start))
        if not windows:
            return "not_working", f"We aren't taking appointments on {spoken_day(start)}.", {}
        first_open, last_close = windows[0].start.astimezone(self._tz), windows[-1].end.astimezone(self._tz)
        containing = [w for w in windows if to_utc(w.start) <= to_utc(start) < to_utc(w.end)]
        if not containing:
            return (
                "outside_hours",
                f"{spoken_time(start)} is outside our hours on {spoken_day(start)}. We're open from {spoken_time(first_open)} to {spoken_time(last_close)}.",
                {"opens": first_open.strftime("%H:%M"), "closes": last_close.strftime("%H:%M")},
            )
        window = containing[0]
        needed_end = add_minutes(start, service.duration_minutes + service.buffer_after_minutes)
        if to_utc(needed_end) > to_utc(window.end):
            latest = add_minutes(window.end, -(service.duration_minutes + service.buffer_after_minutes)).astimezone(self._tz)
            latest = latest.replace(minute=latest.minute - latest.minute % self.cfg.grid_minutes, second=0, microsecond=0)
            close = window.end.astimezone(self._tz)
            if to_utc(latest) >= to_utc(window.start):
                return (
                    "ends_after_closing",
                    f"The {length} service starting at {spoken_time(start)} would run past our {spoken_time(close)} closing. "
                    f"The latest start that day is {spoken_time(latest)}.",
                    {"latest_start": latest.strftime("%H:%M"), "closes": close.strftime("%H:%M")},
                )
            return "ends_after_closing", f"The {length} service doesn't fit within our hours that day.", {"closes": close.strftime("%H:%M")}
        if to_utc(add_minutes(now, self.cfg.min_lead_minutes)) > to_utc(start):
            return "too_soon", f"That's too soon. We need at least {self.cfg.min_lead_minutes} minutes' notice.", {}
        return "occupied", f"{spoken_time(start)} on {spoken_day(start)} is already taken.", {}

    # ---- finding alternatives --------------------------------------------------------------
    def _alternatives(self, first_day: date, preferred_minutes: int, first_snapshot: Optional[DaySnapshot]) -> tuple[list[Slot], int]:
        """(chosen slots, number of days that could not be read). Only slots find_slots returned from a real read."""
        chosen: list[Slot] = []
        unreadable = 0
        for offset in range(self._search_days):
            day = first_day + timedelta(days=offset)
            if offset == 0 and first_snapshot is not None:
                snapshot = first_snapshot
            else:
                try:
                    snapshot = self.driver.read_day(day)
                except DriverError:
                    unreadable += 1
                    continue
            found = find_slots(
                snapshot, self.service, self.cfg.staff_name, now=self._now(),
                grid_minutes=self.cfg.grid_minutes, min_lead_minutes=self.cfg.min_lead_minutes,
            )
            if found.unknown:
                unreadable += 1
                continue
            slots = sorted(found.slots, key=lambda s: to_utc(s.service.start))
            if not slots:
                continue

            def minutes(slot: Slot) -> int:
                local = slot.service.start.astimezone(self._tz)
                return local.hour * 60 + local.minute

            if offset == 0:
                after = [s for s in slots if minutes(s) >= preferred_minutes]
                before = [s for s in slots if minutes(s) < preferred_minutes]
                for slot in ([after[0]] if after else []) + ([before[-1]] if before else []):
                    chosen.append(slot)
            else:
                chosen.append(min(slots, key=lambda s: abs(minutes(s) - preferred_minutes)))
            if len(chosen) >= self._max_options:
                break
        return chosen[: self._max_options], unreadable

    def _offer(self, call_id: str, slots: list[Slot], unreadable: int, *, lead: str) -> dict:
        if not slots:
            if unreadable:
                return _response(
                    "unknown",
                    f"{lead}I couldn't read the calendar clearly enough to offer other times. A team member can help, or we can try again in a moment.",
                    options=[],
                )
            return _response("no_alternatives", f"{lead}I don't see any other openings in the next few days. Would you like me to look further ahead?", options=[])
        options = [self._issue(call_id, s.service.start.astimezone(self._tz)) for s in slots]
        labels = join_choices([o["label"] for o in options])
        return _response("alternatives", f"{lead}I do have {labels}. Which would you prefer?", options=options)

    # ---- the three functions -----------------------------------------------------------------
    def check_slot(self, call_id: str, args: dict) -> dict:
        return self._with_lock(call_id, lambda: self._check_slot(call_id, args or {}))

    def _check_slot(self, call_id: str, args: dict) -> dict:
        day, start, problem = self._parse_when(args, need_time=True)
        if problem:
            return problem
        try:
            snapshot = self.driver.read_day(day)
        except SignInRequired:
            return _response("system_unavailable", "The scheduling system needs attention right now, so I can't check availability. A team member will follow up.")
        except DriverError:
            return _response("unknown", "I couldn't read the calendar clearly, so I can't tell you whether that time is free. Let me not guess.")
        verdict = validate_slot(snapshot, self.service, self.cfg.staff_name, start, now=self._now(), min_lead_minutes=self.cfg.min_lead_minutes)
        if verdict.unknown:
            return _response("unknown", "I couldn't confirm the calendar for that day, so I can't tell you whether it's free. Let me not guess.")
        length = spoken_duration(self.service.duration_minutes)
        if verdict.ok:
            option = self._issue(call_id, start)
            return _response(
                "available",
                f"{spoken_slot(start)} is open for the full {length}. Would you like me to book it?",
                options=[option],
            )
        reason, explanation, extra = self._why_not(snapshot, start)
        local = start.hour * 60 + start.minute
        slots, unreadable = self._alternatives(day, local, snapshot)
        offered = self._offer(call_id, slots, unreadable, lead=explanation + " ")
        offered.update(extra)
        offered["unavailable_reason"] = reason
        offered["requested"] = start.isoformat()
        return offered

    def find_alternatives(self, call_id: str, args: dict) -> dict:
        return self._with_lock(call_id, lambda: self._find_alternatives(call_id, args or {}))

    def _find_alternatives(self, call_id: str, args: dict) -> dict:
        day, start, problem = self._parse_when(args, need_time=False)
        if problem:
            return problem
        preferred = (start.hour * 60 + start.minute) if start else 10 * 60
        slots, unreadable = self._alternatives(day, preferred, None)
        return self._offer(call_id, slots, unreadable, lead="")

    def book_slot(self, call_id: str, args: dict) -> dict:
        return self._with_lock(call_id, lambda: self._book_slot(call_id, args or {}))

    def _book_slot(self, call_id: str, args: dict) -> dict:
        option = self._options.get(str(args.get("option_id") or ""))
        earlier = self._booked_by_call.get(call_id)
        if earlier is not None:
            earlier_option, _start, result = earlier
            if str(args.get("option_id") or "") == earlier_option:
                return dict(result)  # a repeat or a retry: the earlier answer, never a second save
            return _response(
                "refused", "I can only make one booking per call, and that one is already done. A team member can help with anything else.",
                reason="one_booking_per_call",
            )
        if not self.booking_enabled:
            return _response("refused", "Booking isn't switched on for this line right now, so I can't book. A team member will follow up.", reason="booking_disabled")
        if option is None or option.call_id != call_id:
            return _response(
                "invalid_option",
                "I need to check availability again before I can book. Let me look again.",
                reason="unknown_or_expired_option",
            )
        if self._booked_total >= self._max_bookings_total:
            return _response("refused", "This test line has reached its booking limit, so I can't book. A team member will follow up.", reason="session_limit")

        pending = self._pending.get(call_id)
        confirmed = args.get("confirmed") is True  # exactly the boolean true; "yes", 1 and "true" do not count
        if not (confirmed and pending is not None and pending[0] == option.option_id):
            self._pending[call_id] = (option.option_id, self._now() + self._confirm_ttl)
            length = spoken_duration(self.service.duration_minutes)
            return _response(
                "confirmation_required",
                f"To confirm: {self.cfg.service_name}, {length}, on {spoken_slot(option.start)}. Shall I book it?",
                option_id=option.option_id,
                instruction="Read this back, wait for the caller's clear yes, then call book_slot again with the same option_id and confirmed=true.",
            )

        try:
            service = self._service_factory()
            result = service.book(option.start)
        except Exception:  # an unforeseen failure after the Save click could have saved; never claim either way
            return self._remember(call_id, option, _response("needs_review", "Something went wrong and I can't confirm whether the booking went through. A team member will check; please don't rely on it.", reason="exception"))
        return self._remember_or_return(call_id, option, result)

    def _remember(self, call_id: str, option: Option, response: dict) -> dict:
        self._booked_by_call[call_id] = (option.option_id, option.start, response)
        self._pending.pop(call_id, None)
        return dict(response)

    def _remember_or_return(self, call_id: str, option: Option, result: BookingResult) -> dict:
        response = self._map(result, option)
        if response["status"] in ("booked_verified", "already_booked"):
            self._booked_total += 1
            return self._remember(call_id, option, response)
        if response["status"] == "needs_review":
            return self._remember(call_id, option, response)  # outcome unknown: never retry within the call
        self._pending.pop(call_id, None)  # a definite non-booking: the caller may choose again
        return response

    def _map(self, result: BookingResult, option: Option) -> dict:
        when = spoken_slot(option.start)
        length = spoken_duration(self.service.duration_minutes)
        status = result.status
        ref = {"reference": result.ref}
        if status is Status.BOOKED_VERIFIED:
            return _response("booked_verified", f"You're booked: {self.cfg.service_name}, {length}, on {when}. I checked the calendar and it's there.", **ref)
        if status is Status.ALREADY_BOOKED:
            return _response("already_booked", f"That time is already booked for you: {self.cfg.service_name} on {when}. I won't book it twice.", **ref)
        if status is Status.SLOT_UNAVAILABLE:
            return _response("unavailable", f"I'm sorry, {when} is no longer available, so nothing was booked. Shall I look for other times?", reason="changed_before_save", **ref)
        if status is Status.UNKNOWN_AVAILABILITY:
            return _response("unknown", "I couldn't confirm the calendar just now, so I haven't booked anything. Let me not guess.", **ref)
        if status is Status.SIGN_IN_REQUIRED:
            return _response("system_unavailable", "The scheduling system needs attention right now, so I haven't booked anything. A team member will follow up.", **ref)
        if status is Status.REJECTED_BY_SAFETY:
            return _response("refused", "I'm not able to book that one, so nothing was booked. A team member can help.", reason="safety_check", **ref)
        if status is Status.NOT_SAVED:
            return _response("not_saved", "The booking didn't go through, and nothing was saved. Shall I try again?", **ref)
        # uncertain / mismatch / conflict: something may exist but it cannot be confirmed as the requested booking
        return _response(
            "needs_review",
            "I can't confirm that booking. Something may or may not have been saved, so please don't treat it as booked. A team member will check it.",
            **ref,
        )
