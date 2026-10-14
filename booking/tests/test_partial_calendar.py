"""When some schedules cannot be read, and what is kept within one call.

The standard demo's schedules are complete, so an answer about a time is derived from the requested service, day, time and the calendar data alone. Missing hours exist only in
an explicit diagnostic dataset; there, UNKNOWN IS NEVER FREE, but the answer says what is actually the case (the readable schedules cannot take it, and why; whose hours are not
shown) instead of sounding like an outage, and offers genuinely open times from the readable schedules. Nothing here is specific to 4 PM or to any one date.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aria_booking.demo.dataset import DemoDataset
from aria_booking.demo.launch import build_demo

TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 14)
MICRO75 = "facial-microdermabrasion-facial-75"
SIGNATURE60 = "facial-signature-facial-60"
DEEP60 = "massage-deep-tissue-massage-60"
SRC = Path(__file__).resolve().parent.parent / "aria_booking"
OUTAGE = re.compile(r"didn.t respond|not responding|is down|unavailable right now|couldn.t read the calendar|try again in a moment|fully booked|completely booked|booked solid", re.I)


def tools(*, diagnostic=False):
    dataset = DemoDataset(TODAY, diagnostic_unknown_hours=("910010",) if diagnostic else ())
    endpoint, _driver, dataset = build_demo("k", today=TODAY, real_now=lambda: datetime(2026, 10, 14, 12, tzinfo=TZ), dataset=dataset)
    return endpoint._tools, dataset


def ask(t, service, day, time, staff=None, call="c"):
    args = {"service_id": service, "date": day.isoformat(), "time": time, **({"staff": staff} if staff else {})}
    return t.check_slot(call, args)


# ---------------------------------------------------------------- mixed known and unknown (the diagnostic scenario)


def test_some_schedules_unable_and_one_unreadable_says_so_without_sounding_like_an_outage_and_offers_real_alternatives():
    t, dataset = tools(diagnostic=True)
    reply = ask(t, MICRO75, TODAY + timedelta(days=1), "16:00")
    assert reply["status"] == "unknown" and reply["ok"] is False and reply["reason"] == "partial_calendar" and reply["incomplete_schedules"] == ["Delphine"]
    speak = reply["speak"]
    assert speak.startswith("I can't confirm Thursday, October 15 at 4 PM for the full 1 hour 15 minutes of Microdermabrasion Facial. None of the technicians whose schedules I can read can take it.")
    assert "would run into Tamsin's next appointment at 5 PM" in speak, "the readable schedules' actual reason"
    assert "Delphine's working hours aren't shown on the calendar, so I can't say whether Delphine could take it" in speak, "whose hours are missing, not guessed"
    assert not OUTAGE.search(speak), speak
    assert reply["options"] and all(o["technician"] != "Delphine" for o in reply["options"]), "nothing is offered from the schedule that cannot be read"
    for option in reply["options"]:  # every offer is genuinely free for the full service with that person
        day, hhmm = option["start"][:10], option["start"][11:16]
        again = t.check_slot("verify", {"service_id": MICRO75, "date": day, "time": hhmm, "staff": option["technician"]})
        assert again["status"] == "available", option


def test_a_named_unreadable_person_stays_fail_closed_with_no_alternatives_invented():
    t, _ = tools(diagnostic=True)
    reply = ask(t, MICRO75, TODAY + timedelta(days=1), "16:00", staff="Delphine")
    assert reply["status"] == "unknown" and not reply.get("options") and "Let me not guess" in reply["speak"] and reply.get("reason") != "partial_calendar"


def test_an_unreadable_schedule_never_makes_a_time_free_and_the_diagnostic_never_leaks_into_the_standard_demo():
    standard, _ = tools()
    diagnostic, _ = tools(diagnostic=True)
    for offset in range(1, 8):
        day = TODAY + timedelta(days=offset)
        for hhmm in ("10:00", "13:30", "16:00", "19:00"):
            a, b = ask(standard, SIGNATURE60, day, hhmm), ask(diagnostic, SIGNATURE60, day, hhmm, call="d")
            assert a["status"] != "unknown", (day, hhmm, a["speak"])
            if b["status"] == "available":  # whatever the diagnostic offers is free in the standard demo too, with someone who is not Delphine
                assert b["options"][0]["technician"] != "Delphine" and a["status"] == "available"


def test_the_standard_demo_reports_that_everyone_is_busy_and_offers_actual_alternatives_without_forcing_any_time():
    t, _ = tools()
    reply = ask(t, MICRO75, TODAY + timedelta(days=1), "16:00")
    assert reply["status"] == "alternatives" and reply["ok"] is False
    assert reply["speak"].startswith("Everyone working that day who does this is booked or unavailable at 4 PM for the full 1 hour 15 minutes.")
    assert reply["options"] and not any(o["start"].startswith("2026-10-15T16:00") for o in reply["options"]), "4 PM was not turned into an opening"
    for option in reply["options"]:
        assert t.check_slot("v", {"service_id": MICRO75, "date": option["start"][:10], "time": option["start"][11:16], "staff": option["technician"]})["status"] == "available"


# ---------------------------------------------------------------- nothing is a script: arbitrary services, days and times, derived from the calendar


@pytest.mark.parametrize("diagnostic", [False, True], ids=["standard", "diagnostic"])
def test_every_answer_for_arbitrary_services_days_and_times_agrees_with_the_calendar_it_is_derived_from(diagnostic):
    t, dataset = tools(diagnostic=diagnostic)
    seen = set()
    for service in (MICRO75, SIGNATURE60, DEEP60, "waxing-full-leg-40", "lash-eyelash-lifting-45"):
        for offset in (1, 2, 3, 9, 16):
            for hhmm in ("10:15", "12:00", "14:30", "17:45", "19:30"):
                day = TODAY + timedelta(days=offset)
                reply = ask(t, service, day, hhmm, call=f"{service}-{offset}-{hhmm}")
                seen.add(reply["status"])
                assert reply["status"] in ("available", "alternatives", "no_alternatives", "unknown", "needs_clarification"), reply
                assert not OUTAGE.search(reply["speak"]) or reply["status"] == "no_alternatives", reply["speak"]
                if reply["status"] == "available":
                    technician = reply["options"][0]["technician"]
                    staff = next(s for s in dataset.roster if s.name == technician)
                    assert staff.hours_known and dataset.registry.get(service).eligible(staff.name, staff.staff_id)
                if reply["status"] == "unknown":
                    assert diagnostic, "the standard demo never answers 'cannot confirm' for a covered day"
                    assert reply["reason"] == "partial_calendar" or "Let me not guess" in reply["speak"]
                for option in reply.get("options", []):
                    again = t.check_slot("v", {"service_id": service, "date": option["start"][:10], "time": option["start"][11:16], "staff": option["technician"]})
                    assert again["status"] == "available", (service, day, hhmm, option)
    assert {"available", "alternatives"} <= seen, seen


def test_the_four_oclock_reproduction_is_not_hard_coded_anywhere_in_the_answering_code():
    for module in ("voice/tools.py", "appointments/tools.py", "demo/driver.py", "demo/launch.py", "availability.py"):
        text = (SRC / module).read_text(encoding="utf-8")
        assert not re.search(r'"16:00"|16 \* 60|\b4 ?PM\b|Delphine', text.replace("# ", "#")), module


def test_asking_for_a_different_time_gives_a_different_answer_from_the_data():
    t, _ = tools()
    day = TODAY + timedelta(days=1)
    four, eleven = ask(t, MICRO75, day, "16:00"), ask(t, MICRO75, day, "11:15", call="e")
    assert four["status"] == "alternatives" and eleven["status"] == "available", (four["status"], eleven["status"])
    assert four["speak"] != eleven["speak"] and eleven["options"][0]["start"].startswith("2026-10-15T11:15")


# ---------------------------------------------------------------- the caller's time is kept within a call, and only there


def labels(reply):
    return [o["label"] for o in reply.get("options", [])]


def alt(t, call, day=TODAY + timedelta(days=1), service=MICRO75, **extra):
    return t.find_alternatives(call, {"service_id": service, "date": day.isoformat(), **extra})


def test_a_follow_up_search_without_a_time_keeps_the_time_the_caller_asked_for_in_this_call():
    t, _ = tools()
    ask(t, MICRO75, TODAY + timedelta(days=1), "16:00", call="one")
    kept = alt(t, "one")
    explicit = alt(t, "two", time="16:00")
    assert labels(kept) == labels(explicit) and kept["options"], "the same as if she had said 4 PM again"
    assert labels(alt(t, "three")) != labels(kept), "a call that never asked for a time gets the plain default search"


def test_a_new_time_replaces_the_old_one_and_any_time_drops_it():
    t, _ = tools()
    ask(t, MICRO75, TODAY + timedelta(days=1), "16:00", call="one")
    moved = alt(t, "one", time="11:00")
    assert labels(moved) == labels(alt(t, "ref", time="11:00"))
    assert labels(alt(t, "one")) == labels(moved), "the new time is the one that is kept"
    plain = alt(t, "default")
    assert labels(alt(t, "one", any_time=True)) == labels(plain), "any time works: back to the default search"
    assert labels(alt(t, "one")) == labels(plain), "and it stays dropped"


def test_the_time_belongs_to_the_call_and_follows_a_change_of_service_or_technician_within_it():
    t, _ = tools()
    ask(t, MICRO75, TODAY + timedelta(days=1), "16:00", call="one")
    other_service = alt(t, "one", service=SIGNATURE60)
    assert labels(other_service) == labels(alt(t, "ref", service=SIGNATURE60, time="16:00")), "she still wants 4 PM for the other treatment"
    ask(t, DEEP60, TODAY + timedelta(days=2), "09:15", call="late")
    assert labels(alt(t, "one")) == labels(alt(t, "ref2", time="16:00")), "another call's time never reaches this one"


def test_the_remembered_times_are_bounded():
    t, _ = tools()
    for n in range(700):
        t._remember_time(f"call-{n}", 600)
    assert len(t._preferred_minutes) <= 512 and "call-699" in t._preferred_minutes and "call-0" not in t._preferred_minutes


def test_the_tool_files_offer_any_time_on_find_alternatives_for_the_demo_agents():
    import json

    draft = Path(__file__).resolve().parent.parent / "voice_agent_draft"
    for name in ("tools_availability_only.json", "tools_demo_lifecycle.json"):
        find = next(tool for tool in json.loads((draft / name).read_text(encoding="utf-8"))["tools"] if tool["name"] == "find_alternatives")
        assert find["parameters"]["properties"]["any_time"]["type"] == "boolean" and "any_time" not in find["parameters"]["required"], name
        assert "the time the caller last asked for in this call is kept" in find["parameters"]["properties"]["time"]["description"], name
