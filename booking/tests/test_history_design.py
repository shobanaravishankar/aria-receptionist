"""Client-history lookup: the OFFLINE design, tested with fictional records only. Nothing real, nothing live, nothing exposed.

Policy under test (supervised demo, set by the user): the COMPLETE phone number locates exactly one client record; that is record matching,
not proof of identity. Latest COMPLETED visit in the asked category only. Never guess; never disclose another client's details.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aria_booking.history.classify import Classifier
from aria_booking.history.lookup import MAX_ATTEMPTS_PER_CALL, HistoryLookup
from aria_booking.history.models import (
    CANCELLED, COMPLETED, NO_SHOW, UNKNOWN, UPCOMING, ClientHistory, ClientRef, HistoryUnavailable, HistoryUnreadable, Visit,
)
from aria_booking.history.phone import normalize_phone
from aria_booking.voice.retell_http import READ_ONLY_ROUTES, ROUTES

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 9, 12, tzinfo=TZ)
CALL = "call_H"
BOOKING = Path(__file__).resolve().parent.parent
PHONE = "(555) 234-5678"  # fictional


def at(days_ago: float, hour: int = 11) -> datetime:
    return (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=0)


def visit(days_ago, service, status=COMPLETED, tech=None, start="auto"):
    return Visit(at(days_ago) if start == "auto" else start, (service,) if isinstance(service, str) else tuple(service), status, tech)


class FakeSource:
    """Synthetic client records keyed by normalised phone. Records which clients' histories were read."""

    def __init__(self):
        self.clients: dict[str, list[tuple[ClientRef, ClientHistory | Exception]]] = {}
        self.read: list[str] = []
        self.find_error = None

    def add(self, phone, history, *, name=None, ref=None):
        number = normalize_phone(phone)
        ref = ref or f"ref-{len(self.read)}-{sum(len(v) for v in self.clients.values())}"
        self.clients.setdefault(number, []).append((ClientRef(ref, name), history))
        return ref

    def find_clients(self, normalized_phone):
        if self.find_error:
            raise self.find_error
        return [c for c, _h in self.clients.get(normalized_phone, [])]

    def read_history(self, client):
        self.read.append(client.ref)
        for entries in self.clients.values():
            for c, h in entries:
                if c.ref == client.ref:
                    if isinstance(h, Exception):
                        raise h
                    return h
        raise HistoryUnreadable("gone")


def lookup(source=None, **kw):
    source = source or FakeSource()
    return HistoryLookup(source, lambda: NOW, **kw), source


def ask(tool, category="lashes", phone=PHONE, **extra):
    args = {"service_category": category, "phone": phone, **extra}
    return tool.last_completed_visit(CALL, args)


def history(*visits, **kw):
    return ClientHistory(tuple(visits), **kw)


# ---------------------------------------------------------------- phone numbers: complete or nothing, never a guessed digit


@pytest.mark.parametrize("text,expected", [
    ("(555) 234-5678", "+15552345678"), ("555-234-5678", "+15552345678"), ("555.234.5678", "+15552345678"), ("5552345678", "+15552345678"),
    (" 1 555 234 5678 ", "+15552345678"), ("+1 (555) 234-5678", "+15552345678"), ("+44 20 7946 0958", "+442079460958"),
])
def test_complete_numbers_normalise_to_one_form(text, expected):
    assert normalize_phone(text) == expected


@pytest.mark.parametrize("text", [
    "234-5678", "555 234 567", "555 234 56789", "5552345", "", "   ", None, 5552345678, "five five five", "555-234-5678 x12", "555-234-5678 ext 4",
    "055-234-5678", "155-234-5678", "555-034-5678", "555-134-5678", "+1", "+123", "+0 555 234 5678", "555+234+5678", "(555) 234-5678; DROP", "1" * 41,
])
def test_incomplete_or_unclear_numbers_are_not_completed_by_guessing(text):
    assert normalize_phone(text) is None


# ---------------------------------------------------------------- the answer: latest COMPLETED visit in the asked category


