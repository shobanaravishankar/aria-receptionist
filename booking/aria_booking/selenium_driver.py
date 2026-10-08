"""Selenium adapter for the Booksy Biz calendar.

STATUS: SCAFFOLD. What is real here: launching a dedicated browser profile, detecting sign-in by URL,
verifying which business the browser is signed in to, and structural discovery. What is deliberately NOT
here yet: reading availability and creating appointments. Those need selectors that can only be learned
from the live page, and this code will not click guessed elements on a real account. Until discovery has
been done, ``read_day`` and ``create_appointment`` refuse with DiscoveryRequired, which the booking
service reports as "availability unknown" (never as free, never as booked).

Credentials: this module never types a password and never stores cookies. A person signs in, in the
dedicated browser window; the browser keeps its own session in the git-ignored profile directory.
"""

from __future__ import annotations

import re
import shutil
import time
from datetime import date
from typing import Any, Callable, Optional

from .config import Config
from .discover import DISCOVERY_JS, build_report
from .driver import BeforeSaveError, DriverUnavailable, SignInRequired
from .models import AppointmentSpec, DaySnapshot

# Any signed-in, business-scoped page, e.g. https://booksy.com/pro/en-us/<business id>/calendar?...
# or a dashboard page under the same prefix. The login page has no business id in its path.
BUSINESS_PATH_RE = re.compile(r"/pro/[a-z]{2}-[a-z]{2}/(\d+)(?:/|$|\?)")


class DiscoveryRequired(BeforeSaveError):
    """Selectors/parsers are not verified against the live site yet. Nothing was changed."""


def build_chrome(cfg: Config):
    """Start Chrome with the dedicated profile. A browser driver download is a file download from
    the internet, so it only happens when explicitly allowed."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    executable = cfg.chromedriver_path or shutil.which("chromedriver")
    if executable:
        service = Service(executable_path=executable)
    elif cfg.allow_driver_download:
        service = Service()  # Selenium Manager fetches a matching driver (explicitly allowed)
    else:
        raise DriverUnavailable(
            "No chromedriver found and automatic download is not allowed. Set ARIA_CHROMEDRIVER_PATH to an "
            "existing driver, or set ARIA_ALLOW_DRIVER_DOWNLOAD=1 to let Selenium download a matching one."
        )

    cfg.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
    options = Options()
    options.add_argument(f"--user-data-dir={cfg.chrome_profile_dir}")
    options.add_argument("--window-size=1400,1000")
    options.add_argument("--no-first-run")
    return webdriver.Chrome(service=service, options=options)


class SeleniumBooksyDriver:
    def __init__(
        self,
        cfg: Config,
        *,
        webdriver_factory: Callable[[Config], Any] = build_chrome,
        notify: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        poll_seconds: float = 2.0,
    ):
        self.cfg = cfg
        self._factory = webdriver_factory
        self._notify = notify
        self._sleep = sleep
        self._monotonic = monotonic
        self._poll = poll_seconds
        self._driver: Optional[Any] = None

    # ---- lifecycle -----------------------------------------------------------------------
    def _browser(self):
        if self._driver is None:
            self._driver = self._factory(self.cfg)
        return self._driver

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            finally:
                self._driver = None

    # ---- sign-in and identity ------------------------------------------------------------
    def verify_business(self) -> str:
        """Open the calendar and return the business id the browser is signed in to.

        If the browser is not signed in, wait (visibly) for a person to sign in; never automate login."""
        browser = self._browser()
        browser.get(self.cfg.calendar_url("today"))
        deadline = self._monotonic() + self.cfg.sign_in_wait_seconds
        announced = False
        while True:
            match = BUSINESS_PATH_RE.search(browser.current_url or "")
            if match:
                return match.group(1)  # signed in; callers compare it with the configured business
            if not announced:
                self._notify(
                    "Not signed in (or the session expired). Please sign in to Booksy Biz in the browser window "
                    "that just opened. Do not share the password with anyone; this tool never asks for it."
                )
                announced = True
            if self._monotonic() >= deadline:
                raise SignInRequired("timed out waiting for a person to sign in; run the login command again")
            self._sleep(self._poll)

    # ---- not yet verified against the live site -----------------------------------------
    def read_day(self, day: date) -> DaySnapshot:
        raise DiscoveryRequired(
            "calendar parsing has not been verified against the live page; run `python -m aria_booking discover` "
            "while signed in, then the parsers can be written from the sanitized structure"
        )

    def create_appointment(self, spec: AppointmentSpec) -> None:
        raise DiscoveryRequired("appointment creation has not been verified against the live page; nothing was clicked")

    # ---- discovery -----------------------------------------------------------------------
    def discover(self, day_text: str = "today") -> dict[str, Any]:
        browser = self._browser()
        browser.get(self.cfg.calendar_url(day_text))
        self._sleep(3)  # let the single-page app render
        if not BUSINESS_PATH_RE.search(browser.current_url or ""):
            raise SignInRequired("the calendar did not open (signed out?); sign in and run again")
        nodes = browser.execute_script(DISCOVERY_JS)
        return build_report(browser.current_url, browser.title, nodes, day=day_text)
