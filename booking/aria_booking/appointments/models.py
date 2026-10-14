"""Plain data types for the simulated appointment store. No I/O here.

Everything in this package is the DEMO's own bookkeeping over invented clients: it is never wired to the Booksy driver, and a stored appointment is a
row in a local file, not an entry in anyone's real calendar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

UTC = timezone.utc

CONFIRMED, CANCELLED, COMPLETED, NO_SHOW = "confirmed", "cancelled", "completed", "no_show"
STATUSES = (CONFIRMED, CANCELLED, COMPLETED, NO_SHOW)

# Where a record came from. Only "seed" rows exist before anyone books anything; "voice" rows are made by a confirmed tool call.
ORIGIN_SEED, ORIGIN_VOICE, ORIGIN_TEST = "seed", "voice", "test"


def from_epoch(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)


def to_epoch(moment: datetime) -> int:
    if moment.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    return int(moment.astimezone(UTC).timestamp())


def name_key(name: str) -> str:
    """How two spellings of one name compare: case and runs of whitespace do not matter, nothing else is guessed."""
    return " ".join((name or "").casefold().split())


@dataclass(frozen=True)
class ClientRecord:
    client_id: int
    name: str
    phone: str  # normalised "+<digits>": identifies a DEMO record, not a person (the demo's policy, not production authentication)


@dataclass(frozen=True)
class StoredAppointment:
    appt_id: int
    reference: str  # the stable, spoken-friendly handle; it never changes, not even when the appointment is moved
    client_id: int
    client_name: str
    service_id: str
    service_name: str
    category: str
    staff_id: str
    start_utc: int
    duration_minutes: int
    buffer_before_minutes: int
    buffer_after_minutes: int
    status: str
    version: int  # +1 on every change; a confirmation made against an older version is stale
    origin: str
    created_at: int
    updated_at: int
    cancelled_at: Optional[int] = None
    cancel_reason: Optional[str] = None
    seed_key: Optional[str] = None

    @property
    def start(self) -> datetime:
        return from_epoch(self.start_utc)

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.duration_minutes)

    @property
    def end_utc(self) -> int:
        return self.start_utc + self.duration_minutes * 60

    @property
    def blocked_start_utc(self) -> int:
        return self.start_utc - self.buffer_before_minutes * 60

    @property
    def blocked_end_utc(self) -> int:
        return self.end_utc + self.buffer_after_minutes * 60


@dataclass(frozen=True)
class NewBooking:
    """What a confirmed booking saves. The service facts come from the demo's registry, never from the caller."""

    client_name: str
    phone: str  # already normalised
    service_id: str
    service_name: str
    category: str
    staff_id: str
    start_utc: int
    duration_minutes: int
    buffer_before_minutes: int = 0
    buffer_after_minutes: int = 0
    origin: str = ORIGIN_VOICE
    seed_key: Optional[str] = None
    request_key: Optional[str] = None  # a repeat of the same request returns the first result and saves nothing more


@dataclass(frozen=True)
class OutboxMessage:
    """One owner notification, written in the same transaction as the change it reports and sent (if ever) only after that commits."""

    kind: str  # booked / cancelled / rescheduled
    body: str


@dataclass(frozen=True)
class Outcome:
    status: str
    appointment: Optional[StoredAppointment] = None
    previous: Optional[StoredAppointment] = None  # a reschedule: how it was before
    reason: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in SUCCESS


# Outcomes that mean "the stored state is now what was asked for" (including a repeat that changed nothing).
SUCCESS = frozenset({"booked", "replayed", "cancelled", "already_cancelled", "rescheduled", "unchanged"})


class Refused(Exception):
    """Raised inside a transaction to abandon it. Nothing the transaction did is kept; the caller gets ``status`` and ``reason``."""

    def __init__(self, status: str, reason: str = "", *, appointment: Optional["StoredAppointment"] = None, **detail):
        super().__init__(f"{status}: {reason}")
        self.status, self.reason, self.appointment, self.detail = status, reason, appointment, detail


@dataclass(frozen=True)
class Placement:
    """Where an appointment is being put: what a validator is asked to judge."""

    staff_id: str
    start_utc: int
    duration_minutes: int
    buffer_before_minutes: int
    buffer_after_minutes: int
    service_id: str
    category: str
    ignore_reference: Optional[str] = None  # the appointment being moved: its own old place is not a conflict

    @staticmethod
    def of(booking: "NewBooking") -> "Placement":
        return Placement(
            booking.staff_id, booking.start_utc, booking.duration_minutes, booking.buffer_before_minutes, booking.buffer_after_minutes,
            booking.service_id, booking.category,
        )

    @property
    def end_utc(self) -> int:
        return self.start_utc + self.duration_minutes * 60

    @property
    def blocked_start_utc(self) -> int:
        return self.start_utc - self.buffer_before_minutes * 60

    @property
    def blocked_end_utc(self) -> int:
        return self.end_utc + self.buffer_after_minutes * 60
