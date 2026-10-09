"""Creating the one real appointment: gating before Save, the single Save click, the post-Save state machine,
leaving the window open on anything unexpected, and the note read-back. A scripted page stands in for the browser;
the form-filling front half was proven live by the rehearsal (see README) and is replaced by a canned result here."""

from __future__ import annotations

import ast
import inspect
from datetime import date, datetime
from pathlib import Path

import pytest

from aria_booking import appointment_creator as creator_module
from aria_booking import cli, selenium_driver
from aria_booking.appointment_creator import (
    CARDS,
    REQUIRED_APPROVALS,
    SAVE_BUTTON,
    AppointmentCreator,
    CreationRefused,
    LeaveWindowOpen,
    NoteReader,
    attach_notes,
)
from aria_booking.config import Config
from aria_booking.discover_interactive import DIALOG_SELECTORS, NOT_IN_TOUR, ClickRefused, RunStopped
from aria_booking.driver import BeforeSaveError, DriverError, SaveOutcomeUnknown
from aria_booking.form_rehearsal import DRAWER, FormState
from aria_booking.models import Appointment, AppointmentSpec, DaySnapshot, Interval, StaffDay
from aria_booking.safety import build_note, extract_ref

from conftest import TZ

START = datetime(2026, 10, 12, 11, 0, tzinfo=TZ)
REF = "ARIA-0123ABCD"
ALL = set(REQUIRED_APPROVALS)


def spec_for():
    return AppointmentSpec("Shobs", "Aria Salon", START, 150, build_note(REF))


class El:
    def __init__(self, page, kind, text="", cls="", testid="", enabled=True):
        self.page, self.kind, self.text, self.cls, self.testid, self._enabled = page, kind, text, cls, testid, enabled

    def get_attribute(self, name):
        return {"class": self.cls, "data-testid": self.testid, "aria-label": None}.get(name)

    def is_enabled(self):
        return self._enabled

    def click(self):
        self.page.clicks.append(self.kind)
        self.page.on_click(self)


class Page:
    """What the creator can see after the form is filled: Save, the drawer, dialogs, and the calendar's cards."""

    def __init__(self, scenario="prompt", cards=0, save_buttons=1, save_text="Save", save_cls="", save_enabled=True):
        self.scenario, self.cards, self.clicks = scenario, cards, []
        self.drawer_open, self.prompt, self.dialog_until = True, False, None
        self.prompts_shown = 0
        self.saves = [El(self, "save", save_text, save_cls, "appointment-button-save", save_enabled) for _ in range(save_buttons)]
        self.not_now = El(self, "not-now", "NOT NOW")
        self.clock = None

    def now(self):
        return self.clock.t

    def on_click(self, el):
        if el.kind == "save":
            if self.scenario == "prompt":
                self.prompt, self.prompts_shown = True, 1
            elif self.scenario == "no_prompt":
                self.drawer_open, self.cards = False, self.cards + 1
            elif self.scenario == "unknown_dialog":
                self.dialog_until = float("inf")
            elif self.scenario == "transient_dialog":
                self.dialog_until = self.now() + 2
                self.drawer_open, self.cards = False, self.cards + 1
            elif self.scenario == "stays_open":
                pass
            elif self.scenario == "closes_no_card":
                self.drawer_open = False
            elif self.scenario == "two_cards":
                self.drawer_open, self.cards = False, self.cards + 2
            elif self.scenario == "prompt_twice":
                self.prompt, self.prompts_shown = True, 1
            elif self.scenario == "save_click_raises":
                raise RuntimeError("stale element")
        elif el.kind == "not-now":
            self.prompt = False
            if self.scenario == "prompt_twice" and self.prompts_shown < 2:
                self.prompt, self.prompts_shown = True, 2
                return
            self.drawer_open, self.cards = False, self.cards + 1

    def dialog_visible(self):
        return self.prompt or (self.dialog_until is not None and self.now() < self.dialog_until)

    def query(self, css):
        if css == SAVE_BUTTON + NOT_IN_TOUR:
            return list(self.saves) if self.drawer_open else []
        if css == "button, [role='button'], a":
            return [self.not_now] if self.prompt else []
        if css in [d + NOT_IN_TOUR for d in DIALOG_SELECTORS]:
            return [El(self, "dialog", "some dialog")] if self.dialog_visible() else []
        if css == DRAWER:
            return [El(self, "drawer")] if self.drawer_open else []
        if css == CARDS:
            return [El(self, "card") for _ in range(self.cards)]
        return []


