"""The functions a voice agent may call. Pure Python: no HTTP, no Retell, no browser of its own.

  lookup_service(call_id, args)     LOCAL ONLY. What the website says about a service (price, length, description) and whether
                                    it can be booked online. Never touches the calendar, never waits for the browser lock.
  check_slot(call_id, args)         READ-ONLY. Is this start time bookable for the full service, with whom? If not, why, plus
                                    genuinely free alternatives from the current calendar.
  find_alternatives(call_id, args)  READ-ONLY. Free options near a preferred day/time.
  book_slot(call_id, args)          The ONLY writer. Needs an option this call was offered AND a two-step explicit
                                    confirmation held on the server, then re-validates and goes through BookingService.

Honesty rules enforced here, not left to the prompt:
  * ``ok`` is True only for ``booked_verified`` / ``already_booked``. Unknown, conflict, mismatch, uncertain,
    signed-out and busy are never success, and their ``speak`` text says so.
  * Alternatives are only slots that ``find_slots`` returned from a calendar read in this request; nothing is invented.
  * A service is chosen ONLY by its canonical id from the bookable allowlist. Its duration, buffers and Booksy name come from
    that allowlist, never from the caller or the model; a website service with no verified mapping can be discussed, not booked.
  * An option id binds call + service + technician + start on the server. It cannot be altered, forged, shared or reused by
    another call, and it expires. Changing the service or technician in a call retires that call's earlier options.
  * Every option and every read-back names the verified service, length and technician.
  * Booking needs ``confirmed`` to be exactly True on a SECOND call after a ``confirmation_required`` read-back.
  * One booking per call; a repeated or retried book request returns the earlier result and never saves again, and a
    repeated success is re-verified read-only first.
  * A single lock serialises the one dedicated browser; a request that cannot get it says "busy", it does not queue.
"""

from __future__ import annotations

import hashlib
import inspect
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from ..availability import find_slots, validate_slot
from ..booking_service import BookingResult, BookingService, Status
from ..catalog.bookable import BookableRegistry, BookableService, staff_key
from ..catalog.lookup import DEFAULT_CATALOG, Catalog, descriptive_label
from ..config import Config
from ..driver import DriverError, SignInRequired
from ..models import DaySnapshot, ServiceSpec, Slot, add_minutes, to_utc
from ..timing import PhaseTimer
from .speech import join_choices, spoken_day, spoken_duration, spoken_price, spoken_slot, spoken_time

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
SUCCESS_STATUSES = frozenset({"booked_verified", "already_booked"})
CONTACT = "Please contact the salon directly."
AVAILABILITY_ONLY_NOTE = "I can only check availability on this line, so I can't book it. Please contact the salon directly to book."
# how close a refused start is to being bookable: the most informative reason to give when nobody can take it
REASON_RANK = {"not_working": 0, "outside_hours": 1, "ends_after_closing": 2, "too_soon": 3, "occupied": 4}


@dataclass
class Option:
    option_id: str
    call_id: str
    start: datetime
    created: datetime
    service: BookableService
    staff: str  # the technician's display name as the calendar shows it
    price_usd: Optional[int]


class ReadTimedOut(DriverError):
    """A calendar read did not finish inside its deadline. Nothing is known about the calendar; the answer must be 'cannot confirm'."""


class ReadBudgetExhausted(DriverError):
    """The whole request has used its time budget, so no further calendar read is even started."""


MIN_READ_SECONDS = 1.5  # a read is not started with less than this left in the request budget: it could not finish


class ReadStillRunning(DriverError):
    """An earlier read that missed its deadline is still using the one browser. This request fails at once instead of queueing behind it."""


def _response(status: str, speak: str, **data: Any) -> dict:
    return {"status": status, "ok": status in SUCCESS_STATUSES, "speak": speak, **data}


