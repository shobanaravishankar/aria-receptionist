"""The text of the owner's prototype appointment alerts. Pure functions: nothing here sends anything.

One alert per change, written into the outbox in the same transaction as the change (see store.py):
  * booked      ->  one alert with the new appointment
  * cancelled   ->  one alert with the appointment that was cancelled
  * rescheduled ->  ONE coherent alert with the old and the new time. It is deliberately not a cancellation followed by a booking: two alerts would tell the owner
                    a client had left and another had arrived, and a failure of one of them would leave the owner believing something that never happened.

Each message is plainly marked as a prototype alert, names the client and the appointment (service, technician, day and time, reference), and carries nothing else:
no phone number, no free-text reason, nothing about any other client. The recipient is never the client and never anything a caller supplied: it is the one owner
destination in local configuration (see sender.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from .models import OutboxMessage, StoredAppointment

PREFIX = "[Serenity prototype alert]"


def _when(row: StoredAppointment, tz: ZoneInfo) -> str:
    start, end = row.start.astimezone(tz), row.end.astimezone(tz)

    def clock(moment: datetime) -> str:
        return f"{moment.hour % 12 or 12}:{moment.minute:02d}{'AM' if moment.hour < 12 else 'PM'}"

    return f"{start:%a %b} {start.day} {clock(start)}-{clock(end)}"


def _what(row: StoredAppointment, technician: str, tz: ZoneInfo) -> str:
    return f"{row.client_name}, {row.service_name} with {technician}, {_when(row, tz)}"


def booked_text(row: StoredAppointment, technician: str, tz: ZoneInfo) -> str:
    return f"{PREFIX} Booked: {_what(row, technician, tz)}. Ref {row.reference}."


def cancelled_text(row: StoredAppointment, technician: str, tz: ZoneInfo) -> str:
    return f"{PREFIX} Cancelled: {_what(row, technician, tz)}. Ref {row.reference}."


def rescheduled_text(new: StoredAppointment, old: StoredAppointment, technician: str, old_technician: str, tz: ZoneInfo) -> str:
    moved_from = _when(old, tz) + ("" if old_technician == technician else f" with {old_technician}")
    return f"{PREFIX} Rescheduled: {new.client_name}, {new.service_name}. Was {moved_from}; now {_when(new, tz)} with {technician}. Ref {new.reference}."


def outbox_builder(staff_name: Callable[[str], str], tz: ZoneInfo):
    """The store's ``outbox`` callback: (saved, previous) -> the one message for that change."""

    def build(saved: StoredAppointment, previous):
        technician = staff_name(saved.staff_id)
        if saved.status == "cancelled":
            return OutboxMessage("cancelled", cancelled_text(saved, technician, tz))
        if previous is not None:
            return OutboxMessage("rescheduled", rescheduled_text(saved, previous, technician, staff_name(previous.staff_id), tz))
        return OutboxMessage("booked", booked_text(saved, technician, tz))

    return build
