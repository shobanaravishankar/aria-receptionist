"""The public backend's tools and packaging: the appointment-file copy and check, the commands, the Netlify bundle the operator builds, and the container files. A real image build and a real
host are NOT exercised here (neither Docker nor a host account is used); what IS exercised is that the files the image copies are enough to start the backend, and that nothing secret is in any file."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aria_booking import cli
from aria_booking.appointments.lifecycle import open_lifecycle
from aria_booking.demo import agent_ops
from aria_booking.demo.agent_ops import OperatorConfig, OperatorError, State, load_config
from aria_booking.hosting.store_tools import StoreToolError, backup, summarize

from test_demo_agent_ops import FakeSecrets, KEY, PUBLIC, TOKEN, TUNNEL
from test_hosting_server import ORIGIN, SITE, Running

BOOKING = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 22, 10, 0, tzinfo=TZ)
BACKEND = "https://serenity-demo.fly.dev"


def populated(tmp_path: Path, name: str = "source.sqlite3") -> Path:
    path = tmp_path / name
    open_lifecycle(path, today=NOW.date(), real_now=lambda: NOW)  # seeded with the fictional clients, like a real start
    return path


# ---------------------------------------------------------------- the appointment file


def test_a_copy_is_consistent_complete_and_leaves_the_original_byte_for_byte_alone(tmp_path):
    source = populated(tmp_path)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    lines: list = []
    copied = backup(source, tmp_path / "copy" / "appointments.sqlite3", out=lines.append)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before, "the original was only read"
    original = summarize(source)
    assert copied["appointments"] == original["appointments"] and copied["clients"] == original["clients"] == 3 and copied["integrity"] == "ok"
    assert lines and "integrity ok" in lines[0]


def test_the_copy_opens_as_a_working_store_with_the_same_content(tmp_path):
    from aria_booking.appointments.store import AppointmentStore

    source = populated(tmp_path)
    backup(source, tmp_path / "again.sqlite3", out=lambda m: None)
    assert AppointmentStore(tmp_path / "again.sqlite3").counts() == AppointmentStore(source).counts()


def test_the_summary_and_the_messages_carry_counts_only(tmp_path):
    text = json.dumps(summarize(populated(tmp_path)))
    for private in ("Willa", "Hartwell", "Ansel", "Cedric", "2125550142", "+1212"):
        assert private not in text
    assert set(json.loads(text)) == {"schema_version", "appointments", "clients", "alerts_waiting", "integrity", "bytes"}


def test_a_copy_never_overwrites_and_never_copies_a_file_onto_itself(tmp_path):
    source = populated(tmp_path)
    existing = tmp_path / "existing.sqlite3"
    existing.write_bytes(b"keep me")
    with pytest.raises(StoreToolError, match="already exists"):
        backup(source, existing, out=lambda m: None)
    assert existing.read_bytes() == b"keep me"
    with pytest.raises(StoreToolError, match="already exists|same file"):
        backup(source, source, out=lambda m: None)


@pytest.mark.parametrize("make,message", [
    (lambda p: p / "missing.sqlite3", "there is no file"),
    (lambda p: _write(p / "notsqlite.sqlite3", b"this is not a database at all, just text"), "cannot be opened|could not be read"),
    (lambda p: _other_database(p / "other.sqlite3"), "not an appointment file"),
])
def test_a_file_that_is_not_an_appointment_file_is_refused_with_a_plain_message_and_no_copy_is_made(tmp_path, make, message):
    bad = make(tmp_path)
    with pytest.raises(StoreToolError, match=message):
        backup(bad, tmp_path / "out.sqlite3", out=lambda m: None)
    assert not (tmp_path / "out.sqlite3").exists()


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def _other_database(path: Path) -> Path:
    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE something_else (x INTEGER)")
    conn.commit()
    conn.close()
    return path


def test_a_copy_can_be_taken_while_the_server_is_open_and_writing(tmp_path):
    first = Running(tmp_path, NOW)
    try:
        copied = backup(first.config.store_path, tmp_path / "live-copy.sqlite3", out=lambda m: None)
        assert copied["integrity"] == "ok" and copied["clients"] == first.app.lifecycle.store.counts()["clients"]
    finally:
        first.stop()


# ---------------------------------------------------------------- the commands


def run_cli(*argv, env=None):
    lines: list = []
    code = cli.main(list(argv), environ=env or {}, out=lines.append)
    return code, "\n".join(lines)


def test_the_store_commands_check_and_copy_and_refuse_plainly(tmp_path):
    source = populated(tmp_path)
    code, text = run_cli("demo-store", "check", "--path", str(source))
    assert code == cli.EXIT_OK and json.loads(text)["integrity"] == "ok"
    code, text = run_cli("demo-store", "backup", "--path", str(source), "--dest", str(tmp_path / "b.sqlite3"))
    assert code == cli.EXIT_OK and (tmp_path / "b.sqlite3").is_file() and "copied to" in text
    code, text = run_cli("demo-store", "backup", "--path", str(source))
    assert code == cli.EXIT_REFUSED and "needs --dest" in text
    code, text = run_cli("demo-store", "backup", "--path", str(source), "--dest", str(tmp_path / "b.sqlite3"))
    assert code == cli.EXIT_REFUSED and "already exists" in text
    code, text = run_cli("demo-store", "check", "--path", str(tmp_path / "nope.sqlite3"))
    assert code == cli.EXIT_REFUSED and "there is no file" in text


def test_serve_refuses_with_every_problem_named_and_starts_nothing():
    code, text = run_cli("demo-public-serve")
    assert code == cli.EXIT_REFUSED and text.startswith("refused: The public backend cannot start:")
    for name in ("ARIA_RETELL_API_KEY", "ARIA_RETELL_TOOL_TOKEN", "ARIA_PUBLIC_ORIGIN", "ARIA_STORE_PATH"):
        assert name in text


def test_serve_refuses_a_secret_free_message_even_when_a_secret_is_set(tmp_path):
    code, text = run_cli("demo-public-serve", "--store", str(tmp_path / "s.sqlite3"), env={"ARIA_RETELL_API_KEY": KEY, "ARIA_RETELL_TOOL_TOKEN": TOKEN, "ARIA_PUBLIC_ORIGIN": "http://not-https.example"})
    assert code == cli.EXIT_REFUSED and "must be https" in text and KEY not in text and TOKEN not in text


def test_the_outside_check_command_passes_a_healthy_backend_and_names_the_fix_for_a_website_that_is_not_listed(tmp_path):
    run = Running(tmp_path, NOW)
    try:
        code, text = run_cli("demo-public-check", "--url", run.base, "--site-origin", SITE)
        assert code == cli.EXIT_OK and "FAIL" not in text and "PASS  website_may_read" in text and "all checks passed" in text
        code, text = run_cli("demo-public-check", "--url", run.base, "--site-origin", "https://other.example")
        assert code == cli.EXIT_REFUSED and "FAIL  website_may_read" in text and "fix: Add https://other.example to ARIA_SITE_ORIGINS" in text and "check(s) failed" in text
    finally:
        run.stop()


def test_the_outside_check_command_refuses_a_bad_address_and_reports_one_that_does_not_answer():
    code, text = run_cli("demo-public-check", "--url", "not an address")
    assert code == cli.EXIT_REFUSED and text.startswith("refused:")
    code, text = run_cli("demo-public-check", "--url", "http://127.0.0.1:9")
    assert code == cli.EXIT_REFUSED and "FAIL  reachable" in text and "does not answer" in text


# ---------------------------------------------------------------- the files the image copies are enough to start the backend


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_backend_starts_from_only_the_files_the_image_copies_and_without_a_browser_library(tmp_path):
    image = tmp_path / "app"
    import shutil

    shutil.copytree(BOOKING / "aria_booking", image / "aria_booking", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(BOOKING / "voice_agent_draft", image / "voice_agent_draft")
    port = free_port()
    env = {**os.environ, "PYTHONPATH": str(image), "PORT": str(port), "ARIA_STORE_PATH": str(tmp_path / "data" / "appointments.sqlite3"), "ARIA_PUBLIC_ORIGIN": BACKEND,
           "ARIA_SITE_ORIGINS": SITE, "ARIA_RETELL_API_KEY": KEY, "ARIA_RETELL_TOOL_TOKEN": TOKEN, "ARIA_HOST": "127.0.0.1"}
    for name in ("ARIA_SECRET_DIR", "ARIA_DEMO_STATE_DIR"):
        env.pop(name, None)
    bootstrap = ("import sys\nfor m in ('playwright', 'playwright.sync_api', 'selenium', 'greenlet', 'pyee', 'pytest'): sys.modules[m] = None\n"
                 "import runpy\nsys.argv = ['aria_booking', 'demo-public-serve']\nrunpy.run_module('aria_booking', run_name='__main__')")
    process = subprocess.Popen([sys.executable, "-c", bootstrap], cwd=str(tmp_path), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        ready = None
        deadline = time.time() + 40
        while time.time() < deadline and ready is None:
            if process.poll() is not None:
                raise AssertionError("the backend exited: " + process.stdout.read()[-1500:])
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=2) as response:
                    ready = json.loads(response.read())
            except Exception:
                time.sleep(0.3)
        assert ready and ready["ok"] is True, ready
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            assert b"Serenity Spa" in response.read()
        assert (tmp_path / "data" / "appointments.sqlite3").is_file(), "the file is made on the volume path it was given"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


# ---------------------------------------------------------------- the Netlify bundle the operator builds


def operator_config(tmp_path, **kw) -> OperatorConfig:
    return OperatorConfig(tunnel_url=TUNNEL, state_dir=tmp_path / "state", **kw)


def prepared(tmp_path, **kw):
    State(tmp_path / "state").write("agent.json", {"agent_id": "agent_test", "version": 0})
    cfg = operator_config(tmp_path, **kw)
    lines: list = []
    assert agent_ops.prepare_web(cfg, FakeSecrets(), lines.append) == 0
    return cfg, "\n".join(lines)


def test_a_page_for_a_public_backend_points_only_at_that_backend_and_carries_the_hosts_rules_file(tmp_path):
    cfg, said = prepared(tmp_path, backend_url=BACKEND + "/")
    html = (cfg.web_out / "index.html").read_text(encoding="utf-8")
    assert f'"calendarOrigin": "{BACKEND}"' in html and f'data-src="{BACKEND}/pro/en-us/9000001/calendar' in html
    assert "localhost" not in html and "127.0.0.1" not in html and "8790" not in html
    assert "demo-public-check" in said and BACKEND in said
    headers = (cfg.web_out / "_headers").read_text(encoding="utf-8")
    assert "Permissions-Policy: microphone=(self)" in headers and "X-Content-Type-Options: nosniff" in headers and "/assets/*\n  Cache-Control: public" in headers
    config_js = (cfg.web_out / "demo-config.js").read_text(encoding="utf-8")
    assert PUBLIC in config_js and "agent_test" in config_js
    for secret in (KEY, TOKEN):
        assert not any(secret in p.read_text(encoding="utf-8", errors="ignore") for p in cfg.web_out.rglob("*") if p.is_file() and p.suffix in (".html", ".js", ".css", ".json", "") ), "no secret in the bundle"


def test_the_local_page_is_unchanged_without_a_backend_address(tmp_path):
    cfg, _said = prepared(tmp_path)
    html = (cfg.web_out / "index.html").read_text(encoding="utf-8")
    assert "http://localhost:8790" in html and not (cfg.web_out / "_headers").exists()


@pytest.mark.parametrize("address,message", [("http://serenity-demo.fly.dev", "must be https"), ("https://localhost", "is not public"), ("https://serenity-demo.fly.dev/app", "is not an origin"),
                                              ("serenity-demo.fly.dev", "is not an origin")])
def test_a_bad_backend_address_stops_prepare_web_with_the_reason(tmp_path, address, message):
    State(tmp_path / "state").write("agent.json", {"agent_id": "agent_test", "version": 0})
    with pytest.raises(OperatorError, match=message):
        agent_ops.prepare_web(operator_config(tmp_path, backend_url=address), FakeSecrets(), lambda m: None)


def test_the_backend_address_can_come_from_the_environment_or_the_config_file(tmp_path):
    assert load_config({"ARIA_BACKEND_URL": BACKEND}).backend_url == BACKEND
    file = tmp_path / "demo.json"
    file.write_text(json.dumps({"backend_url": BACKEND}), encoding="utf-8")
    assert load_config({}, file=str(file)).backend_url == BACKEND
    assert OperatorConfig().backend_url == ""


def test_the_bundle_works_against_the_backend_it_names_cors_and_framing_included(tmp_path):
    """The page is built for the backend's public address, but the backend is exercised on a loopback port: the website origin it allows is what makes the page's reads work."""
    run = Running(tmp_path, NOW)
    try:
        status, headers, _b = run.get("/demo/open-times?service_id=massage-swedish-massage-60&date=2026-10-23", origin=SITE)
        assert status == 200 and headers["Access-Control-Allow-Origin"] == SITE
        assert run.get("/pro/en-us/9000001/calendar?date=today&view=day&staffers=working")[1]["Content-Security-Policy"].count(SITE) == 1
    finally:
        run.stop()


# ---------------------------------------------------------------- the container files


def test_the_image_installs_only_the_runtime_requirements_copies_only_the_package_and_holds_no_secret():
    docker = (BOOKING / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY requirements-runtime.txt" in docker and "pip install --no-cache-dir -r requirements-runtime.txt" in docker
    copies = re.findall(r"^COPY (\S+)", docker, re.M)
    assert copies == ["requirements-runtime.txt", "aria_booking", "voice_agent_draft"], "no tests, no local folder, no personal file"
    assert 'CMD ["python", "-m", "aria_booking", "demo-public-serve"]' in docker and "EXPOSE 8080" in docker and 'VOLUME ["/data"]' in docker
    assert "ARIA_STORE_PATH=/data/appointments.sqlite3" in docker and not re.search(r"ARIA_RETELL_(API_KEY|TOOL_TOKEN)\s*=", docker), "secrets are the host's, never baked in"
    requirements = (BOOKING / "requirements-runtime.txt").read_text(encoding="utf-8")
    assert [l for l in requirements.splitlines() if l and not l.startswith("#")] == ["tzdata==2026.5"]
    ignore = (BOOKING / ".dockerignore").read_text(encoding="utf-8").split()
    for excluded in (".local", "tests", "*.sqlite3", ".env"):
        assert excluded in ignore


@pytest.mark.parametrize("name", ["fly.toml.example", "render.yaml.example"])
def test_the_host_templates_have_placeholders_a_volume_and_no_secret_value(name):
    text = (BOOKING / "deploy" / name).read_text(encoding="utf-8")
    assert "REPLACE" in text and "/data" in text and "ARIA_STORE_PATH" in text and "/healthz" in text and "ARIA_SITE_ORIGINS" in text
    assert not re.search(r"(key|token)_[0-9a-f]{12,}", text, re.I) and "public_key_" not in text
    for secret_name in ("ARIA_RETELL_API_KEY", "ARIA_RETELL_TOOL_TOKEN"):
        for line in text.splitlines():
            if secret_name in line and not line.lstrip().startswith("#"):
                assert "sync: false" in text, f"{secret_name} appears only as a dashboard-entered secret"
                assert "value" not in line, f"{secret_name} is never given a value in a file"


def test_fly_runs_one_always_on_machine_with_the_volume_and_a_health_check():
    text = (BOOKING / "deploy" / "fly.toml.example").read_text(encoding="utf-8")
    assert 'auto_stop_machines = "off"' in text and "min_machines_running = 1" in text and 'source = "aria_data"' in text and 'path = "/healthz"' in text and "force_https = true" in text


def test_a_personal_fly_file_is_ignored_by_git():
    assert "booking/fly.toml" in (BOOKING.parent / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_the_runbook_names_only_commands_and_settings_that_exist():
    text = (BOOKING / "DEPLOY.md").read_text(encoding="utf-8")
    commands = set(re.findall(r"python -m aria_booking ([a-z][a-z-]+)", text))
    choices = set(cli.build_parser()._subparsers._group_actions[0].choices)
    assert commands and commands <= choices, commands - choices
    source = "".join((BOOKING / "aria_booking" / rel).read_text(encoding="utf-8") for rel in ("hosting/config.py", "demo/agent_ops.py", "demo/secret_store.py", "cli.py", "hosting/server.py"))
    names = set(re.findall(r"\b(ARIA_[A-Z_]+)\b", text))
    assert names and all(name in source for name in names), sorted(n for n in names if n not in source)
    sentences = text.lower()
    assert "nothing in this repository provisions, publishes or pays" in sentences and "what only a call can prove" in sentences
