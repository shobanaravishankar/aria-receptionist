"""The multi-staff calendar reader: each column is bound to a staff member by its own stable id, and anything unverified is UNKNOWN.

Pages are built from real captured structure (see multistaff_pages.py). Staff are made up.
"""

from __future__ import annotations

import copy
from datetime import date, datetime

import pytest

from aria_booking.availability import find_slots, validate_slot
from aria_booking.calendar_parser import CalendarParseError, parse_day_multi, parse_hours_text
from aria_booking.models import ServiceSpec
from aria_booking.roster import INPUT_RE, ROSTER_JS, parse_roster

from conftest import TZ
from multistaff_pages import build_page, roster_items, roster_of

DAY = date(2026, 10, 12)
CAPTURED = datetime(2026, 10, 8, 16, 14, tzinfo=TZ)
SERVICE = ServiceSpec("Svc", 60)
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=TZ)


def at(hour, minute=0):
    return datetime(2026, 10, 12, hour, minute, tzinfo=TZ)


def mins(h, m=0):
    return h * 60 + m


STAFF = [
    {"id": "1001", "name": "Lily Chen", "hours": "10AM-8PM", "nonworking": [(mins(9), mins(10))], "appointments": [(mins(14), mins(15, 30), "Deep Tissue")]},
    {"id": "1002", "name": "Maya Ortiz", "hours": "10AM-8PM", "nonworking": [(mins(9), mins(10))]},
    {"id": "1003", "name": "Noor Haddad", "hours": "11AM-6PM", "nonworking": [(mins(9), mins(11)), (mins(18), mins(20))]},
]
ROSTER = [("1001", "Lily Chen"), ("1002", "Maya Ortiz"), ("1003", "Noor Haddad"), ("1004", "Priya Nair", False), ("1005", "Sam Ellis", False)]


def parse(staff=STAFF, roster=ROSTER, **page):
    return parse_day_multi(build_page(staff, **page), day=DAY, tz=TZ, roster=roster_of(roster), captured_at=CAPTURED)


def by_name(snapshot):
    return {sd.staff: sd for sd in snapshot.staff_days}


# ---------------------------------------------------------------- reading a multi-staff day


def test_each_column_becomes_one_staff_day_bound_to_its_id_and_roster_name():
    snap = parse()
    assert [(sd.staff, sd.staff_id) for sd in snap.staff_days] == [("Lily Chen", "1001"), ("Maya Ortiz", "1002"), ("Noor Haddad", "1003")]
    assert snap.day == DAY


def test_working_hours_come_from_each_staffs_own_header_minus_their_own_non_working_blocks():
    days = by_name(parse())
    assert [(w.start, w.end) for w in days["Lily Chen"].working] == [(at(10), at(20))]
    assert [(w.start, w.end) for w in days["Noor Haddad"].working] == [(at(11), at(18))], "a different shift is read from that person's own header"
    assert all(sd.time_off == () for sd in days.values())


def test_appointments_belong_only_to_the_column_they_are_in():
    days = by_name(parse())
    (appt,) = days["Lily Chen"].appointments
    assert (appt.interval.start, appt.interval.end, appt.staff, appt.service) == (at(14), at(15, 30), "Lily Chen", "Deep Tissue")
    assert days["Maya Ortiz"].appointments == () and days["Noor Haddad"].appointments == ()


def test_staff_in_the_roster_but_not_on_the_page_are_simply_absent_not_free():
    snap = parse()
    assert {sd.staff for sd in snap.staff_days} == {"Lily Chen", "Maya Ortiz", "Noor Haddad"}
    assert snap.for_staff("Priya Nair") is None, "present in the filter (11-style roster) but not shown today: no data, so never offered"


@pytest.mark.parametrize("order", [[0, 1, 2], [2, 1, 0], [1, 2, 0]])
def test_attribution_follows_the_staff_id_not_the_position_or_dom_order(order):
    staff = [STAFF[i] for i in order]
    days = by_name(parse(staff))
    assert days["Lily Chen"].staff_id == "1001" and len(days["Lily Chen"].appointments) == 1
    assert days["Maya Ortiz"].staff_id == "1002" and days["Maya Ortiz"].appointments == ()
    assert [(w.start, w.end) for w in days["Noor Haddad"].working] == [(at(11), at(18))]


