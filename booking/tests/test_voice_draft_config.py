"""The draft Retell tool definitions must agree with the server that will receive them, and must hold no secrets."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from aria_booking.voice.retell_http import MAX_BODY_BYTES, ROUTES

DRAFT = Path(__file__).resolve().parent.parent / "voice_agent_draft"
CONFIG = json.loads((DRAFT / "tools.json").read_text(encoding="utf-8"))
TOOLS = {t["name"]: t for t in CONFIG["tools"]}


def test_there_is_exactly_one_definition_per_server_route():
    assert set(TOOLS) == set(ROUTES.values()) and len(CONFIG["tools"]) == len(ROUTES)


@pytest.mark.parametrize("name", sorted(ROUTES.values()))
def test_each_definition_points_at_its_own_route_with_post_and_no_retries(name):
    tool = TOOLS[name]
    route = next(path for path, fn in ROUTES.items() if fn == name)
    assert tool["url"] == f"https://<YOUR-TUNNEL-HOST>{route}" and tool["method"] == "POST"
    assert tool["max_retry"] == 0
    assert 1000 <= tool["timeout_ms"] <= 600000
    assert tool["speak_during_execution"] is (name != "lookup_service"), "only the local lookup needs no filler speech"
    assert not tool.get("payload_args_only"), "the server needs the call object (call_id)"


def test_the_parameters_match_what_the_server_reads():
    look = TOOLS["lookup_service"]["parameters"]
    assert set(look["properties"]) == {"query"} and look["required"] == ["query"]
    check = TOOLS["check_slot"]["parameters"]
    assert set(check["properties"]) == {"service_id", "date", "time", "staff"} and set(check["required"]) == {"service_id", "date", "time"}
    alt = TOOLS["find_alternatives"]["parameters"]
    assert set(alt["properties"]) == {"service_id", "date", "time", "staff"} and set(alt["required"]) == {"service_id", "date"}
    book = TOOLS["book_slot"]["parameters"]
    assert set(book["properties"]) == {"option_id", "confirmed"} and book["required"] == ["option_id"], "an option fixes service and technician"
    assert book["properties"]["confirmed"]["type"] == "boolean", "the server accepts only the JSON boolean true"
    for tool in TOOLS.values():
        assert tool["parameters"]["type"] == "object"
        assert all(p["type"] == "string" for n, p in tool["parameters"]["properties"].items() if n != "confirmed")


def test_the_local_lookup_is_fast_and_the_booking_call_is_the_slowest():
    assert TOOLS["lookup_service"]["timeout_ms"] < TOOLS["check_slot"]["timeout_ms"] <= TOOLS["book_slot"]["timeout_ms"]


def test_the_book_timeout_is_longer_than_the_read_timeout():
    assert TOOLS["book_slot"]["timeout_ms"] > TOOLS["check_slot"]["timeout_ms"]


def test_the_draft_holds_placeholders_only_and_no_secret_shaped_values():
    text = (DRAFT / "tools.json").read_text(encoding="utf-8")
    assert "<YOUR-TUNNEL-HOST>" in text and "<set in the Retell dashboard>" in text
    assert not re.search(r"key_[A-Za-z0-9]{8,}", text), "looks like a Retell key"
    assert not re.search(r"Bearer (?!<)[A-Za-z0-9._-]{8,}", text), "a real bearer token"
    assert not re.search(r"https://(?!<YOUR-TUNNEL-HOST>)[a-z0-9.-]+\.[a-z]{2,}/", text), "a real host"
    assert not re.search(r"\b\d{7,}\b", text), "a long number (a business id?)"


PROMPT = (DRAFT / "prompt_test_only.md").read_text(encoding="utf-8")
NORMAL = " ".join(PROMPT.lower().replace("**", "").split())  # one line, no bold: for phrase checks that survive re-wrapping
TEMPLATE = (DRAFT / "prompt_template.md").read_text(encoding="utf-8")


def test_the_committed_prompt_is_exactly_the_template_plus_the_catalogue_so_they_cannot_drift():
    from aria_booking.catalog.bookable import BookableRegistry
    from aria_booking.catalog.render import build_prompt
    from aria_booking.config import Config

    registry = BookableRegistry.from_config(Config(business_id="0000000"))
    assert PROMPT == build_prompt(TEMPLATE, registry=registry), "run: python -m aria_booking.catalog.render --write"


def test_the_prompt_states_the_honesty_rules_the_server_enforces():
    low = NORMAL
    for phrase in ("booked_verified", "already_booked", "needs_review", "confirmed: true", "one booking per call",
                   "never state or imply availability", "the calendar tools are the only source", "full length"):
        assert phrase in low, phrase


def test_every_status_the_server_can_return_is_named_in_the_prompt():
    import inspect
    import re as _re

    from aria_booking.voice import tools as module

    statuses = set(_re.findall(r'_response\(\s*"([a-z_]+)"', inspect.getsource(module))) | {"error"}  # `error` comes from the HTTP layer
    assert {"booked_verified", "already_booked", "needs_review", "unknown", "confirmation_required", "service_info", "not_bookable"} <= statuses
    missing = sorted(s for s in statuses if s not in PROMPT)
    assert not missing, f"the prompt never mentions: {missing}"


def test_the_prompt_is_clearly_test_only_and_does_not_carry_the_old_rules():
    assert "TEST-ONLY" in PROMPT and "DRAFT" in PROMPT and "must stay untouched" in NORMAL
    low = NORMAL
    assert "no scheduling connection" in low and "deliberately not carried over" in low, "the old rule is named only to say it is dropped"
    for old_rule in ("redirect callers to the website", "visit our website to book", "i can't book appointments"):
        assert old_rule not in low
    assert not (DRAFT / "prompt.md").exists(), "the earlier 'add to the existing prompt' draft stays replaced"


def test_website_questions_are_separated_from_the_calendar_in_the_prompt():
    low = NORMAL
    for phrase in ("no tool is needed for these questions", "you must not check the calendar", "discussing a service is not a request to book",
                   "never use them to say whether an appointment is or is not available", "do not look at the calendar unless the caller says"):
        assert phrase in low, phrase
    assert "retail products" in low and "do not have that information" in low


def test_the_prompt_names_the_boundaries_for_medical_questions_and_follow_up():
    low = NORMAL
    assert "do not diagnose" in low and "do not promise follow-up" in low and "provisional" in low


def test_the_prompt_lists_the_bookable_service_ids_and_marks_the_rest_information_only():
    assert "`test-aria-salon`" in PROMPT and "INFORMATION ONLY" in PROMPT
    assert "@" not in PROMPT and not re.search(r"\(\d{3}\)\s*\d{3}-\d{4}", PROMPT), "no contact details in the prompt"


def test_the_generated_prompt_stays_a_reasonable_size():
    assert 8_000 < len(PROMPT) < 30_000


def test_the_request_size_limit_leaves_room_for_a_long_transcript():
    """Retell sends the transcript so far in `call`; a very long call must not be refused for size alone."""
    assert MAX_BODY_BYTES >= 256 * 1024


def test_the_handover_checklist_names_the_reviewed_commit_and_separates_permission_from_verification():
    text = (DRAFT / "SUPERVISED_TEST_CHECKLIST.md").read_text(encoding="utf-8")
    assert "f5f531263a0d162840362678d3101346be827e4a" in text and "525 passed" in text
    assert "Permission is not verification" in text and "Authorization versus verification" in text
    assert "**not yet; the main open question**" in text
    assert text.count("- [ ]") >= 15 and "- [x]" not in text, "no step may be pre-ticked as done"
    for secret_shape in (r"key_[A-Za-z0-9]{8,}", r"Bearer (?!<)[A-Za-z0-9._-]{8,}", r"\+?1?[ -]?\(?\d{3}\)?[ -]\d{3}[ -]\d{4}", r"https://(?!<)[a-z0-9.-]+\.[a-z]{2,}/"):
        assert not re.search(secret_shape, text), secret_shape
    assert "Do not ask for it again" in text or "do not ask for it again" in text.lower()


def test_the_draft_readme_no_longer_says_nothing_is_authorized():
    text = (DRAFT / "README.md").read_text(encoding="utf-8")
    assert "No tunnel, public endpoint, credential or live Retell change is authorized yet" not in text
    assert "Authorized" in text and "Verified:" in text and "none of it yet" in text
