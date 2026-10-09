"""Service questions to booking: the catalogue, service and technician selection, and every protection that must survive it.

All calendars here are SYNTHETIC (three staff, several services). None of these services or staff exist in the real Booksy
test account; the real adapter is still single-staff and single-service and its guards are tested at the bottom.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from aria_booking.booking_service import BookingService
from aria_booking.catalog.bookable import DEFAULT_SERVICE_ID, BookableRegistry, BookableService, staff_key
from aria_booking.config import Config
from aria_booking.driver import BeforeSaveError
from aria_booking.ledger import Ledger, State, request_key
from aria_booking.models import AppointmentSpec
from aria_booking.safety import build_note
from aria_booking.selenium_driver import SeleniumBooksyDriver
from aria_booking.voice.tools import SUCCESS_STATUSES, VoiceTools

from fakes import FakeMultiCalendar

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 8, 12, tzinfo=TZ)
DAY = date(2026, 10, 12)  # a Monday
SWEDISH, DEEP90, FACIAL, SIGNATURE, ANYONE60 = (
    "massage-swedish-massage-60", "massage-deep-tissue-massage-90", "facial-hydrafacial-treatment-75", "facial-signature-facial-60", "synthetic-any-60",
)
CALL = "call_A"


def registry(default_id=None, *, extra=()):
    return BookableRegistry([
        BookableService(SWEDISH, "Swedish Massage 60", 60, eligible_staff=frozenset({"ana", "ben"}), verified=True, catalog_item=SWEDISH),
        BookableService(DEEP90, "Deep Tissue Massage 90", 90, verified=True, catalog_item=DEEP90),  # any staff
        BookableService(FACIAL, "HydraFacial 75", 75, eligible_staff=frozenset({"cara"}), verified=True, catalog_item=FACIAL),
        BookableService(SIGNATURE, "Signature Facial 60", 60, verified=False, catalog_item=SIGNATURE),  # mapped but NOT verified
        BookableService(ANYONE60, "Any Service 60", 60, verified=True),
        *extra,
    ], default_id=default_id)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def world(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    clock = Clock()
    calendar = FakeMultiCalendar(TZ, ["Ana", "Ben", "Cara"], business_id="9999999", now=NOW)
    calendar.set_all_hours(DAY)
    made = []

    def factory(service_cfg):
        made.append(service_cfg)
        return BookingService(service_cfg, calendar, Ledger(cfg.ledger_path, clock=clock), clock=clock, sleep=lambda s: None)

    tools = VoiceTools(cfg, calendar, factory, clock, registry=registry(), booking_enabled=True, require_service_id=True)
    tools.made = made
    return tools, calendar, cfg, clock


def ask(tools, service_id, time, day=DAY, call=CALL, staff=None, key="check"):
    args = {"service_id": service_id, "date": day.isoformat(), "time": time}
    if staff:
        args["staff"] = staff
    resp = tools.check_slot(call, args)
    assert resp["ok"] is (resp["status"] in SUCCESS_STATUSES)
    return resp


def confirm(tools, option_id, call=CALL):
    first = tools.book_slot(call, {"option_id": option_id, "confirmed": False})
    assert first["status"] == "confirmation_required", first
    return tools.book_slot(call, {"option_id": option_id, "confirmed": True})


# ---------------------------------------------------------------- FAQ only: no calendar, no browser lock


class ExplodingDriver:
    reads = 0

    def read_day(self, day, include_notes=False):
        ExplodingDriver.reads += 1
        raise AssertionError("a service/price question must never reach the calendar")

    def __getattr__(self, name):
        raise AssertionError(f"driver.{name} touched by an FAQ-only path")


def test_faq_only_questions_make_zero_calendar_or_booking_calls(tmp_path):
    ExplodingDriver.reads = 0

    def factory(*a):
        raise AssertionError("the booking service must not even be built for an FAQ")

    tools = VoiceTools(Config(business_id="9999999", local_dir=tmp_path), ExplodingDriver(), factory, Clock(), registry=registry(), require_service_id=True)
    queries = ["swedish massage", "swedish massage 60 minutes", "facial", "hot stone massage", "microblading", "eyelash removal", "membership",
               "how much is a deep tissue massage for 90 minutes", "haircut", "moisturizer", "xyzzy", "lymphatic drainage"]
    for query in queries:
        resp = tools.lookup_service(CALL, {"query": query})
        assert resp["ok"] is False and resp["status"] in {"service_info", "choose_variant", "choose_service", "unknown_service"}, (query, resp)
    assert ExplodingDriver.reads == 0


def test_a_service_question_does_not_wait_behind_a_slow_calendar_read(world):
    tools, calendar, *_ = world
    assert tools._lock.acquire()  # the browser is busy with someone else's read
    try:
        resp = tools.lookup_service(CALL, {"query": "swedish massage 60 minutes"})
    finally:
        tools._lock.release()
    assert resp["status"] == "service_info" and calendar.read_calls == 0


def test_the_lookup_cannot_change_state_or_issue_options(world):
    tools, calendar, *_ = world
    before = (dict(tools._options), dict(tools._pending), dict(tools._context))
    tools.lookup_service(CALL, {"query": "swedish massage 60 minutes"})
    assert (tools._options, tools._pending, tools._context) == before and calendar.read_calls == 0 and calendar.create_calls == []


@pytest.mark.parametrize("args", [{}, {"query": ""}, {"query": "   "}, {"query": None}, {"query": 5}, {"query": ["swedish"]}, {"query": {"a": 1}}])
def test_a_missing_or_malformed_lookup_query_asks_a_question(world, args):
    tools, calendar, *_ = world
    resp = tools.lookup_service(CALL, args)
    assert resp["status"] == "needs_clarification" and calendar.read_calls == 0


def test_the_lookup_path_in_the_source_never_references_the_driver_or_the_factory():
    import ast
    import inspect
    import textwrap

    from aria_booking.voice.tools import VoiceTools as V

    tree = ast.parse(textwrap.dedent(inspect.getsource(V.lookup_service)))
    identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not identifiers & {"driver", "_service_factory", "_lock", "read_day", "_with_lock", "_issue", "_options", "_pending"}, identifiers


# ---------------------------------------------------------------- listed prices and lengths come from the catalogue


@pytest.mark.parametrize("query, minutes, price", [
    ("swedish massage 60 minutes", 60, 85), ("a one hour swedish massage", 60, 85), ("deep tissue massage, 90 minutes", 90, 120),
    ("hydrafacial", 75, 179), ("microblading", 150, 590), ("eyelash removal", 45, 30), ("back polish", 30, 58),
])
def test_price_and_duration_in_the_answer_match_the_catalogue_exactly(world, query, minutes, price):
    tools, *_ = world
    resp = tools.lookup_service(CALL, {"query": query})
    assert resp["status"] == "service_info"
    assert (resp["item"]["duration_minutes"], resp["item"]["price_usd"]) == (minutes, price)
    assert f"${price}" in resp["speak"] and "listed on our website" in resp["speak"]
    assert resp["item"]["source_url"].startswith("https://www.warrenglamourdayspanj.com/") and resp["item"]["retrieved_on"] == "2026-10-08"


def test_an_item_with_no_published_length_or_price_says_so_instead_of_guessing(world):
    tools, *_ = world
    refill = tools.lookup_service(CALL, {"query": "lash-classic-full-set-refill-2w"})
    assert refill["item"]["duration_minutes"] is None and "no listed length" in refill["speak"] and "$55" in refill["speak"]
    member = tools.lookup_service(CALL, {"query": "membership-glamour-5-visit-package-5-visit"})
    assert "$345" in member["speak"] and "no listed length" in member["speak"]


def test_a_website_service_that_is_not_mapped_is_discussed_but_never_bookable(world):
    tools, calendar, *_ = world
    info = tools.lookup_service(CALL, {"query": "hot stone massage 60 minutes"})
    assert info["status"] == "service_info" and info["bookable"] is False and "can't book it online yet" in info["speak"]
    assert "bookable_service_id" not in info
    booked = ask(tools, "massage-hot-stone-massage-60", "11:00")
    assert booked["status"] == "not_bookable" and booked["reason"] == "no_verified_booking_mapping"
    assert calendar.read_calls == 0, "an unmapped service does not even reach the calendar"


def test_a_mapped_service_says_it_can_be_booked_and_gives_the_exact_id_to_use(world):
    tools, *_ = world
    info = tools.lookup_service(CALL, {"query": "swedish massage 60 minutes"})
    assert info["bookable"] is True and info["bookable_service_id"] == SWEDISH and "I can check availability for it" in info["speak"]


def test_a_mapped_but_unverified_service_is_not_bookable(world):
    tools, calendar, *_ = world
    info = tools.lookup_service(CALL, {"query": "signature facial"})
    assert info["bookable"] is False
    assert ask(tools, SIGNATURE, "11:00")["status"] == "not_bookable" and calendar.read_calls == 0


# ---------------------------------------------------------------- ambiguous names


def test_a_generic_word_asks_which_service(world):
    tools, *_ = world
    resp = tools.lookup_service(CALL, {"query": "facial"})
    assert resp["status"] == "choose_service" and len(resp["families"]) >= 2 and "Which one did you mean?" in resp["speak"]


def test_several_lengths_ask_which_length_and_a_missing_length_is_said_not_substituted(world):
    tools, *_ = world
    resp = tools.lookup_service(CALL, {"query": "swedish massage"})
    assert resp["status"] == "choose_variant" and [i["duration_minutes"] for i in resp["items"]] == [30, 60, 90] and "Which length would you like?" in resp["speak"]
    missing = tools.lookup_service(CALL, {"query": "swedish massage 45 minutes"})
    assert missing["status"] == "choose_variant" and "I don't see that length" in missing["speak"]
    two = tools.lookup_service(CALL, {"query": "swedish massage 30 or 60 minutes"})
    assert two["status"] == "choose_variant", "two named lengths means the caller has not chosen"


def test_free_text_is_never_accepted_as_a_service_id_for_the_calendar(world):
    tools, calendar, *_ = world
    for raw in ("swedish massage", "Swedish Massage 60", "swedish-massage", 5, ["x"], {"a": 1}, "'; drop", "massage-swedish-massage-61"):
        resp = ask(tools, raw, "11:00")
        assert resp["status"] in {"invalid_service", "not_bookable"} and not resp["ok"], raw
    candidates = ask(tools, "swedish massage", "11:00")["candidates"]
    assert candidates and all({"service_id", "name", "bookable"} <= set(c) for c in candidates)
    assert calendar.read_calls == 0 and calendar.create_calls == []


# ---------------------------------------------------------------- the chosen service drives length and availability


def test_a_longer_service_sees_a_conflict_a_shorter_one_does_not(world):
    tools, calendar, *_ = world
    for who in ("Ana", "Ben", "Cara"):
        calendar.add_staff_appointment(who, DAY, (12, 0), (13, 0))
    short = ask(tools, SWEDISH, "11:00")  # 11:00-12:00 touches the 12:00 appointments
    assert short["status"] == "available" and short["options"][0]["duration_minutes"] == 60
    long = ask(tools, DEEP90, "11:00")  # 11:00-12:30 overlaps them
    assert long["status"] == "alternatives" and long["unavailable_reason"] == "occupied"
    assert all(o["duration_minutes"] == 90 and o["service_id"] == DEEP90 for o in long["options"])


def test_switching_service_retires_the_old_options(world):
    tools, calendar, *_ = world
    old = ask(tools, SWEDISH, "11:00")["options"][0]["option_id"]
    ask(tools, DEEP90, "13:00")  # the caller changed their mind
    stale = tools.book_slot(CALL, {"option_id": old, "confirmed": False})
    assert stale["status"] == "invalid_option" and calendar.create_calls == []


def test_switching_technician_retires_the_old_options_but_repeating_the_same_choice_keeps_them(world):
    tools, *_ = world
    first = ask(tools, SWEDISH, "11:00", staff="Ana")["options"][0]["option_id"]
    ask(tools, SWEDISH, "12:00", staff="Ana")
    assert tools.book_slot(CALL, {"option_id": first, "confirmed": False})["status"] == "confirmation_required"
    ask(tools, SWEDISH, "12:00", staff="Ben")
    assert tools.book_slot(CALL, {"option_id": first, "confirmed": False})["status"] == "invalid_option"


def test_a_pending_confirmation_does_not_survive_a_change_of_choice(world):
    tools, calendar, *_ = world
    option = ask(tools, SWEDISH, "11:00")["options"][0]["option_id"]
    tools.book_slot(CALL, {"option_id": option, "confirmed": False})
    ask(tools, FACIAL, "11:00")
    assert CALL not in tools._pending and tools.book_slot(CALL, {"option_id": option, "confirmed": True})["status"] == "invalid_option"
    assert calendar.create_calls == []


def test_a_longer_service_that_would_cross_closing_is_refused_with_its_own_latest_start(world):
    tools, *_ = world
    deep = ask(tools, DEEP90, "18:45")  # 18:45 + 1h30 = 20:15 > 20:00
    assert deep["unavailable_reason"] == "ends_after_closing" and deep["latest_start"] == "18:30" and "1 hour 30 minutes" in deep["speak"]
    short = ask(tools, SWEDISH, "18:45")  # ends 19:45
    assert short["status"] == "available"
    assert ask(tools, DEEP90, "18:30")["status"] == "available" and ask(tools, SWEDISH, "19:00")["status"] == "available"
    assert ask(tools, SWEDISH, "19:15")["unavailable_reason"] == "ends_after_closing"


# ---------------------------------------------------------------- technicians


def test_every_option_names_the_service_length_and_technician(world):
    tools, *_ = world
    resp = ask(tools, SWEDISH, "11:00")
    option = resp["options"][0]
    assert {"option_id", "label", "start", "service_id", "service", "duration_minutes", "price_usd", "technician"} <= set(option)
    assert (option["service"], option["duration_minutes"], option["technician"]) == ("Swedish Massage 60", 60, "Ana")
    assert option["price_usd"] == 85, "the listed website price of the mapped item"
    assert "with Ana" in option["label"] and "with Ana" in resp["speak"]


def test_who_will_do_it_is_answerable_from_tool_metadata_for_each_option(world):
    tools, calendar, *_ = world
    calendar.add_staff_appointment("Ana", DAY, (11, 0), (12, 0))
    resp = ask(tools, SWEDISH, "11:00")
    assert resp["status"] == "available" and resp["options"][0]["technician"] == "Ben", "Ana is busy, so Ben is offered and named"


def test_only_eligible_staff_are_considered(world):
    tools, *_ = world
    assert ask(tools, FACIAL, "11:00")["options"][0]["technician"] == "Cara"
    assert {o["technician"] for o in ask(tools, SWEDISH, "11:00")["options"]} <= {"Ana", "Ben"}


def test_a_named_technician_who_cannot_do_the_service_is_said_not_swapped(world):
    tools, calendar, *_ = world
    resp = ask(tools, FACIAL, "11:00", staff="Ana")
    assert resp["status"] == "staff_unavailable" and resp["reason"] == "staff_not_eligible" and resp["available_staff"] == ["Cara"]
    assert "Ana doesn't do HydraFacial 75" in resp["speak"]
    assert calendar.create_calls == []


def test_a_named_technician_who_is_not_on_the_schedule_is_said(world):
    tools, *_ = world
    resp = ask(tools, SWEDISH, "11:00", staff="Zoe")
    assert resp["status"] == "staff_unavailable" and resp["reason"] == "staff_not_on_schedule" and "Zoe" in resp["speak"]


def test_an_unavailable_named_technician_gets_her_own_alternatives_only(world):
    tools, calendar, *_ = world
    calendar.add_staff_appointment("Ana", DAY, (11, 0), (12, 0))
    resp = ask(tools, SWEDISH, "11:00", staff="ana")  # case-insensitive
    assert resp["status"] == "alternatives" and resp["unavailable_reason"] == "occupied"
    assert "Ana is already booked at 11 AM" in resp["speak"]
    assert {o["technician"] for o in resp["options"]} == {"Ana"}, "Ben being free is not offered unless the caller asks"
    for option in resp["options"]:
        start = datetime.fromisoformat(option["start"])
        assert not (start < calendar.at(DAY, 12) and start + timedelta(minutes=60) > calendar.at(DAY, 11)), "never overlaps Ana's appointment"


def test_a_named_technician_with_no_openings_offers_to_check_others(world):
    tools, calendar, *_ = world
    for d in range(4):
        day = DAY + timedelta(days=d)
        calendar.set_all_hours(day)
        calendar.add_staff_appointment("Ana", day, (9, 0), (20, 0))
    resp = ask(tools, SWEDISH, "11:00", staff="Ana")
    assert resp["status"] == "no_alternatives" and "for Ana" in resp["speak"] and "other technicians" in resp["speak"]


def test_find_alternatives_respects_service_and_technician(world):
    tools, *_ = world
    resp = tools.find_alternatives(CALL, {"service_id": FACIAL, "date": DAY.isoformat(), "time": "14:00", "staff": "Cara"})
    assert resp["status"] == "alternatives" and {o["technician"] for o in resp["options"]} == {"Cara"} and {o["duration_minutes"] for o in resp["options"]} == {75}
    named = tools.find_alternatives(CALL, {"service_id": FACIAL, "date": DAY.isoformat(), "staff": "Ben"})
    assert named["status"] == "staff_unavailable"


@pytest.mark.parametrize("size", [1, 2, 5, 10, 25])
def test_any_roster_size_works_without_a_hard_coded_count(tmp_path, size):
    names = [f"Tech{i}" for i in range(size)]
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    clock = Clock()
    calendar = FakeMultiCalendar(TZ, names, business_id="9999999", now=NOW)
    calendar.set_all_hours(DAY)
    tools = VoiceTools(cfg, calendar, lambda c: BookingService(c, calendar, Ledger(cfg.ledger_path, clock=clock), clock=clock, sleep=lambda s: None),
                       clock, registry=registry(), booking_enabled=True, require_service_id=True)
    first = ask(tools, ANYONE60, "11:00")
    assert first["status"] == "available" and first["options"][0]["technician"] == "Tech0"
    last = ask(tools, ANYONE60, "11:00", staff=names[-1])
    assert last["options"][0]["technician"] == names[-1]


def test_an_empty_roster_is_said_not_assumed(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    calendar = FakeMultiCalendar(TZ, [], business_id="9999999", now=NOW)
    tools = VoiceTools(cfg, calendar, lambda c: None, Clock(), registry=registry(), require_service_id=True)
    resp = ask(tools, ANYONE60, "11:00")
    assert resp["status"] == "staff_unavailable" and resp["reason"] == "no_eligible_staff"


def test_two_columns_with_the_same_display_name_are_not_guessed(world):
    tools, calendar, *_ = world
    calendar.roster = ["Ana", "Ben", "ana"]
    resp = ask(tools, SWEDISH, "11:00")
    assert resp["status"] == "unknown" and resp["reason"] == "ambiguous_roster" and calendar.create_calls == []


def test_unknown_data_for_one_technician_is_never_treated_as_free(world):
    tools, calendar, *_ = world
    calendar.unknown_appointments = True
    resp = ask(tools, SWEDISH, "11:00")
    assert resp["status"] == "unknown" and "options" not in resp


# ---------------------------------------------------------------- an option IS its service and technician


def test_an_option_cannot_be_redirected_to_another_service_or_technician(world):
    tools, calendar, *_ = world
    option = ask(tools, SWEDISH, "11:00")["options"][0]
    for extra in ({"service_id": FACIAL}, {"service_id": DEEP90}, {"staff": "Cara"}, {"staff": "Ben"}, {"service_id": 5}, {"staff": ["Ana"]}):
        resp = tools.book_slot(CALL, {"option_id": option["option_id"], "confirmed": True, **extra})
        assert resp["status"] == "invalid_option" and resp["reason"] == "option_mismatch", extra
    assert calendar.create_calls == [] and CALL not in tools._pending
    matching = tools.book_slot(CALL, {"option_id": option["option_id"], "service_id": SWEDISH, "staff": "ANA", "confirmed": False})
    assert matching["status"] == "confirmation_required", "repeating the SAME values is fine"


def test_a_forged_or_borrowed_option_cannot_book(world):
    tools, calendar, *_ = world
    mine = ask(tools, SWEDISH, "11:00", call="caller_A")["options"][0]["option_id"]
    for forged in ("opt_forged", mine + "x", mine.upper(), "", "null"):
        assert tools.book_slot("caller_A", {"option_id": forged, "confirmed": True})["status"] == "invalid_option"
    assert tools.book_slot("caller_B", {"option_id": mine, "confirmed": True})["status"] == "invalid_option"
    assert calendar.create_calls == []


def test_the_read_back_names_service_length_technician_day_and_time_before_any_save(world):
    tools, calendar, *_ = world
    option = ask(tools, SWEDISH, "11:00")["options"][0]["option_id"]
    resp = tools.book_slot(CALL, {"option_id": option, "confirmed": False})
    assert resp["status"] == "confirmation_required" and calendar.create_calls == []
    for part in ("Swedish Massage 60", "1 hour", "with Ana", "Monday, October 12 at 11 AM", "$85", "Shall I book it?"):
        assert part in resp["speak"], part
    assert (resp["service_id"], resp["technician"], resp["duration_minutes"], resp["price_usd"]) == (SWEDISH, "Ana", 60, 85)


# ---------------------------------------------------------------- booking a mapped service for a technician


def test_a_booking_uses_the_services_own_name_length_and_the_technician_and_is_verified(world):
    tools, calendar, cfg, clock = world
    option = ask(tools, SWEDISH, "11:00", staff="Ben")["options"][0]["option_id"]
    done = confirm(tools, option)
    assert done["status"] == "booked_verified" and done["ok"]
    for part in ("Swedish Massage 60", "1 hour", "with Ben", "Monday, October 12 at 11 AM"):
        assert part in done["speak"], part
    (saved,) = calendar.saved_by_aria()
    assert (saved.staff, saved.service, saved.interval.minutes) == ("Ben", "Swedish Massage 60", 60)
    entry = Ledger(cfg.ledger_path).all_entries()[0]
    assert (entry.staff, entry.service, entry.state, entry.observed) == ("Ben", "Swedish Massage 60", State.VERIFIED, True)
    assert tools.made[-1].service_name == "Swedish Massage 60" and tools.made[-1].service_duration_minutes == 60 and tools.made[-1].staff_name == "Ben"
    assert cfg.service_name == "Aria Salon", "the base configuration is untouched"


def test_the_slot_is_rechecked_just_before_saving_for_that_technician(world):
    tools, calendar, *_ = world
    option = ask(tools, SWEDISH, "11:00", staff="Ben")["options"][0]["option_id"]
    tools.book_slot(CALL, {"option_id": option, "confirmed": False})
    calendar.add_staff_appointment("Ben", DAY, (10, 30), (11, 30), note="booked by someone else meanwhile")
    resp = tools.book_slot(CALL, {"option_id": option, "confirmed": True})
    assert resp["status"] == "unavailable" and calendar.create_calls == []


def test_two_callers_may_book_different_technicians_at_the_same_time_but_not_the_same_one(world):
    tools, calendar, *_ = world
    a = ask(tools, SWEDISH, "11:00", staff="Ana", call="caller_A")["options"][0]["option_id"]
    b = ask(tools, SWEDISH, "11:00", staff="Ben", call="caller_B")["options"][0]["option_id"]
    c = ask(tools, SWEDISH, "11:00", staff="Ana", call="caller_C")["options"][0]["option_id"]
    assert confirm(tools, a, "caller_A")["status"] == "booked_verified"
    assert confirm(tools, b, "caller_B")["status"] == "booked_verified"
    taken = confirm(tools, c, "caller_C")
    assert taken["status"] == "unavailable" and "already booked for you" not in taken["speak"].lower()
    assert len(calendar.create_calls) == 2 and {x.staff for x in calendar.saved_by_aria()} == {"Ana", "Ben"}


def test_the_same_slot_for_two_different_services_has_two_different_references(world):
    tools, calendar, cfg, clock = world
    start = datetime(2026, 10, 12, 11, 0, tzinfo=TZ)
    k1 = request_key("9999999", "Ana", "Swedish Massage 60", start, 60)
    k2 = request_key("9999999", "Ana", "Deep Tissue Massage 90", start, 90)
    k3 = request_key("9999999", "Ben", "Swedish Massage 60", start, 60)
    assert len({k1, k2, k3}) == 3


def test_a_retry_after_success_reverifies_with_the_services_own_configuration(world):
    tools, calendar, *_ = world
    option = ask(tools, DEEP90, "11:00")["options"][0]["option_id"]
    first = confirm(tools, option)
    assert first["ok"] and tools.book_slot(CALL, {"option_id": option, "confirmed": True}) == first
    saved = calendar.appointments[0]
    calendar.appointments.clear()
    again = tools.book_slot(CALL, {"option_id": option, "confirmed": True})
    assert not again["ok"] and again["reason"] == "reverification_failed" and len(calendar.create_calls) == 1 and saved.service == "Deep Tissue Massage 90"


def test_a_factory_that_cannot_serve_the_service_refuses_before_any_attempt(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    clock = Clock()
    calendar = FakeMultiCalendar(TZ, ["Ana"], business_id="9999999", now=NOW)
    calendar.set_all_hours(DAY)
    built = []
    tools = VoiceTools(cfg, calendar, lambda: built.append(1), clock, registry=registry(), booking_enabled=True, require_service_id=True)
    option = ask(tools, SWEDISH, "11:00")["options"][0]["option_id"]
    tools.book_slot(CALL, {"option_id": option, "confirmed": False})
    resp = tools.book_slot(CALL, {"option_id": option, "confirmed": True})
    assert resp["status"] == "refused" and resp["reason"] == "no_booking_path" and built == [] and calendar.create_calls == []


# ---------------------------------------------------------------- the default (single-service) path is unchanged


def test_without_require_service_id_the_old_single_service_path_still_defaults(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    clock = Clock()
    calendar = FakeMultiCalendar(TZ, ["Shobs"], business_id="9999999", now=NOW)
    calendar.set_all_hours(DAY)
    tools = VoiceTools(cfg, calendar, lambda: BookingService(cfg, calendar, Ledger(cfg.ledger_path, clock=clock), clock=clock, sleep=lambda s: None), clock, booking_enabled=True)
    resp = tools.check_slot(CALL, {"date": DAY.isoformat(), "time": "11:00"})
    assert resp["status"] == "available" and resp["options"][0]["service_id"] == DEFAULT_SERVICE_ID
    assert (resp["options"][0]["service"], resp["options"][0]["duration_minutes"], resp["options"][0]["technician"]) == ("Aria Salon", 150, "Shobs")
    assert confirm(tools, resp["options"][0]["option_id"])["status"] == "booked_verified"


def test_requiring_a_service_id_means_the_model_can_never_fall_back_to_the_default(tmp_path):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    calendar = FakeMultiCalendar(TZ, ["Shobs"], business_id="9999999", now=NOW)
    tools = VoiceTools(cfg, calendar, lambda c: None, Clock(), require_service_id=True)
    resp = tools.check_slot(CALL, {"date": DAY.isoformat(), "time": "11:00"})
    assert resp["status"] == "needs_clarification" and resp["reason"] == "service_required" and calendar.read_calls == 0


# ---------------------------------------------------------------- the live adapter stays fail-closed


def _driver(cfg):
    def no_browser(_cfg):
        raise AssertionError("the browser must not even start for a refused spec")

    return SeleniumBooksyDriver(cfg, webdriver_factory=no_browser, notify=lambda m: None, sleep=lambda s: None, monotonic=lambda: 0.0)


def _spec(staff, service, minutes):
    return AppointmentSpec(staff, service, datetime(2026, 10, 12, 11, 0, tzinfo=TZ), minutes, build_note("ARIA-0123ABCD"))


def test_the_live_adapter_refuses_another_staff_member_or_service_before_touching_the_browser():
    drv = _driver(Config(business_id="1234567"))
    with pytest.raises(BeforeSaveError, match="verified test service"):
        drv.create_appointment(_spec("Shobs", "Swedish Massage 60", 60))
    with pytest.raises(BeforeSaveError, match="single verified staff member"):
        drv.create_appointment(_spec("Ben", "Aria Salon", 150))


def test_the_live_adapter_services_can_only_be_widened_explicitly():
    cfg = Config(business_id="1234567")
    assert _driver(cfg).allowed_services == frozenset({"Aria Salon"})
    wide = SeleniumBooksyDriver(cfg, webdriver_factory=lambda c: None, allowed_services=frozenset({"Aria Salon", "Swedish Massage 60"}))
    assert "Swedish Massage 60" in wide.allowed_services


def test_the_real_registry_offers_only_the_verified_test_service():
    real = BookableRegistry.from_config(Config(business_id="1234567"))
    assert real.ids() == [DEFAULT_SERVICE_ID] and staff_key("  Shobs ") in real.get(DEFAULT_SERVICE_ID).eligible_staff


# ---------------------------------------------------------------- gaps found by mutation checks


def test_when_nobody_can_take_it_the_reason_closest_to_bookable_is_given(world):
    tools, calendar, *_ = world
    other_day = DAY + timedelta(days=1)
    calendar.set_staff_hours("Ben", other_day, 9, 20)  # Ana and Cara do not work that day; Ben does but is busy at 11
    calendar.add_staff_appointment("Ben", other_day, (11, 0), (12, 0))
    resp = ask(tools, SWEDISH, "11:00", day=other_day)
    assert resp["unavailable_reason"] == "occupied", "a technician who works but is busy beats 'not working'"
    assert "already taken" in resp["speak"]


def test_a_start_shared_by_several_free_technicians_is_assigned_to_the_first_eligible_one(world):
    tools, *_ = world
    resp = tools.find_alternatives(CALL, {"service_id": SWEDISH, "date": DAY.isoformat(), "time": "11:00"})
    assert resp["status"] == "alternatives" and {o["technician"] for o in resp["options"]} == {"Ana"}, "Ana and Ben are both free; Ana is first"
    ana_busy = world[1]
    ana_busy.add_staff_appointment("Ana", DAY, (9, 0), (20, 0))
    resp2 = tools.find_alternatives("caller_B", {"service_id": SWEDISH, "date": DAY.isoformat(), "time": "11:00"})
    assert {o["technician"] for o in resp2["options"]} == {"Ben"}, "with Ana busy the same starts go to Ben"
