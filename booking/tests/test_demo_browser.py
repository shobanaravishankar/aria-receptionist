"""The demo calendar in a REAL (visible) browser: the page is what the voice answers say, the layout matches the reference geometry, and nothing is clipped.

* PARITY: the page is read with the real capture script and the real parser (the live code path) and must equal the dataset the voice tools answer from.
* GEOMETRY: key elements are compared with rectangles taken from a redacted capture of the real day view at 1600 x 789 (the demo adds a 28 px banner).
* HONEST LIMIT: colours, fonts, icons and borders are not in that capture and no reference screenshot exists, so they are NOT compared or claimed.
Loopback only (the same guard as the smoke tests); no Booksy.
"""

from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

from aria_booking.calendar_parser import CalendarParseError
from aria_booking.config import Config
from aria_booking.demo import DEMO_BUSINESS_ID, DEMO_LABEL
from aria_booking.demo.dataset import DemoDataset
from aria_booking.demo.driver import DemoCalendarDriver
from aria_booking.demo.screenshots import capture
from aria_booking.demo.server import calendar_url, make_site
from aria_booking.demo.visual import BANNER_PX, read_png
from aria_booking.playwright_driver import PlaywrightBooksyDriver

from test_playwright_smoke import LoopbackOnlySession, channel, never_left_this_machine  # noqa: F401  (fixtures)

TODAY = date(2026, 10, 13)
REFERENCE = json.loads((Path(__file__).parent / "fixtures" / "demo_reference_geometry.json").read_text(encoding="utf-8"))
TOMORROW = TODAY + timedelta(days=1)


@pytest.fixture(scope="module")
def dataset():
    return DemoDataset(TODAY)


@pytest.fixture(scope="module")
def port(dataset):
    site = make_site(dataset, 0)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    yield site.server_address[1]
    site.shutdown()
    site.server_close()


def make_driver(tmp_path, port, channel, **kw):
    cfg = Config(business_id=DEMO_BUSINESS_ID, local_dir=tmp_path, browser_channel=channel)
    kw.setdefault("load_timeout_seconds", 10)
    return PlaywrightBooksyDriver(cfg, session_factory=lambda: LoopbackOnlySession(cfg), notify=lambda m: None,
                                  url_for_day=lambda day: calendar_url(port, day), **kw)


@pytest.fixture(scope="module")
def driver(tmp_path_factory, port, channel):
    drv = make_driver(tmp_path_factory.mktemp("demo-browser"), port, channel)
    yield drv
    drv.close()


def goto(driver, day, width=1600, height=817):
    def work(page):
        page.set_viewport_size({"width": width, "height": height})
        page.goto(calendar_url(driver_port(driver), day.isoformat()), wait_until="load")
        page.wait_for_selector('[data-testid="calendar-grid-day"]')
        return True

    return driver._run(work, 30)


def driver_port(driver) -> int:
    return int(driver._url_for_day("today").split(":")[2].split("/")[0])


def measure(driver, script, arg=None):
    return driver._run(lambda page: page.evaluate(script, arg) if arg is not None else page.evaluate(script), 30)


# ---------------------------------------------------------------- the page IS the data: the real reader reads exactly what the voice tools answer from


def comparable(snapshot):
    return [
        (sd.staff, sd.staff_id, None if sd.working is None else [(w.start, w.end) for w in sd.working],
         sorted((a.interval.start, a.interval.end) for a in sd.appointments or ()), sd.time_off)
        for sd in snapshot.staff_days
    ]


def test_the_page_passes_the_real_sign_in_evidence_check(driver):
    assert driver.verify_business() == DEMO_BUSINESS_ID


def test_the_real_reader_reads_the_page_exactly_as_the_dataset_answers(driver, dataset):
    answers = DemoCalendarDriver(dataset)
    days = [dataset.first_day, *(TODAY + timedelta(days=n) for n in (0, 1, 2, 3, 4, 5, 6, 9, 13, 20)), dataset.last_day]
    assert {d.weekday() for d in days} == set(range(7)), "every day of the week is exercised"
    for day in days:
        assert comparable(driver.read_day(day)) == comparable(answers.read_day(day)), day.isoformat()


def test_the_scripted_day_read_from_the_page_has_every_pinned_case(driver):
    by_name = {sd.staff: sd for sd in driver.read_day(TOMORROW).staff_days}
    assert "Imogen" not in by_name, "off duty"
    assert by_name["Delphine"].working is None, "hours not shown: unknown"
    assert [(a.interval.start.hour, a.interval.start.minute) for a in by_name["Marisol"].appointments] == [(10, 0), (15, 45)]
    assert [(w.start.hour, w.end.hour) for w in by_name["Rowan"].working] == [(10, 15), (17, 20)], "her break splits the day"
    assert by_name["Sienna"].staff_id == "910006" and all(sd.staff_id.startswith("9100") for sd in by_name.values())


