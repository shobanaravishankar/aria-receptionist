"""The demo agent's prompt: generated from the same template and catalogue as the others, over the demo registry, and it says the calendar is simulated."""

from __future__ import annotations

import json
from pathlib import Path

from aria_booking.catalog.render import AVAILABILITY_TOOLS_FILE, DEMO_PROMPT_FILE, DEMO_SECTION, build_demo_prompt, build_prompt
from aria_booking.demo.dataset import demo_registry

DRAFT = Path(__file__).resolve().parent.parent / "voice_agent_draft"
TEMPLATE = (DRAFT / "prompt_template.md").read_text(encoding="utf-8")
PROMPT = (DRAFT / DEMO_PROMPT_FILE).read_text(encoding="utf-8")


def test_the_committed_demo_prompt_is_exactly_what_the_generator_produces():
    assert PROMPT == build_demo_prompt(TEMPLATE), "regenerate with: python -m aria_booking.catalog.render --write"


def test_it_says_the_calendar_is_simulated_and_tells_the_agent_to_say_so():
    assert DEMO_SECTION in PROMPT
    assert "SIMULATED demo calendar" in PROMPT and "Demo calendar - simulated availability" in PROMPT
    assert "never describe a demo appointment as a real one" in PROMPT
    assert "never\n  swap in a similar-sounding name" in PROMPT and "ask the caller" in PROMPT


def test_the_demo_section_sits_before_the_waiting_rules_and_each_appears_once():
    assert PROMPT.count("## You are connected to a DEMO calendar") == 1 and PROMPT.count("## While the calendar is being checked") == 1
    assert PROMPT.index("## You are connected to a DEMO calendar") < PROMPT.index("## While the calendar is being checked")


def test_it_lists_every_demo_service_and_no_other_as_checkable():
    registry = demo_registry()
    listed = [line for line in PROMPT.splitlines() if line.startswith("- service_id `")]
    assert {line.split("`")[1] for line in listed} == set(registry.ids())
    assert "booking" not in PROMPT.split("### What you can check availability for right now", 1)[1].split("Every other service", 1)[0].lower()


def test_it_is_the_availability_only_prompt_with_no_booking_function_and_no_promises():
    assert "You can only check availability" in PROMPT and "book_slot" not in PROMPT and "option_id" not in PROMPT
    assert "Do not promise follow-up" in PROMPT and "One moment while I check" in PROMPT


def test_the_other_prompts_are_untouched_by_the_demo():
    assert "DEMO calendar" not in build_prompt(TEMPLATE, mode="availability") and "DEMO calendar" not in (DRAFT / "prompt_availability_only.md").read_text(encoding="utf-8")


def test_the_demo_uses_the_existing_read_only_tool_file_which_has_no_booking_function():
    tools = json.loads((DRAFT / AVAILABILITY_TOOLS_FILE).read_text(encoding="utf-8"))["tools"]
    assert {t["name"] for t in tools} == {"lookup_service", "check_slot", "find_alternatives"}


def test_the_prompt_is_not_absurdly_long():
    assert len(PROMPT) < 30_000
