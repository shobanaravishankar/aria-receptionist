"""The demo dataset: invented, deterministic, explicit about what it covers, and shaped like a real salon's week. No browser, no network."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from aria_booking.catalog.lookup import DEFAULT_CATALOG
from aria_booking.demo import DEMO_BUSINESS_ID
from aria_booking.demo.dataset import (
    COVERAGE_AFTER_DAYS, COVERAGE_BEFORE_DAYS, FIRST_NAMES, GRID_END, GRID_START, ROSTER, DemoDataset, NotCovered, demo_registry,
)

TODAY = date(2026, 10, 13)  # a Tuesday
LABELS = ["Elva", "Pinny Du", "Maggie Jin", "Anna Zheng", "Ada", "Lily Xie", "Linda Chen", "Amy Wu", "Jessica DU", "Fiona Tsao"]  # observed labels, reference only


@pytest.fixture(scope="module")
def dataset():
    return DemoDataset(TODAY)


def all_days(dataset):
    day = dataset.first_day
    while day <= dataset.last_day:
        yield day
        day += timedelta(days=1)


# ---------------------------------------------------------------- the roster


def test_the_roster_uses_the_observed_labels_with_synthetic_ids():
    assert [s.name for s in ROSTER] == LABELS
    assert all(re.fullmatch(r"9100\d\d", s.staff_id) for s in ROSTER) and len({s.staff_id for s in ROSTER}) == len(ROSTER)
    assert DEMO_BUSINESS_ID == "9000001"


def test_the_hidden_internal_test_account_is_not_in_the_demo_roster():
    assert "Aria" not in {s.name for s in ROSTER}, "it would be confused with the voice agent's own name"


def test_one_person_has_no_hours_shown_like_the_real_owner_column():
    unknown = [s for s in ROSTER if not s.hours_known]
    assert [s.name for s in unknown] == ["Fiona Tsao"]


def test_no_roster_name_is_silently_a_neighbour_of_another():
    names = {s.name.casefold() for s in ROSTER}
    assert "tinny" not in names and not any(n.split()[0] == "tinny" for n in names), "'Tinny' is not a roster name and must never be mapped to 'Pinny Du'"


# ---------------------------------------------------------------- coverage and determinism


def test_the_window_is_explicit_and_everything_outside_it_is_not_covered(dataset):
    assert dataset.first_day == TODAY - timedelta(days=COVERAGE_BEFORE_DAYS) and dataset.last_day == TODAY + timedelta(days=COVERAGE_AFTER_DAYS)
    dataset.day_plan(dataset.first_day)
    dataset.day_plan(dataset.last_day)
    for outside in (dataset.first_day - timedelta(days=1), dataset.last_day + timedelta(days=1), date(2030, 1, 1)):
        assert not dataset.covers(outside)
        with pytest.raises(NotCovered):
            dataset.day_plan(outside)


def test_the_same_date_always_gives_the_same_schedule():
    a, b = DemoDataset(TODAY), DemoDataset(TODAY)
    for day in (TODAY, TODAY + timedelta(days=3), TODAY + timedelta(days=20)):
        assert a.day_plan(day) == b.day_plan(day)


def test_an_ordinary_day_does_not_move_when_the_demo_date_moves():
    later = DemoDataset(TODAY + timedelta(days=1))
    far = TODAY + timedelta(days=12)  # not scripted in either dataset
    assert DemoDataset(TODAY).day_plan(far) == later.day_plan(far)


def test_the_demo_date_is_adjustable_and_the_scripted_days_follow_it():
    for today in (date(2026, 10, 13), date(2026, 12, 25), date(2027, 2, 28)):
        ds = DemoDataset(today)
        assert ds.scripted_days == (today + timedelta(days=1), today + timedelta(days=2))
        assert ds.covers(today) and ds.covers(today + timedelta(days=COVERAGE_AFTER_DAYS))


# ---------------------------------------------------------------- the schedule is internally sound


def test_every_visit_is_inside_the_persons_hours_and_clear_of_breaks_and_other_visits(dataset):
    for day in all_days(dataset):
        for plan in dataset.day_plan(day).plans:
            if plan.hours is None:
                continue
            assert GRID_START <= plan.hours[0] < plan.hours[1] <= GRID_END
            visits = sorted(plan.visits, key=lambda v: v.start)
            for v in visits:
                assert plan.hours[0] <= v.start < v.end <= plan.hours[1], (day, plan.staff.name, v)
                assert v.start % 15 == 0 and v.end % 5 == 0, "appointments start on the quarter hour and end on the five-minute grid"
                assert not any(v.start < b.end and b.start < v.end for b in plan.breaks), (day, plan.staff.name, v)
            for earlier, later in zip(visits, visits[1:]):
                assert earlier.end <= later.start, (day, plan.staff.name, earlier, later)
            for b in plan.breaks:
                assert plan.hours[0] <= b.start < b.end <= plan.hours[1]


def test_visits_are_of_services_the_person_is_eligible_for(dataset):
    registry, by_name = dataset.registry, {}
    for service_id in registry.ids():
        by_name[registry.get(service_id).booksy_name] = registry.get(service_id)
    for day in all_days(dataset):
        for plan in dataset.day_plan(day).plans:
            for v in plan.visits:
                service = by_name[v.service] if v.service in by_name else None
                if service is not None:  # the scripted days use readable labels such as "Swedish Massage, 1 hour"
                    assert service.eligible(plan.staff.name, plan.staff.staff_id), (plan.staff.name, v.service)
                assert v.category in plan.staff.categories, (plan.staff.name, v)


def test_the_person_with_no_shown_hours_has_no_visits_and_no_header_hours(dataset):
    for day in all_days(dataset):
        for plan in dataset.day_plan(day).plans:
            if not plan.staff.hours_known:
                assert plan.visits == () and plan.shown_hours is None


def test_utilisation_is_realistic_not_empty_and_not_solid(dataset):
    booked = open_ = 0
    for day in all_days(dataset):
        for plan in dataset.day_plan(day).plans:
            if plan.hours and plan.staff.hours_known:
                booked += sum(v.end - v.start for v in plan.visits)
                open_ += plan.hours[1] - plan.hours[0]
    assert 0.25 < booked / open_ < 0.75, booked / open_


def test_the_week_has_varied_staff_days_and_all_categories_appear(dataset):
    shown_counts, categories = set(), set()
    for day in all_days(dataset):
        plan = dataset.day_plan(day)
        shown_counts.add(len(plan.plans))
        categories |= {v.category for p in plan.plans for v in p.visits}
    assert len(shown_counts) >= 3 and categories == {"massage", "facial", "lash", "waxing", "head-spa"}


def test_clients_are_fictional_and_carry_no_contact_details(dataset):
    clients = set()
    for day in all_days(dataset):
        for plan in dataset.day_plan(day).plans:
            clients |= {v.client for v in plan.visits}
    assert clients and all(re.fullmatch(r"[A-Z][a-z]+ [A-Z]\.", c) for c in clients)
    assert {c.split()[0] for c in clients} <= set(FIRST_NAMES)
    assert not any(re.search(r"\d|@|\+", c) for c in clients)


# ---------------------------------------------------------------- the scripted days pin the interesting cases


def minute(hhmm: str) -> int:
    hour, mins = hhmm.split(":")
    return int(hour) * 60 + int(mins)


def plan_of(dataset, day, name):
    return next((p for p in dataset.day_plan(day).plans if p.staff.name == name), None)


@pytest.mark.parametrize("today", [date(2026, 10, 13), date(2026, 10, 17), date(2026, 11, 1)])
def test_tomorrow_always_has_the_pinned_cases_whatever_the_weekday(today):
    ds = DemoDataset(today)
    tomorrow = ds.scripted_days[0]
    lily, elva, pinny, linda = (plan_of(ds, tomorrow, n) for n in ("Lily Xie", "Elva", "Pinny Du", "Linda Chen"))
    assert not any(v.start < minute("20:00") and minute("15:00") < v.end for v in lily.visits), "Lily is free from 3 PM"
    assert any(v.start <= minute("16:00") < v.end for v in elva.visits), "Elva is booked at 4 PM"
    assert any(v.start == minute("17:00") for v in pinny.visits) and not any(v.start < minute("17:00") and v.end > minute("15:00") for v in pinny.visits)
    assert [(b.start, b.end) for b in linda.breaks] == [(minute("15:00"), minute("17:00"))]
    assert plan_of(ds, tomorrow, "Amy Wu") is None, "off duty"
    fiona = plan_of(ds, tomorrow, "Fiona Tsao")
    assert fiona is not None and fiona.shown_hours is None


def test_the_second_scripted_day_is_a_different_mix(dataset):
    first, second = dataset.scripted_days
    assert plan_of(dataset, second, "Amy Wu") is not None and plan_of(dataset, first, "Amy Wu") is None
    assert plan_of(dataset, second, "Elva").breaks


def test_describe_day_lists_everyone_without_inventing_contact_details(dataset):
    lines = dataset.describe_day(dataset.scripted_days[0])
    text = "\n".join(lines)
    assert "Lily Xie" in text and re.search(r"not working: .*Amy Wu", text) and "hours not shown" in text
    assert not re.search(r"@|\d{3}[- ]\d{3}[- ]\d{4}", text)


# ---------------------------------------------------------------- the service registry


def test_every_demo_service_is_a_real_website_item_with_its_listed_length_and_a_synthetic_staff_mapping(dataset):
    registry = dataset.registry
    ids = registry.ids()
    assert len(ids) >= 30
    staff_ids = {s.staff_id for s in ROSTER}
    for service_id in ids:
        service = registry.get(service_id)
        item = DEFAULT_CATALOG.get(service.catalog_item)
        assert item is not None and item.duration_minutes == service.duration_minutes
        assert service.eligible_staff_ids and service.eligible_staff_ids <= staff_ids
        assert service.buffer_after_minutes >= 0


def test_massage_comes_in_three_lengths_and_the_listed_buffers_apply():
    registry = demo_registry()
    lengths = sorted(registry.get(i).duration_minutes for i in registry.ids() if i.startswith("massage-deep-tissue"))
    assert lengths == [30, 60, 90]
    assert registry.get("massage-deep-tissue-massage-60").buffer_after_minutes == 15
    assert registry.get("waxing-eyebrows-15").buffer_after_minutes == 0


def test_eligibility_follows_the_synthetic_category_assignment():
    registry = demo_registry()
    massage = registry.get("massage-swedish-massage-60")
    by_name = {s.name: s for s in ROSTER}
    assert massage.eligible("x", by_name["Lily Xie"].staff_id) and not massage.eligible("x", by_name["Maggie Jin"].staff_id)
    assert not massage.eligible("Lily Xie", ""), "a person whose id is unknown is never eligible"


def test_an_extra_person_can_be_added_for_ambiguity_tests_without_touching_the_real_roster():
    from aria_booking.demo.dataset import DemoStaff

    twin = DemoStaff("910099", "Amy Lin", frozenset({0, 1, 2, 3, 4, 5, 6}), (600, 1200), frozenset({"massage"}))
    ds = DemoDataset(TODAY, extra_staff=[twin])
    assert len(ds.roster) == len(ROSTER) + 1 and len(ROSTER) == 10
    assert ds.registry.get("massage-swedish-massage-60").eligible("x", "910099")


# ---------------------------------------------------------------- review MD1/MD2 (Sol): restarts and labels


def _subprocess_json(script: str, seed: str) -> str:
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(Path.cwd()))
    result = subprocess.run([sys.executable, "-c", script], env=env, cwd=Path.cwd(), capture_output=True, text=True, check=True, timeout=60)
    return result.stdout


def test_same_demo_day_repeats_after_process_restart():  # Sol's MD1 reproduction, unchanged in substance
    script = '''
import json
from datetime import date
from aria_booking.demo.dataset import DemoDataset
plan = DemoDataset(date(2026, 10, 13)).day_plan(date(2026, 10, 16))
print(json.dumps([(p.staff.name, p.hours,
                  [(v.start, v.end, v.service, v.client) for v in p.visits])
                 for p in plan.plans]))
'''
    import json

    schedules = [json.loads(_subprocess_json(script, seed)) for seed in ("1", "2")]
    assert schedules[0] == schedules[1], "The same demo date changes when Python hash randomization changes across launches"


def test_scripted_appointment_label_agrees_with_its_block_length():  # Sol's MD2 reproduction, unchanged in substance
    from aria_booking.catalog.lookup import duration_hint

    ds = DemoDataset(TODAY)
    mismatches = []
    for day in ds.scripted_days:
        for plan in ds.day_plan(day).plans:
            for visit in plan.visits:
                stated = duration_hint(visit.service)
                actual = visit.end - visit.start
                if stated is not None and stated != actual:
                    mismatches.append((day.isoformat(), plan.staff.name, visit.service, stated, actual))
    assert not mismatches, mismatches


def test_the_whole_window_and_the_page_are_identical_under_any_hash_seed():
    script = '''
import hashlib
from datetime import date, timedelta
from aria_booking.demo.dataset import DemoDataset
from aria_booking.demo.page import render_page
ds = DemoDataset(date(2026, 10, 13))
digest = hashlib.sha256()
day = ds.first_day
while day <= ds.last_day:
    digest.update(repr([(p.staff.staff_id, p.hours, p.breaks, p.visits) for p in ds.day_plan(day).plans]).encode())  # data only: a frozenset's repr order is not data
    digest.update(render_page(ds, day).encode())
    day += timedelta(days=1)
print(digest.hexdigest())
'''
    digests = {_subprocess_json(script, seed).strip() for seed in ("0", "1", "2", "3", "random")}
    assert len(digests) == 1, "every covered day (schedule AND page) must be the same in every fresh process"


def test_the_service_pool_is_built_in_a_fixed_order_not_from_a_set(dataset):
    import inspect

    from aria_booking.demo import dataset as module

    assert "sorted(staff.categories)" in inspect.getsource(module.DemoDataset._generated)


def test_every_visit_label_is_a_real_demo_service_whose_length_matches_its_block(dataset):
    lengths = {dataset.registry.get(i).booksy_name: dataset.registry.get(i).duration_minutes for i in dataset.registry.ids()}
    checked = 0
    day = dataset.first_day
    while day <= dataset.last_day:
        for plan in dataset.day_plan(day).plans:
            for visit in plan.visits:
                assert visit.service in lengths, f"{visit.service!r} is not a demo service name"
                assert lengths[visit.service] == visit.end - visit.start, (day, plan.staff.name, visit)
                checked += 1
        day += timedelta(days=1)
    assert checked > 500


def test_the_scripted_four_pm_examples_still_hold_after_the_label_fix(dataset):
    first = dataset.scripted_days[0]
    lily = plan_of(dataset, first, "Lily Xie")
    assert [(v.start, v.end, v.service) for v in lily.visits] == [(690, 750, "Swedish Massage, 1 hour")]
    assert all(v.end <= minute("15:00") for v in lily.visits), "she is still free from 3 PM, so 4 PM stays available"
    linda = plan_of(dataset, first, "Linda Chen")
    assert [(v.start, v.end, v.service) for v in linda.visits] == [(600, 615, "Eyebrows")]
