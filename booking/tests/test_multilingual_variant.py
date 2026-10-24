"""The multilingual (English, Spanish, Mandarin Chinese) prompt variant that the owner applied to the owned test agent on 2026-10-22 at the user's explicit request, kept in source.

The generated file is rebuilt from the builder, and -- with the standard session-clock section the operator adds to every prompt -- must have EXACTLY the sha256 recorded when it was applied
(line endings normalised to LF, as the platform stores them). So the source provably equals what is live. The English prompts are untouched; the variant is the English
lifecycle-with-preferences prompt plus two changes (one widened sentence, one section). These tests cannot show that speech recognition, spoken Spanish or Mandarin, or faithful translation of
treatment words work: that needs supervised calls. No network, no Retell, no paid anything."""

from __future__ import annotations

import hashlib

import pytest

from aria_booking.catalog.render import (
    DRAFT_DIR, LIFECYCLE_TEMPLATE_FILE, MULTILINGUAL_PROMPT_FILE, MULTILINGUAL_SECTION, MULTILINGUAL_SENTENCE, PREFERENCES_PROMPT_FILE, SPANISH_PROMPT_FILE, build_demo_prompt,
)
from aria_booking.demo.agent_ops import CLOCK_SECTION

APPLIED_SHA256 = "f73eac682acd49828000ab234a912222a4a4bfbd56ef60628f2be664a25c5a69"  # recorded by the owner when the prompt was applied and read back (2026-10-22)


@pytest.fixture(scope="module")
def template():
    return (DRAFT_DIR / LIFECYCLE_TEMPLATE_FILE).read_text(encoding="utf-8")


def sent(text: str) -> str:
    """What the agent is sent: from the first section heading on, plus the operator's session-clock section."""
    return text[text.index("## Who you are"):] + CLOCK_SECTION


def test_the_generated_file_is_exactly_what_the_builder_makes(template):
    written = (DRAFT_DIR / MULTILINGUAL_PROMPT_FILE).read_text(encoding="utf-8")  # universal newlines: a Windows checkout may store CRLF, the digests are of the LF text
    assert written == build_demo_prompt(template, lifecycle=True, preferences=True, multilingual=True), "run: python -m aria_booking.catalog.render --write"


def test_the_prompt_sent_has_exactly_the_digest_recorded_when_it_was_applied():
    written = (DRAFT_DIR / MULTILINGUAL_PROMPT_FILE).read_text(encoding="utf-8")  # universal newlines: a Windows checkout may store CRLF, the digests are of the LF text
    assert hashlib.sha256(sent(written).encode("utf-8")).hexdigest() == APPLIED_SHA256


def test_the_variant_is_the_english_preferences_prompt_with_two_changes_and_nothing_else(template):
    english = build_demo_prompt(template, lifecycle=True, preferences=True)
    variant = build_demo_prompt(template, lifecycle=True, preferences=True, multilingual=True)
    body = variant[variant.index("## Who you are"):]
    assert body.count(MULTILINGUAL_SECTION) == 1
    reverted = body.replace(MULTILINGUAL_SECTION, "")
    new, old = MULTILINGUAL_SENTENCE
    assert reverted.count(", " + new + ". One natural conversation") == 1
    reverted = reverted.replace(", " + new + ". One natural conversation", old)
    assert reverted == english[english.index("## Who you are"):], "undo the two changes and the English prompt comes back, byte for byte"
    assert variant.index(MULTILINGUAL_SECTION) < variant.index("## Your opening line") < variant.index("## Two sources, never mixed"), "the section sits before the opening line"


def test_no_english_prompt_changed_and_the_narrower_spanish_variant_is_still_the_menu_only_one(template):
    assert build_demo_prompt(template, lifecycle=True, preferences=True) == (DRAFT_DIR / PREFERENCES_PROMPT_FILE).read_text(encoding="utf-8")
    assert build_demo_prompt(template, lifecycle=True, preferences=True, spanish=True) == (DRAFT_DIR / SPANISH_PROMPT_FILE).read_text(encoding="utf-8")
    for name in ("prompt_demo_lifecycle.md", PREFERENCES_PROMPT_FILE, "prompt_demo_calendar.md"):
        text = (DRAFT_DIR / name).read_text(encoding="utf-8")
        assert "Mandarin" not in text and "Conversation languages" not in text, name


def test_the_variant_only_exists_on_top_of_the_lifecycle_preferences_prompt_and_not_with_the_menu_only_one(template):
    with pytest.raises(ValueError):
        build_demo_prompt(template, lifecycle=True, multilingual=True)
    with pytest.raises(ValueError):
        build_demo_prompt(template, multilingual=True)
    with pytest.raises(ValueError):
        build_demo_prompt(template, lifecycle=True, preferences=True, multilingual=True, spanish=True)


def test_the_section_keeps_every_guardrail_in_force_in_every_language():
    text = " ".join(MULTILINGUAL_SECTION.split())
    assert "All existing calendar, identity, availability, and confirmation rules below remain in force in every language" in text
    assert "Language switching never creates a booking or confirms a pending action" in text
    # tool arguments stay English, translated faithfully, never choosing a treatment the caller did not
    assert "faithfully translate the caller's treatment words into English" in text and "never Swedish or deep tissue" in text
    assert "deep tissue massage 60 minutes" in text and "If the meaning is uncertain, ask rather than choosing a treatment" in text
    # names, digits, tokens, ids and fields are never translated or invented
    assert "Keep client and technician names, phone digits, references, service IDs, confirmation tokens, booleans, and tool field names unchanged" in text
    assert "Do not translate or invent a person's name" in text
    # an exact read-back and an explicit affirmative to it; hesitation and a language request are not confirmation
    assert "Only an explicit affirmative to that exact read-back" in text and "A language request, a hesitant answer, or changed details is not confirmation" in text
    assert "Never say an action succeeded without its verified tool success response" in text
    # no silent switching or silent changes
    assert "do not silently switch languages or silently change treatment, identity, or appointment details" in text
    assert "Do not describe yourself as English-only" in text
