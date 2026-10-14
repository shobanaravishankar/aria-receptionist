"""The stored appointments on the demo calendar, the commit-time judgement, and the seeded fictional clients.

The point of these tests is AGREEMENT: the calendar page, the voice tools' read driver and the commit-time judge all see the same day, because they all go
through ``DemoDataset.day_plan``. Every test here changes the store the way a real action would and then looks at all of them.
"""

from __future__ import annotations

import re
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from aria_booking.appointments.judge import PlacementJudge
from aria_booking.appointments.models import CANCELLED, CONFIRMED, NewBooking, Placement, Refused, to_epoch
from aria_booking.appointments.overlay import StoreOverlay, day_bounds, display_name
from aria_booking.appointments.seed import ANSEL, CEDRIC, SEEDED_CLIENTS, UPCOMING, WILLA, seed_demo
from aria_booking.appointments.store import AppointmentStore
from aria_booking.demo import SPA_TIMEZONE
from aria_booking.demo.dataset import FIRST_NAMES, DemoDataset
from aria_booking.demo.driver import DemoCalendarDriver
from aria_booking.demo.launch import build_demo, demo_clock
from aria_booking.demo.page import render_page
from aria_booking.history.phone import normalize_phone
from aria_booking.models import Interval

TZ = ZoneInfo(SPA_TIMEZONE)
TODAY = date(2026, 10, 13)  # a Tuesday; the scripted days are Wednesday the 14th and Thursday the 15th
TOMORROW = TODAY + timedelta(days=1)
PHONE = "+12125550142"
SWEDISH_60 = "massage-swedish-massage-60"
SIENNA, MARISOL, IMOGEN, DELPHINE = "910006", "910001", "910008", "910010"  # Sienna is free from 3 PM on the first scripted day; Imogen is off; Delphine's hours are not shown


class World:
    def __init__(self, tmp_path, today=TODAY, hour=10, name="world.sqlite3"):
        self.now = datetime(today.year, today.month, today.day, hour, 0, tzinfo=TZ)
        self.clock = demo_clock(today, TZ, real_now=lambda: self.now)
        self.store = AppointmentStore(tmp_path / name, clock=lambda: to_epoch(self.clock()))
        self.dataset = DemoDataset(today, overlay=StoreOverlay(self.store, TZ))
        self.judge = PlacementJudge(self.dataset, tz=TZ, clock=self.clock)
        self.endpoint, self.driver, _ = build_demo("test-key", today=today, real_now=lambda: self.now, dataset=self.dataset)
        self.tools = self.endpoint._tools

    def at(self, day: date, hour: int, minute: int = 0) -> int:
        return to_epoch(datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ))

    def new(self, hour=16, *, day=TOMORROW, staff=SIENNA, service=SWEDISH_60, name="Willa Hartwell", phone=PHONE, key=None) -> NewBooking:
        svc = self.dataset.registry.get(service)
        return NewBooking(
            name, phone, svc.service_id, svc.booksy_name, self.dataset.category_of(service), staff, self.at(day, hour), svc.duration_minutes,
            svc.buffer_before_minutes, svc.buffer_after_minutes, request_key=key,
        )

    def book(self, hour=16, **kw):
        return self.store.book(self.new(hour, **kw), validate=self.judge.validator())

    def plan_visits(self, day=TOMORROW, staff=SIENNA):
        plan = next((p for p in self.dataset.day_plan(day).plans if p.staff.staff_id == staff), None)
        return plan.visits if plan else ()

    def snapshot_appointments(self, day=TOMORROW, staff_name="Sienna"):
        sd = self.driver.read_day(day).for_staff(staff_name)
        return [(a.interval.start.astimezone(TZ).strftime("%H:%M"), a.interval.end.astimezone(TZ).strftime("%H:%M")) for a in sd.appointments]

    def check(self, hour=16, day=TOMORROW, staff="Sienna", service=SWEDISH_60) -> dict:
        return self.tools.check_slot("call-1", {"service_id": service, "date": day.isoformat(), "time": f"{hour:02d}:00", "staff": staff})


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# ---------------------------------------------------------------- one source: page, driver, voice and judge agree


