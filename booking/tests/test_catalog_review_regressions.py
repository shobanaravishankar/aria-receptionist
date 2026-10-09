"""Permanent tests for Sol's catalogue review (C1-C4) at 411ddae, ported from the external probes (which stay unchanged), plus
extensions for the same classes of defect.

C1 a spoken length was shortened ("one and a half hours" -> 30), C2 a technician's day off/shift was said as the salon's,
C3 a read-only server invited a booking it would refuse, C4 variant names were lost (equal-length refills indistinguishable).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aria_booking.catalog.bookable import BookableRegistry, BookableService
from aria_booking.catalog.lookup import DEFAULT_CATALOG, Catalog, descriptive_label, duration_hint
from aria_booking.catalog.render import render_knowledge
from aria_booking.config import Config
from aria_booking.voice.tools import VoiceTools

from fakes import FakeMultiCalendar

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 8, 12, tzinfo=TZ)
DAY = date(2026, 10, 12)
CAT = DEFAULT_CATALOG


# ---------------------------------------------------------------- C1: a spoken length is never shortened (Sol's probes, ported)


@pytest.mark.parametrize("phrase, minutes", [
    ("one and a half hours", 90), ("an hour and a half", 90), ("half an hour", 30), ("two hours", 120),
])
def test_spoken_duration_keeps_requested_length(phrase, minutes):
    assert duration_hint(phrase) == minutes


def test_ninety_minute_massage_not_silently_resolved_to_thirty():
    found = Catalog().resolve("deep tissue massage one and a half hours")
    assert found.kind == "exact"
    assert found.items[0].duration_minutes == 90


# ---------------------------------------------------------------- C1 extensions: longest match, and unknown phrases fail SAFE


@pytest.mark.parametrize("phrase, minutes", [
    ("one and a half hour", 90), ("1 and a half hours", 90), ("hour and a half", 90), ("an hour and a half", 90),
    ("half hour", 30), ("a half hour", 30), ("half an hour", 30), ("30 minutes", 30), ("thirty", None),
    ("one hour", 60), ("an hour", 60), ("two hours", 120), ("three hours", 180), ("two and a half hours", 150), ("2 and a half hours", 150),
    ("1.5 hours", 90), ("90 minutes", 90), ("90-minute massage", 90), ("a 60 min massage", 60), ("1 hr", 60), ("2 hrs", 120),
])
def test_every_understood_way_of_saying_a_length(phrase, minutes):
    assert duration_hint(phrase) == minutes


@pytest.mark.parametrize("phrase", [
    "three and a half hours", "four and a half hours", "half a day", "a half", "half", "half hour or an hour and a half",
    "an hour or two hours", "30 or 60 minutes", "60", "massage",
])
def test_a_phrase_that_is_not_understood_or_names_several_lengths_gives_no_length(phrase):
    assert duration_hint(phrase) is None, "never guess a shorter (or any) length"


@pytest.mark.parametrize("query, expected", [
    ("swedish massage one and a half hours", 90), ("deep tissue an hour and a half", 90), ("swedish massage half an hour", 30),
    ("swedish massage 1 hour", 60), ("a two hour swedish massage", None),
])
def test_resolution_never_shortens_to_a_different_length(query, expected):
    r = CAT.resolve(query)
    if expected is None:
        assert r.kind == "variants" and r.hint_unmatched, "no 2-hour Swedish exists: say so, do not substitute"
        assert 120 not in [i.duration_minutes for i in r.items] and {i.duration_minutes for i in r.items} == {30, 60, 90}
    else:
        assert r.kind == "exact" and r.items[0].duration_minutes == expected, (query, r)


def test_an_unknown_half_phrase_in_a_service_request_asks_which_length_rather_than_picking():
    r = CAT.resolve("deep tissue massage three and a half hours")
    assert r.kind == "variants" and not r.hint_unmatched and {i.duration_minutes for i in r.items} == {30, 60, 90}


# ---------------------------------------------------------------- C2 / C3 (Sol's probes, ported)


def make_tools(tmp_path, ana_hours=None):
    calendar = FakeMultiCalendar(TZ, ["Ana", "Ben"], business_id="9999999", now=NOW)
    calendar.set_staff_hours("Ben", DAY, 9, 20)
    if ana_hours:
        calendar.set_staff_hours("Ana", DAY, *ana_hours)
    registry = BookableRegistry([BookableService("synthetic-massage-60", "Synthetic Massage", 60, eligible_staff=frozenset({"ana", "ben"}), verified=True)])

    def no_writer(*args):
        raise AssertionError("Availability-only demo must not construct a writer")

    tools = VoiceTools(Config(business_id="9999999", local_dir=tmp_path), calendar, no_writer, lambda: NOW, registry=registry,
                       require_service_id=True, booking_enabled=False, search_days=1)
    return tools, calendar


def ask(tools, staff, time):
    return tools.check_slot("caller", {"service_id": "synthetic-massage-60", "staff": staff, "date": DAY.isoformat(), "time": time})


def test_technician_day_off_is_not_described_as_salon_closed(tmp_path):
    tools, calendar = make_tools(tmp_path)
    reply = ask(tools, "Ana", "16:00")
    assert calendar.create_calls == []
    assert "we aren't taking appointments" not in reply["speak"].lower(), reply
    assert "ana" in reply["speak"].lower(), reply


def test_technician_shift_is_not_reported_as_salon_opening_hours(tmp_path):
    tools, calendar = make_tools(tmp_path, (10, 17))
    reply = ask(tools, "Ana", "18:00")
    assert calendar.create_calls == []
    assert "we're open from" not in reply["speak"].lower(), reply
    assert "ana" in reply["speak"].lower(), reply


def test_readonly_available_reply_does_not_offer_to_save(tmp_path):
    tools, calendar = make_tools(tmp_path, (10, 19))
    reply = ask(tools, "Ana", "16:00")
    assert reply["status"] == "available"
    assert calendar.create_calls == []
    assert "would you like me to book" not in reply["speak"].lower(), reply


def test_a_server_that_cannot_write_never_invites_a_booking_whatever_flag_made_it_so(tmp_path):
    """C3 in every shape: availability_only, or merely booking disabled. Neither may say 'book it' or issue an id."""
    for kwargs in ({"availability_only": True}, {"booking_enabled": False}, {"availability_only": True, "booking_enabled": True}):
        calendar = FakeMultiCalendar(TZ, ["Ana"], business_id="9999999", now=NOW)
        calendar.set_staff_hours("Ana", DAY, 9, 20)
        registry = BookableRegistry([BookableService("m60", "M 60", 60, verified=True)])
        tools = VoiceTools(Config(business_id="9999999"), calendar, lambda *a: None, lambda: NOW, registry=registry, require_service_id=True, **kwargs)
        for reply in (
            tools.check_slot("c", {"service_id": "m60", "date": DAY.isoformat(), "time": "11:00"}),
            tools.find_alternatives("c", {"service_id": "m60", "date": DAY.isoformat()}),
        ):
            low = reply["speak"].lower()
            assert "would you like me to book" not in low and "which would you prefer" not in low and "shall i book" not in low, (kwargs, reply)
            assert all("option_id" not in o for o in reply["options"]), kwargs
            assert "i can only check availability" in low and "can't book" in low, (kwargs, reply)


def test_a_day_off_for_one_technician_is_said_about_that_technician_not_the_salon(tmp_path):
    tools, calendar = make_tools(tmp_path)
    reply = ask(tools, "Ana", "16:00")
    assert "Ana isn't working on Monday, October 12" in reply["speak"]


def test_a_different_technician_being_free_is_not_hidden_when_nobody_is_named(tmp_path):
    tools, calendar = make_tools(tmp_path)  # Ana has no hours that day; Ben works 9-20
    reply = tools.check_slot("caller", {"service_id": "synthetic-massage-60", "date": DAY.isoformat(), "time": "16:00"})
    assert reply["status"] == "available" and reply["options"][0]["technician"] == "Ben"


# ---------------------------------------------------------------- C4 (Sol's probe, ported) and extensions: variants keep their names


def test_lash_refill_prompt_preserves_variant_labels():
    rendered = render_knowledge()
    line = next(s for s in rendered.splitlines() if s.startswith("- Glamour Full Set:"))
    assert "2-week refill" in line and "3-week refill" in line and "4-week refill" in line, line


def test_every_variant_of_every_family_with_equal_lengths_or_prices_is_told_apart_in_the_knowledge():
    text = render_knowledge()
    for family in CAT.families():
        variants = [i for i in CAT.items if i.family == family]
        if len(variants) < 2:
            continue
        line = next(s for s in text.splitlines() if s.startswith(f"- {family}:"))
        shown = [(i.duration_minutes, i.price_usd) for i in variants]
        if len(set(shown)) != len(shown) or any(descriptive_label(i) for i in variants):
            for item in variants:
                assert descriptive_label(item) in line, (family, descriptive_label(item), line)


def test_the_initial_set_is_labelled_so_it_is_not_confused_with_a_refill():
    glamour = {i.item_id: descriptive_label(i) for i in CAT.items if i.family == "Glamour Full Set"}
    assert sorted(glamour.values()) == ["2-week refill", "3-week refill", "4-week refill", "initial set"]


def test_the_lookup_clarification_names_each_variant_and_its_own_price():
    tools = VoiceTools(Config(business_id="9999999"), FakeMultiCalendar(TZ, ["Ana"], now=NOW), lambda *a: None, lambda: NOW,
                       registry=BookableRegistry([BookableService("x", "X", 30, verified=True)]), require_service_id=True)
    reply = tools.lookup_service("c", {"query": "glamour full set"})
    assert reply["status"] == "choose_variant"
    for part in ("initial set: 1 hour 15 minutes for $149", "2-week refill: 1 hour for $75", "3-week refill: 1 hour for $90", "4-week refill: 1 hour 15 minutes for $105"):
        assert part in reply["speak"], part
    assert [i.get("variant") for i in reply["items"]] == ["initial set", "2-week refill", "3-week refill", "4-week refill"]


@pytest.mark.parametrize("query, item_id", [
    ("glamour full set 2 week refill", "lash-glamour-full-set-refill-2w"), ("glamour full set 3-week refill", "lash-glamour-full-set-refill-3w"),
    ("natural full set 4 week refill", "lash-natural-full-set-refill-4w"), ("hybrid 3d 5d full set initial set", "lash-hybrid-3d-5d-full-set-full-set"),
    ("classic full set 2 week refill", "lash-classic-full-set-refill-2w"),
])
def test_a_named_variant_selects_exactly_that_variant(query, item_id):
    r = CAT.resolve(query)
    assert r.kind == "exact" and r.items[0].item_id == item_id, (query, r)


def test_equal_length_variants_are_asked_not_picked():
    r = CAT.resolve("glamour full set refill 1 hour")  # the 2-week and 3-week refills are both an hour
    assert r.kind == "variants" and {i.item_id for i in r.items} == {"lash-glamour-full-set-refill-2w", "lash-glamour-full-set-refill-3w"}
    assert CAT.resolve("glamour full set refill").kind == "variants"
    assert CAT.resolve("glamour full set").kind == "variants"


def test_massage_variants_are_not_cluttered_with_labels_that_only_repeat_the_length():
    assert all(descriptive_label(i) == "" for i in CAT.items if i.family == "Swedish Massage")
    line = next(s for s in render_knowledge().splitlines() if s.startswith("- Swedish Massage:"))
    assert "30 min $49 | 1 hr $85 | 1 hr 30 min $120" in line


# ---------------------------------------------------------------- source hygiene: nothing invisible may creep into the code


def test_no_stray_control_characters_in_any_source_document_or_draft():
    """A patch once left backspace characters where a regex word boundary belonged; tests still passed. Never again."""
    root = Path(__file__).resolve().parent.parent
    allowed = {"\n", "\r", "\t"}
    bad = []
    for path in list(root.rglob("*.py")) + list(root.rglob("*.md")) + list(root.rglob("*.json")):
        if ".local" in path.parts or "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for ch in set(text):
            if ord(ch) < 32 and ch not in allowed:
                bad.append((str(path.relative_to(root)), hex(ord(ch))))
    assert not bad, bad
