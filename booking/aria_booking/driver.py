"""The browser boundary. The booking logic only ever talks to this interface, so it can be tested
without Booksy and the Selenium details stay in one replaceable file.

The two save-related errors matter: ``BeforeSaveError`` means the appointment was definitely NOT
created; ``SaveOutcomeUnknown`` means the Save click happened (or may have) and we cannot tell.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from .models import AppointmentSpec, DaySnapshot


class DriverError(Exception):
    """Anything went wrong talking to the calendar."""


class SignInRequired(DriverError):
    """Not signed in (or the sign-in expired). A person must sign in; the code never types credentials."""


class DriverUnavailable(DriverError):
    """The browser or its driver could not be started (e.g. chromedriver missing, download not allowed)."""


class BeforeSaveError(DriverError):
    """Failure BEFORE the Save click: the appointment was not created."""


class SaveOutcomeUnknown(DriverError):
    """Failure at or after the Save click: the appointment may or may not exist."""


class BookingDriver(Protocol):
    def verify_business(self) -> str:
        """Return the business id the browser is actually signed in to (read from the page)."""

    def read_day(self, day: date, include_notes: bool = False) -> DaySnapshot:
        """Read one day's calendar. Anything unreadable must be reported as None (unknown).
        include_notes also opens each appointment's details (read-only) to read its internal note."""

    def create_appointment(self, spec: AppointmentSpec) -> None:
        """Create exactly one appointment. Raise BeforeSaveError or SaveOutcomeUnknown on failure."""

    def close(self) -> None:
        ...
