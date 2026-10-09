"""An in-memory calendar that behaves like the real one in the ways that matter for the booking
logic, with switches to simulate each failure mode (unknown data, uncertain saves, races...)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import Callable, Optional

from aria_booking.driver import BeforeSaveError, DriverError, SaveOutcomeUnknown, SignInRequired
from aria_booking.models import (
    Appointment,
    AppointmentSpec,
    DaySnapshot,
    Interval,
    StaffDay,
    add_minutes,
)
from aria_booking.safety import extract_ref


class FakeCalendar:
    def __init__(self, tz, *, business_id="9999999", staff="Shobs", now: Optional[datetime] = None):
        self.tz = tz
        self.business_id = business_id
        self.staff = staff
        self.now = now or datetime(2026, 10, 8, 12, 0, tzinfo=tz)
        self.appointments: list[Appointment] = []
        self.working: dict[date, list[Interval]] = {}
        self.time_off: list[Interval] = []
        # behaviour switches
        self.unknown_appointments = False
        self.unknown_working = False
        self.unknown_time_off = False
        self.read_failures = 0  # next N read_day calls raise DriverError
        self.sign_in_required = False
        self.create_mode = "ok"  # ok | before_save_error | unknown_but_saved | unknown_not_saved | saves_wrong_time
        self.on_create: Optional[Callable[["FakeCalendar", AppointmentSpec], None]] = None
        self.notes_need_include_flag = False  # like the real site: the day view shows no notes
        self.hidden_reads_after_create = 0  # eventual consistency: new entry invisible for N reads
        # observations
        self.create_calls: list[AppointmentSpec] = []
        self.read_calls = 0
        self._hide_ref: Optional[str] = None

    # ---- test setup helpers --------------------------------------------------------------
    def at(self, day: date, hour: int, minute: int = 0) -> datetime:
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=self.tz)

    def set_hours(self, day: date, start_hour: int, end_hour: int) -> None:
        self.working.setdefault(day, []).append(Interval(self.at(day, start_hour), self.at(day, end_hour)))

    def add_appointment(self, day: date, start: tuple[int, int], end: tuple[int, int], *, note="", blocks=True) -> Appointment:
        appt = Appointment(self.staff, Interval(self.at(day, *start), self.at(day, *end)), "Other", note, blocks)
        self.appointments.append(appt)
        return appt

    def saved_by_aria(self) -> list[Appointment]:
        return [a for a in self.appointments if extract_ref(a.note)]

    # ---- BookingDriver -------------------------------------------------------------------
    def verify_business(self) -> str:
        if self.sign_in_required:
            raise SignInRequired("not signed in")
        return self.business_id

    def read_day(self, day: date, include_notes: bool = False) -> DaySnapshot:
        self.read_calls += 1
        if self.sign_in_required:
            raise SignInRequired("session expired")
        if self.read_failures > 0:
            self.read_failures -= 1
            raise DriverError("simulated read failure")

        def on_day(interval: Interval) -> bool:
            return interval.start.date() == day

        appts = [a for a in self.appointments if on_day(a.interval)]
        if self._hide_ref and self.hidden_reads_after_create > 0:
            appts = [a for a in appts if extract_ref(a.note) != self._hide_ref]
            self.hidden_reads_after_create -= 1

        if self.notes_need_include_flag and not include_notes:
            appts = [replace(a, note="") for a in appts]

        staff_day = StaffDay(
            self.staff,
            None if self.unknown_working else tuple(self.working.get(day, [])),
            None if self.unknown_time_off else tuple(t for t in self.time_off if on_day(t)),
            None if self.unknown_appointments else tuple(appts),
        )
        return DaySnapshot(day, (staff_day,), self.now)

    def create_appointment(self, spec: AppointmentSpec) -> None:
        self.create_calls.append(spec)
        if self.on_create:
            self.on_create(self, spec)
        if self.create_mode == "before_save_error":
            raise BeforeSaveError("could not open the new-appointment form")
        start = spec.start
        if self.create_mode == "saves_wrong_time":
            start = add_minutes(start, 30)
        saved = Appointment(
            spec.staff, Interval(start, add_minutes(start, spec.duration_minutes)), spec.service_name, spec.note
        )
        if self.create_mode == "unknown_not_saved":
            raise SaveOutcomeUnknown("page timed out before the Save click registered")
        self.appointments.append(saved)
        self._hide_ref = extract_ref(spec.note)
        if self.create_mode == "unknown_but_saved":
            raise SaveOutcomeUnknown("page timed out after the Save click")

    def close(self) -> None:
        pass


class FakeMultiCalendar(FakeCalendar):
    """A SYNTHETIC calendar with several staff members, for the service/technician logic.

    Nothing like it exists in the real Booksy test account (which has exactly one staff member); it only lets the
    offline tests exercise rosters of any size, eligibility, duplicate names and cross-staff conflicts."""

    def __init__(self, tz, roster, *, ids=None, **kwargs):
        roster = list(roster)
        super().__init__(tz, staff=roster[0] if roster else "Nobody", **kwargs)
        self.roster = roster
        self.ids = dict(ids or {})  # display name -> stable staff id (empty when not given, like single-staff data)
        self.staff_hours: dict = {}

    def set_staff_hours(self, staff: str, day: date, start_hour: int, end_hour: int) -> None:
        self.staff_hours.setdefault((staff, day), []).append(Interval(self.at(day, start_hour), self.at(day, end_hour)))

    def set_all_hours(self, day: date, start_hour: int = 9, end_hour: int = 20) -> None:
        for name in dict.fromkeys(self.roster):
            self.set_staff_hours(name, day, start_hour, end_hour)

    def add_staff_appointment(self, staff: str, day: date, start: tuple, end: tuple, *, note="", service="Other", blocks=True) -> Appointment:
        appt = Appointment(staff, Interval(self.at(day, *start), self.at(day, *end)), service, note, blocks)
        self.appointments.append(appt)
        return appt

    def read_day(self, day: date, include_notes: bool = False) -> DaySnapshot:
        self.read_calls += 1
        if self.sign_in_required:
            raise SignInRequired("session expired")
        if self.read_failures > 0:
            self.read_failures -= 1
            raise DriverError("simulated read failure")
        days = []
        for name in self.roster:
            mine = [a for a in self.appointments if a.staff == name and a.interval.start.date() == day]
            days.append(StaffDay(
                name,
                None if self.unknown_working else tuple(self.staff_hours.get((name, day), ())),
                None if self.unknown_time_off else (),
                None if self.unknown_appointments else tuple(mine),
                self.ids.get(name, ""),
            ))
        return DaySnapshot(day, tuple(days), self.now)