def test_two_staff_with_the_same_appointment_time_are_each_attributed_to_their_own_column():
    staff = [dict(STAFF[0], appointments=[(mins(14), mins(15, 30), "A")]), dict(STAFF[1], appointments=[(mins(14), mins(15, 30), "B")])]
    days = by_name(parse(staff))
    assert days["Lily Chen"].appointments[0].service == "A" and days["Maya Ortiz"].appointments[0].service == "B"


def test_a_conflict_in_one_column_does_not_block_another_staff_member():
    snap = parse()
    lily = find_slots(snap, SERVICE, "Lily Chen", now=NOW, min_lead_minutes=60)
    maya = find_slots(snap, SERVICE, "Maya Ortiz", now=NOW, min_lead_minutes=60)
    assert at(14) not in [s.service.start for s in lily.slots] and at(14) in [s.service.start for s in maya.slots]
    assert not validate_slot(snap, SERVICE, "Lily Chen", at(14), now=NOW).ok and validate_slot(snap, SERVICE, "Maya Ortiz", at(14), now=NOW).ok


def test_the_layout_grid_is_never_read_as_a_second_staff_set():
    snap = parse(layout_columns=9)
    assert len(snap.staff_days) == 3, "blank layout columns carry no staff id and are not staff"


def test_eleven_staff_and_more_are_read_with_no_special_count():
    staff = [{"id": str(2000 + i), "name": f"Staff {i}", "hours": "10AM-8PM", "nonworking": []} for i in range(25)]
    snap = parse(staff, roster=[(s["id"], s["name"]) for s in staff])
    assert len(snap.staff_days) == 25 and {sd.staff_id for sd in snap.staff_days} == {s["id"] for s in staff}


# ---------------------------------------------------------------- unknown is never free


def test_a_header_without_hours_makes_that_persons_working_hours_unknown_not_available():
    staff = [STAFF[0], dict(STAFF[1], hours=None)]
    days = by_name(parse(staff))
    assert days["Maya Ortiz"].working is None and days["Lily Chen"].working is not None
    snap = parse(staff)
    assert find_slots(snap, SERVICE, "Maya Ortiz", now=NOW).slots == () and find_slots(snap, SERVICE, "Maya Ortiz", now=NOW).unknown
    assert validate_slot(snap, SERVICE, "Maya Ortiz", at(12), now=NOW).unknown


@pytest.mark.parametrize("hours", ["", "off", "closed", "10-8", "8PM-10AM", "10AM-10AM", "13AM-2PM", "all day"])
def test_unreadable_hours_are_unknown_too(hours):
    assert by_name(parse([dict(STAFF[0], hours=hours)]))["Lily Chen"].working is None


def test_an_unrecognised_card_makes_only_that_persons_time_off_unknown():
    days = by_name(parse([dict(STAFF[0], cards_extra=1), STAFF[1]]))
    assert days["Lily Chen"].time_off is None and days["Maya Ortiz"].time_off == ()


def test_an_unreadable_appointment_makes_only_that_persons_appointments_unknown():
    page = build_page([STAFF[0], STAFF[1]])
    for node in page:
        if "hour_from" in node["cls"]:
            node["text"] = "sometime"
    days = by_name(parse_day_multi(page, day=DAY, tz=TZ, roster=roster_of(ROSTER), captured_at=CAPTURED))
    assert days["Lily Chen"].appointments is None and days["Maya Ortiz"].appointments == ()


def test_a_card_whose_text_disagrees_with_its_position_is_unknown_not_trusted():
    page = build_page([STAFF[0]])
    for node in page:
        if "hour_from" in node["cls"]:
            node["text"] = "9:00 AM"
    assert by_name(parse_day_multi(page, day=DAY, tz=TZ, roster=roster_of(ROSTER), captured_at=CAPTURED))["Lily Chen"].appointments is None


# ---------------------------------------------------------------- refusing to attribute


def refuses(match, staff=STAFF, roster=ROSTER, **page):
    with pytest.raises(CalendarParseError, match=match):
        parse(staff, roster, **page)


def test_a_column_whose_staff_id_is_not_in_the_roster_is_refused():
    refuses("not in the roster", [STAFF[0], dict(STAFF[1], id="9999")])


