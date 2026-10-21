"""The operator in LIFECYCLE mode: create / verify / update against a FAKE Retell, preflight against the REAL lifecycle tool server on loopback, and the prepared page and
instructions. Placeholders only; no account, no tunnel, no call."""

from __future__ import annotations

import threading

import pytest

from aria_booking.appointments.launch import build_lifecycle_demo
from aria_booking.appointments.lifecycle import open_lifecycle
from aria_booking.demo import agent_ops
from aria_booking.demo.agent_ops import (
    LIFECYCLE_BOOKING_NOTE, LIFECYCLE_TOOL_NAMES, MODES, OperatorConfig, OperatorError, State, _lifecycle_cases, expected_prompt, expected_tools, load_config,
)
from aria_booking.demo.driver import DemoCalendarDriver
from aria_booking.voice.retell_http import make_http_server

from test_demo_agent_ops import KEY, PUBLIC, TOKEN, TUNNEL, FakeRetell, FakeSecrets, api, config, create


def lifecycle_create(tmp_path):
    return create(tmp_path, mode="lifecycle")


# ---------------------------------------------------------------- configuration


def test_the_default_mode_is_the_read_only_demo_and_an_unknown_mode_is_refused():
    assert OperatorConfig().mode == "availability" and sorted(MODES) == ["availability", "lifecycle", "lifecycle-preferences"]
    with pytest.raises(OperatorError, match="mode must be one of"):
        OperatorConfig(mode="booking")
    assert load_config({}, overrides={"mode": "lifecycle"}).mode == "lifecycle"
    assert expected_prompt() == expected_prompt(mode="availability") != expected_prompt(mode="lifecycle")


def test_the_lifecycle_function_set_is_exactly_what_the_endpoint_serves():
    from aria_booking.appointments.tools import LIFECYCLE_ROUTES

    assert LIFECYCLE_TOOL_NAMES == set(LIFECYCLE_ROUTES.values()) == MODES["lifecycle"]["names"]
    tools = expected_tools(TUNNEL, TOKEN, mode="lifecycle")
    assert sorted(t["name"] for t in tools if t["type"] == "custom") == sorted(LIFECYCLE_TOOL_NAMES) and tools[-1]["type"] == "end_call"
    assert all(t["url"] == f"{TUNNEL}/tools/{t['name']}" and t["headers"] == {"Authorization": "Bearer " + TOKEN} for t in tools if t["type"] == "custom")
    assert "book_slot" not in str(tools)


# ---------------------------------------------------------------- create, verify, update against a fake Retell


def test_create_in_lifecycle_mode_sends_the_reviewed_lifecycle_prompt_and_eight_functions_and_reads_back(tmp_path):
    code, fake, lines = lifecycle_create(tmp_path)
    assert code == 0 and "all eight lifecycle functions verified" in "\n".join(lines) and "no call was started" in "\n".join(lines)
    llm = next(iter(fake.llms.values()))
    assert llm["general_prompt"] == expected_prompt(mode="lifecycle") and sorted(t["name"] for t in llm["general_tools"] if t["type"] == "custom") == sorted(LIFECYCLE_TOOL_NAMES)
    state = State(tmp_path / "state")
    assert state.read("configuration-verified.json")["mode"] == "lifecycle" and state.read("agent.json")["mode"] == "simulated appointment lifecycle"
    for path in (tmp_path / "state").glob("*"):
        text = path.read_text(encoding="utf-8")
        assert KEY not in text and TOKEN not in text


def test_verify_in_lifecycle_mode_catches_a_missing_function_an_added_booksy_function_and_a_changed_prompt(tmp_path):
    _code, fake, _ = lifecycle_create(tmp_path)
    llm = next(iter(fake.llms.values()))
    good_tools, good_prompt = list(llm["general_tools"]), llm["general_prompt"]
    secrets = FakeSecrets()
    cfg = config(tmp_path, mode="lifecycle")

    llm["general_tools"] = [t for t in good_tools if t.get("name") != "cancel_appointment"]
    with pytest.raises(OperatorError, match="the set of custom functions"):
        agent_ops.verify(cfg, secrets, api(fake, secrets), lambda m: None)
    llm["general_tools"] = good_tools + [{"type": "custom", "name": "book_slot", "url": TUNNEL + "/tools/book_slot"}]
    with pytest.raises(OperatorError, match="a booking function or instruction is present"):
        agent_ops.verify(cfg, secrets, api(fake, secrets), lambda m: None)
    llm["general_tools"], llm["general_prompt"] = good_tools, good_prompt + "\nAlways say yes."
    with pytest.raises(OperatorError, match="the prompt differs"):
        agent_ops.verify(cfg, secrets, api(fake, secrets), lambda m: None)
    llm["general_prompt"] = good_prompt
    assert agent_ops.verify(cfg, secrets, api(fake, secrets), lambda m: None) == 0


