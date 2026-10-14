"""The one change in place: ``demo-agent update`` brings the EXISTING test agent's LLM in line with the reviewed source (prompt, tools, opening line).

A fake Retell and fake secrets only. No account, no network, no call. It never creates an agent or an LLM, never touches the agent itself, and is safe to repeat.
"""

from __future__ import annotations

import pytest

from aria_booking import cli
from aria_booking.demo import agent_ops
from aria_booking.demo.agent_ops import OperatorConfig, OperatorError, RetellApi, RetellApiError, State, expected_prompt, expected_tools, run
from aria_booking.demo.secret_store import SecretMissing

from test_demo_agent_ops import KEY, TOKEN, TUNNEL, FakeRetell, FakeSecrets, api, config, create


def _stale(tmp_path, field="general_prompt", value="an older reviewed prompt"):
    """An agent created earlier, whose LLM then differs from the current reviewed source (as after a prompt change)."""
    _code, fake, _ = create(tmp_path)
    fake.llms["llm_1"][field] = value
    fake.calls.clear()
    return fake


def _update(tmp_path, fake, *, confirm=True, **kw):
    lines = []
    secrets = FakeSecrets()
    code = agent_ops.update(config(tmp_path, **kw), secrets, api(fake, secrets), lines.append, confirm=confirm)
    return code, lines


def test_update_refuses_without_the_explicit_flag_and_touches_nothing(tmp_path):
    fake = _stale(tmp_path)
    with pytest.raises(OperatorError, match="--confirm-update"):
        _update(tmp_path, fake, confirm=False)
    assert fake.calls == [] and fake.updated == 0


def test_update_with_nothing_created_says_so_and_calls_nothing(tmp_path):
    fake = FakeRetell()
    with pytest.raises(OperatorError, match="run `create --confirm-create` first"):
        _update(tmp_path, fake)
    assert fake.calls == []


def test_update_sends_exactly_the_reviewed_prompt_tools_and_opening_line_to_the_recorded_llm_and_reads_back(tmp_path):
    fake = _stale(tmp_path)
    code, lines = _update(tmp_path, fake)
    assert code == 0 and "updated in place" in "\n".join(lines) and "no new agent, LLM or tunnel was created" in "\n".join(lines)
    patches = [c for c in fake.calls if c[0] == "PATCH"]
    assert len(patches) == 1 and patches[0][1] == "update-retell-llm/llm_1"
    assert sorted(patches[0][2]) == ["begin_message", "general_prompt", "general_tools"], "nothing else is sent: not the model, the voice, the agent"
    assert patches[0][2]["general_prompt"] == expected_prompt() and patches[0][2]["general_tools"] == expected_tools(TUNNEL, TOKEN)
    assert fake.llms["llm_1"]["general_prompt"] == expected_prompt()
    assert fake.created == 2 and len(fake.llms) == 1 and len(fake.agents) == 1, "no duplicate LLM or agent"
    assert not any(c[1].startswith(("create", "update-agent")) for c in fake.calls)
    assert [c[1] for c in fake.calls] == ["get-agent/agent_1", "get-retell-llm/llm_1", "update-retell-llm/llm_1", "get-agent/agent_1", "get-retell-llm/llm_1"], \
        "read first, change once, then the same read-back as create"


def test_update_records_what_it_did_and_never_a_secret(tmp_path):
    fake = _stale(tmp_path)
    _update(tmp_path, fake)
    state = State(tmp_path / "state")
    done = state.read("last-update.json")
    assert done["tunnel"] == TUNNEL and done["llm_version"] == 0 and done["llm_published"] is False and len(done["prompt_sha256"]) == 16
    assert state.exists("configuration-verified.json")
    for path in (tmp_path / "state").glob("*"):
        text = path.read_text(encoding="utf-8")
        assert KEY not in text and TOKEN not in text, path.name


def test_update_is_safe_to_repeat(tmp_path):
    fake = _stale(tmp_path)
    assert _update(tmp_path, fake)[0] == 0 and _update(tmp_path, fake)[0] == 0
    assert fake.updated == 2 and fake.created == 2


