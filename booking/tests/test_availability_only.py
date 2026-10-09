"""The availability-only demo: staff-aware availability answers, and NO way to write anything.

Synthetic staff and services only (none exist in any real account). Sol's acceptance example is exercised as a flow, not a hard-coded
staff path: "Is Lily available Monday at 4 for a deep tissue massage?"
"""

from __future__ import annotations

import inspect
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aria_booking import cli
from aria_booking.booking_service import BookingService
from aria_booking.catalog.bookable import BookableRegistry, BookableService
from aria_booking.catalog.render import (
    AVAILABILITY_TOOLS_FILE, GENERATED, apply_mode, build_availability_tools, build_prompt, render_knowledge,
)
from aria_booking.config import Config
from aria_booking.ledger import Ledger
from aria_booking.voice import retell_http
from aria_booking.voice.retell_http import READ_ONLY_ROUTES, ROUTES, RetellEndpoint
from aria_booking.voice.tools import VoiceTools

from fakes import FakeMultiCalendar

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 8, 12, tzinfo=TZ)
DAY = date(2026, 10, 12)  # Monday
DEEP60, DEEP90, FACIAL = "massage-deep-tissue-massage-60", "massage-deep-tissue-massage-90", "facial-signature-facial-60"
DRAFT = Path(__file__).resolve().parent.parent / "voice_agent_draft"
CALL = "call_A"


class Clock:
    def __call__(self):
        return NOW


def registry():
    return BookableRegistry([
        BookableService(DEEP60, "Deep Tissue Massage 60", 60, eligible_staff=frozenset({"lily", "lily park", "maya"}), verified=True, catalog_item=DEEP60),
        BookableService(DEEP90, "Deep Tissue Massage 90", 90, eligible_staff=frozenset({"lily", "lily park", "maya"}), verified=True, catalog_item=DEEP90),
        BookableService(FACIAL, "Signature Facial 60", 60, eligible_staff=frozenset({"maya"}), verified=True, catalog_item=FACIAL),
    ])


def make(tmp_path, roster, *, availability_only=True, boom=False):
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    calendar = FakeMultiCalendar(TZ, roster, business_id="9999999", now=NOW)
    calendar.set_all_hours(DAY)
    built = []

    def factory(service_cfg):
        built.append(service_cfg)
        if boom:
            raise AssertionError("a booking service must not even be built in the availability-only demo")
        return BookingService(service_cfg, calendar, Ledger(cfg.ledger_path, clock=Clock()), clock=Clock(), sleep=lambda s: None)

    tools = VoiceTools(cfg, calendar, factory, Clock(), registry=registry(), require_service_id=True,
                       availability_only=availability_only, booking_enabled=True)
    tools.built = built
    return tools, calendar


def ask(tools, service_id, time, staff=None, day=DAY, call=CALL):
    args = {"service_id": service_id, "date": day.isoformat(), "time": time}
    if staff is not None:
        args["staff"] = staff
    return tools.check_slot(call, args)


# ---------------------------------------------------------------- the acceptance example, as a flow


def test_is_lily_available_monday_at_4_for_a_deep_tissue_massage(tmp_path):
    tools, calendar = make(tmp_path, ["Lily", "Maya"])
    # 1. the caller said "a deep tissue massage": the length is not known, so Aria must ask, not pick
    lookup = tools.lookup_service(CALL, {"query": "deep tissue massage"})
    assert lookup["status"] == "choose_variant" and [i["duration_minutes"] for i in lookup["items"]] == [30, 60, 90]
    assert "Which length would you like?" in lookup["speak"]
    # 2. "an hour": the exact service and the id the calendar tool accepts
    chosen = tools.lookup_service(CALL, {"query": "deep tissue massage 60 minutes"})
    assert chosen["status"] == "service_info" and chosen["bookable_service_id"] == DEEP60 and chosen["item"]["price_usd"] == 85
    # 3. the check: eligibility, the FULL interval, the named technician
    answer = ask(tools, chosen["bookable_service_id"], "16:00", staff="Lily")
    assert answer["status"] == "available" and answer["options"][0]["technician"] == "Lily"
    assert "Lily" in answer["speak"] and "1 hour" in answer["speak"] and "Monday, October 12 at 4 PM" in answer["speak"]
    assert "I can only check availability" in answer["speak"] and "can't book it" in answer["speak"]
    assert answer["ok"] is False, "an open time is never a booking"
    assert calendar.create_calls == [] and tools.built == []


