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
    def _find_by_ref(snapshot: DaySnapshot, staff: str, ref: str) -> tuple[Optional[Appointment], bool]:
        """(appointment or None, appointments_were_readable)."""
        staff_day = snapshot.for_staff(staff)
        if staff_day is None or staff_day.appointments is None:
            return None, False
        for appt in staff_day.appointments:
            if extract_ref(appt.note) == ref:
                return appt, True
        return None, True

    @staticmethod
    def _mismatch(found: Appointment, spec: AppointmentSpec) -> list[str]:
        problems = []
        if to_utc(found.interval.start) != to_utc(spec.start):
            problems.append(f"start {fmt(found.interval.start)} != expected {fmt(spec.start)}")
        if found.interval.minutes != spec.duration_minutes:
            problems.append(f"duration {found.interval.minutes} != expected {spec.duration_minutes} min")
        if found.service and found.service != spec.service_name:
            problems.append("service differs from the expected test service")
        if found.staff and found.staff.casefold() != spec.staff.casefold():
            problems.append("staff differs from the expected test staff member")
        return problems

    @staticmethod
    def _overlaps_with_others(snapshot: DaySnapshot, staff: str, found: Appointment, ref: str) -> list[str]:
        staff_day = snapshot.for_staff(staff)
        if staff_day is None:
            return []
        return [
            f"overlaps another appointment {appt.interval.label()}"
            for appt in staff_day.appointments or ()
            if appt.blocks_time and extract_ref(appt.note) != ref and appt.interval.overlaps(found.interval)
        ]

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

    # ---- internals -----------------------------------------------------------------------
    def _handle_existing(self, entry, spec: AppointmentSpec, ref: str, key: str) -> Optional[BookingResult]:
        """Decide what a previous ledger entry for the same slot means. None = safe to proceed."""
        if entry.state == State.FAILED:
            return None  # known not saved; a new attempt is allowed

        try:
            snapshot = self.driver.read_day(spec.start.date())
        except DriverError as exc:
            if entry.state == State.VERIFIED:
                return BookingResult(
                    Status.ALREADY_BOOKED, "ledger records this slot as booked and verified; calendar not readable now", ref,
                    (f"calendar unreadable: {exc}",),
                )
            return BookingResult(
                Status.UNCERTAIN_NEEDS_REVIEW,
                "an earlier save for this slot is unresolved and the calendar cannot be read; not retrying",
                ref,
                (str(exc),),
            )

        found, readable = self._find_by_ref(snapshot, spec.staff, ref)
        if found is not None:
            problems = self._mismatch(found, spec)
            if problems:
                self.ledger.set_state(key, State.UNCERTAIN, "calendar entry differs: " + "; ".join(problems))
                return BookingResult(Status.VERIFY_MISMATCH, "an entry with this reference exists but differs", ref, tuple(problems))
            self.ledger.set_state(key, State.VERIFIED, "found on calendar by reference")
            return BookingResult(Status.ALREADY_BOOKED, "this slot is already booked (found on the calendar); not creating another", ref)

        if not readable:
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
                snapshot = self.driver.read_day(spec.start.date())
            except DriverError as exc:
                problems_seen.append(f"read-back {attempt + 1}: calendar unreadable ({exc})")
                continue
            found, readable = self._find_by_ref(snapshot, spec.staff, ref)
            if not readable:
                problems_seen.append(f"read-back {attempt + 1}: appointments unreadable")
                continue
            if found is None:
                problems_seen.append(f"read-back {attempt + 1}: not visible yet")
                continue

            mismatch = self._mismatch(found, spec)
            if mismatch:
                self.ledger.set_state(key, State.UNCERTAIN, "read-back mismatch: " + "; ".join(mismatch))
                return BookingResult(Status.VERIFY_MISMATCH, "saved entry does not match what was requested", ref, tuple(mismatch))

            self.ledger.set_state(key, State.VERIFIED, note or "verified by read-back")
            self._booked_this_run += 1
            clashes = self._overlaps_with_others(snapshot, spec.staff, found, ref)
            if clashes:
                return BookingResult(
                    Status.BOOKED_CONFLICT_DETECTED,
                    "booked and verified, BUT it overlaps another appointment (created concurrently?). Nothing was changed automatically.",
                    ref,
                    tuple(clashes),
                )
            return BookingResult(Status.BOOKED_VERIFIED, note or "booked and verified by read-back", ref)

        self.ledger.set_state(key, State.UNCERTAIN, "not confirmed by read-back")
        return BookingResult(
            Status.UNCERTAIN_NEEDS_REVIEW,
            "could not confirm the appointment on the calendar; it may or may not exist. Not retrying automatically.",
            ref,
            tuple(problems_seen),
        )
