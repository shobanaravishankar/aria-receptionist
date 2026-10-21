"""demo-serve --lifecycle and demo-agent --mode lifecycle: the simulated appointment lifecycle as an operator sees it. No browser, no network, no Retell account."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from aria_booking import cli
from aria_booking.appointments.sender import OWNER_FILE
from aria_booking.appointments.tools import LIFECYCLE_ROUTES, LifecycleTools
from aria_booking.demo import server as demo_server
from aria_booking.voice import retell_http

from test_launch import KEY

ENV = {"ARIA_RETELL_API_KEY": KEY}


class FakeServer:
    served = closed = False

    def __init__(self, *a, **k):
        FakeServer.served = FakeServer.closed = False

    def serve_forever(self):
        FakeServer.served = True
        raise KeyboardInterrupt

    def shutdown(self):
        pass

    def server_close(self):
        FakeServer.closed = True


class FakeSite(FakeServer):
    def serve_forever(self):
        pass


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    built = {}
    monkeypatch.setattr(demo_server, "make_site", lambda dataset, port, host="127.0.0.1", **kw: built.setdefault("site", FakeSite()) and built.setdefault("dataset", dataset) and built.setdefault("site_kwargs", kw) and FakeSite())
    monkeypatch.setattr(retell_http, "make_http_server", lambda endpoint, port, host="127.0.0.1": built.setdefault("endpoint", endpoint) and FakeServer())

    def boom(*args, **kwargs):
        raise AssertionError("the lifecycle demo must never build a Booksy browser driver")

    monkeypatch.setattr(cli, "SeleniumBooksyDriver", boom)
    monkeypatch.setattr(cli, "PlaywrightBooksyDriver", boom)
    FakeServer.served = FakeServer.closed = False
    return built


def run(argv, env=ENV):
    lines = []
    code = cli.main(argv, environ=env, clock=lambda: datetime(2026, 10, 13, 12, tzinfo=ZoneInfo("America/New_York")), out=lines.append)
    return code, "\n".join(lines)


def serve(tmp_path, *extra, env=None):
    return run(["demo-serve", "--lifecycle", "--today", "2026-10-13", "--store", str(tmp_path / "a.sqlite3"), *extra], env={**ENV, "ARIA_LOCAL_DIR": str(tmp_path), **(env or {})})


def test_it_starts_says_what_it_is_seeds_once_and_never_prints_the_key(isolated, tmp_path):
    code, text = serve(tmp_path)
    assert code == cli.EXIT_OK and FakeServer.served and FakeServer.closed
    assert "SIMULATED APPOINTMENT LIFECYCLE" in text and "NOT Booksy" in text and "no real salon calendar can be reached or changed" in text
    assert "seeded: 4 upcoming and 7 past made now" in text and "demo date 2026-10-13" in text and KEY not in text
    again = serve(tmp_path)[1]
    assert "seeded: 0 upcoming and 0 past made now" in again, "a restart keeps the records and makes no second set"


def test_the_endpoint_is_the_lifecycle_one_with_no_booksy_route_and_a_labelled_reply(isolated, tmp_path):
    serve(tmp_path)
    endpoint = isolated["endpoint"]
    assert isinstance(endpoint._tools, LifecycleTools) and set(endpoint._routes.values()) == set(LIFECYCLE_ROUTES.values())
    assert "book_slot" not in endpoint._routes.values()
    assert isolated["dataset"].overlay is not None, "the calendar page and the tools share the one store"
    assert type(endpoint._tools._on_committed.__self__).__name__ == "AlertWorker", "a commit wakes the alert worker"


def test_owner_alerts_wait_and_the_status_never_prints_a_number(isolated, tmp_path):
    code, text = serve(tmp_path)
    assert "owner alerts: NOT sent: no SMS provider is set up yet and no owner number is configured; 0 alert(s) waiting in the outbox" in text
    (tmp_path / OWNER_FILE).write_text(json.dumps({"to": "(310) 555-9876"}), encoding="utf-8")
    code, text = serve(tmp_path)
    assert code == cli.EXIT_OK and "(an owner number is configured)" in text and "9876" not in text and "3105559876" not in text


def test_an_unusable_owner_number_is_refused_before_anything_starts(isolated, tmp_path):
    (tmp_path / OWNER_FILE).write_text(json.dumps({"to": "555-0100"}), encoding="utf-8")
    code, text = serve(tmp_path)
    assert code == cli.EXIT_REFUSED and "not a complete phone number" in text and "endpoint" not in isolated and not FakeServer.served


def test_without_the_flag_the_demo_stays_read_only(isolated, tmp_path):
    code, text = run(["demo-serve", "--today", "2026-10-13"])
    assert code == cli.EXIT_OK and "nothing here can book, hold, change or cancel" in text and not hasattr(isolated["endpoint"]._tools, "_lc")
    assert isolated["dataset"].overlay is None


def test_the_contact_fallback_line_says_whether_a_number_is_configured(isolated, tmp_path):
    assert "contact fallback: no salon number is configured, so Aria says to contact the salon and gives none" in serve(tmp_path)[1]


def test_the_default_store_is_under_the_git_ignored_local_folder(isolated, tmp_path):
    code, text = run(["demo-serve", "--lifecycle", "--today", "2026-10-13"], env={**ENV, "ARIA_LOCAL_DIR": str(tmp_path)})
    assert code == cli.EXIT_OK and str(tmp_path / "demo-appointments" / "appointments.sqlite3") in text and (tmp_path / "demo-appointments" / "appointments.sqlite3").exists()


# ---------------------------------------------------------------- the operator: demo-agent --mode


def test_the_agent_mode_flag_is_a_choice_and_defaults_to_the_read_only_demo():
    parser = cli.build_parser()
    assert parser.parse_args(["demo-agent", "plan"]).mode is None
    assert parser.parse_args(["demo-agent", "plan", "--mode", "lifecycle"]).mode == "lifecycle"
    with pytest.raises(SystemExit):
        parser.parse_args(["demo-agent", "plan", "--mode", "booking"])


def test_a_plan_in_lifecycle_mode_names_the_eight_functions_and_the_reviewed_files(tmp_path):
    lines = []
    code = cli.main(["demo-agent", "plan", "--mode", "lifecycle", "--state-dir", str(tmp_path / "state")], environ={}, out=lines.append)
    text = "\n".join(lines)
    assert code == 0 and "mode              : lifecycle (simulated appointment lifecycle)" in text
    assert "book_appointment, cancel_appointment, check_slot, find_alternatives, find_appointments, last_completed_visit, lookup_service, reschedule_appointment + end_call" in text
    assert "none reaches the live Booksy calendar" in text and "prompt_demo_lifecycle.md" in text


def test_the_diagnostic_incomplete_calendar_reaches_the_lifecycle_dataset_only_when_chosen(isolated, tmp_path):
    serve(tmp_path)
    assert isolated["dataset"].diagnostic_unknown_hours == frozenset()
    isolated.clear()
    serve(tmp_path, "--diagnostic-incomplete-calendar")
    assert isolated["dataset"].diagnostic_unknown_hours == frozenset({"910010"})


def test_the_lifecycle_site_allows_only_the_prepared_website_and_uses_the_lifecycle_clock(isolated, tmp_path):
    serve(tmp_path, "--page-port", "5888")
    kw = isolated["site_kwargs"]
    assert set(kw["allowed_origins"]) == {"http://localhost:5888", "http://127.0.0.1:5888"} and kw["clock"]().date().isoformat() == "2026-10-13"


# ---------------------------------------------------------------- the opt-in preferred technicians


def test_the_preferred_technician_functions_are_off_unless_asked_for(isolated, tmp_path):
    from aria_booking.appointments.preference_tools import PreferenceTools

    code, text = serve(tmp_path)
    endpoint = isolated["endpoint"]
    assert code == cli.EXIT_OK and "preferred technicians: off" in text
    assert not isinstance(endpoint._tools, PreferenceTools) and not {"get_preferences", "save_preference", "remove_preference"} & set(endpoint._routes.values())


def test_with_the_flag_the_three_extra_functions_are_served_and_nothing_else_changes(isolated, tmp_path):
    from aria_booking.appointments.preference_tools import PREFERENCE_ROUTES, PreferenceTools

    code, text = serve(tmp_path, "--preferences")
    endpoint = isolated["endpoint"]
    assert code == cli.EXIT_OK and "preferred technicians: ON (demo only)" in text and KEY not in text
    assert isinstance(endpoint._tools, PreferenceTools) and endpoint._routes == PREFERENCE_ROUTES and len(endpoint._routes) == 11
    assert set(LIFECYCLE_ROUTES.values()) < set(endpoint._routes.values()) and "book_slot" not in endpoint._routes.values()


def test_the_flag_without_the_lifecycle_is_refused_before_anything_starts(isolated, tmp_path):
    code, text = run(["demo-serve", "--preferences", "--today", "2026-10-13"])
    assert code == cli.EXIT_REFUSED and "--preferences needs --lifecycle" in text and "endpoint" not in isolated and not FakeServer.served


def test_the_agent_mode_flag_accepts_the_preferences_mode_and_still_refuses_anything_else():
    parser = cli.build_parser()
    assert parser.parse_args(["demo-agent", "plan", "--mode", "lifecycle-preferences"]).mode == "lifecycle-preferences"
    assert parser.parse_args(["demo-serve", "--lifecycle", "--preferences"]).preferences is True and parser.parse_args(["demo-serve", "--lifecycle"]).preferences is False
    with pytest.raises(SystemExit):
        parser.parse_args(["demo-agent", "plan", "--mode", "preferences"])
