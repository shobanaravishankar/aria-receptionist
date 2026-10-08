"""Selenium adapter for the Booksy Biz calendar.

STATUS: SCAFFOLD. What is real here: launching a dedicated browser profile, detecting sign-in by URL,
verifying which business the browser is signed in to, and structural discovery. What is deliberately NOT
here yet: reading availability and creating appointments. Those need selectors that can only be learned
from the live page, and this code will not click guessed elements on a real account. Until discovery has
been done, ``create_appointment`` refuses with DiscoveryRequired (nothing is clicked). ``read_day`` parses the
live page with calendar_parser; whatever it cannot understand raises, which the booking service reports as
"availability unknown" (never as free, never as booked).

Credentials: this module never types a password and never stores cookies. A person signs in, in the
dedicated browser window; the browser keeps its own session in the git-ignored profile directory.
"""

from __future__ import annotations

import re
import shutil
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any, Callable, Optional

from .appointment_creator import AppointmentCreator, LeaveWindowOpen, NoteReader, attach_notes
from .calendar_parser import CalendarParseError, normalize_nodes, parse_day
from .config import Config
from .discover import DISCOVERY_JS, LOADER_GONE_JS, build_report
from .discover_interactive import RunStopped, click_refusal
from .driver import BeforeSaveError, DriverError, DriverUnavailable, SaveOutcomeUnknown, SignInRequired
from .models import AppointmentSpec, DaySnapshot
from .staff_census import LIST_TESTID, confirms_single_staff, parse_staff_list

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
    options.add_experimental_option("detach", True)  # a window left open for a person survives this program exiting
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
        approvals: frozenset = frozenset(),
    ):
        self.cfg = cfg
        self._factory = webdriver_factory
        self._notify = notify
        self._sleep = sleep
        self._monotonic = monotonic
        self._poll = poll_seconds
        self._driver: Optional[Any] = None
        self._staff_confirmed: Optional[bool] = None  # decided once per session from the Staff page
        self.approvals = frozenset(approvals)
        self._appointment_counts: dict = {}  # appointments seen by the latest read of each day
        self._frozen: Optional[str] = None  # set when a window was deliberately left open for a person

    # ---- lifecycle -----------------------------------------------------------------------
    def _browser(self):
        if self._driver is None:
            self._driver = self._factory(self.cfg)
        return self._driver

    def browser(self):
        """The live webdriver (used by the click-through discovery)."""
        return self._browser()

    def close(self) -> None:
        if self._frozen:
            self._notify(f"Leaving the browser window OPEN for a person to look at: {self._frozen}")
            self._driver = None  # never quit it; the detached window stays
            return
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

    # ---- who is the single column? (decided from the Staff page, once per session) ----------------------------
    def verify_single_staff(self) -> bool:
        """Open the Staff page (a read-only view) and confirm the account has exactly one staff member and that it
        is the configured one. The calendar itself never names a column's staff member."""
        if self._staff_confirmed is not None:
            return self._staff_confirmed
        from selenium.webdriver.common.by import By

        browser = self._browser()
        browser.get(self.cfg.calendar_url("today"))
        self._wait_for_calendar(browser, 40.0)
        entries = [e for e in browser.find_elements(By.CSS_SELECTOR, '[data-testid="staff"]') if e.is_displayed()]
        if not entries:
            raise CalendarParseError("the Staff entry was not found in the side menu, so the staff cannot be checked")
        refusal = click_refusal(entries[0].text, entries[0].get_attribute("aria-label"), entries[0].get_attribute("data-testid"))
        if refusal:
            raise CalendarParseError(refusal)
        entries[0].click()  # a navigation click to a read-only page
        deadline = self._monotonic() + 30
        while self._monotonic() < deadline:
            if browser.find_elements(By.CSS_SELECTOR, f'[data-testid="{LIST_TESTID}"]') and browser.execute_script(LOADER_GONE_JS):
                break
            self._sleep(0.8)
        self._sleep(1)
        names = parse_staff_list(normalize_nodes(browser.execute_script(DISCOVERY_JS)))
        self._staff_confirmed = confirms_single_staff(names, self.cfg.staff_name)
        return self._staff_confirmed

    # ---- reading the calendar (parser tested offline against real captures; see README for live status) ---------
    def _require_not_frozen(self) -> None:
        if self._frozen:
            raise DriverError(f"the browser window was left open for a person ({self._frozen}); not touching it")

    def read_day(self, day: date, include_notes: bool = False) -> DaySnapshot:
        """Load that day's calendar and parse it. Anything not understood raises (never 'free').
        include_notes opens each appointment's details (read-only) to fill in its internal note."""
        if include_notes and "note-readback" not in self.approvals:
            raise DriverError("opening appointment details to read notes needs the note-readback approval")
        self._require_not_frozen()
        self.verify_single_staff()  # may raise; once per session
        browser = self._browser()
        browser.get(self.cfg.calendar_url(day.isoformat()))
        ready = self._wait_for_calendar(browser, 40.0)
        if not BUSINESS_PATH_RE.search(browser.current_url or ""):
            raise SignInRequired("the calendar did not open (signed out?); sign in and run again")
        if not ready:
            raise CalendarParseError("the calendar was still loading after 40 seconds")
        self._sleep(1)  # let the grid finish painting after the overlay clears
        raw = browser.execute_script(DISCOVERY_JS)
        tz = ZoneInfo(self.cfg.timezone)
        snapshot = parse_day(
            normalize_nodes(raw),
            day=day,
            tz=tz,
            staff=self.cfg.staff_name,
            staff_confirmed=self._staff_confirmed,
            captured_at=datetime.now(tz),
        )
        staff_day = snapshot.for_staff(self.cfg.staff_name)
        if staff_day is not None and staff_day.appointments is not None:
            self._appointment_counts[day] = len(staff_day.appointments)
        else:
            self._appointment_counts.pop(day, None)
        if include_notes and staff_day is not None and staff_day.appointments:
            reader = NoteReader(browser, self.cfg, sleep=self._sleep, monotonic=self._monotonic, out=self._notify)
            try:
                notes = reader.read_notes(tour_ok="tour-popups" in self.approvals)
            except RunStopped as exc:
                raise DriverError(f"could not read appointment notes: {exc}") from exc
            snapshot = attach_notes(snapshot, notes)
        return snapshot

    # ---- creating the one approved appointment ------------------------------------------------
    def create_appointment(self, spec: AppointmentSpec) -> None:
        """Fill and save the New Appointment form. Before the Save click every failure is BeforeSaveError (nothing
        was created); from the Save click on every failure is SaveOutcomeUnknown and the window is left open."""
        self._require_not_frozen()
        expected = self._appointment_counts.get(spec.start.astimezone(ZoneInfo(self.cfg.timezone)).date())
        if expected is None:
            raise BeforeSaveError("the day was not read just before saving, so its appointment count is unknown")
        creator = AppointmentCreator(
            self._browser(), self.cfg, approvals=set(self.approvals), sleep=self._sleep, monotonic=self._monotonic, out=self._notify
        )
        try:
            creator.create(spec, staff=self.cfg.staff_name, expected_existing=expected)
        except LeaveWindowOpen as exc:
            self._frozen = str(exc)
            raise
        except (BeforeSaveError, SaveOutcomeUnknown):
            if creator.save_clicked:
                self._frozen = self._frozen or "unexpected state after Save"
            raise
        except Exception as exc:  # RunStopped, selenium errors, helper DriverErrors
            if creator.save_clicked:
                self._frozen = f"{type(exc).__name__} after Save: {exc}"
                raise SaveOutcomeUnknown(f"stopped after the Save click: {exc}") from exc
            raise BeforeSaveError(f"stopped before the Save click, nothing was created: {exc}") from exc

    # ---- discovery -----------------------------------------------------------------------
    def _wait_for_calendar(self, browser, timeout: float) -> bool:
        """Wait for the app's loading overlay to clear. False means it never did (still loading)."""
        deadline = self._monotonic() + timeout
        while True:
            if browser.execute_script(LOADER_GONE_JS):
                return True
            if self._monotonic() >= deadline:
                return False
            self._sleep(self._poll)

    def discover(self, day_text: str = "today", *, load_timeout: float = 40.0) -> dict[str, Any]:
        browser = self._browser()
        browser.get(self.cfg.calendar_url(day_text))
        ready = self._wait_for_calendar(browser, load_timeout)
        if not BUSINESS_PATH_RE.search(browser.current_url or ""):
            raise SignInRequired("the calendar did not open (signed out?); sign in and run again")
        self._sleep(1)  # let the grid finish painting after the overlay clears
        nodes = browser.execute_script(DISCOVERY_JS)
        report = build_report(browser.current_url, browser.title, nodes, day=day_text)
        report["page_ready"] = ready  # False => the loading overlay never cleared; structure is partial
        return report
