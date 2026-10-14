"""'What kinds of massage do you have?': for EVERY service group, a few featured choices, ONE simple question, a wait, and only then ONE recommendation from the menu.

Two layers, both tested: the generated prompt carries the rule and the featured list for each group, and `lookup_service` enforces the shape in code (a bare group word gets at most three
examples and the one question), so the behaviour does not depend on the model remembering. Nothing here ranks anything or invents a treatment.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aria_booking.appointments.launch import build_lifecycle_demo
from aria_booking.appointments.lifecycle import open_lifecycle
from aria_booking.catalog.render import CATEGORY_TITLES, DEMO_PROMPT_FILE, LIFECYCLE_PROMPT_FILE, build_demo_prompt
from aria_booking.demo.launch import build_demo
from aria_booking.showcase.catalog import SHOWCASE_CATALOG
from aria_booking.showcase.discovery import FEATURED, FEATURED_LIMIT, QUESTIONS, discovery_section, families, featured, group_of, make_group_answer

DRAFT = Path(__file__).resolve().parent.parent / "voice_agent_draft"
TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 14)
CATEGORIES = [c for c in CATEGORY_TITLES if families(SHOWCASE_CATALOG, c)]


def flat(text: str) -> str:
    return " ".join(text.split())


def tools():
    endpoint, _driver, _dataset = build_demo("k", today=TODAY, real_now=lambda: datetime(2026, 10, 14, 12, tzinfo=TZ))
    return endpoint._tools


# ---------------------------------------------------------------- the data: every group, a few real choices, one question


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_group_on_the_menu_has_at_most_three_real_featured_choices_and_one_question(category):
    names = featured(SHOWCASE_CATALOG, category)
    assert 1 <= len(names) <= FEATURED_LIMIT and set(names) <= set(families(SHOWCASE_CATALOG, category))
    assert category in QUESTIONS and QUESTIONS[category].endswith("?") and QUESTIONS[category].count("?") == 1, "ONE simple question"


def test_the_featured_examples_are_the_first_three_on_the_menu_unless_a_group_is_configured_and_a_bad_configuration_is_refused():
    assert featured(SHOWCASE_CATALOG, "lash") == families(SHOWCASE_CATALOG, "lash")[:3]
    assert featured(SHOWCASE_CATALOG, "massage") == list(FEATURED["massage"]) and set(FEATURED) <= set(CATEGORIES)
    assert featured(SHOWCASE_CATALOG, "waxing", {"waxing": ("Lip",)}) == ["Lip"]
    with pytest.raises(ValueError, match="not on the menu"):
        featured(SHOWCASE_CATALOG, "massage", {"massage": ("Swedish Massage", "Thai Massage")})
    with pytest.raises(ValueError, match="one to 3"):
        featured(SHOWCASE_CATALOG, "massage", {"massage": ("Swedish Massage", "Deep Tissue Massage", "Hot Stone Massage", "Prenatal Massage")})


def test_the_questions_for_massage_and_facials_are_the_ones_asked_for():
    assert QUESTIONS["massage"] == "Are you looking to relax, ease everyday muscle tension, or focus on something else?"
    assert QUESTIONS["facial"].startswith("Are you thinking about hydration, brightness, or the appearance of fine lines")


def test_a_group_without_a_question_stops_the_build_instead_of_going_silent(monkeypatch):
    monkeypatch.delitem(QUESTIONS, "lash")
    with pytest.raises(ValueError, match="no discovery question"):
        discovery_section(SHOWCASE_CATALOG, CATEGORY_TITLES)


# ---------------------------------------------------------------- the prompt carries it, in both demo prompts and no other


def test_both_demo_prompts_carry_the_generated_section_once_before_the_tool_waiting_rules_and_the_other_prompts_do_not():
    section = discovery_section(SHOWCASE_CATALOG, CATEGORY_TITLES)
    for name in (DEMO_PROMPT_FILE, LIFECYCLE_PROMPT_FILE):
        prompt = (DRAFT / name).read_text(encoding="utf-8")
        assert prompt.count(section) == 1 and prompt.index(section) < prompt.index("## While "), name
    for name in ("prompt_test_only.md", "prompt_availability_only.md"):
        assert "When a caller asks what kinds of a treatment you have" not in (DRAFT / name).read_text(encoding="utf-8"), name


def test_the_section_states_every_rule_that_was_asked_for():
    text = flat(discovery_section(SHOWCASE_CATALOG, CATEGORY_TITLES))
    for phrase in (
        "Name AT MOST THREE choices", "then STOP", "Ask the ONE question given for that group, and WAIT for the answer", "Do not read out the whole group",
        "recommend ONE listed treatment that fits what they said", "Offer other choices, or the full list, ONLY if the caller asks for more",
        "do not ask the question again: recommend straight away", "The featured choices are examples, not a ranking",
        "never diagnose, promise a result, say a treatment suits a medical or skin condition", "keep the treatment, its length and any technician they chose",
    ):
        assert phrase in text, phrase
    popularity = re.findall(r"popular|favourite|best seller|recommended by other customers", text)
    assert len(popularity) == 4 and "Never call a treatment popular, a favourite, a best seller or recommended by other customers" in text, "only ever named in the prohibition"


@pytest.mark.parametrize("category", CATEGORIES)
def test_the_prompt_lists_each_groups_featured_choices_and_question(category):
    prompt = flat((DRAFT / DEMO_PROMPT_FILE).read_text(encoding="utf-8"))
    line = f"- {CATEGORY_TITLES[category]}: {', '.join(featured(SHOWCASE_CATALOG, category))}. Ask: \"{QUESTIONS[category]}\""
    assert line in prompt


def test_the_prompt_never_names_a_service_the_menu_does_not_have():
    section = discovery_section(SHOWCASE_CATALOG, CATEGORY_TITLES)
    listed = {name for line in section.splitlines() if line.startswith("- ") and ". Ask:" in line for name in line.split(": ", 1)[1].split(". Ask:")[0].split(", ")}
    assert listed and listed <= {item.family for item in SHOWCASE_CATALOG.items}


# ---------------------------------------------------------------- the code enforces the shape, for every group


@pytest.mark.parametrize("word,category", [
    ("massage", "massage"), ("massages", "massage"), ("facials", "facial"), ("what kinds of facials do you have", "facial"), ("Do you have any waxing?", "waxing"),
    ("lashes", "lash"), ("eyelashes and brows", "lash"), ("head spa", "head-spa"), ("scalp treatments", "head-spa"), ("body scrubs", "body-scrub"), ("combos", "package"),
    ("couples", "couples"), ("what are your bundles", "membership"), ("Which types of massage are available?", "massage"),
])
def test_a_bare_group_word_is_recognised_in_whatever_sentence_it_comes(word, category):
    assert group_of(word) == category


@pytest.mark.parametrize("specific", ["swedish massage", "deep tissue massage 60 minutes", "couples massage", "signature facial", "microdermabrasion", "full leg wax", "eyelash lifting", "something relaxing", "", "   "])
def test_a_specific_treatment_or_a_vague_wish_is_never_taken_for_a_group(specific):
    assert group_of(specific) is None
    assert make_group_answer(SHOWCASE_CATALOG, CATEGORY_TITLES)(specific) is None


@pytest.mark.parametrize("category", CATEGORIES)
def test_lookup_service_answers_every_group_with_at_most_three_examples_and_the_one_question_and_nothing_else(category):
    word = GROUP_QUERY[category]
    reply = tools().lookup_service("call", {"query": word})
    assert reply["status"] == "service_group" and reply["category"] == category and reply["featured"] == featured(SHOWCASE_CATALOG, category)
    speak = reply["speak"]
    assert speak.endswith(QUESTIONS[category]) and speak.count("?") == 1, speak
    assert all(name in speak for name in reply["featured"]) and len(reply["featured"]) <= 3
    others = [f for f in families(SHOWCASE_CATALOG, category) if f not in reply["featured"]]
    assert not any(other in speak for other in others), "none of the other kinds is read out"
    assert f"We have {reply['total_kinds']} kinds of" in speak and reply["total_kinds"] == len(families(SHOWCASE_CATALOG, category))
    assert not re.search(r"popular|favourite|best seller|most booked|customers love", speak, re.I)


GROUP_QUERY = {"massage": "massage", "facial": "what kinds of facials do you have", "package": "combos", "body-scrub": "body scrubs", "lash": "lashes", "head-spa": "head spa", "waxing": "waxing",
               "couples": "couples", "membership": "bundles"}


def test_specific_treatments_still_get_their_own_lengths_and_prices_and_the_live_line_is_untouched():
    t = tools()
    assert t.lookup_service("c", {"query": "swedish massage"})["status"] == "choose_variant"
    assert t.lookup_service("c", {"query": "microdermabrasion"})["status"] == "service_info"
    import inspect

    from aria_booking.voice.tools import VoiceTools  # the reviewed live tools have no group hook unless a demo wires one

    assert inspect.signature(VoiceTools.__init__).parameters["group_answer"].default is None


def test_the_lifecycle_endpoint_answers_a_group_the_same_way(tmp_path):
    lc = open_lifecycle(tmp_path / "d.sqlite3", today=TODAY, real_now=lambda: datetime(2026, 10, 14, 12, tzinfo=TZ), seed=False)
    endpoint, _driver = build_lifecycle_demo("k", lc)
    reply = endpoint._tools.lookup_service("c", {"query": "massage"})
    assert reply["status"] == "service_group" and reply["speak"].endswith(QUESTIONS["massage"])
    assert endpoint._tools.lookup_service("c", {"query": "swedish massage"})["status"] == "choose_variant"


def test_after_the_caller_answers_the_menu_descriptions_the_recommendation_rests_on_are_in_the_prompt():
    prompt = (DRAFT / DEMO_PROMPT_FILE).read_text(encoding="utf-8")
    for family in ("Swedish Massage", "Deep Tissue Massage", "Hot Stone Massage", "Deep Hydration Facial", "Brightening Facial", "Lifting Facial"):
        item = next(i for i in SHOWCASE_CATALOG.items if i.family == family)
        assert item.description.split(".")[0] in prompt, family


def test_the_generated_prompts_still_match_the_generator():
    from aria_booking.catalog.render import LIFECYCLE_TEMPLATE_FILE

    assert (DRAFT / DEMO_PROMPT_FILE).read_text(encoding="utf-8") == build_demo_prompt((DRAFT / "prompt_template.md").read_text(encoding="utf-8"))
    assert (DRAFT / LIFECYCLE_PROMPT_FILE).read_text(encoding="utf-8") == build_demo_prompt((DRAFT / LIFECYCLE_TEMPLATE_FILE).read_text(encoding="utf-8"), lifecycle=True)


def test_the_live_tools_ignore_a_group_hook_unless_they_are_the_labelled_demo(tmp_path):
    from aria_booking.config import Config
    from aria_booking.voice.tools import VoiceTools

    hook = lambda query: {"status": "service_group", "speak": "hijacked"}
    kwargs = dict(cfg=Config(business_id="1234567"), driver=object(), service_factory=lambda: None, clock=lambda: datetime(2026, 10, 14, 12, tzinfo=TZ))
    live = VoiceTools(**kwargs, group_answer=hook)
    demo = VoiceTools(**kwargs, group_answer=hook, source_label="demo_calendar")
    assert live.lookup_service("c", {"query": "massage"})["status"] != "service_group"
    assert demo.lookup_service("c", {"query": "massage"})["status"] == "service_group"