class Clock:
    t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class Harness(AppointmentCreator):
    """The real creation logic, with only the browser-facing primitives replaced."""

    def __init__(self, page, *, problems=(), state_ok=True, filled_cards=None, **kwargs):
        self.clock = Clock()
        page.clock = self.clock
        self.lines: list[str] = []
        super().__init__(browser=None, cfg=Config(business_id="1234567"), sleep=self.clock.sleep, monotonic=self.clock.monotonic, out=self.lines.append, **kwargs)
        self.page, self._problems, self.fill_calls, self.snapshots, self.discards = page, list(problems), 0, [], 0
        self._state = FormState(save_enabled=state_ok)
        self._filled_cards = page.cards if filled_cards is None else filled_cards

    # browser-facing primitives
    def _all(self, css):
        return self.page.query(css)

    def _stable(self, *a, **k):
        pass

    def _snapshot(self, label):
        self.snapshots.append(label)

    def discard_draft(self):
        self.discards += 1
        return True

    def fill_form(self, spec, *, staff):
        self.fill_calls += 1
        return self._state, list(self._problems), self._filled_cards


def create(page, *, approvals=ALL, expected=0, **kwargs):
    h = Harness(page, approvals=approvals, **kwargs)
    h.create(spec_for(), staff="Shobs", expected_existing=expected)
    return h


# ---------------------------------------------------------------- approvals gate everything

@pytest.mark.parametrize("missing", sorted(REQUIRED_APPROVALS))
def test_every_missing_approval_stops_the_run_before_anything_is_touched(missing):
    page = Page()
    h = Harness(page, approvals=ALL - {missing})
    with pytest.raises(CreationRefused, match=missing):
        h.create(spec_for(), staff="Shobs", expected_existing=0)
    assert h.fill_calls == 0 and page.clicks == [] and not h.save_clicked


# ---------------------------------------------------------------- Save is clicked only when everything matches

def test_the_happy_path_clicks_save_once_then_not_now_once_and_nothing_else():
    page = Page("prompt")
    h = create(page)
    assert page.clicks == ["save", "not-now"]
    assert h.save_clicked and h.discards == 0
    assert page.cards == 1 and "24-after-save" in h.snapshots


def test_without_a_prompt_the_only_click_is_save():
    page = Page("no_prompt")
    create(page)
    assert page.clicks == ["save"]


def test_any_difference_between_the_form_and_the_plan_means_no_save_and_the_draft_is_discarded():
    page = Page()
    h = Harness(page, approvals=ALL, problems=["start shows '11:30 AM'"])
    with pytest.raises(CreationRefused, match="start shows"):
        h.create(spec_for(), staff="Shobs", expected_existing=0)
    assert page.clicks == [] and not h.save_clicked and h.discards == 1


def test_a_changed_appointment_count_since_the_day_was_read_means_no_save():
    page = Page(cards=1)
    h = Harness(page, approvals=ALL)
    with pytest.raises(CreationRefused, match="calendar changed"):
        h.create(spec_for(), staff="Shobs", expected_existing=0)
    assert page.clicks == [] and h.discards == 1


def test_a_disabled_save_in_the_form_state_means_no_save():
    page = Page()
    h = Harness(page, approvals=ALL, state_ok=False)
    with pytest.raises(CreationRefused, match="not enabled"):
        h.create(spec_for(), staff="Shobs", expected_existing=0)
    assert page.clicks == [] and h.discards == 1


@pytest.mark.parametrize("kwargs, why", [
    ({"save_buttons": 2}, "exactly one Save button"),
    ({"save_buttons": 0}, "exactly one Save button"),
    ({"save_text": "Save and close"}, "plain enabled 'Save'"),
    ({"save_text": "Delete"}, "plain enabled 'Save'"),
    ({"save_cls": "btn disabled"}, "plain enabled 'Save'"),
    ({"save_enabled": False}, "plain enabled 'Save'"),
])
def test_a_save_button_that_is_not_exactly_one_plain_enabled_save_is_never_clicked(kwargs, why):
    page = Page(**kwargs)
    h = Harness(page, approvals=ALL)
    with pytest.raises(CreationRefused, match=why):
        h.create(spec_for(), staff="Shobs", expected_existing=0)
    assert page.clicks == [] and not h.save_clicked and h.discards == 1


