"""Packaging and setup for the demo's SEPARATE test agent and voice page, checked entirely offline with a FAKE Retell and fake secrets.

No Retell account, no tunnel, no network beyond a loopback server in two tests, no paid call, and nothing real: keys here are obvious placeholders.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import threading
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aria_booking import cli
from aria_booking.demo import agent_ops
from aria_booking.demo.agent_ops import (
    ACTIONS, ALLOWED, CLOCK_SECTION, BOOKING, OperatorConfig, OperatorError, RetellApi, RetellApiError, State, expected_prompt, expected_tools, load_config,
    opening_line, run, sign_body, validate_tunnel,
)
from aria_booking.demo.launch import build_demo
from aria_booking.demo.secret_store import DpapiSecrets, EnvSecrets, SecretMissing, provider_from_environment
from aria_booking.voice.retell_http import make_http_server

TUNNEL = "https://demo-tunnel.example"
KEY, TOKEN, PUBLIC = "placeholder-private-key", "placeholder-tool-token", "public_key_placeholder"
REPO = BOOKING.parent


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeRetell:
    """An in-memory stand-in for the few Retell calls the operator may make. Records every request; can be told to fail or to be tampered with."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None, dict]] = []
        self.llms: dict[str, dict] = {}
        self.agents: dict[str, dict] = {}
        self.fail: dict[str, object] = {}  # path -> HTTP status int, or "network"
        self.created = 0
        self.updated = 0
        self.ignore_updates = False  # an account that answers 200 to an update but does not apply it
        self.on_update = None  # a callable(llm dict) run after an update is applied: lets a test make the account change something it was not asked to

    def __call__(self, request, timeout=None):
        path = request.full_url.removeprefix("https://api.retellai.com/")
        body = json.loads(request.data) if request.data else None
        self.calls.append((request.get_method(), path, body, dict(request.header_items())))
        failure = self.fail.get(path.split("/")[0] if path.startswith("get-") else path)
        if failure == "network":
            raise urllib.error.URLError("simulated outage")
        if isinstance(failure, int):
            raise urllib.error.HTTPError(request.full_url, failure, "x", {}, io.BytesIO(b'{"echo": "Bearer SHOULD-NOT-SURVIVE"}'))
        if path == "create-retell-llm":
            llm_id = f"llm_{len(self.llms) + 1}"
            self.llms[llm_id] = {**body, "llm_id": llm_id, "version": 0, "is_published": False}
            self.created += 1
            return FakeResponse(json.dumps({"llm_id": llm_id}).encode())
        if path == "create-agent":
            agent_id = f"agent_{len(self.agents) + 1}"
            self.agents[agent_id] = {**body, "agent_id": agent_id, "version": 0}
            self.created += 1
            return FakeResponse(json.dumps({"agent_id": agent_id, "agent_name": body["agent_name"], "version": 0}).encode())
        if path.startswith("update-retell-llm/"):
            llm_id = path.split("/", 1)[1]
            assert request.get_method() == "PATCH" and llm_id in self.llms
            if not self.ignore_updates:
                self.llms[llm_id].update(body)
                if self.on_update:
                    self.on_update(self.llms[llm_id])
            self.updated += 1
            return FakeResponse(json.dumps({"llm_id": llm_id, "version": 0, "is_published": False}).encode())
        if path.startswith("get-agent/"):
            return FakeResponse(json.dumps(self.agents[path.split("/", 1)[1]]).encode())
        if path.startswith("get-retell-llm/"):
            return FakeResponse(json.dumps(self.llms[path.split("/", 1)[1]]).encode())
        if path == "v3/list-calls":
            return FakeResponse(json.dumps({"items": [{"call_id": "call_1"}]}).encode())
        if path.startswith("v2/get-call/"):
            return FakeResponse(json.dumps({
                "call_status": "ended", "duration_ms": 61000, "transcript": "SHOULD NOT BE KEPT",
                "transcript_with_tool_calls": [
                    {"role": "user", "content": "private words"},
                    {"role": "tool_call_invocation", "name": "check_slot", "time_sec": 4.2, "arguments": "{\"time\": \"16:00\"}"},
                    {"role": "tool_call_result", "name": "check_slot", "time_sec": 4.4, "content": "ok"},
                ],
            }).encode())
        raise AssertionError("the operator asked the fake for something it should never ask: " + path)


class FakeSecrets:
    def __init__(self, **values):
        self.values = {"retell_api_key": KEY, "tool_token": TOKEN, "retell_public_key": PUBLIC, **values}

    def get(self, name):
        if not self.values.get(name):
            raise SecretMissing(f"{name} is missing")
        return self.values[name]

    def has(self, name):
        return bool(self.values.get(name))

    def describe(self):
        return "fake secrets"


def config(tmp_path, **kw) -> OperatorConfig:
    return OperatorConfig(tunnel_url=TUNNEL, state_dir=tmp_path / "state", **kw)


def api(fake: FakeRetell, secrets=None) -> RetellApi:
    secrets = secrets or FakeSecrets()
    return RetellApi(lambda: secrets.get("retell_api_key"), opener=fake)


def create(tmp_path, fake=None, secrets=None, **kw):
    fake, secrets, lines = fake or FakeRetell(), secrets or FakeSecrets(), []
    code = agent_ops.create(config(tmp_path, **kw), secrets, api(fake, secrets), lines.append, confirm=True)
    return code, fake, lines


# ---------------------------------------------------------------- the tunnel address and configuration


@pytest.mark.parametrize("good", ["https://abc-def.trycloudflare.com", "https://demo.example/", "https://demo.example:8443"])
def test_a_public_https_address_is_accepted(good):
    assert validate_tunnel(good).startswith("https://")


@pytest.mark.parametrize("bad", [
    "", "   ", "http://demo.example", "https://localhost", "https://127.0.0.1", "https://10.0.0.5", "https://192.168.1.2", "https://[::1]", "https://host.local",
    "https://user:pw@demo.example", "https://demo.example/path", "https://demo.example?x=1", "ftp://demo.example", "demo.example", "https://",
])
def test_anything_else_is_refused_because_retell_cannot_reach_it(bad):
    with pytest.raises(OperatorError):
        validate_tunnel(bad)


