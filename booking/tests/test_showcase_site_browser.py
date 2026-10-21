"""The Serenity page in a REAL browser at phone, tablet and desktop widths: nothing overflows or clips, the primary actions are visible and big enough to touch, the
fonts are the self-hosted ones, the menu tabs work by mouse, keyboard and link, the phone bar appears after the hero, and the voice panel opens. The browser can reach only
this machine, and the voice SDK is a local stub, so no call and no network are involved."""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aria_booking.config import Config
from aria_booking.playwright_driver import PlaywrightBooksyDriver
from aria_booking.showcase.site import build_site

from test_playwright_smoke import LoopbackOnlySession, channel, never_left_this_machine  # noqa: F401  (fixtures)

SDK_URL = "https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.1/+esm"
SDK_STUB = "export class RetellClient { constructor(o) {} createWebCall(o) { window.__hooks = o.hooks; return { end: async () => {}, startAudioPlayback() {} }; } }"
SETTINGS = {"publicKey": "public_key_test", "agentId": "agent_test", "agentVersion": 0}
ERRORS: list = []


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        return


def serve(folder):
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    configured, bare = tmp_path_factory.mktemp("configured"), tmp_path_factory.mktemp("bare")
    build_site(configured, settings=SETTINGS)
    build_site(bare)
    servers = [serve(configured), serve(bare)]
    yield {"configured": f"http://127.0.0.1:{servers[0].server_address[1]}/", "bare": f"http://127.0.0.1:{servers[1].server_address[1]}/"}
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def driver(tmp_path_factory, channel):
    cfg = Config(business_id="1234567", local_dir=tmp_path_factory.mktemp("showcase-browser"), browser_channel=channel)
    drv = PlaywrightBooksyDriver(cfg, session_factory=lambda: LoopbackOnlySession(cfg), notify=lambda m: None, load_timeout_seconds=15)
    yield drv
    drv.close()


def look(driver, url, width, height, script, *, scroll=True, actions=None):
    """Open the page at a size, optionally scroll through it (lazy pictures), optionally act, then evaluate ``script``."""

    def work(page):
        if not getattr(page, "_ready", False):
            page.route(SDK_URL, lambda route: route.fulfill(status=200, content_type="application/javascript", headers={"access-control-allow-origin": "*"}, body=SDK_STUB))
            page.on("console", lambda m: ERRORS.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: ERRORS.append(str(e)))
            page._ready = True
        page.set_viewport_size({"width": width, "height": height})
        page.goto(url, wait_until="load")
        page.wait_for_selector("h1")
        if scroll:
            total = page.evaluate("document.documentElement.scrollHeight")
            for y in range(0, total, 500):
                page.evaluate(f"window.scrollTo(0, {y})")
                page.wait_for_timeout(40)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(150)
        if actions:
            actions(page)
        return page.evaluate(script)

    return driver._run(work, 90)


WIDTHS = [(360, 640), (390, 844), (768, 1024), (1024, 768), (1440, 900)]


@pytest.mark.parametrize("width,height", WIDTHS)
def test_nothing_overflows_sideways_at_any_width(driver, sites, width, height):
    result = look(driver, sites["configured"], width, height, """() => {
        const wide = [...document.querySelectorAll('body *')].filter(e => !e.closest('.tabs') && !e.closest('.aria') && e.getBoundingClientRect().right > innerWidth + 1 && getComputedStyle(e).position !== 'fixed')
            .map(e => e.tagName + '.' + e.className).slice(0, 5);
        return {scroll: document.documentElement.scrollWidth, inner: innerWidth, wide};
    }""")
    assert result["scroll"] <= result["inner"] and result["wide"] == [], result


@pytest.mark.parametrize("width,height", [(390, 844), (768, 1024), (1440, 900)])
def test_the_main_actions_are_visible_without_scrolling(driver, sites, width, height):
    result = look(driver, sites["configured"], width, height, """() => {
        const box = s => { const r = document.querySelector(s).getBoundingClientRect(); return {top: r.top, bottom: r.bottom, left: r.left, right: r.right, h: r.height}; };
        return {header: box('.site-header [data-voice-start]'), hero: box('.hero [data-testid=hero-call]'), calendar: box('.hero [data-testid=calendar-link]'), title: box('h1'), inner: [innerWidth, innerHeight]};
    }""", scroll=False)
    w, h = result["inner"]
    for name in ("header", "hero", "title"):
        b = result[name]
        assert 0 <= b["top"] and b["bottom"] <= h and 0 <= b["left"] and b["right"] <= w, (name, b)
    if width >= 768:
        assert result["calendar"]["bottom"] <= h, "the calendar link is also above the fold"


def test_the_phone_composition_is_deliberate_not_a_squeezed_desktop(driver, sites):
    result = look(driver, sites["configured"], 390, 844, """() => {
        const css = s => getComputedStyle(document.querySelector(s));
        return {nav: css('.nav').display, heroActions: css('.hero-actions').flexWrap, btnWidth: document.querySelector('.hero-actions .btn').getBoundingClientRect().width,
                h1: parseFloat(css('h1').fontSize), body: parseFloat(css('body').fontSize), cols: css('.team-grid').gridTemplateColumns.split(' ').length};
    }""")
    assert result["nav"] == "none", "the header keeps only the brand and the voice button on a phone"
    assert result["btnWidth"] > 300, "the hero buttons are full-width on a phone"
    assert result["h1"] >= 40 and result["body"] >= 16 and result["cols"] == 2