def test_a_failure_to_discard_does_not_hide_the_refusal():
    class StuckDiscard(Harness):
        def discard_draft(self):
            raise RunStopped("a dialog appeared that does not say 'discard'")

    page = Page()
    h = StuckDiscard(page, approvals=ALL, problems=["x"])
    with pytest.raises(CreationRefused):
        h.create(spec_for(), staff="Shobs", expected_existing=0)


# ---------------------------------------------------------------- after Save: only what is expected

def test_an_unknown_dialog_after_save_stops_with_the_window_left_open_and_no_more_clicks():
    page = Page("unknown_dialog")
    h = Harness(page, approvals=ALL)
    with pytest.raises(LeaveWindowOpen, match="unexpected dialog"):
        h.create(spec_for(), staff="Shobs", expected_existing=0)
    assert page.clicks == ["save"], "nothing was clicked after the unexpected state appeared"
    assert isinstance(LeaveWindowOpen("x"), SaveOutcomeUnknown)
    assert "23-unexpected-dialog-after-save" in h.snapshots


def test_a_dialog_that_disappears_by_itself_is_tolerated():
    page = Page("transient_dialog")
    create(page)
    assert page.clicks == ["save"]


def test_the_new_client_prompt_is_declined_once_and_never_a_second_time():
    page = Page("prompt_twice")
    h = Harness(page, approvals=ALL)
    with pytest.raises(LeaveWindowOpen, match="second time"):
        h.create(spec_for(), staff="Shobs", expected_existing=0)
    assert page.clicks == ["save", "not-now"]


def test_a_form_that_stays_open_after_save_stops_with_the_window_left_open():
    page = Page("stays_open")
    h = Harness(page, approvals=ALL)
    with pytest.raises(LeaveWindowOpen, match="still open"):
        h.create(spec_for(), staff="Shobs", expected_existing=0)
    assert page.clicks == ["save"]


@pytest.mark.parametrize("scenario", ["closes_no_card", "two_cards"])
def test_a_closed_form_without_exactly_one_new_card_is_an_unknown_outcome_not_a_success(scenario):
    page = Page(scenario)
    h = Harness(page, approvals=ALL)
    with pytest.raises(SaveOutcomeUnknown) as err:
        h.create(spec_for(), staff="Shobs", expected_existing=0)
    assert not isinstance(err.value, LeaveWindowOpen)
    assert page.clicks == ["save"]


def test_a_save_click_that_raises_is_an_unknown_outcome():
    page = Page("save_click_raises")
    h = Harness(page, approvals=ALL)
    with pytest.raises(SaveOutcomeUnknown, match="may or may not"):
        h.create(spec_for(), staff="Shobs", expected_existing=0)
    assert h.save_clicked


def test_save_clicked_is_false_for_every_failure_before_the_click():
    page = Page(save_buttons=2)
    h = Harness(page, approvals=ALL)
    with pytest.raises(BeforeSaveError):
        h.create(spec_for(), staff="Shobs", expected_existing=0)
    assert not h.save_clicked


# ---------------------------------------------------------------- the Save click exists in exactly one place

PACKAGE = Path(inspect.getfile(selenium_driver)).parent


def _save_clicks(tree):
    """(function name) of every .click() whose receiver is built from the Save button's test id or SAVE_BUTTON."""
    hits = []
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        names = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and any(
                (isinstance(c, ast.Constant) and isinstance(c.value, str) and "appointment-button-save" in c.value)
                or (isinstance(c, ast.Name) and c.id == "SAVE_BUTTON")
                for c in ast.walk(node.value)
            ):
                for target in node.targets:
                    names |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
        # one more hop: `button = saves[0]`
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and any(isinstance(c, ast.Name) and c.id in names for c in ast.walk(node.value)):
                for target in node.targets:
                    names |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("click", "_click"):
                receiver = {n.id for n in ast.walk(node.func.value) if isinstance(n, ast.Name)} | {
                    n.id for a in node.args for n in ast.walk(a) if isinstance(n, ast.Name)
                }
                if receiver & names:
                    hits.append(func.name)
    return hits


