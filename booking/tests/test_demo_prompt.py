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


def test_it_tells_the_agent_not_to_announce_the_prototype_but_to_answer_truthfully_when_asked():
    assert DEMO_SECTION in PROMPT
    assert "prototype calendar with invented appointments" in PROMPT and 'labelled\n  "Demo calendar - simulated availability"' in PROMPT
    assert 'Do NOT announce this and do not say "demo", "simulated" or "prototype" on your own, in the greeting or in any answer' in PROMPT
    assert "If the caller asks whether the schedule, the availability or an appointment is real or live, or whether this is a\n  demo, answer truthfully" in PROMPT
    assert "this is a prototype with an invented calendar, not the salon's real schedule" in PROMPT
    assert "Never describe an invented appointment as a real one" in PROMPT and "never imply anything was booked" in PROMPT
    assert "never\n  swap in a similar-sounding name" in PROMPT and "ask the caller" in PROMPT
    assert "Say so when you give availability" not in PROMPT and "already says it" not in PROMPT and "Say\n  they are from the simulated demo calendar" not in PROMPT


def test_the_agent_is_named_serenity_spa_in_its_own_part_of_the_prompt_and_the_services_are_untouched():
    marker = "## Who you are"
    head, body = PROMPT.split(marker)
    assert "Glamour Day Spa" not in body and "voice receptionist for Serenity Spa" in body and "- Salon: Serenity Spa (Warren, NJ)" in body
    assert "Glamour Day Spa Receptionist" in head, "the note about the original public agent keeps its real name"
    for service_line in ("- Glamour Full Set: initial set: 1 hr 15 min $149", "- Glamour 5 Visit Package: package $345", "- Glamour 10 Visit Package: package $650"):
        assert service_line in body, service_line


def test_only_the_salon_name_changed_in_the_website_knowledge():
    start, stop = "## What you may say about the salon", "### What you can check availability for right now"
    availability = build_prompt(TEMPLATE, mode="availability")
    ours, theirs = PROMPT.split(start, 1)[1].split(stop, 1)[0], availability.split(start, 1)[1].split(stop, 1)[0]
    assert ours.replace("Serenity Spa", "Glamour Day Spa") == theirs, "services, lengths, prices, hours and address come from the website knowledge unchanged"
    assert "Glamour Day Spa" in availability and "Serenity" not in availability, "the other prompts are not renamed"


def test_the_opening_line_and_the_prompt_use_the_same_name_and_neither_announces_the_demo():
    from aria_booking.demo import SALON_NAME
    from aria_booking.demo.agent_ops import OperatorConfig, opening_line

    assert SALON_NAME == "Serenity Spa" and OperatorConfig().begin_message == opening_line() and SALON_NAME in opening_line()
    assert opening_line("Other Spa").startswith("Hi, I'm Aria, your AI receptionist for Other Spa.")
    assert not any(word in opening_line().lower() for word in ("demo", "simulat", "prototype", "glamour")) and SALON_NAME in PROMPT


def test_the_salon_name_is_applied_at_rendering_time_and_can_be_changed_in_one_place():
    other = build_demo_prompt(TEMPLATE, salon_name="Lotus Spa")
    body = other.split("## Who you are")[1]
    assert "voice receptionist for Lotus Spa" in body and "Serenity" not in body and "Glamour Day Spa" not in body and "Glamour Full Set" in body


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