def test_configuration_comes_from_defaults_then_the_file_then_the_environment_then_the_command_line(tmp_path):
    file = tmp_path / "c.json"
    file.write_text(json.dumps({"tunnel_url": "https://from-file.example", "agent_name": "From file", "tools_port": 9001}), encoding="utf-8")
    cfg = load_config({"ARIA_DEMO_CONFIG": str(file)})
    assert (cfg.tunnel_url, cfg.agent_name, cfg.tools_port, cfg.model) == ("https://from-file.example", "From file", 9001, "gpt-4.1")
    assert load_config({"ARIA_DEMO_CONFIG": str(file), "ARIA_DEMO_TUNNEL_URL": "https://from-env.example"}).tunnel_url == "https://from-env.example"
    assert load_config({"ARIA_DEMO_CONFIG": str(file)}, overrides={"tunnel_url": "https://from-flag.example", "calendar_port": None}).tunnel_url == "https://from-flag.example"


def test_an_unknown_key_in_the_config_file_is_refused_not_ignored(tmp_path):
    file = tmp_path / "c.json"
    file.write_text(json.dumps({"tunnel_url": TUNNEL, "tunel_url": "typo"}), encoding="utf-8")
    with pytest.raises(OperatorError, match="unknown config keys: tunel_url"):
        load_config({}, file=str(file))


def test_a_missing_or_broken_config_file_is_a_clear_refusal_that_does_not_echo_its_contents(tmp_path):
    with pytest.raises(OperatorError, match="could not be read"):
        load_config({}, file=str(tmp_path / "missing.json"))
    broken = tmp_path / "b.json"
    broken.write_text("{not json SECRET", encoding="utf-8")
    with pytest.raises(OperatorError) as info:
        load_config({}, file=str(broken))
    assert "SECRET" not in str(info.value)


def test_the_shipped_example_config_is_loadable_and_holds_only_placeholders():
    path = BOOKING / "demo_agent.example.json"
    cfg = load_config({}, file=str(path))
    assert cfg.tunnel_url == "https://your-tunnel-host.example" and cfg.calendar_port == 8790
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"key_[A-Za-z0-9]{8,}|Bearer |agent_[0-9a-f]{12}|[A-Za-z]:\\Users|trycloudflare", text)


# ---------------------------------------------------------------- what the agent must be, from the reviewed source


def test_the_expected_prompt_is_the_reviewed_prompt_from_who_you_are_plus_the_clock_line():
    prompt = expected_prompt()
    source = (BOOKING / "voice_agent_draft" / "prompt_demo_calendar.md").read_text(encoding="utf-8")
    assert prompt == source[source.index("## Who you are"):] + CLOCK_SECTION
    assert "{{current_time_America/New_York}}" in prompt and "DEMO calendar" in prompt and "book_slot" not in prompt


def test_a_prompt_without_exactly_one_who_you_are_is_refused(tmp_path):
    root = tmp_path / "b"
    (root / "voice_agent_draft").mkdir(parents=True)
    (root / "voice_agent_draft" / "prompt_demo_calendar.md").write_text("no heading here", encoding="utf-8")
    with pytest.raises(OperatorError, match="exactly once"):
        expected_prompt(root)


def test_the_expected_functions_are_the_three_read_only_ones_pointed_at_the_tunnel_with_the_token():
    tools = expected_tools(TUNNEL, TOKEN)
    custom = [t for t in tools if t["type"] == "custom"]
    assert {t["name"] for t in custom} == {"lookup_service", "check_slot", "find_alternatives"} and tools[-1]["type"] == "end_call"
    for t in custom:
        assert t["url"] == f"{TUNNEL}/tools/{t['name']}" and t["headers"] == {"Authorization": "Bearer " + TOKEN}
        assert t["speak_after_execution"] is True and t["args_at_root"] is False
    assert "book_slot" not in json.dumps(tools) and "option_id" not in json.dumps(tools)
    assert all(t["timeout_ms"] <= 20_000 for t in custom if t["name"] != "lookup_service") and next(t for t in custom if t["name"] == "lookup_service")["timeout_ms"] <= 5000


def test_a_tool_file_with_a_booking_function_is_refused(tmp_path):
    root = tmp_path / "b"
    (root / "voice_agent_draft").mkdir(parents=True)
    tools = json.loads((BOOKING / "voice_agent_draft" / "tools.json").read_text(encoding="utf-8"))
    (root / "voice_agent_draft" / "tools_availability_only.json").write_text(json.dumps(tools), encoding="utf-8")
    with pytest.raises(OperatorError, match="exactly the three read-only functions"):
        expected_tools(TUNNEL, TOKEN, root)


# ---------------------------------------------------------------- plan: offline, reads no secret


def test_plan_prints_what_would_happen_without_any_network_or_secret_value(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("plan must not use the network")))
    lines = []
    secrets = FakeSecrets()
    assert agent_ops.plan(config(tmp_path), secrets, lines.append) == 0
    text = "\n".join(lines)
    assert "PLAN (nothing is created or contacted)" in text and TUNNEL in text and "no booking function" in text and "--confirm-create" in text
    for secret in (KEY, TOKEN, PUBLIC):
        assert secret not in text


def test_plan_works_with_no_tunnel_and_no_secrets_and_says_what_is_missing(tmp_path):
    lines = []
    assert agent_ops.plan(OperatorConfig(state_dir=tmp_path / "s"), EnvSecrets({}), lines.append) == 0
    text = "\n".join(lines)
    assert "(not set:" in text and text.count("NOT available") == 3


# ---------------------------------------------------------------- create: one time, explicit, read back


