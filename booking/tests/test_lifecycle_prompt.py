"""The simulated-lifecycle agent's prompt and tool file: generated, free of the earlier salon's identity, consistent with the tools the endpoint really serves, and carrying the
conversation rules that were asked for (read-back before every change, an optional reason with an offer to move, no invented fees or policies, no claims about texts or transfers)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from aria_booking.appointments.tools import LIFECYCLE_ROUTES
from aria_booking.catalog.render import (
    AVAILABILITY_TOOLS_FILE, DEMO_WORDING, LIFECYCLE_PROMPT_FILE, LIFECYCLE_SECTION, LIFECYCLE_TEMPLATE_FILE, LIFECYCLE_TOOLS_FILE, build_demo_prompt, build_lifecycle_tools,
)
from aria_booking.demo.dataset import demo_registry
from aria_booking.showcase import load_showcase
from aria_booking.showcase.leaks import find_leaks

DRAFT = Path(__file__).resolve().parent.parent / "voice_agent_draft"
SRC = Path(__file__).resolve().parent.parent / "aria_booking"
TEMPLATE = (DRAFT / LIFECYCLE_TEMPLATE_FILE).read_text(encoding="utf-8")
SHARED = (DRAFT / "prompt_template.md").read_text(encoding="utf-8")
PROMPT = (DRAFT / LIFECYCLE_PROMPT_FILE).read_text(encoding="utf-8")
TOOLS = json.loads((DRAFT / LIFECYCLE_TOOLS_FILE).read_text(encoding="utf-8"))
ROUTE_NAMES = set(LIFECYCLE_ROUTES.values())


def flat(text: str) -> str:
    """Whitespace collapsed: a phrase that wraps across lines in the template is still the same phrase."""
    return " ".join(text.split())


FLAT_PROMPT = flat(PROMPT)


def section(text: str, heading: str, *, collapse: bool = True) -> str:
    head = f"## {heading}\n"
    start = text.index(head)
    end = text.find("\n## ", start + len(head))
    body = text[start:end if end != -1 else len(text)]
    return flat(body) if collapse else body


# ---------------------------------------------------------------- generated, and the other prompts untouched


def test_the_committed_files_are_exactly_what_the_generator_produces():
    assert PROMPT == build_demo_prompt(TEMPLATE, lifecycle=True), "regenerate with: python -m aria_booking.catalog.render --write"
    availability = json.loads((DRAFT / AVAILABILITY_TOOLS_FILE).read_text(encoding="utf-8"))
    assert TOOLS == build_lifecycle_tools(availability)


def test_the_availability_only_demo_prompt_and_tools_are_unchanged_by_this_mode():
    from aria_booking.catalog.render import DEMO_PROMPT_FILE

    assert (DRAFT / DEMO_PROMPT_FILE).read_text(encoding="utf-8") == build_demo_prompt(SHARED)
    availability = json.loads((DRAFT / AVAILABILITY_TOOLS_FILE).read_text(encoding="utf-8"))
    assert {t["name"] for t in availability["tools"]} == {"lookup_service", "check_slot", "find_alternatives"}


def test_the_shared_rules_are_the_shared_templates_own_words():
    adapted = section(SHARED, "Two sources, never mixed")
    for old, new in DEMO_WORDING:
        adapted = adapted.replace(old, new)
    assert section(TEMPLATE, "Two sources, never mixed") == adapted, "edit the shared section in prompt_template.md and here together"


# ---------------------------------------------------------------- identity


def test_it_is_serenity_spa_and_carries_nothing_of_the_earlier_salon():
    assert find_leaks(PROMPT) == [] and "{SALON}" not in PROMPT and "{TECH}" not in PROMPT and "Glamour" not in PROMPT
    assert "voice receptionist for Serenity Spa" in PROMPT and "booksy" not in PROMPT.lower() and "website" not in PROMPT.lower()
    assert "## Your opening line" in PROMPT and "This is Aria at Serenity Spa." in PROMPT and PROMPT.count("## Who you are") == 1


def test_it_does_not_announce_the_prototype_but_answers_truthfully_when_asked():
    assert LIFECYCLE_SECTION in PROMPT
    assert 'Do NOT announce this and do not say "demo", "simulated" or "prototype" on your own' in FLAT_PROMPT
    assert "answer truthfully: this is a prototype with an invented calendar, not the salon's real schedule, and the appointments made here exist only in it" in FLAT_PROMPT
    assert "Never describe an invented appointment as a real one" in FLAT_PROMPT and "cannot book, hold, change or cancel anything" not in FLAT_PROMPT


def test_the_menu_and_the_bookable_list_are_the_showcases():
    assert "### What you can check and book right now" in PROMPT and "What you can check availability for right now" not in PROMPT
    for service_id in demo_registry().ids():
        assert f"service_id `{service_id}`" in PROMPT
    assert "- Phone: no number is configured yet. Do not give, guess or invent one" in PROMPT
    with_phone = build_demo_prompt(TEMPLATE, lifecycle=True, showcase=load_showcase(overrides={"contact_phone": "+1 310 555 0123"}))
    assert "- Phone: +1 310 555 0123. Give it when a caller needs the salon directly, and never say a transfer, callback or message has happened." in with_phone


# ---------------------------------------------------------------- the conversation rules that were asked for


def test_every_change_is_read_back_and_confirmed_and_the_success_statuses_are_explicit():
    rules = section(PROMPT, "Hard rules")
    assert "**Every change is two steps**: a read-back, then a clear yes" in rules and "never reuse an old token" in rules
    assert "`booked_verified` or `already_booked`; `cancelled` or `already_cancelled`; `rescheduled`)" in rules and "with `ok: true`" in rules
    booking = section(PROMPT, "Booking an appointment")
    assert "**This does not book anything yet**" in booking and "`confirmation_token` and `confirmed: true`" in booking
    assert "complete phone number including the area code" in booking and "Do not say the number back aloud" in booking


def test_finding_answers_the_asked_service_first_and_never_leaks_another_clients_details():
    finding = section(PROMPT, "Finding an appointment")
    assert "Answer the service they asked about first, then the others briefly" in finding and "`find_appointments`" in finding
    assert "Never say another client's details, how many records matched, or the phone number" in finding and "Looking up never changes anything" in finding
    assert "`name` only when the tool asks for the full name" in finding


def test_cancelling_asks_why_optionally_offers_another_time_and_never_invents_a_fee():
    text = section(PROMPT, "Cancelling or moving an appointment")
    assert "the reason is optional and they do not have to answer" in text
    assert "If they say they are busy or cannot make it, offer another day or time" in text
    assert "If they do not want another time, cancel anyway: cancelling never depends on choosing a new slot" in text
    assert "Never mention a fee, a penalty, a deadline or a policy for cancelling or moving: you have none" in text
    assert "the original appointment is unchanged" in text


def test_the_handoff_gives_the_configured_number_and_never_claims_a_transfer_a_text_or_a_callback():
    rules = section(PROMPT, "Hard rules")
    assert "give the salon's name and phone number from the knowledge section if it has one (if it has none, say you do not have a number to give)" in rules
    assert "Giving a number is not a transfer" in rules and "never say a call was transferred" in rules
    assert "ANY promise of a callback, a text message, an alert, or that staff have been notified" in section(PROMPT, "Never say")
    for banned in ("you will receive a text", "we will text", "a confirmation text"):
        assert banned not in PROMPT.lower()


def test_the_latest_completed_visit_is_separate_from_the_upcoming_lookup():
    text = section(PROMPT, "A client's last visit")
    assert "`last_completed_visit`" in text and "`service_category`" in text and "never name a date you did not get from the tool" in text


def test_there_is_no_booksy_function_or_instruction_anywhere():
    assert "book_slot" not in PROMPT and "option_id" not in PROMPT and "book_slot" not in json.dumps(TOOLS)


# ---------------------------------------------------------------- the tool file matches the endpoint


def test_the_tool_file_defines_exactly_the_functions_the_endpoint_serves():
    assert [t["name"] for t in TOOLS["tools"]][:3] == ["lookup_service", "check_slot", "find_alternatives"]
    assert {t["name"] for t in TOOLS["tools"]} == ROUTE_NAMES and len(TOOLS["tools"]) == len(ROUTE_NAMES) == 8
    for tool in TOOLS["tools"]:
        assert tool["url"] == f"https://<YOUR-TUNNEL-HOST>/tools/{tool['name']}" and f"/tools/{tool['name']}" in LIFECYCLE_ROUTES and tool["method"] == "POST"
        assert tool["max_retry"] == 0 and tool["timeout_ms"] >= 5000 and tool["headers"]["Authorization"].startswith("Bearer <set in")


def test_every_argument_a_definition_names_is_one_the_tools_read_and_the_required_ones_exist():
    source = "\n".join((SRC / part).read_text(encoding="utf-8") for part in ("appointments/tools.py", "voice/tools.py", "history/lookup.py"))
    read = set(re.findall(r'args(?:\.get)?\(?\[?"([a-z_]+)"', source)) | set(re.findall(r'for k in \(([^)]*)\)', source) and re.findall(r'"([a-z_]+)"', " ".join(re.findall(r'for k in \(([^)]*)\)', source))))
    for tool in TOOLS["tools"]:
        properties = tool["parameters"]["properties"]
        assert set(tool["parameters"]["required"]) <= set(properties), tool["name"]
        assert set(properties) <= read | {"confirmation_token", "confirmed"}, (tool["name"], sorted(set(properties) - read))
    by_name = {t["name"]: t for t in TOOLS["tools"]}
    assert set(by_name["book_appointment"]["parameters"]["properties"]) >= {"service_id", "date", "time", "client_name", "phone", "confirmation_token", "confirmed"}
    assert set(by_name["cancel_appointment"]["parameters"]["properties"]) >= {"reference", "phone", "reason", "confirmation_token", "confirmed"}
    assert set(by_name["reschedule_appointment"]["parameters"]["properties"]) >= {"reference", "phone", "date", "time", "confirmation_token", "confirmed"}


def test_the_descriptions_say_what_each_function_changes_and_how_it_is_confirmed():
    by_name = {t["name"]: t["description"] for t in TOOLS["tools"]}
    for name in ("find_appointments", "last_completed_visit", "check_slot", "find_alternatives"):
        assert "changes nothing" in by_name[name] or "nothing is booked, held or changed" in by_name[name], name
    for name in ("book_appointment", "cancel_appointment", "reschedule_appointment"):
        assert "TWO steps" in by_name[name] and "saves nothing" in by_name[name].replace("saves NOTHING", "saves nothing") and "ok true" in by_name[name], name
    assert "this line only checks" not in json.dumps(TOOLS).lower() and "cannot book" not in json.dumps(TOOLS).lower()
    assert "Never mention a fee or policy" in by_name["cancel_appointment"] and "optional" in by_name["cancel_appointment"]
