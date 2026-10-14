"""Aria's approved opening: 'Good morning/afternoon/evening! This is Aria at Serenity Spa. ...', the word chosen per call from the spa's LOCAL hour.

Retell fills ``{{current_hour_<zone>}}`` in per call, and an opening message cannot branch on a variable, so the agent's opening message is left UNSET and the model speaks
first from the prompt. Offline we prove the prompt, the unset message in create/update/read-back and the hour-to-word rule; that the model follows it is provable only in a call.
"""

from __future__ import annotations

import re

import pytest

from aria_booking.demo import AFTERNOON_UNTIL, MORNING_UNTIL, SALON_NAME, SPA_TIMEZONE, greeting_word
from aria_booking.demo.agent_ops import CLOCK_SECTION, OperatorConfig, OperatorError, State, expected_prompt, load_config

from test_demo_agent_ops import FakeSecrets, api, config, create
from test_demo_prompt import PROMPT

SECTION = PROMPT.split("## Your opening line")[1].split("## Two sources, never mixed")[0]
WORDING = (f"<Greeting>! This is Aria at {SALON_NAME}. I'd love to help you get some relaxation on the calendar. "
           "To get started, are you looking for a massage, a facial, or maybe a full spa day?")


# ---------------------------------------------------------------- the hour rule


@pytest.mark.parametrize("hour,word", [(0, "Good morning"), (5.5, "Good morning"), (11.99, "Good morning"), (12, "Good afternoon"), (16.99, "Good afternoon"),
                                       (17, "Good evening"), (20.5, "Good evening"), (23.99, "Good evening")])
def test_the_greeting_word_follows_the_local_hour(hour, word):
    assert greeting_word(hour) == word


def test_the_prompt_states_exactly_the_boundaries_the_code_uses():
    variable = "{{current_hour_" + SPA_TIMEZONE + "}}"
    assert (MORNING_UNTIL, AFTERNOON_UNTIL) == (12, 17)
    assert f'"Good morning" when {variable} is below {MORNING_UNTIL}' in SECTION
    assert f'"Good afternoon" from {MORNING_UNTIL} up to (not including) {AFTERNOON_UNTIL}' in SECTION and f'"Good evening" from {AFTERNOON_UNTIL} on' in SECTION
    assert "never another time zone and never a time you assumed" in SECTION


def test_the_variable_is_per_call_not_a_value_frozen_into_the_file():
    assert not re.search(r"Good (morning|afternoon|evening)! This is Aria", PROMPT), "no fixed greeting is written into the prompt"
    assert SECTION.count("{{current_hour_" + SPA_TIMEZONE + "}}") == 2
    assert "<Greeting>" in SECTION


def test_every_retell_variable_in_the_agent_prompt_is_one_of_the_two_known_clock_variables_for_the_one_spa_timezone():
    variables = set(re.findall(r"\{\{([^}]+)\}\}", expected_prompt()))
    assert variables == {f"current_hour_{SPA_TIMEZONE}", f"current_time_{SPA_TIMEZONE}"}
    assert "{{current_time_" + SPA_TIMEZONE + "}}" in CLOCK_SECTION


# ---------------------------------------------------------------- the approved wording and delivery


def test_the_approved_sentences_are_in_the_prompt_unchanged():
    quoted = [line for line in SECTION.splitlines() if line.startswith("> ")]
    assert quoted == ["> " + WORDING]


def test_no_hello_no_ai_receptionist_and_truthful_if_asked():
    assert 'Do not begin with "Hello" or "Hi"' in SECTION and "do not call yourself an AI receptionist in the opening" in SECTION
    assert "If a caller later asks directly whether you are an AI or an automated assistant, answer truthfully" in SECTION
    assert "Hello" not in WORDING and "receptionist" not in WORDING and "AI" not in WORDING.split()


def test_the_delivery_is_warm_upbeat_and_unhurried_without_changing_the_voice():
    assert "warmly and with a bright, upbeat, smiling lift" in SECTION and "conversational and unhurried" in SECTION
    assert "voice_id" not in SECTION and "retell-" not in SECTION, "the delivery is guidance in the prompt; the agent's voice setting is not touched here"


