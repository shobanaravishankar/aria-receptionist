"""The floating Aria avatar, checked as text and files: its markup and styling, the built-in illustration, how an avatar image is supplied and refused, the operator
setting, and the guarantee that no personal likeness is ever in the public repository. The real-browser behaviour is in test_floating_aria_browser.py. No network."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aria_booking import cli
from aria_booking.demo import agent_ops
from aria_booking.demo.agent_ops import OperatorConfig, OperatorError, State, load_config
from aria_booking.showcase.site import ASSET_DIR, MAX_AVATAR_BYTES, SITE_DIR, build_site

from test_demo_agent_ops import KEY, PUBLIC, TOKEN, TUNNEL, FakeSecrets
from test_showcase_site import SETTINGS, Page

REPO = Path(__file__).resolve().parent.parent.parent


def without_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line.split("//")[0] if "://" not in line else line for line in text.splitlines())


VOICE_JS = without_comments((SITE_DIR / "voice.js").read_text(encoding="utf-8"))  # the code, not what the comments say about it
CSS = without_comments((SITE_DIR / "styles.css").read_text(encoding="utf-8"))
TINY_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="#0b3d3b"/></svg>'


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("aria-site")
    build_site(out, settings=SETTINGS)
    html = (out / "index.html").read_text(encoding="utf-8")
    page = Page()
    page.feed(html)
    return out, html, page


# ---------------------------------------------------------------- the markup


def test_the_widget_is_a_labelled_region_with_separate_call_hangup_and_hide_controls(built):
    _out, _html, page = built
    widget = page.find("aside", id="aria")[0]
    assert widget["role"] == "region" and widget["aria-label"] and widget["data-state"] == "idle"
    buttons = {a["id"]: a for t, a in page.tags if t == "button" and a.get("id", "").startswith("aria-")}
    assert set(buttons) == {"aria-call", "aria-hangup", "aria-hide"}
    assert "hidden" in buttons["aria-hangup"] and "hidden" not in buttons["aria-call"] and "hidden" not in buttons["aria-hide"]
    assert all(b["aria-label"] and b["title"] and b["type"] == "button" for b in buttons.values())
    assert "data-voice-start" in buttons["aria-call"] and "data-voice-start" not in buttons["aria-hangup"]


def test_the_status_is_a_polite_live_region_with_the_ai_and_retention_notice(built):
    _out, html, page = built
    status = page.find("p", id="voice-status")[0]
    assert status["role"] == "status" and status["aria-live"] == "polite" and "hidden" in status
    assert "Aria is an AI voice assistant. Retell may retain this test call for review for up to 7 days." in html


def test_no_modal_dialog_scrim_or_full_screen_card_is_left(built):
    _out, html, page = built
    assert 'role="dialog"' not in html and "aria-modal" not in html and "voice-panel" not in html and "scrim" not in html
    assert "voice-panel" not in CSS and "scrim" not in CSS and "voice-config" not in CSS
    assert "overflow" not in re.findall(r"document\.body\.style\.[a-z]+", VOICE_JS), "no scroll lock"


def test_the_avatar_picture_is_labelled_square_and_local(built):
    out, _html, page = built
    img = page.find("img", **{"data-testid": "aria-avatar"})[0]
    assert img["src"] == "assets/aria-avatar.svg" and img["alt"].startswith("Aria") and img["width"] == img["height"] and (out / img["src"]).is_file()


# ---------------------------------------------------------------- the styling


def test_the_widget_paints_no_card_of_its_own_and_motion_is_switched_off_for_reduced_motion():
    for selector in (".aria", ".aria__stage", ".aria__img"):
        rule = re.search(r"(?m)^" + re.escape(selector) + r" \{([^}]*)\}", CSS)
        assert rule and "background: transparent" in rule.group(1), selector
        assert "box-shadow" not in rule.group(1) and "backdrop-filter" not in rule.group(1)
    reduced = re.search(r"@media \(prefers-reduced-motion: reduce\) \{([^\n]*)", CSS).group(1)
    assert ".aria__dot" in reduced and ".aria__img" in reduced and "animation: none" in reduced


def test_the_controls_are_big_enough_to_touch_and_the_focus_ring_is_visible():
    sizes = [int(m) for m in re.findall(r"(?m)^\.aria-btn \{ width: (\d+)px; height: (\d+)px", CSS) for m in m]
    assert sizes and min(sizes) >= 52
    assert re.search(r"\.aria__hide \{[^}]*width: 44px; height: 44px", CSS)
    assert re.search(r"\.aria-btn:focus-visible[^{]*\{[^}]*outline: 3px solid", CSS)


# ---------------------------------------------------------------- the built-in illustration


def test_the_default_illustration_is_a_small_transparent_original_with_nothing_outside_it():
    path = ASSET_DIR / "aria-avatar.svg"
    assert path.stat().st_size < 6_000
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    ns = "{http://www.w3.org/2000/svg}"
    assert root.attrib["viewBox"] == "0 0 400 400"
    tags = {el.tag.replace(ns, "") for el in root.iter()}
    assert not tags & {"script", "image", "foreignObject", "style", "a"}
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"https?://(?!www\.w3\.org)", text) and "href" not in text.replace("xlink", ""), "no outside reference"
    full = [el for el in root.iter() if el.tag == ns + "rect" and el.attrib.get("width") in ("400", "100%") and el.attrib.get("height") in ("400", "100%")]
    assert full == [], "no background rectangle: the picture is transparent around the figure"


def test_no_personal_likeness_or_photograph_is_ever_in_the_public_repository():
    pictures = sorted(p.name for p in ASSET_DIR.iterdir() if p.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg", ".gif", ".avif"})
    assert pictures == ["diffuser-candles.jpg", "facial-teal.jpg", "hero-stones.jpg", "stones-hands.jpg"], "only the credited stock photographs are committed"
    assert "booking/.local/" in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines(), "a supplied avatar lives under the ignored local folder"
    assert not (REPO / "booking" / "showcase_evidence" / "aria-avatar.webp").exists()


# ---------------------------------------------------------------- supplying an avatar


@pytest.mark.parametrize("name,data", [("a.svg", TINY_SVG.encode()), ("b.webp", b"RIFF....WEBPVP8 "), ("c.png", b"\x89PNG\r\n\x1a\n" + b"0" * 40)])
def test_a_supplied_avatar_is_copied_in_and_used_by_the_page(tmp_path, name, data):
    source = tmp_path / name
    source.write_bytes(data)
    site = tmp_path / "site"
    build_site(site, settings=SETTINGS, avatar=source)
    suffix = source.suffix
    assert (site / "assets" / f"aria-avatar{suffix}").read_bytes() == data
    assert f'src="assets/aria-avatar{suffix}"' in (site / "index.html").read_text(encoding="utf-8")


def test_without_an_avatar_the_page_uses_the_built_in_illustration(tmp_path):
    build_site(tmp_path, settings=SETTINGS)
    assert 'src="assets/aria-avatar.svg"' in (tmp_path / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("name,problem", [("a.jpg", "png, .webp or .svg"), ("a.gif", "png, .webp or .svg"), ("missing.png", "not found")])
def test_an_unusable_avatar_is_refused_with_the_reason(tmp_path, name, problem):
    with pytest.raises(ValueError, match=problem):
        build_site(tmp_path / "site", settings=SETTINGS, avatar=tmp_path / name)


def test_an_oversized_avatar_is_refused_and_points_at_the_resize_command(tmp_path):
    big = tmp_path / "big.png"
    big.write_bytes(b"\x89PNG" + b"0" * (MAX_AVATAR_BYTES + 1))
    with pytest.raises(ValueError, match="showcase-avatar"):
        build_site(tmp_path / "site", settings=SETTINGS, avatar=big)


# ---------------------------------------------------------------- the operator setting


def test_the_operator_can_name_an_avatar_by_file_or_environment_and_the_page_is_built_with_it(tmp_path):
    source = tmp_path / "me.svg"
    source.write_text(TINY_SVG, encoding="utf-8")
    assert OperatorConfig().avatar_image == ""
    assert load_config({"ARIA_AVATAR_IMAGE": str(source)}).avatar_image == str(source)
    config_file = tmp_path / "demo.json"
    config_file.write_text('{"avatar_image": "' + str(source).replace("\\", "/") + '"}', encoding="utf-8")
    assert load_config({}, file=str(config_file)).avatar_image == str(source).replace("\\", "/")
    state = State(tmp_path / "state")
    state.write("agent.json", {"agent_id": "agent_test", "version": 0})
    cfg = OperatorConfig(tunnel_url=TUNNEL, state_dir=tmp_path / "state", avatar_image=str(source))
    assert agent_ops.prepare_web(cfg, FakeSecrets(), lambda m: None) == 0
    assert 'src="assets/aria-avatar.svg"' in (cfg.web_out / "index.html").read_text(encoding="utf-8")
    assert (cfg.web_out / "assets" / "aria-avatar.svg").read_text(encoding="utf-8") == TINY_SVG


def test_a_bad_avatar_stops_prepare_web_with_a_plain_message_and_no_secret(tmp_path):
    state = State(tmp_path / "state")
    state.write("agent.json", {"agent_id": "agent_test", "version": 0})
    cfg = OperatorConfig(tunnel_url=TUNNEL, state_dir=tmp_path / "state", avatar_image=str(tmp_path / "missing.png"))
    with pytest.raises(OperatorError, match="avatar file was not found") as caught:
        agent_ops.prepare_web(cfg, FakeSecrets(), lambda m: None)
    assert KEY not in str(caught.value) and TOKEN not in str(caught.value) and PUBLIC not in str(caught.value)


def test_the_avatar_commands_refuse_a_missing_file_without_a_browser(tmp_path):
    lines: list = []
    code = cli.main(["showcase-avatar", "--source", str(tmp_path / "nope.png"), "--out", str(tmp_path / "out.webp")], environ={}, out=lines.append)
    assert code == cli.EXIT_REFUSED and lines and lines[0].startswith("stopped:")
    lines.clear()
    code = cli.main(["showcase-preview", "--out", str(tmp_path / "pics"), "--avatar", str(tmp_path / "nope.png")], environ={}, out=lines.append)
    assert code == cli.EXIT_REFUSED and any("stopped:" in line for line in lines)


# ---------------------------------------------------------------- the script itself


def test_a_call_is_only_ever_started_from_a_click_and_nothing_pretends_to_speak():
    assert re.findall(r"createWebCall\(", VOICE_JS) == ["createWebCall("], "one place creates a call"
    top_level = [line for line in VOICE_JS.splitlines() if re.match(r"(startCall|loadSdk|endCall)\(", line)]
    assert top_level == [], "nothing starts at load"
    assert 'addEventListener("click", startCall)' in VOICE_JS
    assert "Math.random" not in VOICE_JS and "setInterval" not in VOICE_JS and "autoplay" not in VOICE_JS.lower()
    speaking = re.findall(r'dataset\.speaking = "true"', VOICE_JS)
    assert len(speaking) == 1 and "onAgentStartTalking: () => setSpeaking(true)" in VOICE_JS and "onAgentStopTalking: () => setSpeaking(false)" in VOICE_JS
    assert "hideBtn.addEventListener" in VOICE_JS and "function hideWidget" in VOICE_JS
    hide_body = re.search(r"function hideWidget\(\) \{(.*?)\n\}", VOICE_JS, re.S).group(1)
    assert "endCall" not in hide_body and ".end(" not in hide_body, "hiding never ends a call"


def test_the_script_holds_no_secret_and_loads_only_the_pinned_sdk():
    assert re.findall(r"https?://[^\s\"'`)]+", VOICE_JS) == ["https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.1/+esm"]
    for word in ("api_key", "apikey", "secret", "bearer", "authorization", "password"):
        assert word not in VOICE_JS.lower()