def test_every_control_is_big_enough_to_touch_on_a_phone(driver, sites):
    result = look(driver, sites["configured"], 390, 844, """() => {
        const small = [...document.querySelectorAll('button, a.btn, [role=tab]')].filter(e => e.offsetParent !== null || getComputedStyle(e).position === 'fixed')
            .map(e => [e.textContent.trim().slice(0, 20), Math.round(e.getBoundingClientRect().height)]).filter(([_, h]) => h > 0 && h < 44);
        return small;
    }""")
    assert result == [], result


def test_pictures_load_and_the_fonts_are_the_self_hosted_ones(driver, sites):
    result = look(driver, sites["configured"], 1440, 900, """async () => {
        document.querySelector('[role=tab][data-category=facial]').click();  // the facial picture is in a panel that is not shown until its tab is chosen
        window.scrollTo(0, document.querySelector('#menu').offsetTop);
        await new Promise(r => setTimeout(r, 600));
        await document.fonts.ready;
        const images = [...document.images].filter(i => !i.classList.contains('aria__img') && !i.closest('.call-entry')).map(i => [i.getAttribute('src'), i.complete && i.naturalWidth > 0]);
        const entries = [...document.querySelectorAll('.call-entry img')].filter(i => i.getAttribute('loading') !== 'lazy').map(i => i.complete && i.naturalWidth > 0);
        const avatar = document.querySelector('.aria__img');
        return {images, entries, avatar: avatar.complete && avatar.naturalWidth > 0, serif: document.fonts.check('600 20px "Cormorant Garamond"'), sans: document.fonts.check('16px Jost'),
                faces: [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family.replace(/"/g, '')),
                h1: getComputedStyle(document.querySelector('h1')).fontFamily};
    }""")
    assert all(ok for _src, ok in result["images"]) and len(result["images"]) == 4, result["images"]
    assert result["avatar"] is True, "the floating avatar picture loads"
    assert result["entries"] and all(result["entries"]), "the call entries above the fold show the avatar"
    assert result["serif"] and result["sans"] and {"Cormorant Garamond", "Jost"} <= set(result["faces"]) and result["h1"].startswith('"Cormorant Garamond"')


def test_the_menu_tabs_work_by_click_keyboard_and_link(driver, sites):
    script = """() => ({selected: [...document.querySelectorAll('[role=tab][aria-selected=true]')].map(t => t.dataset.category),
                         visible: [...document.querySelectorAll('[role=tabpanel]')].filter(p => !p.hidden).map(p => p.id)})"""

    def click_waxing(page):
        page.click('[role="tab"][data-category="waxing"]')

    assert look(driver, sites["configured"], 1440, 900, script, actions=click_waxing) == {"selected": ["waxing"], "visible": ["panel-waxing"]}

    def keys(page):
        page.focus('[role="tab"][data-category="massage"]')
        page.keyboard.press("ArrowRight")
        page.keyboard.press("ArrowRight")
        page.keyboard.press("End")
        page.keyboard.press("Home")
        page.keyboard.press("ArrowLeft")

    assert look(driver, sites["configured"], 1440, 900, script, actions=keys)["selected"] == ["membership"], "ArrowLeft from the first wraps to the last"

    def link(page):
        page.evaluate("location.hash = '#head-spa'")
        page.wait_for_timeout(150)

    assert look(driver, sites["configured"], 1440, 900, script, actions=link) == {"selected": ["head-spa"], "visible": ["panel-head-spa"]}


def test_a_panel_is_never_empty_and_every_price_chip_is_readable(driver, sites):
    result = look(driver, sites["configured"], 390, 844, """() => [...document.querySelectorAll('[role=tabpanel]')].map(p => {
        p.hidden = false; const n = p.querySelectorAll('.card').length; const chips = [...p.querySelectorAll('.variants li')].map(l => l.textContent);
        return [p.id, n, chips.length, chips.filter(c => !/\\$\\d/.test(c)).length]; })""")
    assert all(cards > 0 and chips >= cards and bad == 0 for _id, cards, chips, bad in result), result


def test_the_phone_bar_appears_after_the_hero_and_hides_at_the_top(driver, sites):
    script = "() => ({show: document.querySelector('.mobile-cta').classList.contains('show'), op: getComputedStyle(document.querySelector('.mobile-cta')).opacity})"
    top = look(driver, sites["configured"], 390, 844, script, scroll=False)
    assert top["show"] is False and float(top["op"]) == 0

    def down(page):
        page.evaluate("window.scrollTo(0, 1400)")
        page.wait_for_timeout(500)

    after = look(driver, sites["configured"], 390, 844, script, scroll=False, actions=down)
    assert after["show"] is True and float(after["op"]) == 1


def test_the_phone_bar_is_not_there_on_a_desktop(driver, sites):
    assert look(driver, sites["configured"], 1440, 900, "() => getComputedStyle(document.querySelector('.mobile-cta')).display", scroll=False) == "none"


def test_no_script_or_page_error_was_raised_in_any_of_this():
    assert ERRORS == [], ERRORS