def test_the_latest_completed_matching_visit_is_reported_with_its_date_and_service():
    tool, source = lookup()
    source.add(PHONE, history(visit(90, "Classic Full Set", tech="Ana"), visit(30, "Classic Full Set 2-week refill"), visit(10, "Swedish Massage")))
    reply = ask(tool)
    assert reply["status"] == "found" and reply["ok"] is True
    assert reply["date"] == at(30).date().isoformat() and reply["service"] == "Classic Full Set 2-week refill"
    assert "technician" not in reply and "Ana" not in reply["speak"], "technician only when asked"


def test_the_technician_is_named_only_when_the_caller_asked_for_it():
    tool, source = lookup()
    source.add(PHONE, history(visit(30, "Classic Full Set", tech="Ana")))
    assert "with Ana" in ask(tool, include_technician=True)["speak"]
    assert "Ana" not in ask(tool)["speak"]
    assert ask(tool, include_technician="yes")["speak"].count("Ana") == 0, "only a real true counts"


@pytest.mark.parametrize("newer_status", [CANCELLED, NO_SHOW, UPCOMING])
def test_a_newer_visit_that_did_not_happen_is_ignored(newer_status):
    tool, source = lookup()
    source.add(PHONE, history(visit(60, "Classic Full Set"), visit(5, "Classic Full Set", status=newer_status)))
    assert ask(tool)["date"] == at(60).date().isoformat()


def test_a_past_date_alone_does_not_prove_a_visit_happened():
    tool, source = lookup()
    source.add(PHONE, history(visit(60, "Classic Full Set"), visit(5, "Classic Full Set", status=UNKNOWN)))
    reply = ask(tool)
    assert reply["status"] == "unable_to_confirm" and reply["reason"] == "status_uncertain" and "date" not in reply


def test_an_unrecognised_status_word_is_treated_as_unknown():
    tool, source = lookup()
    source.add(PHONE, history(visit(60, "Classic Full Set"), visit(5, "Classic Full Set", status="paid-ish")))
    assert ask(tool)["reason"] == "status_uncertain"


def test_a_completed_visit_dated_in_the_future_is_inconsistent_not_a_visit():
    tool, source = lookup()
    source.add(PHONE, history(visit(60, "Classic Full Set"), visit(-3, "Classic Full Set", status=COMPLETED)))
    assert ask(tool)["reason"] == "status_uncertain"


def test_an_unreadable_date_on_a_relevant_visit_blocks_the_answer():
    tool, source = lookup()
    source.add(PHONE, history(visit(60, "Classic Full Set"), visit(0, "Classic Full Set", start=None)))
    assert ask(tool)["reason"] == "status_uncertain"


def test_an_unknown_visit_older_than_the_answer_does_not_block_it():
    tool, source = lookup()
    source.add(PHONE, history(visit(90, "Classic Full Set", status=UNKNOWN), visit(30, "Classic Full Set")))
    assert ask(tool)["status"] == "found"


def test_other_service_categories_are_not_counted():
    tool, source = lookup()
    source.add(PHONE, history(visit(60, "Classic Full Set"), visit(5, "Swedish Massage"), visit(3, "Express Facial", status=UNKNOWN)))
    assert ask(tool)["date"] == at(60).date().isoformat()


def test_a_newer_visit_whose_service_cannot_be_placed_blocks_the_answer():
    tool, source = lookup()
    source.add(PHONE, history(visit(60, "Classic Full Set"), visit(5, "Special Treatment XYZ")))
    reply = ask(tool)
    assert reply["status"] == "unable_to_confirm" and reply["reason"] == "status_uncertain"


def test_a_visit_with_no_services_listed_is_uncertain_when_newer():
    tool, source = lookup()
    source.add(PHONE, history(visit(60, "Classic Full Set"), Visit(at(5), (), COMPLETED)))
    assert ask(tool)["reason"] == "status_uncertain"


def test_a_multi_service_visit_counts_through_its_matching_service():
    tool, source = lookup()
    source.add(PHONE, history(visit(60, "Classic Full Set"), visit(5, ["Eyebrow Waxing", "Classic Full Set"])))
    reply = ask(tool)
    assert reply["date"] == at(5).date().isoformat() and reply["service"] == "Classic Full Set"


def test_no_completed_visit_in_a_fully_read_history_is_said_plainly():
    tool, source = lookup()
    source.add(PHONE, history(visit(5, "Swedish Massage"), visit(9, "Classic Full Set", status=CANCELLED)))
    reply = ask(tool)
    assert reply["status"] == "no_completed_visit" and "lash" in reply["speak"] and reply["ok"] is False


