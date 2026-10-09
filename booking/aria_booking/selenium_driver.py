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
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any, Callable, Optional

from .appointment_creator import AppointmentCreator, LeaveWindowOpen, NoteReader, attach_notes
from .calendar_parser import CalendarParseError, normalize_nodes, parse_day, parse_day_multi, single_column_identity
from .config import Config
from .discover import DISCOVERY_JS, LOADER_GONE_JS, build_report
from .discover_interactive import RunStopped, click_refusal
from .driver import BeforeSaveError, DriverError, DriverUnavailable, SaveOutcomeUnknown, SignInRequired
from .models import AppointmentSpec, DaySnapshot
from .staff_census import LIST_TESTID, confirms_single_staff, parse_staff_list
from .roster import ROSTER_JS, Roster, parse_roster
from .timing import PhaseTimer

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
        timing_clock: Callable[[], float] = time.monotonic,  # phase timing only; separate so it never consumes the logic clock
        poll_seconds: float = 2.0,
        ready_poll_seconds: float = 0.25,
        load_timeout_seconds: float = 40.0,
        paint_wait_seconds: float = 1.0,
        approvals: frozenset = frozenset(),
        allowed_services: Optional[frozenset] = None,
    ):
        self.cfg = cfg
        self._factory = webdriver_factory
        self._notify = notify
        self._sleep = sleep
        self._monotonic = monotonic
        self._poll = poll_seconds  # how often to look for a person to finish signing in
        self._load_timeout = load_timeout_seconds  # how long a day page may take to finish loading before the read gives up (fails closed)
        self._ready_poll = ready_poll_seconds  # how often to look at the loading overlay (a local page check, not a request to Booksy)
        self._paint_wait = paint_wait_seconds  # pause after the overlay clears; unchanged until a MEASURED run shows it can be shorter
        self._driver: Optional[Any] = None
        self._staff_confirmed: Optional[bool] = None  # decided once per session from the Staff page
        self._single_staff_id: Optional[str] = None  # the staff id first seen on that single column (trust on first use; later changes refuse)
        self.approvals = frozenset(approvals)
        # The live adapter books ONLY these Booksy service names, whatever a caller layer asks for (fail closed).
        self.allowed_services = frozenset(allowed_services) if allowed_services is not None else frozenset({cfg.service_name})
        self._appointment_counts: dict = {}  # appointments seen by the latest read of each day
        self._frozen: Optional[str] = None  # set when a window was deliberately left open for a person
        self.last_roster: Optional[Roster] = None  # the roster seen on the latest day read (None if the page had no staff filter)
        self.last_read_ms: dict = {}  # phase name -> milliseconds for the latest read_day (metadata only; see timing.PHASES)
        self._timing_clock = timing_clock
        self._read_timer = PhaseTimer(timing_clock)

    # ---- lifecycle -----------------------------------------------------------------------
    def _browser(self):
        if self._driver is None:
            self._driver = self._factory(self.cfg)
            for setter in ("set_page_load_timeout", "set_script_timeout"):  # a hung page or script ends in an error, not a silent stall
                try:
                    getattr(self._driver, setter)(self._load_timeout + 5)
                except Exception:  # a fake or older driver without the setter simply keeps its defaults
                    pass
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

    def _load_day(self, day: date):
        """Open that day's calendar and capture it: (raw page nodes, roster or None). Raises if signed out or still loading."""
        browser = self._browser()
        timer = self._read_timer
        with timer.phase("navigate"):
            browser.get(self.cfg.calendar_url(day.isoformat()))
        with timer.phase("page_ready"):
            ready = self._wait_for_calendar(browser, self._load_timeout)
        if not BUSINESS_PATH_RE.search(browser.current_url or ""):
            raise SignInRequired("the calendar did not open (signed out?); sign in and run again")
        if not ready:
            raise CalendarParseError(f"the calendar was still loading after {self._load_timeout:g} seconds")
        with timer.phase("paint_wait"):
            self._sleep(self._paint_wait)  # let the grid finish painting after the overlay clears
        with timer.phase("capture"):
            raw = browser.execute_script(DISCOVERY_JS)
        with timer.phase("roster"):
            try:
                roster = parse_roster(browser.execute_script(ROSTER_JS))
            except Exception:  # an unreadable roster is simply no roster: the single-staff path (which fails closed) then decides
                roster = None
        return raw, roster

    def read_day(self, day: date, include_notes: bool = False) -> DaySnapshot:
        """Load that day's calendar and parse it. Anything not understood raises (never 'free').

        Two paths. A page whose staff filter lists a COMPLETE roster of two or more people is read by staff id (parse_day_multi): each
        column is bound to its roster entry by its own data-resource id. Anything else takes the single-staff path proven live: it
        needs the Staff-page census to confirm exactly one staff member, and refuses otherwise.
        include_notes opens each appointment's details (read-only) to fill in its internal note (single-staff path only)."""
        if include_notes and "note-readback" not in self.approvals:
            raise DriverError("opening appointment details to read notes needs the note-readback approval")
        self._require_not_frozen()
        tz = ZoneInfo(self.cfg.timezone)
        self._read_timer = PhaseTimer(self._timing_clock)
        self.last_read_ms = {}
        try:
            return self._read_day(day, include_notes, tz)
        finally:
            self.last_read_ms = {name: ms for name, ms in self._read_timer.as_dict().items() if name != "total"}

    def _forget_single_staff(self) -> None:
        """Contradictory or missing identity evidence: the earlier Staff-page census no longer vouches for what the page shows now."""
        self._staff_confirmed = None
        self._single_staff_id = None
        self._appointment_counts.clear()  # a count taken under the old identity must never let a save proceed

    def _check_single_staff_identity(self, nodes, roster) -> str:
        """The single-staff path trusts a census taken ONCE. Before reusing it, make sure the page in front of us does not contradict it.

        Refuses (and forgets the census, so the next read re-checks the Staff page) when: the staff filter exists but was not read as exactly
        one complete entry; that entry is not the configured staff member or not the id seen before; the single column carries a different
        id than the filter or than before; or a header names someone else. A page with no filter and no ids at all is the legacy case the
        census was proven on, and keeps working.

        Returns the staff id VERIFIED for the snapshot: the id of a complete one-member filter whose name is the configured staff member
        (and which any column id on the page agrees with). Without that it returns "", i.e. unknown; a column id seen only on the page is
        remembered to detect later changes but is not promoted to a verified id."""
        configured = " ".join(self.cfg.staff_name.casefold().split())
        roster_id: Optional[str] = None
        if roster is not None:
            if not (roster.complete and len(roster.members) == 1):
                self._forget_single_staff()
                why = "; ".join(roster.problems) or "it does not list exactly one staff member"
                raise CalendarParseError(f"the staff filter was not read as one complete entry ({why}); refusing to reuse an earlier single-staff check")
            member = roster.members[0]
            roster_id = member.staff_id
            if " ".join(member.name.casefold().split()) != configured:
                self._forget_single_staff()
                raise CalendarParseError("the staff filter lists someone other than the configured staff member; refusing to reuse an earlier single-staff check")
        ids, header_names = single_column_identity(nodes)
        for name in header_names:
            if " ".join(name.casefold().split()) != configured:
                self._forget_single_staff()
                raise CalendarParseError("a column header names someone other than the configured staff member; refusing to reuse an earlier single-staff check")
        seen = set(ids)
        if len(seen) > 1:
            return ""  # several columns: parse_day refuses with its own message
        known = {value for value in (roster_id, self._single_staff_id) if value}
        if (seen and known and seen != known) or (roster_id and self._single_staff_id and roster_id != self._single_staff_id):
            self._forget_single_staff()
            raise CalendarParseError("the staff id on this page differs from the one seen before; refusing to attribute it to the configured staff member")
        current = roster_id or next(iter(seen), None)
        if current:
            self._single_staff_id = current
        return roster_id or ""

    def _read_day(self, day: date, include_notes: bool, tz) -> DaySnapshot:
        raw, roster = self._load_day(day)
        self.last_roster = roster
        if roster is not None and roster.complete and len(roster.members) >= 2:
            self._forget_single_staff()  # an account that shows several people no longer has a single-staff identity to reuse
            if include_notes:
                raise DriverError("reading appointment notes is not supported on a multi-staff calendar; refusing to guess whose note is whose")
            self._appointment_counts.pop(day, None)  # creation is single-staff only: no count, so create_appointment refuses
            with self._read_timer.phase("parse"):
                return parse_day_multi(normalize_nodes(raw), day=day, tz=tz, roster=roster, captured_at=datetime.now(tz))

        verified_id = self._check_single_staff_identity(normalize_nodes(raw), roster)
        if self._staff_confirmed is None:
            self.verify_single_staff()  # may raise; once per session. It leaves the day page, so load the day again.
            raw, roster = self._load_day(day)
            verified_id = self._check_single_staff_identity(normalize_nodes(raw), roster)
        browser = self._browser()
        with self._read_timer.phase("parse"):
            snapshot = parse_day(
                normalize_nodes(raw),
                day=day,
                tz=tz,
                staff=self.cfg.staff_name,
                staff_confirmed=self._staff_confirmed,
                captured_at=datetime.now(tz),
            )
        if verified_id:  # the single-staff parser yields exactly one staff day
            snapshot = replace(snapshot, staff_days=tuple(replace(day_, staff_id=verified_id) for day_ in snapshot.staff_days))
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
        if spec.service_name not in self.allowed_services:
            raise BeforeSaveError("this adapter is only cleared to book the verified test service; nothing was clicked")
        if " ".join(spec.staff.casefold().split()) != " ".join(self.cfg.staff_name.casefold().split()):
            raise BeforeSaveError("this adapter supports only the single verified staff member; nothing was clicked")
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
            self._sleep(self._ready_poll)

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