def test_the_same_question_with_lily_busy_gives_lilys_real_alternatives_only(tmp_path):
    tools, calendar = make(tmp_path, ["Lily", "Maya"])
    calendar.add_staff_appointment("Lily", DAY, (15, 30), (16, 30))
    answer = ask(tools, DEEP60, "16:00", staff="Lily")
    assert answer["status"] == "alternatives" and answer["unavailable_reason"] == "occupied"
    assert "Lily is already booked at 4 PM" in answer["speak"]
    assert {o["technician"] for o in answer["options"]} == {"Lily"} and 1 <= len(answer["options"]) <= 3
    assert "Which would you prefer?" not in answer["speak"] and "I can't book it" in answer["speak"]
    for option in answer["options"]:
        start = datetime.fromisoformat(option["start"])
        assert not (start < calendar.at(DAY, 16, 30) and start + timedelta(minutes=60) > calendar.at(DAY, 15, 30))


def test_a_technician_whose_day_ends_before_the_service_would_is_told_in_her_terms(tmp_path):
    tools, calendar = make(tmp_path, ["Lily", "Maya"])
    calendar.staff_hours.clear()
    calendar.set_staff_hours("Lily", DAY, 10, 16)  # Lily works 10:00-16:00; Maya 9:00-20:00
    calendar.set_staff_hours("Maya", DAY, 9, 20)
    answer = ask(tools, DEEP60, "16:00", staff="Lily")
    assert answer["unavailable_reason"] == "outside_hours" and "Lily works from 10 AM to 4 PM" in answer["speak"] and "outside those hours" in answer["speak"]
    late = ask(tools, DEEP60, "15:30", staff="Lily")
    assert late["unavailable_reason"] == "ends_after_closing" and late["latest_start"] == "15:00" and "end of Lily's day at 4 PM" in late["speak"]
    assert ask(tools, DEEP60, "15:00", staff="Lily")["status"] == "available"


def test_a_technician_who_is_not_working_that_day_is_said_by_name(tmp_path):
    tools, calendar = make(tmp_path, ["Lily", "Maya"])
    other = DAY + timedelta(days=1)
    calendar.set_staff_hours("Maya", other, 9, 20)
    answer = ask(tools, DEEP60, "16:00", staff="Lily", day=other)
    assert answer["unavailable_reason"] == "not_working" and "Lily isn't working on Tuesday, October 13" in answer["speak"]


def test_a_technician_who_does_not_do_the_service_is_not_swapped(tmp_path):
    tools, calendar = make(tmp_path, ["Lily", "Maya"])
    answer = ask(tools, FACIAL, "16:00", staff="Lily")
    assert answer["status"] == "staff_unavailable" and answer["reason"] == "staff_not_eligible" and answer["available_staff"] == ["Maya"]


# ---------------------------------------------------------------- any verified staff identity; ambiguous names are asked


def test_first_name_matching_asks_when_two_technicians_share_it(tmp_path):
    tools, calendar = make(tmp_path, ["Lily Chen", "Lily Park", "Maya"])
    answer = ask(tools, DEEP60, "16:00", staff="Lily")
    assert answer["status"] == "needs_clarification" and answer["reason"] == "ambiguous_staff" and answer["candidates"] == ["Lily Chen", "Lily Park"]
    assert "Which did you mean?" in answer["speak"] and "options" not in answer
    assert ask(tools, DEEP60, "16:00", staff="Lily Park")["options"][0]["technician"] == "Lily Park"
    assert calendar.read_calls == 2 and calendar.create_calls == []


def test_first_name_matching_works_when_only_one_technician_has_it(tmp_path):
    tools, _ = make(tmp_path, ["Lily Park", "Maya"])
    assert ask(tools, DEEP60, "16:00", staff="lily")["options"][0]["technician"] == "Lily Park"
    assert ask(tools, DEEP60, "16:00", staff="Park")["options"][0]["technician"] == "Lily Park", "a whole word of the name matches"


