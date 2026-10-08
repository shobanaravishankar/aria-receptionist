"""Dress-rehearsal helpers, the plan-vs-form comparison, and the CLI gating (no browser involved)."""

from __future__ import annotations

import inspect
from datetime import date, datetime

import pytest

from aria_booking import cli
from aria_booking import form_rehearsal as module
from aria_booking.config import Config
from aria_booking.form_rehearsal import FormRehearsal, FormState, compare_with_plan, option_testid
from aria_booking.timeparse import day_from_label, parse_clock, parse_date_text, parse_month_label
from aria_booking.models import AppointmentSpec
from aria_booking.safety import build_note

from conftest import NOW, TZ

TODAY = date(2026, 10, 8)


def test_option_ids_match_the_live_structure():
    assert option_testid(11, 0) == "dropdown-option-11:00"
    assert option_testid(9, 5) == "dropdown-option-09:05"
    assert option_testid(23, 55) == "dropdown-option-23:55"


@pytest.mark.parametrize("text, expected", [
    ("11:00 AM", (11, 0)), ("1:30 PM", (13, 30)), ("12:00 AM", (0, 0)), ("12:15 PM", (12, 15)),
    ("1:30 PM", (13, 30)), ("13:30", (13, 30)), ("", None), ("noon", None),
])
def test_clock_text_is_parsed_or_refused(text, expected):
    assert parse_clock(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("Today", TODAY), ("Tomorrow", date(2026, 10, 9)), ("Mon, 12 Oct", date(2026, 10, 12)), ("Mon, Oct 12", date(2026, 10, 12)),
    ("Monday, October 12, 2026", date(2026, 10, 12)), ("Oct 12, 2026", date(2026, 10, 12)),
    ("Wed, 30 Dec", date(2026, 12, 30)), ("Fri, 1 Jan", date(2027, 1, 1)),  # the year nearest to today is inferred
    ("Someday", None), ("", None),
])
def test_form_date_text_is_understood_or_reported_as_unverifiable(text, expected):
    assert parse_date_text(text, TODAY) == expected


def test_month_label_and_calendar_day_label():
    assert parse_month_label("October 2026") == (2026, 10) and parse_month_label("Oct 2026") == (2026, 10)
    assert parse_month_label("gibberish") is None
    assert day_from_label("Mon, 12 Oct 10:00 AM - 7:00 PM", TODAY) == date(2026, 10, 12)
    assert day_from_label("Thu, 8 Oct", TODAY) == date(2026, 10, 8)
    assert day_from_label("not a date 10:00 AM", TODAY) is None


def _spec(cfg):
    return AppointmentSpec(cfg.staff_name, cfg.service_name, datetime(2026, 10, 12, 11, 0, tzinfo=TZ), 150, build_note("ARIA-0123ABCD"))


def _good_state(spec):
    return FormState(
        service_text="Aria Salon | 2h 30min | $200.00", staff_tabs=["Services by Shobs", "All services"],
        date_text="Mon, 12 Oct", start_text="11:00 AM", end_text="1:30 PM", note_value=spec.note, other_note_value="",
    )


def test_a_matching_form_has_no_problems():
    spec = _spec(Config(business_id="1234567"))
    assert compare_with_plan(spec, _good_state(spec), today=TODAY, tz=TZ, staff="Shobs") == []


@pytest.mark.parametrize("change, expected", [
    ({"start_text": "11:30 AM"}, "start shows"),
    ({"end_text": "2:00 PM"}, "end shows"),
    ({"date_text": "Tue, 13 Oct"}, "date shows"),
    ({"date_text": "next week"}, "cannot verify the date"),
    ({"service_text": "Haircut"}, "service shows"),
    ({"staff_tabs": ["Services by Someone Else"]}, "staff filter"),
    ({"note_value": "ARIA TEST - typed wrongly"}, "internal note"),
    ({"other_note_value": "hello"}, "client-visible"),
])
def test_every_kind_of_difference_is_reported(change, expected):
    spec = _spec(Config(business_id="1234567"))
    state = _good_state(spec)
    for key, value in change.items():
        setattr(state, key, value)
    problems = compare_with_plan(spec, state, today=TODAY, tz=TZ, staff="Shobs")
    assert any(expected in p for p in problems), problems


