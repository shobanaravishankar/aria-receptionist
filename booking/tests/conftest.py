from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from aria_booking.config import Config
from aria_booking.ledger import Ledger
from aria_booking.booking_service import BookingService

from fakes import FakeCalendar

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=TZ)
DAY = date(2026, 10, 12)  # a future Monday relative to NOW


class MutableClock:
    def __init__(self, start: datetime):
        self.value = start

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        from datetime import timedelta

        self.value = self.value + timedelta(**kwargs)


@pytest.fixture
def tz():
    return TZ


@pytest.fixture
def day():
    return DAY


@pytest.fixture
def cfg(tmp_path):
    return Config(business_id="9999999", local_dir=tmp_path / "local")


@pytest.fixture
def calendar(tz):
    cal = FakeCalendar(tz, business_id="9999999", now=NOW)
    cal.set_hours(DAY, 9, 20)  # 09:00-20:00
    return cal


@pytest.fixture
def clock():
    return MutableClock(NOW)


@pytest.fixture
def ledger(cfg, clock):
    return Ledger(cfg.ledger_path, clock=clock)  # same injected clock as the service


@pytest.fixture
def service(cfg, calendar, ledger, clock):
    return BookingService(cfg, calendar, ledger, clock=clock, sleep=lambda s: None)