def test_a_full_spa_day_is_an_invitation_not_a_package():
    assert '"A full spa day" is only an invitation to talk about services' in SECTION
    assert "Do not describe, price or promise a package you do not find in the services below" in SECTION


def test_the_opening_names_the_one_salon_name_and_the_demo_is_not_announced():
    assert SALON_NAME == "Serenity Spa" and f"This is Aria at {SALON_NAME}" in SECTION
    from aria_booking.showcase.leaks import find_leaks

    assert not any(word in SECTION.lower() for word in ("demo", "simulat", "prototype")) and find_leaks(SECTION) == []


def test_the_opening_comes_before_everything_else_the_agent_is_told_and_appears_once():
    assert PROMPT.count("## Your opening line") == 1
    assert PROMPT.index("## Who you are") < PROMPT.index("## Your opening line") < PROMPT.index("## Two sources, never mixed")


# ---------------------------------------------------------------- the opening message is UNSET in Retell


def test_the_default_opening_message_is_unset_so_the_model_speaks_first_from_the_prompt():
    assert OperatorConfig().begin_message is None and load_config({}).begin_message is None


def test_create_sends_no_opening_message_when_it_is_unset(tmp_path):
    code, fake, lines = create(tmp_path)
    create_body = next(c[2] for c in fake.calls if c[1] == "create-retell-llm")
    assert code == 0 and "begin_message" not in create_body and "begin_message" not in fake.llms["llm_1"]
    assert "Binding:" in "\n".join(lines)


def test_a_fixed_opening_message_can_still_be_configured_and_is_sent_and_read_back(tmp_path):
    code, fake, _ = create(tmp_path, begin_message="Welcome to the test line.")
    assert code == 0 and fake.llms["llm_1"]["begin_message"] == "Welcome to the test line."
    fake.llms["llm_1"].pop("begin_message")
    with pytest.raises(OperatorError, match="the opening line"):
        from aria_booking.demo import agent_ops

        agent_ops.verify(config(tmp_path, begin_message="Welcome to the test line."), FakeSecrets(), api(fake), lambda m: None)


def test_the_read_back_refuses_an_opening_message_the_account_has_when_none_should_exist(tmp_path):
    from aria_booking.demo import agent_ops

    _code, fake, _ = create(tmp_path)
    fake.llms["llm_1"]["begin_message"] = "Hi there, thanks for calling."
    with pytest.raises(OperatorError, match="the opening line"):
        agent_ops.verify(config(tmp_path), FakeSecrets(), api(fake), lambda m: None)
    fake.llms["llm_1"]["begin_message"] = ""  # an empty message makes the agent WAIT for the caller: also not the same as unset
    with pytest.raises(OperatorError, match="the opening line"):
        agent_ops.verify(config(tmp_path), FakeSecrets(), api(fake), lambda m: None)


def test_update_clears_an_old_fixed_message_with_an_explicit_null_and_the_state_records_it(tmp_path):
    from aria_booking.demo import agent_ops

    _code, fake, _ = create(tmp_path, begin_message="An old fixed greeting.")
    assert agent_ops.update(OperatorConfig(tunnel_url="https://demo-tunnel.example", state_dir=tmp_path / "state"), FakeSecrets(), api(fake), lambda m: None, confirm=True) == 0
    sent = [c for c in fake.calls if c[0] == "PATCH"][0][2]
    assert sent["begin_message"] is None and fake.llms["llm_1"]["begin_message"] is None
    assert State(tmp_path / "state").exists("configuration-verified.json")


def test_plan_says_how_the_agent_opens(tmp_path):
    from aria_booking.demo import agent_ops
    from aria_booking.demo.secret_store import EnvSecrets

    lines = []
    agent_ops.plan(OperatorConfig(state_dir=tmp_path / "s"), EnvSecrets({}), lines.append)
    assert any("opening" in line and "unset: the agent speaks first from the prompt" in line and SPA_TIMEZONE in line for line in lines)
    fixed = []
    agent_ops.plan(OperatorConfig(state_dir=tmp_path / "s", begin_message="Hi."), EnvSecrets({}), fixed.append)
    assert any("fixed message: Hi." in line for line in fixed)