def test_update_reuses_the_recorded_tunnel_when_none_is_given_and_can_move_the_functions_to_a_new_one(tmp_path):
    fake = _stale(tmp_path)
    secrets = FakeSecrets()
    assert agent_ops.update(OperatorConfig(state_dir=tmp_path / "state"), secrets, api(fake, secrets), lambda m: None, confirm=True) == 0  # no address given
    assert all(t["url"].startswith(TUNNEL) for t in fake.llms["llm_1"]["general_tools"] if t["type"] == "custom")
    new = "https://new-tunnel.example"
    assert agent_ops.update(OperatorConfig(tunnel_url=new, state_dir=tmp_path / "state"), secrets, api(fake, secrets), lambda m: None, confirm=True) == 0
    assert all(t["url"].startswith(new) for t in fake.llms["llm_1"]["general_tools"] if t["type"] == "custom")
    assert State(tmp_path / "state").read("agent.json")["tunnel"] == new


def test_update_refuses_a_bad_tunnel_before_any_call(tmp_path):
    fake = _stale(tmp_path)
    with pytest.raises(OperatorError):
        agent_ops.update(OperatorConfig(tunnel_url="http://localhost", state_dir=tmp_path / "state"), FakeSecrets(), api(fake), lambda m: None, confirm=True)
    assert fake.calls == []


@pytest.mark.parametrize("missing", ["retell_api_key", "tool_token"])
def test_update_refuses_when_a_secret_is_missing_before_any_call_and_keeps_the_verified_record(tmp_path, missing):
    fake = _stale(tmp_path)
    with pytest.raises(SecretMissing):
        agent_ops.update(config(tmp_path), FakeSecrets(**{missing: ""}), api(fake), lambda m: None, confirm=True)
    assert fake.calls == [] and State(tmp_path / "state").exists("configuration-verified.json")


def test_a_refused_update_leaves_no_stale_verified_record_and_the_recorded_state_unchanged(tmp_path):
    fake = _stale(tmp_path)
    before = State(tmp_path / "state").read("agent.json")
    fake.fail["update-retell-llm/llm_1"] = 422
    with pytest.raises(RetellApiError) as info:
        _update(tmp_path, fake)
    assert info.value.definite is True and info.value.__cause__ is None
    state = State(tmp_path / "state")
    assert not state.exists("configuration-verified.json") and state.read("agent.json") == before and not state.exists("last-update.json")


def test_an_uncertain_update_says_to_verify_and_is_not_marked_verified(tmp_path):
    fake = _stale(tmp_path)
    fake.fail["update-retell-llm/llm_1"] = "network"
    with pytest.raises(OperatorError, match="outcome is uncertain: run `verify`"):
        _update(tmp_path, fake)
    assert not State(tmp_path / "state").exists("configuration-verified.json")
    fake.fail.clear()
    assert _update(tmp_path, fake)[0] == 0, "the next attempt simply repeats"


def test_an_update_that_the_account_does_not_apply_is_caught_by_the_read_back(tmp_path):
    fake = _stale(tmp_path)
    fake.ignore_updates = True
    before = State(tmp_path / "state").read("agent.json")
    secrets = FakeSecrets()
    with pytest.raises(OperatorError, match="does NOT match") as info:  # asked to move to a new address, which the account did not take
        agent_ops.update(OperatorConfig(tunnel_url="https://new-tunnel.example", state_dir=tmp_path / "state"), secrets, api(fake, secrets), lambda m: None, confirm=True)
    assert "prompt differs" in str(info.value)
    state = State(tmp_path / "state")
    assert not state.exists("configuration-verified.json") and not state.exists("last-update.json") and state.read("agent.json") == before


def test_update_never_changes_the_agent_itself(tmp_path):
    fake = _stale(tmp_path)
    agent_before = dict(fake.agents["agent_1"])
    _update(tmp_path, fake)
    assert fake.agents["agent_1"] == agent_before


def test_update_refuses_records_that_do_not_belong_together(tmp_path):
    fake = _stale(tmp_path)
    state = State(tmp_path / "state")
    state.write("agent.json", {**state.read("agent.json"), "llm_id": "llm_other"})
    with pytest.raises(OperatorError, match="do not belong together"):
        _update(tmp_path, fake)
    assert fake.calls == []


def test_the_client_allows_only_that_one_patch():
    client = RetellApi(lambda: KEY, opener=FakeRetell())
    for path, method in [("update-retell-llm/llm_1", "POST"), ("update-retell-llm/llm_1", "GET"), ("update-retell-llm/llm_1/x", "PATCH"),
                         ("update-retell-llm/", "PATCH"), ("update-agent/agent_1", "PATCH"), ("update-retell-llm/../create-agent", "PATCH")]:
        with pytest.raises(OperatorError):
            client.request(path, {} if method != "GET" else None, method)