def test_a_date_outside_the_dataset_is_refused_by_the_reader_just_as_by_the_driver(driver, dataset):
    outside = dataset.last_day + timedelta(days=1)
    with pytest.raises(CalendarParseError):
        driver.read_day(outside)
    with pytest.raises(CalendarParseError):
        DemoCalendarDriver(dataset).read_day(outside)


# ---------------------------------------------------------------- geometry against the reference capture


GEOMETRY_SCRIPT = """(testid) => { const e = document.querySelector(`[data-testid="${testid}"]`); if (!e) return null;
  const r = e.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; }"""


def test_the_layout_matches_the_reference_rectangles_at_the_reference_viewport(driver):
    goto(driver, TOMORROW, 1600, 789 + BANNER_PX)
    banner = measure(driver, "document.getElementById('demo-banner').getBoundingClientRect().height")
    assert banner == BANNER_PX
    off_by = {}
    for testid, ref in REFERENCE["elements"].items():
        got = measure(driver, GEOMETRY_SCRIPT, testid)
        assert got is not None, f"{testid} is missing from the demo page"
        x, y, w, h = got[0], got[1] - BANNER_PX, got[2], got[3]
        worst = max(abs(x - ref[0]), abs(y - ref[1]), abs(w - ref[2]), abs(h - ref[3]))
        if worst > 2:
            off_by[testid] = (round(worst, 1), ref, [round(v) for v in (x, y, w, h)])
    assert not off_by, f"elements more than 2 px away from the reference: {off_by}"


def test_columns_headers_hour_rows_and_cards_follow_the_reference_numbers(driver, dataset):
    goto(driver, TOMORROW, 1600, 789 + BANNER_PX)
    data = measure(driver, """() => {
      const rect = e => { const r = e.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; };
      const headers = [...document.querySelectorAll('[data-testid="resource"]')].map(rect);
      const labels = [...document.querySelectorAll('[class*="_hourText_"]')].map(e => rect(e)[1]);
      const cards = [...document.querySelectorAll('[data-appointment-id]')].map(rect);
      const columns = [...document.querySelectorAll('[class*="_calendarGrid--overlay_"] [class*="_calendarColumn_"]')].map(rect);
      return {headers, labels, cards, columns}; }""")
    assert {round(b[0] - a[0]) for a, b in zip(data["headers"], data["headers"][1:])} == {REFERENCE["column_width"]}
    assert {round(h[3]) for h in data["headers"]} == {REFERENCE["header_height"]}
    assert {round(b - a) for a, b in zip(data["labels"], data["labels"][1:])} == {REFERENCE["hour_spacing"]}
    assert round(data["labels"][0]) - BANNER_PX == REFERENCE["first_hour_label_top"]
    column_x = {round(c[0]) for c in data["columns"]}
    for x, y, w, h in data["cards"]:
        assert round(w) == REFERENCE["card_width"][0]
        minutes = (h + 1) * 3 / 4
        assert abs(minutes - 5 * round(minutes / 5)) < 0.3, f"height = minutes x 4/3 - 1, the rule the real cards follow (19, 39, 59 ... 199): {h}"
        assert round(x) - 3 in column_x, "every card is inset 3 px from its column's left edge"
    assert data["cards"]


def test_the_demo_header_and_the_hours_axis_read_like_the_real_ones(driver):
    goto(driver, TOMORROW)
    texts = measure(driver, """() => ({
      month: document.querySelector('[class*="_month_1tz3u"]').textContent, hours: document.querySelector('[class*="_hours_1tz3u"]').textContent,
      axis: [...document.querySelectorAll('[class*="_hourText_"]')].map(e => e.textContent), headerHours: [...document.querySelectorAll('[class*="_hours_10ie7"]')].map(e => e.textContent)})""")
    assert texts["month"] == "Wed, 14 Oct" and texts["hours"] == "10:00 AM - 8:00 PM"
    assert texts["axis"][:3] == ["10 am", "11 am", "12 pm"] and texts["axis"][-1] == "8 pm"
    assert "10:00 AM-8:00 PM" in texts["headerHours"]


# ---------------------------------------------------------------- nothing clipped, overlapping or hidden


