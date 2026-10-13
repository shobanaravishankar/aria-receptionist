"""Assemble the DEMO voice endpoint: the same read-only tools, routes and deadlines as live, over the demo dataset. There is no booking route.

``build_demo`` is the ONLY place a dataset-backed driver is wired to the voice tools, and ``cli.serve`` (the live command) never calls it, so the two
sources cannot be swapped by accident. A demo reply says out loud that it is the demo calendar.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from ..config import Config
from ..voice.launch import build_endpoint
from . import DEMO_BUSINESS_ID
from .dataset import DemoDataset
from .driver import DemoCalendarDriver

SOURCE_LABEL = "demo_calendar"
SOURCE_NOTE = "This is the simulated demo calendar, not the salon's real schedule."


def demo_clock(today: Optional[date], tz: ZoneInfo, real_now: Callable[[], datetime] = None) -> Callable[[], datetime]:
    """The voice tools' clock. With an explicit demo date it is that date at the real time of day, so 'tomorrow' means the day after the demo date."""
    real = real_now or (lambda: datetime.now(tz))

    def clock() -> datetime:
        now = real().astimezone(tz)
        if today is None:
            return now
        return datetime(today.year, today.month, today.day, now.hour, now.minute, now.second, tzinfo=tz)

    return clock


def build_demo(
    api_key: str,
    *,
    today: Optional[date] = None,
    timezone: str = "America/New_York",
    bearer_token: Optional[str] = None,
    log: Callable[[str], None] = lambda message: None,
    real_now: Optional[Callable[[], datetime]] = None,
    driver: Optional[Any] = None,
    dataset: Optional[DemoDataset] = None,
    **tool_kwargs: Any,
):
    """(endpoint, driver, dataset). ``driver`` may be a page-reading driver pointed at the demo site (still the demo data); by default it is the dataset."""
    tz = ZoneInfo(timezone)
    clock = demo_clock(today, tz, real_now)
    dataset = dataset or DemoDataset(clock().date())
    driver = driver or DemoCalendarDriver(dataset, timezone=timezone, clock=clock)
    cfg = Config(business_id=DEMO_BUSINESS_ID, timezone=timezone)

    def no_writer(*args: Any, **kwargs: Any):
        raise AssertionError("the demo has no booking service and no writer route")

    tool_kwargs.setdefault("read_cache_seconds", 0.0)  # the demo is instant and its answers should never carry an 'as of' age
    endpoint = build_endpoint(
        cfg, driver, no_writer, clock, api_key, booking_approved=False, bearer_token=bearer_token, registry=dataset.registry, log=log,
        source_label=SOURCE_LABEL, source_note=SOURCE_NOTE, **tool_kwargs,
    )
    return endpoint, driver, dataset