def test_a_column_with_no_staff_id_or_a_non_numeric_one_is_refused():
    refuses("numeric staff id", [dict(STAFF[0], res=None)])
    refuses("numeric staff id", [dict(STAFF[0], res="abc")])
    refuses("numeric staff id", [dict(STAFF[0], res="")])
    refuses("numeric staff id", [dict(STAFF[0], res="12 34")])


def test_two_columns_with_the_same_staff_id_are_refused():
    refuses("appears on two columns", [STAFF[0], dict(STAFF[1], res="1001")])


def test_a_header_whose_name_disagrees_with_the_roster_is_refused():
    refuses("names someone else", [STAFF[0], dict(STAFF[1], header_name="Priya Nair")])


def test_header_name_comparison_ignores_case_and_spacing_but_nothing_else():
    snap = parse([dict(STAFF[0], header_name="  lily   CHEN ")])
    assert snap.staff_days[0].staff == "Lily Chen"
    refuses("names someone else", [dict(STAFF[0], header_name="Lily")])


def test_a_header_count_that_disagrees_with_the_column_count_is_refused():
    refuses("staff headers but", STAFF, header_count=2)
    refuses("staff headers but", STAFF[:2], header_count=3)


def test_no_headers_at_all_leaves_every_working_hour_unknown_but_still_binds_by_id():
    snap = parse(headers=False)
    assert all(sd.working is None for sd in snap.staff_days) and [sd.staff_id for sd in snap.staff_days] == ["1001", "1002", "1003"]


def test_no_columns_at_all_is_refused_not_read_as_an_empty_day():
    page = build_page(STAFF)
    columns = [n for n in page if n.get("res")]
    assert len(columns) == 3, "the page really had staff columns before they were removed"
    removed = set(map(id, columns))
    page = [n for n in page if id(n) not in removed]  # drop only the column nodes: the overlay grid is left with no staff columns
    with pytest.raises(CalendarParseError, match="no staff columns"):
        parse_day_multi(page, day=DAY, tz=TZ, roster=roster_of(ROSTER), captured_at=CAPTURED)


def test_a_truncated_capture_is_refused():
    refuses("cut short", truncated=True)


def test_an_incomplete_or_empty_roster_is_refused_so_nobody_is_guessed():
    incomplete = parse_roster(roster_items(ROSTER) + [{"testid": "filtersValue_1006-input", "label_testid": None, "name": None, "checked": True}])
    assert not incomplete.complete
    with pytest.raises(CalendarParseError, match="roster is incomplete"):
        parse_day_multi(build_page(STAFF), day=DAY, tz=TZ, roster=incomplete, captured_at=CAPTURED)
    with pytest.raises(CalendarParseError, match="roster is incomplete"):
        parse_day_multi(build_page(STAFF), day=DAY, tz=TZ, roster=parse_roster([]), captured_at=CAPTURED)


def test_the_wrong_day_a_loading_page_and_an_open_form_are_still_refused():
    with pytest.raises(CalendarParseError, match="not 2026-10-13"):
        parse_day_multi(build_page(STAFF), day=date(2026, 10, 13), tz=TZ, roster=roster_of(ROSTER), captured_at=CAPTURED)
    page = build_page(STAFF)
    page.insert(0, {"tag": "div", "depth": 3, "cls": [], "attrs": [], "role": None, "testid": "app-loader", "aria": None, "text": None, "box": [0, 0, 1, 1], "res": None})
    with pytest.raises(CalendarParseError, match="loading"):
        parse_day_multi(page, day=DAY, tz=TZ, roster=roster_of(ROSTER), captured_at=CAPTURED)
    page = build_page(STAFF)
    page.append({"tag": "div", "depth": 3, "cls": [], "attrs": [], "role": None, "testid": "drawer-appointment-body", "aria": None, "text": None, "box": [0, 0, 1, 1], "res": None})
    with pytest.raises(CalendarParseError, match="open"):
        parse_day_multi(page, day=DAY, tz=TZ, roster=roster_of(ROSTER), captured_at=CAPTURED)


def test_a_header_over_two_columns_or_two_headers_over_one_column_is_refused():
    page = build_page(STAFF)
    headers = [i for i, n in enumerate(page) if n["testid"] == "resource"]
    page[headers[1]]["box"][0] = page[headers[0]]["box"][0]  # two headers now sit over the first column
    with pytest.raises(CalendarParseError, match="more than one staff header"):
        parse_day_multi(page, day=DAY, tz=TZ, roster=roster_of(ROSTER), captured_at=CAPTURED)


