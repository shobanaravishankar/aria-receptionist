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
    assert 1000 <= tool["timeout_ms"] <= 600000 and tool["speak_during_execution"] is True
    assert not tool.get("payload_args_only"), "the server needs the call object (call_id)"


def test_the_parameters_match_what_the_server_reads():
    check = TOOLS["check_slot"]["parameters"]
    assert set(check["properties"]) == {"date", "time"} and set(check["required"]) == {"date", "time"}
    alt = TOOLS["find_alternatives"]["parameters"]
    assert set(alt["properties"]) == {"date", "time"} and alt["required"] == ["date"]
    book = TOOLS["book_slot"]["parameters"]
    assert set(book["properties"]) == {"option_id", "confirmed"} and book["required"] == ["option_id"]
    assert book["properties"]["confirmed"]["type"] == "boolean", "the server accepts only the JSON boolean true"
    for tool in TOOLS.values():
        assert tool["parameters"]["type"] == "object"
        assert all(p["type"] == "string" for n, p in tool["parameters"]["properties"].items() if n != "confirmed")


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


def test_the_prompt_states_the_honesty_rules_the_server_enforces():
    low = PROMPT.lower()
    for phrase in ("booked_verified", "already_booked", "needs_review", "confirmed: true", "one booking per call",
                   "never state or imply availability", "full 2 hours 30 minutes", "the tools are the only source"):
        assert phrase in low, phrase


def test_every_status_the_server_can_return_is_named_in_the_prompt():
    import inspect
    import re as _re

    from aria_booking.voice import tools as module

    statuses = set(_re.findall(r'_response\(\s*"([a-z_]+)"', inspect.getsource(module))) | {"error"}  # `error` comes from the HTTP layer
    assert {"booked_verified", "already_booked", "needs_review", "unknown", "confirmation_required"} <= statuses
    missing = sorted(s for s in statuses if s not in PROMPT)
    assert not missing, f"the prompt never mentions: {missing}"


def test_the_prompt_is_clearly_test_only_and_carries_no_real_spa_information():
    assert "TEST-ONLY" in PROMPT and "DRAFT" in PROMPT and "must stay untouched" in PROMPT
    body = PROMPT.split("## Who you are", 1)[1].lower()
    for leaked in ("9:30", "8:00 pm", "8 pm", "facial", "massage", "manicure", "haircut", "pedicure", "waxing"):
        assert leaked not in body, f"real-spa detail in the test prompt: {leaked}"
    assert "ignore any other business information" in body, "the instruction that blocks the baseline's hours and menu"
    assert not (DRAFT / "prompt.md").exists(), "the earlier 'add to the existing prompt' draft was replaced"


def test_the_request_size_limit_leaves_room_for_a_long_transcript():
    """Retell sends the transcript so far in `call`; a very long call must not be refused for size alone."""
    assert MAX_BODY_BYTES >= 256 * 1024