def _names_in(node):
    import ast

    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def test_the_rehearsal_has_no_way_to_save():
    """Structural check: no save method, and the Save button element is never passed to a click."""
    import ast

    methods = [n for n in dir(FormRehearsal) if not n.startswith("__")]
    assert not any("save" in n.lower() for n in methods if not n.startswith("read")), methods

    tree = ast.parse(inspect.getsource(module))
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        # names bound to anything built from the Save button's test id
        save_names = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and any(
                isinstance(c, ast.Constant) and isinstance(c.value, str) and "appointment-button-save" in c.value
                for c in ast.walk(node.value)
            ):
                for target in node.targets:
                    save_names |= _names_in(target)
        for node in ast.walk(func):
            if isinstance(node, ast.Call):
                called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if called in ("click", "_click", "_click_testid"):
                    referenced = set().union(*[_names_in(a) for a in node.args], _names_in(node.func))
                    literals = [c.value for a in node.args for c in ast.walk(a) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
                    assert not (referenced & save_names), f"{func.name} clicks something derived from the Save button"
                    assert not any("appointment-button-save" in v for v in literals), f"{func.name} clicks the Save button by id"


def test_that_structural_check_would_catch_a_save_click():
    """The check above must not be vacuous: prove it flags a deliberately bad function."""
    import ast
    import textwrap

    bad = ast.parse(textwrap.dedent("""
        def sneaky(self):
            saves = self._all('[data-testid="appointment-button-save"]')
            saves[0].click()
    """))
    func = bad.body[0]
    save_names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and any(isinstance(c, ast.Constant) and "appointment-button-save" in str(c.value) for c in ast.walk(node.value)):
            for target in node.targets:
                save_names |= _names_in(target)
    flagged = [
        n for n in ast.walk(func)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "click"
        and (_names_in(n.func) & save_names)
    ]
    assert flagged, "the structural check failed to notice a click on the Save button"


# ---------------------------------------------------------------- CLI gating (no browser is ever created)


class SpyDriver:
    created = 0

    def __init__(self, cfg, business="1234567"):
        SpyDriver.created += 1
        self.business, self.closed = business, False

    def verify_business(self):
        return self.business

    def close(self):
        self.closed = True


ENV = {"ARIA_BOOKSY_BUSINESS_ID": "1234567", "ARIA_LIVE_BOOKSY": "1"}
ARGV = ["rehearse", "--start", "2026-10-12 11:00", "--confirm-business-id", "1234567",
        "--approve-note-typing", "--approve-draft-discard", "--approve-tour-popups"]


def run(argv, env, factory=None):
    lines = []
    code = cli.main(argv, environ=env, driver_factory=factory, clock=lambda: NOW, out=lines.append)
    return code, "\n".join(lines)


@pytest.mark.parametrize("drop", ["--approve-note-typing", "--approve-draft-discard", "--approve-tour-popups"])
def test_each_approval_is_required_and_no_browser_is_created_without_them(drop):
    SpyDriver.created = 0
    code, text = run([a for a in ARGV if a != drop], ENV, SpyDriver)
    assert code == cli.EXIT_REFUSED and drop in text and SpyDriver.created == 0


def test_live_switch_matching_confirmation_and_a_sane_time_are_all_required():
    SpyDriver.created = 0
    assert run(ARGV, {"ARIA_BOOKSY_BUSINESS_ID": "1234567"}, SpyDriver)[0] == cli.EXIT_REFUSED
    assert run([("9999999" if a == "1234567" else a) for a in ARGV], ENV, SpyDriver)[0] == cli.EXIT_REFUSED
    assert run([("soon" if a == "2026-10-12 11:00" else a) for a in ARGV], ENV, SpyDriver)[0] == cli.EXIT_REFUSED
    assert SpyDriver.created == 0


def test_a_slot_in_the_past_is_refused_by_the_safety_checks_before_the_page_is_touched(tmp_path):
    past = [("2026-10-01 11:00" if a == "2026-10-12 11:00" else a) for a in ARGV]
    code, text = run(past, {**ENV, "ARIA_LOCAL_DIR": str(tmp_path)}, lambda cfg: SpyDriver(cfg))
    assert code == cli.EXIT_REFUSED and "future" in text


def test_the_wrong_signed_in_business_stops_the_rehearsal(tmp_path):
    code, text = run(ARGV, {**ENV, "ARIA_LOCAL_DIR": str(tmp_path)}, lambda cfg: SpyDriver(cfg, business="7777777"))
    assert code == cli.EXIT_REFUSED and "DIFFERENT business" in text