def test_a_booking_is_on_the_page_in_the_plan_in_the_driver_and_blocks_voice_availability(world):
    assert world.check()["status"] == "available"
    out = world.book()
    assert out.status == "booked"
    ref = out.appointment.reference
    visit = next(v for v in world.plan_visits() if v.reference == ref)
    assert (visit.start, visit.end, visit.client, visit.service) == (16 * 60, 17 * 60, "Willa H.", "Swedish Massage, 1 hour")
    page = render_page(world.dataset, TOMORROW)
    assert f'data-appointment-id="{ref}"' in page and "Willa H." in page and "Hartwell" not in page and "2125550142" not in page
    assert ("16:00", "17:00") in world.snapshot_appointments()
    after = world.check()
    assert after["status"] != "available", "voice now says the time is taken"


def test_a_cancellation_frees_the_slot_on_the_page_in_the_driver_and_in_voice_immediately(world):
    ref = world.book().appointment.reference
    assert world.store.cancel(ref).status == "cancelled"
    assert all(v.reference != ref for v in world.plan_visits()) and f'data-appointment-id="{ref}"' not in render_page(world.dataset, TOMORROW)
    assert ("16:00", "17:00") not in world.snapshot_appointments()
    assert world.check()["status"] == "available"
    assert world.book(name="Someone Else", phone="+12125550101").status == "booked", "and it can be booked again"


def test_a_move_is_at_the_new_time_only_and_voice_agrees(world):
    ref = world.book().appointment.reference
    moved = world.store.reschedule(ref, start_utc=world.at(TOMORROW, 18), validate=world.judge.validator())
    assert moved.status == "rescheduled"
    assert [(v.start, v.end) for v in world.plan_visits() if v.reference == ref] == [(18 * 60, 19 * 60)]
    assert ("16:00", "17:00") not in world.snapshot_appointments() and ("18:00", "19:00") in world.snapshot_appointments()
    assert world.check(16)["status"] == "available" and world.check(18)["status"] != "available"