def test_create_refuses_without_the_explicit_flag_and_touches_nothing(tmp_path):
    fake, lines = FakeRetell(), []
    with pytest.raises(OperatorError, match="--confirm-create"):
        agent_ops.create(config(tmp_path), FakeSecrets(), api(fake), lines.append, confirm=False)
    assert fake.calls == [] and not (tmp_path / "state").exists()


@pytest.mark.parametrize("missing", ["retell_api_key", "tool_token"])
def test_create_refuses_before_writing_anything_when_a_secret_is_missing(tmp_path, missing):
    fake = FakeRetell()
    with pytest.raises(SecretMissing):
        agent_ops.create(config(tmp_path), FakeSecrets(**{missing: ""}), api(fake), lambda m: None, confirm=True)
    assert fake.calls == [] and not (tmp_path / "state").exists()


def test_create_refuses_a_bad_tunnel_before_any_call(tmp_path):
    fake = FakeRetell()
    with pytest.raises(OperatorError):
        agent_ops.create(OperatorConfig(tunnel_url="http://localhost", state_dir=tmp_path / "s"), FakeSecrets(), api(fake), lambda m: None, confirm=True)
    assert fake.calls == []


def test_create_makes_one_llm_and_one_agent_with_the_reviewed_settings_and_reads_them_back(tmp_path):
    code, fake, lines = create(tmp_path)
    assert code == 0 and fake.created == 2 and "verified against the reviewed source" in "\n".join(lines)
    llm = next(iter(fake.llms.values()))
    agent = next(iter(fake.agents.values()))
    assert llm["general_prompt"] == expected_prompt() and llm["model"] == "gpt-4.1"
    assert llm["begin_message"] == opening_line() and "Serenity Spa" in llm["begin_message"] and not any(w in llm["begin_message"].lower() for w in ("demo", "simulat", "glamour"))
    assert {t["name"] for t in llm["general_tools"] if t["type"] == "custom"} == {"lookup_service", "check_slot", "find_alternatives"}
    assert agent["agent_name"] == "Aria - Simulated Calendar Demo" and agent["max_call_duration_ms"] == 300_000 and agent["data_storage_retention_days"] == 7
    assert agent["contact_memory_config"] == {"enable_read": False, "enable_update": False}
    assert agent["response_engine"] == {"type": "retell-llm", "llm_id": "llm_1"}
    state = State(tmp_path / "state")
    assert state.read("agent.json")["tunnel"] == TUNNEL and state.read("llm.json") == {"llm_id": "llm_1"}
    verified = state.read("configuration-verified.json")
    assert verified["booking_enabled"] is False and verified["tools"] == ["check_slot", "find_alternatives", "lookup_service"]


def test_the_state_files_never_hold_a_secret_or_the_tool_token(tmp_path):
    create(tmp_path)
    for path in (tmp_path / "state").glob("*"):
        text = path.read_text(encoding="utf-8")
        assert KEY not in text and TOKEN not in text and PUBLIC not in text, path.name


def test_running_create_again_creates_nothing_more(tmp_path):
    _code, fake, _lines = create(tmp_path)
    code, again, _ = create(tmp_path, fake=fake)
    assert code == 0 and fake.created == 2 and [c[1] for c in fake.calls].count("create-agent") == 1 and again is fake


def test_the_authorization_header_carries_the_key_but_the_key_is_never_in_a_body(tmp_path):
    _code, fake, _ = create(tmp_path)
    for method, path, body, headers in fake.calls:
        assert headers["Authorization"] == "Bearer " + KEY
        assert KEY not in json.dumps(body or {})


def test_an_uncertain_llm_create_leaves_a_marker_and_is_never_retried_blindly(tmp_path):
    fake = FakeRetell()
    fake.fail["create-retell-llm"] = "network"
    with pytest.raises(RetellApiError) as info:
        create(tmp_path, fake=fake)
    assert info.value.definite is False and (tmp_path / "state" / "llm-create-pending.json").exists()
    fake.fail.clear()
    with pytest.raises(OperatorError, match="uncertain outcome"):
        create(tmp_path, fake=fake)
    assert fake.created == 0, "the second attempt made no call that could create a duplicate"


def test_an_uncertain_agent_create_keeps_the_llm_and_stops(tmp_path):
    fake = FakeRetell()
    fake.fail["create-agent"] = "network"
    with pytest.raises(RetellApiError):
        create(tmp_path, fake=fake)
    fake.fail.clear()
    state = State(tmp_path / "state")
    assert state.exists("llm.json") and state.exists("agent-create-pending.json") and not state.exists("agent.json")
    with pytest.raises(OperatorError, match="agent create has an uncertain outcome"):
        create(tmp_path, fake=fake)
    assert len(fake.llms) == 1 and len(fake.agents) == 0


def test_a_definite_refusal_by_retell_clears_the_marker_so_a_retry_is_safe_and_drops_the_error_body(tmp_path):
    fake = FakeRetell()
    fake.fail["create-retell-llm"] = 422
    with pytest.raises(RetellApiError) as info:
        create(tmp_path, fake=fake)
    assert info.value.definite is True and "SHOULD-NOT-SURVIVE" not in str(info.value) and info.value.status == 422
    assert info.value.__cause__ is None and info.value.__suppress_context__, "no chained exception can carry the response body into a traceback"
    assert not (tmp_path / "state" / "llm-create-pending.json").exists()
    fake.fail.clear()
    code, _fake, _ = create(tmp_path, fake=fake)
    assert code == 0 and fake.created == 2


def test_a_server_error_is_treated_as_uncertain_not_as_a_refusal(tmp_path):
    fake = FakeRetell()
    fake.fail["create-retell-llm"] = 503
    with pytest.raises(RetellApiError) as info:
        create(tmp_path, fake=fake)
    assert info.value.definite is False and (tmp_path / "state" / "llm-create-pending.json").exists()


# ---------------------------------------------------------------- verify: every mismatch is caught


def _verify(tmp_path, fake, **kw):
    lines = []
    secrets = FakeSecrets()
    return agent_ops.verify(config(tmp_path, **kw), secrets, api(fake, secrets), lines.append)


