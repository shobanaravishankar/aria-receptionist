"""The demo calendar page and its little web server, without a browser: structure, honesty of the label, safety, and what each card says."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import date, timedelta
from html.parser import HTMLParser

import pytest

from aria_booking.demo import DEMO_BUSINESS_ID, DEMO_LABEL
from aria_booking.demo.dataset import GRID_START, DemoDataset, DemoStaff
from aria_booking.demo.page import COLUMN_WIDTH, PX_PER_HOUR, render_page
from aria_booking.demo.server import CALENDAR_PATH, calendar_url, make_site
from aria_booking.roster import OBSERVED_CONTROLS, ROSTER_JS, parse_roster

TODAY = date(2026, 10, 13)
TOMORROW = TODAY + timedelta(days=1)


@pytest.fixture(scope="module")
def dataset():
    return DemoDataset(TODAY)


@pytest.fixture(scope="module")
def html(dataset):
    return render_page(dataset, TOMORROW)


class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def tags(html: str) -> list[tuple[str, dict]]:
    c = Collector()
    c.feed(html)
    return c.tags


def with_testid(html: str, testid: str):
    return [a for _t, a in tags(html) if a.get("data-testid") == testid]


# ---------------------------------------------------------------- the demo can never be mistaken for the real thing


def test_the_banner_is_the_first_thing_on_the_page_and_says_what_this_is(html):
    body = html.split("<body", 1)[1]
    assert body.index('id="demo-banner"') < body.index("<aside") and body.index('data-testid="demo-banner"') < body.index('data-testid="calendar-view"')
    banner = html.split('id="demo-banner"', 1)[1].split("</div>", 1)[0]
    assert DEMO_LABEL in banner and "invented" in banner and "not Booksy" in banner.replace("This is not Booksy", "not Booksy")
    assert f"<title>{DEMO_LABEL}</title>" in html and 'data-source="demo_calendar"' in html


def test_the_banner_cannot_be_scrolled_away_or_hidden_by_the_page_itself(html):
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    rule = re.search(r"#demo-banner\{([^}]*)\}", css).group(1)
    assert "flex:0 0 28px" in rule and "display:none" not in rule and "visibility:hidden" not in rule
    body_rule = next(line for line in css.splitlines() if line.startswith("body{"))
    assert "overflow:hidden" in body_rule, "the page does not scroll; only the calendar grid does, so the banner stays"
    assert "#demo-banner" not in html.split("<script>", 1)[1], "no script touches the banner"


def test_there_is_no_sign_in_form_no_credentials_and_no_way_to_send_anything(html):
    found = tags(html)
    assert not [t for t, _a in found if t in ("form", "textarea", "select", "iframe", "embed", "object", "link", "img")]
    assert not [a for t, a in found if t == "input" and a.get("type") not in ("checkbox", None)], "only the filter checkboxes and read-only boxes"
    for text in ("password", "sign in", "log in", "login", "otp", "card number", "mailto:", "tel:", "http://", "https://", "src="):
        assert text not in html.lower().replace("sign-in", ""), text


def test_there_is_no_customer_contact_detail_or_note_anywhere(html):
    assert not re.search(r"@\w|\b\d{3}[-. ]\d{3}[-. ]\d{4}\b|\+\d{6,}", html)
    assert "data-note" not in html and "notes" not in html.lower().split("<script>")[0].replace("notifications", "")


def test_clients_on_cards_are_the_dataset_fictional_names_only(dataset, html):
    plan = dataset.day_plan(TOMORROW)
    shown = set(re.findall(r'class="_text_mf7bh_57 ariaClient">([^<]*)<', html))
    assert shown == {v.client for p in plan.plans for v in p.visits}


# ---------------------------------------------------------------- structure the real reader relies on


def test_one_column_per_shown_person_with_a_synthetic_staff_id_and_a_matching_header(dataset, html):
    plan = dataset.day_plan(TOMORROW)
    columns = [a["data-resource"] for t, a in tags(html) if "data-resource" in a]
    assert columns == [p.staff.staff_id for p in plan.plans] and all(c.startswith("9100") for c in columns)
    names = re.findall(r'class="_name_10ie7_43 w-full">([^<]*)<', html)
    assert names == [p.staff.name for p in plan.plans]
    assert len(with_testid(html, "resource")) == len(plan.plans) and len(with_testid(html, "resource-content")) == len(plan.plans)


def test_the_header_shows_the_real_hours_format_for_everyone_and_nothing_for_a_diagnostic_missing_column(dataset, html):
    hours = re.findall(r'class="_hours_10ie7_48">([^<]*)<', html)
    assert "10:00 AM-8:00 PM" in hours and len(hours) == len(dataset.day_plan(TOMORROW).plans), "every standard column shows its hours"
    diagnostic = DemoDataset(TODAY, diagnostic_unknown_hours=("910010",))
    shown = re.findall(r'class="_hours_10ie7_48">([^<]*)<', render_page(diagnostic, TOMORROW))
    assert len(shown) == len(diagnostic.day_plan(TOMORROW).plans) - 1, "the diagnostic column shows no hours"


def test_the_staff_filter_is_the_real_shape_with_its_three_controls_and_every_staff_member(dataset, html):
    boxes = {a["data-testid"] for t, a in tags(html) if t == "input" and "data-testid" in a}
    assert "filtersValue-input" in boxes and "filtersValue_all-input" in boxes
    assert {f"filtersValue_{s.staff_id}-input" for s in dataset.roster} <= boxes
    labels = re.findall(r'<label for="(ariaFilter\d+)" data-testid="([^"]*)"[^>]*>(?:<span[^>]*>[^<]*</span>)?([^<]*)</label>', html)
    triples = {(f"{tid}-input", tid, name) for _for, tid, name in labels[:3]}
    assert triples == OBSERVED_CONTROLS


def test_the_real_roster_script_would_read_the_filter_as_a_complete_roster(dataset, html):
    items = []
    inputs = {a["id"]: a for t, a in tags(html) if t == "input" and a.get("id", "").startswith("ariaFilter")}
    for box_id, tid, name in re.findall(r'<label for="(ariaFilter\d+)" data-testid="([^"]*)"[^>]*>(?:<span[^>]*>[^<]*</span>)?([^<]*)</label>', html):
        items.append({"testid": inputs[box_id]["data-testid"], "label_testid": tid, "name": name.strip(), "checked": "checked" in inputs[box_id]})
    roster = parse_roster(items)
    assert roster.complete and [m.name for m in roster.members] == [s.name for s in dataset.roster]
    shown = {p.staff.name for p in dataset.day_plan(TOMORROW).plans}
    assert {m.name for m in roster.members if m.checked} == shown


def test_the_hour_axis_has_the_eleven_labels_of_the_real_view(html):
    labels = re.findall(r'class="_hourText_ultg9_16" style="top:(-?\d+)px">([^<]*)<', html)
    assert [t for _y, t in labels] == ["10 am", "11 am", "12 pm"] + [f"{h} pm" for h in range(1, 9)]
    ys = [int(y) for y, _t in labels]
    assert ys[0] == PX_PER_HOUR - 9 and {b - a for a, b in zip(ys, ys[1:])} == {PX_PER_HOUR}


def test_the_date_label_is_in_the_real_format_and_the_navigation_stays_inside_the_demo(html):
    assert '<div class="_month_1tz3u_20">Wed, 14 Oct</div>' in html and '<div class="_hours_1tz3u_55">10:00 AM - 8:00 PM</div>' in html
    targets = re.findall(r'data-href="([^"]*)"', html)
    assert targets and all(t.startswith(f"{CALENDAR_PATH}?date=") for t in targets), targets
    assert f"date={TODAY.isoformat()}" in "".join(targets) or "date=today" in "".join(targets)


def test_every_card_is_placed_by_its_time_with_the_real_height_rule(dataset, html):
    plan = dataset.day_plan(TOMORROW)
    expected = []
    for p in plan.plans:
        for v in p.visits:
            expected.append((round((v.start - GRID_START) * PX_PER_HOUR / 60), round((v.end - v.start) * PX_PER_HOUR / 60) - 1))
    found = [(int(t), int(h)) for t, h in re.findall(r'data-appointment-id="x" style="top:(\d+)px;height:(\d+)px"', html)]
    assert sorted(found) == sorted(expected) and found


def test_card_times_read_as_the_real_calendar_prints_them(dataset, html):
    texts = re.findall(r'<span class="hour_from">([^<]*)</span> - <span class="hour_till">([^<]*)</span>', html)
    assert texts and all(re.fullmatch(r"\d{1,2}:\d{2} (AM|PM)", a) and re.fullmatch(r"\d{1,2}:\d{2} (AM|PM)", b) for a, b in texts)
    assert ("3:45 PM", "5:15 PM") in texts


def test_non_working_blocks_shade_the_hours_outside_the_day_and_the_breaks(dataset, html):
    blocks = len(re.findall(r'data-testid="non-working-event"', html))
    plan = dataset.day_plan(TOMORROW)
    expected = sum((p.hours[0] > GRID_START) + (p.hours[1] < 21 * 60) + len(p.breaks) for p in plan.plans if p.shown_hours)
    assert blocks == expected
    assert 'data-label="Break"' in html


def test_the_person_without_hours_is_shaded_nowhere():
    dataset = DemoDataset(TODAY, diagnostic_unknown_hours=("910010",))
    page = render_page(dataset, TOMORROW)
    column = page.split(f'data-resource="{dataset.roster[-1].staff_id}"', 1)[1].split("</div></div></div>", 1)[0]
    assert "non-working-event" not in column.split('class="_calendarColumn_1pknx_16"', 1)[0]


# ---------------------------------------------------------------- not covered, escaping, determinism


def test_a_date_outside_the_dataset_shows_no_columns_and_says_why(dataset):
    page = render_page(dataset, TODAY + timedelta(days=200))
    assert 'data-testid="demo-no-data"' in page and "has no data for" in page and dataset.first_day.isoformat() in page
    assert "data-resource" not in page and 'data-appointment-id' not in page and DEMO_LABEL in page


def test_names_are_escaped():
    odd = DemoStaff("910098", "<script>alert(1)</script>", frozenset(range(7)), (600, 1200), frozenset({"facial"}))
    page = render_page(DemoDataset(TODAY, extra_staff=[odd]), TOMORROW)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_the_same_day_renders_identically_twice(dataset):
    assert render_page(dataset, TOMORROW) == render_page(dataset, TOMORROW)


def test_the_page_is_independent_of_the_real_clock(dataset):
    assert render_page(DemoDataset(TODAY), TOMORROW) == render_page(DemoDataset(TODAY), TOMORROW)


def test_columns_are_250_wide_and_the_wrapper_is_as_wide_as_the_columns(dataset, html):
    n = len(dataset.day_plan(TOMORROW).plans)
    assert f"width:{n * COLUMN_WIDTH}px;height:960px" in html and COLUMN_WIDTH == 250


# ---------------------------------------------------------------- the little web server


@pytest.fixture()
def server(dataset):
    import threading

    site = make_site(dataset, 0)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    yield site.server_address[1]
    site.shutdown()
    site.server_close()


def fetch(port, path, method="GET"):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"x" if method != "GET" else None)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8")


def test_the_server_serves_the_page_with_the_source_header_and_no_caching(server):
    status, headers, body = fetch(server, CALENDAR_PATH + f"?date={TOMORROW.isoformat()}&view=day&staffers=working")
    assert status == 200 and headers["X-Demo-Source"] == "demo_calendar" and headers["Cache-Control"] == "no-store" and DEMO_LABEL in body


def test_today_means_the_demo_date_and_the_root_redirects_there(server):
    status, _h, body = fetch(server, CALENDAR_PATH + "?date=today")
    assert status == 200 and f'data-demo-date="{TODAY.isoformat()}"' in body
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as info:
        opener.open(f"http://127.0.0.1:{server}/", timeout=10)
    assert info.value.code == 302 and CALENDAR_PATH in info.value.headers["Location"]


def test_bad_dates_unknown_paths_and_every_write_method_are_refused(server):
    assert fetch(server, CALENDAR_PATH + "?date=banana")[0] == 400
    assert fetch(server, "/somewhere/else")[0] == 404
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert fetch(server, CALENDAR_PATH, method)[0] == 405, method


def test_the_health_endpoint_names_the_source_and_what_is_covered(server, dataset):
    status, _h, body = fetch(server, "/demo/health")
    data = json.loads(body)
    assert status == 200 and data["source"] == "demo_calendar" and data["label"] == DEMO_LABEL
    assert data["today"] == TODAY.isoformat() and data["covers"] == [dataset.first_day.isoformat(), dataset.last_day.isoformat()]


def test_the_server_binds_to_the_loopback_interface_only(dataset):
    with pytest.raises(ValueError):
        make_site(dataset, 0, host="0.0.0.0")
    site = make_site(dataset, 0)
    try:
        assert site.server_address[0] == "127.0.0.1"
    finally:
        site.server_close()


def test_the_calendar_url_helper_matches_the_served_path():
    assert calendar_url(8790) == f"http://127.0.0.1:8790/pro/en-us/{DEMO_BUSINESS_ID}/calendar?date=today&view=day&staffers=working"
