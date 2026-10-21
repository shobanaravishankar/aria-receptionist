"""Preferred technicians as an OPT-IN mode (demo only): the generated prompt and tool file, the operator that holds an agent to them, and the preflight that proves the running server
has them. The lifecycle prompt, its tool file and its eight functions stay exactly as they were. Placeholders only; no account, no tunnel, no call."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

from aria_booking.appointments.launch import build_lifecycle_demo
from aria_booking.appointments.lifecycle import open_lifecycle
from aria_booking.appointments.preference_tools import PREFERENCE_ROUTES
from aria_booking.appointments.preferences import PreferenceStore
from aria_booking.appointments.seed import ANSEL, WILLA
from aria_booking.appointments.tool_defs import PREFERENCE_TOOL_NAMES, PREFERENCE_TOOLS
from aria_booking.appointments.tools import LIFECYCLE_ROUTES
from aria_booking.catalog.render import (
    AVAILABILITY_TOOLS_FILE, LIFECYCLE_PROMPT_FILE, LIFECYCLE_TEMPLATE_FILE, LIFECYCLE_TOOLS_FILE, PREFERENCES_PROMPT_FILE, PREFERENCES_SECTION, PREFERENCES_TOOLS_FILE,
    build_demo_prompt, build_lifecycle_tools, build_preference_tools,
)
from aria_booking.demo import agent_ops
from aria_booking.demo.agent_ops import (
    LIFECYCLE_MODES, LIFECYCLE_TOOL_NAMES, MODES, OperatorConfig, OperatorError, State, _preference_cases, _preference_problems, expected_prompt, expected_tools, load_config,
)
from aria_booking.showcase.leaks import find_leaks
from aria_booking.showcase.roster import name_of
from aria_booking.voice.retell_http import make_http_server

from test_demo_agent_ops import KEY, PUBLIC, TOKEN, TUNNEL, FakeSecrets, api, config, create

DRAFT = Path(__file__).resolve().parent.parent / "voice_agent_draft"
SRC = Path(__file__).resolve().parent.parent / "aria_booking"
TEMPLATE = (DRAFT / LIFECYCLE_TEMPLATE_FILE).read_text(encoding="utf-8")
SHARED = (DRAFT / "prompt_template.md").read_text(encoding="utf-8")
LIFECYCLE_PROMPT = (DRAFT / LIFECYCLE_PROMPT_FILE).read_text(encoding="utf-8")
PROMPT = (DRAFT / PREFERENCES_PROMPT_FILE).read_text(encoding="utf-8")
LIFECYCLE_TOOLS = json.loads((DRAFT / LIFECYCLE_TOOLS_FILE).read_text(encoding="utf-8"))
TOOLS = json.loads((DRAFT / PREFERENCES_TOOLS_FILE).read_text(encoding="utf-8"))
MODE = "lifecycle-preferences"
SECTION = PREFERENCES_SECTION.replace("{TECH}", name_of("910006")).replace("{OTHER}", name_of("910007"))


def flat(text: str) -> str:
    return " ".join(text.split())


def body(text: str) -> str:
    return text[text.index("## Who you are"):]


# ---------------------------------------------------------------- generated, and the lifecycle files untouched


def test_the_committed_files_are_exactly_what_the_generator_produces():
    assert PROMPT == build_demo_prompt(TEMPLATE, lifecycle=True, preferences=True), "regenerate with: python -m aria_booking.catalog.render --write"
    assert TOOLS == build_preference_tools(LIFECYCLE_TOOLS)


def test_the_lifecycle_prompt_and_tool_file_are_unchanged_by_this_mode():
    assert LIFECYCLE_PROMPT == build_demo_prompt(TEMPLATE, lifecycle=True)
    availability = json.loads((DRAFT / AVAILABILITY_TOOLS_FILE).read_text(encoding="utf-8"))
    assert LIFECYCLE_TOOLS == build_lifecycle_tools(availability)
    assert "preferred technician" not in LIFECYCLE_PROMPT.lower() and not (PREFERENCE_TOOL_NAMES & {t["name"] for t in LIFECYCLE_TOOLS["tools"]})


def test_the_preferences_prompt_is_the_lifecycle_prompt_plus_exactly_one_section():
    assert body(PROMPT).count(SECTION) == 1
    assert body(PROMPT).replace(SECTION, "") == body(LIFECYCLE_PROMPT), "nothing else in the conversation changes"
    assert PROMPT.index("## Preferred technicians") < PROMPT.index("## While a tool is working") and PROMPT.count("## Preferred technicians") == 1


def test_preferred_technicians_belong_to_the_lifecycle_prompt_only():
    with pytest.raises(ValueError, match="lifecycle prompt only"):
        build_demo_prompt(SHARED, preferences=True)


def test_the_first_eight_definitions_are_the_lifecycle_ones_byte_for_byte_and_three_are_added():
    assert TOOLS["tools"][:8] == LIFECYCLE_TOOLS["tools"]
    assert [t["name"] for t in TOOLS["tools"][8:]] == ["get_preferences", "save_preference", "remove_preference"] and TOOLS["tools"][8:] == PREFERENCE_TOOLS
    assert "PREFERRED TECHNICIANS" in TOOLS["_status"] and "no function for the live Booksy calendar" in TOOLS["_status"] and "DRAFT FOR REVIEW" in TOOLS["_status"]


# ---------------------------------------------------------------- what the prompt tells Aria


def test_it_is_still_serenity_and_names_only_roster_technicians():
    assert not re.search(r"\{[A-Z]{2,}\}", PROMPT), "every placeholder is filled"
    assert find_leaks(body(PROMPT)) == [], "nothing of the earlier salon"
    assert name_of("910006") in SECTION and name_of("910007") in SECTION


def test_a_preference_is_saved_only_when_asked_and_never_inferred_or_suggested():
    text = flat(SECTION)
    assert "Only on request" in text and "only when the caller asks for it in their own words" in text
    assert "Never infer one from a booking or a past visit" in text and "never suggest saving one" in text
    assert "never save one because the caller chose a technician for a single appointment" in text


def test_every_change_is_read_back_and_confirmed_and_success_is_only_the_explicit_statuses():
    text = flat(SECTION)
    assert "saves nothing yet" in text and "only after a clear yes call it again with exactly the same details, the token and `confirmed: true`" in text
    assert "saved only on `preference_saved` or `preference_unchanged` with `ok: true`" in text and "removed only on `preference_removed` with `ok: true`" in text
    assert "If the caller says no, say that nothing was saved" in text and "`nothing_to_remove` means there was nothing saved" in text


def test_it_asks_about_a_group_and_about_an_unknown_or_ineligible_technician_and_never_guesses():
    text = flat(SECTION)
    assert "not a whole group such as \"massage\": ask which one" in text and "`needs_clarification`" in text
    assert "a technician who is unknown or does not do that service" in text and "give the technicians it names" in text


def test_a_saved_technician_is_offered_first_when_booking_but_only_with_the_callers_yes_and_never_as_a_promise_of_a_time():
    text = flat(SECTION)
    assert "did not name a technician, and you have their complete phone number, call `get_preferences`" in text and "`eligible: true`" in text
    assert "pass `staff` to `check_slot` only after the caller says yes" in text
    assert "If you do not have their number yet, carry on as normal and do this once you have it, before you read the booking back" in text
    assert "It never guarantees a time" in text and "leave the saved preference as it is" in text
    assert "book that person: it does not change the saved preference" in text
    assert "never changes an appointment that is already booked" in text


def test_it_does_not_announce_the_prototype_promise_a_follow_up_or_mention_another_clients_data():
    text = flat(SECTION).lower()
    for word in ("demo", "simulated", "prototype", "text message", "call you back", "notified", "booksy", "book_slot"):
        assert word not in text, word
    assert "never say another client's preferences" in text


# ---------------------------------------------------------------- the tool file matches the endpoint


def test_the_tool_file_defines_exactly_the_functions_the_preference_endpoint_serves():
    served = set(PREFERENCE_ROUTES.values())
    assert PREFERENCE_TOOL_NAMES == served - set(LIFECYCLE_ROUTES.values()) == {"get_preferences", "save_preference", "remove_preference"}
    assert {t["name"] for t in TOOLS["tools"]} == served and len(TOOLS["tools"]) == len(served) == 11
    for tool in TOOLS["tools"]:
        assert tool["url"] == f"https://<YOUR-TUNNEL-HOST>/tools/{tool['name']}" and f"/tools/{tool['name']}" in PREFERENCE_ROUTES and tool["method"] == "POST"
        assert tool["max_retry"] == 0 and tool["timeout_ms"] >= 5000 and tool["headers"]["Authorization"].startswith("Bearer <set in")
    assert "book_slot" not in json.dumps(TOOLS) and "/tools/book_slot" not in PREFERENCE_ROUTES


def test_every_argument_a_preference_definition_names_is_one_the_tools_read_and_the_required_ones_exist():
    source = "\n".join((SRC / part).read_text(encoding="utf-8") for part in ("appointments/tools.py", "appointments/preference_tools.py", "voice/tools.py", "history/lookup.py"))
    read = set(re.findall(r'args(?:\.get)?\(?\[?"([a-z_]+)"', source))
    by_name = {t["name"]: t for t in PREFERENCE_TOOLS}
    for tool in PREFERENCE_TOOLS:
        properties = tool["parameters"]["properties"]
        assert set(tool["parameters"]["required"]) <= set(properties), tool["name"]
        assert set(properties) <= read | {"confirmation_token", "confirmed"}, (tool["name"], sorted(set(properties) - read))
    assert by_name["get_preferences"]["parameters"]["required"] == ["phone"]
    assert by_name["save_preference"]["parameters"]["required"] == ["phone", "service", "staff"]
    assert by_name["remove_preference"]["parameters"]["required"] == ["phone", "service"]
    assert set(by_name["save_preference"]["parameters"]["properties"]) >= {"confirmation_token", "confirmed", "name"} and "staff" not in by_name["remove_preference"]["parameters"]["properties"]


def test_the_descriptions_say_what_each_function_changes_and_how_it_is_confirmed():
    by_name = {t["name"]: t["description"] for t in PREFERENCE_TOOLS}
    assert "changes nothing" in by_name["get_preferences"] and "never guarantees a time" in by_name["get_preferences"]
    for name in ("save_preference", "remove_preference"):
        text = by_name[name]
        assert "TWO steps" in text and "NOTHING" in text and "ok true" in text and "never changes an existing appointment" in text, name
        assert "ONLY when the caller asks" in text, name
    assert "or `preference_unchanged`" in by_name["save_preference"] and "`preference_removed`" in by_name["remove_preference"]
    properties = {p for tool in PREFERENCE_TOOLS for p in tool["parameters"]["properties"]}
    assert "whole group" in next(t for t in PREFERENCE_TOOLS if t["name"] == "save_preference")["parameters"]["properties"]["service"]["description"] and "staff" in properties


# ---------------------------------------------------------------- the operator holds an agent to this mode


def test_the_mode_is_a_third_reviewed_configuration_and_the_default_stays_read_only():
    assert OperatorConfig().mode == "availability" and sorted(MODES) == ["availability", "lifecycle", MODE]
    assert LIFECYCLE_MODES == {"lifecycle", MODE} and MODES[MODE]["names"] == LIFECYCLE_TOOL_NAMES | PREFERENCE_TOOL_NAMES == set(PREFERENCE_ROUTES.values())
    assert OperatorConfig(mode=MODE).mode == MODE and load_config({}, overrides={"mode": MODE}).mode == MODE
    assert MODES["lifecycle"]["names"] == LIFECYCLE_TOOL_NAMES, "the lifecycle mode is exactly the eight functions it was"


def test_the_expected_prompt_and_functions_for_the_mode_come_from_the_reviewed_files():
    prompt = expected_prompt(mode=MODE)
    assert "## Preferred technicians" in prompt and "## Preferred technicians" not in expected_prompt(mode="lifecycle") and "## Preferred technicians" not in expected_prompt()
    tools = expected_tools(TUNNEL, TOKEN, mode=MODE)
    custom = [t for t in tools if t["type"] == "custom"]
    assert sorted(t["name"] for t in custom) == sorted(MODES[MODE]["names"]) and len(custom) == 11 and tools[-1]["type"] == "end_call"
    assert all(t["url"] == f"{TUNNEL}/tools/{t['name']}" and t["headers"] == {"Authorization": "Bearer " + TOKEN} for t in custom) and "book_slot" not in str(tools)
    assert len(expected_tools(TUNNEL, TOKEN, mode="lifecycle")) == 9


def test_plan_names_the_mode_and_its_eleven_functions_and_starts_nothing(tmp_path):
    lines = []
    assert agent_ops.plan(config(tmp_path, mode=MODE), FakeSecrets(), lines.append) == 0
    text = "\n".join(lines)
    assert f"{MODE} (simulated appointment lifecycle with preferred technicians)" in text and "get_preferences" in text and "save_preference" in text and "remove_preference" in text
    assert "none reaches the live Booksy calendar" in text and KEY not in text and TOKEN not in text


def test_create_in_this_mode_sends_the_reviewed_prompt_and_eleven_functions_and_reads_back(tmp_path):
    code, fake, lines = create(tmp_path, mode=MODE)
    assert code == 0 and "all eleven lifecycle and preferred-technician functions verified" in "\n".join(lines) and "no call was started" in "\n".join(lines)
    llm = next(iter(fake.llms.values()))
    assert llm["general_prompt"] == expected_prompt(mode=MODE) and sorted(t["name"] for t in llm["general_tools"] if t["type"] == "custom") == sorted(MODES[MODE]["names"])
    state = State(tmp_path / "state")
    assert state.read("configuration-verified.json")["mode"] == MODE and state.read("agent.json")["mode"] == "simulated appointment lifecycle with preferred technicians"
    for path in (tmp_path / "state").glob("*"):
        assert KEY not in path.read_text(encoding="utf-8") and TOKEN not in path.read_text(encoding="utf-8")


def test_an_agent_that_was_not_updated_is_not_mistaken_for_one_that_has_the_preferences_and_the_other_way_round(tmp_path):
    secrets = FakeSecrets()
    _code, fake, _ = create(tmp_path / "a", mode="lifecycle")
    with pytest.raises(OperatorError, match="the prompt differs|the set of custom functions"):
        agent_ops.verify(config(tmp_path / "a", mode=MODE), secrets, api(fake, secrets), lambda m: None)
    _code, fake2, _ = create(tmp_path / "b", mode=MODE)
    with pytest.raises(OperatorError, match="the prompt differs|the set of custom functions"):
        agent_ops.verify(config(tmp_path / "b", mode="lifecycle"), secrets, api(fake2, secrets), lambda m: None)
    assert agent_ops.verify(config(tmp_path / "b", mode=MODE), secrets, api(fake2, secrets), lambda m: None) == 0


def test_verify_catches_a_missing_preference_function_a_booksy_function_and_a_changed_prompt(tmp_path):
    _code, fake, _ = create(tmp_path, mode=MODE)
    llm = next(iter(fake.llms.values()))
    good_tools, good_prompt = list(llm["general_tools"]), llm["general_prompt"]
    secrets, cfg = FakeSecrets(), config(tmp_path, mode=MODE)
    llm["general_tools"] = [t for t in good_tools if t.get("name") != "save_preference"]
    with pytest.raises(OperatorError, match="the set of custom functions"):
        agent_ops.verify(cfg, secrets, api(fake, secrets), lambda m: None)
    llm["general_tools"] = good_tools + [{"type": "custom", "name": "book_slot", "url": TUNNEL + "/tools/book_slot"}]
    with pytest.raises(OperatorError, match="a booking function or instruction is present"):
        agent_ops.verify(cfg, secrets, api(fake, secrets), lambda m: None)
    llm["general_tools"], llm["general_prompt"] = good_tools, good_prompt.replace("## Preferred technicians", "## Notes")
    with pytest.raises(OperatorError, match="the prompt differs"):
        agent_ops.verify(cfg, secrets, api(fake, secrets), lambda m: None)
    llm["general_prompt"] = good_prompt
    assert agent_ops.verify(cfg, secrets, api(fake, secrets), lambda m: None) == 0


def test_update_moves_the_existing_lifecycle_agent_to_this_mode_in_place_with_no_duplicate(tmp_path):
    _code, fake, _ = create(tmp_path, mode="lifecycle")
    fake.calls.clear()
    lines, secrets = [], FakeSecrets()
    assert agent_ops.update(config(tmp_path, mode=MODE), secrets, api(fake, secrets), lines.append, confirm=True) == 0
    patches = [c for c in fake.calls if c[0] == "PATCH"]
    assert len(patches) == 1 and sorted(patches[0][2]) == ["begin_message", "general_prompt", "general_tools"]
    assert patches[0][2]["general_prompt"] == expected_prompt(mode=MODE) and len(patches[0][2]["general_tools"]) == 12
    assert fake.created == 2 and len(fake.llms) == 1 and len(fake.agents) == 1 and not any(c[1].startswith(("create", "update-agent")) for c in fake.calls)
    with pytest.raises(OperatorError, match="--confirm-update"):
        agent_ops.update(config(tmp_path, mode=MODE), secrets, api(fake, secrets), lambda m: None, confirm=False)


# ---------------------------------------------------------------- preflight against the REAL server


@pytest.fixture()
def serving(tmp_path):
    servers = []

    def start(*, preferences=True, name="pre.sqlite3"):
        lifecycle = open_lifecycle(tmp_path / name)
        endpoint, _driver = build_lifecycle_demo(KEY, lifecycle, bearer_token=TOKEN, preferences=preferences)
        server = make_http_server(endpoint, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}", lifecycle

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_preflight_passes_against_the_real_server_and_saves_nothing(tmp_path, serving):
    base, lifecycle = serving()
    before, held, stamp = lifecycle.store.counts(), PreferenceStore(lifecycle.store).counts(), lifecycle.store.change_stamp()
    lines = []
    assert agent_ops.preflight(config(tmp_path, mode=MODE), FakeSecrets(), lines.append, base_url=base) == 0
    saved = State(tmp_path / "state").read("preflight.json")
    assert saved["problems"] == [] and saved["paid_call_started"] is False and saved["unsigned_rejected"] and saved["booking_route_absent"]
    assert [r["name"] for r in saved["records"]][-3:] == ["get_preferences", "save_preference", "remove_preference"] and len(saved["records"]) == 12
    results = {r["name"]: r["result"] for r in saved["records"][-3:]}
    assert results["get_preferences"]["status"] == "no_preferences" and results["save_preference"]["status"] == "confirmation_required" and results["remove_preference"]["status"] == "nothing_to_remove"
    assert all("confirmed" not in r["args"] and "confirmation_token" not in r["args"] for r in saved["records"]), "a preflight only reads and reads back"
    assert lifecycle.store.counts() == before and PreferenceStore(lifecycle.store).counts() == held == {"preferences": 0} and lifecycle.store.change_stamp() == stamp
    assert lifecycle.store.outbox_rows() == []
    assert KEY not in "\n".join(lines) and TOKEN not in "\n".join(lines) and "0142" not in "\n".join(lines)


def test_preflight_still_passes_and_changes_nothing_when_the_fictional_client_already_has_preferences(tmp_path, serving):
    base, lifecycle = serving()
    willa = next(c for c in lifecycle.store.clients_with_phone(WILLA[1]) if c.name == WILLA[0])
    prefs = PreferenceStore(lifecycle.store)
    prefs.set(willa.client_id, "massage-swedish-massage", "910007")  # the very technician the preflight proposes: it must answer 'unchanged', not save again
    prefs.set(willa.client_id, "facial-signature-facial", "910011")
    held = [(p.family_id, p.staff_id, p.version) for p in prefs.for_client(willa.client_id)]
    assert agent_ops.preflight(config(tmp_path, mode=MODE), FakeSecrets(), lambda m: None, base_url=base) == 0
    records = State(tmp_path / "state").read("preflight.json")["records"][-3:]
    assert [r["result"]["status"] for r in records] == ["preferences_listed", "preference_unchanged", "confirmation_required"]
    assert [(p.family_id, p.staff_id, p.version) for p in prefs.for_client(willa.client_id)] == held


def test_preflight_in_this_mode_against_a_server_without_the_preferences_says_how_to_start_it(tmp_path, serving):
    base, _ = serving(preferences=False)
    with pytest.raises(OperatorError, match=r"does not have the preferred-technician functions: start it with `demo-serve --lifecycle --preferences`"):
        agent_ops.preflight(config(tmp_path, mode=MODE), FakeSecrets(), lambda m: None, base_url=base)


def test_the_lifecycle_preflight_is_unchanged_and_asks_nothing_of_preferences(tmp_path, serving):
    base, _ = serving(preferences=False)
    assert agent_ops.preflight(config(tmp_path, mode="lifecycle"), FakeSecrets(), lambda m: None, base_url=base) == 0
    assert len(State(tmp_path / "state").read("preflight.json")["records"]) == 9


def test_a_preference_preflight_case_can_never_confirm_anything_and_uses_the_fictional_client():
    cases = list(_preference_cases())
    assert [name for name, _ in cases] == ["get_preferences", "save_preference", "remove_preference"]
    for name, args in cases:
        assert "confirmed" not in args and "confirmation_token" not in args, name
        assert args["phone"] == WILLA[1] and args["name"] == WILLA[0] and WILLA[0] != ANSEL[0]


def test_the_preflight_judges_a_reply_that_saved_something_or_does_not_say_it_is_the_demo_calendar_as_a_problem():
    good = [{"result": {"status": "no_preferences", "source": "demo_calendar"}},
            {"result": {"status": "confirmation_required", "confirmation_token": "t", "source": "demo_calendar"}},
            {"result": {"status": "nothing_to_remove", "source": "demo_calendar"}}]
    assert _preference_problems(good) == []
    saved = [good[0], {"result": {"status": "preference_saved", "source": "demo_calendar"}}, good[2]]
    assert any("did not come back as a read-back" in p for p in _preference_problems(saved))
    removed = [good[0], good[1], {"result": {"status": "preference_removed", "source": "demo_calendar"}}]
    assert any("removing a preferred technician" in p for p in _preference_problems(removed))
    tokenless = [good[0], {"result": {"status": "confirmation_required", "source": "demo_calendar"}}, good[2]]
    assert any("saving a preferred technician" in p for p in _preference_problems(tokenless))
    unlabelled = [{"result": {"status": "no_preferences"}}, good[1], good[2]]
    assert any("does not say it is the demo calendar" in p for p in _preference_problems(unlabelled))
    assert any("lookup" in p for p in _preference_problems([{"result": {"status": "error", "source": "demo_calendar"}}, good[1], good[2]]))


# ---------------------------------------------------------------- the prepared page and the instructions


def test_the_instructions_for_this_mode_start_the_server_with_the_flag_and_give_the_scripted_checks(tmp_path):
    state = State(tmp_path / "state")
    state.write("agent.json", {"agent_id": "agent_test", "version": 0})
    cfg = config(tmp_path, mode=MODE)
    assert agent_ops.prepare_web(cfg, FakeSecrets(), lambda m: None) == 0
    text = (tmp_path / "state" / "test-instructions.txt").read_text(encoding="utf-8")
    assert "python -m aria_booking demo-serve --lifecycle --preferences\n" in text and "PREFERRED TECHNICIANS (demo only" in text
    for step in ("P1.", "P2.", "P3.", "P4.", "P5.", "P6.", "P7."):
        assert step in text, step
    assert "never changes an appointment and never guarantees a time" in text and "Nothing is saved unless you ask for it" in text
    assert f"{WILLA[0]} and {ANSEL[0]} share a number" in text and "their preferences never mix" in text
    page = (cfg.web_out / "index.html").read_text(encoding="utf-8")
    assert page.count("Demonstration site. Appointments and business details are sample data.") == 1
    assert KEY not in text and TOKEN not in text and PUBLIC not in text


def test_the_instructions_for_the_other_modes_say_nothing_of_preferences(tmp_path):
    for mode in ("lifecycle", "availability"):
        directory = tmp_path / mode
        State(directory / "state").write("agent.json", {"agent_id": "agent_test", "version": 0})
        assert agent_ops.prepare_web(config(directory, mode=mode), FakeSecrets(), lambda m: None) == 0
        text = (directory / "state" / "test-instructions.txt").read_text(encoding="utf-8")
        assert "--preferences" not in text and "PREFERRED TECHNICIANS" not in text and "preferred technician" not in text.lower()
