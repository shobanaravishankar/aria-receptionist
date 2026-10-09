"""Plain data types for the history design. Synthetic sources fill them in tests; a real adapter would fill them from the client screens."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol

# What a visit's state can be. Only COMPLETED counts as a visit that happened. A past date alone proves nothing.
COMPLETED, CANCELLED, NO_SHOW, UPCOMING, UNKNOWN = "completed", "cancelled", "no_show", "upcoming", "unknown"
STATUSES = frozenset({COMPLETED, CANCELLED, NO_SHOW, UPCOMING, UNKNOWN})


class HistoryUnavailable(Exception):
    """The source could not be reached or is signed out."""


class HistoryUnreadable(Exception):
    """A record's history could not be read."""


@dataclass(frozen=True)
class Visit:
    start: Optional[datetime]  # None = the date could not be read
    services: tuple[str, ...]  # service names as the source shows them (an appointment can hold several)
    status: str  # one of STATUSES; anything the source cannot classify must be UNKNOWN, never COMPLETED
    technician: Optional[str] = None


@dataclass(frozen=True)
class ClientRef:
    """A client the phone number matched. Never spoken, logged or returned: only used to read that client's history."""

    ref: str  # opaque handle for the source
    name: Optional[str] = None  # many records have none; used ONLY to tell apart records that share a number


@dataclass(frozen=True)
class ClientHistory:
    visits: tuple[Visit, ...] = ()
    completion_known: bool = True  # False when the source shows past visits without telling completed from cancelled/no-show
    complete: bool = True  # False when pagination / loading stopped early (the oldest or newest visits may be missing)
    newest_first: bool = False  # True only if the source's order was verified newest-first, so an incomplete read still has the latest


class HistorySource(Protocol):
    def find_clients(self, normalized_phone: str) -> list[ClientRef]:
        """Every client record whose phone normalises to exactly this number. Raises HistoryUnavailable."""

    def read_history(self, client: ClientRef) -> ClientHistory:
        """That client's visits. Raises HistoryUnreadable / HistoryUnavailable."""
