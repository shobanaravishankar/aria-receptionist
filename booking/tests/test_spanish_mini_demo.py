"""The optional Spanish mini-demonstration (a prompt variant; nothing is applied to any agent): Spanish for a greeting, the menu and prices ONLY, English tool arguments, and a return to
English for every appointment. These tests keep the English prompts untouched, keep the variant exactly the preferences prompt plus one section, and check -- on the real tools -- that the
canonical English queries the section prescribes return the menu's own facts, so a Spanish answer built from them cannot carry an invented price or length. They cannot show that speech
recognition, argument fidelity or spoken Spanish work: that needs a supervised call. No network, no Retell, no paid anything."""

from __future__ import annotations

import re

import pytest

from aria_booking.catalog import render
from aria_booking.catalog.render import DRAFT_DIR, LIFECYCLE_TEMPLATE_FILE, PREFERENCES_PROMPT_FILE, SPANISH_PROMPT_FILE, SPANISH_SECTION, build_demo_prompt
from aria_booking.showcase.catalog import SHOWCASE_CATALOG

from test_appointment_tools import Env


@pytest.fixture(scope="module")
def template():
    return (DRAFT_DIR / LIFECYCLE_TEMPLATE_FILE).read_text(encoding="utf-8")


def body(text: str) -> str:
    """What the agent is sent: from the first section heading on."""
    return text.split("## Who you are", 1)[1]


def test_the_generated_spanish_prompt_is_exactly_what_the_builder_makes(template):
    written = (DRAFT_DIR / SPANISH_PROMPT_FILE).read_text(encoding="utf-8")
    assert written == build_demo_prompt(template, lifecycle=True, preferences=True, spanish=True), "run: python -m aria_booking.catalog.render --write"


def test_the_variant_is_the_preferences_prompt_plus_one_section_and_nothing_else(template):
    preferences = build_demo_prompt(template, lifecycle=True, preferences=True)
    spanish = build_demo_prompt(template, lifecycle=True, preferences=True, spanish=True)
    assert spanish.count(SPANISH_SECTION) == 1
    assert body(spanish).replace(SPANISH_SECTION, "") == body(preferences), "removing the section gives the English prompt back, byte for byte"
    assert spanish.index(SPANISH_SECTION) < spanish.index("## While a tool is working"), "it sits with the other conversation sections, before the tool-wait rules"
    assert spanish.index("## Hard rules") > spanish.index(SPANISH_SECTION) or spanish.index("## Hard rules") < spanish.index(SPANISH_SECTION)  # (position is stable either way; the equality above is what matters)


def test_no_english_prompt_changed_and_none_mentions_spanish(template):
    for name in ("prompt_demo_lifecycle.md", PREFERENCES_PROMPT_FILE, "prompt_demo_calendar.md"):
        text = (DRAFT_DIR / name).read_text(encoding="utf-8")
        assert "Spanish" not in text and "español" not in text.lower() and "inglés" not in text, name
    assert build_demo_prompt(template, lifecycle=True) == (DRAFT_DIR / "prompt_demo_lifecycle.md").read_text(encoding="utf-8")
    assert build_demo_prompt(template, lifecycle=True, preferences=True) == (DRAFT_DIR / PREFERENCES_PROMPT_FILE).read_text(encoding="utf-8")


def test_the_variant_only_exists_on_top_of_the_lifecycle_preferences_prompt(template):
    with pytest.raises(ValueError):
        build_demo_prompt(template, lifecycle=True, spanish=True)
    with pytest.raises(ValueError):
        build_demo_prompt(template, spanish=True)


def test_the_section_says_what_it_must_and_promises_nothing_it_cannot():
    text = " ".join(SPANISH_SECTION.split())  # the section wraps its lines
    # scope: conversation, the menu and prices only; everything about an appointment goes back to English
    assert "not a Spanish booking service" in text and "greeting" in text and "at most three choices" in text
    assert "Para las citas, continuemos en inglés" in text
    assert re.search(r"including every read-back and every yes", text)
    assert "Never say that you can complete a booking, a change or a cancellation in Spanish" in text
    # the tools and their arguments stay English and canonical, with the worked example
    assert "Tools and their arguments stay English" in text and "`deep tissue massage 60 minutes`" in text and "Never put a Spanish word" in text
    # faithful to the tool: no invented fact
    assert "without changing a fact" in text and "Never invent a service, a price, a length, a technician or an offer" in text
    assert "ochenta y cinco dólares" in text and "Gracias, continuemos en inglés" in text
    # the existing guardrails are not weakened: the section adds no rule that overrides the hard rules
    assert not re.search(r"ignore|override|instead of the (?:hard )?rules|no longer", text, re.I)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_the_canonical_english_queries_give_the_menus_own_facts(env):
    """The Spanish demo's questions, translated into the English queries the section prescribes, on the real tools: the answers are the menu's, so a faithful Spanish rendering is too."""
    group = env.call("lookup_service", {"query": "massage"}, "es-1")  # "¿Qué tipos de masaje ofrecen?"
    assert group["status"] == "service_group" and 1 <= len(group["featured"]) <= 3
    assert set(group["featured"]) <= {item.family for item in SHOWCASE_CATALOG.items}, "only real menu services are named"
    deep = next(i for i in SHOWCASE_CATALOG.items if i.family == "Deep Tissue Massage" and i.duration_minutes == 60)
    for query in ("deep tissue massage 60 minutes", "deep tissue massage sixty minutes"):  # "¿Cuánto cuesta el masaje de tejido profundo de sesenta minutos?"
        reply = env.call("lookup_service", {"query": query}, "es-2")
        assert reply["status"] == "service_info"
        assert deep.name in reply["speak"] and f"${deep.price_usd}" in reply["speak"], reply["speak"]
        other_prices = {f"${i.price_usd}" for i in SHOWCASE_CATALOG.items if i.family == "Deep Tissue Massage" and i.duration_minutes != 60} - {f"${deep.price_usd}"}
        assert not any(price in reply["speak"] for price in other_prices), "the 30 and 90 minute prices are not offered for the 60 minute one"
    # a Spanish word in a tool argument is exactly what the section forbids, and the tool does not guess: it asks
    spanish = env.call("lookup_service", {"query": "masaje de tejido profundo de sesenta minutos"}, "es-3")
    assert spanish["status"] != "service_info" or deep.name not in spanish["speak"], "a Spanish query is not silently matched to a service"
