"""The website's two in-page calendar views, checked without a browser: where they are linked from, how they are built, what the page may read from the calendar, and that nothing
secret or outside this machine is in them. The real-browser behaviour (same tab, Back, a call kept alive) is in test_showcase_views_browser.py. No network beyond loopback.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from aria_booking.demo.dataset import ROSTER, DemoDataset, demo_services
from aria_booking.demo.open_times import open_times
from aria_booking.demo.server import OPEN_TIMES_PATH, make_site, page_origins
from aria_booking.showcase import SHOWCASE
from aria_booking.showcase.site import SITE_DIR, build_site, calendar_link, calendar_origin
from aria_booking.showcase.site.bookable import bookable_data, script_tag

from test_showcase_site import Page, SETTINGS

TZ = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 14)
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)
TOMORROW = (TODAY + timedelta(days=1)).isoformat()
SWEDISH60 = "massage-swedish-massage-60"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("views-site")
    build_site(out, settings=SETTINGS, calendar_port=8790)
    html = (out / "index.html").read_text(encoding="utf-8")
    page = Page()
    page.feed(html)
    return out, html, page


def by_testid(page, testid):
    return [a for _t, a in page.tags if a.get("data-testid") == testid]


# ---------------------------------------------------------------- the four entry points


ENTRY_POINTS = [("nav-calendar", "#calendar"), ("nav-staff-calendar", "#staff-calendar"), ("calendar-link", "#calendar"), ("hours-calendar", "#calendar"),
                ("hours-staff-calendar", "#staff-calendar"), ("mobile-calendar", "#calendar")]


@pytest.mark.parametrize("testid,target", ENTRY_POINTS)
def test_each_entry_point_is_a_plain_in_page_link_that_opens_nothing_new(built, testid, target):
    _out, _html, page = built
    found = by_testid(page, testid)
    assert len(found) == 1, f"exactly one {testid}"
    assert found[0].get("href") == target and not found[0].get("target") and "download" not in found[0]


def test_the_staff_calendar_is_linked_visibly_from_the_header_and_the_hours_section(built):
    _out, _html, page = built
    for testid in ("nav-staff-calendar", "hours-staff-calendar"):
        link = by_testid(page, testid)[0]
        assert "hidden" not in link and "aria-hidden" not in link and "display:none" not in (link.get("style") or "")


def test_the_phone_bar_and_the_no_script_fallback_use_the_configured_calendar_address(built):
    out, html, page = built
    assert 'href="#calendar"' in html
    fallbacks = [m for m in re.findall(r"<noscript>(.*?)</noscript>", html, re.S) if "calendar" in m]
    assert len(fallbacks) == 1 and f'href="{calendar_link(8790).replace("&", "&amp;")}"' in fallbacks[0], "without scripts the hours section still reaches the configured calendar"


# ---------------------------------------------------------------- the views


def test_the_home_page_and_each_view_are_separate_sections_and_only_the_home_page_starts_open(built):
    _out, _html, page = built
    home = page.find("div", id="home") or page.find("main", id="home") or page.find("section", id="home")
    assert home and "hidden" not in home[0]
    for view in ("view-calendar", "view-staff"):
        section = page.find("section", id=view)
        assert section and "hidden" in section[0], f"{view} starts hidden"


def test_each_view_has_a_visible_back_control_named_for_the_business(built):
    _out, html, page = built
    backs = [a for _t, a in page.tags if "data-home" in a]
    assert len(backs) == 2 and all(a.get("href") == "#top" and not a.get("target") for a in backs)
    assert html.count(f"Back to {SHOWCASE.name}") == 2


def test_the_staff_calendar_frame_loads_nothing_until_opened_and_is_labelled(built):
    _out, _html, page = built
    frame = page.find("iframe")[0]
    assert "src" not in frame and frame["data-src"] == calendar_link(8790) and frame.get("title")
    assert frame.get("id") == "staff-frame" and frame.get("data-testid") == "staff-frame"


def test_the_open_times_form_has_labelled_fields_and_a_live_results_region(built):
    _out, _html, page = built
    form = page.find("form", id="open-times-form")
    assert form
    for field in ("ot-family", "ot-length", "ot-date", "ot-staff"):
        assert any(a.get("for") == field for t, a in page.tags if t == "label"), f"{field} has a label"
    results = page.find("div", id="ot-results")[0]
    assert results.get("aria-live") == "polite"


def test_a_call_in_progress_can_be_put_away_and_brought_back(built):
    _out, _html, page = built
    assert page.find("button", id="aria-hide") and "hidden" not in page.find("button", id="aria-hide")[0], "the avatar can always be put away, and doing so never ends a call"
    bar = page.find("div", id="call-bar")
    assert bar and "hidden" in bar[0] and bar[0].get("role") == "region"
    assert page.find("button", id="call-show") and page.find("button", id="call-end")


def test_the_configured_calendar_port_is_used_everywhere_and_the_default_is_not_hard_coded(tmp_path):
    build_site(tmp_path, settings=SETTINGS, calendar_port=9911)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "localhost:8790" not in html and "8790" not in html
    page = Page()
    page.feed(html)
    assert page.find("iframe")[0]["data-src"] == calendar_link(9911) and calendar_origin(9911) == "http://localhost:9911"
    data = json.loads(re.search(r"window.SERENITY_BOOKABLE = (.*?);</script>", html, re.S).group(1))
    assert data["calendarOrigin"] == "http://localhost:9911"
    assert "8790" not in (SITE_DIR / "views.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------- what the form offers


def test_the_form_offers_only_treatments_the_calendar_can_check_with_only_the_technicians_who_do_them(built):
    _out, html, _page = built
    data = json.loads(re.search(r"window.SERENITY_BOOKABLE = (.*?);</script>", html, re.S).group(1))
    dataset = DemoDataset(TODAY)
    roster = {s.name: s for s in ROSTER}
    ids = 0
    for group in data["groups"]:
        assert group["families"], group["category"]
        for family in group["families"]:
            for variant in family["variants"]:
                ids += 1
                service = dataset.registry.get(variant["service_id"])
                assert service is not None, f"{variant['service_id']} is checkable"
                assert variant["technicians"] == [s.name for s in ROSTER if service.eligible(s.name, s.staff_id)], "eligibility comes from the calendar's own rules"
                assert variant["minutes"] and variant["price"] is not None and variant["label"]
    assert ids == len({sid for sid, _c in [(s.service_id, c) for s, c in demo_services(ROSTER)]}) or ids > 20
    assert all(name in roster for g in data["groups"] for f in g["families"] for v in f["variants"] for name in v["technicians"])


def test_the_embedded_data_cannot_end_its_script_element_and_carries_no_client_details(built):
    _out, html, _page = built
    assert script_tag({"x": "</script><b>"}).count("</script>") == 1
    data = re.search(r"window.SERENITY_BOOKABLE = (.*?);</script>", html, re.S).group(1)
    dataset = DemoDataset(TODAY)
    clients = {v.client for p in dataset.day_plan(TODAY + timedelta(days=1)).plans for v in p.visits}
    assert clients and not any(c in data for c in clients)
    assert not re.search(r"\d{3}[-. )]+\d{3}[-. ]\d{4}", data), "no phone number"


# ---------------------------------------------------------------- nothing secret, nothing outside this machine


def test_the_view_script_holds_no_credential_and_reaches_only_the_configured_calendar():
    js = (SITE_DIR / "views.js").read_text(encoding="utf-8")
    assert not re.search(r"https?://", js), "no address is written into the script: the calendar address comes from the page's data"
    for word in ("apikey", "api_key", "authorization", "bearer", "secret", "password", "token", "public_key", "agent_id"):
        assert word not in js.lower().replace(" ", ""), word
    assert "window.open" not in js and "_blank" not in js and not re.search(r"setAttribute\(\s*[\"']target[\"']", js) and not re.search(r"\.target\s*=[^=]", js)
    fetches = re.findall(r"fetch\(([^,)]*)", js)
    assert len(fetches) == 3 and all(f.strip() == "url" or "data.calendarOrigin" in f for f in fetches), fetches
    assert sorted(re.findall(r"/demo/[a-z-]+", js)) == ["/demo/changes", "/demo/health", "/demo/open-times"], "the only calendar addresses it asks"
    assert [m for m in re.findall(r"var url = ([^;]*)", js) if "data.calendarOrigin" in m], "the request address is built from the configured calendar origin"
    assert not re.search(r"method\s*:\s*[\"'](POST|PUT|PATCH|DELETE)", js, re.I)


def test_the_view_script_never_uses_browser_history_to_go_home_and_drops_late_answers():
    js = (SITE_DIR / "views.js").read_text(encoding="utf-8")
    code = "\n".join(line for line in js.splitlines() if not line.strip().startswith(("*", "/*", "//")))
    assert "history.back" not in code and "history.go(" not in code and "history.forward" not in code, "Home must not depend on a history that the staff frame's days also fill"
    assert "replaceState" in code
    assert code.count("mine === latest") == 1 and code.count("mine !== latest") == 1, "both the answer and the failure of a search are checked against the newest search"
    assert "++latest" in code and re.search(r"fetch\(url[^)]*\)\.then\(function \(r\) \{ return r\.json\(\); \}\)\.then\(function \(r\) \{\s*if \(mine === latest\) render\(r\);", code)
    assert re.search(r"visibilityState", code), "the watcher pauses while the page is hidden"
    assert "setInterval" not in code, "the watcher is a chain of single timeouts that backs off, never a fixed interval that piles up"


def test_the_framed_calendar_page_replaces_its_own_history_and_reports_its_day_only_when_framed():
    from aria_booking.demo.page import render_page

    html = render_page(DemoDataset(TODAY), TODAY)
    script = html[html.rindex("<script>"):]
    assert "embedded=window.parent!==window" in script
    assert "if(embedded){location.replace(url);}else{location.href=url;}" in script, "on its own the page navigates as before"
    assert script.count("postMessage") == 1 and script.index("postMessage") > script.index("if(embedded){\n    var key"), "it reports only when framed"
    assert "serenity-calendar" in script and "'*'" in script and "location.href" in script
    assert "document.cookie" not in script and "localStorage" not in script, "nothing stored beyond its own scroll position for the session"


def test_the_page_has_no_outside_address_in_the_new_views(built):
    out, html, _page = built
    views = re.search(r'<section class="view" id="view-calendar".*?</section>\s*<section class="view" id="view-staff".*?</section>', html, re.S)
    assert views, "the two views are found"
    hosts = set(re.findall(r"https?://([^/\"' )]+)", views.group(0)))
    assert hosts <= {"localhost:8790"}, f"the views point at the configured calendar and nowhere else: {hosts}"
    assert "https://" not in (out / "views.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------- the calendar's read-only answer


@pytest.fixture(scope="module")
def calendar():
    dataset = DemoDataset(TODAY)
    server = make_site(dataset, 0, allowed_origins=page_origins(5174), clock=lambda: NOW)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield dataset, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def fetch(url, *, origin=None, method="GET"):
    request = urllib.request.Request(url, method=method, headers={"Origin": origin} if origin else {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_the_open_times_answer_equals_the_engine_and_is_json(calendar):
    dataset, base = calendar
    status, headers, body = fetch(f"{base}{OPEN_TIMES_PATH}?service_id={SWEDISH60}&date={TOMORROW}")
    assert status == 200 and headers["Content-Type"].startswith("application/json") and headers["Cache-Control"] == "no-store"
    assert json.loads(body) == open_times(dataset, SWEDISH60, date.fromisoformat(TOMORROW), tz=TZ, now=NOW)
    status, _h, body = fetch(f"{base}{OPEN_TIMES_PATH}?service_id={SWEDISH60}&date={TOMORROW}&staff=Sienna")
    assert status == 200 and {t for time in json.loads(body)["times"] for t in time["technicians"]} == {"Sienna"}


def test_todays_times_follow_the_servers_own_clock_and_its_minimum_notice(calendar):
    dataset, base = calendar
    status, _h, body = fetch(f"{base}{OPEN_TIMES_PATH}?service_id={SWEDISH60}&date={TODAY.isoformat()}")
    reply = json.loads(body)
    assert status == 200 and reply == open_times(dataset, SWEDISH60, TODAY, tz=TZ, now=NOW)
    assert reply["times"] and all(t["start"] >= "13:00" for t in reply["times"]), "nothing sooner than an hour after the clock's 12:00"


@pytest.mark.parametrize("origin", ["http://localhost:5174", "http://127.0.0.1:5174"])
def test_the_prepared_page_may_read_the_open_times_and_the_health_answer(calendar, origin):
    _dataset, base = calendar
    for path in (f"{OPEN_TIMES_PATH}?service_id={SWEDISH60}&date={TOMORROW}", "/demo/health"):
        status, headers, _body = fetch(base + path, origin=origin)
        assert status == 200 and headers["Access-Control-Allow-Origin"] == origin and headers["Vary"] == "Origin"


@pytest.mark.parametrize("origin", ["http://localhost:5175", "https://example.com", "http://127.0.0.1:80", "null", "http://localhost:5174.evil.test"])
def test_no_other_page_is_told_it_may_read_them(calendar, origin):
    _dataset, base = calendar
    for path in (f"{OPEN_TIMES_PATH}?service_id={SWEDISH60}&date={TOMORROW}", "/demo/health"):
        status, headers, _body = fetch(base + path, origin=origin)
        assert status == 200 and "Access-Control-Allow-Origin" not in headers


def test_a_request_without_an_origin_gets_no_cors_header_and_the_answer_is_never_cached(calendar):
    _dataset, base = calendar
    status, headers, _body = fetch(f"{base}{OPEN_TIMES_PATH}?service_id={SWEDISH60}&date={TOMORROW}")
    assert status == 200 and "Access-Control-Allow-Origin" not in headers and headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("query", ["", "service_id=", f"service_id={SWEDISH60}", f"service_id={SWEDISH60}&date=tomorrow", f"service_id={SWEDISH60}&date=2026-13-45",
                                   "service_id=" + "x" * 200 + f"&date={TOMORROW}", f"service_id={SWEDISH60}&date={TOMORROW}&staff=" + "y" * 200])
def test_a_bad_request_is_a_400_with_json_and_the_same_cors_rule(calendar, query):
    _dataset, base = calendar
    status, headers, body = fetch(f"{base}{OPEN_TIMES_PATH}?{query}", origin="http://localhost:5174")
    assert status == 400 and json.loads(body)["status"] == "error" and headers["Access-Control-Allow-Origin"] == "http://localhost:5174"


def test_an_unknown_treatment_or_technician_gets_an_honest_answer_not_a_crash(calendar):
    _dataset, base = calendar
    status, _h, body = fetch(f"{base}{OPEN_TIMES_PATH}?service_id=nothing-here&date={TOMORROW}")
    assert status == 200 and json.loads(body)["status"] == "error" and not json.loads(body).get("times")
    status, _h, body = fetch(f"{base}{OPEN_TIMES_PATH}?service_id={SWEDISH60}&date={TOMORROW}&staff=Nobody")
    assert status == 200 and not json.loads(body).get("times")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_nothing_can_be_written_through_the_open_times_address(calendar, method):
    dataset, base = calendar
    before = open_times(dataset, SWEDISH60, date.fromisoformat(TOMORROW), tz=TZ, now=NOW)
    status, _h, _body = fetch(f"{base}{OPEN_TIMES_PATH}?service_id={SWEDISH60}&date={TOMORROW}", method=method, origin="http://localhost:5174")
    assert status == 405
    assert open_times(dataset, SWEDISH60, date.fromisoformat(TOMORROW), tz=TZ, now=NOW) == before


# ---------------------------------------------------------------- "has anything changed?"


def test_the_change_stamp_over_http_is_static_for_the_plain_calendar_and_carries_no_personal_detail(calendar):
    dataset, base = calendar
    status, headers, body = fetch(f"{base}/demo/changes", origin="http://localhost:5174")
    assert status == 200 and json.loads(body) == {"stamp": f"{TODAY.isoformat()}|static", "today": TODAY.isoformat()}
    assert headers["Access-Control-Allow-Origin"] == "http://localhost:5174" and headers["Cache-Control"] == "no-store"
    status, headers, _b = fetch(f"{base}/demo/changes", origin="https://example.com")
    assert status == 200 and "Access-Control-Allow-Origin" not in headers
    assert fetch(f"{base}/demo/changes", method="POST", origin="http://localhost:5174")[0] == 405


def test_the_stamp_changes_with_every_booking_cancellation_and_move_and_only_then(tmp_path):
    from aria_booking.appointments.lifecycle import open_lifecycle
    from aria_booking.appointments.models import NewBooking, to_epoch

    lc = open_lifecycle(tmp_path / "stamp.sqlite3", today=TODAY, real_now=lambda: NOW, seed=False)
    seen = [lc.dataset.change_stamp()]
    assert seen[0] == lc.dataset.change_stamp(), "asking twice changes nothing"
    svc = lc.dataset.registry.get(SWEDISH60)
    start = to_epoch(datetime(2026, 10, 15, 16, 0, tzinfo=TZ))
    booked = lc.store.book(NewBooking("Stamp Person", "+12125550123", SWEDISH60, svc.booksy_name, "massage", "910006", start, svc.duration_minutes,
                                      svc.buffer_before_minutes, svc.buffer_after_minutes), validate=lc.judge.validator())
    assert booked.status == "booked"
    seen.append(lc.dataset.change_stamp())
    moved = lc.store.reschedule(booked.appointment.reference, start_utc=to_epoch(datetime(2026, 10, 15, 17, 0, tzinfo=TZ)), validate=lc.judge.validator())
    assert moved.status == "rescheduled"
    seen.append(lc.dataset.change_stamp())
    assert lc.store.cancel(booked.appointment.reference).status == "cancelled"
    seen.append(lc.dataset.change_stamp())
    assert len(set(seen)) == 4, f"every change shows: {seen}"
    assert lc.dataset.change_stamp() == seen[-1]
    assert not any(word in "".join(seen) for word in ("Stamp", "2125550123", "910006")), "no names, numbers or ids"
    assert all(stamp.startswith(f"{TODAY.isoformat()}|") for stamp in seen)


def test_the_stamp_over_http_follows_the_stored_appointments(tmp_path):
    from aria_booking.appointments.lifecycle import open_lifecycle
    from aria_booking.appointments.models import NewBooking, to_epoch

    lc = open_lifecycle(tmp_path / "http-stamp.sqlite3", today=TODAY, real_now=lambda: NOW, seed=False)
    server = make_site(lc.dataset, 0, allowed_origins=page_origins(5174), clock=lambda: NOW)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        before = json.loads(fetch(f"{base}/demo/changes")[2])["stamp"]
        svc = lc.dataset.registry.get(SWEDISH60)
        lc.store.book(NewBooking("Http Person", "+12125550124", SWEDISH60, svc.booksy_name, "massage", "910006", to_epoch(datetime(2026, 10, 15, 16, 0, tzinfo=TZ)),
                                 svc.duration_minutes, svc.buffer_before_minutes, svc.buffer_after_minutes), validate=lc.judge.validator())
        after = json.loads(fetch(f"{base}/demo/changes")[2])["stamp"]
    finally:
        server.shutdown()
        server.server_close()
    assert before != after


def test_page_origins_names_exactly_the_two_addresses_the_prepared_page_is_opened_at():
    assert page_origins(5174) == ("http://localhost:5174", "http://127.0.0.1:5174")
    assert page_origins(8000)[0] == "http://localhost:8000"


def test_with_no_origins_given_no_page_is_allowed():
    server = make_site(DemoDataset(TODAY), 0, clock=lambda: NOW)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _s, headers, _b = fetch(f"http://127.0.0.1:{server.server_address[1]}{OPEN_TIMES_PATH}?service_id={SWEDISH60}&date={TOMORROW}", origin="http://localhost:5174")
        assert "Access-Control-Allow-Origin" not in headers
    finally:
        server.shutdown()
        server.server_close()