def test_a_stored_appointment_replaces_a_generated_filler_visit_under_it_and_nothing_else_changes(world):
    day = TODAY + timedelta(days=5)
    base = world.dataset.base_day_plan(day)
    plan = next(p for p in base.plans if p.visits)
    victim = plan.visits[0]
    start = world.at(day, victim.start // 60, victim.start % 60)
    svc = world.dataset.registry.get(SWEDISH_60)
    row = world.store.book(NewBooking("Willa Hartwell", PHONE, svc.service_id, svc.booksy_name, "massage", plan.staff.staff_id, start, svc.duration_minutes, 0, 15, origin="test")).appointment
    merged = next(p for p in world.dataset.day_plan(day).plans if p.staff.staff_id == plan.staff.staff_id)
    assert victim not in merged.visits and any(v.reference == row.reference for v in merged.visits)
    assert all(a.end <= b.start for a, b in zip(merged.visits, merged.visits[1:])), "no two cards overlap"
    others = [p for p in base.plans if p.staff.staff_id != plan.staff.staff_id]
    assert [p for p in world.dataset.day_plan(day).plans if p.staff.staff_id != plan.staff.staff_id] == others


def test_a_technician_who_is_off_that_day_but_has_an_appointment_is_shown_with_no_hours_and_cannot_take_new_ones(world):
    assert all(p.staff.staff_id != IMOGEN for p in world.dataset.base_day_plan(TOMORROW).plans), "Imogen is off on the first scripted day"
    svc = world.dataset.registry.get("lash-eyelash-lifting-45")
    row = world.store.book(NewBooking("Willa Hartwell", PHONE, svc.service_id, svc.booksy_name, "lash", IMOGEN, world.at(TOMORROW, 12), 45, 0, 0, origin="test")).appointment
    plan = next(p for p in world.dataset.day_plan(TOMORROW).plans if p.staff.staff_id == IMOGEN)
    assert plan.shown_hours is None and [v.reference for v in plan.visits] == [row.reference]
    with pytest.raises(Refused) as why:
        world.judge.require(Placement(IMOGEN, world.at(TOMORROW, 15), 45, 0, 0, svc.service_id, "lash"), world.store.between)
    assert why.value.status == "unknown", "unknown is never free"
    assert ("12:00", "12:45") in world.snapshot_appointments(staff_name="Imogen") or world.driver.read_day(TOMORROW).for_staff("Imogen").working is None


def test_display_names_never_show_more_than_a_first_name_and_an_initial():
    assert display_name("Willa Hartwell") == "Willa H." and display_name("Ana de la Cruz") == "Ana C." and display_name("Cher") == "Cher" and display_name("  Mia   Ray ") == "Mia R."


def test_a_day_is_the_local_wall_clock_day_across_a_clock_change():
    low, high = day_bounds(date(2026, 11, 1), TZ)  # the clocks go back this day: 25 hours
    assert high - low == 25 * 3600
    low, high = day_bounds(date(2026, 3, 8), TZ)  # and forward: 23
    assert high - low == 23 * 3600


# ---------------------------------------------------------------- the commit-time judgement


@pytest.mark.parametrize("change,expected", [
    (dict(hour=9), "not_free"),                     # before the day starts (outside hours)
    (dict(hour=19), "not_free"),                    # a 60-minute massage plus cleanup would run past the 8 PM close
    (dict(hour=11), "not_free"),                    # Sienna's scripted 11:30 visit
    (dict(staff="910007", hour=16), "not_free"),    # Rowan's scripted break
    (dict(staff=DELPHINE, hour=12, service="facial-signature-facial-60"), "calendar_unknown"),  # hours not shown: unknown is not free
    (dict(day=TODAY + timedelta(days=60)), "not_covered"),
])
def test_the_judge_refuses_what_check_slot_would_refuse_and_says_which_rule(world, change, expected):
    kw = dict(hour=16, day=TOMORROW, staff=SIENNA, service=SWEDISH_60)
    kw.update(change)
    with pytest.raises(Refused) as why:
        world.judge.require(Placement.of(world.new(**kw)), world.store.between)
    assert why.value.reason == expected


def test_the_minimum_notice_and_a_day_off_are_their_own_refusals(world):
    working = next(p for p in world.dataset.day_plan(TODAY).plans if p.staff.staff_id != DELPHINE and p.shown_hours and "massage" in p.staff.categories)
    with pytest.raises(Refused) as soon:
        world.judge.require(Placement.of(world.new(hour=10, day=TODAY, staff=working.staff.staff_id)), world.store.between)  # the clock says 10:00
    assert soon.value.reason == "not_free" and any("too soon" in r for r in soon.value.detail["reasons"])
    with pytest.raises(Refused) as off:
        world.judge.require(Placement.of(world.new(hour=14, staff=IMOGEN, service="massage-swedish-massage-60")), world.store.between)  # Imogen is off the first scripted day
    assert off.value.status == "unavailable" and off.value.reason == "not_working"


def test_the_judge_never_accepts_the_callers_length_or_an_ineligible_technician_or_an_off_grid_time(world):
    good = world.new()
    shortened = Placement(good.staff_id, good.start_utc, 30, 0, 15, good.service_id, good.category)
    for placement, reason in [
        (shortened, "service_facts_mismatch"),
        (Placement(MARISOL, world.at(TOMORROW, 16), 60, 0, 10, "facial-signature-facial-60", "facial"), "staff_not_eligible"),  # Marisol does not do facials
        (Placement(SIENNA, world.at(TOMORROW, 16, 10), 60, 0, 15, SWEDISH_60, "massage"), "off_grid"),
        (Placement("999999", world.at(TOMORROW, 16), 60, 0, 15, SWEDISH_60, "massage"), "unknown_staff"),
        (Placement(SIENNA, world.at(TOMORROW, 16), 60, 0, 15, "no-such-service", "massage"), "unknown_service"),
    ]:
        with pytest.raises(Refused) as why:
            world.judge.require(placement, world.store.between)
        assert why.value.reason == reason and why.value.status == "refused"
    assert world.judge.require(Placement.of(good), world.store.between).staff_name == "Sienna"


def test_a_slot_that_was_free_at_confirmation_is_refused_at_commit_if_someone_took_it_meanwhile(world):
    judged = world.judge.require(Placement.of(world.new()), world.store.between)  # the dry run: free
    assert judged.staff_name == "Sienna"
    assert world.book(name="Quicker Caller", phone="+12125550101").status == "booked"
    late = world.store.book(world.new(), validate=world.judge.validator())  # the confirmed commit
    assert late.status == "unavailable" and late.reason == "not_free" and "snapshot" in late.detail
    assert world.store.counts()["appointments"] == {"confirmed": 1}


def test_racing_commits_through_the_real_judge_book_exactly_one(world):
    results, gate = [], threading.Barrier(6)

    def go(i):
        gate.wait()
        results.append(world.book(name=f"Guest {i}", phone=f"+1212555010{i}"))

    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.status for r in results) == ["booked"] + ["unavailable"] * 5
    assert len([v for v in world.plan_visits() if v.reference]) == 1


