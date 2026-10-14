"""Sol's first-voice-test finding: Microdermabrasion Facial is on the website but was not in the demo mapping, so Aria discussed it, offered availability, and the
tool answered not_bookable. Now: it is in the demo mapping with its catalog length and the synthetic facial-technician assignment; who does a service is
answered from the demo roster (labelled demo-only); and a service that is NOT in the mapping can never cause a calendar read or a promised check.

No browser, no network, no Booksy.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from aria_booking.catalog.lookup import DEFAULT_CATALOG
from aria_booking.config import Config
from aria_booking.demo import DEMO_BUSINESS_ID
from aria_booking.demo.dataset import ROSTER, DemoDataset, demo_registry
from aria_booking.demo.driver import DemoCalendarDriver
from aria_booking.voice.speech import join_names
from aria_booking.voice.tools import VoiceTools

from test_demo_voice import DEEP60, TODAY, TOMORROW, TZ, ask, make

MICRO = "facial-microdermabrasion-facial-75"
FACIAL_STAFF = ["Pinny Du", "Maggie Jin", "Anna Zheng", "Ada", "Jessica DU", "Fiona Tsao"]


# ---------------------------------------------------------------- the mapping


def test_microdermabrasion_is_in_the_demo_mapping_with_its_website_length_and_price():
    item = DEFAULT_CATALOG.get(MICRO)
    assert item.name == "Microdermabrasion Facial" and item.duration_minutes == 75 and item.price_usd == 149
    service = demo_registry().get(MICRO)
    assert service is not None and service.verified and service.duration_minutes == 75 and service.price_usd == 149 and service.buffer_after_minutes == 10


def test_its_staff_are_exactly_the_demo_roster_members_assigned_the_facial_category():
    service = demo_registry().get(MICRO)
    facial = {staff.staff_id for staff in ROSTER if "facial" in staff.categories}
    assert set(service.eligible_staff_ids) == facial
    lily = next(s for s in ROSTER if s.name == "Lily Xie")
    assert lily.categories == frozenset({"massage", "head-spa"}) and lily.staff_id not in service.eligible_staff_ids, "Lily is not silently made facial-eligible"


def test_the_demo_dataset_says_who_does_a_service_in_roster_order():
    dataset = DemoDataset(TODAY)
    assert dataset.providers(dataset.registry.get(MICRO)) == FACIAL_STAFF
    assert dataset.providers(dataset.registry.get(DEEP60)) == ["Elva", "Pinny Du", "Lily Xie", "Linda Chen", "Amy Wu"]


def test_names_are_joined_with_and_not_or():
    assert join_names(["A"]) == "A" and join_names(["A", "B"]) == "A and B" and join_names(["A", "B", "C"]) == "A, B and C"


# ---------------------------------------------------------------- who provides the service (lookup, no calendar)


def test_lookup_names_the_demo_technicians_and_says_where_the_list_comes_from():
    tools, _endpoint, driver, _dataset = make()
    reply = tools.lookup_service("call_x", {"query": "microdermabrasion facial"})
    assert reply["status"] == "service_info" and reply["bookable"] is True and reply["bookable_service_id"] == MICRO
    assert reply["technicians"] == FACIAL_STAFF and reply["technicians_source"] == "demo_calendar"
    assert " Pinny Du, Maggie Jin, Anna Zheng, Ada, Jessica DU and Fiona Tsao do this service." in reply["speak"]
    assert not any(word in reply["speak"].lower() for word in ("demo", "simulat", "prototype")), "the source is in technicians_source, not spoken"
    assert "I can check availability for it." in reply["speak"] and driver.reads == 0, "a lookup never reads the calendar"


def test_a_service_with_no_demo_mapping_has_no_technician_list_and_is_information_only():
    tools, _endpoint, driver, _dataset = make()
    reply = tools.lookup_service("call_y", {"query": "acne skin facial"})
    assert reply["status"] == "service_info" and reply["bookable"] is False and "technicians" not in reply and "bookable_service_id" not in reply
    assert "can't book it online yet" in reply["speak"] and "simulated demo calendar" not in reply["speak"] and driver.reads == 0


def test_the_list_is_only_ever_given_for_a_calendar_that_is_labelled_as_not_the_real_one():
    dataset = DemoDataset(TODAY)
    clock = lambda: datetime(2026, 10, 13, 12, tzinfo=TZ)  # noqa: E731
    driver = DemoCalendarDriver(dataset, timezone="America/New_York", clock=clock)
    unlabelled = VoiceTools(Config(business_id=DEMO_BUSINESS_ID), driver, lambda *a, **k: None, clock,
                            registry=dataset.registry, availability_only=True, providers=dataset.providers)  # providers given, but NO source label
    reply = unlabelled.lookup_service("call_z", {"query": "microdermabrasion facial"})
    assert reply["bookable"] is True and "technicians" not in reply and "simulated demo calendar" not in reply["speak"]


# ---------------------------------------------------------------- the calendar, for that service


def test_find_alternatives_now_offers_real_demo_times_with_facial_technicians():
    tools, *_ = make()
    reply = tools.find_alternatives("call_a", {"service_id": MICRO, "date": TOMORROW.isoformat(), "time": "16:00"})
    assert reply["status"] in ("alternatives", "available") and reply["status"] != "not_bookable"
    assert reply["options"] and {o["technician"] for o in reply["options"]} <= set(FACIAL_STAFF)
    assert all(o["service_id"] == MICRO and o["duration_minutes"] == 75 for o in reply["options"])


def test_a_technician_who_does_not_offer_it_is_refused_by_name_not_swapped_and_the_right_people_are_offered():
    tools, *_ = make()
    reply = ask(tools, "Lily", service=MICRO)
    assert reply["status"] == "staff_unavailable" and reply["reason"] == "staff_not_eligible"
    assert "Lily Xie doesn't do Microdermabrasion Facial" in reply["speak"]
    assert "Lily Xie" not in reply["available_staff"] and set(reply["available_staff"]) <= set(FACIAL_STAFF) and reply["available_staff"]


def test_a_facial_technician_is_checked_for_the_full_75_minutes():
    tools, *_ = make()
    free = ask(tools, "Anna", service=MICRO)  # Anna Zheng is free from 4 PM on the scripted day
    assert free["status"] == "available" and free["options"][0]["technician"] == "Anna Zheng" and "1 hour 15 minutes" in free["speak"]
    runs_into = ask(tools, "Pinny", service=MICRO)  # 4:00-5:15 PM runs into Pinny Du's 5 PM visit
    assert runs_into["status"] == "alternatives" and runs_into["unavailable_reason"] == "conflicts_next" and "1 hour 15 minutes" in runs_into["speak"]
    booked = ask(tools, "Jessica", service=MICRO)  # Jessica DU has a 3:45-4:45 PM visit
    assert booked["unavailable_reason"] == "occupied" and {o["technician"] for o in booked["options"]} == {"Jessica DU"}


# ---------------------------------------------------------------- unsupported services never cause a calendar read


UNSUPPORTED = [item for item in DEFAULT_CATALOG.items if item.item_id not in demo_registry().ids()]


def test_there_are_unsupported_website_services_so_this_guarantee_is_meaningful():
    assert len(UNSUPPORTED) > 20 and any(i.name == "Acne Skin Facial" for i in UNSUPPORTED)


@pytest.mark.parametrize("function", ["check_slot", "find_alternatives"])
def test_every_website_service_without_a_demo_mapping_answers_not_bookable_without_reading_the_calendar(function):
    tools, _endpoint, driver, _dataset = make()
    for item in UNSUPPORTED:
        reply = getattr(tools, function)("call_u", {"service_id": item.item_id, "date": TOMORROW.isoformat(), "time": "16:00"})
        assert reply["status"] == "not_bookable", (function, item.item_id, reply["status"])
        assert "can't book it online yet" in reply["speak"]
    assert driver.reads == 0


def test_a_service_id_the_model_invents_is_refused_without_a_calendar_read():
    tools, _endpoint, driver, _dataset = make()
    for invented in ("facial-microdermabrasion-75", "microdermabrasion", "facial-acne-facial-45", "massage-deep-tissue-massage-75"):
        reply = tools.check_slot("call_i", {"service_id": invented, "date": TOMORROW.isoformat(), "time": "16:00"})
        assert reply["status"] in ("invalid_service", "not_bookable"), (invented, reply["status"])
    assert driver.reads == 0


# ---------------------------------------------------------------- the prompt says the same


def test_the_prompt_lists_it_as_checkable_and_tells_the_agent_when_not_to_offer_a_check():
    from test_demo_prompt import PROMPT

    assert f"- service_id `{MICRO}`: Microdermabrasion Facial" in PROMPT
    for needle in ("Offer or run a calendar check ONLY for a service under", "do NOT offer a check, say you will check, or call a\n  calendar tool",
                   "Never build a `service_id` yourself", "call `lookup_service`: for a service you can check it returns `technicians`",
                   "never say there is no list when it returned one", "say that technician does not do that\n  service and name those who do"):
        assert needle in PROMPT, needle
    assert PROMPT.index("Offer or run a calendar check ONLY") < PROMPT.index("## While the calendar is being checked")


def test_only_the_demo_prompt_gained_those_rules():
    from pathlib import Path

    draft = Path(__file__).resolve().parent.parent / "voice_agent_draft"
    for name in ("prompt_availability_only.md", "prompt_test_only.md", "prompt_template.md"):
        assert "technicians`" not in (draft / name).read_text(encoding="utf-8"), name