@pytest.mark.parametrize("width,height", [(1600, 817), (1280, 720)])
def test_nothing_is_clipped_overlapping_or_off_the_page(driver, width, height):
    goto(driver, TOMORROW, width, height)
    problems = measure(driver, """() => {
      const out = [], rect = e => e.getBoundingClientRect();
      if (document.documentElement.scrollWidth > innerWidth + 1) out.push('the page scrolls sideways');
      if (document.documentElement.scrollHeight > innerHeight + 1) out.push('the page scrolls down');
      const banner = document.getElementById('demo-banner'); const br = rect(banner);
      if (br.y !== 0 || br.height < 20 || banner.querySelector('strong').scrollWidth > banner.querySelector('strong').clientWidth + 1) out.push('the demo banner is hidden or clipped');
      const columns = [...document.querySelectorAll('[class*="_calendarGrid--overlay_"] [class*="_calendarColumn_"]')];
      columns.forEach((col, ci) => {
        const cr = rect(col), cards = [...col.querySelectorAll('[class*="_cardWrap_"]')].map(c => [c, rect(c)]);
        cards.forEach(([c, r]) => {
          if (r.left < cr.left - 0.5 || r.right > cr.right + 0.5 || r.top < cr.top - 0.5 || r.bottom > cr.bottom + 0.5) out.push('a card leaves its column ' + ci);
          const h = c.querySelector('[class*="_header_"]'); if (h && h.scrollWidth > h.clientWidth + 1) out.push('a card time is clipped ' + ci);
        });
        const real = cards.filter(([c]) => c.hasAttribute('data-appointment-id'));
        for (let i = 0; i < real.length; i++) for (let j = i + 1; j < real.length; j++) {
          const a = real[i][1], b = real[j][1];
          if (a.top < b.bottom - 1 && b.top < a.bottom - 1) out.push('two appointments overlap in column ' + ci);
        }
      });
      document.querySelectorAll('[class*="_name_10ie7"]').forEach(n => { if (n.scrollWidth > n.clientWidth + 1 && n.clientWidth) out.push('a staff name is clipped'); });
      const bars = ['calendar-filters-toggle', 'calendar-grid-tabs', 'today-button', 'date-switcher-previous', 'date-switcher-label', 'date-switcher-next', 'settings-icon', 'referral-icon', 'notifications-icon']
        .map(id => [id, rect(document.querySelector(`[data-testid="${id}"]`))]);
      for (let i = 0; i < bars.length; i++) for (let j = i + 1; j < bars.length; j++) {
        const a = bars[i][1], b = bars[j][1];
        if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5) out.push('toolbar items overlap: ' + bars[i][0] + ' / ' + bars[j][0]);
      }
      return out; }""")
    assert problems == []


def test_scrolling_the_grid_moves_the_hour_axis_and_the_staff_headers_but_never_the_banner(driver):
    goto(driver, TOMORROW)
    before = measure(driver, """() => { const g = document.getElementById('ariaGrid'), r = e => e.getBoundingClientRect();
      return {label: r(document.querySelector('[class*="_hourText_"]')).y, header: r(document.querySelector('[data-testid="resource"]')).x, banner: r(document.getElementById('demo-banner')).y,
              headerY: r(document.querySelector('[data-testid="resource"]')).y, max: [g.scrollWidth - g.clientWidth, g.scrollHeight - g.clientHeight]}; }""")
    assert before["max"][0] > 0 and before["max"][1] > 0, "the grid has something to scroll in both directions"
    after = measure(driver, """() => { const g = document.getElementById('ariaGrid'); g.scrollTop = 200; g.scrollLeft = 250;
      return new Promise(res => setTimeout(() => { const r = e => e.getBoundingClientRect();
        res({label: r(document.querySelector('[class*="_hourText_"]')).y, header: r(document.querySelector('[data-testid="resource"]')).x, banner: r(document.getElementById('demo-banner')).y,
             headerY: r(document.querySelector('[data-testid="resource"]')).y}); }, 50)); }""")
    assert round(before["label"] - after["label"]) == 200, "the hour axis follows vertical scrolling"
    assert round(before["header"] - after["header"]) == 250, "the staff headers follow horizontal scrolling"
    assert after["banner"] == before["banner"] == 0 and after["headerY"] == before["headerY"], "the banner and the header row stay put"


def test_working_hours_are_shaded_outside_each_persons_day(driver):
    goto(driver, TOMORROW)
    shading = measure(driver, """() => {
      const col = [...document.querySelectorAll('[class*="_calendarGrid--overlay_"] [class*="_calendarColumn_"]')].find(c => c.getAttribute('data-resource') === '910004');
      const top = col.getBoundingClientRect().y;
      return [...col.querySelectorAll('[data-testid="non-working-event"]')].map(e => { const r = e.getBoundingClientRect(); return [Math.round(r.y - top), Math.round(r.height)]; }); }""")
    # Priya works 11:00 AM - 7:00 PM, the grid is 9:00 AM - 9:00 PM: 9-11 and 7-9 are shaded (80 px an hour, minus the 1 px border)
    assert [0, 159] in shading and [800, 159] in shading