def test_an_empty_history_is_not_found_as_a_visit():
    tool, source = lookup()
    source.add(PHONE, history())
    assert ask(tool)["status"] == "no_completed_visit"


def test_the_order_the_visits_arrive_in_does_not_matter():
    tool, source = lookup()
    source.add(PHONE, history(visit(5, "Classic Full Set"), visit(90, "Classic Full Set"), visit(30, "Classic Full Set")))
    assert ask(tool)["date"] == at(5).date().isoformat()


# ---------------------------------------------------------------- limits of what the source could read


def test_when_completion_state_is_not_available_the_limitation_is_reported():
    tool, source = lookup()
    source.add(PHONE, history(visit(30, "Classic Full Set"), completion_known=False))
    reply = ask(tool)
    assert reply["status"] == "unable_to_confirm" and reply["reason"] == "completion_unavailable" and "not whether they were completed" in reply["speak"]


def test_an_incomplete_read_cannot_claim_a_latest_visit_unless_the_order_was_verified():
    tool, source = lookup()
    source.add(PHONE, history(visit(30, "Classic Full Set"), complete=False))
    assert ask(tool)["reason"] == "history_incomplete"
    source2_tool, source2 = lookup()
    source2.add(PHONE, history(visit(30, "Classic Full Set"), complete=False, newest_first=True))
    assert ask(source2_tool)["status"] == "found"


def test_an_incomplete_read_with_nothing_found_never_says_there_was_no_visit():
    tool, source = lookup()
    source.add(PHONE, history(visit(5, "Swedish Massage"), complete=False, newest_first=True))
    assert ask(tool)["reason"] == "history_incomplete"


def test_unreadable_history_and_an_unreachable_source_are_unable_to_confirm_without_leaking_the_error():
    for error, reason in ((HistoryUnreadable("SECRET-DETAIL"), "history_unreadable"), (HistoryUnavailable("SECRET-DETAIL"), "source_unavailable"),
                          (RuntimeError("SECRET-DETAIL"), "history_unreadable")):
        tool, source = lookup()
        source.add(PHONE, error)
        reply = ask(tool)
        assert reply["status"] == "unable_to_confirm" and reply["reason"] == reason and "SECRET" not in json.dumps(reply)
    tool, source = lookup()
    source.find_error = HistoryUnavailable("SECRET-DETAIL")
    reply = ask(tool)
    assert reply["reason"] == "source_unavailable" and "SECRET" not in json.dumps(reply)
    source.find_error = ValueError("SECRET-DETAIL")
    assert ask(tool)["reason"] == "source_error"


# ---------------------------------------------------------------- finding exactly one record


def test_a_missing_or_incomplete_number_is_asked_for_and_nothing_is_looked_up():
    tool, source = lookup()
    assert ask(tool, phone=None)["reason"] == "phone_required" and ask(tool, phone="  ")["reason"] == "phone_required"
    bad = ask(tool, phone="234-5678")
    assert bad["status"] == "invalid_phone" and "area code" in bad["speak"] and "234" not in bad["speak"]
    assert source.read == []


def test_no_matching_record_is_not_found_with_no_invented_history():
    tool, source = lookup()
    source.add("555-999-0000", history(visit(5, "Classic Full Set")))
    reply = ask(tool)
    assert reply["status"] == "not_found" and "date" not in reply and source.read == []


def test_a_number_that_differs_by_one_digit_matches_nothing():
    tool, source = lookup()
    source.add("555-234-5678", history(visit(5, "Classic Full Set")))
    assert ask(tool, phone="555-234-5679")["status"] == "not_found"


def test_different_formatting_of_the_same_number_matches_the_same_record():
    tool, source = lookup()
    source.add("+1 555 234 5678", history(visit(5, "Classic Full Set")))
    assert ask(tool, phone="(555)234-5678")["status"] == "found"


def test_a_name_is_not_required_when_the_number_matches_one_record():
    tool, source = lookup()
    source.add(PHONE, history(visit(5, "Classic Full Set")), name=None)
    assert ask(tool)["status"] == "found"


def test_a_name_the_caller_gives_is_ignored_when_one_record_matches():
    tool, source = lookup()
    source.add(PHONE, history(visit(5, "Classic Full Set")), name="Real Name")
    assert ask(tool, name="Somebody Else")["status"] == "found"