class VoiceTools:
    def __init__(
        self,
        cfg: Config,
        driver: Any,
        service_factory: Callable[..., BookingService],
        clock: Callable[[], datetime],
        *,
        registry: Optional[BookableRegistry] = None,
        catalog: Catalog = DEFAULT_CATALOG,
        require_verified: bool = True,
        require_service_id: bool = False,
        availability_only: bool = False,
        booking_enabled: bool = False,
        option_ttl_seconds: int = 900,
        confirm_ttl_seconds: int = 300,
        search_days: int = 4,
        max_options: int = 3,
        horizon_days: int = 60,
        max_bookings_total: int = 3,
        lock_timeout_seconds: float = 20.0,
        read_deadline_seconds: Optional[float] = None,
        request_budget_seconds: Optional[float] = None,
        check_search_days: Optional[int] = None,
        read_cache_seconds: float = 0.0,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.cfg, self.driver, self._service_factory, self._clock = cfg, driver, service_factory, clock
        self.registry = registry if registry is not None else BookableRegistry.from_config(cfg)
        self.catalog, self._require_verified, self._require_service_id = catalog, require_verified, require_service_id
        self.availability_only = availability_only
        self.booking_enabled = booking_enabled and not availability_only  # availability-only can never write
        self._read_only = not self.booking_enabled  # no writer here: never invite a booking, never issue an id that could confirm one
        self._option_ttl = timedelta(seconds=option_ttl_seconds)
        self._confirm_ttl = timedelta(seconds=confirm_ttl_seconds)
        self._search_days, self._max_options, self._horizon_days = search_days, max_options, horizon_days
        self._max_bookings_total, self._lock_timeout = max_bookings_total, lock_timeout_seconds
        self._lock = threading.Lock()
        self._options: dict[str, Option] = {}
        self._pending: dict[str, tuple[str, datetime]] = {}  # call_id -> (option_id, expires)
        self._context: dict[str, tuple[str, Optional[str]]] = {}  # call_id -> (service_id, staff key asked for)
        self._booked_by_call: dict[str, tuple[str, Option, dict]] = {}  # call_id -> (option_id, option, response)
        self._booked_total = 0
        self._tz = ZoneInfo(cfg.timezone)
        # How many days check_slot looks at when the asked time is not open. 1 = answer from the day already read (no extra page loads);
        # find_alternatives is the wider search. Default: the same as search_days (the earlier, slower behaviour).
        self._check_days = max(1, check_search_days) if check_search_days is not None else search_days
        self._cache_seconds = max(0.0, float(read_cache_seconds))  # 0 = never reuse a read; read-only offers only, never the pre-save check
        self._day_cache: dict = {}
        self._mono = monotonic
        self._timer = PhaseTimer(monotonic)
        self._thread_state = threading.local()  # last_timing is per request thread: overlapping requests must not overwrite each other's log
        self.last_timing = {}
        # A hard bound on one calendar read (None = call the driver directly, as before). The read runs in a worker thread so a hung browser
        # page cannot hold the call; a read that misses the deadline is abandoned (its late result is never used or cached) and, while it still
        # occupies the browser, every new read fails immediately as ReadStillRunning.
        self._read_deadline = read_deadline_seconds if read_deadline_seconds is None else max(0.05, float(read_deadline_seconds))
        self._abandoned: Optional[threading.Thread] = None
        # The time budget of one WHOLE read-only request (lock wait + every day read + processing). It must sit below the voice platform's
        # tool timeout, so the server never keeps reading Booksy and holding the lock after the caller's tool has given up. None = unbounded.
        # None = the same as the per-read deadline (so a deadline alone can never let a multi-day search run for several deadlines in a row);
        # 0 = explicitly unbounded.
        if request_budget_seconds is None:
            self._request_budget = self._read_deadline
        elif request_budget_seconds == 0:
            self._request_budget = None
        else:
            self._request_budget = max(MIN_READ_SECONDS, float(request_budget_seconds))
        if self._request_budget is not None and self._read_deadline is None:
            self._read_deadline = self._request_budget  # a budget needs the bounded (worker-thread) read path to be enforceable
        # no read is started with less than this left: normally MIN_READ_SECONDS, but never more than half of a very small budget
        self._min_read = MIN_READ_SECONDS if self._request_budget is None else min(MIN_READ_SECONDS, self._request_budget / 2)

    # ---- helpers ---------------------------------------------------------------------------
    @property
    def last_timing(self) -> dict:
        return getattr(self._thread_state, "timing", {})

    @last_timing.setter
    def last_timing(self, value: dict) -> None:
        self._thread_state.timing = value

    def _budget_left(self) -> Optional[float]:
        """Seconds left in this request's budget (None when unbounded). Per request thread."""
        deadline = getattr(self._thread_state, "deadline", None)
        return None if deadline is None else deadline - self._mono()

    def _driver_read(self, day: date) -> DaySnapshot:
        if self._read_deadline is None:
            return self.driver.read_day(day)
        if self._abandoned is not None:
            if self._abandoned.is_alive():
                raise ReadStillRunning("an earlier calendar read is still running")
            self._abandoned = None
        left = self._budget_left()
        if left is not None and left < self._min_read:
            raise ReadBudgetExhausted("this request has used its time budget; no further calendar read is started")
        wait = self._read_deadline if left is None else min(self._read_deadline, left)
        outcome: dict = {}

        def work() -> None:
            try:
                outcome["snapshot"] = self.driver.read_day(day)
            except BaseException as exc:  # handed back to the requesting thread, never swallowed
                outcome["error"] = exc

        worker = threading.Thread(target=work, name="calendar-read", daemon=True)
        worker.start()
        worker.join(wait)
        if worker.is_alive():
            self._abandoned = worker
            raise ReadTimedOut("the calendar read missed its deadline")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["snapshot"]

    @staticmethod
    def _spec(service: BookableService) -> ServiceSpec:
        return ServiceSpec(service.booksy_name, service.duration_minutes, service.buffer_before_minutes, service.buffer_after_minutes)

    @staticmethod
    def _owner(call_id: str) -> str:
        """An opaque, stable token for one call. The raw call id is not stored in the ledger."""
        return "call-" + hashlib.sha256(call_id.encode("utf-8")).hexdigest()[:16]

    def _now(self) -> datetime:
        return self._clock().astimezone(self._tz)

    def _prune(self) -> None:
        now = self._now()
        self._options = {k: o for k, o in self._options.items() if now - o.created <= self._option_ttl}
        self._pending = {k: v for k, v in self._pending.items() if v[1] > now}

    def _price(self, service: BookableService) -> Optional[int]:
        if service.price_usd is not None:
            return service.price_usd
        item = self.catalog.get(service.catalog_item) if service.catalog_item else None
        return item.price_usd if item is not None else None

    def _issue(self, call_id: str, start: datetime, service: BookableService, staff: str) -> dict:
        option = Option("opt_" + secrets.token_urlsafe(6), call_id, start, self._now(), service, staff, self._price(service))
        view = {
            "label": f"{spoken_slot(start)} with {staff}",
            "start": start.isoformat(),
            "service_id": service.service_id,
            "service": service.booksy_name,
            "duration_minutes": service.duration_minutes,
            "price_usd": option.price_usd,
            "technician": staff,
        }
        if self._read_only:
            return view  # an availability answer, not an offer: no id is issued, so there is nothing to confirm or book
        self._options[option.option_id] = option
        return {"option_id": option.option_id, **view}

    def _with_lock(self, call_id: str, work: Callable[[], dict]) -> dict:
        if not isinstance(call_id, str) or not call_id.strip():
            return _response("needs_clarification", "I could not identify this call, so I can't check the calendar.", reason="no_call_id")
        timer = PhaseTimer(self._mono)
        self._thread_state.deadline = None if self._request_budget is None else self._mono() + self._request_budget
        with timer.phase("lock_wait"):
            acquired = self._lock.acquire(timeout=self._lock_timeout)
        if not acquired:
            self.last_timing = timer.as_dict()
            return _response("busy", "I'm still working on the previous request. Please give me a moment and I'll try again.")
        self._timer = timer
        try:
            self._prune()
            return work()
        finally:
            self.last_timing = timer.as_dict()
            self._lock.release()

    def _set_context(self, call_id: str, service: BookableService, staff: Optional[str]) -> None:
        """Remember what this call is asking about. A change of service or technician retires the call's earlier options and
        any half-given confirmation, so nothing offered for the OLD choice can be booked under the NEW one."""
        context = (service.service_id, staff_key(staff) if staff else None)
        if self._context.get(call_id) not in (None, context):
            self._options = {k: o for k, o in self._options.items() if o.call_id != call_id}
            self._pending.pop(call_id, None)
        self._context[call_id] = context

    def _read(self, day: date) -> tuple[DaySnapshot, float]:
        """(snapshot, age in seconds). A cached read is reused only while younger than read_cache_seconds, and only for READ-ONLY answers:
        booking re-reads through BookingService, which never sees this cache. Failures are never cached."""
        if self._cache_seconds:
            hit = self._day_cache.get(day)
            if hit is not None and self._mono() - hit[0] <= self._cache_seconds:
                self._timer.add("cache_hit", 0)  # logged as cache_hit, with no read= for it: warm latency is not Booksy latency
                return hit[1], self._mono() - hit[0]
        with self._timer.phase("read"):
            snapshot = self._driver_read(day)
        inner = getattr(self.driver, "last_read_ms", None)
        if isinstance(inner, dict):
            self._timer.merge(inner)
        if self._cache_seconds:
            if len(self._day_cache) >= 32:
                self._day_cache.pop(min(self._day_cache, key=lambda d: self._day_cache[d][0]))
            self._day_cache[day] = (self._mono(), snapshot)
        return snapshot, 0.0

    @staticmethod
    def _stamp_age(response: dict, age: float) -> dict:
        """A reply built from a reused read says how old it is. Under a few seconds it is simply current."""
        if age >= 1:
            response["as_of_seconds"] = int(age)
        if age >= 15:
            response["speak"] += f" (That was checked about {int(age)} seconds ago.)"
        return response

    @property
    def last_timing_text(self) -> str:
        """Phase names and durations of the latest request. Metadata only; safe to log."""
        text = " ".join(f"{name}={ms}ms" for name, ms in self.last_timing.items() if name != "total")
        return f"total={self.last_timing.get('total', 0)}ms" + (" " + text if text else "")

    # ---- choosing the service and technician --------------------------------------------------
    def _select(self, args: dict) -> tuple[Optional[BookableService], Optional[str], Optional[dict]]:
        """(service, staff name asked for or None, None) or (None, None, response). The service is a canonical id only."""
        raw = args.get("service_id")
        if raw is None or raw == "":
            # With require_service_id the model must always name the service; it can never fall back to a default silently.
            service = None if self._require_service_id else (self.registry.get(self.registry.default_id) if self.registry.default_id else None)
            if service is None:
                return None, None, _response("needs_clarification", "Which service would you like?", reason="service_required")
        else:
            service = self.registry.get(raw)
            if service is None:
                item = self.catalog.get(raw)
                if item is not None:
                    return None, None, self._not_bookable(item.name, item.item_id)
                resolution = self.catalog.resolve(raw) if isinstance(raw, str) else None
                candidates = [self._lite(i) for i in (resolution.items if resolution else ())][:5]
                return None, None, _response(
                    "invalid_service",
                    "I'm not sure which service you mean. Could you tell me the service you'd like?",
                    reason="unknown_service_id", candidates=candidates,
                )
        if self._require_verified and not service.verified:
            return None, None, self._not_bookable(service.booksy_name, service.service_id)
        staff = args.get("staff")
        if staff is not None and staff != "" and not isinstance(staff, str):
            return None, None, _response("needs_clarification", "Which technician did you have in mind?", reason="bad_staff")
        return service, (staff.strip() or None) if isinstance(staff, str) else None, None

    @staticmethod
    def _not_bookable(name: str, service_id: str) -> dict:
        return _response(
            "not_bookable",
            f"I can tell you about {name}, but I can't book it online yet. {CONTACT}",
            reason="no_verified_booking_mapping", service_id=service_id,
        )

    def _lite(self, item) -> dict:
        mapped = self.registry.for_catalog_item(item.item_id)
        return {
            "service_id": item.item_id, "name": item.name, "duration_minutes": item.duration_minutes, "price_usd": item.price_usd,
            **({"variant": descriptive_label(item)} if descriptive_label(item) else {}),
            "bookable": bool(mapped and (mapped.verified or not self._require_verified)),
            **({"bookable_service_id": mapped.service_id} if mapped and (mapped.verified or not self._require_verified) else {}),
        }

    def _eligible_staff(self, snapshot: DaySnapshot, service: BookableService, named: Optional[str]) -> tuple[list[str], Optional[dict]]:
        """Technicians who can do this service on this day's snapshot, or a response explaining why none can be used."""
        days = snapshot.staff_days
        keys = [staff_key(sd.staff) for sd in days]
        if len(set(keys)) != len(keys):  # two columns with one display name cannot be attributed; never guess
            return [], _response("unknown", "I couldn't tell the technicians' schedules apart clearly enough to check, so I won't guess.", reason="ambiguous_roster")
        eligible = [sd.staff for sd in days if service.eligible(sd.staff, sd.staff_id)]
        if named:
            wanted = staff_key(named)
            present = [sd.staff for sd in days if staff_key(sd.staff) == wanted]
            if not present:  # "Lily" may mean "Lily Chen": match whole words of the name, never a fragment
                words = set(wanted.split())
                present = [sd.staff for sd in days if words and words <= set(staff_key(sd.staff).split())]
            if len(present) > 1:
                return [], _response(
                    "needs_clarification", f"I see more than one {named} on the schedule: {join_choices(present)}. Which did you mean?",
                    reason="ambiguous_staff", candidates=present,
                )
            if not present:
                return [], _response(
                    "staff_unavailable", f"I don't see {named} on the schedule for that day.", reason="staff_not_on_schedule",
                    available_staff=eligible,
                )
            if not service.eligible(present[0], next(sd.staff_id for sd in days if sd.staff == present[0])):
                return [], _response(
                    "staff_unavailable", f"{present[0]} doesn't do {service.booksy_name}.", reason="staff_not_eligible", available_staff=eligible,
                )
            return present, None
        if not eligible:
            return [], _response(
                "staff_unavailable", f"I can't find anyone on the schedule who does {service.booksy_name}.", reason="no_eligible_staff",
            )
        return eligible, None

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
    def _why_not_one(self, snapshot: DaySnapshot, start: datetime, service: BookableService, staff: str) -> tuple[str, str, dict]:
        sd = snapshot.for_staff(staff)
        spec, now = self._spec(service), self._now()
        length = spoken_duration(spec.duration_minutes)
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
        needed_end = add_minutes(start, spec.duration_minutes + spec.buffer_after_minutes)
        if to_utc(needed_end) > to_utc(window.end):
            latest = add_minutes(window.end, -(spec.duration_minutes + spec.buffer_after_minutes)).astimezone(self._tz)
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

    def _why_not(self, snapshot: DaySnapshot, start: datetime, service: BookableService, staffs: list[str], named: Optional[str]) -> tuple[str, str, dict]:
        """(reason, spoken explanation, extra data) for a start time nobody eligible can take. Known facts only. When several
        technicians are considered, the reason closest to bookable is the most useful to give."""
        reasons = [self._why_not_one(snapshot, start, service, name) + (name,) for name in staffs]
        reason, text, extra, who = max(reasons, key=lambda r: REASON_RANK[r[0]])
        if named:  # a technician was asked for by name: speak about THEIR day, not the salon's
            def at(hhmm: str) -> str:
                hour, minute = (int(part) for part in hhmm.split(":"))
                return spoken_time(start.replace(hour=hour, minute=minute))

            if reason == "occupied":
                text = f"{who} is already booked at {spoken_time(start)} on {spoken_day(start)}."
            elif reason == "not_working":
                text = f"{who} isn't working on {spoken_day(start)}."
            elif reason == "outside_hours":
                text = f"{who} works from {at(extra['opens'])} to {at(extra['closes'])} on {spoken_day(start)}, so {spoken_time(start)} is outside those hours."
            elif reason == "ends_after_closing" and "latest_start" in extra:
                length = spoken_duration(service.duration_minutes)
                text = (
                    f"The {length} service starting at {spoken_time(start)} would run past the end of {who}'s day at {at(extra['closes'])}. "
                    f"The latest start that day is {at(extra['latest_start'])}."
                )
        return reason, text, extra

    # ---- finding alternatives --------------------------------------------------------------
    def _alternatives(
        self, first_day: date, preferred_minutes: int, first_snapshot: Optional[DaySnapshot], service: BookableService, named: Optional[str],
        days: Optional[int] = None,
    ) -> tuple[list[Slot], int, Optional[dict], float, bool]:
        """(chosen slots, number of days that could not be read, why the FIRST day's technician choice failed, if it did, the age in
        seconds of the OLDEST read this call itself made or reused, and whether the search was CUT SHORT by the request's time budget).
        Only slots find_slots returned from a real read."""
        chosen: list[Slot] = []
        oldest = 0.0  # the first day's snapshot, when given, was read by the caller, who knows its age
        unreadable = 0
        first_error: Optional[dict] = None
        spec = self._spec(service)
        total_days = self._search_days if days is None else days
        cut_short = False
        for offset in range(total_days):
            day = first_day + timedelta(days=offset)
            if offset == 0 and first_snapshot is not None:
                snapshot = first_snapshot
            else:
                try:
                    snapshot, age = self._read(day)
                except ReadBudgetExhausted:
                    cut_short = True  # this day and every later one were never checked; _offer says so instead of claiming nothing was found
                    break
                except DriverError:
                    unreadable += 1
                    continue
                oldest = max(oldest, age)
            staffs, error = self._eligible_staff(snapshot, service, named)
            if error is not None:
                if error["status"] == "unknown":
                    unreadable += 1
                if offset == 0:
                    first_error = error
                continue
            by_start: dict[datetime, Slot] = {}
            day_unknown = False
            for name in staffs:
                found = find_slots(
                    snapshot, spec, name, now=self._now(), grid_minutes=self.cfg.grid_minutes, min_lead_minutes=self.cfg.min_lead_minutes
                )
                if found.unknown:
                    day_unknown = True
                    continue
                for slot in found.slots:
                    by_start.setdefault(to_utc(slot.service.start), slot)  # the first eligible technician keeps a shared start
            slots = sorted(by_start.values(), key=lambda s: to_utc(s.service.start))
            if day_unknown and not slots:
                unreadable += 1
                continue
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
        return chosen[: self._max_options], unreadable, first_error, oldest, cut_short

    def _offer(
        self, call_id: str, slots: list[Slot], unreadable: int, service: BookableService, named: Optional[str], *, lead: str,
        days_searched: Optional[int] = None, cut_short: bool = False,
    ) -> dict:
        if not slots:
            if cut_short:
                return _response(
                    "unknown",
                    f"{lead}I ran out of time before I could check all of those days, so I can't tell you whether there is anything else. "
                    "Please try again in a moment, or contact the salon directly.",
                    options=[], reason="search_incomplete",
                )
            if unreadable:
                return _response(
                    "unknown",
                    f"{lead}I couldn't read the calendar clearly enough to offer other times. We can try again in a moment, or you can contact the salon directly.",
                    options=[],
                )
            who = f" for {named}" if named else ""
            searched = self._search_days if days_searched is None else days_searched
            scope = "that day" if searched == 1 else "in the next few days"
            if named:
                more = " Would you like me to check other technicians?"
            else:
                more = " Would you like me to look at other days?" if searched == 1 else " Would you like me to look further ahead?"
            return _response("no_alternatives", f"{lead}I don't see any other openings{who} {scope}.{more}", options=[], days_searched=searched)
        options = [self._issue(call_id, s.service.start.astimezone(self._tz), service, s.staff) for s in slots]
        labels = join_choices([o["label"] for o in options])
        partial = " I only had time to check part of the range." if cut_short else ""
        if self._read_only:
            return _response("alternatives", f"{lead}I do have {labels} open.{partial} {AVAILABILITY_ONLY_NOTE}", options=options, **({"search_incomplete": True} if cut_short else {}))
        return _response("alternatives", f"{lead}I do have {labels}.{partial} Which would you prefer?", options=options, **({"search_incomplete": True} if cut_short else {}))

    # ---- local information (never the calendar) ----------------------------------------------
    def lookup_service(self, call_id: str, args: dict) -> dict:
        """What the website says about a service, matched from the caller's words. No driver, no browser lock, no calendar."""
        args = args or {}
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return _response("needs_clarification", "Which service would you like to know about?", reason="no_query")
        resolution = self.catalog.resolve(query)
        if resolution.kind == "unknown":
            return _response(
                "unknown_service",
                "I don't have that on the services I can see. I don't want to guess, so please contact the salon directly about it.",
                items=[],
            )
        if resolution.kind == "ambiguous":
            families = list(dict.fromkeys(i.family for i in resolution.items))[:8]
            return _response(
                "choose_service", f"Several services fit that: {join_choices(families)}. Which one did you mean?",
                families=families, items=[self._lite(i) for i in resolution.items][:8],
            )
        if resolution.kind == "variants":
            parts = [
                (f"{descriptive_label(i)}: " if descriptive_label(i) else "")
                + f"{spoken_duration(i.duration_minutes) if i.duration_minutes else 'no listed length'} for {spoken_price(i.price_usd)}"
                for i in resolution.items
            ]
            family = resolution.items[0].family
            prefix = f"I don't see that length for {family}. " if resolution.hint_unmatched else ""
            return _response(
                "choose_variant", f"{prefix}{family} comes as {join_choices(parts)}. Which length would you like?",
                family=family, items=[self._lite(i) for i in resolution.items],
            )
        item = resolution.items[0]
        mapped = self.registry.for_catalog_item(item.item_id)
        can_book = bool(mapped and (mapped.verified or not self._require_verified))
        length = spoken_duration(item.duration_minutes) if item.duration_minutes else "no listed length"
        note = f" Note: {item.notes}" if item.notes else ""
        booking = "I can check availability for it." if can_book else "I can tell you about it, but I can't book it online yet."
        return _response(
            "service_info",
            f"{item.name}: {length}, {spoken_price(item.price_usd)} as listed on our website ({item.retrieved_on}). {item.description} {booking}{note}",
            item=item.to_public(), bookable=can_book, **({"bookable_service_id": mapped.service_id} if can_book else {}),
        )

    # ---- the calendar functions -----------------------------------------------------------------
    def check_slot(self, call_id: str, args: dict) -> dict:
        return self._with_lock(call_id, lambda: self._check_slot(call_id, args or {}))

    def _check_slot(self, call_id: str, args: dict) -> dict:
        service, named, problem = self._select(args)
        if problem:
            return problem
        day, start, problem = self._parse_when(args, need_time=True)
        if problem:
            return problem
        try:
            snapshot, age = self._read(day)
        except SignInRequired:
            return _response("system_unavailable", "The scheduling system needs attention right now, so I can't check availability. Please contact the salon directly.")
        except (ReadTimedOut, ReadStillRunning, ReadBudgetExhausted):
            return _response(
                "unknown",
                "Checking the calendar is taking longer than it should, so I can't confirm that time right now. "
                "Please try again in a moment, or contact the salon directly.",
                reason="read_timeout",
            )
        except DriverError:
            return _response("unknown", "I couldn't read the calendar clearly, so I can't tell you whether that time is free. Let me not guess.")
        staffs, problem = self._eligible_staff(snapshot, service, named)
        if problem:
            return problem
        self._set_context(call_id, service, named)
        spec = self._spec(service)
        free, unknown = [], False
        for name in staffs:
            verdict = validate_slot(snapshot, spec, name, start, now=self._now(), min_lead_minutes=self.cfg.min_lead_minutes)
            if verdict.unknown:
                unknown = True
            elif verdict.ok:
                free.append(name)
        length = spoken_duration(service.duration_minutes)
        if free:
            option = self._issue(call_id, start, service, free[0])
            if self._read_only:
                return self._stamp_age(_response(
                    "available",
                    f"{spoken_slot(start)} is open for the full {length} of {service.booksy_name}, with {free[0]}. {AVAILABILITY_ONLY_NOTE}",
                    options=[option],
                ), age)
            return self._stamp_age(_response(
                "available",
                f"{spoken_slot(start)} is open for the full {length} of {service.booksy_name}, with {free[0]}. Would you like me to book it?",
                options=[option],
            ), age)
        if unknown:
            return _response("unknown", "I couldn't confirm the calendar for that day, so I can't tell you whether it's free. Let me not guess.")
        with self._timer.phase("search"):
            reason, explanation, extra = self._why_not(snapshot, start, service, staffs, named)
            slots, unreadable, _error, alt_age, cut_short = self._alternatives(
                day, start.hour * 60 + start.minute, snapshot, service, named, days=self._check_days
            )
            offered = self._offer(call_id, slots, unreadable, service, named, lead=explanation + " ", days_searched=self._check_days, cut_short=cut_short)
        offered.update(extra)
        offered["unavailable_reason"] = reason
        offered["requested"] = start.isoformat()
        return self._stamp_age(offered, max(age, alt_age))

    def find_alternatives(self, call_id: str, args: dict) -> dict:
        return self._with_lock(call_id, lambda: self._find_alternatives(call_id, args or {}))

    def _find_alternatives(self, call_id: str, args: dict) -> dict:
        service, named, problem = self._select(args)
        if problem:
            return problem
        day, start, problem = self._parse_when(args, need_time=False)
        if problem:
            return problem
        self._set_context(call_id, service, named)
        preferred = (start.hour * 60 + start.minute) if start else 10 * 60
        slots, unreadable, first_error, oldest, cut_short = self._alternatives(day, preferred, None, service, named)
        if not slots and first_error is not None and first_error["status"] == "staff_unavailable":
            return self._stamp_age(first_error, oldest)
        return self._stamp_age(self._offer(call_id, slots, unreadable, service, named, lead="", cut_short=cut_short), oldest)

    # ---- the one writer ---------------------------------------------------------------------------
    def book_slot(self, call_id: str, args: dict) -> dict:
        return self._with_lock(call_id, lambda: self._book_slot(call_id, args or {}))

    def _book_slot(self, call_id: str, args: dict) -> dict:
        if self.availability_only:  # first, before any lookup: this line cannot write, whatever the caller or model says
            return _response(
                "refused",
                "I can only check availability on this line, so I can't book, hold, change or cancel anything. Please contact the salon directly to book.",
                reason="availability_only",
            )
        option = self._options.get(str(args.get("option_id") or ""))
        earlier = self._booked_by_call.get(call_id)
        if earlier is not None:
            earlier_option, earlier_record, result = earlier
            if str(args.get("option_id") or "") == earlier_option:
                if result["status"] in SUCCESS_STATUSES:
                    return self._reconfirm(earlier_record, result)  # a repeat may restate success only if it is STILL true
                return dict(result)  # an unknown outcome stays unknown for the rest of the call
            if result["status"] in SUCCESS_STATUSES:
                return _response(
                    "refused",
                    "I made one booking earlier in this call, and I can only make one per call. Please contact the salon directly for anything else.",
                    reason="one_booking_per_call",
                )
            return _response(
                "refused",
                "I couldn't confirm what happened with the earlier booking attempt in this call, so I won't start another. "
                "Please don't treat anything as booked, and contact the salon directly to check.",
                reason="earlier_outcome_unknown",
            )
        if not self.booking_enabled:
            return _response("refused", "Booking isn't switched on for this line right now, so I can't book. Please contact the salon directly.", reason="booking_disabled")
        if option is None or option.call_id != call_id:
            return _response(
                "invalid_option",
                "I need to check availability again before I can book. Let me look again.",
                reason="unknown_or_expired_option",
            )
        # an option IS its service and technician: a caller or model cannot change either by passing other values
        asked_service, asked_staff = args.get("service_id"), args.get("staff")
        if (asked_service not in (None, "") and asked_service != option.service.service_id) or (
            asked_staff not in (None, "") and (not isinstance(asked_staff, str) or staff_key(asked_staff) != staff_key(option.staff))
        ):
            return _response(
                "invalid_option",
                "That doesn't match what I offered. Let me check availability again for what you'd like.",
                reason="option_mismatch",
            )
        if self._booked_total >= self._max_bookings_total:
            return _response("refused", "This test line has reached its booking limit, so I can't book. Please contact the salon directly.", reason="session_limit")

        pending = self._pending.get(call_id)
        confirmed = args.get("confirmed") is True  # exactly the boolean true; "yes", 1 and "true" do not count
        if not (confirmed and pending is not None and pending[0] == option.option_id):
            self._pending[call_id] = (option.option_id, self._now() + self._confirm_ttl)
            length = spoken_duration(option.service.duration_minutes)
            price = f" The listed price is {spoken_price(option.price_usd)}." if option.price_usd is not None else ""
            return _response(
                "confirmation_required",
                f"To confirm: {option.service.booksy_name}, {length}, with {option.staff}, on {spoken_slot(option.start)}.{price} Shall I book it?",
                option_id=option.option_id, service_id=option.service.service_id, service=option.service.booksy_name,
                duration_minutes=option.service.duration_minutes, technician=option.staff, price_usd=option.price_usd, start=option.start.isoformat(),
                instruction="Read this back, wait for the caller's clear yes, then call book_slot again with the same option_id and confirmed=true.",
            )

        makes = self._service_maker(option)
        if makes is None:
            return _response("refused", "I can't book that service with that technician yet, so nothing was booked. " + CONTACT, reason="no_booking_path")
        try:
            result = makes().book(option.start, owner=self._owner(call_id))
        except Exception:  # an unforeseen failure after the Save click could have saved; never claim either way
            return self._remember(call_id, option, _response("needs_review", "Something went wrong and I can't confirm whether the booking went through. Please don't rely on it, and contact the salon directly to check.", reason="exception"))
        return self._remember_or_return(call_id, option, result)

    def _service_maker(self, option: Option) -> Optional[Callable[[], BookingService]]:
        """A function that builds the BookingService for exactly this option's service and technician, or None if the
        configured factory cannot serve it (nothing is attempted then)."""
        bc = option.service.booking_config(self.cfg, option.staff)
        try:
            takes_config = bool(inspect.signature(self._service_factory).parameters)
        except (TypeError, ValueError):
            takes_config = False
        if takes_config:
            return lambda: self._service_factory(bc)
        same = (
            bc.service_name == self.cfg.service_name and bc.service_duration_minutes == self.cfg.service_duration_minutes
            and bc.buffer_before_minutes == self.cfg.buffer_before_minutes and bc.buffer_after_minutes == self.cfg.buffer_after_minutes
            and staff_key(bc.staff_name) == staff_key(self.cfg.staff_name)
        )
        return self._service_factory if same else None

    def _reconfirm(self, option: Option, stored: dict) -> dict:
        """A repeat of a successful booking: re-run the READ-ONLY verifier on the current calendar before repeating the
        success. If the booking was cancelled, deleted, moved, duplicated, conflicted, or the calendar can't be read,
        say so honestly. Nothing is ever recreated."""
        try:
            makes = self._service_maker(option)
            verdict = makes().verify(option.start) if makes is not None else None
        except Exception:
            verdict = None
        if verdict is not None and verdict.status is Status.BOOKED_VERIFIED:
            return dict(stored)
        return _response(
            "needs_review",
            "I made that booking earlier in this call, but when I checked the calendar again I can't confirm it is still there as booked. "
            "I haven't made another one. Please don't rely on it, and contact the salon directly to check.",
            reason="reverification_failed",
            reference=stored.get("reference"),
        )

    def _remember(self, call_id: str, option: Option, response: dict) -> dict:
        self._booked_by_call[call_id] = (option.option_id, option, response)
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
        length = spoken_duration(option.service.duration_minutes)
        what = f"{option.service.booksy_name}, {length}, with {option.staff}"
        status = result.status
        ref = {"reference": result.ref}
        if status is Status.BOOKED_VERIFIED:
            return _response("booked_verified", f"You're booked: {what}, on {when}. I checked the calendar and it's there.", **ref)
        if status is Status.ALREADY_BOOKED:
            return _response("already_booked", f"That time is already booked for you: {what}, on {when}. I won't book it twice.", **ref)
        if status is Status.SLOT_UNAVAILABLE:
            return _response("unavailable", f"I'm sorry, {when} is no longer available, so nothing was booked. Shall I look for other times?", reason="changed_before_save", **ref)
        if status is Status.UNKNOWN_AVAILABILITY:
            return _response("unknown", "I couldn't confirm the calendar just now, so I haven't booked anything. Let me not guess.", **ref)
        if status is Status.SIGN_IN_REQUIRED:
            return _response("system_unavailable", "The scheduling system needs attention right now, so I haven't booked anything. Please contact the salon directly.", **ref)
        if status is Status.REJECTED_BY_SAFETY:
            return _response("refused", "I'm not able to book that one, so nothing was booked. Please contact the salon directly.", reason="safety_check", **ref)
        if status is Status.NOT_SAVED:
            return _response("not_saved", "The booking didn't go through, and nothing was saved. Shall I try again?", **ref)
        # uncertain / mismatch / conflict: something may exist but it cannot be confirmed as the requested booking
        return _response(
            "needs_review",
            "I can't confirm that booking. Something may or may not have been saved, so please don't treat it as booked. Please contact the salon directly to check.",
            **ref,
        )
