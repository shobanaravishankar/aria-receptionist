"""Orchestrates one safe booking: check, re-check, record intent, save, read back, reconcile.

What this does NOT promise: atomic conflict prevention. A browser cannot lock the calendar, so a
colleague could add an appointment between our final re-check and the Save click. We narrow that
window (fresh read right before saving), and we DETECT it afterwards (read-back overlap check), but
we never claim it cannot happen, and we never delete anything automatically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Callable, Optional

from .availability import SlotSearch, find_slots, validate_slot
from .config import Config
from .driver import (
    BeforeSaveError,
    BookingDriver,
    DriverError,
    SaveOutcomeUnknown,
    SignInRequired,
)
from .ledger import Ledger, State, ref_from_key, request_key
from .models import Appointment, AppointmentSpec, DaySnapshot, ServiceSpec, fmt, to_utc
from .safety import SafetyViolation, build_note, check_request, extract_ref, run_limit_reason


class Status(str, Enum):
    BOOKED_VERIFIED = "booked_verified"
    BOOKED_CONFLICT_DETECTED = "booked_conflict_detected"
    ALREADY_BOOKED = "already_booked"
    SLOT_UNAVAILABLE = "slot_unavailable"
    UNKNOWN_AVAILABILITY = "unknown_availability"
    REJECTED_BY_SAFETY = "rejected_by_safety"
    SIGN_IN_REQUIRED = "sign_in_required"
    NOT_SAVED = "not_saved"
    UNCERTAIN_NEEDS_REVIEW = "uncertain_needs_review"
    VERIFY_MISMATCH = "verify_mismatch"


@dataclass(frozen=True)
class BookingResult:
    status: Status
    message: str
    ref: Optional[str] = None
    details: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True only when exactly one verified appointment exists for the request."""
        return self.status in (Status.BOOKED_VERIFIED, Status.ALREADY_BOOKED)


@dataclass(frozen=True)
class Assessment:
    """kind: unreadable | absent | duplicate | inactive | identity_unknown | mismatch | conflict | ok"""

    kind: str
    found: Optional[Appointment]
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class DaySearch:
    day: date
    search: SlotSearch