def test_shared_numbers_are_never_silently_chosen_or_described():
    tool, source = lookup()
    source.add(PHONE, history(visit(5, "Classic Full Set")), name="Alex Rivera")
    source.add(PHONE, history(visit(7, "Classic Full Set")), name="Sam Rivera")
    reply = ask(tool)
    assert reply["status"] == "needs_clarification" and reply["reason"] == "name_needed"
    text = json.dumps(reply)
    assert "Alex" not in text and "Sam" not in text and "two" not in text.lower() and "2" not in reply["speak"]
    assert source.read == [], "no history was read before one record was singled out"


def test_a_full_name_can_single_out_one_of_several_records_and_only_that_one_is_read():
    tool, source = lookup()
    a = source.add(PHONE, history(visit(5, "Classic Full Set")), name="Alex Rivera")
    source.add(PHONE, history(visit(7, "Classic Full Set")), name="Sam Rivera")
    reply = ask(tool, name="  sam  RIVERA ")
    assert reply["status"] == "found" and reply["date"] == at(7).date().isoformat()
    assert a not in source.read and len(source.read) == 1, "the other client's history was never even read"


@pytest.mark.parametrize("name", ["Pat Nobody", "Rivera", "Alex"])
def test_a_name_that_does_not_single_out_one_record_gives_no_answer(name):
    tool, source = lookup()
    source.add(PHONE, history(visit(5, "Classic Full Set")), name="Alex Rivera")
    source.add(PHONE, history(visit(7, "Classic Full Set")), name="Sam Rivera")
    reply = ask(tool, name=name)
    assert reply["status"] == "unable_to_confirm" and reply["reason"] == "record_ambiguous" and source.read == []


def test_records_without_names_can_never_be_singled_out_by_a_name():
    tool, source = lookup()
    source.add(PHONE, history(visit(5, "Classic Full Set")), name=None)
    source.add(PHONE, history(visit(7, "Classic Full Set")), name=None)
    assert ask(tool, name="")["reason"] == "name_needed"
    assert ask(tool, name="Anyone")["reason"] == "record_ambiguous"


def test_two_records_with_the_same_name_and_number_are_not_guessed_between():
    tool, source = lookup()
    source.add(PHONE, history(visit(5, "Classic Full Set")), name="Alex Rivera")
    source.add(PHONE, history(visit(7, "Classic Full Set")), name="Alex Rivera")
    assert ask(tool, name="Alex Rivera")["reason"] == "record_ambiguous"


# ---------------------------------------------------------------- the service category the caller asked about


@pytest.mark.parametrize("words,category", [
    ("lashes", "lash"), ("my eyelash appointment", "lash"), ("massage", "massage"), ("facials", "facial"), ("waxing", "waxing"),
    ("lash", "lash"), ("head spa", "head-spa"), ("body scrub", "body-scrub"),
])
def test_the_category_comes_from_the_menu_not_from_one_hard_coded_service(words, category):
    assert Classifier().category_of_request(words) == category


@pytest.mark.parametrize("words", [None, "", "   ", "something nice", "lashes and a massage", "couples massage", 5])
def test_a_missing_ambiguous_or_unknown_category_is_asked_about(words):
    tool, source = lookup()
    source.add(PHONE, history(visit(5, "Classic Full Set")))
    reply = tool.last_completed_visit(CALL, {"service_category": words, "phone": PHONE})
    assert reply["status"] == "needs_clarification" and reply["reason"] == "service_category" and source.read == []


def test_the_same_machinery_answers_for_a_massage():
    tool, source = lookup()
    source.add(PHONE, history(visit(40, "Deep Tissue Massage"), visit(12, "Swedish Massage"), visit(3, "Classic Full Set")))
    reply = ask(tool, category="massage")
    assert reply["date"] == at(12).date().isoformat() and reply["service"] == "Swedish Massage" and "massage" in reply["speak"]


def test_service_titles_are_placed_by_the_menu_and_a_couples_massage_counts_as_a_massage():
    c = Classifier()
    assert c.categories_of("Classic Full Set") == {"lash"} and "massage" in c.categories_of("Couples Massage") and "couples" in c.categories_of("Couples Massage")
    assert c.categories_of("Something Unlisted") == frozenset() and c.categories_of(None) == frozenset()


