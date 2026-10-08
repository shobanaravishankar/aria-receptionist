"""Creating a REAL appointment: the only place in this codebase that clicks Save.

The order is fixed and every step can only narrow what happens:
  1. every required approval must have been given (otherwise nothing is touched);
  2. the form is filled with the same routine that was rehearsed live, and read back;
  3. Save is clicked ONLY if every value read back matches the plan, the calendar still has the number of
     appointments it had when it was read, and the Save button is a single, enabled button labelled "Save";
  4. after Save, only ONE expected prompt is handled (the new-client prompt: NOT NOW). Anything else, a
     dialog we do not recognise, a form that will not close, stops the run WITHOUT clicking anything else and
     leaves the browser window open for a person (LeaveWindowOpen);
  5. success is claimed only when the form has closed AND the day shows one more appointment. The booking
     service then verifies the saved appointment by reading its internal note back.
A failure before the Save click means nothing was created (BeforeSaveError). A failure at or after it means
the outcome is unknown (SaveOutcomeUnknown): the booking service reconciles against the calendar and never
blindly retries.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

from .discover_interactive import DIALOG_SELECTORS, NOT_IN_TOUR, ClickRefused, RunStopped, click_refusal
from .driver import BeforeSaveError, DriverError, SaveOutcomeUnknown
from .form_rehearsal import DRAWER, FormRehearsal
from .models import AppointmentSpec, DaySnapshot
from .timeparse import parse_clock

SAVE_BUTTON = '[data-testid="appointment-button-save"]'
CARDS = '[data-testid="calendar-grid-day"] [data-appointment-id]'

REQUIRED_APPROVALS = frozenset({"save", "note-typing", "tour-popups", "not-now", "note-readback"})


class CreationRefused(BeforeSaveError):
    """A reason not to save was found. Nothing was created."""


class LeaveWindowOpen(SaveOutcomeUnknown):
    """Unexpected state after Save. Stop, click nothing more, and leave the window open for a person."""


class AppointmentCreator(FormRehearsal):
    SETTLE_SECONDS = 40.0
    NEW_CARD_SECONDS = 15.0

    def __init__(self, browser: Any, cfg, *, approvals: set[str], **kwargs):
        super().__init__(browser, cfg, **kwargs)
        self.approvals = set(approvals)
        self.save_clicked = False

    # ---- the whole creation ---------------------------------------------------------------------
    def create(self, spec: AppointmentSpec, *, staff: str, expected_existing: Optional[int]) -> None:
        missing = sorted(REQUIRED_APPROVALS - self.approvals)
        if missing:
            raise CreationRefused("not approved: " + ", ".join(missing))
        local = spec.start.astimezone(self._tz())
        self.out(f"CREATING a real appointment for {local:%a %d %b %Y %I:%M %p} ({spec.duration_minutes} min)")

        state, problems, before_cards = self.fill_form(spec, staff=staff)
        if expected_existing is not None and before_cards != expected_existing:
            problems.append(
                f"the calendar changed since it was read ({before_cards} appointment card(s) now, {expected_existing} when read)"
            )
        if not state.save_enabled:
            problems.append("the Save button is not enabled")
        if problems:
            self.out("  NOT saving: " + "; ".join(problems))
            self._abandon_draft()
            raise CreationRefused("; ".join(problems))

        self._click_save()
        self._finish_after_save(before_cards)

    def _tz(self):
        from zoneinfo import ZoneInfo

        return ZoneInfo(self.cfg.timezone)

    def _abandon_draft(self) -> None:
        try:
            self.discard_draft()
        except RunStopped:
            pass  # ending the run closes the browser, which discards an unsaved draft

    # ---- the one deliberate click ------------------------------------------------------------------
    def _click_save(self) -> None:
        saves = self._all(SAVE_BUTTON + NOT_IN_TOUR)
        if len(saves) != 1:
            self._abandon_draft()
            raise CreationRefused(f"expected exactly one Save button, found {len(saves)}")
        button = saves[0]
        label = (button.text or "").strip().casefold()
        css = (button.get_attribute("class") or "").casefold()
        if label != "save" or not button.is_enabled() or "disabled" in css:
            self._abandon_draft()
            raise CreationRefused(f"the Save button is not a plain enabled 'Save' button (label {label!r})")
        self.out("  CLICK SAVE: the one deliberate click that creates the appointment")
        self.save_clicked = True  # from here on, a failure means the outcome is UNKNOWN
        try:
            button.click()
        except Exception as exc:
            raise SaveOutcomeUnknown(f"the Save click raised {type(exc).__name__}; it may or may not have registered") from exc

    # ---- after Save ----------------------------------------------------------------------------------
    def _not_now_buttons(self) -> list[Any]:
        return [e for e in self._all("button, [role='button'], a") if e.text.strip().casefold() == "not now"]

    def _unexpected_dialog(self) -> bool:
        return any(self._all(css + NOT_IN_TOUR) for css in DIALOG_SELECTORS)

    DIALOG_GRACE_SECONDS = 4.0

    def _finish_after_save(self, before_cards: int) -> None:
        deadline = self._monotonic() + self.SETTLE_SECONDS
        declined = 0
        dialog_since: Optional[float] = None
        while True:
            prompts = self._not_now_buttons()
            if prompts:
                if declined >= 1:
                    self._snapshot("23-prompt-repeated-after-save")
                    raise LeaveWindowOpen("the new-client prompt appeared a second time; not clicking it again")
                self._click(prompts[0], "decline the new-client prompt (NOT NOW)")
                declined += 1
                dialog_since = None
                self._stable()
                continue
            if self._unexpected_dialog():
                # a dialog can linger for a moment while it animates away; one that stays is not something we know
                now = self._monotonic()
                dialog_since = now if dialog_since is None else dialog_since
                if now - dialog_since >= self.DIALOG_GRACE_SECONDS:
                    self._snapshot("23-unexpected-dialog-after-save")
                    raise LeaveWindowOpen("an unexpected dialog is showing after Save; not clicking anything")
                self._sleep(0.5)
                continue
            dialog_since = None
            if not self._all(DRAWER):
                break
            if self._monotonic() >= deadline:
                self._snapshot("23-form-still-open-after-save")
                raise LeaveWindowOpen(f"the form is still open {int(self.SETTLE_SECONDS)} seconds after Save")
            self._sleep(0.7)

        self._stable()
        grace = self._monotonic() + self.NEW_CARD_SECONDS
        while True:
            now_cards = len(self._all(CARDS))
            if now_cards == before_cards + 1:
                break
            if self._monotonic() >= grace:
                self._snapshot("24-after-save-unclear")
                raise SaveOutcomeUnknown(
                    f"the form closed but the calendar shows {now_cards} appointment card(s), expected {before_cards + 1}"
                )
            self._sleep(1)
        self._snapshot("24-after-save")
        self.out("  the form closed and the calendar now shows one more appointment")


NOTE_JS = """
const out = [];
document.querySelectorAll('textarea').forEach(t => {
  if (t.offsetParent !== null && !t.closest('[data-testid="calendar-grid-day"]')) out.push(t.value || '');
});
document.querySelectorAll('[data-testid="business_secret_note"]').forEach(e => {
  if (e.offsetParent !== null) out.push(e.innerText || '');
});
return out.join('\\n');
"""


class NoteReader(FormRehearsal):
    """Reads the internal note of every appointment card on the loaded day by opening its details (READ-ONLY).
    The day view shows no note, so verifying a saved booking by its reference needs this."""

    def _times_of(self, card: Any) -> Optional[tuple[tuple[int, int], tuple[int, int]]]:
        starts = card.find_elements("css selector", ".hour_from")
        ends = card.find_elements("css selector", ".hour_till")
        if not starts or not ends:
            return None
        start, end = parse_clock(starts[0].text), parse_clock(ends[0].text)
        return (start, end) if start and end else None

    def _open_card(self, card: Any) -> None:
        # A card's text is the client's and service's name, not an action label, so the click guard looks at the
        # card's own accessibility label and test id only.
        reason = click_refusal(None, card.get_attribute("aria-label"), card.get_attribute("data-testid"))
        if reason:
            raise ClickRefused(reason)
        self._click_card(card)

    def _click_card(self, card: Any) -> None:
        self.out("  click: open an appointment's details to read its internal note (read-only)")
        try:
            card.click()
        except Exception as exc:
            raise DriverError(f"could not open an appointment card: {type(exc).__name__}") from exc

    def read_notes(self, *, tour_ok: bool) -> dict[tuple[tuple[int, int], tuple[int, int]], str]:
        """One note per card, keyed by its (start, end). Raises, rather than returning something partial, when the
        read cannot be trusted: a card whose times are unreadable, two cards with the same times (the key could not
        tell them apart), or a card whose Notes & Info tab could not be opened. A missing note is never 'no note'."""
        notes: dict[tuple[tuple[int, int], tuple[int, int]], str] = {}
        for index in range(len(self._all(CARDS))):
            cards = self._all(CARDS)  # re-found each time: the page may re-render after a drawer closes
            if index >= len(cards):
                raise DriverError("the number of appointment cards changed while reading notes; the read is incomplete")
            key = self._times_of(cards[index])
            if key is None:
                raise DriverError("an appointment card's times could not be read, so its note cannot be matched to it")
            if key in notes:
                raise DriverError(
                    "two appointment cards share the same start and end, so their notes cannot be told apart; refusing to guess"
                )
            self._open_card(cards[index])
            self._stable()
            self._dismiss_tour_popups(tour_ok)
            if not self._click_testid("notes-and-info", "view the Notes & Info tab (read-only)"):
                raise DriverError("an appointment's Notes & Info tab could not be opened, so its note is unknown")
            self._snapshot(f"30-details-notes-{index}")  # structure for review; also shows what the note looked like
            value = str(self.browser.execute_script(NOTE_JS) or "")
            self._dismiss("the appointment details")
            if self._dialog_showing():
                raise RunStopped("a dialog appeared after closing an appointment's details; not clicking it")
            notes[key] = value
        return notes


def attach_notes(snapshot: DaySnapshot, notes: dict[tuple[tuple[int, int], tuple[int, int]], str]) -> DaySnapshot:
    """Fill each appointment's note from the notes read by time. Refuses (DriverError) anything ambiguous or incomplete:
    two appointments with the same times, an appointment with no note read, or a note with no appointment.
    Appointments that were already unknown stay unknown."""
    days = []
    for staff_day in snapshot.staff_days:
        if staff_day.appointments is None:
            days.append(staff_day)
            continue
        keys = [((a.interval.start.hour, a.interval.start.minute), (a.interval.end.hour, a.interval.end.minute)) for a in staff_day.appointments]
        if len(set(keys)) != len(keys):
            raise DriverError("two appointments share the same start and end, so their notes cannot be matched; refusing to guess")
        if set(keys) != set(notes):
            raise DriverError(
                f"the notes read ({len(notes)}) do not match the appointments on the page ({len(keys)}); the read is incomplete"
            )
        appointments = tuple(replace(a, note=notes[key]) for a, key in zip(staff_day.appointments, keys))
        days.append(replace(staff_day, appointments=appointments))
    return replace(snapshot, staff_days=tuple(days))
