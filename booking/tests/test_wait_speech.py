"""While the real calendar lookup runs, Aria says ONE truthful acknowledgement (user's direct clarification, relayed by Sol). Not the September
'um'/'uh' fillers: no time estimate, no loop of waiting messages, and nothing that implies a finished or failed check is still going on.

These are checks on the text the model is given (tool config + prompt) and on the server's own words. The model's actual speech can only be
confirmed in a supervised call. No Booksy, no Retell.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from aria_booking.catalog.render import GENERATED, build_availability_tools
from fakes import FakeMultiCalendar
from test_speed import CALL, DAY, SID, ask, make

DRAFT = Path(__file__).resolve().parent.parent / "voice_agent_draft"
SOURCE = json.loads((DRAFT / "tools.json").read_text(encoding="utf-8"))
AVAILABILITY = json.loads((DRAFT / "tools_availability_only.json").read_text(encoding="utf-8"))
PROMPTS = {name: (DRAFT / filename).read_text(encoding="utf-8") for name, filename in GENERATED.items()}
WAITING = ("check_slot", "find_alternatives")


def tool(config, name):
    return next(t for t in config["tools"] if t["name"] == name)


# ---------------------------------------------------------------- the tool configuration


@pytest.mark.parametrize("config", [SOURCE, AVAILABILITY], ids=["booking-test file", "availability-only file"])
@pytest.mark.parametrize("name", WAITING)
def test_the_calendar_tools_speak_once_when_they_start(config, name):
    t = tool(config, name)
    assert t["speak_during_execution"] is True
    message = t["execution_message_description"]
    assert "ONE short sentence, once" in message and "One moment while I" in message
    assert "availability" in message or "other times" in message


@pytest.mark.parametrize("name", WAITING)
def test_the_acknowledgement_names_a_technician_or_service_only_when_it_is_known(name):
    message = tool(AVAILABILITY, name)["execution_message_description"]
    assert "technician" in message and "Never guess a name" in message
    if name == "check_slot":
        assert "only the service is known" in message and "otherwise 'One moment while I check availability.'" in message


@pytest.mark.parametrize("name", WAITING)
def test_the_acknowledgement_gives_no_estimate_and_no_loop_and_no_filler(name):
    message = tool(AVAILABILITY, name)["execution_message_description"].lower()
    for estimated in ("few seconds", "a minute", "a second", "shortly", "quickly", "won't be long", "this may take", "it may take"):
        assert estimated not in message, estimated
    assert not re.search(r"\d", message), "no number of seconds"
    assert "no estimate" in message and "do not repeat or extend it" in message and "no filler" in message


def test_the_local_lookup_stays_silent_because_it_answers_at_once():
    assert tool(AVAILABILITY, "lookup_service")["speak_during_execution"] is False


def test_the_availability_file_is_still_exactly_what_the_generator_produces_from_the_source():
    assert build_availability_tools(SOURCE) == AVAILABILITY


def test_normal_interruption_behaviour_is_left_alone():
    for config in (SOURCE, AVAILABILITY):
        for t in config["tools"]:
            assert not any("interrupt" in key.lower() for key in t), "no field here changes how Retell handles a caller who speaks"


def test_the_acknowledgement_does_not_add_a_calendar_call():
    for name in WAITING:
        t = tool(AVAILABILITY, name)
        assert t["max_retry"] == 0, "waiting speech is spoken by the platform while one request runs; it never repeats the request"


# ---------------------------------------------------------------- the generated prompts


@pytest.mark.parametrize("mode", sorted(PROMPTS))
def test_both_generated_prompts_carry_the_waiting_rules(mode):
    text = PROMPTS[mode]
    assert "## While the calendar is being checked" in text
    section = text.split("## While the calendar is being checked")[1].split("## Hard rules")[0]
    assert "**one** short, calm sentence, **once**" in section and "One moment while I check" in section
    assert "only if the caller has already given it" in section
    assert "**no** estimate of how long it will take" in section and "Do not repeat or extend the sentence" in section
    assert "stop and listen as normal" in section, "a caller who speaks is not talked over"
    assert "never call a tool again just to fill the time" in section
    assert "Never say you are \"still checking\"" in section and "after the result has come back" in section


@pytest.mark.parametrize("mode", sorted(PROMPTS))
def test_the_prompt_forbids_the_september_fillers_while_allowing_the_status_sentence(mode):
    section = PROMPTS[mode].split("## While the calendar is being checked")[1].split("## Hard rules")[0]
    for filler in ("um", "uh", "hmm", "let me see", "give me a sec"):
        assert f'"{filler}"' in section, filler
    assert "status update, not filler" in section


def test_the_availability_prompt_tells_the_model_to_say_it_could_not_confirm_after_a_failure():
    text = PROMPTS["availability"] if "availability" in PROMPTS else next(iter(PROMPTS.values()))
    assert "say you could not confirm it; never guess and never say you are still checking" in text


def test_the_availability_prompt_no_longer_claims_nothing_exists_after_a_one_day_search():
    text = next(v for k, v in PROMPTS.items() if k != "booking")
    assert "say there is nothing in the next few days" not in text
    assert "including how far it looked" in text and "Never say there is nothing at all" in text


def test_opening_hours_and_a_technicians_working_hours_stay_distinct():
    for text in PROMPTS.values():
        assert "you may tell the caller the salon's opening hours when" in text and "no calendar check" in text
        assert "a particular technician's own working hours and days come only from the calendar tools" in text


def test_general_service_questions_still_need_no_calendar_call():
    for text in PROMPTS.values():
        assert "No tool is needed for these questions, and you must not check the calendar" in text


# ---------------------------------------------------------------- the server never talks as if a finished check were still running


FORBIDDEN_ONGOING = ("still checking", "still working", "one moment", "hold on", "bear with me", "just a moment", "give me a moment", "taking longer")


def test_the_servers_own_words_never_imply_a_finished_or_failed_check_is_still_going(tmp_path):
    import threading

    from test_readonly_hardening import hanging

    replies = []
    tools, calendar = make(tmp_path)
    replies.append(ask(tools))  # available
    calendar.read_failures = 1
    replies.append(ask(tools))  # unreadable page
    calendar.sign_in_required = True
    replies.append(ask(tools))  # signed out
    hung_tools, hung = hanging(tmp_path, read_deadline_seconds=0.2, lock_timeout_seconds=0.1)
    replies.append(ask(hung_tools))  # deadline missed
    replies.append(ask(hung_tools))  # browser still occupied by the abandoned read
    busy_tools, busy_calendar = hanging(tmp_path, read_deadline_seconds=5, lock_timeout_seconds=0.1)
    holder = threading.Thread(target=lambda: ask(busy_tools), daemon=True)
    holder.start()
    assert busy_calendar.started.wait(2)
    replies.append(ask(busy_tools))  # busy
    busy_calendar.release.set()
    hung.release.set()
    holder.join(3)
    statuses = {r["status"] for r in replies}
    assert {"available", "unknown", "system_unavailable", "busy"} <= statuses
    for reply in replies:
        spoken = reply["speak"].lower()
        for phrase in FORBIDDEN_ONGOING:
            assert phrase not in spoken, (reply["status"], phrase)


def test_a_failed_result_says_it_could_not_be_confirmed(tmp_path):
    from test_readonly_hardening import hanging

    tools, hung = hanging(tmp_path, read_deadline_seconds=0.2)
    reply = ask(tools)
    assert reply["status"] == "unknown" and "can't confirm" in reply["speak"] and "didn't respond in time" in reply["speak"]
    hung.release.set()


def test_the_server_adds_no_waiting_speech_of_its_own(tmp_path):
    source = (Path(__file__).resolve().parent.parent / "aria_booking" / "voice" / "tools.py").read_text(encoding="utf-8").lower()
    for phrase in FORBIDDEN_ONGOING:
        assert phrase not in source, phrase