# ---------------------------------------------------------------- privacy and abuse limits


def test_replies_never_contain_the_number_a_name_or_a_record_handle():
    tool, source = lookup()
    ref = source.add(PHONE, history(visit(5, "Classic Full Set", tech="Ana")), name="Alex Rivera")
    for extra in ({}, {"include_technician": True}, {"name": "Alex Rivera"}):
        text = json.dumps(ask(tool, **extra))
        for secret in ("5552345678", "234-5678", "Rivera", "Alex", ref):
            assert secret not in text, secret


def test_a_caller_can_only_try_a_few_numbers_per_call():
    tool, source = lookup()
    for i in range(MAX_ATTEMPTS_PER_CALL):
        assert ask(tool, phone=f"555-234-56{70 + i}")["status"] == "not_found"
    refused = ask(tool, phone="555-234-5678")
    assert refused["status"] == "refused" and refused["reason"] == "too_many_attempts"
    other_call = tool.last_completed_visit("call_other", {"service_category": "lash", "phone": "555-999-0001"})
    assert other_call["status"] == "not_found", "each call has its own allowance"


def test_incomplete_numbers_do_not_use_up_the_allowance():
    tool, _ = lookup()
    for _i in range(10):
        assert ask(tool, phone="234-5678")["status"] == "invalid_phone"
    assert ask(tool)["status"] == "not_found"


def test_a_missing_call_id_is_refused():
    tool, _ = lookup()
    assert tool.last_completed_visit("", {"service_category": "lash", "phone": PHONE})["reason"] == "no_call_id"


def test_the_answer_is_limited_to_date_service_and_optionally_the_technician():
    tool, source = lookup()
    source.add(PHONE, history(visit(5, "Classic Full Set", tech="Ana")))
    assert set(ask(tool, include_technician=True)) == {"status", "ok", "speak", "date", "service", "technician"}
    assert set(ask(tool)) == {"status", "ok", "speak", "date", "service"}


# ---------------------------------------------------------------- NOT exposed, NOT touching Booksy


def test_the_history_function_is_not_a_route_and_not_in_any_retell_tool_file():
    assert not any("history" in r or "last_completed" in r for r in (*ROUTES.values(), *READ_ONLY_ROUTES.values(), *ROUTES, *READ_ONLY_ROUTES))
    assert set(READ_ONLY_ROUTES.values()) == {"lookup_service", "check_slot", "find_alternatives"}
    for name in ("tools.json", "tools_availability_only.json"):
        text = (BOOKING / "voice_agent_draft" / name).read_text(encoding="utf-8").lower()
        assert "last_completed" not in text and "history" not in text


def test_nothing_that_serves_the_demo_imports_the_history_package():
    for relative in ("aria_booking/voice/launch.py", "aria_booking/voice/retell_http.py", "aria_booking/voice/tools.py", "aria_booking/cli.py"):
        tree = ast.parse((BOOKING / relative).read_text(encoding="utf-8"))
        names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not any("history" in name for name in names), relative


def test_the_history_package_has_no_browser_or_network_access():
    banned = {"selenium", "requests", "urllib", "http", "socket", "subprocess", "webbrowser"}
    for path in (BOOKING / "aria_booking" / "history").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0}
        imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not imported & banned, (path.name, imported & banned)
    relative = {(n.module or "") for p in (BOOKING / "aria_booking" / "history").glob("*.py") for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
                if isinstance(n, ast.ImportFrom) and n.level > 0}
    assert not {m for m in relative if "selenium" in m or "driver" in m or "discover" in m}


def test_no_real_looking_phone_numbers_or_names_are_in_the_history_package_or_these_tests():
    texts = [p.read_text(encoding="utf-8") for p in (BOOKING / "aria_booking" / "history").glob("*.py")]
    texts.append(Path(__file__).read_text(encoding="utf-8"))
    for text in texts:
        for number in re.findall(r"\b\d{3}[-. ]\d{3}[-. ]\d{4}\b", text):
            digits = re.sub(r"\D", "", number)
            if digits[0] in "01" or digits[3] in "01":
                continue  # not a possible US number at all (these are the deliberately invalid test inputs)
            assert digits.startswith("555"), "only fictional 555 numbers"