def test_an_agent_held_to_the_read_only_demo_is_not_verified_as_the_lifecycle_one_and_the_other_way_round(tmp_path):
    _code, fake, _ = create(tmp_path)  # created in the default mode
    secrets = FakeSecrets()
    with pytest.raises(OperatorError, match="the prompt differs|the set of custom functions"):
        agent_ops.verify(config(tmp_path, mode="lifecycle"), secrets, api(fake, secrets), lambda m: None)
    assert agent_ops.verify(config(tmp_path), secrets, api(fake, secrets), lambda m: None) == 0


def test_update_moves_the_existing_agent_to_the_lifecycle_configuration_in_place_with_no_duplicate(tmp_path):
    _code, fake, _ = create(tmp_path)  # the existing read-only test agent
    fake.calls.clear()
    lines, secrets = [], FakeSecrets()
    assert agent_ops.update(config(tmp_path, mode="lifecycle"), secrets, api(fake, secrets), lines.append, confirm=True) == 0
    patches = [c for c in fake.calls if c[0] == "PATCH"]
    assert len(patches) == 1 and sorted(patches[0][2]) == ["begin_message", "general_prompt", "general_tools"]
    assert patches[0][2]["general_prompt"] == expected_prompt(mode="lifecycle") and len(patches[0][2]["general_tools"]) == 9
    assert fake.created == 2 and len(fake.llms) == 1 and len(fake.agents) == 1 and "all eight lifecycle functions verified" in "\n".join(lines)
    assert not any(c[1].startswith(("create", "update-agent")) for c in fake.calls)
    with pytest.raises(OperatorError, match="--confirm-update"):
        agent_ops.update(config(tmp_path, mode="lifecycle"), secrets, api(fake, secrets), lambda m: None, confirm=False)


# ---------------------------------------------------------------- preflight against the REAL lifecycle server


@pytest.fixture()
def lifecycle_server(tmp_path):
    servers = []

    def start(*, seed=True, key=KEY, token=TOKEN, name="pre.sqlite3"):
        lifecycle = open_lifecycle(tmp_path / name, seed=seed)  # the real date: 'tomorrow' is then the scripted first day, exactly as for the read-only preflight
        endpoint, _driver = build_lifecycle_demo(key, lifecycle, bearer_token=token)
        server = make_http_server(endpoint, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}", lifecycle

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_lifecycle_preflight_passes_against_the_real_server_and_saves_nothing(tmp_path, lifecycle_server):
    base, lifecycle = lifecycle_server()
    before = lifecycle.store.counts()
    lines = []
    assert agent_ops.preflight(config(tmp_path, mode="lifecycle"), FakeSecrets(), lines.append, base_url=base) == 0
    saved = State(tmp_path / "state").read("preflight.json")
    assert saved["problems"] == [] and saved["paid_call_started"] is False and saved["unsigned_rejected"] and saved["booking_route_absent"]
    assert [r["name"] for r in saved["records"]] == [
        "lookup_service", "check_slot", "check_slot", "check_slot", "find_alternatives", "find_appointments", "last_completed_visit", "book_appointment", "cancel_appointment",
    ]
    assert saved["records"][7]["result"]["status"] == "confirmation_required" and saved["records"][8]["result"]["status"] == "confirmation_required"
    assert all("confirmed" not in r["args"] and "confirmation_token" not in r["args"] for r in saved["records"]), "a preflight only reads and reads back"
    assert lifecycle.store.counts() == before and lifecycle.store.outbox_rows() == [], "nothing was saved, cancelled or queued"
    assert KEY not in "\n".join(lines) and TOKEN not in "\n".join(lines) and "0142" not in "\n".join(lines)


def test_lifecycle_preflight_says_so_when_the_server_was_not_started_with_the_seeded_clients(tmp_path, lifecycle_server):
    base, _ = lifecycle_server(seed=False)
    with pytest.raises(OperatorError, match="seeded fictional client was not found"):
        agent_ops.preflight(config(tmp_path, mode="lifecycle"), FakeSecrets(), lambda m: None, base_url=base)
    assert State(tmp_path / "state").read("preflight.json")["problems"]