def test_the_only_save_click_in_the_whole_package_is_in_click_save_of_the_creator():
    found = {}
    for path in sorted(PACKAGE.glob("*.py")):
        hits = _save_clicks(ast.parse(path.read_text(encoding="utf-8")))
        if hits:
            found[path.name] = hits
    assert found == {"appointment_creator.py": ["_click_save"]}, found


def test_that_search_would_catch_a_save_click_elsewhere():
    bad = ast.parse("def sneaky(self):\n    buttons = self._all(SAVE_BUTTON)\n    b = buttons[0]\n    b.click()\n")
    assert _save_clicks(bad) == ["sneaky"]


def test_the_note_reader_and_creator_never_type_anything():
    source = inspect.getsource(creator_module)
    assert "send_keys" not in source, "typing happens only in the rehearsed form-filling code"


# ---------------------------------------------------------------- reading notes back

CARD_TEXTS = {("11:00 AM", "1:30 PM"): f"ARIA TEST - fictional appointment. Ref: {REF}", ("3:15 PM", "5:45 PM"): "walk-in note"}


class Card:
    def __init__(self, reader, times, testid="calendar-card-1", aria=None):
        self.reader, self.times, self.testid, self.aria = reader, times, testid, aria
        self.text = "Aria Salon"

    def get_attribute(self, name):
        return {"data-testid": self.testid, "aria-label": self.aria}.get(name)

    def find_elements(self, by, css):
        cls = css.lstrip(".")
        index = {"hour_from": 0, "hour_till": 1}.get(cls)
        return [type("T", (), {"text": self.times[index]})()] if index is not None else []

    def click(self):
        self.reader.opened.append(self.times)
        self.reader.open_card = self.times


class ReaderHarness(NoteReader):
    def __init__(self, cards, **kw):
        self.clock = Clock()
        self.opened, self.open_card, self.lines, self.snapshots = [], None, [], []
        self.cards_ = [Card(self, t) for t in cards]
        self.dialog_after_close = kw.pop("dialog_after_close", False)
        super().__init__(browser=self, cfg=Config(business_id="1234567"), sleep=self.clock.sleep, monotonic=self.clock.monotonic, out=self.lines.append)

    # a tiny browser
    def execute_script(self, js, *a):
        return CARD_TEXTS.get(self.open_card, "") if js == creator_module.NOTE_JS else None

    def _all(self, css):
        return list(self.cards_) if css == CARDS else []

    def _stable(self, *a, **k):
        pass

    def _snapshot(self, label):
        self.snapshots.append(label)

    def _dismiss_tour_popups(self, ok):
        pass

    def _click_testid(self, testid, why):
        return testid == "notes-and-info"

    def _dismiss(self, what):
        self.open_card = None

    def _dialog_showing(self):
        return self.dialog_after_close


def test_notes_are_read_for_every_card_by_opening_it_read_only():
    reader = ReaderHarness([("11:00 AM", "1:30 PM"), ("3:15 PM", "5:45 PM")])
    notes = reader.read_notes(tour_ok=True)
    assert notes == {((11, 0), (13, 30)): CARD_TEXTS[("11:00 AM", "1:30 PM")], ((15, 15), (17, 45)): "walk-in note"}
    assert reader.opened == [("11:00 AM", "1:30 PM"), ("3:15 PM", "5:45 PM")]


def test_a_dialog_after_closing_the_details_stops_the_reading():
    with pytest.raises(RunStopped):
        ReaderHarness([("11:00 AM", "1:30 PM")], dialog_after_close=True).read_notes(tour_ok=True)


def test_a_card_whose_accessibility_label_looks_like_an_action_is_not_opened():
    reader = ReaderHarness([("11:00 AM", "1:30 PM")])
    reader.cards_[0].aria = "Delete this appointment"
    with pytest.raises(ClickRefused):
        reader.read_notes(tour_ok=True)
    assert reader.opened == []


def test_a_card_with_unreadable_times_makes_the_read_incomplete_not_skipped():
    reader = ReaderHarness([("11:00 AM", "1:30 PM"), ("sometime", "later")])
    with pytest.raises(DriverError, match="times could not be read"):
        reader.read_notes(tour_ok=True)