def test_a_move_is_judged_without_its_own_old_place_and_a_refused_move_changes_nothing(world):
    ref = world.book().appointment.reference
    nudge = world.store.reschedule(ref, start_utc=world.at(TOMORROW, 16, 30), validate=world.judge.validator())
    assert nudge.status == "rescheduled", "16:30-17:30 overlaps its own old 16:00-17:00, which is not a conflict"
    refused = world.store.reschedule(ref, start_utc=world.at(TOMORROW, 11), validate=world.judge.validator())  # onto Sienna's 11:30 visit
    assert refused.status == "unavailable" and world.store.get(ref).start_utc == world.at(TOMORROW, 16, 30) and world.store.get(ref).version == 2


# ---------------------------------------------------------------- the seeded fictional clients


def seeded(world):
    return seed_demo(world.store, world.judge, world.clock, TZ)


def test_seeding_makes_the_upcoming_and_past_records_once_with_no_notifications(world):
    first = seeded(world)
    assert len(first.upcoming_added) == len(UPCOMING) and first.upcoming_skipped == [] and first.history_added == 7
    counts = world.store.counts()
    assert counts["appointments"] == {"confirmed": 4, "completed": 5, "cancelled": 1, "no_show": 1} and counts["clients"] == 3 and counts["outbox"] == {}
    again = seeded(world)
    assert again.upcoming_added == [] and again.history_added == 0 and world.store.counts() == counts


def test_every_seeded_upcoming_appointment_is_a_valid_placement_on_the_demo_calendar(world):
    seeded(world)
    now = to_epoch(world.now)
    for client in (WILLA, ANSEL):
        for client_record in world.store.clients_with_phone(normalize_phone(client[1])):
            for row in world.store.for_client(client_record.client_id, (CONFIRMED,)):
                assert row.start_utc > now and row.origin == "seed"
                placement = Placement(row.staff_id, row.start_utc, row.duration_minutes, row.buffer_before_minutes, row.buffer_after_minutes, row.service_id, row.category, row.reference)
                world.judge.require(placement, world.store.between)  # raises if it clashed with the generated schedule or another seeded row


def test_willas_upcoming_appointments_match_the_illustrative_week(world):
    seeded(world)
    willa = next(c for c in world.store.clients_with_phone(PHONE) if c.name == "Willa Hartwell")
    rows = world.store.for_client(willa.client_id, (CONFIRMED,))
    assert sorted((r.category, r.start.astimezone(TZ).strftime("%A")) for r in rows) == sorted([("waxing", "Tuesday"), ("head-spa", "Friday"), ("massage", "Sunday")])
    assert len(world.store.clients_with_phone(PHONE)) == 2, "the household number belongs to two records, so a name is needed to tell them apart"


def test_seeded_references_are_stable_across_a_restart_a_rebuilt_file_and_a_new_day(tmp_path):
    first = World(tmp_path)
    made = seeded(first).upcoming_added
    refs = {r.reference for r in first.store.between(0, 2**40, statuses=(CONFIRMED, "completed", CANCELLED, "no_show"))}
    assert len(refs) == 11
    restarted = World(tmp_path)  # a new process on the same file
    again = seeded(restarted)
    assert again.upcoming_added == [] and {r.reference for r in restarted.store.between(0, 2**40, statuses=(CONFIRMED, "completed", CANCELLED, "no_show"))} == refs
    rebuilt = World(tmp_path, name="fresh.sqlite3")  # a different file, the same demo date: the same references
    seeded(rebuilt)
    assert {r.reference for r in rebuilt.store.between(0, 2**40, statuses=(CONFIRMED, "completed", CANCELLED, "no_show"))} == refs
    monday = World(tmp_path, today=date(2026, 10, 19))  # the demo restarts next week
    topped = seeded(monday)
    kept = {r.reference for r in monday.store.between(0, 2**40, statuses=(CONFIRMED, "completed", CANCELLED, "no_show"))}
    assert refs <= kept, "nothing that existed changed identity"
    passed = {template for template, ref in made if first.store.get(ref).start < monday.now.astimezone(first.store.get(ref).start.tzinfo)}
    assert passed and {t for t, _ in topped.upcoming_added} == passed, "only the templates whose seeded row had already passed were made again"
    assert "tue-waxing" not in passed, "the Tuesday waxing is still ahead on the new date, so it is left alone"
    assert topped.history_added == 0