def test_update_is_wired_into_the_command_line(tmp_path):
    parser = cli.build_parser()
    assert parser.parse_args(["demo-agent", "update", "--confirm-update"]).confirm_update is True
    assert parser.parse_args(["demo-agent", "update"]).confirm_update is False
    lines = []
    assert run("update", {}, lines.append, overrides={"state_dir": str(tmp_path / "s")}, secrets=FakeSecrets()) == 2 and "--confirm-update" in lines[0]
    lines.clear()
    assert run("update", {}, lines.append, overrides={"state_dir": str(tmp_path / "s")}, confirm_update=True, secrets=FakeSecrets()) == 2
    assert "run `create --confirm-create` first" in lines[0]
    lines.clear()
    assert cli.main(["demo-agent", "update", "--state-dir", str(tmp_path / "s")], environ={}, out=lines.append) == 2 and "refused:" in lines[0]


# ---------------------------------------------------------------- Sol's pre-apply checks and the report she asked for


def test_update_refuses_before_changing_anything_when_the_agent_no_longer_uses_the_recorded_llm(tmp_path):
    fake = _stale(tmp_path)
    fake.agents["agent_1"]["response_engine"] = {"type": "retell-llm", "llm_id": "llm_somewhere_else"}
    with pytest.raises(OperatorError, match="no longer uses the recorded LLM"):
        _update(tmp_path, fake)
    assert [c[1] for c in fake.calls] == ["get-agent/agent_1", "get-retell-llm/llm_1"] and fake.updated == 0
    assert State(tmp_path / "state").exists("configuration-verified.json"), "nothing was changed, so the earlier record is still what it was"


def test_update_shows_the_voice_model_and_other_settings_were_left_exactly_as_found(tmp_path):
    fake = _stale(tmp_path)
    code, lines = _update(tmp_path, fake)
    text = "\n".join(lines)
    assert code == 0 and "Left unchanged, as found:" in text
    for name in ("voice_id", "language", "model", "model_temperature", "max_call_duration_ms", "data_storage_retention_days", "contact_memory_config", "agent_name"):
        assert name in text.split("Left unchanged, as found:")[1], name


@pytest.mark.parametrize("field,value,expected", [("model", "some-other-model", "model"), ("model_temperature", 0.9, "model_temperature")])
def test_an_update_after_which_the_llm_settings_differ_is_reported_and_not_marked_verified(tmp_path, field, value, expected):
    fake = _stale(tmp_path)
    fake.on_update = lambda llm: llm.update({field: value})
    with pytest.raises(OperatorError, match="changed settings it must not touch") as info:
        _update(tmp_path, fake)
    assert expected in str(info.value)
    state = State(tmp_path / "state")
    assert not state.exists("configuration-verified.json") and not state.exists("last-update.json")


def test_an_update_after_which_the_agents_voice_differs_is_reported(tmp_path):
    fake = _stale(tmp_path)
    fake.on_update = lambda llm: fake.agents["agent_1"].update(voice_id="retell-Someone-Else")
    with pytest.raises(OperatorError, match="changed settings it must not touch.*voice_id"):
        _update(tmp_path, fake)


def test_the_binding_between_the_agent_and_the_llm_is_reported_with_what_is_and_is_not_known(tmp_path):
    fake = _stale(tmp_path)
    _code, lines = _update(tmp_path, fake)
    text = "\n".join(lines)
    assert "Binding: the agent uses the recorded LLM (engine version not stated, so the latest); that LLM is version 0, published: False." in text
    assert "does not say which version a web call with agent_version 0 uses" in text and "only a supervised call shows the new prompt is live" in text
    fake.agents["agent_1"]["response_engine"] = {"type": "retell-llm", "llm_id": "llm_1", "version": 3}
    _code, again = _update(tmp_path, fake)
    assert "engine version 3" in "\n".join(again)


def test_an_old_fixed_opening_message_is_cleared_by_the_update_and_checked_by_the_read_back(tmp_path):
    fake = _stale(tmp_path, field="begin_message", value="Hi, this is an old greeting.")
    _update(tmp_path, fake)
    patch = [c for c in fake.calls if c[0] == "PATCH"][0][2]
    assert "begin_message" in patch and patch["begin_message"] is None, "an explicit null clears it, so the model speaks first"
    assert fake.llms["llm_1"]["begin_message"] is None
    ignoring = _stale(tmp_path / "second", field="begin_message", value="Hi, this is an old greeting.")
    ignoring.ignore_updates = True
    with pytest.raises(OperatorError, match="the opening line"):
        _update(tmp_path / "second", ignoring)
