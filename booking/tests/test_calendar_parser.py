"""The calendar reader, tested against REAL captured pages (redacted structure) plus deliberate corruptions."""

from __future__ import annotations

import copy
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from aria_booking.availability import find_slots, validate_slot
from aria_booking.calendar_parser import CalendarParseError, normalize_nodes, parse_day
from aria_booking.config import Config
from aria_booking.models import ServiceSpec

from conftest import TZ

FIXTURES = Path(__file__).parent / "fixtures"
CAPTURED = datetime(2026, 10, 8, 16, 14, tzinfo=TZ)
SERVICE = ServiceSpec("Aria Salon", 150)


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["nodes"]


def parse(name, day, *, confirmed=True, nodes=None, staff="Shobs"):
    return parse_day(nodes if nodes is not None else load(name), day=day, tz=TZ, staff=staff, staff_confirmed=confirmed, captured_at=CAPTURED)


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


BUSY, MON, SUN = date(2026, 10, 8), date(2026, 10, 12), date(2026, 10, 11)


def only_staff_day(snapshot):
    assert len(snapshot.staff_days) == 1
    return snapshot.staff_days[0]


# ---------------------------------------------------------------- reading the real pages


def test_busy_day_has_the_one_existing_appointment_read_from_the_card_text():
    sd = only_staff_day(parse("busy_day_thu_8_oct.json", BUSY))
    assert len(sd.appointments) == 1
    appt = sd.appointments[0]
    assert (appt.interval.start, appt.interval.end) == (at(BUSY, 15, 15), at(BUSY, 17, 45))
    assert appt.service == "Aria Salon" and appt.blocks_time
    assert appt.note == "", "the note is not on the day card; it is only readable from the details view"


def test_working_hours_are_the_grid_minus_non_working_blocks_limited_to_the_displayed_hours():
    sd = only_staff_day(parse("busy_day_thu_8_oct.json", BUSY))
    assert [(w.start, w.end) for w in sd.working] == [(at(BUSY, 10), at(BUSY, 19))]
    assert sd.time_off == (), "no unrecognised cards on the page, so time off is known to be empty"


@pytest.mark.parametrize("name, day", [("empty_day_mon_12_oct.json", MON), ("empty_day_sun_11_oct.json", SUN)])
def test_empty_days_are_known_to_be_empty_not_unknown(name, day):
    sd = only_staff_day(parse(name, day))
    assert sd.appointments == () and sd.time_off == ()
    assert [(w.start, w.end) for w in sd.working] == [(at(day, 10), at(day, 19))]


def test_the_reader_feeds_the_availability_rules_end_to_end():
    now = datetime(2026, 10, 8, 12, 0, tzinfo=TZ)
    free = find_slots(parse("empty_day_mon_12_oct.json", MON), SERVICE, "Shobs", now=now)
    starts = [s.service.start.strftime("%H:%M") for s in free.slots]
    assert starts[0] == "10:00" and starts[-1] == "16:30"  # 150 minutes must end by 19:00
    assert not free.unknown

    # busy Thursday, "now" 10:00 so the 60-minute lead puts the earliest start at 11:00
    busy_now = datetime(2026, 10, 8, 10, 0, tzinfo=TZ)
    busy_snapshot = parse("busy_day_thu_8_oct.json", BUSY)
    busy = find_slots(busy_snapshot, SERVICE, "Shobs", now=busy_now)
    busy_starts = [s.service.start.strftime("%H:%M") for s in busy.slots]
    # morning gap 10:00-15:15 fits starts up to 12:45 (ends exactly 15:15); the 17:45-19:00 gap is too short
    assert busy_starts == ["11:00", "11:15", "11:30", "11:45", "12:00", "12:15", "12:30", "12:45"]
    assert validate_slot(busy_snapshot, SERVICE, "Shobs", at(BUSY, 12, 45), now=busy_now).ok  # touching is allowed
    clash = validate_slot(busy_snapshot, SERVICE, "Shobs", at(BUSY, 14, 0), now=busy_now)  # 14:00-16:30 overlaps
    assert not clash.ok and any("existing appointment 15:15-17:45" in r for r in clash.reasons)


