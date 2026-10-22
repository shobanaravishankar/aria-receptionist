"""The public-release handoff and the runbook it sits on: only real commands, real options and real settings; the exact source; nothing personal or secret; the order of work, the records, the
avatar and the way back for each step. Documentation tests: nothing is run against any host."""

from __future__ import annotations

import re
from pathlib import Path

from aria_booking import cli
from aria_booking.showcase.leaks import find_leaks

BOOKING = Path(__file__).resolve().parent.parent
HANDOFF = (BOOKING / "PUBLIC_RELEASE_HANDOFF.md").read_text(encoding="utf-8")
DEPLOY = (BOOKING / "DEPLOY.md").read_text(encoding="utf-8")
SOURCE_COMMIT = "e32488ad2628fd457514ce4ba2c79c30f5a1f03d"


def test_it_names_the_exact_source_and_how_to_check_a_checkout_against_it():
    assert SOURCE_COMMIT in HANDOFF and f"git diff {SOURCE_COMMIT[:7]} <your commit> -- booking/aria_booking booking/voice_agent_draft" in HANDOFF
    assert (BOOKING / "voice_agent_draft" / "prompt_demo_lifecycle_preferences.md").is_file() and (BOOKING / "voice_agent_draft" / "tools_demo_lifecycle_preferences.json").is_file()


def test_every_command_and_setting_it_names_exists():
    for text in (HANDOFF, DEPLOY):
        commands = set(re.findall(r"python -m aria_booking ([a-z][a-z-]+)", text))
        assert commands and commands <= set(cli.build_parser()._subparsers._group_actions[0].choices), commands
    source = "".join((BOOKING / "aria_booking" / rel).read_text(encoding="utf-8") for rel in ("hosting/config.py", "demo/agent_ops.py", "demo/secret_store.py", "cli.py", "hosting/server.py"))
    names = set(re.findall(r"\b(ARIA_[A-Z_]+)\b", HANDOFF))
    assert {"ARIA_PREFERENCES", "ARIA_SITE_ORIGINS", "ARIA_STORE_PATH", "ARIA_PUBLIC_ORIGIN", "ARIA_SERVE_SITE", "ARIA_BACKEND_URL", "ARIA_AVATAR_IMAGE", "ARIA_DEMO_TUNNEL_URL"} <= names
    assert all(name in source for name in names), sorted(n for n in names if n not in source)


def test_the_operator_options_it_uses_are_real_and_the_preferences_mode_is_accepted():
    parser = cli.build_parser()
    args = parser.parse_args(["demo-agent", "prepare-web", "--mode", "lifecycle-preferences", "--web-dir", "somewhere"])
    assert args.mode == "lifecycle-preferences" and args.web_dir == "somewhere"
    assert parser.parse_args(["demo-agent", "preflight", "--mode", "lifecycle-preferences", "--base-url", "https://backend.example"]).base_url == "https://backend.example"
    assert parser.parse_args(["demo-agent", "update", "--mode", "lifecycle-preferences", "--confirm-update"]).confirm_update is True
    assert parser.parse_args(["demo-store", "backup", "--path", "a", "--dest", "b"]).dest == "b"


def test_a_website_bundle_is_always_built_into_its_own_folder_in_both_documents():
    for text in (HANDOFF, DEPLOY):
        build = [line for line in text.splitlines() if "demo-agent prepare-web" in line and "ARIA_BACKEND_URL" in line]
        assert build and all("--web-dir" in line for line in build), build
    assert "default folder" in HANDOFF and "overwrite" in HANDOFF


def test_the_runbook_no_longer_moves_a_preferences_agent_with_the_older_mode_and_says_what_each_mode_needs():
    assert "demo-agent update --mode lifecycle --confirm-update" not in DEPLOY
    assert "demo-agent update --mode lifecycle-preferences --confirm-update" in DEPLOY and "ARIA_PREFERENCES=1" in DEPLOY and "do the local rehearsal first" not in DEPLOY
    assert "ARIA_PREFERENCES" in HANDOFF and "answer \"not found\"" in HANDOFF


def test_it_covers_the_records_the_avatar_the_stable_address_and_the_way_back_for_each_step():
    lowered = HANDOFF.lower()
    for needle in ("demo-store backup", "demo-store check", "one writer per file", "way back", "publish deploy", "demo-public-check", "supervised", "approved modern-headset webp", "outside git",
                   "stable", "named", "quick tunnel", "not chosen", "no paid infrastructure is authorised", "booksy", "monitoring"):
        assert needle in lowered, needle
    assert HANDOFF.count("*Way back") + HANDOFF.count("*Way back:*") >= 4 or lowered.count("way back") >= 4


def test_nothing_personal_secret_or_invented_is_in_either_document():
    for text in (HANDOFF, DEPLOY):
        assert not re.search(r"[A-Za-z]:[\\/]Users|/Users/|/home/", text), "no personal path"
        assert not re.search(r"(key|token)_[0-9a-f]{12,}|public_key_[a-z0-9]{6,}|sk-[A-Za-z0-9]{10,}", text, re.I)
        assert find_leaks(text, allowed=("glamour-dayspa-aria-demo.netlify.app",)) == [], "no identifier of the earlier salon and no phone number (the existing site's own address is the one allowed mention)"
    assert "glamour-dayspa-aria-demo.netlify.app" in HANDOFF, "the one existing site this release replaces"
