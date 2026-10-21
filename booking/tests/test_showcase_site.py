"""The generated Serenity page, checked as text: structure, content from the one showcase source, credits, no outside resources, no old identifiers, no invented claims.
Browser behaviour (layout at desktop and phone width, tabs, the voice panel) is in test_showcase_site_browser.py. No network."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from aria_booking.showcase import SHOWCASE, load_showcase
from aria_booking.showcase.catalog import SHOWCASE_CATALOG
from aria_booking.showcase.leaks import find_leaks
from aria_booking.showcase.roster import ROSTER_ROWS, role_of
from aria_booking.showcase.site import ASSET_DIR, CATEGORIES, build_site, calendar_link, duration_text, render_index

SETTINGS = {"publicKey": "public_key_test", "agentId": "agent_test", "agentVersion": 0}


class Page(HTMLParser):
    """Just enough structure: tags with attributes, and the visible text."""

    def __init__(self):
        super().__init__()
        self.tags: list = []
        self.text: list = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.text.append(data.strip())

    def find(self, tag, **attrs):
        return [a for t, a in self.tags if t == tag and all(a.get(k) == v for k, v in attrs.items())]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    build_site(out, settings=SETTINGS)
    html = (out / "index.html").read_text(encoding="utf-8")
    page = Page()
    page.feed(html)
    return out, html, page


# ---------------------------------------------------------------- the files


def test_the_site_is_a_small_self_contained_folder(built):
    out, _html, _page = built
    names = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert {"index.html", "styles.css", "voice.js", "site.js", "demo-config.js", "assets/credits.json"} <= names
    assert {f"assets/{n}" for n in ("hero-stones.jpg", "facial-teal.jpg", "stones-hands.jpg", "diffuser-candles.jpg")} <= names
    assert {"assets/fonts/cormorant-garamond-latin.woff2", "assets/fonts/jost-latin.woff2"} <= names
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    assert total < 1_000_000, f"the whole page weighs {total // 1024} KB"
    assert all(p.stat().st_size < 200_000 for p in (out / "assets").glob("*.jpg")), "every picture is optimized"


def test_building_twice_gives_the_same_page_and_never_touches_the_repository_website(tmp_path):
    root = Path(__file__).resolve().parent.parent.parent
    before = {n: (root / n).read_bytes() for n in ("index.html", "retell-voice.js", "styles.css", "avatar.js") if (root / n).exists()}
    build_site(tmp_path / "a", settings=SETTINGS)
    build_site(tmp_path / "b", settings=SETTINGS)
    assert (tmp_path / "a" / "index.html").read_bytes() == (tmp_path / "b" / "index.html").read_bytes()
    assert {n: (root / n).read_bytes() for n in before} == before and not (root / "demo-config.js").exists()


def test_the_voice_settings_are_public_only_and_absent_settings_say_so_instead_of_pretending(tmp_path):
    build_site(tmp_path / "with", settings=SETTINGS)
    config = (tmp_path / "with" / "demo-config.js").read_text(encoding="utf-8")
    assert json.loads(config[config.index("{"):config.rindex("}") + 1]) == SETTINGS and "key_" not in config.replace("public_key", "")
    build_site(tmp_path / "none")
    assert "window.RETELL_DEMO = {};" in (tmp_path / "none" / "demo-config.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------- structure and accessibility


def test_one_h1_a_lang_a_title_a_description_and_a_skip_link(built):
    _out, html, page = built
    h1 = [a["id"] for a in page.find("h1")]
    assert h1 == ["hero-title", "view-calendar-title", "view-staff-title"], "the home page's one visible heading, plus one for each of the two views (hidden until opened)"
    assert page.find("html")[0]["lang"] == "en"
    assert "<title>Serenity Spa: prototype showcase</title>" in html and page.find("meta", name="description")
    assert page.find("a", href="#main") and page.find("main", id="main") and page.find("meta", name="viewport")


def test_every_image_has_alt_text_and_its_size_and_exists(built):
    out, _html, page = built
    everything = [a for t, a in page.tags if t == "img"]
    avatar = [a for a in everything if a.get("class") == "aria__img"]
    assert len(avatar) == 1 and avatar[0]["src"] == "assets/aria-avatar.svg" and (out / avatar[0]["src"]).is_file() and avatar[0]["alt"].startswith("Aria"), "the floating avatar"
    entries = [a for a in everything if a.get("alt") == "" and a.get("src") == avatar[0]["src"]]
    assert len(entries) == 5 and all(a["width"].isdigit() and a["height"].isdigit() for a in entries), "the avatar-led call entries: decorative copies of the one avatar file (their buttons carry the label)"
    images = [a for a in everything if a.get("class") != "aria__img" and a not in entries]
    assert len(images) == 4, "the hero, the massage and facial photos and the band picture: a few relevant pictures, no clutter"
    for attrs in images:
        assert 20 <= len(attrs["alt"]) <= 200 and attrs["width"].isdigit() and attrs["height"].isdigit() and (out / attrs["src"]).is_file(), attrs
    assert page.find("img", src="assets/hero-stones.jpg")[0]["fetchpriority"] == "high"
    assert all(a.get("loading") == "lazy" for a in images if a["src"] != "assets/hero-stones.jpg")


def test_buttons_and_links_have_names_and_icons_are_decorative(built):
    _out, html, _page = built
    assert html.count('aria-hidden="true" focusable="false"') >= 8
    assert html.count("data-voice-start") >= 4, "Speak to Aria is in the header, the hero, the band and the phone bar"
    assert 'role="tablist"' in html and html.count('role="tabpanel"') == len([c for c in CATEGORIES if any(i.category == c[0] for i in SHOWCASE_CATALOG.items)])


def test_the_prototype_label_is_discreet_and_present_and_says_what_is_not_there(built):
    _out, html, _page = built
    assert 'data-testid="demo-page-banner"' in html and f"{SHOWCASE.prototype_label} · {SHOWCASE.booking_note}" in html
    changed = render_index(load_showcase(overrides={"booking_note": "Simulated booking is available"}), SHOWCASE_CATALOG, ROSTER_ROWS, calendar_port=8790, retention_days=7)
    assert "Simulated booking is available" in changed and "Booking is not part of this prototype" not in changed


# ---------------------------------------------------------------- one source: the menu and the roster


def test_every_menu_family_is_on_the_page_with_its_description_and_every_price_and_length(built):
    _out, html, page = built
    text = " ".join(page.text)
    for family in SHOWCASE_CATALOG.families():
        items = [i for i in SHOWCASE_CATALOG.items if i.family == family]
        assert family in text and items[0].description in text, family
        for item in items:
            assert f"${item.price_usd}" in text, (family, item.price_usd)
            if item.duration_minutes:
                assert duration_text(item.duration_minutes) in text, (family, item.duration_minutes)


def test_the_first_tab_is_selected_and_only_its_panel_is_visible(built):
    _out, _html, page = built
    tabs = page.find("button", role="tab")
    assert tabs[0]["aria-selected"] == "true" and all(t["aria-selected"] == "false" for t in tabs[1:])
    panels = page.find("section", role="tabpanel")
    assert "hidden" not in panels[0] and all("hidden" in p for p in panels[1:]) and len(tabs) == len(panels)
    assert {t["aria-controls"] for t in tabs} == {p["id"] for p in panels}


def test_the_team_is_the_roster_with_roles_and_a_fictional_note(built):
    _out, _html, page = built
    text = " ".join(page.text)
    for row in ROSTER_ROWS:
        assert row.name in text and role_of(row) in text
    assert "Fictional names, used only for this prototype." in text
    assert all(a["src"] == "assets/aria-avatar.svg" for t, a in page.tags if t == "img" and len(a["alt"]) == 0), "only the decorative call-entry pictures have an empty alt"


# ---------------------------------------------------------------- nothing invented, nothing of the earlier salon, nothing outside


def test_no_old_identifier_no_address_no_phone_in_any_generated_file(built):
    out, _html, _page = built
    for name in ("index.html", "styles.css", "voice.js", "site.js", "demo-config.js", "assets/credits.json"):
        assert find_leaks((out / name).read_text(encoding="utf-8")) == [], name
    text = " ".join(Page_text(out))
    assert "Contact details are not configured in this prototype." in text and "tel:" not in (out / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"\b(street|st\.|avenue|ave\.|boulevard|blvd|road|suite|zip|new jersey|directions|map)\b", text, re.I)


def Page_text(out):
    page = Page()
    page.feed((out / "index.html").read_text(encoding="utf-8"))
    return page.text


def test_a_configured_phone_is_shown_as_a_tel_link_and_never_by_default(tmp_path):
    html = render_index(load_showcase(overrides={"contact_phone": "+1 555 010 0199"}), SHOWCASE_CATALOG, ROSTER_ROWS, calendar_port=8790, retention_days=7)
    assert 'href="tel:+15550100199"' in html and "+1 555 010 0199" in html and "not configured" not in html


def test_no_testimonial_award_credential_or_review_claims(built):
    _out, html, page = built
    text = " ".join(page.text).lower()
    for claim in ("testimonial", "award", "best in", "voted", "five-star", "5-star", "★", "certified", "licensed", "since 19", "since 20", "years of experience", "rated", "reviews"):
        assert claim not in text, claim


def test_the_page_makes_no_outside_request_except_the_voice_sdk_and_plain_links(built):
    _out, html, page = built
    for tag, attrs in page.tags:
        if tag in ("script", "link", "img", "source", "iframe") and (attrs.get("src") or attrs.get("href")):
            ref = attrs.get("src") or attrs.get("href")
            assert not ref.startswith(("http://", "https://", "//")), (tag, ref)
    assert "fonts.googleapis" not in html and "gstatic" not in html
    frames = page.find("iframe")
    assert len(frames) == 1 and "src" not in frames[0] and frames[0]["data-src"] == calendar_link(8790), "the staff calendar frame loads nothing until the view is opened"
    sdk = (built[0] / "voice.js").read_text(encoding="utf-8")
    assert sdk.count("https://") == 1 or "cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.1" in sdk
    outbound = {a["href"] for t, a in page.tags if t == "a" and a.get("href", "").startswith("http")}
    assert all(h.startswith(("http://localhost:", "https://unsplash.com/")) for h in outbound), outbound


def test_the_calendar_link_goes_to_the_demo_calendar_on_localhost(built):
    _out, _html, page = built
    expected = calendar_link(8790)
    assert expected == "http://localhost:8790/pro/en-us/9000001/calendar?date=today&view=day&staffers=working"
    assert page.find("iframe")[0]["data-src"] == expected, "the configured calendar address is the staff frame's source"
    assert not [a for t, a in page.tags if t == "a" and a.get("href") == expected and a.get("target")], "no link opens it elsewhere"
    assert calendar_link(9911).startswith("http://localhost:9911/")


# ---------------------------------------------------------------- credits and provenance


def test_every_picture_and_font_has_provenance_and_the_footer_credits_the_photographers(built):
    out, html, _page = built
    credits = json.loads((out / "assets" / "credits.json").read_text(encoding="utf-8"))
    assert credits["license"] == "Unsplash License" and credits["license_url"] == "https://unsplash.com/license"
    pictures = {c["file"] for c in credits["images"]}
    assert pictures == {p.name for p in ASSET_DIR.glob("*.jpg")}, "every picture on disk is recorded and nothing recorded is missing"
    for entry in credits["images"]:
        assert entry["photographer"] and entry["photo_url"].startswith("https://unsplash.com/photos/") and entry["source_image"].startswith("https://images.unsplash.com/photo-")
        assert "unsplash.com/plus" not in entry["photo_url"] and entry["photographer"] in html
    assert {f["family"] for f in credits["fonts"]} == {"Cormorant Garamond", "Jost"} and all("Open Font License" in f["license"] for f in credits["fonts"])
    assert "not Serenity Spa staff and no location is pictured" in html and "Stock photographs" in html


def test_jpeg_files_are_real_pictures_of_the_recorded_size():
    import struct

    credits = json.loads((ASSET_DIR / "credits.json").read_text(encoding="utf-8"))["images"]
    for entry in credits:
        data = (ASSET_DIR / entry["file"]).read_bytes()
        assert data[:2] == b"\xff\xd8", entry["file"]
        i, size = 2, None
        while i < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                size = (width, height)
                break
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
        assert size == (entry["width"], entry["height"]), (entry["file"], size)
    for font in (ASSET_DIR / "fonts").glob("*.woff2"):
        assert font.read_bytes()[:4] == b"wOF2"


# ---------------------------------------------------------------- the theme


def test_the_theme_tokens_are_in_the_page_and_the_page_is_teal_led(built):
    out, html, _page = built
    for token in ("--teal-900:#0b3d3b", "--teal-700:#0f6b68", "--sand-100:#f6f1ea", "--ink:#18302f"):
        assert token in html
    css = (out / "styles.css").read_text(encoding="utf-8")
    assert css.count("var(--teal-") > 25 and not re.findall(r"#[0-9a-fA-F]{3,8}\b", re.sub(r"/\*.*?\*/", "", css, flags=re.S)), "colours come from tokens, not literals"
    assert "prefers-reduced-motion" in css and "@media (max-width: 759px)" in css and ":focus-visible" in css


def test_durations_are_spoken_the_same_way_as_the_menu_shows_them():
    assert [duration_text(m) for m in (15, 30, 60, 75, 90, 120, None)] == ["15 min", "30 min", "1 hr", "1 hr 15 min", "1 hr 30 min", "2 hr", ""]