@pytest.mark.parametrize("fragment", ["Lil", "ily", "L", "Li Park", "Lily Pa", "Zoe", " "])
def test_a_fragment_or_unknown_name_never_matches_by_guess(tmp_path, fragment):
    tools, _ = make(tmp_path, ["Lily Park", "Maya"])
    answer = ask(tools, DEEP60, "16:00", staff=fragment)
    if fragment.strip():
        assert answer["status"] == "staff_unavailable" and answer["reason"] == "staff_not_on_schedule"
    else:
        assert answer["status"] == "available", "a blank name means no preference"


def test_an_exact_name_beats_a_first_name_that_others_share(tmp_path):
    tools, _ = make(tmp_path, ["Lily", "Lily Park"])
    assert ask(tools, DEEP60, "16:00", staff="Lily")["options"][0]["technician"] == "Lily"


@pytest.mark.parametrize("size", [1, 3, 11, 40])
def test_the_technician_count_is_never_assumed(tmp_path, size):
    names = [f"Staff{i:02d}" for i in range(size)]
    reg = BookableRegistry([BookableService("any-60", "Any 60", 60, verified=True)])
    cfg = Config(business_id="9999999", local_dir=tmp_path)
    calendar = FakeMultiCalendar(TZ, names, business_id="9999999", now=NOW)
    calendar.set_all_hours(DAY)
    tools = VoiceTools(cfg, calendar, lambda c: None, Clock(), registry=reg, require_service_id=True, availability_only=True)
    for name in names:
        assert ask(tools, "any-60", "11:00", staff=name)["options"][0]["technician"] == name
    open_ = ask(tools, "any-60", "11:00")
    assert open_["options"][0]["technician"] == "Staff00"


# ---------------------------------------------------------------- writes are impossible


def test_availability_answers_carry_no_option_id_and_no_invitation_to_book(tmp_path):
    tools, calendar = make(tmp_path, ["Lily", "Maya"])
    calendar.add_staff_appointment("Lily", DAY, (9, 0), (20, 0))
    for answer in (ask(tools, DEEP60, "16:00"), ask(tools, DEEP60, "16:00", staff="Lily"), tools.find_alternatives(CALL, {"service_id": DEEP60, "date": DAY.isoformat()})):
        for option in answer["options"]:
            assert "option_id" not in option
        low = answer["speak"].lower()
        assert "would you like me to book" not in low and "which would you prefer" not in low and "shall i book" not in low
    assert tools._options == {} and tools._pending == {}, "nothing is stored that could later be confirmed"


@pytest.mark.parametrize("args", [
    {}, {"option_id": "opt_forged"}, {"option_id": "opt_x", "confirmed": True}, {"option_id": None, "confirmed": True},
    {"service_id": DEEP60, "staff": "Lily", "date": "2026-10-12", "time": "16:00", "confirmed": True}, {"confirmed": True},
])
def test_asking_to_book_is_refused_whatever_is_sent_and_builds_no_booking_service(tmp_path, args):
    tools, calendar = make(tmp_path, ["Lily", "Maya"], boom=True)
    answer = ask(tools, DEEP60, "16:00", staff="Lily")  # an open time was just described
    reply = tools.book_slot(CALL, dict(args))
    assert reply["status"] == "refused" and reply["reason"] == "availability_only" and reply["ok"] is False
    low = reply["speak"].lower()
    assert "can't book, hold, change or cancel" in low and "contact the salon directly" in low
    assert not any(word in low for word in ("booked", "confirmed", "reserved", "all set", "scheduled")), low
    assert tools.built == [] and calendar.create_calls == [] and answer["ok"] is False


def test_availability_only_wins_even_if_booking_was_requested_in_the_configuration(tmp_path):
    tools, calendar = make(tmp_path, ["Lily"], availability_only=True)
    assert tools.booking_enabled is False and tools.availability_only is True


def test_an_earlier_style_booking_server_is_unchanged_when_the_mode_is_off(tmp_path):
    tools, calendar = make(tmp_path, ["Lily", "Maya"], availability_only=False)
    answer = ask(tools, DEEP60, "16:00", staff="Lily")
    option = answer["options"][0]
    assert "option_id" in option and "Would you like me to book it?" in answer["speak"]


def test_the_read_only_routes_have_no_booking_route_at_all():
    assert "/tools/book_slot" in ROUTES and "/tools/book_slot" not in READ_ONLY_ROUTES
    assert set(READ_ONLY_ROUTES.values()) == {"lookup_service", "check_slot", "find_alternatives"}


