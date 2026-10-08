"""OPT-IN live tests against Shobana's own Booksy Biz TEST account. Never run by default.

    set ARIA_LIVE_BOOKSY=1
    set ARIA_BOOKSY_BUSINESS_ID=<test business id>
    (and either ARIA_CHROMEDRIVER_PATH=... or ARIA_ALLOW_DRIVER_DOWNLOAD=1)
    python -m pytest -m live

A person must be signed in to Booksy Biz in the dedicated browser window (the tests wait for it).
Read-only tests come first; the booking test is a placeholder until live discovery has been done.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


def _enabled() -> bool:
    return os.environ.get("ARIA_LIVE_BOOKSY") == "1" and bool(os.environ.get("ARIA_BOOKSY_BUSINESS_ID"))


@pytest.fixture(scope="module")
def live_driver():
    if not _enabled():
        pytest.skip("live tests need ARIA_LIVE_BOOKSY=1 and ARIA_BOOKSY_BUSINESS_ID")
    pytest.importorskip("selenium")
    from aria_booking.config import Config
    from aria_booking.selenium_driver import SeleniumBooksyDriver

    cfg = Config.from_env()
    driver = SeleniumBooksyDriver(cfg)
    yield cfg, driver
    driver.close()


def test_signed_in_business_matches_the_configured_test_business(live_driver):
    cfg, driver = live_driver
    assert driver.verify_business() == cfg.business_id


def test_discovery_returns_a_redacted_calendar_structure(live_driver):
    cfg, driver = live_driver
    assert driver.verify_business() == cfg.business_id
    report = driver.discover("today")
    assert report["node_count"] > 20
    assert "<business-id>" in report["page"]["url"] and cfg.business_id not in report["page"]["url"]


def test_end_to_end_booking_is_not_runnable_yet():
    pytest.skip("pending: calendar parsing and appointment creation are not verified against the live page")