def test_the_banner_is_visible_in_the_picture_and_the_label_is_the_exact_text(driver):
    goto(driver, TOMORROW)
    assert measure(driver, "document.querySelector('#demo-banner strong').textContent") == DEMO_LABEL
    assert measure(driver, "getComputedStyle(document.getElementById('demo-banner')).display") != "none"


# ---------------------------------------------------------------- pictures


def test_screenshots_are_produced_at_the_reference_viewports_and_are_not_blank(tmp_path, dataset, channel):
    paths = capture(dataset, tmp_path, channel=channel)
    assert {p.name for p in paths} == {"demo-day-1600x817.png", "demo-day-afternoon-1600x817.png", "demo-day-1280x720.png", "demo-day-afternoon-1280x720.png"}
    for path in paths:
        image = read_png(path)
        width, height = (int(v) for v in path.stem.rsplit("-", 1)[1].split("x"))
        assert (image.width, image.height) == (width, height)
        colours = {image.pixel(x, y) for x in range(0, image.width, 37) for y in range(0, image.height, 29)}
        assert len(colours) > 12, "the picture has real content"
        assert image.pixel(5, 5)[0] > 240 and image.pixel(5, 5)[2] < 180, "the amber demo banner is in the top-left corner"


def test_compare_runs_end_to_end_against_a_screenshot_of_itself(tmp_path, dataset, channel):
    from aria_booking.demo.visual import compare_to_reference

    (reference_path, *_rest) = capture(dataset, tmp_path, viewports=((1600, 817),), channel=channel, scroll_afternoon=False)
    rendered = read_png(reference_path)
    reference = rendered.crop(0, BANNER_PX, rendered.width, rendered.height - BANNER_PX)  # pretend the demo minus its banner is the "real" screenshot
    diffs = compare_to_reference(reference, rendered)
    assert [d.name for d in diffs] == ["sidebar", "toolbar", "hour_axis"] and all(d.mean_abs_diff == 0 and d.differing_ratio == 0 for d in diffs)


# ---------------------------------------------------------------- the whole voice path: tools -> the LIVE reader -> the demo page, equal to tools -> dataset


def test_the_voice_answers_are_the_same_whether_they_come_from_the_dataset_or_from_reading_the_page(driver, dataset):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from aria_booking.demo.launch import build_demo

    tz = ZoneInfo("America/New_York")
    real_now = lambda: datetime(2026, 10, 13, 12, 0, tzinfo=tz)  # noqa: E731
    (direct_endpoint, *_) = build_demo("k", today=TODAY, real_now=real_now, dataset=dataset)
    direct = direct_endpoint._tools
    (page_endpoint, *_) = build_demo("k", today=TODAY, real_now=real_now, dataset=dataset, driver=driver)
    via_page = page_endpoint._tools
    questions = [
        ("Sienna", "16:00", "massage-deep-tissue-massage-60"), ("Marisol", "16:00", "massage-deep-tissue-massage-60"), ("Imogen", "16:00", "massage-deep-tissue-massage-60"),
        ("Tamsin", "16:00", "massage-deep-tissue-massage-60"), ("Tamsin", "16:00", "massage-deep-tissue-massage-90"), ("Tamsin", "16:00", "massage-deep-tissue-massage-30"),
        ("Rowan", "16:00", "massage-deep-tissue-massage-60"), ("Delphine", "16:00", "facial-signature-facial-60"), ("Tamzin", "16:00", "massage-deep-tissue-massage-60"),
        (None, "16:00", "massage-deep-tissue-massage-60"), ("Sienna", "20:30", "massage-deep-tissue-massage-60"), ("Sienna", "19:30", "massage-deep-tissue-massage-60"),
        ("Marisol", "16:00", "facial-signature-facial-60"),
    ]
    for staff, at, service in questions:
        args = {"service_id": service, "date": TOMORROW.isoformat(), "time": at, **({"staff": staff} if staff else {})}
        a, b = direct.check_slot(f"d-{staff}-{at}", args), via_page.check_slot(f"p-{staff}-{at}", args)
        assert (a["status"], a.get("unavailable_reason"), a.get("reason"), a["speak"]) == (b["status"], b.get("unavailable_reason"), b.get("reason"), b["speak"]), (staff, at, service)
        assert [o["label"] for o in a.get("options", [])] == [o["label"] for o in b.get("options", [])]