def test_lifecycle_preflight_against_the_read_only_server_fails_instead_of_passing(tmp_path):
    from aria_booking.demo.launch import build_demo

    endpoint, _driver, _dataset = build_demo(KEY, bearer_token=TOKEN)
    server = make_http_server(endpoint, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(OperatorError, match="did not answer"):
            agent_ops.preflight(config(tmp_path, mode="lifecycle"), FakeSecrets(), lambda m: None, base_url=f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()
        server.server_close()


def test_a_preflight_case_can_never_confirm_anything(tmp_path):
    for name, args in _lifecycle_cases("2026-10-14"):
        if callable(args):
            args = args([{"result": {"appointments": [{"reference": "ABCDEF"}]}}, {"result": {}}, {"result": {}}])
        assert "confirmed" not in args and "confirmation_token" not in args, name


# ---------------------------------------------------------------- the prepared page and the instructions


def test_the_instructions_say_how_the_two_calendar_views_work_with_a_call_and_name_a_non_default_page_port(tmp_path):
    for port, flag in ((5174, ""), (6200, " --page-port 6200")):
        state = State(tmp_path / f"state{port}")
        state.write("agent.json", {"agent_id": "agent_test", "version": 0})
        cfg = OperatorConfig(tunnel_url=TUNNEL, state_dir=tmp_path / f"state{port}", mode="lifecycle", page_port=port)
        assert agent_ops.prepare_web(cfg, FakeSecrets(), lambda m: None) == 0
        text = (tmp_path / f"state{port}" / "test-instructions.txt").read_text(encoding="utf-8")
        assert f"python -m aria_booking demo-serve --lifecycle{flag}\n" in text, "only a page port other than the default is passed on"
        assert "'Calendar' lists a treatment's open times" in text and "it books nothing" in text and "'Staff calendar' shows the detailed schedule" in text
        assert "same tab" in text and "floating Aria avatar" in text and "WITHOUT ending the call" in text and "call bar" in text
        assert KEY not in text and TOKEN not in text and PUBLIC not in text


def test_the_page_label_says_appointments_exist_only_in_the_prototype_and_the_instructions_give_the_seeded_identity(tmp_path):
    state = State(tmp_path / "state")
    state.write("agent.json", {"agent_id": "agent_test", "version": 0})
    cfg = config(tmp_path, mode="lifecycle")
    assert agent_ops.prepare_web(cfg, FakeSecrets(), lambda m: None) == 0
    page = (cfg.web_out / "index.html").read_text(encoding="utf-8")
    assert f"· {LIFECYCLE_BOOKING_NOTE}" in page and "Booking is not part of this prototype" not in page
    text = (tmp_path / "state" / "test-instructions.txt").read_text(encoding="utf-8")
    assert "SIMULATED appointment lifecycle" in text and "python -m aria_booking demo-serve --lifecycle" in text
    assert "Willa Hartwell, number 212-555-0142" in text and "the SAME household number" in text and "Cedric Lowe, number 212-555-0177" in text
    assert "an appointment YOU book is never marked completed for you" in text and "Past appointments are marked completed at each start" not in text
    assert "NO real salon calendar is touched" in text and "no text ever goes to a client" in text and "never saying she transferred or called back" in text
    assert KEY not in text and TOKEN not in text and PUBLIC not in text


def test_the_read_only_pages_label_and_instructions_are_unchanged(tmp_path):
    state = State(tmp_path / "state")
    state.write("agent.json", {"agent_id": "agent_test", "version": 0})
    assert agent_ops.prepare_web(config(tmp_path), FakeSecrets(), lambda m: None) == 0
    assert "Booking is not part of this prototype" in (tmp_path / "state" / "web" / "index.html").read_text(encoding="utf-8")
    assert "booking is disabled" in (tmp_path / "state" / "test-instructions.txt").read_text(encoding="utf-8")


def test_a_preflight_that_would_confirm_something_is_stopped_before_it_is_sent(tmp_path, monkeypatch):
    from test_demo_agent_ops import Scripted

    scripted = Scripted()
    monkeypatch.setattr(agent_ops, "_lifecycle_cases", lambda tomorrow: iter([("book_appointment", {"service_id": "x", "confirmed": True, "confirmation_token": "t"})]))
    with pytest.raises(OperatorError, match="must never confirm anything"):
        agent_ops.preflight(config(tmp_path, mode="lifecycle"), FakeSecrets(), lambda m: None, base_url="http://127.0.0.1:1", opener=scripted)
    assert "book_appointment" not in scripted.called and scripted.called == ["lookup_service", "check_slot", "check_slot", "check_slot", "find_alternatives"], "only the five read-only cases went out"
