"""Stored appointments on the demo calendar in a REAL browser: the page, read with the real parser (the live code path), equals the dataset the voice tools answer from,
after every kind of change. Loopback only (the same guard as the other browser tests); no Booksy."""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from aria_booking.appointments.lifecycle import open_lifecycle
from aria_booking.appointments.models import NewBooking, to_epoch
from aria_booking.demo import SPA_TIMEZONE
from aria_booking.demo.driver import DemoCalendarDriver
from aria_booking.demo.server import make_site

from test_demo_browser import comparable, make_driver
from test_playwright_smoke import LoopbackOnlySession, channel, never_left_this_machine  # noqa: F401  (fixtures)

TZ = ZoneInfo(SPA_TIMEZONE)
TODAY = date(2026, 10, 13)
TOMORROW = TODAY + timedelta(days=1)
SWEDISH_60 = "massage-swedish-massage-60"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return open_lifecycle(tmp_path_factory.mktemp("browser-world") / "a.sqlite3", today=TODAY, real_now=lambda: datetime(2026, 10, 13, 10, 0, tzinfo=TZ))


@pytest.fixture(scope="module")
def driver(tmp_path_factory, world, channel):
    site = make_site(world.dataset, 0)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    drv = make_driver(tmp_path_factory.mktemp("browser-driver"), site.server_address[1], channel)
    yield drv
    drv.close()
    site.shutdown()
    site.server_close()


def at(day: date, hour: int, minute: int = 0) -> int:
    return to_epoch(datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ))


def agree(driver, world, days):
    answers = DemoCalendarDriver(world.dataset, clock=world.clock)
    for day in days:
        assert comparable(driver.read_day(day)) == comparable(answers.read_day(day)), day.isoformat()


def test_the_real_reader_reads_the_seeded_calendar_exactly_as_the_dataset_answers(driver, world):
    seeded_days = {world.store.get(ref).start.astimezone(TZ).date() for _template, ref in world.seeded.upcoming_added}
    agree(driver, world, [TODAY, TOMORROW, *sorted(seeded_days)])


def test_after_a_booking_a_cancellation_and_a_move_the_page_and_the_dataset_still_agree(driver, world):
    svc = world.dataset.registry.get(SWEDISH_60)
    booked = world.store.book(
        NewBooking("Mira Quillfeather", "+12125550188", svc.service_id, svc.booksy_name, "massage", "910006", at(TOMORROW + timedelta(days=1), 16), 60, 0, 15),
        validate=world.judge.validator(),
    )
    assert booked.status == "booked"
    day = TOMORROW + timedelta(days=1)
    agree(driver, world, [day])
    seen = driver.read_day(day).for_staff("Sienna")
    assert any(a.interval.start.astimezone(TZ).hour == 16 and a.interval.minutes == 60 for a in seen.appointments), "the reader sees the stored appointment on the page"
    assert world.store.reschedule(booked.appointment.reference, start_utc=at(day, 18), validate=world.judge.validator()).status == "rescheduled"
    agree(driver, world, [day])
    seen = driver.read_day(day).for_staff("Sienna")
    assert not any(a.interval.start.astimezone(TZ).hour == 16 and a.interval.minutes == 60 for a in seen.appointments) and any(a.interval.start.astimezone(TZ).hour == 18 for a in seen.appointments)
    assert world.store.cancel(booked.appointment.reference).status == "cancelled"
    agree(driver, world, [day])
    assert not any(a.interval.start.astimezone(TZ).hour == 18 and a.interval.minutes == 60 for a in driver.read_day(day).for_staff("Sienna").appointments)


def test_a_technician_shown_only_for_her_appointment_reads_as_unknown_hours_in_the_real_reader(driver, world):
    svc = world.dataset.registry.get("lash-eyelash-lifting-45")
    out = world.store.book(NewBooking("Mira Quillfeather", "+12125550188", svc.service_id, svc.booksy_name, "lash", "910008", at(TOMORROW, 12), 45, 0, 0, origin="test"))
    assert out.status == "booked"
    agree(driver, world, [TOMORROW])
    imogen = driver.read_day(TOMORROW).for_staff("Imogen")
    assert imogen is not None and imogen.working is None and len(imogen.appointments) == 1