def test_two_cards_with_the_same_times_are_refused_not_overwritten():
    reader = ReaderHarness([("11:00 AM", "1:30 PM"), ("11:00 AM", "1:30 PM")])
    with pytest.raises(DriverError, match="share the same start and end"):
        reader.read_notes(tour_ok=True)


def test_a_notes_tab_that_cannot_be_opened_means_the_note_is_unknown_not_empty():
    class NoTab(ReaderHarness):
        def _click_testid(self, testid, why):
            return False

    with pytest.raises(DriverError, match="Notes & Info tab could not be opened"):
        NoTab([("11:00 AM", "1:30 PM")]).read_notes(tour_ok=True)


def snapshot_with(*times):
    appts = tuple(
        Appointment("Shobs", Interval(datetime(2026, 10, 12, a[0], a[1], tzinfo=TZ), datetime(2026, 10, 12, b[0], b[1], tzinfo=TZ)), "Aria Salon", "", True)
        for a, b in times
    )
    return DaySnapshot(date(2026, 10, 12), (StaffDay("Shobs", (), (), appts),), datetime(2026, 10, 12, tzinfo=TZ))


def test_attached_notes_make_the_reference_findable_by_the_booking_service():
    snap = attach_notes(
        snapshot_with(((11, 0), (13, 30)), ((15, 15), (17, 45))),
        {((11, 0), (13, 30)): CARD_TEXTS[("11:00 AM", "1:30 PM")], ((15, 15), (17, 45)): "walk-in note"},
    )
    first, second = snap.staff_days[0].appointments
    assert extract_ref(first.note) == REF and second.note == "walk-in note"


def test_notes_never_attach_to_an_appointment_at_a_different_time():
    with pytest.raises(DriverError, match="incomplete"):
        attach_notes(snapshot_with(((12, 0), (14, 30))), {((11, 0), (13, 30)): CARD_TEXTS[("11:00 AM", "1:30 PM")]})


def test_unknown_appointments_stay_unknown_when_notes_are_attached():
    snap = DaySnapshot(date(2026, 10, 12), (StaffDay("Shobs", (), (), None),), datetime(2026, 10, 12, tzinfo=TZ))
    assert attach_notes(snap, {((11, 0), (13, 30)): "x"}).staff_days[0].appointments is None


# ---------------------------------------------------------------- the driver: classification and leaving the window open

class StubCreator:
    save_clicked = False
    raises: Exception | None = None
    sets_clicked = False

    def __init__(self, *a, **k):
        pass

    def create(self, spec, *, staff, expected_existing):
        StubCreator.seen_expected = expected_existing
        if StubCreator.sets_clicked:
            self.save_clicked = True
        if StubCreator.raises:
            raise StubCreator.raises


class QuitSpy:
    quit_called = False
    current_url = "https://booksy.com/pro/en-us/1234567/calendar"
    title = "Calendar"

    def quit(self):
        QuitSpy.quit_called = True


@pytest.fixture
def drv(monkeypatch):
    QuitSpy.quit_called = False
    StubCreator.raises, StubCreator.sets_clicked = None, False
    monkeypatch.setattr(selenium_driver, "AppointmentCreator", StubCreator)
    messages = []
    d = selenium_driver.SeleniumBooksyDriver(
        Config(business_id="1234567"), webdriver_factory=lambda c: QuitSpy(), notify=messages.append,
        sleep=lambda s: None, monotonic=lambda: 0.0, approvals=frozenset(ALL),
    )
    d._appointment_counts[date(2026, 10, 12)] = 0
    d.messages = messages
    return d


def test_the_expected_count_from_the_latest_read_reaches_the_creator(drv):
    drv._appointment_counts[date(2026, 10, 12)] = 3
    drv.create_appointment(spec_for())
    assert StubCreator.seen_expected == 3


def test_an_unexpected_error_before_the_save_click_means_nothing_was_created(drv):
    StubCreator.raises = RunStopped("form did not open")
    with pytest.raises(BeforeSaveError, match="nothing was created"):
        drv.create_appointment(spec_for())
    drv.close()
    assert QuitSpy.quit_called, "nothing unusual happened, so the browser is closed normally"


def test_an_unexpected_error_after_the_save_click_is_unknown_and_leaves_the_window_open(drv):
    StubCreator.sets_clicked, StubCreator.raises = True, RuntimeError("boom")
    with pytest.raises(SaveOutcomeUnknown):
        drv.create_appointment(spec_for())
    drv.close()
    assert not QuitSpy.quit_called and any("OPEN" in m for m in drv.messages)