def test_a_booking_that_would_pass_the_displayed_closing_time_is_declined():
    snap = parse("empty_day_mon_12_oct.json", MON)
    now = datetime(2026, 10, 8, 12, 0, tzinfo=TZ)
    assert validate_slot(snap, SERVICE, "Shobs", at(MON, 16, 30), now=now).ok
    late = validate_slot(snap, SERVICE, "Shobs", at(MON, 17, 0), now=now)
    assert not late.ok and any("outside working hours" in r for r in late.reasons)


def test_the_rehearsed_slot_is_free_on_the_empty_monday():
    now = datetime(2026, 10, 8, 16, 0, tzinfo=TZ)
    assert validate_slot(parse("empty_day_mon_12_oct.json", MON), SERVICE, "Shobs", at(MON, 11, 0), now=now).ok


# ---------------------------------------------------------------- refusing to guess


def test_the_wrong_day_is_refused():
    with pytest.raises(CalendarParseError, match="not 2026-10-13"):
        parse("empty_day_mon_12_oct.json", date(2026, 10, 13))


def test_the_same_date_in_another_year_is_refused_by_the_weekday():
    with pytest.raises(CalendarParseError):
        parse("empty_day_mon_12_oct.json", date(2027, 10, 12))  # Tuesday, but the page says Mon


def test_an_open_form_or_drawer_makes_the_page_unreadable():
    with pytest.raises(CalendarParseError, match="open"):
        parse("form_open_mon_12_oct.json", MON)


def test_a_loading_page_is_refused():
    nodes = load("empty_day_mon_12_oct.json")
    nodes.insert(0, {"tag": "div", "depth": 3, "cls": [], "attrs": [], "role": None, "testid": "app-loader", "aria": None, "text": None, "box": [0, 0, 1, 1]})
    with pytest.raises(CalendarParseError, match="loading"):
        parse(None, MON, nodes=nodes)


def test_the_single_column_is_never_assumed_to_be_the_configured_staff():
    with pytest.raises(CalendarParseError, match="ARIA_CONFIRM_SINGLE_STAFF"):
        parse("empty_day_mon_12_oct.json", MON, confirmed=False)


def test_more_than_one_column_is_refused():
    nodes = load("empty_day_mon_12_oct.json")
    overlay = next(i for i, n in enumerate(nodes) if any(c.startswith("_calendarGrid--overlay_") for c in n["cls"]))
    column = next(copy.deepcopy(n) for n in nodes[overlay:] if any(c.startswith("_calendarColumn_") for c in n["cls"]))
    nodes.insert(overlay + 1, column)
    with pytest.raises(CalendarParseError, match="2 staff columns"):
        parse(None, MON, nodes=nodes)


def test_an_unrecognised_card_makes_time_off_unknown_never_empty():
    nodes = load("empty_day_mon_12_oct.json")
    for n in nodes:
        if n["testid"] == "non-working-event":
            n["testid"] = "mystery-block"
    # the card wrappers stay; only the inner marker changed, so they are no longer recognisable as non-working
    sd = only_staff_day(parse(None, MON, nodes=nodes))
    assert sd.time_off is None
    result = find_slots(parse(None, MON, nodes=nodes), SERVICE, "Shobs", now=datetime(2026, 10, 8, 12, 0, tzinfo=TZ))
    assert result.slots == () and any("time off unknown" in u for u in result.unknown)


def test_an_appointment_whose_text_disagrees_with_its_position_makes_appointments_unknown():
    nodes = load("busy_day_thu_8_oct.json")
    for n in nodes:
        if "hour_from" in n["cls"]:
            n["text"] = "9:00 AM"  # the card is drawn at 3:15 PM
    sd = only_staff_day(parse(None, BUSY, nodes=nodes))
    assert sd.appointments is None


