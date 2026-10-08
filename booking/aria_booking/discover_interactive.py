"""Click-through structural discovery of the Booksy calendar. READ-ONLY in intent.

Why: some structure (the appointment details panel, the New Appointment form) only exists after a
click. Every click here is (1) approved in advance by the account owner by name on the command line,
(2) chosen by an explicit selector, never by guessing, and (3) screened by a refusal guard that will not
click anything that looks like an action (save, confirm, delete, pay, send ...). Nothing is typed.
The New Appointment form is opened and then dismissed WITHOUT saving.

If anything unexpected appears (for example a "discard changes?" confirmation), the run stops without
clicking further; ending the run closes the browser, which discards an unsaved form.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .config import Config
from .discover import DISCOVERY_JS, LOADER_GONE_JS, build_report, write_report
from .driver import DriverError

ALLOWED_STEPS = ("tour", "appointment", "new-form")

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


class ClickRefused(DriverError):
    pass


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
    return steps


class InteractiveDiscovery:
    def __init__(
        self,
        browser: Any,
        cfg: Config,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        out: Callable[[str], None] = print,
    ):
        self.browser = browser
        self.cfg = cfg
        self._sleep = sleep
        self._monotonic = monotonic
        self.out = out
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
        element.click()

    def _dismiss(self, what: str) -> bool:
        """Close a panel/form WITHOUT saving: a close button if there is one, else the Escape key."""
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys

        for css in CLOSE_SELECTORS:
            # never the product tour's own close button: dismissing the tour needs its own approval
            found = self._all(css + ':not([data-testid="step-0"] *)')
            if found:
                self._click(found[-1], f"close {what} without saving")
                self._stable()
                return True
        self.out(f"  no close button found for {what}; pressing Escape")
        ActionChains(self.browser).send_keys(Keys.ESCAPE).perform()
        self._stable()
        return False

    # ---- steps ---------------------------------------------------------------------------
    def _open_calendar(self) -> None:
        self.browser.get(self.cfg.calendar_url("today"))
        deadline = self._monotonic() + 40
        while self._monotonic() < deadline and not self.browser.execute_script(LOADER_GONE_JS):
            self._sleep(1)
        self._stable()

    def step_tour(self) -> None:
        self.out("step: product tour")
        close = self._all('[data-testid="step-0"] [data-testid="close-icon"]')
        if close:
            self._click(close[0], "close the first-run product tour")
            if not self._wait_gone('[data-testid="step-0"]'):
                self.out("  the tour is still showing after closing; continuing")
        else:
            self.out("  no product tour is showing")
        self._stable()
        self._snapshot("01-after-tour")

    def step_appointment(self) -> None:
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
        self._dismiss("the appointment details")
        self._snapshot("03-after-details-closed")

    def step_new_form(self) -> None:
        self.out("step: New Appointment form (opened, never saved)")
        add = self._all('[data-testid="add-button"]')
        if not add:
            self.out("  no add button found; skipping")
            return
        self._click(add[0], "open the add menu (plus button)")
        self._stable()
        self._snapshot("04-add-menu")

        options = [
            e
            for e in self._all('button, a, li, [role="menuitem"], [role="button"], div')
            if e.text.strip().casefold() == "new appointment"
        ]
        if not options:
            self.out("  no 'NEW APPOINTMENT' entry found; leaving the menu as is")
            return
        self._click(options[0], "choose NEW APPOINTMENT (form only; nothing will be saved)")
        self._stable()
        self._snapshot("05-new-appointment-form")
        self._dismiss("the New Appointment form")
        path = self._snapshot("06-after-form-closed")
        self.out(f"  final state captured -> {path.name}; review it before any further clicks")

    def run(self, allow: set[str]) -> list[Path]:
        self._open_calendar()
        if "tour" in allow:
            self.step_tour()
        if "appointment" in allow:
            self.step_appointment()
        if "new-form" in allow:
            self.step_new_form()
        return self.paths


def run_interactive_discovery(driver: Any, cfg: Config, *, allow: set[str], out: Callable[[str], None] = print) -> list[Path]:
    return InteractiveDiscovery(driver.browser(), cfg, out=out).run(allow)
