"""The demo agent's prompt: generated from the shared conversation template and the SERENITY showcase menu, over the demo registry. It does not announce that the calendar
is a prototype, answers truthfully if asked, names the salon Serenity Spa, and carries nothing from the earlier salon's identity, staff, address or website knowledge."""

from __future__ import annotations

import json
import re
from pathlib import Path

from aria_booking.catalog.render import AVAILABILITY_TOOLS_FILE, DEMO_PROMPT_FILE, DEMO_SECTION, build_demo_prompt, build_prompt
from aria_booking.demo.dataset import demo_registry
from aria_booking.showcase import SHOWCASE, load_showcase
from aria_booking.showcase.catalog import SHOWCASE_CATALOG
from aria_booking.showcase.leaks import find_leaks
from aria_booking.showcase.roster import ROSTER_ROWS

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


# ---------------------------------------------------------------- the Serenity identity, and nothing of the earlier salon


def test_the_agent_is_named_serenity_spa_and_the_whole_generated_file_carries_nothing_of_the_earlier_salon():
    head, body = PROMPT.split("## Who you are")
    assert "voice receptionist for Serenity Spa" in body and "- Salon: Serenity Spa." in body
    assert find_leaks(PROMPT) == [], "no old staff name, business name, town, street or phone number, anywhere in the file (header included)"
    assert "booksy" not in PROMPT.lower() and "website" not in PROMPT.lower() and "menu" in body.lower(), "the source of services and prices is the prototype menu"


def test_no_address_or_location_and_no_phone_is_given_until_one_is_configured():
    assert "- Address and location: none is configured. Do not state, guess or look up an address, town or directions" in PROMPT
    assert "- Phone: no number is configured yet. Do not give, guess or invent one" in PROMPT
    with_phone = build_demo_prompt(TEMPLATE, showcase=load_showcase(overrides={"contact_phone": "+1 555 010 0199"}))
    assert "- Phone: +1 555 010 0199. Give it when a caller needs the salon directly, and never say a transfer, callback or message has happened." in with_phone
    assert "no number is configured" not in with_phone


def test_the_knowledge_comes_from_the_showcase_menu_with_its_own_wording_not_from_the_earlier_website_knowledge():
    from aria_booking.catalog.lookup import DEFAULT_CATALOG

    body = PROMPT.split("### Services and listed prices")[1].split("### What you can check availability for right now")[0]
    for family in SHOWCASE_CATALOG.families():
        first = next(i for i in SHOWCASE_CATALOG.items if i.family == family)
        assert first.description in body, family  # the prompt lists a family once, with its first variant's description
    old_descriptions = {i.description for i in DEFAULT_CATALOG.items}
    assert not any(item.description in old_descriptions for item in SHOWCASE_CATALOG.items), "no description is carried over from the earlier salon's text"
    assert "Source: the Serenity Spa prototype menu. Its prices, lengths and hours are example data that can be edited." in PROMPT
    assert "official website" not in PROMPT and "read on 2026" not in PROMPT


def test_the_legacy_trimester_example_in_the_shared_template_is_not_carried_into_the_showcase():
    assert "trimester" in TEMPLATE, "(the shared template still has it: that is why the demo adapts it)"
    assert "trimester" not in PROMPT and "its stated conditions, if the menu lists any" in PROMPT


def test_the_template_example_technician_is_a_fictional_roster_name_not_the_template_default():
    from aria_booking.showcase.roster import name_of

    assert re.search(r'"Is Sienna available Monday at 4 for a deep tissue', PROMPT) and name_of("910006") == "Sienna"
    assert "One moment while I check\n  Sienna's availability." in PROMPT


def test_every_roster_name_in_the_prompt_is_one_of_the_fictional_first_names_or_absent():
    names = {row.name for row in ROSTER_ROWS}
    assert len(names) == len(ROSTER_ROWS) and all(" " not in n for n in names), "distinct single first names"
    assert not any(n in PROMPT for n in names - {"Sienna"}), "the prompt does not list the roster: the tools do"


def test_the_opening_line_and_the_prompt_use_the_same_name_and_neither_announces_the_demo():
    from aria_booking.demo import SALON_NAME
    from aria_booking.demo.agent_ops import OperatorConfig

    assert SALON_NAME == SHOWCASE.name == "Serenity Spa" and OperatorConfig().begin_message is None
    assert SALON_NAME in PROMPT.split("## Your opening line")[1].split("## Two sources")[0]


def test_the_salon_name_is_applied_at_rendering_time_and_can_be_changed_in_one_place():
    other = build_demo_prompt(TEMPLATE, salon_name="Lotus Spa")
    body = other.split("## Who you are")[1]
    assert "voice receptionist for Lotus Spa" in body and "Serenity" not in body.replace("Source: the Serenity Spa prototype menu", "") and find_leaks(other) == []


def test_the_leak_guard_stops_a_build_that_would_carry_an_old_identifier():
    import pytest

    bad = TEMPLATE.replace("## Who you are", "## Who you are\n\nCall Pinny.\n", 1)
    with pytest.raises(ValueError, match="earlier salon's identifiers"):
        build_demo_prompt(bad)


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
    availability = build_prompt(TEMPLATE, mode="availability")
    assert "DEMO calendar" not in availability and "DEMO calendar" not in (DRAFT / "prompt_availability_only.md").read_text(encoding="utf-8")
    assert "Serenity" not in availability and find_leaks(availability, allow_phone=True), "the earlier salon's own prompts still describe the earlier salon"


def test_the_demo_uses_the_existing_read_only_tool_file_which_has_no_booking_function():
    tools = json.loads((DRAFT / AVAILABILITY_TOOLS_FILE).read_text(encoding="utf-8"))["tools"]
    assert {t["name"] for t in tools} == {"lookup_service", "check_slot", "find_alternatives"}


def test_the_prompt_is_not_absurdly_long():
    assert len(PROMPT) < 30_000