def _tamper(tmp_path, change):
    _code, fake, _ = create(tmp_path)
    change(fake)
    return fake


@pytest.mark.parametrize("label,change", [
    ("the prompt differs", lambda f: f.llms["llm_1"].update(general_prompt="changed")),
    ("the opening line", lambda f: f.llms["llm_1"].update(begin_message="Hi, this is a different greeting.")),
    ("a booking function or instruction is present", lambda f: f.llms["llm_1"]["general_tools"].append({"type": "custom", "name": "book_slot", "url": TUNNEL + "/tools/book_slot"})),
    ("the set of custom functions", lambda f: f.llms["llm_1"].update(general_tools=[t for t in f.llms["llm_1"]["general_tools"] if t.get("name") != "find_alternatives"])),
    ("check_slot.url", lambda f: next(t for t in f.llms["llm_1"]["general_tools"] if t.get("name") == "check_slot").update(url="https://elsewhere.example/tools/check_slot")),
    ("check_slot.headers", lambda f: next(t for t in f.llms["llm_1"]["general_tools"] if t.get("name") == "check_slot").update(headers={"Authorization": "Bearer other"})),
    ("check_slot.speak_during_execution", lambda f: next(t for t in f.llms["llm_1"]["general_tools"] if t.get("name") == "check_slot").update(speak_during_execution=False)),
    ("check_slot.execution_message_description", lambda f: next(t for t in f.llms["llm_1"]["general_tools"] if t.get("name") == "check_slot").update(execution_message_description="about 30 seconds")),
    ("check_slot.parameters", lambda f: next(t for t in f.llms["llm_1"]["general_tools"] if t.get("name") == "check_slot").update(parameters={})),
    ("the call-length cap", lambda f: f.agents["agent_1"].update(max_call_duration_ms=3_600_000)),
    ("the retention days", lambda f: f.agents["agent_1"].update(data_storage_retention_days=730)),
    ("contact memory is not off", lambda f: f.agents["agent_1"].update(contact_memory_config={"enable_read": True, "enable_update": True})),
    ("the agent does not use the recorded LLM", lambda f: f.agents["agent_1"].update(response_engine={"type": "retell-llm", "llm_id": "llm_other"})),
    ("the agent's name", lambda f: f.agents["agent_1"].update(agent_name="Glamour Day Spa Receptionist")),
    ("the end_call function", lambda f: f.llms["llm_1"].update(general_tools=[t for t in f.llms["llm_1"]["general_tools"] if t.get("type") != "end_call"])),
])
def test_the_read_back_catches_every_kind_of_drift(tmp_path, label, change):
    fake = _tamper(tmp_path, change)
    with pytest.raises(OperatorError) as info:
        _verify(tmp_path, fake)
    assert label in str(info.value) and "does NOT match" in str(info.value)
    assert not (tmp_path / "state" / "configuration-verified.json").exists(), "a failed read-back removes the 'verified' record"


def test_a_changed_tunnel_address_is_reported_as_a_mismatch_not_ignored(tmp_path):
    _code, fake, _ = create(tmp_path)
    with pytest.raises(OperatorError, match="check_slot.url"):
        agent_ops.verify(OperatorConfig(tunnel_url="https://new-tunnel.example", state_dir=tmp_path / "state"), FakeSecrets(), api(fake), lambda m: None)


def test_verify_with_nothing_created_says_so(tmp_path):
    with pytest.raises(OperatorError, match="run `create --confirm-create` first"):
        _verify(tmp_path, FakeRetell())


def test_a_correct_read_back_after_a_failed_one_restores_the_verified_record(tmp_path):
    fake = _tamper(tmp_path, lambda f: f.llms["llm_1"].update(general_prompt="changed"))
    with pytest.raises(OperatorError):
        _verify(tmp_path, fake)
    fake.llms["llm_1"]["general_prompt"] = expected_prompt()
    assert _verify(tmp_path, fake) == 0 and (tmp_path / "state" / "configuration-verified.json").exists()


# ---------------------------------------------------------------- nothing can start a call


def test_the_retell_client_only_accepts_its_allow_list():
    fake = FakeRetell()
    client = RetellApi(lambda: KEY, opener=fake)
    for path, method in [("create-phone-call", "POST"), ("v2/create-phone-call", "POST"), ("create-web-call", "POST"), ("v2/create-web-call", "POST"),
                         ("create-batch-call", "POST"), ("update-agent/agent_1", "PATCH"), ("delete-agent/agent_1", "DELETE"), ("get-agent/agent_1", "POST"),
                         ("get-agent/../create-agent", "GET"), ("v2/get-call/x/../../create-agent", "GET")]:
        with pytest.raises(OperatorError):
            client.request(path, {} if method != "GET" else None, method)
    assert fake.calls == []


def test_no_path_that_starts_or_schedules_a_call_is_in_the_module():
    allowed = "".join(p.pattern for _v, p in ALLOWED)
    for forbidden in ("create-phone-call", "create-web-call", "create-batch", "register-phone", "import-phone", "schedule"):
        assert forbidden not in allowed, forbidden
    assert {p.pattern for _v, p in ALLOWED} == {
        "^create-retell-llm$", "^create-agent$", "^get-agent/[A-Za-z0-9_.-]+$", "^get-retell-llm/[A-Za-z0-9_.-]+$", "^update-retell-llm/[A-Za-z0-9_.-]+$",
        "^v3/list-calls$", "^v2/get-call/[A-Za-z0-9_.-]+$",
    }
    assert {v for v, p in ALLOWED if "update" in p.pattern} == {"PATCH"} and not any("agent" in p.pattern and v == "PATCH" for v, p in ALLOWED), "no agent update, ever"


def test_a_network_failure_surfaces_as_a_status_free_error_with_no_body():
    fake = FakeRetell()
    fake.fail["get-agent"] = "network"
    with pytest.raises(RetellApiError, match="no answer") as info:
        RetellApi(lambda: KEY, opener=fake).request("get-agent/agent_1")
    assert info.value.status is None and info.value.definite is False