def test_a_deliberately_left_open_window_is_never_touched_again(drv):
    StubCreator.raises = LeaveWindowOpen("an unexpected dialog is showing after Save")
    with pytest.raises(LeaveWindowOpen):
        drv.create_appointment(spec_for())
    with pytest.raises(DriverError, match="left open"):
        drv.read_day(date(2026, 10, 12))
    with pytest.raises(DriverError, match="left open"):
        drv.create_appointment(spec_for())
    drv.close()
    assert not QuitSpy.quit_called


def test_the_browser_is_started_detached_so_a_left_open_window_survives_the_program():
    assert 'add_experimental_option("detach", True)' in inspect.getsource(selenium_driver.build_chrome)


# ---------------------------------------------------------------- the CLI

ENV = {"ARIA_BOOKSY_BUSINESS_ID": "1234567", "ARIA_LIVE_BOOKSY": "1"}
BOOK = ["book", "--start", "2026-10-12 11:00", "--confirm-business-id", "1234567"]
FLAGS = ["--approve-" + f for f in cli.BOOK_APPROVALS]


class Spy:
    created = 0

    def __init__(self, cfg):
        Spy.created += 1


def run(argv):
    lines = []
    code = cli.main(argv, environ=ENV, driver_factory=Spy, clock=lambda: datetime(2026, 10, 8, 12, 0, tzinfo=TZ), out=lines.append)
    return code, "\n".join(lines)


def test_the_cli_approval_flags_are_exactly_the_creator_approvals():
    assert {f.replace("--approve-", "") for f in FLAGS} == REQUIRED_APPROVALS


@pytest.mark.parametrize("drop", FLAGS)
def test_book_needs_every_single_approval_flag_and_creates_no_browser_without_them(drop):
    Spy.created = 0
    code, text = run(BOOK + [f for f in FLAGS if f != drop])
    assert code == cli.EXIT_REFUSED and drop in text and Spy.created == 0


def test_book_with_no_approvals_at_all_names_every_missing_one():
    Spy.created = 0
    code, text = run(BOOK)
    assert code == cli.EXIT_REFUSED and all(f in text for f in FLAGS) and Spy.created == 0


def test_the_real_driver_gets_the_approvals_that_were_given(monkeypatch):
    seen = {}
    timeouts = []

    class Recording:
        def __init__(self, cfg, approvals=frozenset(), load_timeout_seconds=40.0):
            timeouts.append(load_timeout_seconds)
            seen["approvals"] = approvals

        def verify_business(self):
            return "7777777"  # wrong business: stops at once, after construction

        def close(self):
            pass

    monkeypatch.setattr(cli, "SeleniumBooksyDriver", Recording)
    lines = []
    cli.main(BOOK + FLAGS, environ=ENV, clock=lambda: datetime(2026, 10, 8, 12, 0, tzinfo=TZ), out=lines.append)
    assert seen["approvals"] == REQUIRED_APPROVALS and timeouts == [40.0], "the interactive command keeps its longer page wait"


# ---------------------------------------------------------------- the dialog detector and the read-only verify command

def test_the_calendars_own_status_labels_are_not_mistaken_for_a_dialog():
    """A live booking was stopped because 'confirmed' / 'unconfirmed' labels matched the confirm-dialog selector."""
    from aria_booking.discover_interactive import NOT_STATUS_LABELS

    fixtures = Path(__file__).parent / "fixtures"
    import json

    labels = set()
    for name in ("empty_day_mon_12_oct.json", "busy_day_thu_8_oct.json"):
        for n in json.loads((fixtures / name).read_text(encoding="utf-8"))["nodes"]:
            tid = (n["testid"] or "").casefold()
            if "confirm" in tid or "modal" in tid:
                labels.add(tid)
    assert labels == {"confirmed", "unconfirmed"}, labels  # exactly the labels the live page always shows
    confirm_selector = next(d for d in DIALOG_SELECTORS if "confirm" in d)
    for label in labels:
        assert f':not([data-testid="{label}"])' in confirm_selector
    assert NOT_STATUS_LABELS in confirm_selector