def test_a_cancelled_seed_is_not_brought_back_while_it_is_still_ahead(world):
    seeded(world)
    willa = next(c for c in world.store.clients_with_phone(PHONE) if c.name == "Willa Hartwell")
    tuesday = next(r for r in world.store.for_client(willa.client_id, (CONFIRMED,)) if r.category == "waxing")
    assert world.store.cancel(tuesday.reference).status == "cancelled"
    assert seeded(world).upcoming_added == []
    assert world.store.get(tuesday.reference).status == CANCELLED


def test_seeded_identities_are_fictional_and_reserved_and_cannot_be_confused_with_staff(world):
    names = {first.split()[0] for first, _ in SEEDED_CLIENTS} | {c[0].split()[0] for c in (WILLA, ANSEL, CEDRIC)}
    staff = {s.name for s in world.dataset.roster}
    assert not (names & staff) and not (names & set(FIRST_NAMES))
    for _name, phone in SEEDED_CLIENTS:
        assert re.fullmatch(r"\+1\d{3}5550[1]\d\d", phone), "the 555-01xx range is reserved for fiction: no real line"
    from aria_booking.showcase.leaks import find_leaks

    page = render_page(world.dataset, TODAY + timedelta(days=2))
    assert find_leaks(page) == []


def test_the_dataset_without_a_store_is_exactly_the_generated_calendar_and_refuses_rows():
    plain = DemoDataset(TODAY)
    assert plain.day_plan(TOMORROW) == plain.base_day_plan(TOMORROW)
    with pytest.raises(ValueError):
        plain.day_plan(TOMORROW, rows=[object()])


# ---------------------------------------------------------------- the merge's own defences


def test_the_merge_itself_ignores_rows_that_are_not_confirmed(world):
    from dataclasses import replace

    from aria_booking.appointments.overlay import merge_rows

    row = world.book().appointment
    base = world.dataset.base_day_plan(TOMORROW)
    for status in ("cancelled", "completed", "no_show"):
        assert merge_rows(base, [replace(row, status=status)], TZ, world.dataset.roster) is base


def test_a_technician_added_only_because_of_a_stored_appointment_keeps_the_rosters_order(world):
    from aria_booking.appointments.overlay import merge_rows

    roster = list(world.dataset.roster)
    for offset in range(1, 30):
        day = TODAY + timedelta(days=offset)
        shown = {p.staff.staff_id for p in world.dataset.base_day_plan(day).plans}
        missing = [s for s in roster if s.staff_id not in shown and s.hours_known]
        later = [s for s in roster if s.staff_id in shown and roster.index(s) > roster.index(missing[0])] if missing else []
        if later:
            break
    else:
        pytest.skip("no day in the window where a technician is absent before a present one")
    svc = world.dataset.registry.get("massage-swedish-massage-60")
    row = world.store.book(NewBooking("Willa Hartwell", PHONE, svc.service_id, svc.booksy_name, "massage", missing[0].staff_id, world.at(day, 12), 60, 0, 15, origin="test")).appointment
    order = [p.staff.staff_id for p in world.dataset.day_plan(day).plans]
    assert order == [s.staff_id for s in roster if s.staff_id in order] and missing[0].staff_id in order and row.reference


def test_a_service_that_is_not_verified_can_never_be_placed(world, monkeypatch):
    from dataclasses import replace

    registry = world.dataset.registry
    service = registry.get(SWEDISH_60)
    original = type(registry).get
    monkeypatch.setattr(type(registry), "get", lambda self, sid: replace(service, verified=False) if sid == SWEDISH_60 else original(self, sid))
    with pytest.raises(Refused) as why:
        world.judge.require(Placement.of(world.new()), world.store.between)
    assert why.value.reason == "unknown_service"


# ---------------------------------------------------------------- opening the world


def test_open_lifecycle_builds_one_consistent_world_and_seeds_it_once(tmp_path):
    from aria_booking.appointments.lifecycle import default_store_path, open_lifecycle

    path = tmp_path / "nested" / "appointments.sqlite3"
    noon = datetime(2026, 10, 13, 12, 0, tzinfo=TZ)
    first = open_lifecycle(path, today=TODAY, real_now=lambda: noon)
    assert first.seeded.history_added == 7 and len(first.seeded.upcoming_added) == 4 and first.dataset.today == TODAY
    second = open_lifecycle(path, today=TODAY, real_now=lambda: noon)
    assert second.seeded.upcoming_added == [] and second.store.counts() == first.store.counts()
    bare = open_lifecycle(tmp_path / "bare.sqlite3", today=TODAY, real_now=lambda: noon, seed=False)
    assert bare.seeded is None and bare.store.counts()["appointments"] == {}
    assert default_store_path(tmp_path).parts[-2:] == ("demo-appointments", "appointments.sqlite3")
    assert first.judge.now() == noon and first.store.get(first.seeded.upcoming_added[0][1]).status == CONFIRMED


