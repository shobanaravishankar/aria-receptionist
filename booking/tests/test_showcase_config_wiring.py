"""One optional file (ARIA_SHOWCASE_CONFIG) holds the fictional address and phone, and every place that shows or says them reads it through ONE resolver: the page, the spoken fallback,
the voice prompt and the check that the live agent matches. Unset stays unset. Fictional values only (a reserved 555-01xx number and an obviously made-up street)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from aria_booking.appointments.contact import contact_phrase
from aria_booking.catalog.render import LIFECYCLE_TEMPLATE_FILE, build_demo_prompt
from aria_booking.demo import agent_ops
from aria_booking.demo.agent_ops import (
    LIFECYCLE_BOOKING_NOTE, MODES, OperatorConfig, OperatorError, State, expected_prompt, load_config,
)
from aria_booking.showcase import SHOWCASE
from aria_booking.showcase.config import ENV_VAR, Showcase, load_showcase, resolve_showcase
from aria_booking.showcase.site import build_site

from test_demo_agent_ops import KEY, PUBLIC, TOKEN, FakeSecrets, api, config, create

BOOKING = Path(__file__).resolve().parent.parent
DRAFT = BOOKING / "voice_agent_draft"
PHONE, SPOKEN = "(212) 555-0111", "2 1 2, 5 5 5, 0 1 1 1"
LOCATION = "17 Example Row, Testville"
SETTINGS = {"publicKey": "public_key_test", "agentId": "agent_test", "agentVersion": 0}
MODE_NAMES = sorted(MODES)


def write_config(directory: Path, _filename: str = "showcase.json", **values) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _filename
    path.write_text(json.dumps(values), encoding="utf-8")
    return str(path)


@pytest.fixture
def configured(tmp_path) -> str:
    return write_config(tmp_path / "cfg", location=LOCATION, contact_phone=PHONE)


def page_of(showcase: Showcase, directory: Path) -> str:
    build_site(directory, showcase=showcase, settings=SETTINGS)
    return (directory / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- the one resolver


def test_with_no_file_nothing_is_configured_and_only_the_lifecycle_label_differs():
    plain, lifecycle = resolve_showcase({}), resolve_showcase({}, lifecycle=True)
    assert plain == Showcase() == SHOWCASE and plain.location is None and plain.contact_phone is None
    assert lifecycle.booking_note == LIFECYCLE_BOOKING_NOTE and lifecycle.location is None and lifecycle.contact_phone is None
    assert Showcase().booking_note != LIFECYCLE_BOOKING_NOTE
    assert resolve_showcase({ENV_VAR: "   "}) == plain, "a blank variable is no file"


def test_the_file_is_found_by_the_variable_or_by_an_explicit_path_and_the_path_wins(tmp_path, configured):
    other = write_config(tmp_path / "other", location="9 Other Lane, Testville")
    assert resolve_showcase({ENV_VAR: configured}).location == LOCATION and resolve_showcase({ENV_VAR: configured}).contact_phone == PHONE
    assert resolve_showcase({ENV_VAR: configured}, path=other).location == "9 Other Lane, Testville" and resolve_showcase({ENV_VAR: configured}, path=other).contact_phone is None
    assert resolve_showcase(path=configured, lifecycle=True).booking_note == LIFECYCLE_BOOKING_NOTE


def test_a_file_may_still_choose_its_own_label_ending_and_other_fields_are_kept(tmp_path):
    path = write_config(tmp_path, booking_note="Booked here, kept here", tagline="Slow down.")
    shown = resolve_showcase({ENV_VAR: path}, lifecycle=True)
    assert shown.booking_note == "Booked here, kept here" and shown.tagline == "Slow down." and shown.name == "Serenity Spa"


def test_null_and_blank_values_stay_unset_and_the_committed_example_file_is_inert(tmp_path):
    blank = write_config(tmp_path, location="   ", contact_phone="", contact_name=None)
    shown = resolve_showcase({ENV_VAR: blank})
    assert shown.location is None and shown.contact_phone is None and shown.contact_name is None
    example = resolve_showcase({ENV_VAR: str(BOOKING / "showcase.example.json")})
    assert example == Showcase(), "copying the example without filling it in configures nothing"


def test_values_are_trimmed_and_a_plus_country_number_is_accepted(tmp_path):
    shown = resolve_showcase({ENV_VAR: write_config(tmp_path, location="  17 Example Row  ", contact_phone=" +44 20 7946 0958 ")})
    assert shown.location == "17 Example Row" and shown.contact_phone == "+44 20 7946 0958"


@pytest.mark.parametrize("values, fragment", [
    ({"contact_phone": "555-0111"}, "not a complete phone number"),
    ({"contact_phone": "(212) 555-01xx"}, "not a complete phone number"),
    ({"contact_phone": 2125550111}, "contact_phone must be text"),
    ({"location": "17 Example Row\nTestville"}, "location must be one line"),
    ({"location": "x" * 121}, "location must be one line"),
    ({"location": 17}, "location must be text"),
    ({"contact_name": "a\tb"}, "contact_name must be one line"),
    ({"name": "  "}, "name must be text"),
    ({"tagline": 5}, "tagline must be text"),
    ({"opening": "10 to 8"}, "unknown showcase keys: opening"),
])
def test_a_bad_value_stops_everything_with_the_reason_and_never_echoes_the_value(tmp_path, values, fragment):
    with pytest.raises(ValueError, match=fragment) as caught:
        resolve_showcase({ENV_VAR: write_config(tmp_path, **values)})
    assert "555" not in str(caught.value) and "Testville" not in str(caught.value)


def test_an_unreadable_or_non_object_file_stops_with_a_reason_that_names_no_path(tmp_path):
    with pytest.raises(ValueError, match="could not be read") as caught:
        resolve_showcase({ENV_VAR: str(tmp_path / "missing.json")})
    assert str(tmp_path) not in str(caught.value)
    (tmp_path / "list.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError, match="one JSON object"):
        resolve_showcase({ENV_VAR: str(tmp_path / "list.json")})
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="could not be read"):
        resolve_showcase({ENV_VAR: str(tmp_path / "broken.json")})


def test_load_showcase_keeps_its_old_behaviour_for_callers_that_pass_overrides_directly():
    assert load_showcase(overrides={"contact_phone": "+1 555 010 0199"}).contact_phone == "+1 555 010 0199"
    assert load_showcase() == Showcase()


# ---------------------------------------------------------------- the page


def test_the_page_shows_the_address_and_a_phone_link_when_set_and_says_so_plainly_when_not(tmp_path, configured):
    html = page_of(resolve_showcase({ENV_VAR: configured}), tmp_path / "a")
    assert html.count(f"<p>Address: {LOCATION}</p>") == 1 and html.count(f'<p>Phone: <a href="tel:2125550111">{PHONE}</a></p>') == 1
    assert "Contact details are not configured" not in html
    default = page_of(Showcase(), tmp_path / "b")
    assert "Contact details are not configured in this prototype." in default and "Address:" not in default and "Phone:" not in default and LOCATION not in default


def test_only_an_address_or_only_a_phone_shows_just_that_and_not_the_not_configured_sentence(tmp_path):
    only_address = page_of(resolve_showcase({ENV_VAR: write_config(tmp_path / "x", location=LOCATION)}), tmp_path / "a")
    assert f"<p>Address: {LOCATION}</p>" in only_address and "Phone:" not in only_address and "Contact details are not configured" not in only_address
    only_phone = page_of(resolve_showcase({ENV_VAR: write_config(tmp_path / "y", contact_phone=PHONE)}), tmp_path / "b")
    assert "Phone:" in only_phone and "Address:" not in only_phone and "Contact details are not configured" not in only_phone


def test_the_address_is_escaped_on_the_page(tmp_path):
    html = page_of(resolve_showcase({ENV_VAR: write_config(tmp_path / "e", location='1 <b>Row</b> & "Co"')}), tmp_path / "a")
    assert "1 &lt;b&gt;Row&lt;/b&gt; &amp; &quot;Co&quot;" in html and "<b>Row</b>" not in html


# ---------------------------------------------------------------- the prompt


def render(mode: str, showcase: Showcase | None = None) -> str:
    template = (DRAFT / (LIFECYCLE_TEMPLATE_FILE if mode != "availability" else "prompt_template.md")).read_text(encoding="utf-8")
    return build_demo_prompt(template, showcase=showcase, lifecycle=mode != "availability", preferences=mode == "lifecycle-preferences")


@pytest.mark.parametrize("mode", MODE_NAMES)
def test_the_facts_block_gives_the_configured_address_and_phone_exactly_once_and_nothing_else_changes(mode, configured):
    shown = resolve_showcase({ENV_VAR: configured}, lifecycle=mode != "availability")
    prompt, default = render(mode, shown), render(mode)
    assert prompt.count(f"- Address and location: {LOCATION}.") == 1 and prompt.count(f"- Phone: {PHONE}.") == 1
    assert prompt.count(LOCATION) == 1 and prompt.count(PHONE) == 1, "only in the facts block"
    assert "Address and location: none is configured" not in prompt and "no number is configured" not in prompt
    assert "Address and location: none is configured" in default and "no number is configured yet" in default and LOCATION not in default and PHONE not in default
    changed = [line for line in prompt.splitlines() if line not in default.splitlines()]
    assert len(changed) == 2, "exactly the address line and the phone line differ"


@pytest.mark.parametrize("mode", MODE_NAMES)
def test_the_committed_prompts_are_the_unconfigured_ones(mode):
    assert (BOOKING / MODES[mode]["prompt"]).read_text(encoding="utf-8") == render(mode) == render(mode, Showcase() if mode == "availability" else resolve_showcase({}, lifecycle=True))


def test_the_address_is_said_as_written_with_no_directions_of_its_own(configured):
    text = " ".join(render("lifecycle", resolve_showcase({ENV_VAR: configured}, lifecycle=True)).split())
    assert "Give it exactly as written when a caller asks where the salon is; add no directions, town, landmark, map or travel time of your own" in text


# ---------------------------------------------------------------- the operator holds the agent to the same file


def test_the_operator_reads_the_file_from_its_config_or_the_environment(tmp_path, configured):
    assert load_config({ENV_VAR: configured}).showcase_file == configured and load_config({}).showcase_file == ""
    filed = tmp_path / "agent.json"
    filed.write_text(json.dumps({"showcase_file": configured}), encoding="utf-8")
    assert load_config({}, file=str(filed)).showcase_file == configured
    with pytest.raises(OperatorError, match="unknown config keys"):
        (tmp_path / "bad.json").write_text(json.dumps({"showcase": configured}), encoding="utf-8")
        load_config({}, file=str(tmp_path / "bad.json"))


@pytest.mark.parametrize("mode", MODE_NAMES)
def test_the_expected_prompt_is_the_committed_one_without_a_file_and_the_rendered_one_with_it(mode, configured):
    assert expected_prompt(mode=mode) == expected_prompt(mode=mode, showcase=None)
    cfg = OperatorConfig(mode=mode, showcase_file=configured)
    rendered = expected_prompt(mode=mode, showcase=cfg.prompt_showcase())
    assert rendered != expected_prompt(mode=mode) and LOCATION in rendered and PHONE in rendered and rendered.endswith(agent_ops.CLOCK_SECTION)
    assert OperatorConfig(mode=mode).prompt_showcase() is None


def test_plan_says_whether_an_address_and_phone_are_configured_without_printing_them(tmp_path, configured):
    default, shown = [], []
    agent_ops.plan(config(tmp_path / "a", mode="lifecycle"), FakeSecrets(), default.append)
    agent_ops.plan(config(tmp_path / "b", mode="lifecycle", showcase_file=configured), FakeSecrets(), shown.append)
    assert "showcase          : default (no address and no phone number configured)" in "\n".join(default)
    assert "showcase          : from the configured file (address set, phone set)" in "\n".join(shown)
    assert LOCATION not in "\n".join(shown) and PHONE not in "\n".join(shown) and "555-0111" not in "\n".join(shown) and KEY not in "\n".join(shown)
    sha = lambda lines: re.search(r"sha256 (\w+)", "\n".join(lines)).group(1)  # noqa: E731
    assert sha(default) != sha(shown), "the agent is held to a different prompt once an address and phone are configured"


def test_an_unreadable_file_stops_the_operator_with_the_setting_named(tmp_path):
    cfg = config(tmp_path, showcase_file=str(tmp_path / "missing.json"))
    with pytest.raises(OperatorError, match="showcase_file: the showcase file could not be read"):
        agent_ops.plan(cfg, FakeSecrets(), lambda m: None)


def test_create_holds_the_agent_to_the_configured_prompt_and_verify_notices_a_mismatch_either_way(tmp_path, configured):
    secrets = FakeSecrets()
    code, fake, _ = create(tmp_path, mode="lifecycle", showcase_file=configured)
    assert code == 0
    llm = next(iter(fake.llms.values()))
    assert llm["general_prompt"] == expected_prompt(mode="lifecycle", showcase=OperatorConfig(mode="lifecycle", showcase_file=configured).prompt_showcase())
    assert LOCATION in llm["general_prompt"] and PHONE in llm["general_prompt"]
    assert agent_ops.verify(config(tmp_path, mode="lifecycle", showcase_file=configured), secrets, api(fake, secrets), lambda m: None) == 0
    with pytest.raises(OperatorError, match="the prompt differs"):
        agent_ops.verify(config(tmp_path, mode="lifecycle"), secrets, api(fake, secrets), lambda m: None)
    _code, plain, _ = create(tmp_path / "plain", mode="lifecycle")
    with pytest.raises(OperatorError, match="the prompt differs"):
        agent_ops.verify(config(tmp_path / "plain", mode="lifecycle", showcase_file=configured), secrets, api(plain, secrets), lambda m: None)
    for path in (tmp_path / "state").glob("*"):
        assert KEY not in path.read_text(encoding="utf-8") and TOKEN not in path.read_text(encoding="utf-8")


def test_update_sends_the_configured_prompt_to_the_existing_agent_in_place(tmp_path, configured):
    _code, fake, _ = create(tmp_path, mode="lifecycle")  # the existing agent: no address, no phone
    fake.calls.clear()
    secrets, lines = FakeSecrets(), []
    assert agent_ops.update(config(tmp_path, mode="lifecycle", showcase_file=configured), secrets, api(fake, secrets), lines.append, confirm=True) == 0
    patches = [c for c in fake.calls if c[0] == "PATCH"]
    assert len(patches) == 1 and LOCATION in patches[0][2]["general_prompt"] and PHONE in patches[0][2]["general_prompt"]
    assert patches[0][2]["general_prompt"] == expected_prompt(mode="lifecycle", showcase=resolve_showcase(path=configured, lifecycle=True))
    assert len(fake.llms) == 1 and len(fake.agents) == 1


@pytest.mark.parametrize("mode", MODE_NAMES)
def test_the_page_the_prompt_and_the_spoken_fallback_all_come_from_the_same_two_values(tmp_path, configured, mode):
    cfg = config(tmp_path, mode=mode, showcase_file=configured)
    State(tmp_path / "state").write("agent.json", {"agent_id": "agent_test", "version": 0})
    assert agent_ops.prepare_web(cfg, FakeSecrets(), lambda m: None) == 0
    page = (cfg.web_out / "index.html").read_text(encoding="utf-8")
    prompt = expected_prompt(mode=mode, showcase=cfg.prompt_showcase())
    phrase = contact_phrase(cfg.resolved_showcase())
    assert LOCATION in page and PHONE in page and LOCATION in prompt and PHONE in prompt and phrase == f"contact Serenity Spa at {SPOKEN}"
    assert 'data-contact-tel="+12125550111"' in page and f'data-contact-phone="{PHONE}"' in page, "the widget's click-to-call notice carries the same number"
    assert (cfg.web_out / "index.html").read_text(encoding="utf-8").count(LOCATION) == 1
    text = (tmp_path / "state" / "test-instructions.txt").read_text(encoding="utf-8")
    assert LOCATION not in text and PHONE not in text and "555-0111" not in text, "the operator's step sheet carries neither"
    assert KEY not in page and TOKEN not in page


def test_without_a_file_the_prepared_page_is_the_unconfigured_one_in_every_mode(tmp_path):
    for mode in MODE_NAMES:
        directory = tmp_path / mode
        State(directory / "state").write("agent.json", {"agent_id": "agent_test", "version": 0})
        cfg = config(directory, mode=mode)
        assert agent_ops.prepare_web(cfg, FakeSecrets(), lambda m: None) == 0
        page = (cfg.web_out / "index.html").read_text(encoding="utf-8")
        assert "Contact details are not configured in this prototype." in page and "Address:" not in page and "data-contact" not in page
        assert (LIFECYCLE_BOOKING_NOTE in page) == (mode != "availability")
    assert PUBLIC not in page
