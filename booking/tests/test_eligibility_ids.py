"""Who can perform a service is decided by STABLE staff ids when the mapping gives them, never by display names."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from aria_booking.catalog.bookable import BookableRegistry, BookableService
from aria_booking.config import Config
from aria_booking.voice.tools import VoiceTools

from fakes import FakeMultiCalendar

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 8, 12, tzinfo=TZ)
DAY = date(2026, 10, 12)


def stack(service, roster=("Lily", "Maya"), ids=None):
    ids = {"Lily": "1001", "Maya": "1002"} if ids is None else ids
    calendar = FakeMultiCalendar(TZ, list(roster), ids=ids, business_id="9999999", now=NOW)
    calendar.set_all_hours(DAY)
    tools = VoiceTools(Config(business_id="9999999"), calendar, lambda *a: None, lambda: NOW, registry=BookableRegistry([service]),
                       require_service_id=True, availability_only=True)
    return tools, calendar


def ask(tools, staff=None):
    args = {"service_id": "svc", "date": DAY.isoformat(), "time": "11:00"}
    if staff:
        args["staff"] = staff
    return tools.check_slot("c", args)


def svc(**kw):
    return BookableService("svc", "Svc", 60, verified=True, **kw)


def test_eligibility_by_id_admits_exactly_the_listed_people():
    tools, _ = stack(svc(eligible_staff_ids=frozenset({"1001"})))
    assert ask(tools)["options"][0]["technician"] == "Lily"
    refused = ask(tools, "Maya")
    assert refused["status"] == "staff_unavailable" and refused["reason"] == "staff_not_eligible" and refused["available_staff"] == ["Lily"]


def test_a_rename_does_not_change_who_is_eligible():
    tools, _ = stack(svc(eligible_staff_ids=frozenset({"1001"})), roster=("Lilian Chen", "Maya"), ids={"Lilian Chen": "1001", "Maya": "1002"})
    assert ask(tools)["options"][0]["technician"] == "Lilian Chen"


def test_the_listed_id_is_not_enough_if_the_calendar_gives_the_person_no_id():
    tools, _ = stack(svc(eligible_staff_ids=frozenset({"1001"})), ids={})
    reply = ask(tools)
    assert reply["status"] == "staff_unavailable" and reply["reason"] == "no_eligible_staff", "an unidentified person is never eligible"


def test_ids_take_precedence_over_names():
    tools, _ = stack(svc(eligible_staff=frozenset({"maya"}), eligible_staff_ids=frozenset({"1001"})))
    assert ask(tools)["options"][0]["technician"] == "Lily" and ask(tools, "Maya")["reason"] == "staff_not_eligible"


def test_an_empty_id_set_means_nobody():
    tools, _ = stack(svc(eligible_staff_ids=frozenset()))
    assert ask(tools)["reason"] == "no_eligible_staff"


def test_without_ids_the_older_name_based_eligibility_still_works():
    tools, _ = stack(svc(eligible_staff=frozenset({"maya"})), ids={})
    assert ask(tools)["options"][0]["technician"] == "Maya"


@pytest.mark.parametrize("name, staff_id, expected", [("Lily", "1001", True), ("Lily", "", False), ("Lily", "1002", False), ("Anyone", "1001", True)])
def test_the_service_decides_by_id_alone_when_ids_are_given(name, staff_id, expected):
    assert svc(eligible_staff_ids=frozenset({"1001"})).eligible(name, staff_id) is expected


def test_two_people_with_one_display_name_are_still_not_guessed_between_even_with_ids():
    tools, _ = stack(svc(eligible_staff_ids=frozenset({"1001", "1002"})), roster=("Alex Kim", "Alex Kim"), ids={"Alex Kim": "1001"})
    reply = ask(tools, "Alex")
    assert reply["status"] == "unknown" and reply["reason"] == "ambiguous_roster"