def _signed(endpoint_key, body):
    import hashlib
    import hmac

    ts = 1_790_000_000_000
    return {"X-Retell-Signature": f"v={ts},d=" + hmac.new(endpoint_key.encode(), body + str(ts).encode(), hashlib.sha256).hexdigest()}


class Spy:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name in {"lookup_service", "check_slot", "find_alternatives", "book_slot"}:
            return lambda call_id, args: (self.calls.append(name) or {"status": "unknown", "ok": False, "speak": "x"})
        raise AttributeError(name)


def test_a_signed_request_to_the_booking_route_is_a_404_in_read_only_mode_and_never_reaches_the_tools():
    spy = Spy()
    endpoint = RetellEndpoint(spy, "k", routes=READ_ONLY_ROUTES, now_ms=lambda: 1_790_000_000_000)
    body = json.dumps({"name": "book_slot", "call": {"call_id": "c"}, "args": {"option_id": "x", "confirmed": True}}).encode()
    status, payload = endpoint.handle("POST", "/tools/book_slot", _signed("k", body), body)
    assert status == 404 and spy.calls == []
    body2 = json.dumps({"name": "check_slot", "call": {"call_id": "c"}, "args": {}}).encode()
    assert endpoint.handle("POST", "/tools/check_slot", _signed("k", body2), body2)[0] == 200 and spy.calls == ["check_slot"]
    full = RetellEndpoint(Spy(), "k", now_ms=lambda: 1_790_000_000_000)
    assert full.handle("POST", "/tools/book_slot", _signed("k", body), body)[0] == 200, "the booking-capable server still has the route"


ENV = {"ARIA_BOOKSY_BUSINESS_ID": "1234567", "ARIA_LIVE_BOOKSY": "1", "ARIA_RETELL_API_KEY": "test-key-not-real"}
SERVE = ["serve", "--confirm-business-id", "1234567", "--port", "18787"]
FIVE = ["--approve-" + f for f in cli.BOOK_APPROVALS]


class StubServer:
    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        pass


class SpyDriver:
    def __init__(self, cfg):
        pass

    def verify_business(self):
        return "1234567"

    def close(self):
        pass


def _serve(monkeypatch, extra):
    captured = {}

    def fake_make(endpoint, port, host="127.0.0.1"):
        captured["endpoint"] = endpoint
        return StubServer()

    monkeypatch.setattr(retell_http, "make_http_server", fake_make)
    lines = []
    code = cli.main(SERVE + extra, environ=ENV, driver_factory=SpyDriver, clock=lambda: NOW, out=lines.append)
    assert code == cli.EXIT_OK
    return captured["endpoint"], "\n".join(lines)


def test_serve_without_the_five_approvals_is_availability_only_with_no_booking_route(monkeypatch):
    endpoint, text = _serve(monkeypatch, [])
    assert endpoint._tools.availability_only is True and endpoint._tools.booking_enabled is False
    assert endpoint._routes == READ_ONLY_ROUTES and "no booking route" in text


def test_serve_with_all_five_approvals_keeps_the_booking_route(monkeypatch):
    endpoint, _ = _serve(monkeypatch, FIVE)
    assert endpoint._tools.availability_only is False and endpoint._tools.booking_enabled is True and endpoint._routes == ROUTES


# ---------------------------------------------------------------- the generated availability-only prompt and tools


TEMPLATE = (DRAFT / "prompt_template.md").read_text(encoding="utf-8")
AVAIL_PROMPT = (DRAFT / GENERATED["availability"]).read_text(encoding="utf-8")
NORMAL = " ".join(AVAIL_PROMPT.lower().replace("**", "").split())


def test_the_committed_files_are_exactly_what_the_generator_produces():
    reg = BookableRegistry.from_config(Config(business_id="0000000"))
    assert AVAIL_PROMPT == build_prompt(TEMPLATE, registry=reg, mode="availability"), "run: python -m aria_booking.catalog.render --write"
    tools = json.loads((DRAFT / "tools.json").read_text(encoding="utf-8"))
    on_disk = (DRAFT / AVAILABILITY_TOOLS_FILE).read_text(encoding="utf-8")
    assert json.loads(on_disk) == build_availability_tools(tools)


