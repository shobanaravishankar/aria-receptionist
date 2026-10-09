"""Playwright adapter for the Booksy Biz calendar: HEADFUL, READ-ONLY, one serialized browser owner.

What it is
  * Standard Playwright (sync API) with ``headless=False`` stated explicitly, using its OWN profile directory (``<local>/playwright-profile``,
    git-ignored). It never attaches to a personal browser profile, never reads or copies cookies or sessions, never types a password, and sets
    nothing to disguise automation (no stealth flags, no ``ignore_default_args``, no user-agent override). A PERSON signs in, in the window.
    The window is visible because headless=False is stated; that is NOT claimed to change how the site treats the browser (the Selenium
    adapter was already headful).
  * Read-only: it reads calendars. ``create_appointment`` refuses before any UI action; notes cannot be read; booking is not migrated.
  * The same fail-closed day reading and staff-identity rules as the Selenium adapter (``day_reader.DayReader``).

Threading (the reason this is not a mechanical WebDriver replacement)
  Playwright's sync API is not thread-safe. The voice server calls ``read_day`` from many threads, so the Playwright instance, the browser
  context and the page are created, used and closed ONLY on one owner thread (``owner_thread.OwnerThread``). Callers submit bounded jobs:
  they run one at a time, a job that has not started by its deadline is never run, a caller that gives up discards the late result, and
  nothing spawns a browser per request. Waits are Playwright's native waits (``goto``, ``wait_for_function``, ``wait_for_url``), not sleeps.
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Any, Callable, Optional

from .calendar_parser import CalendarParseError, normalize_nodes
from .config import Config
from .day_reader import DayReader
from .discover import DISCOVERY_JS, LOADER_GONE_JS
from .discover_interactive import click_refusal
from .driver import BeforeSaveError, DriverError, DriverUnavailable, SignInRequired
from .models import AppointmentSpec, DaySnapshot
from .owner_thread import Job, OwnerThread
from .roster import ROSTER_JS, Roster, parse_roster
from .staff_census import LIST_TESTID, confirms_single_staff, parse_staff_list
from .timing import PhaseTimer

try:  # Playwright is a declared dependency, but the Selenium-only commands must keep working without it
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
except ImportError:  # pragma: no cover - exercised only where Playwright is absent
    class PlaywrightError(Exception):  # type: ignore[no-redef]
        pass

    class PlaywrightTimeout(PlaywrightError):  # type: ignore[no-redef]
        pass

# Any signed-in, business-scoped page, e.g. https://booksy.com/pro/en-us/<business id>/calendar?...
BUSINESS_PATH_RE = re.compile(r"/pro/[a-z]{2}-[a-z]{2}/(\d+)(?:/|$|\?)")

VIEWPORT = {"width": 1400, "height": 1000}
RUNNER_SLACK_SECONDS = 5.0  # time allowed beyond the page waits for the hand-off and the two page scripts


def as_function(script: str) -> str:
    """The page scripts are written as function BODIES (they use a top-level ``return``); Playwright evaluates functions or expressions."""
    return "() => {\n" + script + "\n}"


LOADER_GONE_FN = "() => !document.querySelector('[data-testid=\"app-loader\"]')"
# True once the page has stopped changing for `ms` milliseconds (element count and staff columns unchanged): replaces a fixed pause.
SETTLE_FN = """(ms) => {
  const signature = document.getElementsByTagName('*').length + ':' + document.querySelectorAll('[data-resource]').length;
  const state = window.__ariaSettle || (window.__ariaSettle = {signature: null, since: performance.now()});
  const now = performance.now();
  if (state.signature !== signature) { state.signature = signature; state.since = now; return false; }
  return now - state.since >= ms;
}"""


def launch_options(cfg: Config) -> dict[str, Any]:
    """Exactly what the browser is started with. headless=False is explicit; nothing here hides automation."""
    options: dict[str, Any] = {"user_data_dir": str(cfg.playwright_profile_dir), "headless": False, "viewport": dict(VIEWPORT)}
    if cfg.browser_channel:
        options["channel"] = cfg.browser_channel
    return options


def _start_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise DriverUnavailable("Playwright is not installed. Install the pinned requirements (see booking/README.md).") from exc
    return sync_playwright().start()


class PlaywrightSession:
    """The Playwright instance, browser context and page. Create it anywhere, but call everything except ``__init__`` ONLY on the owner thread."""

    def __init__(self, cfg: Config, *, starter: Callable[[], Any] = _start_playwright):
        self._cfg, self._starter = cfg, starter
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None
        self.launches = 0

    def page(self):
        if self._page is not None:
            try:
                if not self._page.is_closed():
                    return self._page
            except PlaywrightError:
                pass
            self._page = None
        if self._context is not None:
            try:
                self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
                if self._page.is_closed():
                    self._page = self._context.new_page()
                return self._page
            except PlaywrightError:
                self._drop_context()  # the whole window went away: start again below
        self._launch()
        return self._page

    def _launch(self) -> None:
        if self._pw is None:
            self._pw = self._starter()
        self._cfg.playwright_profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._context = self._pw.chromium.launch_persistent_context(**launch_options(self._cfg))
        except PlaywrightError as exc:
            if "Executable doesn't exist" in str(exc) or "executable doesn't exist" in str(exc).lower():
                raise DriverUnavailable(
                    "The Playwright browser is not installed. Either run `python -m playwright install chromium` (a download of about 150 MB) "
                    "or set ARIA_BROWSER_CHANNEL=chrome to use the Chrome already installed on this computer."
                ) from exc
            raise DriverUnavailable(f"the browser could not be started ({type(exc).__name__})") from exc
        self.launches += 1
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()

    def _drop_context(self) -> None:
        for name in ("_page",):
            setattr(self, name, None)
        context, self._context = self._context, None
        if context is not None:
            try:
                context.close()
            except Exception:
                pass

    def close(self) -> None:
        self._drop_context()
        pw, self._pw = self._pw, None
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass


class PlaywrightBooksyDriver(DayReader):
    def __init__(
        self,
        cfg: Config,
        *,
        session_factory: Optional[Callable[[], Any]] = None,
        notify: Callable[[str], None] = print,
        load_timeout_seconds: float = 40.0,
        paint_settle_seconds: float = 0.3,
        timing_clock: Callable[[], float] = time.monotonic,
        monotonic: Callable[[], float] = time.monotonic,
        url_for_day: Optional[Callable[[str], str]] = None,
        allowed_services: Optional[frozenset] = None,
    ):
        self.cfg = cfg
        self._notify = notify
        self._load_timeout = load_timeout_seconds  # one calendar read's page waits, in total, are bounded by this
        self._settle_ms = max(0, int(paint_settle_seconds * 1000))
        self._timing_clock, self._mono = timing_clock, monotonic
        self._url_for_day = url_for_day or cfg.calendar_url
        self.approvals = frozenset()  # this adapter can never hold a booking or note-reading approval
        self.allowed_services = frozenset(allowed_services) if allowed_services is not None else frozenset()
        self._staff_confirmed: Optional[bool] = None
        self._single_staff_id: Optional[str] = None
        self._appointment_counts: dict = {}
        self.last_roster: Optional[Roster] = None
        self.last_read_ms: dict = {}
        self._read_timer = PhaseTimer(timing_clock)
        self._session = session_factory() if session_factory else PlaywrightSession(cfg)
        self._runner = OwnerThread(name="playwright-owner", monotonic=monotonic, on_stop=self._session.close)

    # ---- lifecycle ---------------------------------------------------------------------------------
    def close(self) -> None:
        """Close the window and Playwright, on the thread that owns them. Idempotent. Unlike the Selenium adapter this does not leave a window
        open: the sign-in lives in the profile directory, not in the window."""
        self._runner.close()

    @property
    def runner_stats(self) -> dict:
        return dict(self._runner.stats)

    # ---- running browser work on the owner thread --------------------------------------------------
    def _run(self, work: Callable[[Any], Any], timeout: float) -> Any:
        """``work(page)`` runs on the owner thread. Raises DriverError subclasses only; a Playwright error never escapes as itself."""
        timer = self._read_timer
        clock = self._timing_clock

        def on_owner() -> Any:
            began = clock()
            page = self._session.page()  # starts the browser on the first job only; afterwards it returns the page it already has
            timer.add("launch", (clock() - began) * 1000.0)
            return work(page)

        job: Job = self._runner.submit(on_owner, expires_in=timeout)
        try:
            return self._runner.wait(job, timeout)
        except DriverError:
            raise
        except PlaywrightTimeout as exc:
            raise CalendarParseError("the browser timed out") from exc
        except PlaywrightError as exc:
            raise DriverError(f"the browser reported an error ({type(exc).__name__})") from exc
        finally:
            if job.queue_wait is not None:
                timer.add("runner_wait", job.queue_wait * 1000.0)

    # ---- sign-in and identity ----------------------------------------------------------------------
    def verify_business(self) -> str:
        """Open the calendar and return the business id the browser is signed in to. If it is not signed in, wait (visibly) for a PERSON to
        sign in in the window; never automate login."""
        wait_s = float(self.cfg.sign_in_wait_seconds)

        def work(page) -> str:
            page.goto(self._url_for_day("today"), wait_until="domcontentloaded", timeout=int(self._load_timeout * 1000))
            match = BUSINESS_PATH_RE.search(page.url or "")
            if match:
                return match.group(1)
            self._notify(
                "Not signed in (or the session expired). Please sign in to Booksy Biz in the browser window that just opened. "
                "Do not share the password with anyone; this tool never asks for it."
            )
            try:
                page.wait_for_url(BUSINESS_PATH_RE, timeout=int(wait_s * 1000))
            except PlaywrightTimeout:
                raise SignInRequired("timed out waiting for a person to sign in; run the login command again") from None
            found = BUSINESS_PATH_RE.search(page.url or "")
            if not found:
                raise SignInRequired("the sign-in did not reach a business page")
            return found.group(1)

        return self._run(work, self._load_timeout + wait_s + RUNNER_SLACK_SECONDS)

    def verify_single_staff(self) -> bool:
        """The once-per-session Staff-page census (read-only navigation), same rules as the Selenium adapter."""
        if self._staff_confirmed is not None:
            return self._staff_confirmed
        load = self._load_timeout

        def work(page) -> bool:
            deadline = self._mono() + load

            def left() -> int:
                return max(1, int((deadline - self._mono()) * 1000))

            page.goto(self._url_for_day("today"), wait_until="domcontentloaded", timeout=left())
            page.wait_for_function(LOADER_GONE_FN, timeout=left())
            entries = page.locator('[data-testid="staff"]:visible')
            if entries.count() == 0:
                raise CalendarParseError("the Staff entry was not found in the side menu, so the staff cannot be checked")
            first = entries.first
            refusal = click_refusal(first.inner_text(), first.get_attribute("aria-label"), first.get_attribute("data-testid"))
            if refusal:
                raise CalendarParseError(refusal)
            first.click(timeout=left())  # a navigation click to a read-only page
            page.wait_for_selector(f'[data-testid="{LIST_TESTID}"]', timeout=left())
            page.wait_for_function(LOADER_GONE_FN, timeout=left())
            page.wait_for_function(SETTLE_FN, arg=self._settle_ms, timeout=left())
            names = parse_staff_list(normalize_nodes(page.evaluate(as_function(DISCOVERY_JS))))
            return confirms_single_staff(names, self.cfg.staff_name)

        self._staff_confirmed = bool(self._run(work, load + RUNNER_SLACK_SECONDS))
        return self._staff_confirmed

    # ---- reading the calendar -----------------------------------------------------------------------
    def _load_day(self, day: date):
        """Open that day's calendar and capture it: (raw page nodes, roster or None). Raises if signed out, still loading, or never settling."""
        url = self._url_for_day(day.isoformat())
        load = self._load_timeout
        timer = self._read_timer
        settle_ms = self._settle_ms

        def work(page):
            deadline = self._mono() + load

            def left() -> int:
                return max(1, int((deadline - self._mono()) * 1000))

            with timer.phase("navigate"):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=left())
                except PlaywrightTimeout:
                    raise CalendarParseError(f"the calendar page did not load within {load:g} seconds") from None
            with timer.phase("page_ready"):
                try:
                    page.wait_for_function(LOADER_GONE_FN, timeout=left())
                    ready = True
                except PlaywrightTimeout:
                    ready = False
            if not BUSINESS_PATH_RE.search(page.url or ""):
                raise SignInRequired("the calendar did not open (signed out?); sign in and run again")
            if not ready:
                raise CalendarParseError(f"the calendar was still loading after {load:g} seconds")
            with timer.phase("paint_wait"):
                try:
                    page.wait_for_function(SETTLE_FN, arg=settle_ms, timeout=left())
                except PlaywrightTimeout:
                    raise CalendarParseError("the calendar kept changing and never settled") from None
            with timer.phase("capture"):
                raw = page.evaluate(as_function(DISCOVERY_JS))
            with timer.phase("roster"):
                try:
                    roster = parse_roster(page.evaluate(as_function(ROSTER_JS)))
                except Exception:  # an unreadable roster is simply no roster: the single-staff path (which fails closed) then decides
                    roster = None
            return raw, roster

        return self._run(work, load + RUNNER_SLACK_SECONDS)

    # ---- not supported here: this adapter is read-only ----------------------------------------------
    def create_appointment(self, spec: AppointmentSpec) -> None:
        raise BeforeSaveError("the Playwright adapter is read-only: it cannot create appointments, and nothing was clicked")

    def discover(self, *args, **kwargs):
        raise DriverError("discovery runs on the Selenium adapter; the Playwright adapter only reads calendars")

    def browser(self):
        raise DriverError("the Playwright browser belongs to its owner thread and is never handed out")
