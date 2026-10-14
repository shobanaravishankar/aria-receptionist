"""The website's 'open times': derived from the same calendar and the same availability engine as the voice assistant, with the same honest outcomes.

The agreement test is the heart of it: for many services, days and times, the set of (time, technician) pairs the page lists equals exactly the set the voice's own check_slot
calls available, so the page can neither promise a time Aria would refuse nor hide one she would offer.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from aria_booking.appointments.lifecycle import open_lifecycle
from aria_booking.appointments.models import NewBooking, to_epoch
from aria_booking.demo.dataset import ROSTER, DayPlan, DemoDataset, StaffPlan, Visit
from aria_booking.demo.launch import build_demo
from aria_booking.demo.open_times import open_times

TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 14)
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)
SWEDISH60, MICRO75, SIGNATURE60, FULLLEG40 = "massage-swedish-massage-60", "facial-microdermabrasion-facial-75", "facial-signature-facial-60", "waxing-full-leg-40"
BY_NAME = {s.name: s for s in ROSTER}


def voice(dataset):
    endpoint, _driver, _ds = build_demo("k", today=TODAY, real_now=lambda: NOW, dataset=dataset)
    return endpoint._tools


def pairs(reply: dict) -> set:
    return {(t["start"], tech) for t in reply.get("times", []) for tech in t["technicians"]}


def voice_pairs(tools, dataset, service_id, day) -> set:
    """Every (time, technician) the voice itself calls available, asked one technician and one quarter hour at a time."""
    service = dataset.registry.get(service_id)
    found = set()
    for staff in dataset.roster:
        if not service.eligible(staff.name, staff.staff_id):
            continue
        for minutes in range(9 * 60, 21 * 60, 15):
            at = f"{minutes // 60:02d}:{minutes % 60:02d}"
            reply = tools.check_slot("agree", {"service_id": service_id, "date": day.isoformat(), "time": at, "staff": staff.name})
            if reply["status"] == "available":
                found.add((at, staff.name))
    return found


# ---------------------------------------------------------------- agreement with the voice


@pytest.mark.parametrize("service", [SWEDISH60, MICRO75, FULLLEG40])
def test_the_page_lists_exactly_the_times_the_voice_calls_available(service):
    dataset = DemoDataset(TODAY)
    tools = voice(dataset)
    for offset in (1, 2, 5, 9):
        day = TODAY + timedelta(days=offset)
        reply = open_times(dataset, service, day, tz=TZ, now=NOW)
        assert pairs(reply) == voice_pairs(tools, dataset, service, day), (service, day)


def test_a_named_technician_narrows_it_to_that_person_and_agrees_with_the_voice():
    dataset = DemoDataset(TODAY)
    tools = voice(dataset)
    day = TODAY + timedelta(days=1)
    reply = open_times(dataset, SWEDISH60, day, tz=TZ, now=NOW, staff="Sienna")
    assert reply["status"] == "open" and {tech for _t, tech in pairs(reply)} == {"Sienna"}
    assert pairs(reply) == {p for p in voice_pairs(tools, dataset, SWEDISH60, day) if p[1] == "Sienna"}


def test_the_full_service_and_its_cleanup_must_fit_and_only_eligible_technicians_are_considered():
    dataset = DemoDataset(TODAY)
    day = TODAY + timedelta(days=1)
    reply = open_times(dataset, MICRO75, day, tz=TZ, now=NOW)  # 75 minutes plus 10 of cleanup
    assert reply["duration_minutes"] == 75 and all(t["start"] <= "18:45" for t in reply["times"]), "must finish, cleanup included, by 8 PM"
    assert "Sienna" not in {tech for _t, tech in pairs(reply)}, "Sienna does massage and head spa, not facials"
    wrong = open_times(dataset, MICRO75, day, tz=TZ, now=NOW, staff="Sienna")
    assert wrong["status"] == "error" and wrong["reason"] == "staff_not_eligible" and "doesn't do" in wrong["message"]


def test_minimum_notice_and_the_past_are_respected_today():
    dataset = DemoDataset(TODAY)
    today = open_times(dataset, SIGNATURE60, TODAY, tz=TZ, now=NOW)  # it is noon, 60 minutes' notice
    assert all(t["start"] >= "13:00" for t in today["times"]), today["times"][:3]


def test_stored_appointments_and_their_cleanup_disappear_from_the_page_and_come_back_when_cancelled(tmp_path):
    lc = open_lifecycle(tmp_path / "ot.sqlite3", today=TODAY, real_now=lambda: NOW, seed=False)
    day = TODAY + timedelta(days=1)
    svc = lc.dataset.registry.get(SWEDISH60)
    before = pairs(open_times(lc.dataset, SWEDISH60, day, tz=TZ, now=NOW, staff="Sienna"))
    assert ("16:00", "Sienna") in before and ("17:15", "Sienna") in before
    row = lc.store.book(NewBooking("Mira Quillfeather", "+12125550188", svc.service_id, svc.booksy_name, "massage", "910006", to_epoch(datetime(2026, 10, 15, 16, tzinfo=TZ)), 60, 0, 15),
                        validate=lc.judge.validator()).appointment
    during = pairs(open_times(lc.dataset, SWEDISH60, day, tz=TZ, now=NOW, staff="Sienna"))
    assert ("16:00", "Sienna") not in during and ("17:00", "Sienna") not in during and ("17:15", "Sienna") in during, "the booking and its cleanup are gone, the next start is not"
    lc.store.cancel(row.reference)
    assert pairs(open_times(lc.dataset, SWEDISH60, day, tz=TZ, now=NOW, staff="Sienna")) == before


# ---------------------------------------------------------------- honest outcomes


class Crafted(DemoDataset):
    """A dataset whose one day is exactly what a test draws, to reach states the generated calendar does not."""

    def __init__(self, plans, **kw):
        super().__init__(TODAY, **kw)
        self._crafted = plans

    def day_plan(self, day, *, rows=None):
        return DayPlan(day, tuple(self._crafted))


def plan(name, *, visits=(), hours=(600, 1200), known=True):
    from dataclasses import replace

    staff = replace(BY_NAME[name], hours_known=known)
    return StaffPlan(staff, hours, (), tuple(Visit(a, b, "Swedish Massage, 1 hour", "massage", "Someone Else Q.") for a, b in visits))


def test_none_when_everyone_who_does_it_is_fully_booked_and_it_says_so():
    full = Crafted([plan("Sienna", visits=[(600, 1200)]), plan("Marisol", visits=[(600, 1200)])])
    reply = open_times(full, SWEDISH60, TODAY + timedelta(days=1), tz=TZ, now=NOW)
    assert reply["status"] == "none" and reply["times"] == [] and "No openings for the full 1 hour of Swedish Massage, 1 hour" in reply["message"]


def test_unknown_when_nothing_is_open_and_a_schedule_could_not_be_read():
    mixed = Crafted([plan("Sienna", visits=[(600, 1200)]), plan("Marisol", known=False)])
    reply = open_times(mixed, SWEDISH60, TODAY + timedelta(days=1), tz=TZ, now=NOW)
    assert reply["status"] == "unknown" and reply["unreadable"] == ["Marisol"] and reply["times"] == []
    assert "can't confirm" in reply["message"] and "nothing is claimed" in reply["message"] and "No openings" not in reply["message"], "'no openings' is not claimed"


def test_open_with_a_note_when_some_times_exist_and_some_schedule_could_not_be_read_and_the_unreadable_person_is_never_listed():
    mixed = Crafted([plan("Sienna"), plan("Marisol", known=False)])
    reply = open_times(mixed, SWEDISH60, TODAY + timedelta(days=1), tz=TZ, now=NOW)
    assert reply["status"] == "open" and reply["partial"] is True and "Marisol" in reply["message"]
    assert {tech for _t, tech in pairs(reply)} == {"Sienna"}


def test_nobody_working_a_named_person_off_duty_an_unknown_service_and_a_day_outside_the_calendar():
    dataset = DemoDataset(TODAY)
    nobody = open_times(Crafted([plan("Calista")]), SWEDISH60, TODAY + timedelta(days=1), tz=TZ, now=NOW)
    assert nobody["status"] == "none" and nobody["message"].startswith("Nobody who does Swedish Massage, 1 hour is working on")
    off = open_times(dataset, SWEDISH60, TODAY + timedelta(days=1), tz=TZ, now=NOW, staff="Imogen")  # Imogen is off the first scripted day
    assert off["status"] == "none" and off["message"].startswith("Imogen isn't working on")
    assert open_times(dataset, "no-such-service", TODAY, tz=TZ, now=NOW)["reason"] == "unknown_service"
    far = open_times(dataset, SWEDISH60, TODAY + timedelta(days=400), tz=TZ, now=NOW)
    assert far["status"] == "outside" and far["times"] == []
    assert far["message"] == f"The calendar only covers {dataset.first_day.isoformat()} to {dataset.last_day.isoformat()}, so times for that day can't be shown."


def test_the_page_never_carries_a_clients_name_or_a_hidden_appointment():
    dataset = DemoDataset(TODAY)
    clients = {v.client for p in dataset.day_plan(TODAY + timedelta(days=1)).plans for v in p.visits}
    assert clients
    text = json.dumps([open_times(dataset, s, TODAY + timedelta(days=1), tz=TZ, now=NOW) for s in (SWEDISH60, MICRO75, FULLLEG40)])
    assert not any(c in text for c in clients), "no client name"
    assert set(json.loads(text)[0]) >= {"status", "times", "service", "date"} and all(set(t) == {"start", "label", "technicians"} for r in json.loads(text) for t in r.get("times", []))
