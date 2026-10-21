"""The public backend, running for real on a loopback port: health and readiness, the website it serves, the calendar and its framing/CORS rules, the signed voice tools, the daily
upkeep and the rolling date across midnight, and the logs. No Retell account, no browser, no network beyond loopback."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aria_booking.demo.open_times import open_times
from aria_booking.hosting.checks import check_public
from aria_booking.hosting.config import PublicConfig, load_public_config
from aria_booking.hosting.server import build_app, make_public_server

TZ = ZoneInfo("America/New_York")
KEY = "key_" + "a1b2c3d4e5f6a7b8c9d0e1f2"
TOKEN = "token_" + "0f1e2d3c4b5a69788796a5b4"
SITE = "https://glamour-dayspa-aria-demo.netlify.app"
ORIGIN = "https://serenity-demo.fly.dev"
SWEDISH60 = "massage-swedish-massage-60"


class Running:
    def __init__(self, tmp_path: Path, start: datetime, *, log=None, **config_overrides):
        self.now = [start]
        self.lines: list = []
        env = {"ARIA_RETELL_API_KEY": KEY, "ARIA_RETELL_TOOL_TOKEN": TOKEN, "ARIA_PUBLIC_ORIGIN": ORIGIN, "ARIA_SITE_ORIGINS": SITE,
               "ARIA_RETELL_PUBLIC_KEY": "public_key_" + "abcdef0123456789", "ARIA_RETELL_AGENT_ID": "agent_test"}
        self.config = load_public_config(env, overrides={"store": str(tmp_path / "appointments.sqlite3"), **config_overrides})
        self.app, self.worker, self.cleanup = build_app(self.config, log=self.lines.append, clock=lambda: self.now[0], site_dir=tmp_path / "site")
        self.server = make_public_server(self.app, "127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.stop()

    def get(self, path, *, origin=None, method="GET"):
        headers = {"Origin": origin} if origin else {}
        request = urllib.request.Request(self.base + path, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def tool(self, name, args, *, sign=True, bearer=TOKEN, call="call-1"):
        body = json.dumps({"name": name, "call": {"call_id": call}, "args": args}).encode()
        headers = {"Content-Type": "application/json"}
        if sign:
            stamp = str(int(time.time() * 1000))
            headers["X-Retell-Signature"] = f"v={stamp},d=" + hmac.new(KEY.encode(), body + stamp.encode(), hashlib.sha256).hexdigest()
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        request = urllib.request.Request(f"{self.base}/tools/{name}", data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())


@pytest.fixture
def running(tmp_path):
    run = Running(tmp_path, datetime(2026, 10, 22, 10, 0, tzinfo=TZ))
    yield run
    run.stop()


# ---------------------------------------------------------------- liveness and readiness


def test_healthz_answers_without_touching_anything_else_and_head_works(running):
    status, headers, body = running.get("/healthz")
    assert status == 200 and json.loads(body) == {"ok": True} and headers["X-Content-Type-Options"] == "nosniff"
    assert running.get("/healthz", method="HEAD")[0] == 200


def test_readyz_is_ready_with_every_required_check_and_carries_no_secret(running):
    status, _h, body = running.get("/readyz")
    ready = json.loads(body)
    assert status == 200 and ready["ok"] is True and ready["spa_date"] == "2026-10-22"
    names = {c["name"] for c in ready["checks"]}
    assert {"store_readable", "store_writable", "spa_date", "daily_upkeep", "tool_secrets", "origins", "voice_settings", "site_files"} <= names
    text = body.decode()
    for secret in (KEY, TOKEN, "public_key_abcdef", "212555"):
        assert secret not in text
    assert ready["uptime_seconds"] >= 0


def test_readyz_says_what_to_fix_and_answers_503_when_the_store_cannot_be_written(running, monkeypatch):
    monkeypatch.setattr(running.app.lifecycle.store, "can_write", lambda: False)
    status, _h, body = running.get("/readyz")
    ready = json.loads(body)
    broken = next(c for c in ready["checks"] if c["name"] == "store_writable")
    assert status == 503 and ready["ok"] is False and broken["ok"] is False and "volume" in broken["fix"]


def test_an_optional_check_that_fails_does_not_make_the_backend_not_ready(tmp_path):
    run = Running(tmp_path, datetime(2026, 10, 22, 10, 0, tzinfo=TZ))
    try:
        run.app.upkeep.date = "2026-10-01"  # the upkeep is behind
        status, _h, body = run.get("/readyz")
        check = next(c for c in json.loads(body)["checks"] if c["name"] == "daily_upkeep")
        assert check["ok"] is False and check["required"] is False and status == 200
    finally:
        run.stop()


# ---------------------------------------------------------------- the website it serves


def test_the_website_is_served_from_here_pointing_at_this_backend_and_nothing_local(running):
    status, headers, body = running.get("/")
    html = body.decode()
    assert status == 200 and headers["Content-Type"].startswith("text/html") and headers["Permissions-Policy"] == "microphone=(self)" and headers["Cache-Control"] == "no-cache"
    assert 'data-testid="aria-widget"' in html and f'"calendarOrigin": "{ORIGIN}"' in html and f'data-src="{ORIGIN}/pro/en-us/9000001/calendar' in html
    assert "localhost" not in html and "127.0.0.1" not in html and "8790" not in html, "nothing in the page points at a machine"
    assert '"publicKey": "public_key_abcdef0123456789"' in (running.app.site_dir / "demo-config.js").read_text(encoding="utf-8") or "public_key_abcdef0123456789" in (running.app.site_dir / "demo-config.js").read_text(encoding="utf-8")
    assert KEY not in html and TOKEN not in html


@pytest.mark.parametrize("path,kind", [("/styles.css", "text/css"), ("/views.js", "application/javascript"), ("/voice.js", "application/javascript"), ("/assets/aria-avatar.svg", "image/svg+xml"),
                                         ("/assets/fonts/jost-latin.woff2", "font/woff2"), ("/assets/hero-stones.jpg", "image/jpeg"), ("/index.html", "text/html")])
def test_the_site_files_are_served_with_the_right_type(running, path, kind):
    status, headers, body = running.get(path)
    assert status == 200 and headers["Content-Type"].startswith(kind) and body


@pytest.mark.parametrize("path", ["/../appointments.sqlite3", "/%2e%2e/appointments.sqlite3", "/assets/../../appointments.sqlite3", "/assets/%2e%2e/%2e%2e/appointments.sqlite3",
                                  "/..%2fappointments.sqlite3", "/nothing-here.txt", "/assets/", "/tools/anything", "/%00"])
def test_nothing_outside_the_site_folder_and_no_missing_file_is_ever_served(running, path):
    status, _h, body = running.get(path)
    assert status in (404, 405, 400) and b"SQLite" not in body[:20]


def test_a_backend_that_only_serves_the_calendar_and_the_tools_serves_no_website(tmp_path):
    run = Running(tmp_path, datetime(2026, 10, 22, 10, 0, tzinfo=TZ), serve_site=False)
    try:
        assert run.get("/styles.css")[0] == 404
        connection = http.client.HTTPConnection("127.0.0.1", run.server.server_address[1], timeout=10)  # not urllib: it would follow the redirect
        connection.request("GET", "/")
        response = connection.getresponse()
        assert response.status == 302 and "/pro/en-us/9000001/calendar" in response.getheader("Location"), "the root goes to the calendar, as on a person's own machine"
        connection.close()
        assert run.get("/readyz")[0] == 200, "and it is still ready (no site files are required)"
    finally:
        run.stop()


# ---------------------------------------------------------------- the calendar, CORS and framing


def test_the_calendar_page_may_be_framed_by_the_listed_websites_and_this_backend_only(running):
    status, headers, body = running.get("/pro/en-us/9000001/calendar?date=today&view=day&staffers=working")
    policy = headers["Content-Security-Policy"]
    assert status == 200 and policy == f"frame-ancestors 'self' {SITE} {ORIGIN}" and b'data-testid="demo-banner"' in body


def test_only_the_listed_websites_may_read_the_open_times_and_the_change_stamp(running):
    for path in ("/demo/health", "/demo/changes", f"/demo/open-times?service_id={SWEDISH60}&date=2026-10-23"):
        status, headers, _b = running.get(path, origin=SITE)
        assert status == 200 and headers["Access-Control-Allow-Origin"] == SITE
        status, headers, _b = running.get(path, origin="https://evil.example")
        assert status == 200 and "Access-Control-Allow-Origin" not in headers


def test_a_browsers_permission_question_is_answered_yes_only_to_the_listed_websites(running):
    status, headers, _b = running.get("/demo/open-times", origin=SITE, method="OPTIONS")
    assert status == 204 and headers["Access-Control-Allow-Origin"] == SITE and "GET" in headers["Access-Control-Allow-Methods"]
    status, headers, _b = running.get("/demo/open-times", origin="https://evil.example", method="OPTIONS")
    assert status == 204 and "Access-Control-Allow-Origin" not in headers and "Access-Control-Allow-Methods" not in headers


def test_the_open_times_answer_is_the_engines_answer(running):
    status, _h, body = running.get(f"/demo/open-times?service_id={SWEDISH60}&date=2026-10-23")
    expected = open_times(running.app.lifecycle.dataset, SWEDISH60, date(2026, 10, 23), tz=TZ, now=running.now[0])
    assert status == 200 and json.loads(body) == expected and expected["status"] == "open"


def test_nothing_can_be_written_through_the_calendar_addresses(running):
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        status, _h, _b = running.get("/demo/open-times", method=method)
        assert status == 405, method


# ---------------------------------------------------------------- the signed tools


def test_a_signed_tool_call_with_the_right_token_is_answered(running):
    status, reply = running.tool("lookup_service", {"query": "swedish massage"})
    assert status == 200 and reply["status"] != "error" and reply["speak"], "a real answer from the same tools the call uses"


@pytest.mark.parametrize("kind", ["unsigned", "wrong_token", "no_token", "wrong_key"])
def test_a_tool_call_without_both_the_signature_and_the_token_is_refused_and_does_nothing(running, kind):
    if kind == "unsigned":
        status, reply = running.tool("find_appointments", {"phone": "2125550142"}, sign=False)
    elif kind == "wrong_token":
        status, reply = running.tool("find_appointments", {"phone": "2125550142"}, bearer="not-the-token")
    elif kind == "no_token":
        status, reply = running.tool("find_appointments", {"phone": "2125550142"}, bearer="")
    else:
        body = json.dumps({"name": "find_appointments", "call": {"call_id": "x"}, "args": {"phone": "2125550142"}}).encode()
        stamp = str(int(time.time() * 1000))
        request = urllib.request.Request(f"{running.base}/tools/find_appointments", data=body, method="POST", headers={
            "X-Retell-Signature": f"v={stamp},d=" + hmac.new(b"some other key", body + stamp.encode(), hashlib.sha256).hexdigest(), "Authorization": f"Bearer {TOKEN}"})
        try:
            urllib.request.urlopen(request, timeout=10)
            raise AssertionError("must be refused")
        except urllib.error.HTTPError as error:
            status, reply = error.code, json.loads(error.read())
    assert status == 401 and reply["error"] == "unauthorized" and "speak" in reply


def test_a_tool_address_is_post_only_and_an_unknown_one_is_a_404(running):
    assert running.get("/tools/lookup_service")[0] == 405
    assert running.tool("book_slot", {})[0] == 404, "no Booksy-style route exists"
    assert running.tool("not_a_tool", {})[0] == 404


def test_an_oversized_tool_body_is_refused_before_it_is_read(running):
    connection = http.client.HTTPConnection("127.0.0.1", running.server.server_address[1], timeout=10)
    connection.putrequest("POST", "/tools/lookup_service")
    connection.putheader("Content-Length", str(300 * 1024))
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 413
    connection.close()


# ---------------------------------------------------------------- the logs


def test_the_log_has_methods_paths_statuses_and_timings_and_never_a_secret_number_or_name(running):
    running.tool("lookup_service", {"query": "swedish massage"})
    running.tool("find_appointments", {"phone": "2125550142", "name": "Willa Hartwell"}, call="log-check")
    running.get("/demo/open-times?service_id=massage-swedish-massage-60&date=2026-10-24&staff=Sienna")
    text = "\n".join(running.lines)
    assert "POST /tools/lookup_service 200" in text and "GET /demo/open-times 200" in text
    for private in (KEY, TOKEN, "2125550142", "212-555", "Willa", "Hartwell", "swedish massage", "log-check", "2026-10-24", "Sienna", "service_id", "?"):
        assert private not in text, private  # not even a query string: only the path


def test_an_unexpected_failure_is_a_500_with_no_detail(running, monkeypatch):
    monkeypatch.setattr(running.app.calendar, "respond", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret detail " + KEY)))
    status, _h, body = running.get("/demo/health")
    assert status == 500 and json.loads(body) == {"error": "server_error"}
    assert KEY not in "\n".join(running.lines) and "failed (RuntimeError)" in "\n".join(running.lines)


def test_the_stores_write_probe_is_true_when_the_file_can_be_written_and_false_when_it_cannot(running, monkeypatch):
    from aria_booking.appointments.store import StoreError

    store = running.app.lifecycle.store
    assert store.can_write() is True
    counts = store.counts()

    def refuse():
        raise StoreError("locked")

    monkeypatch.setattr(store, "_write", refuse)
    assert store.can_write() is False
    monkeypatch.undo()
    assert store.counts() == counts, "the probe changed nothing"


# ---------------------------------------------------------------- the date follows the clock, across midnight and across days


def test_the_calendars_today_the_scripted_days_and_the_stamp_follow_the_real_date(running):
    _s, _h, body = running.get("/demo/health")
    first = json.loads(body)
    assert first["today"] == "2026-10-22" and first["covers"][1] == (date(2026, 10, 22) + timedelta(days=running.app.lifecycle.dataset.last_day.toordinal() - date(2026, 10, 22).toordinal())).isoformat()
    before_stamp = json.loads(running.get("/demo/changes")[2])["stamp"]
    running.now[0] = datetime(2026, 10, 23, 0, 5, tzinfo=TZ)  # just after midnight, Eastern
    health = json.loads(running.get("/demo/health")[2])
    assert health["today"] == "2026-10-23" and health["covers"][0] > first["covers"][0] and health["covers"][1] > first["covers"][1]
    assert json.loads(running.get("/demo/changes")[2])["stamp"] != before_stamp, "a page that is open sees the new day without a restart"
    ready = json.loads(running.get("/readyz")[2])
    assert ready["spa_date"] == "2026-10-23" and next(c for c in ready["checks"] if c["name"] == "spa_date")["ok"] is True
    assert next(c for c in ready["checks"] if c["name"] == "daily_upkeep")["ok"] is True, "and the upkeep ran for the new day by itself"
    assert running.app.lifecycle.dataset.scripted_days == (date(2026, 10, 24), date(2026, 10, 25))


def test_the_upkeep_settles_past_fictional_visits_and_tops_up_upcoming_ones_across_days(running):
    store = running.app.lifecycle.store
    willa = store.clients_with_phone("+12125550142")
    assert willa, "the fictional clients are seeded when the file is new"
    ids = [c.client_id for c in willa]
    upcoming_before = {a.reference for cid in ids for a in store.for_client(cid, ("confirmed",))}
    assert upcoming_before
    running.now[0] = datetime(2026, 11, 3, 9, 0, tzinfo=TZ)  # twelve days later: every one of those is in the past
    running.get("/demo/health")  # any request runs the day's upkeep
    after = {a.reference: a for cid in ids for a in store.for_client(cid)}
    assert all(after[r].status == "completed" for r in upcoming_before), "past fictional visits are settled, not left 'upcoming'"
    now_upcoming = {a.reference for a in after.values() if a.status == "confirmed"}
    assert now_upcoming and not (now_upcoming & upcoming_before) and all(after[r].start_utc > int(running.now[0].timestamp()) for r in now_upcoming), "and new upcoming ones exist"
    assert "upkeep for 2026-11-03" in "\n".join(running.lines)


def test_the_upkeep_runs_once_a_day_not_on_every_request(running):
    calls = []
    original = running.app.lifecycle.maintain
    running.app.lifecycle.maintain = lambda: calls.append(1) or original()
    for _ in range(5):
        running.get("/demo/health")
    assert calls == []
    running.now[0] = running.now[0] + timedelta(days=1)
    for _ in range(5):
        running.get("/demo/health")
    assert calls == [1]


def test_a_failing_upkeep_never_stops_the_server_answering_and_is_visible_in_readyz(running, monkeypatch):
    def broken():
        raise RuntimeError("disk")

    monkeypatch.setattr(running.app.lifecycle, "maintain", broken)
    running.now[0] = running.now[0] + timedelta(days=1)
    assert running.get("/demo/health")[0] == 200
    ready = json.loads(running.get("/readyz")[2])
    assert next(c for c in ready["checks"] if c["name"] == "daily_upkeep")["ok"] is False
    assert "failed (RuntimeError)" in "\n".join(running.lines)


# ---------------------------------------------------------------- persistence across a restart


def test_appointments_survive_a_restart_of_the_whole_backend(tmp_path):
    first = Running(tmp_path, datetime(2026, 10, 22, 10, 0, tzinfo=TZ))
    try:
        svc = first.app.lifecycle.dataset.registry.get(SWEDISH60)
        from aria_booking.appointments.models import NewBooking, to_epoch

        booked = first.app.lifecycle.store.book(NewBooking("Restart Person", "+12125550166", SWEDISH60, svc.booksy_name, "massage", "910006",
                                                           to_epoch(datetime(2026, 10, 23, 16, 0, tzinfo=TZ)), svc.duration_minutes, svc.buffer_before_minutes, svc.buffer_after_minutes),
                                                validate=first.app.lifecycle.judge.validator())
        assert booked.status == "booked"
        counts = first.app.lifecycle.store.counts()
    finally:
        first.stop()
    second = Running(tmp_path, datetime(2026, 10, 22, 12, 0, tzinfo=TZ))
    try:
        assert second.app.lifecycle.store.counts() == counts, "nothing lost, nothing seeded twice"
        assert second.app.lifecycle.store.get(booked.appointment.reference).status == "confirmed"
        page = second.get("/pro/en-us/9000001/calendar?date=2026-10-23&view=day&staffers=working")[2].decode()
        assert "Restart P." in page, "and the calendar draws it"
    finally:
        second.stop()


# ---------------------------------------------------------------- the checks a person runs from outside


def test_every_outside_check_passes_against_a_healthy_backend_for_a_listed_website(running):
    checks = check_public(running.base, SITE)
    failing = [(c.name, c.detail, c.fix) for c in checks if not c.ok]
    assert not failing, failing
    assert {c.name for c in checks} >= {"reachable", "ready", "calendar_health", "website_may_read", "open_times", "calendar_page_frameable", "tools_protected", "tools_read_only_surface"}


def test_the_outside_check_says_exactly_what_to_change_for_a_website_that_is_not_listed(running):
    checks = {c.name: c for c in check_public(running.base, "https://not-listed.example")}
    assert checks["website_may_read"].ok is False and "ARIA_SITE_ORIGINS" in checks["website_may_read"].fix and "https://not-listed.example" in checks["website_may_read"].fix
    assert checks["calendar_page_frameable"].ok is False and "ARIA_SITE_ORIGINS" in checks["calendar_page_frameable"].fix
    assert checks["tools_protected"].ok and checks["ready"].ok


def test_the_outside_check_stops_at_an_address_that_does_not_answer():
    checks = check_public("http://127.0.0.1:9", SITE)
    assert [c.name for c in checks] == ["reachable"] and checks[0].ok is False and "does not answer" in checks[0].fix


def test_the_outside_check_reports_a_backend_that_is_up_but_not_ready(running, monkeypatch):
    monkeypatch.setattr(running.app.lifecycle.store, "can_write", lambda: False)
    ready = {c.name: c for c in check_public(running.base, SITE)}["ready"]
    assert ready.ok is False and "volume" in ready.fix


# ---------------------------------------------------------------- the opt-in preferred technicians


PREFERENCE_CALLS = (("get_preferences", {"phone": "2125550142", "name": "Willa Hartwell"}),
                    ("save_preference", {"phone": "2125550142", "name": "Willa Hartwell", "service": "Swedish Massage", "staff": "Rowan"}),
                    ("remove_preference", {"phone": "2125550142", "name": "Willa Hartwell", "service": "Swedish Massage"}))


def test_the_preferred_technician_addresses_do_not_exist_unless_the_host_turned_them_on(running):
    for name, args in PREFERENCE_CALLS:
        assert running.tool(name, args)[0] == 404, name
    assert running.tool("find_appointments", {"phone": "2125550142", "name": "Willa Hartwell"})[0] == 200, "the rest of the lifecycle is exactly as it was"


def test_with_the_switch_on_they_answer_signed_calls_only_and_a_first_call_saves_nothing(tmp_path):
    run = Running(tmp_path, datetime(2026, 10, 22, 10, 0, tzinfo=TZ), preferences=True)
    try:
        assert run.config.preferences is True
        before = run.app.lifecycle.store.counts()
        listed = run.tool("get_preferences", PREFERENCE_CALLS[0][1])
        assert listed[0] == 200 and listed[1]["status"] == "no_preferences" and listed[1]["ok"] is True
        read_back = run.tool("save_preference", PREFERENCE_CALLS[1][1])
        assert read_back[0] == 200 and read_back[1]["status"] == "confirmation_required" and read_back[1]["confirmation_token"]
        assert run.tool("get_preferences", PREFERENCE_CALLS[0][1])[1]["status"] == "no_preferences", "the read-back saved nothing"
        for name, args in PREFERENCE_CALLS:
            assert run.tool(name, args, sign=False)[0] == 401 and run.tool(name, args, bearer="not-the-token")[0] == 401, name
        assert run.app.lifecycle.store.counts() == before
        assert run.tool("book_slot", {})[0] == 404
    finally:
        run.stop()


# ---------------------------------------------------------------- the fictional address and phone

FALLBACK_NUMBERS = ("2125550101", "2125550102", "2125550103", "2125550104")


def refusal_speech(run):
    """The sentence Aria says when she must send the caller to the salon: the fourth different number on one call is refused."""
    replies = [run.tool("find_appointments", {"phone": number}, call="fallback")[1] for number in FALLBACK_NUMBERS]
    assert [r["status"] for r in replies[:3]] == ["not_found"] * 3 and replies[3]["status"] == "refused"
    return replies[3]["speak"]


def test_without_a_showcase_file_the_site_and_the_spoken_fallback_carry_no_address_and_no_number(running):
    status, _headers, body = running.get("/")
    text = body.decode("utf-8")
    assert status == 200 and "Contact details are not configured in this prototype." in text and "Address:" not in text
    assert refusal_speech(running).endswith("Please contact the salon directly.")


def test_a_showcase_file_puts_the_same_address_and_number_on_the_site_and_in_the_spoken_fallback(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"location": "17 Example Row, Testville", "contact_phone": "(212) 555-0111"}), encoding="utf-8")
    run = Running(tmp_path, datetime(2026, 10, 22, 10, 0, tzinfo=TZ), showcase=str(path))
    try:
        text = run.get("/")[2].decode("utf-8")
        assert "<p>Address: 17 Example Row, Testville</p>" in text and 'tel:2125550111' in text and "Contact details are not configured" not in text
        assert refusal_speech(run).endswith("Please contact Serenity Spa at 2 1 2, 5 5 5, 0 1 1 1.")
        assert "Appointments made here exist only in this prototype calendar" in text, "the lifecycle label ending is kept"
    finally:
        run.stop()


# ---------------------------------------------------------------- the log line and a caller who goes away


def test_the_log_line_is_written_before_the_answer_goes_out(running, monkeypatch):
    import http.server

    original = http.server.BaseHTTPRequestHandler.send_response

    def marked(self, *args, **kwargs):
        running.lines.append("<<answer sent>>")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(http.server.BaseHTTPRequestHandler, "send_response", marked)
    assert running.get("/demo/health")[0] == 200
    lines = list(running.lines)
    logged = [i for i, line in enumerate(lines) if line.startswith("GET /demo/health 200")]
    assert logged and lines.index("<<answer sent>>") > logged[-1], "anyone who has read the answer can already find its line"


def test_a_caller_who_goes_away_mid_answer_is_still_logged_and_makes_no_noise(running, monkeypatch, capsys):
    import http.client
    import http.server

    def gone(self, *args, **kwargs):
        raise BrokenPipeError("the caller is gone")

    monkeypatch.setattr(http.server.BaseHTTPRequestHandler, "send_response", gone)
    connection = http.client.HTTPConnection("127.0.0.1", running.server.server_address[1], timeout=10)
    with pytest.raises((http.client.RemoteDisconnected, ConnectionError)):
        connection.request("GET", "/demo/health")
        connection.getresponse()
    connection.close()
    text = "\n".join(running.lines)
    assert "GET /demo/health 200" in text and "failed" not in text and capsys.readouterr().err == ""
    assert "ended early" not in text, "a caller going away mid-answer is handled where it happens; it never reaches the error handler"


def test_a_connection_error_outside_a_request_is_one_plain_line_and_any_other_failure_says_only_its_type(running, capsys):
    for error in (ConnectionResetError("secret detail " + KEY), TimeoutError("secret detail " + KEY), ValueError("secret detail " + KEY)):
        try:
            raise error
        except Exception:
            running.server.handle_error(None, ("203.0.113.9", 5))
    text = "\n".join(running.lines)
    assert text.count("a connection ended early") == 2 and "a request failed (ValueError)" in text
    assert KEY not in text and "203.0.113.9" not in text and "secret detail" not in text
    assert capsys.readouterr().err == ""