# ---------------------------------------------------------------- preflight, against the REAL demo tool server on loopback


@pytest.fixture()
def tool_server():
    servers = []

    def start(*, key=KEY, token=TOKEN):
        endpoint, _driver, _dataset = build_demo(key, bearer_token=token)
        server = make_http_server(endpoint, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_preflight_passes_against_the_real_demo_server_and_records_tool_latency(tmp_path, tool_server):
    base, lines = tool_server(), []
    assert agent_ops.preflight(config(tmp_path), FakeSecrets(), lines.append, base_url=base) == 0
    saved = State(tmp_path / "state").read("preflight.json")
    assert saved["problems"] == [] and saved["unsigned_rejected"] and saved["booking_route_absent"] and saved["paid_call_started"] is False
    assert [r["name"] for r in saved["records"]] == ["lookup_service", "check_slot", "check_slot", "check_slot", "find_alternatives"], "all three functions are called"
    assert saved["records"][1]["result"]["status"] == "available" and saved["records"][2]["result"]["unavailable_reason"] == "occupied"
    assert all(r["result"].get("source") == "demo_calendar" for r in saved["records"] if r["name"] != "lookup_service") and all(r["seconds"] < 5 for r in saved["records"])
    alternatives = saved["records"][4]["result"]
    assert alternatives["status"] == "alternatives" and alternatives["options"] and not any(o["start"].endswith("T16:00:00-04:00") and o["start"].startswith(saved["records"][4]["args"]["date"])
                                                                                          for o in alternatives["options"])
    assert KEY not in "\n".join(lines) and TOKEN not in "\n".join(lines)


class Scripted:
    """A tool server stand-in that answers every signed call correctly unless told to answer one tool differently."""

    def __init__(self, **override):
        self.override, self.called = override, []

    def __call__(self, request, timeout=None):
        url = request.full_url
        if request.get_header("X-retell-signature") is None:
            raise urllib.error.HTTPError(url, 401 if url.endswith("check_slot") else 404, "x", {}, io.BytesIO(b""))
        name, body = url.rsplit("/", 1)[1], json.loads(request.data)
        self.called.append(name)
        if name in self.override:
            return FakeResponse(json.dumps(self.override[name]).encode())
        if name == "lookup_service":
            reply = {"status": "found", "ok": True, "speak": "x"}
        elif name == "check_slot":
            busy = body["args"].get("staff") == "Elva"
            reply = {"status": "unavailable" if busy else "available", "ok": not busy, "speak": "x", "source": "demo_calendar", **({"unavailable_reason": "occupied"} if busy else {})}
        else:
            date = body["args"]["date"]
            reply = {"status": "alternatives", "ok": False, "speak": "x", "source": "demo_calendar", "options": [{"start": f"{date}T17:15:00-04:00", "technician": "Elva"}]}
        return FakeResponse(json.dumps(reply).encode())


def _preflight_with(tmp_path, **override):
    scripted = Scripted(**override)
    code = agent_ops.preflight(config(tmp_path), FakeSecrets(), lambda m: None, base_url="http://127.0.0.1:1", opener=scripted)
    return code, scripted


def test_preflight_calls_all_three_functions_and_passes_when_each_answers_correctly(tmp_path):
    code, scripted = _preflight_with(tmp_path)
    assert code == 0 and set(scripted.called) == {"lookup_service", "check_slot", "find_alternatives"}


@pytest.mark.parametrize("label,reply,expected", [
    ("no alternatives", {"status": "alternatives", "ok": False, "speak": "x", "source": "demo_calendar", "options": []}, "offered no alternatives"),
    ("a different status that still lists options", {"status": "unknown", "ok": False, "speak": "x", "source": "demo_calendar",
                                                      "options": [{"start": "2030-01-01T17:15:00-04:00"}]}, "offered no alternatives"),
    ("options that are not a list", {"status": "alternatives", "ok": False, "speak": "x", "source": "demo_calendar", "options": "soon"}, "offered no alternatives"),
    ("the occupied slot itself", None, "the very slot that is occupied"),
    ("an unlabelled reply", {"status": "alternatives", "ok": False, "speak": "x", "options": [{"start": "2030-01-01T17:15:00-04:00"}]}, "find_alternatives reply does not say it is the demo calendar"),
])
def test_preflight_refuses_a_broken_find_alternatives(tmp_path, label, reply, expected):
    if reply is None:  # offers exactly the busy 4 PM slot on the asked day
        class Echo(Scripted):
            def __call__(self, request, timeout=None):
                if request.get_header("X-retell-signature") is not None and request.full_url.endswith("find_alternatives"):
                    date = json.loads(request.data)["args"]["date"]
                    self.override["find_alternatives"] = {"status": "alternatives", "ok": False, "speak": "x", "source": "demo_calendar",
                                                          "options": [{"start": f"{date}T16:00:00-04:00", "technician": "Elva"}]}
                return super().__call__(request, timeout)

        scripted = Echo()
    else:
        scripted = Scripted(find_alternatives=reply)
    with pytest.raises(OperatorError) as info:
        agent_ops.preflight(config(tmp_path), FakeSecrets(), lambda m: None, base_url="http://127.0.0.1:1", opener=scripted)
    assert expected in str(info.value), label
    assert State(tmp_path / "state").read("preflight.json")["problems"], "the failure is recorded, not only raised"


def test_preflight_fails_when_the_signing_key_differs(tmp_path, tool_server):
    base = tool_server(key="a-different-key")
    with pytest.raises(OperatorError, match="did not answer|HTTPError"):
        agent_ops.preflight(config(tmp_path), FakeSecrets(), lambda m: None, base_url=base)


def test_preflight_fails_when_the_bearer_token_differs(tmp_path, tool_server):
    base = tool_server(token="another-token")
    with pytest.raises(OperatorError):
        agent_ops.preflight(config(tmp_path), FakeSecrets(), lambda m: None, base_url=base)


def test_preflight_notices_a_pinned_demo_date_and_a_missing_label(tmp_path):
    class Opener:
        def __init__(self):
            self.n = 0

        def __call__(self, request, timeout=None):
            url = request.full_url
            if request.get_header("X-retell-signature") is None:
                raise urllib.error.HTTPError(url, 401 if url.endswith("check_slot") else 404, "x", {}, io.BytesIO(b""))
            body = {"status": "unknown", "ok": False, "speak": "x"}  # no source label, and not the scripted answers
            return FakeResponse(json.dumps(body).encode())

    with pytest.raises(OperatorError) as info:
        agent_ops.preflight(config(tmp_path), FakeSecrets(), lambda m: None, base_url="http://127.0.0.1:1", opener=Opener())
    assert "does not say it is the demo calendar" in str(info.value) and "WITHOUT --today" in str(info.value)


def test_preflight_catches_an_accepted_unsigned_request_and_a_booking_route(tmp_path):
    class Permissive:
        def __call__(self, request, timeout=None):
            payload = {"status": "available", "ok": False, "speak": "x", "source": "demo_calendar", "unavailable_reason": "occupied"}
            return FakeResponse(json.dumps(payload).encode())

    with pytest.raises(OperatorError) as info:
        agent_ops.preflight(config(tmp_path), FakeSecrets(), lambda m: None, base_url="http://127.0.0.1:1", opener=Permissive())
    assert "unsigned request to check_slot was accepted" in str(info.value) and "a booking route answered" in str(info.value)


@pytest.mark.parametrize("bad", ["http://example.com", "ftp://127.0.0.1", "example.com"])
def test_preflight_only_checks_loopback_or_https(tmp_path, bad):
    with pytest.raises(OperatorError, match="loopback address or an https address"):
        agent_ops.preflight(config(tmp_path), FakeSecrets(), lambda m: None, base_url=bad)


def test_the_signature_helper_matches_the_scheme_the_server_verifies():
    from aria_booking.voice.retell_http import verify_signature

    raw = b'{"a": 1}'
    assert verify_signature(raw, sign_body(raw, KEY, 1_790_000_000_000), KEY, now_ms=1_790_000_000_000)
    assert not verify_signature(raw, sign_body(raw, "other", 1_790_000_000_000), KEY, now_ms=1_790_000_000_000)


# ---------------------------------------------------------------- the isolated voice page


def _prepared(tmp_path, secrets=None, **kw):
    create(tmp_path)
    lines = []
    fixed = datetime(2026, 10, 13, 12, tzinfo=ZoneInfo("America/New_York")).timestamp()
    code = agent_ops.prepare_web(config(tmp_path, **kw), secrets or FakeSecrets(), lines.append, clock=lambda: fixed)
    return code, tmp_path / "state" / "web", lines


def test_the_page_is_built_from_the_template_with_the_banner_and_a_public_only_config(tmp_path):
    code, web, lines = _prepared(tmp_path)
    assert code == 0 and "Isolated voice page prepared" in lines[0] and any("python -m http.server" in line for line in lines)
    page = (web / "index.html").read_text(encoding="utf-8")
    assert "<title>Aria — Simulated Calendar Demo</title>" in page and 'data-testid="demo-page-banner"' in page
    assert "Demo calendar: simulated availability · Booking disabled" in page and "http://localhost:8790/pro/en-us/9000001/calendar?date=2026-10-14&amp;view=day" in page
    assert page.index("<body>") < page.index('data-testid="demo-page-banner"') < page.index('<header class="site-header">')
    assert "Nothing is recorded by this page." not in page and "up to 7 days" in page
    assert "up to 7 days" in (web / "retell-voice.js").read_text(encoding="utf-8") and "Nothing is recorded by this page." not in (web / "retell-voice.js").read_text(encoding="utf-8")
    config_js = (web / "demo-config.js").read_text(encoding="utf-8")
    assert json.loads(config_js[config_js.index("{"):config_js.rindex("}") + 1]) == {"publicKey": PUBLIC, "agentId": "agent_1", "agentVersion": 0}
    for secret in (KEY, TOKEN):
        assert all(secret not in p.read_text(encoding="utf-8") for p in web.glob("*"))


def test_the_template_files_in_the_repository_are_not_modified(tmp_path):
    before = {n: (REPO / n).read_bytes() for n in ("index.html", "retell-voice.js", "avatar.js", "styles.css", "demo-config.example.js")}
    _prepared(tmp_path)
    assert {n: (REPO / n).read_bytes() for n in before} == before and not (REPO / "demo-config.js").exists()


def test_the_page_refuses_a_private_key_in_the_public_slot_and_a_missing_agent(tmp_path):
    create(tmp_path)
    with pytest.raises(OperatorError, match="must be a Retell PUBLIC key"):
        agent_ops.prepare_web(config(tmp_path), FakeSecrets(retell_public_key="key_private_looking"), lambda m: None)
    assert not (tmp_path / "state" / "web" / "demo-config.js").exists()
    with pytest.raises(OperatorError, match="no created agent"):
        agent_ops.prepare_web(config(tmp_path / "other"), FakeSecrets(), lambda m: None)
    with pytest.raises(SecretMissing):
        agent_ops.prepare_web(config(tmp_path), FakeSecrets(retell_public_key=""), lambda m: None)


def test_a_changed_website_template_is_refused_rather_than_silently_half_patched(tmp_path):
    create(tmp_path)
    template = tmp_path / "template"
    template.mkdir()
    for name in ("retell-voice.js", "avatar.js", "styles.css"):
        (template / name).write_text("x", encoding="utf-8")
    (template / "index.html").write_text("<html><body>no microphone note</body></html>", encoding="utf-8")
    with pytest.raises(OperatorError, match="changed shape"):
        agent_ops.prepare_web(config(tmp_path, template_dir=template), FakeSecrets(), lambda m: None)
    (template / "avatar.js").unlink()
    with pytest.raises(OperatorError, match="missing avatar.js"):
        agent_ops.prepare_web(config(tmp_path, template_dir=template), FakeSecrets(), lambda m: None)


def test_the_web_folder_and_the_calendar_port_are_configurable(tmp_path):
    out = tmp_path / "elsewhere"
    code, _web, _ = _prepared(tmp_path, web_dir=out, calendar_port=9911)
    assert code == 0 and "localhost:9911" in (out / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- call summary: tool events only


def test_the_call_summary_keeps_tool_events_and_timings_but_never_the_transcript(tmp_path):
    _code, fake, _ = create(tmp_path)
    lines = []
    assert agent_ops.call_summary(config(tmp_path), FakeSecrets(), api(fake), lines.append) == 0
    saved = (tmp_path / "state" / "last-call-summary.json").read_text(encoding="utf-8")
    assert "private words" not in saved and "SHOULD NOT BE KEPT" not in saved and "private words" not in "\n".join(lines)
    events = json.loads(saved)["tool_events"]
    assert [e["role"] for e in events] == ["tool_call_invocation", "tool_call_result"] and events[0]["time_sec"] == 4.2


# ---------------------------------------------------------------- secrets


def test_environment_secrets_are_read_but_never_exposed_in_messages():
    secrets = EnvSecrets({"ARIA_RETELL_API_KEY": "  abc  "})
    assert secrets.get("retell_api_key") == "abc" and secrets.has("retell_api_key") and not secrets.has("tool_token")
    with pytest.raises(SecretMissing) as info:
        secrets.get("tool_token")
    assert "ARIA_RETELL_TOOL_TOKEN is not set" in str(info.value)


def test_the_provider_is_chosen_by_configuration_not_by_guessing(tmp_path):
    assert isinstance(provider_from_environment({}), EnvSecrets)
    assert isinstance(provider_from_environment({"ARIA_SECRET_DIR": str(tmp_path)}), DpapiSecrets)


def _fake_crypt(raw: bytes, encrypt: bool) -> bytes:
    return b"ENC:" + raw[::-1] if encrypt else raw[4:][::-1]


def test_encrypted_file_secrets_never_overwrite_and_are_not_plaintext_on_disk(tmp_path):
    secrets = DpapiSecrets(tmp_path / "vault", crypt=_fake_crypt)
    path = secrets.put("retell_api_key", " the-key ")
    assert b"the-key" not in path.read_bytes() and secrets.get("retell_api_key") == "the-key"
    with pytest.raises(FileExistsError):
        secrets.put("retell_api_key", "another")
    assert secrets.get("retell_api_key") == "the-key"
    with pytest.raises(SecretMissing):
        secrets.put("tool_token", "   ")


def test_the_tool_token_is_generated_once_and_then_stable(tmp_path):
    secrets = DpapiSecrets(tmp_path / "vault", crypt=_fake_crypt)
    first = secrets.ensure_token()
    assert len(first) >= 40 and secrets.ensure_token() == first


def test_a_missing_encrypted_file_says_how_to_store_it(tmp_path):
    with pytest.raises(SecretMissing, match="store-secret retell_api_key"):
        DpapiSecrets(tmp_path, crypt=_fake_crypt).get("retell_api_key")


def test_store_secret_reads_the_value_from_a_hidden_prompt_and_prints_no_value(tmp_path):
    secrets, lines = DpapiSecrets(tmp_path / "vault", crypt=_fake_crypt), []
    assert agent_ops.store_secret("retell_public_key", secrets, lambda label: PUBLIC, lines.append) == 0
    assert PUBLIC not in "\n".join(lines) and secrets.get("retell_public_key") == PUBLIC
    with pytest.raises(OperatorError, match="name must be one of"):
        agent_ops.store_secret("something_else", secrets, lambda label: "x", lines.append)
    with pytest.raises(OperatorError, match="needs ARIA_SECRET_DIR"):
        agent_ops.store_secret("retell_api_key", EnvSecrets({}), lambda label: "x", lines.append)


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows credential protection")
def test_windows_credential_protection_round_trips_and_hides_the_value(tmp_path):
    secrets = DpapiSecrets(tmp_path / "vault")
    path = secrets.put("tool_token", "round-trip-value-123")
    assert b"round-trip-value-123" not in path.read_bytes() and secrets.get("tool_token") == "round-trip-value-123"


# ---------------------------------------------------------------- the command line


def test_every_action_is_wired_and_unknown_ones_are_not():
    parser = cli.build_parser()
    for action in ACTIONS:
        assert parser.parse_args(["demo-agent", action]).action == action
    with pytest.raises(SystemExit):
        parser.parse_args(["demo-agent", "start-call"])


def test_run_reports_refusals_with_exit_2_and_no_secret(tmp_path):
    lines = []
    code = run("create", {}, lines.append, overrides={"tunnel_url": TUNNEL, "state_dir": str(tmp_path / "s")}, secrets=FakeSecrets())
    assert code == 2 and "--confirm-create" in lines[0] and KEY not in "\n".join(lines)


def test_the_plan_command_runs_end_to_end_through_the_cli_without_any_environment(tmp_path, monkeypatch):
    for name in ("ARIA_RETELL_API_KEY", "ARIA_RETELL_TOOL_TOKEN", "ARIA_RETELL_PUBLIC_KEY", "ARIA_SECRET_DIR", "ARIA_DEMO_TUNNEL_URL", "ARIA_DEMO_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    lines = []
    assert cli.main(["demo-agent", "plan", "--tunnel-url", TUNNEL, "--state-dir", str(tmp_path / "s")], environ={}, out=lines.append) == 0
    assert TUNNEL in "\n".join(lines) and cli.main(["demo-agent", "create", "--tunnel-url", TUNNEL, "--state-dir", str(tmp_path / "s")], environ={}, out=lines.append) == 2


# ---------------------------------------------------------------- packaging: what is, and is not, in Git


def _tracked_and_new(root: Path) -> list[str]:
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return [line for line in result.stdout.splitlines() if line]


def test_no_secret_runtime_state_or_local_file_would_be_committed():
    files = _tracked_and_new(REPO)
    banned = [f for f in files if f.endswith((".dpapi", ".log")) or "/.local/" in "/" + f or f.endswith("demo-config.js") or "llm-create-pending" in f
              or f.endswith(("agent.json", "preflight.json", "configuration-verified.json", "last-call-summary.json"))]
    assert banned == [], banned


def test_the_new_operator_source_has_no_machine_specific_or_secret_looking_text():
    sources = [BOOKING / "aria_booking" / "demo" / name for name in ("agent_ops.py", "secret_store.py")] + [BOOKING / "demo_agent.example.json"]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:\\Users|[A-Za-z]:/Users|/home/\w+|trycloudflare\.com|agent_[0-9a-f]{20}|llm_[0-9a-f]{20}|public_key_[A-Za-z0-9]{10}", text), path.name
        assert not re.search(r"\bkey_[A-Za-z0-9]{16,}|Bearer [A-Za-z0-9_-]{20,}", text), path.name


def test_the_git_ignore_rules_cover_the_places_the_operator_writes():
    ignored = subprocess.run(["git", "check-ignore", "-v",
                              "booking/.local/demo-agent/agent.json", "booking/.local/demo-agent/web/demo-config.js", "demo-config.js", "booking/.local/secrets/retell-api-key.dpapi"],
                             cwd=REPO, capture_output=True, text=True, timeout=60)
    if ignored.returncode not in (0, 1) or "fatal" in ignored.stderr:
        pytest.skip("not a git checkout")
    assert ignored.stdout.count("\n") == 4, ignored.stdout


def test_the_runbook_documents_every_input_and_every_intentionally_local_item():
    text = (BOOKING / "DEMO_CALENDAR.md").read_text(encoding="utf-8")
    for needle in ("ARIA_RETELL_API_KEY", "ARIA_RETELL_TOOL_TOKEN", "ARIA_RETELL_PUBLIC_KEY", "ARIA_SECRET_DIR", "ARIA_DEMO_TUNNEL_URL", "ARIA_DEMO_CONFIG",
                   "demo-agent plan", "--confirm-create", "prepare-web", "preflight", "Intentionally local", "demo_agent.example.json", "Not verified"):
        assert needle in text, needle


def test_nothing_in_the_operator_runs_a_server_a_tunnel_or_a_browser():
    for name in ("agent_ops.py", "secret_store.py"):
        source = (BOOKING / "aria_booking" / "demo" / name).read_text(encoding="utf-8")
        for needle in ("cloudflared", "Popen", "serve_forever", "playwright", "selenium", "webbrowser", "os.system"):
            assert needle not in source, (name, needle)


# ---------------------------------------------------------------- the one address that works with the Retell public key


def test_the_voice_page_address_is_localhost_never_the_numeric_loopback_address():
    assert OperatorConfig().page_url == "http://localhost:5174/"
    assert OperatorConfig(page_port=6000).page_url == "http://localhost:6000/"
    assert load_config({}, overrides={"page_port": 6100}).page_url == "http://localhost:6100/"
    assert "127.0.0.1" not in OperatorConfig().page_url


def test_the_example_config_names_the_page_port_and_the_loader_accepts_it(tmp_path):
    cfg = load_config({}, file=str(BOOKING / "demo_agent.example.json"))
    assert cfg.page_port == 5174 and cfg.page_url == "http://localhost:5174/"


def test_prepare_web_prints_the_serve_command_and_the_localhost_address(tmp_path):
    code, web, lines = _prepared(tmp_path, page_port=5999)
    text = "\n".join(lines)
    assert code == 0 and f"python -m http.server 5999 --bind 127.0.0.1 --directory {web}" in text
    assert "Open it at http://localhost:5999/" in text and "NOT 127.0.0.1" in text


def test_prepare_web_writes_test_instructions_beside_the_state_not_inside_the_served_folder(tmp_path):
    _code, web, _lines = _prepared(tmp_path)
    steps = (tmp_path / "state" / "test-instructions.txt").read_text(encoding="utf-8")
    assert "test-instructions.txt" not in {p.name for p in web.iterdir()}, "the steps are not served with the page"
    assert "http://localhost:5174/" in steps and "Do NOT use http://127.0.0.1:5174/" in steps and "domain error" in steps
    assert "http://localhost:8790/pro/en-us/9000001/calendar?date=2026-10-14&view=day&staffers=working" in steps
    assert "Microdermabrasion Facial" in steps and "Is Lily available tomorrow at 4" in steps
    assert "Is this schedule real?" in steps and "The greeting names Serenity Spa" in steps
    assert "after midnight Eastern, restart the server (not the tunnel or the agent)" in steps
    for secret in (KEY, TOKEN, PUBLIC):
        assert secret not in steps


def test_the_instructions_follow_the_configured_ports(tmp_path):
    _prepared(tmp_path, page_port=6200, calendar_port=9911)
    steps = (tmp_path / "state" / "test-instructions.txt").read_text(encoding="utf-8")
    assert "http://localhost:6200/" in steps and "localhost:9911/pro/en-us/9000001/calendar" in steps and "5174" not in steps


def test_plan_shows_the_page_address(tmp_path):
    lines = []
    assert agent_ops.plan(config(tmp_path, page_port=5300), FakeSecrets(), lines.append) == 0
    assert "open it at http://localhost:5300/ (localhost, not 127.0.0.1)" in "\n".join(lines)


def test_the_cli_passes_the_page_port_through(tmp_path):
    lines = []
    assert cli.main(["demo-agent", "plan", "--tunnel-url", TUNNEL, "--state-dir", str(tmp_path / "s"), "--page-port", "5888"], environ={}, out=lines.append) == 0
    assert "http://localhost:5888/" in "\n".join(lines)