def test_duplicate_display_names_with_different_ids_are_kept_apart_for_the_tools_to_refuse():
    staff = [dict(STAFF[0], id="1001", name="Alex Kim"), dict(STAFF[1], id="1002", name="Alex Kim")]
    snap = parse(staff, roster=[("1001", "Alex Kim"), ("1002", "Alex Kim")])
    assert [sd.staff_id for sd in snap.staff_days] == ["1001", "1002"] and [sd.staff for sd in snap.staff_days] == ["Alex Kim", "Alex Kim"]


# ---------------------------------------------------------------- the roster


def test_the_roster_lists_every_staff_member_with_a_stable_id_and_ignores_select_all():
    roster = roster_of([(str(100 + i), f"Person {i}", i % 2 == 0) for i in range(11)])
    assert roster.complete and len(roster.members) == 11
    assert roster.get("103").name == "Person 3" and roster.get("103").checked is False and roster.get("999") is None
    assert roster.names()[0] == "Person 0"


def test_the_roster_is_unavailable_when_the_page_has_no_filter_panel():
    assert parse_roster(None) is None


@pytest.mark.parametrize("bad, problem", [
    ({"testid": "filtersValue_7-input", "label_testid": None, "name": None, "checked": True}, "no matching label"),
    ({"testid": "filtersValue_7-input", "label_testid": "filtersValue_8", "name": "X", "checked": True}, "no matching label"),
    ({"testid": "filtersValue_7-input", "label_testid": "filtersValue_7", "name": "   ", "checked": True}, "empty name"),
    ({"testid": "filtersValue_7-input", "label_testid": "filtersValue_7", "name": None, "checked": True}, "empty name"),
])
def test_a_filter_entry_that_does_not_fit_makes_the_roster_incomplete(bad, problem):
    roster = parse_roster(roster_items([("1", "Ok")]) + [bad])
    assert not roster.complete and any(problem in p for p in roster.problems)
    assert roster.get("1") is not None, "the good entry is still read; the roster is just not trusted as complete"


def test_a_repeated_staff_id_makes_the_roster_incomplete():
    roster = parse_roster(roster_items([("5", "A")]) + roster_items([("5", "B")], select_all=False))
    assert not roster.complete and any("more than once" in p for p in roster.problems)


def test_names_are_whitespace_normalised_and_only_numeric_ids_count():
    roster = parse_roster([{"testid": "filtersValue_42-input", "label_testid": "filtersValue_42", "name": "  Ana \n  Lee ", "checked": False},
                           {"testid": "filtersValue_abc-input", "label_testid": "filtersValue_abc", "name": "Nope", "checked": True},
                           {"testid": None}, "junk", 5])
    assert roster.names() == ["Ana Lee"] and any("unreadable" in p for p in roster.problems)


def test_the_page_script_reads_the_closed_filter_panel_without_changing_anything():
    assert "resources-filter-by-staffers" in ROSTER_JS and "querySelectorAll('input[type=\"checkbox\"]')" in ROSTER_JS
    for banned in (".click(", ".checked =", "dispatchEvent", ".value =", "setAttribute", "removeAttribute", "innerHTML ="):
        assert banned not in ROSTER_JS, banned
    assert INPUT_RE.match("filtersValue_900001-input") and not INPUT_RE.match("filtersValue_selectAll-input")


# ---------------------------------------------------------------- hours text


@pytest.mark.parametrize("text, expected", [
    ("10AM-8PM", ((10, 0), (20, 0))), ("10:00 AM - 7:00 PM", ((10, 0), (19, 0))), ("9:30AM-8PM", ((9, 30), (20, 0))),
    ("12PM-6PM", ((12, 0), (18, 0))), ("10 am – 8 pm", ((10, 0), (20, 0))), (" 10AM - 8PM ", ((10, 0), (20, 0))),
    ("8PM-10AM", None), ("10AM-10AM", None), ("", None), (None, None), ("10-8", None), ("13AM-2PM", None), ("10AM-8", None),
])
def test_hours_text_is_understood_or_unknown(text, expected):
    assert parse_hours_text(text) == expected