def test_a_stored_appointments_cleanup_is_drawn_and_blocks_page_driver_voice_and_judge_alike(world):
    ref = world.book().appointment.reference  # Swedish 60 with Sienna 16:00-17:00, 15 minutes of cleanup after
    plan = next(p for p in world.dataset.day_plan(TOMORROW).plans if p.staff.staff_id == SIENNA)
    assert [(b.start, b.end, b.label) for b in plan.breaks if b.label == "Cleanup"] == [(17 * 60, 17 * 60 + 15, "Cleanup")]
    assert [(v.start, v.end) for v in plan.visits if v.reference == ref] == [(16 * 60, 17 * 60)], "the card and every spoken length stay the service time"
    page = render_page(world.dataset, TOMORROW)
    assert "Cleanup" in page and f'data-appointment-id="{ref}"' in page
    sd = world.driver.read_day(TOMORROW).for_staff("Sienna")
    at_five = datetime(TOMORROW.year, TOMORROW.month, TOMORROW.day, 17, 0, tzinfo=TZ)
    assert not any(w.start <= at_five < w.end for w in sd.working), "no working window covers the cleanup, so every reader treats it as time nobody can book"
    assert world.check(17)["status"] != "available", "voice no longer offers the minute the first appointment ends"
    assert world.check(18)["status"] == "available"
    quarter = world.tools.check_slot("c", {"service_id": SWEDISH_60, "date": TOMORROW.isoformat(), "time": "17:15", "staff": "Sienna"})
    assert quarter["status"] == "available", "the first start after the cleanup is offered"
    with pytest.raises(Refused) as inside:
        world.judge.require(Placement.of(world.new(17)), world.store.between)
    assert inside.value.reason == "not_free"
    from dataclasses import replace

    quarter_past = replace(world.new(17), start_utc=world.at(TOMORROW, 17, 15))
    assert world.judge.require(Placement.of(quarter_past), world.store.between).staff_name == "Sienna"
    assert world.book(17, name="Too Soon", phone="+12125550109").status == "unavailable"
    assert world.store.book(world.new(17, name="Bypass", phone="+12125550110"), validate=None).status == "unavailable", "the store refuses it even with no validator at all"


def test_overlapping_cleanup_and_a_scripted_break_are_joined_so_the_readers_never_see_overlapping_breaks(world):
    from aria_booking.appointments.overlay import _union
    from aria_booking.demo.dataset import Break

    joined = _union((Break(900, 960, "Lunch"), Break(950, 975, "Cleanup"), Break(975, 990, "Cleanup"), Break(1000, 1010, "Cleanup")))
    assert [(b.start, b.end, b.label) for b in joined] == [(900, 975, "Break"), (975, 990, "Cleanup"), (1000, 1010, "Cleanup")]
    assert _union((Break(10, 20, "Cleanup"), Break(15, 30, "Cleanup")))[0].label == "Cleanup"


def test_an_appointments_preparation_time_before_it_is_drawn_and_blocks_like_its_cleanup(world):
    svc = world.dataset.registry.get(SWEDISH_60)
    row = world.store.book(NewBooking("Willa Hartwell", PHONE, svc.service_id, svc.booksy_name, "massage", SIENNA, world.at(TOMORROW, 17), 60, 30, 0, origin="test")).appointment
    plan = next(p for p in world.dataset.day_plan(TOMORROW).plans if p.staff.staff_id == SIENNA)
    assert [(b.start, b.end, b.label) for b in plan.breaks if b.label == "Cleanup"] == [(16 * 60 + 30, 17 * 60, "Cleanup")]
    assert [(v.start, v.end) for v in plan.visits if v.reference == row.reference] == [(17 * 60, 18 * 60)]
    assert world.judge.require(Placement.of(world.new(15)), world.store.between).staff_name == "Sienna", "15:00-16:00 plus its own cleanup ends at 16:15, before the preparation begins"
    with pytest.raises(Refused) as inside:
        world.judge.require(Placement.of(world.new(16)), world.store.between)  # 16:00-17:00 runs into the preparation (from 16:30) and the appointment
    assert inside.value.reason == "not_free"
