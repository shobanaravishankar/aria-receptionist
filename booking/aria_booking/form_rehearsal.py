"""Dress rehearsal of the New Appointment form: fill in EVERYTHING except Save, verify, discard the draft.

This is the front half of the real creation flow, proven on an unsaved draft first. Hard limits:
  * There is NO save method here. The Save button is only ever *read* (enabled or not), never clicked.
  * The only text typed is the exact ARIA TEST note built by safety.build_note, into the staff-only
    internal note box (testid business_secret_note), never into the second (client-visible) note box.
  * The staff member is confirmed from the page BEFORE the service is chosen; anything else stops the run.
  * Every value is read back from the form and compared with the plan; any difference is reported.
  * The draft is disposed of with the Discard confirmation, a deliberate narrow exception to the click guard:
    allowed only if that dialog's own text says "discard", and it affects only an unsaved draft.
If anything unexpected happens the run stops; ending it closes the browser, which discards the draft.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from .config import Config
from .discover_interactive import NOT_IN_TOUR, InteractiveDiscovery, RunStopped
from .models import AppointmentSpec, add_minutes
from .safety import NOTE_PREFIX
from .timeparse import day_from_label, parse_clock, parse_date_text

DRAWER = '[data-testid="drawer-appointment-body"]'
NOTE_BOX = '[data-testid="business_secret_note"]'
DISCARD_MODAL = '[data-testid="discard-modal"]'


# ---------------------------------------------------------------- pure helpers (unit-tested)
def option_testid(hour: int, minute: int) -> str:
    return f"dropdown-option-{hour:02d}:{minute:02d}"


@dataclass
class FormState:
    service_text: str = ""
    staff_tabs: list[str] = field(default_factory=list)
    date_text: str = ""
    start_text: str = ""
    end_text: str = ""
    note_value: str = ""
    other_note_value: str = ""
    save_enabled: Optional[bool] = None


def compare_with_plan(spec: AppointmentSpec, state: FormState, *, today: date, tz: ZoneInfo, staff: str) -> list[str]:
    """Every difference between the plan and what the form actually shows. Empty list = it all matches."""
    problems: list[str] = []
    if spec.service_name.casefold() not in (state.service_text or "").casefold():
        problems.append(f"service shows {state.service_text!r}, expected {spec.service_name!r}")
    if not any(staff.casefold() in tab.casefold() for tab in state.staff_tabs):
        problems.append(f"the staff filter did not name {staff!r} (saw {state.staff_tabs!r})")

    local_start = spec.start.astimezone(tz)
    local_end = add_minutes(spec.start, spec.duration_minutes).astimezone(tz)
    if parse_clock(state.start_text) != (local_start.hour, local_start.minute):
        problems.append(f"start shows {state.start_text!r}, expected {local_start.strftime('%I:%M %p')}")
    if parse_clock(state.end_text) != (local_end.hour, local_end.minute):
        problems.append(f"end shows {state.end_text!r}, expected {local_end.strftime('%I:%M %p')}")

    shown = parse_date_text(state.date_text, today)
    if shown is None:
        problems.append(f"cannot verify the date: the control reads {state.date_text!r} and that format is not understood")
    elif shown != local_start.date():
        problems.append(f"date shows {shown.isoformat()}, expected {local_start.date().isoformat()}")

    if state.note_value != spec.note:
        problems.append("the internal note does not match the planned ARIA TEST note exactly")
    if state.other_note_value.strip():
        problems.append("the second (client-visible) note box is not empty")
    return problems


@dataclass
class RehearsalResult:
    state: FormState
    problems: list[str]
    discarded: bool
    paths: list[Any]


# ---------------------------------------------------------------- the browser flow
class FormRehearsal(InteractiveDiscovery):
    """Subclasses the discovery helpers (guarded clicks, snapshots, waiting). Has no save method on purpose."""

    def _drawer_only(self, elements: list[Any]) -> list[Any]:
        return [e for e in elements if self.browser.execute_script("return !!arguments[0].closest(arguments[1]);", e, DRAWER)]

    def _wait_for(self, css: str, timeout: float = 10.0) -> list[Any]:
        deadline = self._monotonic() + timeout
        while True:
            found = self._all(css)
            if found or self._monotonic() >= deadline:
                return found
            self._sleep(0.4)

    def _value(self, css: str) -> str:
        found = self._all(css)
        return (found[0].get_attribute("value") or "") if found else ""

    # ---- steps
    def _verify_page_day(self, target: date) -> None:
        label = self._day_label()
        shown = day_from_label(label, self._today())
        self.out(f"  the calendar page reads {label!r}")
        if shown != target:
            raise RunStopped(f"the calendar loaded {shown} but the plan is for {target}; refusing to continue")

    def _open_form(self) -> None:
        if not self._click_testid("add-button", "open the add menu (plus button)"):
            raise RunStopped("the add button was not found")
        if not self._click_testid("new-appointment-button", "choose New Appointment (an UNSAVED form)"):
            raise RunStopped("the New Appointment entry was not found")
        if not self._wait_for(DRAWER):
            raise RunStopped("the New Appointment form did not open")
        self._dismiss_tour_popups(True)

    def _choose_service(self, staff: str, service: str) -> list[str]:
        if not self._click_testid("subbooking-select-service", "open the service list"):
            raise RunStopped("the service field was not found")
        self._wait_for('[data-testid="service-select-search"]')
        tabs = [t.text.replace("\n", " ") for t in self._all('li[class*="_tabBordered_"]')]
        self.out(f"  staff filter tabs on the service list read: {tabs!r}")
        if not any(staff.casefold() in tab.casefold() for tab in tabs):
            raise RunStopped(f"the service list does not name the staff member {staff!r}; refusing to choose a service")
        items = [
            i for i in self._all('[data-testid="services-list-item"]')
            if i.find_elements("css selector", '[data-testid="service-name"]')
            and i.find_elements("css selector", '[data-testid="service-name"]')[0].text.strip().casefold() == service.casefold()
        ]
        if len(items) != 1:
            raise RunStopped(f"expected exactly one {service!r} entry in the service list, found {len(items)}")
        self._click(items[0], f"choose the {service!r} service")
        self._stable()
        self._dismiss_tour_popups(True)
        return tabs

    def _check_date(self, target: date) -> None:
        """The form's date follows the calendar page that was loaded (verified live). Check it; never drive the
        date picker: that path has not been exercised against the real site, so a mismatch stops the run."""
        controls = self._drawer_only(self._all(".size--20-sb"))
        if not controls:
            raise RunStopped("the date control was not found in the form")
        text = controls[0].text
        shown = parse_date_text(text, self._today())
        self.out(f"  the form's date control reads {text!r}")
        if shown is None:
            raise RunStopped(f"cannot verify the form's date from {text!r}; refusing to continue")
        if shown != target:
            raise RunStopped(
                f"the form's date is {shown} but the plan is for {target}; the date picker is not used because it has "
                "not been verified against the real site"
            )

    def _set_start(self, hour: int, minute: int) -> None:
        if not self._click_testid("select-input-toggle-booked_from", "open the start-time options"):
            raise RunStopped("the start-time control was not found")
        options = self._wait_for(f'[data-testid="{option_testid(hour, minute)}"]')
        if len(options) != 1:
            raise RunStopped(f"expected exactly one start-time option {hour:02d}:{minute:02d}, found {len(options)}")
        self._click(options[0], f"choose the {hour:02d}:{minute:02d} start time")
        self._stable()

    def _type_note(self, note: str) -> None:
        if not note.startswith(NOTE_PREFIX):
            raise RunStopped("refusing to type anything that is not the ARIA TEST note")
        if not self._click_testid("notes-and-info", "open the Notes & Info tab"):
            raise RunStopped("the Notes & Info tab was not found")
        boxes = self._all(f"{NOTE_BOX} textarea")
        if len(boxes) != 1:
            raise RunStopped(f"expected exactly one internal-note box, found {len(boxes)}")
        self.out("  type: the ARIA TEST note into the staff-only internal note box")
        boxes[0].click()
        boxes[0].send_keys(note)

    def read_state(self, staff_tabs: list[str]) -> FormState:
        state = FormState(staff_tabs=staff_tabs)
        self._click_testid("appointment", "show the Appointment tab (read values)")
        services = self._all('[data-testid="subbooking-select-service"]')
        state.service_text = services[0].text.replace("\n", " | ") if services else ""
        state.start_text = self._value('[data-testid="booked_from"] input')
        state.end_text = self._value('[data-testid="booked_till"] input')
        controls = self._drawer_only(self._all(".size--20-sb"))
        state.date_text = controls[0].text if controls else ""
        saves = self._all('[data-testid="appointment-button-save"]')  # READ ONLY: never clicked
        state.save_enabled = bool(saves and saves[0].is_enabled() and "disabled" not in (saves[0].get_attribute("class") or "").casefold())
        self._click_testid("notes-and-info", "show the Notes & Info tab (read the note)")
        state.note_value = self._value(f"{NOTE_BOX} textarea")
        others = [t for t in self._drawer_only(self._all("textarea")) if not self.browser.execute_script("return !!arguments[0].closest(arguments[1]);", t, NOTE_BOX)]
        state.other_note_value = (others[0].get_attribute("value") or "") if others else ""
        return state

    def discard_draft(self) -> bool:
        """Close the form and confirm Discard. ONLY for an unsaved draft, and only if the dialog says so."""
        from selenium.webdriver.common.by import By

        self._dismiss("the New Appointment form")  # may raise nothing; the dialog (if any) is handled below
        modals = self._all(DISCARD_MODAL)
        if not modals:
            return not self._all(DRAWER)
        modal_text = modals[0].text.casefold()
        if "discard" not in modal_text:
            raise RunStopped("a dialog appeared that does not say 'discard'; not clicking it")
        confirm = modals[0].find_elements(By.CSS_SELECTOR, '[data-testid="confirm-btn"]')
        if len(confirm) != 1 or confirm[0].text.strip().casefold() != "discard":
            raise RunStopped("the discard dialog's confirm button is not labelled 'Discard'; not clicking it")
        self.out("  click: Discard (the unsaved DRAFT only; the dialog itself says discard)")
        confirm[0].click()
        self._wait_gone(DISCARD_MODAL)
        self._stable()
        return not self._all(DRAWER) and not self._all(DISCARD_MODAL)

    # ---- the rehearsal
    def rehearse(self, spec: AppointmentSpec, *, staff: str) -> RehearsalResult:
        tz = ZoneInfo(self.cfg.timezone)
        local = spec.start.astimezone(tz)
        target = local.date()
        self.out(f"rehearsal for {local:%a %d %b %Y %I:%M %p} ({spec.duration_minutes} min): fills the form, NEVER saves")
        self._open_calendar(target.isoformat())
        self._dismiss_tour_popups(True)
        self._verify_page_day(target)
        before_cards = len(self._all('[data-testid="calendar-grid-day"] [data-appointment-id]'))
        self.out(f"  appointment cards on that day before: {before_cards}")

        self._open_form()
        self._snapshot("20-form-open")
        tabs = self._choose_service(staff, spec.service_name)
        self._check_date(target)
        self._set_start(local.hour, local.minute)
        self._dismiss_tour_popups(True)
        self._type_note(spec.note)
        state = self.read_state(tabs)
        problems = compare_with_plan(spec, state, today=self._today(), tz=tz, staff=staff)
        self._snapshot("21-rehearsal-filled")

        self.out("  form as read back (no values are saved anywhere):")
        for name in ("service_text", "date_text", "start_text", "end_text", "save_enabled"):
            self.out(f"    {name}: {getattr(state, name)!r}")
        self.out(f"    internal note matches the plan exactly: {state.note_value == spec.note}")
        self.out(f"    second note box empty: {not state.other_note_value.strip()}")
        self.out("  STOPPING BEFORE SAVE. Save was never clicked.")

        discarded = self.discard_draft()
        after_cards = len(self._all('[data-testid="calendar-grid-day"] [data-appointment-id]'))
        self.out(f"  draft discarded: {discarded}; appointment cards on that day after: {after_cards} (before: {before_cards})")
        if after_cards != before_cards:
            problems.append(f"appointment count changed from {before_cards} to {after_cards}")
        self._snapshot("22-after-discard")
        return RehearsalResult(state, problems, discarded, self.paths)


def run_rehearsal(driver: Any, cfg: Config, spec: AppointmentSpec, *, out: Callable[[str], None] = print) -> RehearsalResult:
    try:
        return FormRehearsal(driver.browser(), cfg, out=out).rehearse(spec, staff=cfg.staff_name)
    except RunStopped as stop:
        out(f"STOPPED: {stop}. Nothing was saved; closing the browser discards any draft.")
        raise