@pytest.mark.parametrize("breakage", ["no_from", "bad_text", "end_before_start"])
def test_an_unreadable_appointment_card_makes_appointments_unknown(breakage):
    nodes = load("busy_day_thu_8_oct.json")
    for n in nodes:
        if "hour_from" in n["cls"]:
            if breakage == "no_from":
                n["cls"] = ["renamed"]
            elif breakage == "bad_text":
                n["text"] = "sometime"
            else:
                n["text"] = "6:00 PM"
    assert only_staff_day(parse(None, BUSY, nodes=nodes)).appointments is None


def test_unevenly_spaced_hour_labels_are_refused():
    nodes = load("empty_day_mon_12_oct.json")
    labels = [n for n in nodes if any(c.startswith("_hourText_") for c in n["cls"])]
    labels[3]["box"][1] += 30
    with pytest.raises(CalendarParseError, match="evenly spaced"):
        parse(None, MON, nodes=nodes)


def test_a_page_without_the_date_label_cannot_be_verified():
    nodes = [n for n in load("empty_day_mon_12_oct.json") if n["testid"] != "date-switcher-label"]
    with pytest.raises(CalendarParseError, match="date label"):
        parse(None, MON, nodes=nodes)


# ---------------------------------------------------------------- each defence must hold on its own


def drop_subtrees(nodes, predicate):
    out, skip_depth = [], None
    for n in nodes:
        if skip_depth is not None:
            if n["depth"] > skip_depth:
                continue
            skip_depth = None
        if predicate(n):
            skip_depth = n["depth"]
            continue
        out.append(n)
    return out


def test_an_open_drawer_alone_is_enough_to_refuse_the_page():
    nodes = load("form_open_mon_12_oct.json")
    for n in nodes:
        n["cls"] = [c for c in n["cls"] if "_isEdited_" not in c]  # no draft card; only the drawer remains
    with pytest.raises(CalendarParseError, match="drawer or dialog"):
        parse(None, MON, nodes=nodes)


def test_a_card_in_edit_state_alone_is_enough_to_refuse_the_page():
    nodes = load("form_open_mon_12_oct.json")
    for n in nodes:
        if n["testid"] in ("drawer-appointment-body", "discard-modal"):
            n["testid"] = None  # no drawer marker; only the draft card remains
    with pytest.raises(CalendarParseError, match="editing state"):
        parse(None, MON, nodes=nodes)


def test_displayed_hours_limit_working_time_even_when_nothing_is_marked_non_working():
    nodes = drop_subtrees(load("empty_day_mon_12_oct.json"), lambda n: (n["testid"] or "").startswith("calendar-content-calendar-grid-calendar-card-"))
    sd = only_staff_day(parse(None, MON, nodes=nodes))
    assert [(w.start, w.end) for w in sd.working] == [(at(MON, 10), at(MON, 19))]  # not the whole 9:00-20:00 grid


def test_non_working_blocks_alone_define_working_time_when_the_displayed_hours_are_missing():
    from aria_booking.timeparse import hours_range_in_label

    nodes = [n for n in load("empty_day_mon_12_oct.json") if not hours_range_in_label(n["text"] or "")]
    sd = only_staff_day(parse(None, MON, nodes=nodes))
    assert [(w.start, w.end) for w in sd.working] == [(at(MON, 10), at(MON, 19))]


# ---------------------------------------------------------------- live-output shape


def test_normalize_turns_the_live_script_shape_into_the_stored_shape():
    raw = [{"tag": "div", "depth": 3, "cls": ["a"], "attrs": ["data-x"], "role": None, "testid": "t", "aria": None, "text": "hi", "x": 1, "y": 2, "w": 3, "h": 4}]
    assert normalize_nodes(raw) == [{"tag": "div", "depth": 3, "cls": ["a"], "attrs": ["data-x"], "role": None, "testid": "t", "aria": None, "text": "hi", "box": [1, 2, 3, 4]}]


def test_config_defaults_to_not_confirmed():
    assert Config.from_env({}).single_staff_confirmed is False
    assert Config.from_env({"ARIA_CONFIRM_SINGLE_STAFF": "1"}).single_staff_confirmed is True