def test_the_availability_only_tool_file_has_no_writer_and_no_option_ids():
    tools = json.loads((DRAFT / AVAILABILITY_TOOLS_FILE).read_text(encoding="utf-8"))
    assert [t["name"] for t in tools["tools"]] == ["lookup_service", "check_slot", "find_alternatives"]
    text = json.dumps(tools).lower()
    assert "book_slot" not in text and "option_id" not in text and "confirmed" not in text
    for tool in tools["tools"][1:]:
        assert "only checks" in tool["description"].lower() and "nothing is booked, held or changed" in tool["description"].lower()
    assert "AVAILABILITY-ONLY" in tools["_status"]


def test_the_availability_prompt_cannot_mention_a_booking_function_or_flow():
    for banned in ("book_slot", "option_id", "confirmed: true", "booked_verified", "already_booked", "confirmation_required", "two steps",
                   "what can be booked online", "would you like to book an appointment", "one booking per call"):
        assert banned not in NORMAL, banned


def test_the_availability_prompt_forbids_booking_holding_and_promises_and_covers_the_example():
    for phrase in ("you can only check availability", "even if the caller says", "book it", "hold that for me", "never say \"booked\"",
                   "contact the salon directly to book", "do not promise follow-up", "ask the length first", "any technician by name",
                   "several technicians have that name", "is lily available monday at 4 for a deep tissue massage",
                   "what you can check availability for right now", "no tool is needed for these questions"):
        assert phrase in NORMAL, phrase
    assert "availability-only version" in NORMAL and "no booking function" in NORMAL


def test_the_availability_prompt_names_every_status_it_must_handle_and_the_lookup_statuses():
    for status in ("available", "alternatives", "no_alternatives", "needs_clarification", "staff_unavailable", "not_bookable", "invalid_service",
                   "unknown", "system_unavailable", "busy", "service_info", "choose_variant", "choose_service", "unknown_service"):
        assert status in AVAIL_PROMPT, status


def test_both_prompts_keep_the_website_knowledge_and_the_original_prices():
    for mode in ("booking", "availability"):
        text = render_knowledge(mode=mode)
        assert "(was $425)" in text and "(was $850)" in text
        assert "Retail products: the website lists none" in text and "NOT calendar availability" in text
    assert "What you can check availability for right now" in render_knowledge(mode="availability")
    assert "What can be booked online right now" in render_knowledge(mode="booking")


def test_the_two_prompts_differ_only_where_the_modes_differ():
    reg = BookableRegistry.from_config(Config(business_id="0000000"))
    booking, availability = build_prompt(TEMPLATE, registry=reg, mode="booking"), build_prompt(TEMPLATE, registry=reg, mode="availability")
    assert booking != availability and len(availability) < len(booking) + 1500
    for shared in ("Two sources, never mixed", "Do not diagnose a skin or medical condition", "Retail products: the website lists none"):
        assert shared in booking and shared in availability


def test_mode_markers_are_validated():
    with pytest.raises(ValueError, match="unbalanced"):
        apply_mode("<!--A-->only open", "booking")
    with pytest.raises(ValueError, match="mode must be one of"):
        render_knowledge(mode="both")
    assert apply_mode("x<!--A-->a<!--/A--><!--B-->b<!--/B-->y", "booking") == "xby"
    assert apply_mode("x<!--A-->a<!--/A--><!--B-->b<!--/B-->y", "availability") == "xay"


def test_the_prompt_stays_free_of_contact_details_and_a_reasonable_size():
    import re

    assert "@" not in AVAIL_PROMPT and not re.search(r"\(\d{3}\)\s*\d{3}-\d{4}", AVAIL_PROMPT)
    assert 8_000 < len(AVAIL_PROMPT) < 30_000


def test_the_availability_path_never_calls_a_writer_in_the_source():
    import ast
    import textwrap

    for name in ("_check_slot", "_find_alternatives", "_alternatives", "_offer", "_issue", "_eligible_staff", "_why_not"):
        fn = ast.parse(textwrap.dedent(inspect.getsource(getattr(VoiceTools, name))))
        called = {n.func.attr for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert not called & {"book", "verify", "create_appointment", "click", "_service_factory"}, (name, called)
