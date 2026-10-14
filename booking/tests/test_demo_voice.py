"""The demo's voice answers, through the SAME tool path the live calendar uses (VoiceTools -> deadlines -> read-only routes), over the demo dataset.

Scripted day (the demo date plus one): Sienna is free from 3 PM; Marisol is booked 3:45-5:15 PM; Tamsin has a 5:00 PM visit; Rowan is on a 3-5 PM
break; Imogen is off; Delphine works a complete day (her missing-hours column is a diagnostic scenario). Nothing here touches a browser, Booksy or the network except one loopback HTTP test.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aria_booking.demo import DEMO_LABEL
from aria_booking.demo.dataset import ROSTER, DemoDataset, DemoStaff
from aria_booking.demo.driver import DemoCalendarDriver
from aria_booking.demo.launch import SOURCE_LABEL, SOURCE_NOTE, build_demo
from aria_booking.voice.retell_http import READ_ONLY_ROUTES, ROUTES

from test_launch import KEY, TS, signed

TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 13)
TOMORROW = TODAY + timedelta(days=1)
DEEP60, DEEP90, DEEP30 = "massage-deep-tissue-massage-60", "massage-deep-tissue-massage-90", "massage-deep-tissue-massage-30"
FACIAL = "facial-signature-facial-60"
BOOKING = Path(__file__).resolve().parent.parent


def real_now():
    return datetime(2026, 10, 13, 12, 0, tzinfo=TZ)


def make(extra_staff=(), **kw):
    dataset = DemoDataset(TODAY, extra_staff=extra_staff)
    endpoint, driver, dataset = build_demo(KEY, today=TODAY, real_now=real_now, dataset=dataset, **kw)
    return endpoint._tools, endpoint, driver, dataset


def ask(tools, staff=None, time="16:00", service=DEEP60, day=TOMORROW, call="call_1"):
    args = {"service_id": service, "date": day.isoformat(), "time": time}
    if staff is not None:
        args["staff"] = staff
    return tools.check_slot(call, args)


# ---------------------------------------------------------------- the questions Shobana wants to be able to ask


def test_is_the_requested_person_available_tomorrow_at_4_pm_yes():
    tools, *_ = make()
    reply = ask(tools, "Sienna")
    assert reply["status"] == "available" and reply["options"][0]["technician"] == "Sienna"
    assert "Sienna" in reply["speak"] and "4 PM" in reply["speak"] and "can't book it" in reply["speak"]
    assert reply["ok"] is False and "option_id" not in reply["options"][0]


def test_occupied_says_so_and_offers_that_persons_own_other_times():
    tools, *_ = make()
    reply = ask(tools, "Marisol")
    assert reply["status"] == "alternatives" and reply["unavailable_reason"] == "occupied" and "Marisol is already booked at 4 PM" in reply["speak"]
    assert {o["technician"] for o in reply["options"]} == {"Marisol"}


def test_an_off_duty_person_is_not_on_the_schedule_and_nothing_is_offered_for_her():
    tools, *_ = make()
    reply = ask(tools, "Imogen")
    assert reply["status"] == "staff_unavailable" and reply["reason"] == "staff_not_on_schedule" and not reply.get("options")
    assert "don't see Imogen" in reply["speak"]


def test_a_visit_that_would_cross_into_the_next_appointment_is_refused_with_that_reason():
    tools, *_ = make()
    reply = ask(tools, "Tamsin", service=DEEP90)  # 4:00-5:30 runs into the 5:00 PM visit
    assert reply["unavailable_reason"] == "conflicts_next" and "would run into Tamsin's next appointment at 5 PM" in reply["speak"]
    assert all(o["duration_minutes"] == 90 for o in reply["options"])


def test_the_cleanup_time_counts_a_one_hour_massage_fits_the_gap_only_without_its_buffer():
    tools, *_ = make()
    reply = ask(tools, "Tamsin", service=DEEP60)  # 4:00-5:00 touches the 5:00 visit, but 15 minutes of cleanup follow a massage
    assert reply["unavailable_reason"] == "conflicts_next" and "wouldn't be time for the 15 minutes of cleanup" in reply["speak"]
    assert ask(tools, "Tamsin", service=DEEP30)["status"] == "available", "a half-hour fits with its cleanup"


def test_a_break_is_explained_as_time_off_not_as_outside_hours():
    tools, *_ = make()
    reply = ask(tools, "Rowan")
    assert reply["unavailable_reason"] == "on_break" and "Rowan is off between 3 PM and 5 PM" in reply["speak"]
    assert "outside" not in reply["speak"]


def test_a_visit_that_would_run_into_a_break_says_the_break_not_the_closing_time():
    tools, *_ = make()
    reply = ask(tools, "Rowan", time="14:30")  # 2:30-3:30 PM (plus cleanup) runs into her 3 PM break
    assert reply["unavailable_reason"] == "conflicts_next" and "would run into Rowan's break at 3 PM" in reply["speak"]
    assert "closing" not in reply["speak"] and "end of" not in reply["speak"]


def test_the_person_with_no_shown_hours_is_unknown_never_available():
    dataset = DemoDataset(TODAY, diagnostic_unknown_hours=("910010",))  # the DIAGNOSTIC scenario: the normal demo has complete schedules
    endpoint, _driver, _dataset = build_demo(KEY, today=TODAY, real_now=real_now, dataset=dataset)
    reply = ask(endpoint._tools, "Delphine", service=FACIAL)
    assert reply["status"] == "unknown" and not reply.get("options") and "free" not in reply["speak"].replace("whether it's free", "")


def test_nobody_named_returns_who_is_actually_free_at_that_time():
    tools, *_ = make()
    reply = ask(tools, None)
    assert reply["status"] == "available" and reply["options"][0]["technician"] == "Sienna"
    reply = ask(tools, None, service=DEEP90)
    assert reply["status"] in ("available", "alternatives") and all(o["technician"] != "Imogen" for o in reply["options"])


def test_a_person_who_does_not_do_the_service_is_said_so():
    tools, *_ = make()
    reply = ask(tools, "Marisol", service=FACIAL)
    assert reply["status"] == "staff_unavailable" and reply["reason"] == "staff_not_eligible"


# ---------------------------------------------------------------- names: ambiguity and near-misses are asked about, never guessed


def test_two_people_with_one_first_name_need_a_question_not_a_guess():
    ash = DemoStaff("910098", "Zuri Ash", frozenset(range(7)), (600, 1200), frozenset({"massage"}))
    bell = DemoStaff("910099", "Zuri Bell", frozenset(range(7)), (600, 1200), frozenset({"massage"}))
    tools, *_ = make(extra_staff=[ash, bell])
    reply = ask(tools, "Zuri")  # neither is called just "Zuri": it is two people, and the caller is asked
    assert reply["status"] == "needs_clarification" and reply["reason"] == "ambiguous_staff"
    assert set(reply["candidates"]) == {"Zuri Ash", "Zuri Bell"} and "Which did you mean" in reply["speak"] and not reply.get("options")
    for full in ("Zuri Ash", "Zuri Bell"):  # the full name is exactly one person: never a question, and never the other twin's times
        answer = ask(tools, full)
        assert answer["status"] != "needs_clarification" and {o["technician"] for o in answer.get("options", [])} <= {full}, answer


def test_a_near_miss_name_is_never_silently_a_roster_member():
    tools, *_ = make()
    reply = ask(tools, "Tamzin")
    assert reply["status"] == "staff_unavailable" and not reply.get("options")
    assert "Tamsin" not in json.dumps({k: v for k, v in reply.items() if k != "scheduled_staff"}) or "don't see Tamzin" in reply["speak"]
    assert "Tamzin" in reply["speak"], "she is told the name was not found, in her own words"


# ---------------------------------------------------------------- missing data and the edges of the dataset


def test_a_date_inside_the_look_ahead_but_outside_the_dataset_is_unknown_not_free():
    tools, *_ = make()
    far = TODAY + timedelta(days=50)  # beyond the 35 covered days, inside the 60-day look-ahead
    reply = ask(tools, "Sienna", day=far)
    assert reply["status"] == "unknown" and not reply.get("options")


def test_a_date_beyond_the_look_ahead_asks_for_a_nearer_one():
    tools, *_ = make()
    assert ask(tools, "Sienna", day=TODAY + timedelta(days=300))["status"] == "needs_clarification"


def test_a_start_after_closing_is_refused_with_the_reason():
    tools, *_ = make()
    reply = ask(tools, "Sienna", time="20:30")
    assert reply["unavailable_reason"] == "outside_hours" and "outside those hours" in reply["speak"]


def test_a_visit_that_would_run_past_the_end_of_the_day():
    tools, *_ = make()
    reply = ask(tools, "Sienna", time="19:30", service=DEEP60)
    assert reply["unavailable_reason"] == "ends_after_closing" and "latest start" in reply["speak"]


def test_the_second_scripted_day_answers_differently():
    tools, *_ = make()
    second = TODAY + timedelta(days=2)
    assert ask(tools, "Imogen", day=second, service="massage-swedish-massage-60")["status"] in ("available", "alternatives"), "Imogen works on the second scripted day"
    assert ask(tools, "Sienna", day=second, time="10:00", service=DEEP60)["unavailable_reason"] == "occupied"


# ---------------------------------------------------------------- the source is always obvious in the conversation


SPOKEN_DEMO_WORDS = ("demo", "simulat", "prototype", "not the salon's real schedule", "invented")


def test_every_availability_reply_carries_the_source_label_and_none_announces_it_by_default():
    tools, *_ = make()
    first = ask(tools, "Sienna", call="callA")
    second = ask(tools, "Marisol", call="callA")
    other_call = ask(tools, "Sienna", call="callB")
    alternatives = tools.find_alternatives("callC", {"service_id": DEEP60, "date": TOMORROW.isoformat(), "staff": "Marisol"})
    for reply in (first, second, other_call, alternatives):
        assert reply["source"] == SOURCE_LABEL, "the machine-readable label stays on every availability reply"
        assert not any(word in reply["speak"].lower() for word in SPOKEN_DEMO_WORDS), reply["speak"]
    assert first["speak"].startswith("Wednesday, October 14 at 4 PM is open"), "the answer itself comes first, with no preamble"


def test_no_spoken_reply_of_any_kind_says_demo_simulated_or_prototype():
    tools, *_ = make()
    far = TODAY + timedelta(days=40)
    replies = []
    for staff in [None, "Sienna", "Marisol", "Imogen", "Delphine", "Tamsin", "Rowan", "Priya", "Tamzin", "Calista"]:
        for service in (DEEP60, DEEP90, FACIAL, "facial-microdermabrasion-facial-75", "head-spa-head-scalp-massage-30"):
            for day in (TOMORROW, TOMORROW + timedelta(days=1), far):
                replies.append(ask(tools, staff, service=service, day=day, call=f"sweep-{len(replies)}"))
    replies += [tools.find_alternatives("sweep-fa", {"service_id": DEEP60, "date": TOMORROW.isoformat()}),
                tools.check_slot("sweep-bad", {"service_id": "facial-acne-skin-facial-60", "date": TOMORROW.isoformat(), "time": "16:00"}),
                tools.check_slot("sweep-missing", {"service_id": DEEP60}),
                tools.lookup_service("sweep-l1", {"query": "microdermabrasion facial"}), tools.lookup_service("sweep-l2", {"query": "deep tissue massage"}),
                tools.lookup_service("sweep-l3", {"query": "acne skin facial"}), tools.lookup_service("sweep-l4", {"query": "no such thing"})]
    assert len(replies) == 10 * 5 * 3 + 7
    for reply in replies:
        assert not any(word in reply["speak"].lower() for word in SPOKEN_DEMO_WORDS), reply["speak"]
    assert {r["source"] for r in replies if r["status"] not in ("service_info", "unknown_service", "choose_variant", "choose_service", "needs_clarification")} == {SOURCE_LABEL}


def test_the_one_time_spoken_note_is_still_there_when_asked_for():
    tools, *_ = make(announce_source=True)
    first = ask(tools, "Sienna", call="callA")
    second = ask(tools, "Marisol", call="callA")
    other_call = ask(tools, "Sienna", call="callB")
    assert first["speak"].startswith(SOURCE_NOTE) and other_call["speak"].startswith(SOURCE_NOTE)
    assert SOURCE_NOTE not in second["speak"], "said once per call, not on every answer"
    assert "demo calendar" in SOURCE_NOTE and "not the salon's real schedule" in SOURCE_NOTE
    assert first["source"] == second["source"] == SOURCE_LABEL


def test_the_local_service_lookup_needs_no_calendar_and_is_not_labelled_as_one(monkeypatch):
    tools, _endpoint, driver, _ds = make()
    reply = tools.lookup_service("callD", {"query": "deep tissue massage 60 minutes"})
    assert "source" not in reply and driver.reads == 0


# ---------------------------------------------------------------- read-only, by construction


def test_there_is_no_booking_route_and_no_booking_function_in_the_demo_endpoint():
    tools, endpoint, *_ = make()
    assert endpoint._routes == READ_ONLY_ROUTES and "book_slot" not in endpoint._routes.values() and tools.availability_only and not tools.booking_enabled
    body = json.dumps({"call": {"call_id": "x"}, "args": {"option_id": "a", "confirmed": True}}).encode()
    endpoint._now = lambda: TS
    status, _payload = endpoint.handle("POST", "/tools/book_slot", signed(body), body)
    assert status == 404
    assert tools.book_slot("x", {"option_id": "a", "confirmed": True})["reason"] == "availability_only"


def test_the_demo_driver_cannot_create_anything_and_never_changes_the_dataset():
    from aria_booking.driver import BeforeSaveError

    _tools, _endpoint, driver, dataset = make()
    before = dataset.day_plan(TOMORROW)
    with pytest.raises(BeforeSaveError, match="read-only"):
        driver.create_appointment(object())
    driver.read_day(TOMORROW)
    assert dataset.day_plan(TOMORROW) == before


def test_a_read_of_a_booking_never_builds_a_booking_service():
    tools, *_ = make()
    for who in ("Sienna", "Marisol", "Imogen", "Rowan", "Tamsin"):
        ask(tools, who)  # build_demo's service factory raises AssertionError if anything ever tries to build a writer


def test_the_endpoint_over_signed_http_semantics_returns_the_label():
    _tools, endpoint, *_ = make()
    body = json.dumps({"call": {"call_id": "callH"}, "args": {"service_id": DEEP60, "date": TOMORROW.isoformat(), "time": "16:00", "staff": "Sienna"}}).encode()
    endpoint._now = lambda: TS  # the signature carries a timestamp: pin the clock to it
    status, payload = endpoint.handle("POST", "/tools/check_slot", signed(body), body)
    assert status == 200 and payload["status"] == "available" and payload["source"] == SOURCE_LABEL


def test_the_log_line_for_a_demo_call_holds_no_arguments_and_no_names():
    lines = []
    _tools, endpoint, *_ = make(log=lines.append)
    body = json.dumps({"call": {"call_id": "callL"}, "args": {"service_id": DEEP60, "date": TOMORROW.isoformat(), "time": "16:00", "staff": "Sienna"}}).encode()
    endpoint._now = lambda: TS
    endpoint.handle("POST", "/tools/check_slot", signed(body), body)
    assert lines and all("Sienna" not in l and "callL" not in l and DEEP60 not in l for l in lines)


# ---------------------------------------------------------------- the driver and the demo wiring


def test_the_driver_answers_exactly_what_the_dataset_says_for_every_covered_day():
    dataset = DemoDataset(TODAY)
    driver = DemoCalendarDriver(dataset, clock=real_now)
    day = dataset.first_day
    while day <= dataset.last_day:
        snapshot = driver.read_day(day)
        plan = dataset.day_plan(day)
        assert [sd.staff for sd in snapshot.staff_days] == [p.staff.name for p in plan.plans]
        for sd, p in zip(snapshot.staff_days, plan.plans):
            assert sd.staff_id == p.staff.staff_id and sd.time_off == ()
            assert (sd.working is None) == (p.shown_hours is None)
            assert len(sd.appointments) == len(p.visits)
        day += timedelta(days=1)


def test_the_driver_follows_the_dataset_rather_than_the_real_calendar_date():
    tools, _e, driver, _d = make()
    assert driver.verify_business() == "9000001"
    assert driver.last_roster is None
    driver.read_day(TOMORROW)
    assert len(driver.last_roster.members) == len(ROSTER) and {m.name for m in driver.last_roster.members if m.checked} >= {"Sienna", "Marisol"}


def test_tomorrow_means_the_day_after_the_demo_date_not_the_real_date():
    from aria_booking.demo.launch import demo_clock

    clock = demo_clock(date(2026, 12, 24), TZ, real_now)
    assert clock().date() == date(2026, 12, 24) and clock().hour == 12


def test_importing_the_demo_never_loads_a_browser_driver():
    code = (
        "import sys; import aria_booking.demo, aria_booking.demo.dataset, aria_booking.demo.driver, aria_booking.demo.launch, aria_booking.demo.page, aria_booking.demo.server;"
        "bad=[m for m in ('selenium','playwright') if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=BOOKING, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_measured_tool_latency_is_tiny_and_says_nothing_about_a_spoken_call(capsys):
    import time

    tools, *_ = make()
    began = time.perf_counter()
    for i in range(50):
        ask(tools, "Sienna", call=f"lat{i}")
    per_call_ms = (time.perf_counter() - began) * 1000 / 50
    with capsys.disabled():
        print(f"\nDEMO tool latency (dataset driver, no browser, no network): {per_call_ms:.2f} ms per check_slot. NOT spoken-call latency; the 2-3 s requirement is unproven.")
    assert per_call_ms < 250
