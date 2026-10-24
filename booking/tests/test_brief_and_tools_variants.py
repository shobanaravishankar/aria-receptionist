"""The second user-authorized live prompt (brief replies, no spoken prices; applied 2026-10-22 11:38) and the multilingual tool wording, kept in source and proved equal to what is live.

The brief prompt is rebuilt from the multilingual one by one inserted section and nine exact wording edits, and -- with the operator's session-clock section -- must have EXACTLY the sha256
recorded when it was applied (LF line endings). The multilingual tool file is the committed lifecycle-with-preferences tools plus appended wording only, and must reproduce, field for field,
the live descriptions the owner recorded. Nothing here shows how the agent SOUNDS: no spoken price, shortness or translation can be proven offline. No network, no Retell, no paid anything."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from aria_booking.catalog.render import (
    BREVITY_ANCHOR, BREVITY_EDITS, BREVITY_PROMPT_FILE, BREVITY_SECTION, DRAFT_DIR, LIFECYCLE_TEMPLATE_FILE, MULTILINGUAL_EXEC_SUFFIX, MULTILINGUAL_PARAM_SUFFIX,
    MULTILINGUAL_PARAM_TARGETS, MULTILINGUAL_PROMPT_FILE, MULTILINGUAL_TOOL_OVERRIDES_FILE, MULTILINGUAL_TOOLS_FILE, PREFERENCES_TOOLS_FILE, build_demo_prompt, build_multilingual_tools,
)
from aria_booking.demo.agent_ops import CLOCK_SECTION

BREVITY_APPLIED_SHA256 = "e73c9d832e224410dce4ce9eb687d78196f2f88079891d8947ab3610b8a3175d"  # recorded by the owner when the prompt was applied and read back (2026-10-22 11:38)


@pytest.fixture(scope="module")
def template():
    return (DRAFT_DIR / LIFECYCLE_TEMPLATE_FILE).read_text(encoding="utf-8")


def sent(text: str) -> str:
    return text[text.index("## Who you are"):] + CLOCK_SECTION


# ---------------------------------------------------------------- the brief, no-spoken-prices prompt


def test_the_generated_brief_prompt_is_exactly_what_the_builder_makes(template):
    written = (DRAFT_DIR / BREVITY_PROMPT_FILE).read_text(encoding="utf-8")  # universal newlines: a Windows checkout may store CRLF, the digests are of the LF text
    assert written == build_demo_prompt(template, lifecycle=True, preferences=True, multilingual=True, brief=True), "run: python -m aria_booking.catalog.render --write"


def test_the_brief_prompt_sent_has_exactly_the_digest_recorded_when_it_was_applied():
    written = (DRAFT_DIR / BREVITY_PROMPT_FILE).read_text(encoding="utf-8")  # universal newlines: a Windows checkout may store CRLF, the digests are of the LF text
    assert hashlib.sha256(sent(written).encode("utf-8")).hexdigest() == BREVITY_APPLIED_SHA256


def test_the_brief_prompt_is_the_multilingual_prompt_with_one_section_and_nine_edits_and_nothing_else(template):
    multilingual = build_demo_prompt(template, lifecycle=True, preferences=True, multilingual=True)
    brief = build_demo_prompt(template, lifecycle=True, preferences=True, multilingual=True, brief=True)
    body = brief[brief.index("## Who you are"):]
    assert body.count(BREVITY_SECTION) == 1 and len(BREVITY_EDITS) == 9
    reverted = body.replace(BREVITY_SECTION, "")
    for old, new in BREVITY_EDITS:
        assert reverted.count(new) == 1, new[:60]
        reverted = reverted.replace(new, old)
    assert reverted == multilingual[multilingual.index("## Who you are"):], "undo the section and the nine edits and the multilingual prompt comes back, byte for byte"
    assert body.index(BREVITY_SECTION) + len(BREVITY_SECTION) == body.index(BREVITY_ANCHOR), "the section sits directly before the languages section"


def test_the_brief_prompt_only_exists_on_top_of_the_multilingual_one(template):
    with pytest.raises(ValueError):
        build_demo_prompt(template, lifecycle=True, preferences=True, brief=True)
    with pytest.raises(ValueError):
        build_demo_prompt(template, lifecycle=True, brief=True, multilingual=True)


def test_the_brief_rules_say_no_prices_and_keep_every_confirmation():
    text = " ".join(BREVITY_SECTION.split())
    assert "Do not state prices, currency amounts, price ranges, or fees" in text and "prices are listed on the website" in text
    assert "Keep every essential booking or change confirmation" in text and "never omit a required identity clarification or confirmation, change tool arguments, or bypass an explicit yes" in text
    assert "Existing language switching, appointment safeguards, and successful-result requirements remain in force" in text
    assert "If unavailable or uncertain, give one brief truthful reason; do not hide uncertainty or claim availability without a tool check" in text


def test_the_other_prompts_are_untouched_by_the_variants(template):
    for name in ("prompt_demo_lifecycle.md", "prompt_demo_lifecycle_preferences.md", MULTILINGUAL_PROMPT_FILE):
        text = (DRAFT_DIR / name).read_text(encoding="utf-8")
        assert "Brief replies and no spoken prices" not in text, name
    assert hashlib.sha256(sent((DRAFT_DIR / MULTILINGUAL_PROMPT_FILE).read_text(encoding="utf-8")).encode()).hexdigest() == "f73eac682acd49828000ab234a912222a4a4bfbd56ef60628f2be664a25c5a69"


# ---------------------------------------------------------------- the multilingual tool wording


def load(name: str) -> dict:
    return json.loads((DRAFT_DIR / name).read_text(encoding="utf-8"))


def test_the_generated_tools_file_is_what_the_builder_makes():
    base = load(PREFERENCES_TOOLS_FILE)
    assert load(MULTILINGUAL_TOOLS_FILE) == build_multilingual_tools(base), "run: python -m aria_booking.catalog.render --write"


def test_the_tools_reproduce_the_live_descriptions_the_owner_recorded_field_for_field():
    generated = {t["name"]: t for t in load(MULTILINGUAL_TOOLS_FILE)["tools"]}
    recorded = json.loads((DRAFT_DIR / MULTILINGUAL_TOOL_OVERRIDES_FILE).read_text(encoding="utf-8"))
    assert set(recorded) - set(generated) == {"end_call"} and recorded["end_call"] == {"execution_message_description": None, "parameter_descriptions": {}}, "end_call is the platform's own tool; nothing to change"
    checked = 0
    for name, entry in recorded.items():
        if name not in generated:
            continue
        if entry["execution_message_description"] is not None:
            assert generated[name]["execution_message_description"] == entry["execution_message_description"], name
            checked += 1
        for parameter, text in entry["parameter_descriptions"].items():
            assert generated[name]["parameters"]["properties"][parameter]["description"] == text, (name, parameter)
            checked += 1
    assert checked == 10 + len(MULTILINGUAL_PARAM_TARGETS) == 19, checked  # ten waiting messages and nine treatment-word arguments


def test_only_descriptions_changed_and_nothing_else_about_any_tool():
    base, variant = load(PREFERENCES_TOOLS_FILE), load(MULTILINGUAL_TOOLS_FILE)
    assert [t["name"] for t in variant["tools"]] == [t["name"] for t in base["tools"]]

    def stripped(doc: dict) -> dict:
        doc = copy.deepcopy(doc)
        doc.pop("_variant", None)
        for tool in doc["tools"]:
            tool.pop("execution_message_description", None)
            for parameter in tool["parameters"]["properties"].values():
                parameter.pop("description", None)
        return doc

    assert stripped(variant) == stripped(base), "no name, address, schema, requirement, timeout or flag differs"
    for new, old in zip(variant["tools"], base["tools"]):
        if old.get("execution_message_description"):
            assert new["execution_message_description"] == old["execution_message_description"] + MULTILINGUAL_EXEC_SUFFIX, old["name"]
        assert new["description"] == old["description"], "the tool's own description is unchanged"
    changed = [(n["name"], p) for n, o in zip(variant["tools"], base["tools"]) for p, v in n["parameters"]["properties"].items() if v.get("description") != o["parameters"]["properties"][p].get("description")]
    assert changed == [tuple(t) for t in MULTILINGUAL_PARAM_TARGETS]
    for tool, parameter in MULTILINGUAL_PARAM_TARGETS:
        assert next(t for t in variant["tools"] if t["name"] == tool)["parameters"]["properties"][parameter]["description"].endswith(MULTILINGUAL_PARAM_SUFFIX)


def test_the_tool_wording_keeps_names_translation_free_and_never_invents_a_treatment():
    assert "Never translate person names or invent a treatment" in MULTILINGUAL_PARAM_SUFFIX and "preserving ambiguity and duration" in MULTILINGUAL_PARAM_SUFFIX
    assert "preserving all names and facts" in MULTILINGUAL_EXEC_SUFFIX