def test_real_dialogs_are_still_recognised():
    assert '[role="dialog"]' in DIALOG_SELECTORS and any("modal" in d for d in DIALOG_SELECTORS)
    assert any("confirm" in d for d in DIALOG_SELECTORS)


def test_reading_notes_through_the_driver_needs_the_note_readback_approval(drv):
    drv.approvals = frozenset({"tour-popups"})
    with pytest.raises(DriverError, match="note-readback"):
        drv.read_day(date(2026, 10, 12), include_notes=True)
    assert drv._driver is None, "the check happens before the browser is even started"


def _slot(calendar):
    return calendar.at(date(2026, 10, 12), 11, 0)


def _add_booked(service, calendar, *, late_minutes=0):
    """Record a booking the way book() would, but leave the ledger entry UNCERTAIN, as after the live run."""
    from aria_booking.ledger import ref_from_key, request_key

    cfg = service.cfg
    start = _slot(calendar)
    key = request_key(cfg.business_id, cfg.staff_name, cfg.service_name, start, cfg.service_duration_minutes)
    ref = ref_from_key(key)
    service.ledger.create(key, ref, cfg.staff_name, cfg.service_name, start.isoformat(), (start.replace(hour=13, minute=30)).isoformat(), "saving")
    service.ledger.set_state(key, "uncertain", "not confirmed by read-back")
    start_m, end_m = 11 * 60 + late_minutes, 13 * 60 + 30 + late_minutes
    day = date(2026, 10, 12)
    calendar.appointments.append(Appointment(
        cfg.staff_name, Interval(calendar.at(day, *divmod(start_m, 60)), calendar.at(day, *divmod(end_m, 60))), cfg.service_name, build_note(ref)))
    return key, ref


def test_verify_finds_the_booking_by_its_reference_and_marks_the_ledger_verified(service, calendar):
    from aria_booking.booking_service import Status
    from aria_booking.ledger import State

    calendar.notes_need_include_flag = True
    key, ref = _add_booked(service, calendar)
    result = service.verify(_slot(calendar))
    assert result.status is Status.BOOKED_VERIFIED and result.ref == ref
    assert service.ledger.get(key).state == State.VERIFIED
    assert calendar.create_calls == [], "verify never creates anything"


def test_verify_does_not_mark_anything_failed_when_the_reference_is_not_found(service, calendar):
    from aria_booking.booking_service import Status
    from aria_booking.ledger import State

    key, _ = _add_booked(service, calendar)
    calendar.appointments.clear()
    result = service.verify(_slot(calendar))
    assert result.status is Status.UNCERTAIN_NEEDS_REVIEW
    assert service.ledger.get(key).state == State.UNCERTAIN, "absence is reported, never turned into 'failed'"
    assert calendar.create_calls == []


def test_verify_reports_a_mismatch(service, calendar):
    from aria_booking.booking_service import Status

    key, _ = _add_booked(service, calendar, late_minutes=30)  # saved 30 minutes late
    result = service.verify(_slot(calendar))
    assert result.status is Status.VERIFY_MISMATCH and calendar.create_calls == []


def test_verify_with_no_ledger_record_refuses_to_guess(service, calendar):
    from aria_booking.booking_service import Status

    assert service.verify(_slot(calendar)).status is Status.UNCERTAIN_NEEDS_REVIEW


def test_verify_survives_an_unreadable_calendar(service, calendar):
    from aria_booking.booking_service import Status

    key, _ = _add_booked(service, calendar)
    calendar.read_failures = 1
    assert service.verify(_slot(calendar)).status is Status.UNCERTAIN_NEEDS_REVIEW


VERIFY = ["verify", "--start", "2026-10-12 11:00", "--confirm-business-id", "1234567", "--approve-tour-popups", "--approve-note-readback"]


@pytest.mark.parametrize("drop", ["--approve-tour-popups", "--approve-note-readback"])
def test_verify_needs_both_approvals_and_opens_no_browser_without_them(drop):
    Spy.created = 0
    code, text = run([a for a in VERIFY if a != drop])
    assert code == cli.EXIT_REFUSED and Spy.created == 0


def test_verify_cannot_create_anything_by_construction():
    """verify() contains no call that creates or saves."""
    from aria_booking.booking_service import BookingService

    source = inspect.getsource(BookingService.verify)
    assert "create_appointment" not in source and ".book(" not in source
    assert "State.FAILED" not in source