class BookingService:
    def __init__(
        self,
        cfg: Config,
        driver: BookingDriver,
        ledger: Ledger,
        *,
        clock: Callable[[], datetime],
        sleep: Callable[[float], None] = time.sleep,
        readback_attempts: int = 3,
        readback_delay_seconds: float = 2.0,
    ):
        self.cfg = cfg
        self.driver = driver
        self.ledger = ledger
        self.clock = clock
        self.sleep = sleep
        self.readback_attempts = max(1, readback_attempts)
        self.readback_delay_seconds = readback_delay_seconds
        self._booked_this_run = 0

    # ---- helpers -------------------------------------------------------------------------
    @property
    def service(self) -> ServiceSpec:
        c = self.cfg
        return ServiceSpec(c.service_name, c.service_duration_minutes, c.buffer_before_minutes, c.buffer_after_minutes)

    def _spec(self, start: datetime, ref: str) -> AppointmentSpec:
        c = self.cfg
        return AppointmentSpec(c.staff_name, c.service_name, start, c.service_duration_minutes, build_note(ref))

    @staticmethod
    def _assess(snapshot: DaySnapshot, spec: AppointmentSpec, ref: str) -> "Assessment":
        """The ONE judgement used by booking, retry and verify: what does today's calendar say about this reference?

        Success (OK) needs exactly one record carrying the reference, positively active, with its service and staff
        shown and equal to the request, the requested start and length, and no overlap with another active
        appointment. Everything else is an explicit non-success; a record is never accepted on missing data."""
        staff_day = snapshot.for_staff(spec.staff)
        if staff_day is None or staff_day.appointments is None:
            return Assessment("unreadable", None, ("the day's appointments could not be read",))
        matches = [a for a in staff_day.appointments if extract_ref(a.note) == ref]
        if not matches:
            return Assessment("absent", None)
        if len(matches) > 1:
            return Assessment("duplicate", None, (f"{len(matches)} records on the calendar carry this reference; expected exactly one",))
        found = matches[0]
        if not found.blocks_time:
            return Assessment("inactive", found, ("the record carrying this reference is not an active booking (cancelled or no-show?)",))
        missing = []
        if not found.service.strip():
            missing.append("the record does not show its service")
        if not found.staff.strip():
            missing.append("the record does not show its staff member")
        if missing:
            return Assessment("identity_unknown", found, tuple(missing))
        problems = []
        if to_utc(found.interval.start) != to_utc(spec.start):
            problems.append(f"start {fmt(found.interval.start)} != expected {fmt(spec.start)}")
        if found.interval.minutes != spec.duration_minutes:
            problems.append(f"duration {found.interval.minutes} != expected {spec.duration_minutes} min")
        if found.service.strip().casefold() != spec.service_name.strip().casefold():
            problems.append("service differs from the expected test service")
        if found.staff.strip().casefold() != spec.staff.strip().casefold():
            problems.append("staff differs from the expected test staff member")
        if problems:
            return Assessment("mismatch", found, tuple(problems))
        clashes = tuple(
            f"overlaps another appointment {appt.interval.label()}"
            for appt in staff_day.appointments
            if appt is not found and appt.blocks_time and appt.interval.overlaps(found.interval)
        )
        if clashes:
            return Assessment("conflict", found, clashes)
        return Assessment("ok", found)

    def _conclude(
        self, assessment: "Assessment", key: str, ref: str, *, ok_status: Status, ok_message: str, ok_detail: str
    ) -> Optional[BookingResult]:
        """Turn an assessment of a record that EXISTS into a ledger update and a result. None = nothing found/readable."""
        kind, details = assessment.kind, assessment.details
        if kind in ("unreadable", "absent"):
            return None
        if kind == "ok":
            self.ledger.set_state(key, State.VERIFIED, ok_detail)
            return BookingResult(ok_status, ok_message, ref)
        # Anything else is never a clean success, and the ledger must not say verified while it is true.
        self.ledger.set_state(key, State.UNCERTAIN, f"{kind}: " + "; ".join(details))
        if kind == "conflict":
            return BookingResult(
                Status.BOOKED_CONFLICT_DETECTED,
                "found, BUT it overlaps another appointment. Nothing was changed automatically; this stays unresolved until a person looks.",
                ref,
                details,
            )
        if kind == "identity_unknown":
            return BookingResult(Status.UNCERTAIN_NEEDS_REVIEW, "a record with this reference exists but its identity cannot be verified", ref, details)
        message = {
            "duplicate": "more than one appointment carries this reference",
            "inactive": "the saved record is not an active booking",
            "mismatch": "an entry with this reference exists but differs from the request",
        }[kind]
        return BookingResult(Status.VERIFY_MISMATCH, message, ref, details)

    # ---- search --------------------------------------------------------------------------
    def search(self, first_day: date, days: int) -> list[DaySearch]:
        results = []
        for offset in range(days):
            day = first_day + timedelta(days=offset)
            try:
                snapshot = self.driver.read_day(day)
            except DriverError as exc:
                results.append(DaySearch(day, SlotSearch((), unknown=(f"calendar unreadable: {exc}",))))
                continue
            results.append(
                DaySearch(
                    day,
                    find_slots(
                        snapshot,
                        self.service,
                        self.cfg.staff_name,
                        now=self.clock(),
                        grid_minutes=self.cfg.grid_minutes,
                        min_lead_minutes=self.cfg.min_lead_minutes,
                    ),
                )
            )
        return results

    # ---- booking -------------------------------------------------------------------------
    def book(self, start: datetime) -> BookingResult:
        cfg = self.cfg
        key = request_key(cfg.business_id, cfg.staff_name, cfg.service_name, start, cfg.service_duration_minutes)
        ref = ref_from_key(key)
        spec = self._spec(start, ref)

        # 1. Safety: right business, future slot, marked as a test, run limit.
        try:
            observed = self.driver.verify_business()
        except SignInRequired as exc:
            return BookingResult(Status.SIGN_IN_REQUIRED, f"sign-in required: {exc}", ref)
        except DriverError as exc:
            return BookingResult(Status.UNKNOWN_AVAILABILITY, f"could not verify the signed-in business: {exc}", ref)
        try:
            # The run limit is applied later, after duplicate detection: a duplicate creates nothing.
            check_request(cfg, spec, now=self.clock(), observed_business_id=observed, bookings_this_run=0)
        except SafetyViolation as violation:
            return BookingResult(Status.REJECTED_BY_SAFETY, "refused by safety checks", ref, tuple(violation.reasons))

        with self.ledger.locked():
            existing = self.ledger.get(key)
            if existing is not None:
                early = self._handle_existing(existing, spec, ref, key)
                if early is not None:
                    return early

            limit = run_limit_reason(cfg, self._booked_this_run)
            if limit:
                return BookingResult(Status.REJECTED_BY_SAFETY, "refused by safety checks", ref, (limit,))

            # 2. Fresh read and validation immediately before saving.
            try:
                snapshot = self.driver.read_day(start.date())
            except SignInRequired as exc:
                return BookingResult(Status.SIGN_IN_REQUIRED, f"sign-in required: {exc}", ref)
            except DriverError as exc:
                return BookingResult(Status.UNKNOWN_AVAILABILITY, f"calendar unreadable: {exc}", ref)
            check = validate_slot(
                snapshot, self.service, cfg.staff_name, start, now=self.clock(), min_lead_minutes=cfg.min_lead_minutes
            )
            if check.unknown:
                return BookingResult(
                    Status.UNKNOWN_AVAILABILITY, "availability could not be established; unknown is not free", ref, check.unknown
                )
            if not check.ok:
                return BookingResult(Status.SLOT_UNAVAILABLE, "slot is not available", ref, check.reasons)

            # 3. Record intent BEFORE acting, so a crash leaves evidence.
            end = spec.interval.end
            self.ledger.create(key, ref, cfg.staff_name, cfg.service_name, start.isoformat(), end.isoformat(), State.SAVING)

            # 4. Save.
            try:
                self.driver.create_appointment(spec)
            except BeforeSaveError as exc:
                self.ledger.set_state(key, State.FAILED, f"not saved: {exc}")
                return BookingResult(Status.NOT_SAVED, f"the appointment was not saved: {exc}", ref)
            except SaveOutcomeUnknown as exc:
                self.ledger.set_state(key, State.UNCERTAIN, f"save outcome unknown: {exc}")
                return self._read_back(spec, ref, key, note=f"save outcome unknown ({exc}); checked the calendar")
            except DriverError as exc:  # unclassified: treat as uncertain, never as "not saved"
                self.ledger.set_state(key, State.UNCERTAIN, f"unclassified driver error: {exc}")
                return self._read_back(spec, ref, key, note=f"driver error ({exc}); checked the calendar")

            # 5. Read back and verify.
            return self._read_back(spec, ref, key, note="")

    def verify(self, start: datetime) -> BookingResult:
        """READ-ONLY: find the appointment recorded for this slot on the calendar by its reference and check it.
        Creates nothing. An absent entry is reported (never turned into 'failed'); a person decides."""
        cfg = self.cfg
        key = request_key(cfg.business_id, cfg.staff_name, cfg.service_name, start, cfg.service_duration_minutes)
        ref = ref_from_key(key)
        spec = self._spec(start, ref)
        with self.ledger.locked():
            if self.ledger.get(key) is None:
                return BookingResult(Status.UNCERTAIN_NEEDS_REVIEW, "the ledger has no record of this slot; nothing to verify", ref)
            try:
                snapshot = self.driver.read_day(start.date(), include_notes=True)
            except SignInRequired as exc:
                return BookingResult(Status.SIGN_IN_REQUIRED, f"sign-in required: {exc}", ref)
            except DriverError as exc:
                return BookingResult(Status.UNCERTAIN_NEEDS_REVIEW, f"calendar unreadable: {exc}", ref)
            assessment = self._assess(snapshot, spec, ref)
            if assessment.kind == "unreadable":
                return BookingResult(Status.UNCERTAIN_NEEDS_REVIEW, "the day's appointments could not be read", ref)
            if assessment.kind == "absent":
                return BookingResult(
                    Status.UNCERTAIN_NEEDS_REVIEW,
                    "no appointment with this reference was found. Its note may be unreadable, or it may not exist; "
                    "the ledger was left unchanged and nothing was retried.",
                    ref,
                )
            return self._conclude(
                assessment, key, ref,
                ok_status=Status.BOOKED_VERIFIED,
                ok_message="found on the calendar by its reference and it matches the request",
                ok_detail="verified by reading the saved note back (verify command)",
            )

    # ---- internals -----------------------------------------------------------------------
    def _handle_existing(self, entry, spec: AppointmentSpec, ref: str, key: str) -> Optional[BookingResult]:
        """Decide what a previous ledger entry for the same slot means. None = safe to proceed."""
        if entry.state == State.FAILED:
            return None  # known not saved; a new attempt is allowed

        try:
            snapshot = self.driver.read_day(spec.start.date(), include_notes=True)
        except DriverError as exc:
            if entry.state == State.VERIFIED:
                # An old verification says nothing about TODAY's calendar. Suppress a duplicate, but do not
                # report current success.
                return BookingResult(
                    Status.UNCERTAIN_NEEDS_REVIEW,
                    "the ledger recorded this slot as verified earlier, but the calendar cannot be read now, so its CURRENT state is "
                    "unknown. Not creating another; not confirming this one.",
                    ref,
                    (f"calendar unreadable: {exc}",),
                )
            return BookingResult(
                Status.UNCERTAIN_NEEDS_REVIEW,
                "an earlier save for this slot is unresolved and the calendar cannot be read; not retrying",
                ref,
                (str(exc),),
            )

        assessment = self._assess(snapshot, spec, ref)
        concluded = self._conclude(
            assessment, key, ref,
            ok_status=Status.ALREADY_BOOKED,
            ok_message="this slot is already booked (found on the calendar); not creating another",
            ok_detail="found on calendar by reference",
        )
        if concluded is not None:
            return concluded

        if assessment.kind == "unreadable":
            return BookingResult(
                Status.UNCERTAIN_NEEDS_REVIEW,
                "an earlier save for this slot is unresolved and existing appointments cannot be read; not retrying",
                ref,
            )

        # Calendar readable and the reference is absent.
        if entry.state == State.VERIFIED:
            return BookingResult(
                Status.UNCERTAIN_NEEDS_REVIEW,
                "ledger says this was booked but it is not on the calendar (cancelled or moved?); not re-booking automatically",
                ref,
            )
        age = to_utc(self.clock()) - to_utc(datetime.fromisoformat(entry.updated_at))
        if age < timedelta(seconds=self.cfg.settle_seconds):
            return BookingResult(
                Status.UNCERTAIN_NEEDS_REVIEW,
                f"an earlier save was attempted {int(age.total_seconds())}s ago; wait {self.cfg.settle_seconds}s and check again",
                ref,
            )
        self.ledger.set_state(key, State.FAILED, "proven absent from the calendar after an uncertain save")
        return None

    def _read_back(self, spec: AppointmentSpec, ref: str, key: str, *, note: str) -> BookingResult:
        problems_seen: list[str] = []
        for attempt in range(self.readback_attempts):
            if attempt:
                self.sleep(self.readback_delay_seconds)
            try:
                snapshot = self.driver.read_day(spec.start.date(), include_notes=True)
            except DriverError as exc:
                problems_seen.append(f"read-back {attempt + 1}: calendar unreadable ({exc})")
                continue
            assessment = self._assess(snapshot, spec, ref)
            if assessment.kind == "unreadable":
                problems_seen.append(f"read-back {attempt + 1}: appointments unreadable")
                continue
            if assessment.kind == "absent":
                problems_seen.append(f"read-back {attempt + 1}: not visible yet")
                continue

            self._booked_this_run += 1  # something carrying our reference exists, whatever its condition
            return self._conclude(
                assessment, key, ref,
                ok_status=Status.BOOKED_VERIFIED,
                ok_message=note or "booked and verified by read-back",
                ok_detail=note or "verified by read-back",
            )

        self.ledger.set_state(key, State.UNCERTAIN, "not confirmed by read-back")
        return BookingResult(
            Status.UNCERTAIN_NEEDS_REVIEW,
            "could not confirm the appointment on the calendar; it may or may not exist. Not retrying automatically.",
            ref,
            tuple(problems_seen),
        )
