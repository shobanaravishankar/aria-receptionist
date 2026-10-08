"""Click-through structural discovery of the Booksy calendar. READ-ONLY in intent.

Why: some structure (the appointment details panel, the New Appointment form and its pickers) only
exists after a click. Every click here is (1) approved in advance by the account owner by step name on
the command line, (2) chosen by an explicit selector, never by guessing, and (3) screened by a refusal
guard that will not click anything that looks like an action (save, confirm, delete, pay, send ...).
NOTHING IS TYPED and nothing is ever saved. Forms are opened, looked at, and closed.

If anything unexpected appears (for example a "discard changes?" confirmation after the form was
touched), the run STOPS without clicking further. Ending the run closes the browser, which discards an
unsaved form.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from .config import Config
from .discover import DISCOVERY_JS, LOADER_GONE_JS, build_report, write_report
from .driver import DriverError

ALLOWED_STEPS = ("tour", "appointment", "notes-tab", "future-date", "new-form", "form-explore", "staff-list")
REQUIRES = {"notes-tab": "appointment", "form-explore": "new-form"}

# Words that mean "this click changes something". Matched against the element's text, aria-label and test id.
DENY_RE = re.compile(
    r"\b(save|confirm|delete|remove|cancel appointment|check ?out|pay|charge|send|submit|discard|create|book|yes)\b",
    re.IGNORECASE,
)

CLOSE_SELECTORS = (
    '[data-testid="close-icon"]',
    '[data-testid*="close" i]',
    'button[aria-label*="close" i]',
)
NOT_IN_TOUR = ':not([data-testid="step-0"] *)'
TOUR = '[data-testid="step-0"]'
# The calendar always carries "confirmed" / "unconfirmed" status labels. They are not dialogs (a live booking was once
# stopped by mistaking them for one), so they are excluded from the "looks like a confirm dialog" selector.
NOT_STATUS_LABELS = ':not([data-testid="confirmed"]):not([data-testid="unconfirmed"])'
DIALOG_SELECTORS = ('[role="dialog"]', '[role="alertdialog"]', '[data-testid*="modal" i]', '[data-testid*="confirm" i]' + NOT_STATUS_LABELS)


class ClickRefused(DriverError):
    pass


class RunStopped(Exception):
    """Something unexpected appeared; stop cleanly without clicking anything else."""


def click_refusal(text: Optional[str], aria: Optional[str], testid: Optional[str]) -> Optional[str]:
    """Return a reason to refuse the click, or None if it is allowed."""
    blob = " ".join(part for part in (text, aria, testid) if part)
    match = DENY_RE.search(blob)
    if match:
        return f"refused to click an element that looks like an action ({match.group(0)!r})"
    return None


def parse_allow(raw: str) -> set[str]:
    """Validate the --allow list. Raises ValueError for anything not an approved step."""
    steps = {part.strip() for part in (raw or "").split(",") if part.strip()}
    if not steps:
        raise ValueError("no steps approved; pass --allow with one or more of: " + ", ".join(ALLOWED_STEPS))
    unknown = steps - set(ALLOWED_STEPS)
    if unknown:
        raise ValueError(f"unknown step(s) {sorted(unknown)}; allowed: {', '.join(ALLOWED_STEPS)}")
    for step, needed in REQUIRES.items():
        if step in steps and needed not in steps:
            raise ValueError(f"step '{step}' also needs '{needed}' to be approved")
    return steps


def future_dates(today: date) -> list[date]:
    """The next Sunday and the Monday after it: a likely non-working day and a likely working day."""
    days_to_sunday = (6 - today.weekday()) % 7 or 7
    sunday = today + timedelta(days=days_to_sunday)
    return [sunday, sunday + timedelta(days=1)]


class InteractiveDiscovery:
    def __init__(
        self,
        browser: Any,
        cfg: Config,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        out: Callable[[str], None] = print,
        today: Optional[Callable[[], date]] = None,
    ):
        self.browser = browser
        self.cfg = cfg
        self._sleep = sleep
        self._monotonic = monotonic
        self.out = out
        self._today = today or (lambda: datetime.now(ZoneInfo(cfg.timezone)).date())
        self.paths: list[Path] = []

    # ---- low-level helpers ---------------------------------------------------------------
    def _all(self, css: str) -> list[Any]:
        from selenium.webdriver.common.by import By

        return [e for e in self.browser.find_elements(By.CSS_SELECTOR, css) if e.is_displayed()]

    def _count_nodes(self) -> int:
        return int(self.browser.execute_script("return document.querySelectorAll('*').length;"))

    def _stable(self, timeout: float = 12.0, interval: float = 0.8) -> None:
        """Wait until the loader is gone and the DOM size stops changing."""
        deadline = self._monotonic() + timeout
        last = -1
        while self._monotonic() < deadline:
            self._sleep(interval)
            count = self._count_nodes()
            if count == last and self.browser.execute_script(LOADER_GONE_JS):
                return
            last = count

    def _wait_gone(self, css: str, timeout: float = 8.0) -> bool:
        deadline = self._monotonic() + timeout
        while self._monotonic() < deadline:
            if not self._all(css):
                return True
            self._sleep(0.5)
        return False

    def _snapshot(self, label: str) -> Path:
        nodes = self.browser.execute_script(DISCOVERY_JS)
        report = build_report(self.browser.current_url, self.browser.title, nodes, day="today")
        report["step"] = label
        path = write_report(self.cfg.evidence_dir, report, label=label)
        self.paths.append(path)
        self.out(f"  captured {report['node_count']} redacted nodes -> {path.name}")
        return path

    def _click(self, element: Any, why: str) -> None:
        reason = click_refusal(element.text, element.get_attribute("aria-label"), element.get_attribute("data-testid"))
        if reason:
            raise ClickRefused(f"{reason} while trying to: {why}")
        self.out(f"  click: {why}")
        try:
            element.click()
        except Exception as exc:  # e.g. another element intercepts the click
            raise DriverError(f"could not click ({why}): {type(exc).__name__}") from exc

    def _click_testid(self, testid: str, why: str) -> bool:
        found = self._all(f'[data-testid="{testid}"]' + NOT_IN_TOUR)
        if not found:
            self.out(f"  not found: {testid} ({why}); skipping")
            return False
        self._click(found[0], why)
        self._stable()
        return True

    def _dialog_showing(self) -> bool:
        return any(self._all(css + NOT_IN_TOUR) for css in DIALOG_SELECTORS)

    def _dismiss_tour_popups(self, approved: bool) -> None:
        """Close the product tour's own popup (close or finish only, never 'next'). Needs the 'tour' approval."""
        for _ in range(3):
            popups = self._all(TOUR)
            if not popups:
                return
            if not approved:
                raise RunStopped("a product-tour popup is covering the page and 'tour' was not approved")
            button = self._all(TOUR + ' [data-testid="close-icon"]') or self._all(TOUR + ' [data-testid="finish-button"]')
            if not button:
                raise RunStopped("a product-tour popup has no close/finish button I am allowed to use")
            self._click(button[0], "dismiss the product-tour popup (close/finish only)")
            self._wait_gone(TOUR, timeout=4)
        self._stable()

    def _dismiss(self, what: str) -> None:
        """Close a panel/form WITHOUT saving: a close button if there is one, else the Escape key."""
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys

        for css in CLOSE_SELECTORS:
            found = self._all(css + NOT_IN_TOUR)  # never the product tour's own close button here
            if found:
                self._click(found[-1], f"close {what} without saving")
                self._stable()
                return
        self.out(f"  no close button found for {what}; pressing Escape")
        ActionChains(self.browser).send_keys(Keys.ESCAPE).perform()
        self._stable()

    def _day_label(self) -> str:
        found = self._all('[data-testid="date-switcher-label"]')
        return found[0].text.replace("\n", " ") if found else "(date label not found)"

    # ---- steps ---------------------------------------------------------------------------
    def _open_calendar(self, day_text: str = "today") -> None:
        self.browser.get(self.cfg.calendar_url(day_text))
        deadline = self._monotonic() + 40
        while self._monotonic() < deadline and not self.browser.execute_script(LOADER_GONE_JS):
            self._sleep(1)
        self._stable()

    def step_tour(self) -> None:
        self.out("step: product tour")
        close = self._all(TOUR + ' [data-testid="close-icon"]')
        if close:
            self._click(close[0], "close the first-run product tour")
            if not self._wait_gone(TOUR):
                self.out("  the tour is still showing after closing; continuing")
        else:
            self.out("  no product tour is showing")
        self._stable()
        self._snapshot("01-after-tour")

    def step_appointment(self, allow: set[str]) -> None:
        self.out("step: existing appointment details")
        cards = self._all('[data-testid="calendar-grid-day"] [data-appointment-id]')
        wanted = [c for c in cards if "aria salon" in c.text.casefold() and "walk-in" in c.text.casefold()]
        if len(cards) != 1 or len(wanted) != 1:
            self.out(
                f"  NOT opening anything: expected exactly one Aria Salon walk-in appointment today, "
                f"found {len(cards)} appointment card(s) ({len(wanted)} matching)."
            )
            return
        self._click(wanted[0], "open the existing test appointment's details (read-only)")
        self._stable()
        self._snapshot("02-appointment-details")
        self._dismiss_tour_popups("tour" in allow)
        if "notes-tab" in allow and self._click_testid("notes-and-info", "view the Notes & Info tab (read-only)"):
            self._snapshot("02b-appointment-notes-tab")
        self._dismiss("the appointment details")
        self._snapshot("03-after-details-closed")

    PICK_JS = """
        const name = arguments[0].toLowerCase();
        const els = [...document.querySelectorAll('div,li,button,span,p')].filter(e => {
          const r = e.getBoundingClientRect();
          return r.width > 0 && r.height > 0
            && !e.closest('[data-testid="calendar-grid-day"]') && !e.closest('[data-appointment-id]')
            && !e.closest('[data-testid="step-0"]')
            && (e.innerText || '').trim().toLowerCase().startsWith(name);
        });
        const area = e => { const r = e.getBoundingClientRect(); return r.width * r.height; };
        els.sort((a, b) => area(a) - area(b));
        return els.length ? els[0] : null;
    """

    def _pick_service(self, name: str) -> bool:
        """Click the smallest visible element starting with the service name, never one in the calendar grid."""
        target = self.browser.execute_script(self.PICK_JS, name)
        if target is None:
            return False
        self._click(target, f"choose the '{name}' service in the UNSAVED form")
        self._stable()
        return True

    def _neutral_click(self) -> None:
        """Click a harmless heading inside the drawer to close an open dropdown/picker."""
        found = self._all('[data-testid="appointment-header"] .heading--1')
        if found:
            self._click(found[0], "click the form heading to close the open picker")
            self._stable()

    def step_new_form(self, allow: set[str]) -> None:
        self.out("step: New Appointment form (opened, never saved)")
        if not self._click_testid("add-button", "open the add menu (plus button)"):
            return
        self._snapshot("04-add-menu")
        if not self._click_testid("new-appointment-button", "choose New Appointment (form only; nothing will be saved)"):
            return
        self._snapshot("05-new-appointment-form")
        self._dismiss_tour_popups("tour" in allow)

        if "form-explore" in allow:
            self.out("  exploring the UNSAVED form (no typing, no saving)")
            if self._click_testid("subbooking-select-service", "open the service list"):
                self._snapshot("07-service-list")
                if self._pick_service(self.cfg.service_name):
                    self._dismiss_tour_popups("tour" in allow)
                    self._snapshot("08-service-selected")
                else:
                    self.out("  could not find the service entry; leaving it")
            if self._click_testid("select-input-toggle-booked_from", "open the start-time options"):
                self._snapshot("09-start-time-options")
                self._neutral_click()
            date_controls = self._all(".size--20-sb")
            if date_controls:
                self._click(date_controls[0], "open the date control")
                self._stable()
                self._snapshot("10-date-picker")
                self._neutral_click()
            if self._click_testid("notes-and-info", "view the Notes & Info tab (no typing)"):
                self._snapshot("11-notes-tab")

        self._dismiss("the New Appointment form")
        if self._dialog_showing():
            self._snapshot("12-confirmation-dialog")
            raise RunStopped(
                "a confirmation dialog appeared after closing the touched form. Not clicking anything; ending the run "
                "closes the browser, which discards the unsaved form."
            )
        path = self._snapshot("12-after-form-closed")
        self.out(f"  final state captured -> {path.name}; review it before any further clicks")

    def step_future_dates(self, allow: set[str]) -> None:
        self.out("step: future dates (address only, no clicks)")
        for day in future_dates(self._today()):
            self._open_calendar(day.isoformat())
            self._dismiss_tour_popups("tour" in allow)
            self.out(f"  loaded {day.isoformat()}; the page's own date label reads: {self._day_label()!r}")
            self._snapshot(f"13-date-{day.isoformat()}")

    def step_staff_list(self, allow: set[str]) -> None:
        """Open the Staff page from the side menu and capture its structure (a read-only page view).
        Used to establish how many staff members the account has; nothing on it is clicked or changed."""
        self.out("step: staff list (a read-only page view)")
        if not self._click_testid("staff", "open the Staff page from the side menu (read-only view)"):
            return
        deadline = self._monotonic() + 30
        while self._monotonic() < deadline and not self.browser.execute_script(LOADER_GONE_JS):
            self._sleep(1)
        self._stable()
        self._dismiss_tour_popups("tour" in allow)
        self._snapshot("30-staff-page")

    def run(self, allow: set[str]) -> list[Path]:
        self._open_calendar("today")
        try:
            if "tour" in allow:
                self.step_tour()
            if "appointment" in allow:
                self.step_appointment(allow)
            if "new-form" in allow:
                self.step_new_form(allow)
            if "future-date" in allow:
                self.step_future_dates(allow)
            if "staff-list" in allow:
                self.step_staff_list(allow)  # last: it navigates away from the calendar
        except RunStopped as stop:
            self.out(f"STOPPED: {stop}")
        return self.paths


def run_interactive_discovery(driver: Any, cfg: Config, *, allow: set[str], out: Callable[[str], None] = print) -> list[Path]:
    return InteractiveDiscovery(driver.browser(), cfg, out=out).run(allow)
