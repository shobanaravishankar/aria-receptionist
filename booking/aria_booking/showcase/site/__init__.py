"""Builds the Serenity Spa showcase page: a polished, responsive, teal-led website generated from the ONE showcase source (brand, fictional roster, independent menu).

Nothing on it is a real business: there is no address, no phone number unless one is configured, no testimonial, award or credential, and the pictures are stock
photographs (credited, free to use) that do not show Serenity staff or premises. A discreet bar labels it a prototype. It is written to a folder and served by any
static server; the original published salon site is never touched.
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path
from typing import Optional

from ...catalog.lookup import Catalog
from ...demo import DEMO_BUSINESS_ID
from ..catalog import SHOWCASE_CATALOG
from ..config import SHOWCASE, Showcase
from ..roster import ROSTER_ROWS, StaffRow, role_of
from .bookable import bookable_data, script_tag

SITE_DIR = Path(__file__).resolve().parent
ASSET_DIR = SITE_DIR / "assets"
DEFAULT_AVATAR = "aria-avatar.svg"  # an original neutral illustration; a build can supply its own image (square, transparent background)
AVATAR_TYPES = {".svg", ".png", ".webp"}  # formats with real transparency

# (category id, tab label, panel heading, short line, photo file or None)
CATEGORIES = (
    ("massage", "Massages", "Massages", "Unhurried, hands-on time in a range of styles and lengths.", "stones-hands.jpg"),
    ("facial", "Facials", "Facials", "Skin care for every routine, from a quick refresh to a longer, deeper session.", "facial-teal.jpg"),
    ("lash", "Lashes and brows", "Lashes and brows", "Sets, refills, lifts and tints for lashes and brows.", None),
    ("waxing", "Waxing", "Waxing", "Quick, careful hair removal for the face and body.", None),
    ("head-spa", "Head spa", "Head spa", "Scalp treatments for a slower, calmer wash.", None),
    ("body-scrub", "Body scrubs", "Body scrubs", "Polishing treatments for the whole body or just the back.", None),
    ("package", "Combos", "Combos", "Two treatments in one visit.", None),
    ("couples", "Couples", "Couples treatments", "Two treatments side by side in one room.", None),
    ("membership", "Visit bundles", "Visit bundles", "Several visits to use as you go.", None),
)

ICON_LOTUS = ('<svg viewBox="0 0 32 32" aria-hidden="true" focusable="false"><path fill="currentColor" d="M16 3c2.6 3.1 4 6.5 4 10 0 2.6-1.3 5.2-4 7.4C13.3 18.2 12 15.6 12 13c0-3.5 1.4-6.9 4-10Z"/>'
              '<path fill="currentColor" opacity=".75" d="M4 12c3.6.2 6.7 1.5 8.9 3.8-.4 3.3-2.4 5.9-5.6 7.4C4.6 21.4 3.3 17.4 4 12Z"/>'
              '<path fill="currentColor" opacity=".75" d="M28 12c.7 5.4-.6 9.4-3.3 11.2-3.2-1.5-5.2-4.100-5.600-7.400 2.200-2.300 5.300-3.600 8.900-3.800Z"/>'
              '<path fill="currentColor" opacity=".5" d="M5 26c4-1.800 7.700-1.800 11-.2 3.300-1.600 7-1.600 11 .2-4 2.600-7.700 3.300-11 3.300S9 28.600 5 26Z"/></svg>')
ICON_MIC = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M12 15a4 4 0 0 0 4-4V6a4 4 0 0 0-8 0v5a4 4 0 0 0 4 4Z"/>'
            '<path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M5.500 11a6.500 6.500 0 0 0 13 0M12 17.500V21M8.500 21h7"/></svg>')
ICON_CALENDAR = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="3.500" y="5" width="17" height="15" rx="3" fill="none" stroke="currentColor" stroke-width="1.8"/>'
                 '<path d="M3.500 10h17M8 3v4M16 3v4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="14.500" r="1.500" fill="currentColor"/></svg>')
ICON_MENU = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M5 7h14M5 12h14M5 17h9" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>')
ICON_PHONE = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
              '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>')  # Feather icons (MIT)
ICON_HANGUP = ICON_PHONE.replace("<svg ", '<svg class="turned" ', 1)  # the same handset, laid down by CSS
ICON_BACK = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M14.500 5.500 8 12l6.500 6.500" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>')
ICON_CHAT = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 6.500A2.500 2.500 0 0 1 6.500 4h11A2.500 2.500 0 0 1 20 6.500v7a2.500 2.500 0 0 1-2.500 2.500H11l-4.500 3.500V16h0A2.500 2.500 0 0 1 4 13.500Z" '
             'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>')

_e = html.escape


def duration_text(minutes: Optional[int]) -> str:
    if not minutes:
        return ""
    hours, rest = divmod(minutes, 60)
    return " ".join(p for p in (f"{hours} hr" if hours else "", f"{rest} min" if rest else "") if p)


def calendar_origin(calendar_port: int) -> str:
    """Where the demo calendar server is, for the page's open-times requests: 'localhost', like every other address this page uses."""
    return f"http://localhost:{calendar_port}"


def calendar_link(calendar_port: int) -> str:
    """The demo calendar page. 'localhost', like the voice page: the same machine, one consistent address."""
    return f"http://localhost:{calendar_port}/pro/en-us/{DEMO_BUSINESS_ID}/calendar?date=today&view=day&staffers=working"


def _variants(items) -> str:
    chips = []
    for item in items:
        label = item.variant_label
        length = duration_text(item.duration_minutes)
        price = f"${item.price_usd}"
        descriptive = label and label != length and not label.endswith(("hour", "minutes", "hours"))
        text = " · ".join(p for p in ((label if descriptive else ""), length, price) if p)
        chips.append(f"<li>{_e(text)}</li>")
    return '<ul class="variants">' + "".join(chips) + "</ul>"


def _panel(catalog: Catalog, category: str, heading: str, line: str, photo: Optional[str], alts: dict, active: bool) -> str:
    families: dict = {}
    for item in catalog.items:
        if item.category == category:
            families.setdefault(item.family, []).append(item)
    cards = "".join(
        f'<article class="card"><h3>{_e(name)}</h3><p>{_e(items[0].description)}</p>{_variants(items)}</article>' for name, items in families.items()
    )
    photo_html = (f'<div class="panel-photo"><img src="assets/{photo}" alt="{_e(alts[photo])}" width="{alts["_size"][photo][0]}" height="{alts["_size"][photo][1]}" '
                  'loading="lazy" decoding="async"></div>') if photo else ""
    layout = "panel-layout has-photo" if photo else "panel-layout"
    hidden = "" if active else " hidden"
    return (f'<section class="panel" id="panel-{category}" role="tabpanel" aria-labelledby="tab-{category}"{hidden}>'
            f'<div class="{layout}">{photo_html}<div><h3 class="panel-title">{_e(heading)}</h3><p class="panel-sub">{_e(line)}</p>'
            f'<div class="cards">{cards}</div></div></div></section>')


def _people(rows) -> str:
    return "".join(
        f'<article class="person"><div class="avatar" aria-hidden="true">{_e(row.name[0])}</div><h3>{_e(row.name)}</h3><p>{_e(role_of(row))}</p></article>' for row in rows
    )


def _credits() -> list:
    return json.loads((ASSET_DIR / "credits.json").read_text(encoding="utf-8"))["images"]


def render_index(showcase: Showcase, catalog: Catalog, rows, *, calendar_port: int, retention_days: int, bookable: Optional[dict] = None, avatar_file: str = DEFAULT_AVATAR,
                 refresh_seconds: int = 5) -> str:
    credits = {c["file"]: c for c in _credits()}
    alts = {name: c["alt"] for name, c in credits.items()}
    alts["_size"] = {name: (c["width"], c["height"]) for name, c in credits.items()}
    present = [c for c in CATEGORIES if any(i.category == c[0] for i in catalog.items)]
    tabs = "".join(
        f'<button class="tab" role="tab" id="tab-{cid}" data-category="{cid}" aria-controls="panel-{cid}" aria-selected="{"true" if n == 0 else "false"}" '
        f'tabindex="{0 if n == 0 else -1}" type="button">{_e(label)}</button>' for n, (cid, label, *_rest) in enumerate(present)
    )
    panels = "".join(_panel(catalog, cid, heading, line, photo, alts, n == 0) for n, (cid, _label, heading, line, photo) in enumerate(present))
    people = _people(rows)
    cal = calendar_link(calendar_port)  # the configured staff calendar address: used only as the frame's source, never hard-coded elsewhere
    bookable_script = script_tag(bookable if bookable is not None else bookable_data([(cid, label) for cid, label, *_r in present], calendar_origin(calendar_port), catalog, refresh_seconds=refresh_seconds))
    phone = (f'<p>Phone: <a href="tel:{_e("".join(ch for ch in showcase.contact_phone if ch.isdigit() or ch == "+"))}">{_e(showcase.contact_phone)}</a></p>'
             if showcase.contact_phone else "<p>Contact details are not configured in this prototype.</p>")
    credit_items = "".join(
        f'<li><a href="{_e(c["photo_url"])}" rel="noopener noreferrer" target="_blank">{_e(c["photographer"])}</a></li>' for c in credits.values()
    )
    theme = showcase.theme
    tokens = ";".join(f"--{k.replace('_', '-')}:{v}" for k, v in vars(theme).items())
    hero = credits["hero-stones.jpg"]
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(showcase.name)}: prototype showcase</title>
<meta name="description" content="{_e(showcase.name)} is a prototype spa website that demonstrates a voice assistant, Aria, answering questions about a menu and checking times on a simulated calendar. Example content only.">
<meta name="theme-color" content="{theme.teal_900}">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='16' fill='%230f6b68'/%3E%3Cpath d='M16 6c2.500 3 3.800 6 3.800 9 0 2.300-1.200 4.400-3.800 6.300C13.400 19.400 12.200 17.300 12.200 15c0-3 1.300-6 3.800-9Z' fill='white'/%3E%3C/svg%3E">
<link rel="preload" href="assets/fonts/cormorant-garamond-latin.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="assets/fonts/jost-latin.woff2" as="font" type="font/woff2" crossorigin>
<script>document.documentElement.classList.add("js")</script>
<style>:root{{{tokens}}}</style>
<link rel="stylesheet" href="styles.css">
<script src="demo-config.js"></script>
{bookable_script}
</head>
<body class="has-mobile-cta">
<a class="skip-link" href="#main">Skip to content</a>
<div class="proto-bar" role="note" data-testid="demo-page-banner">{_e(showcase.prototype_label)} · {_e(showcase.booking_note)}</div>
<header class="site-header">
  <div class="wrap header-row">
    <a class="brand" href="#top" aria-label="{_e(showcase.name)}, home">{ICON_LOTUS}<span class="brand-name">{_e(showcase.name)}</span></a>
    <nav class="nav" aria-label="Main">
      <a href="#menu">Menu</a><a href="#team">Team</a><a href="#visit">Hours</a><a href="#calendar" data-testid="nav-calendar">Calendar</a><a href="#staff-calendar" data-testid="nav-staff-calendar">Staff calendar</a>
    </nav>
    <div class="header-actions"><button class="btn btn-primary btn-small" type="button" data-voice-start data-testid="speak-to-aria">{ICON_MIC}<span>Speak to Aria</span></button></div>
  </div>
</header>
<main id="main">
<div id="home">
<section class="hero" id="top" aria-labelledby="hero-title">
  <img src="assets/hero-stones.jpg" alt="{_e(hero["alt"])}" width="{hero["width"]}" height="{hero["height"]}" fetchpriority="high" decoding="async">
  <div class="wrap"><div class="hero-inner">
    <p class="eyebrow">Massage · Facials · Lashes · Waxing</p>
    <h1 id="hero-title">{_e(showcase.tagline)}</h1>
    <p class="lede">A calm place to book your next treatment. Ask Aria, our voice assistant, about the menu or open times.</p>
    <div class="hero-actions">
      <button class="btn btn-light" type="button" data-voice-start>{ICON_MIC}<span>Speak to Aria</span></button>
      <a class="btn btn-outline" href="#calendar" data-testid="calendar-link">{ICON_CALENDAR}<span>See open times</span></a>
    </div>
    <p class="hero-note">Aria can answer questions about the menu and check which times are open on the prototype calendar. Team members can open the <a href="#staff-calendar" style="color:var(--white)">staff calendar</a>.</p>
  </div></div>
</section>
<section class="steps" aria-label="How it works"><div class="wrap steps-grid">
  <div class="step"><span class="step-icon">{ICON_CHAT}</span><div><h3>Ask Aria</h3><p>What a treatment involves, how long it takes and what it costs.</p></div></div>
  <div class="step"><span class="step-icon">{ICON_CALENDAR}</span><div><h3>Check a time</h3><p>Aria looks at the calendar and tells you who is free.</p></div></div>
  <div class="step"><span class="step-icon">{ICON_MENU}</span><div><h3>Browse the menu</h3><p>Massages, facials, lashes, waxing and more, below.</p></div></div>
</div></section>
<section class="section" id="menu" aria-labelledby="menu-title"><div class="wrap">
  <div class="section-head"><p class="kicker">The menu</p><h2 id="menu-title">Find your treatment</h2><p>Example menu and prices for this prototype.</p></div>
  <div class="tabs" role="tablist" aria-label="Menu categories">{tabs}</div>
  {panels}
  <noscript><style>.panel[hidden]{{display:block}}</style></noscript>
  <p class="menu-note">Prices and lengths are example data for this prototype.</p>
</div></section>
<section class="band" aria-labelledby="band-title"><div class="band-grid">
  <div class="band-photo"><img src="assets/diffuser-candles.jpg" alt="{_e(credits["diffuser-candles.jpg"]["alt"])}" width="900" height="1350" loading="lazy" decoding="async"></div>
  <div class="band-copy"><p class="kicker" style="color:var(--teal-100)">Aria, your assistant</p><h2 id="band-title">Questions, answered out loud</h2>
    <p>Press the button and just talk. Aria can explain the menu, tell you who offers a treatment and check which times are open on the calendar.</p>
    <button class="btn btn-light" type="button" data-voice-start>{ICON_MIC}<span>Speak to Aria</span></button></div>
</div></section>
<section class="section" id="team" aria-labelledby="team-title"><div class="wrap">
  <div class="section-head"><p class="kicker">The team</p><h2 id="team-title">Meet the team</h2><p>The people on the calendar.</p></div>
  <div class="team-grid">{people}</div>
  <p class="team-note">Fictional names, used only for this prototype.</p>
</div></section>
<section class="section" id="visit" style="padding-top:0" aria-labelledby="visit-title"><div class="wrap">
  <div class="section-head"><p class="kicker">Plan a visit</p><h2 id="visit-title">Hours and calendar</h2></div>
  <div class="visit-grid">
    <div class="visit-card"><h3>Opening hours</h3><p>{_e(showcase.example_hours.capitalize())}.</p><p>Example hours for this prototype. Availability comes only from the calendar.</p>{phone}</div>
    <div class="visit-card"><h3>The calendar</h3><p>See which times are open for a treatment, or the day as the team sees it. Everything on it is simulated for this demonstration.</p>
      <div class="visit-actions"><a class="btn btn-ghost" href="#calendar" data-testid="hours-calendar">{ICON_CALENDAR}<span>See open times</span></a><a class="btn btn-ghost" href="#staff-calendar" data-testid="hours-staff-calendar">{ICON_CALENDAR}<span>Staff calendar</span></a></div>
      <noscript><p style="margin-top:12px"><a href="{_e(cal)}">Open the staff calendar</a> (needs the demo calendar running).</p></noscript></div>
  </div>
</div></section>
</div>
<section class="view" id="view-calendar" hidden aria-labelledby="view-calendar-title" data-testid="view-calendar"><div class="wrap">
  <div class="view-top"><a class="btn btn-ghost btn-small back-home" href="#top" data-home data-testid="back-home">{ICON_BACK}<span>Back to {_e(showcase.name)}</span></a></div>
  <div class="view-head"><p class="kicker">Calendar</p><h1 id="view-calendar-title">Open times</h1>
    <p>Choose a treatment and a day to see when it is open. The times come from the same prototype calendar Aria reads, and cover the full length of the treatment.</p></div>
  <form class="open-form" id="open-times-form" novalidate>
    <div class="field"><label for="ot-family">Treatment</label><select id="ot-family"></select></div>
    <div class="field"><label for="ot-length">Length</label><select id="ot-length"></select></div>
    <div class="field"><label for="ot-date">Day</label><input id="ot-date" type="date"></div>
    <div class="field"><label for="ot-staff">Technician</label><select id="ot-staff"></select></div>
    <button class="btn btn-primary" type="submit" data-testid="show-open-times">Show open times</button>
  </form>
  <div class="open-results" id="ot-results" aria-live="polite" data-state=""><p>Pick a treatment and a day, then choose Show open times.</p></div>
  <p class="ot-refresh-note" id="ot-refresh-note" role="status" data-testid="ot-refresh-note"></p>
  <p class="view-foot">This view only lists open times; it does not make a booking. The times refresh by themselves when the calendar changes.</p>
  <div class="visit-actions"><button type="button" class="btn btn-primary btn-small" data-voice-start>{ICON_MIC}<span>Ask Aria about a time</span></button><button type="button" class="btn btn-ghost btn-small" id="ot-refresh" data-refresh="calendar" data-testid="ot-refresh">Refresh times</button><a class="btn btn-ghost btn-small" href="#staff-calendar">Staff calendar</a></div>
</div></section>
<section class="view" id="view-staff" hidden aria-labelledby="view-staff-title" data-testid="view-staff"><div class="wrap">
  <div class="view-top"><a class="btn btn-ghost btn-small back-home" href="#top" data-home>{ICON_BACK}<span>Back to {_e(showcase.name)}</span></a></div>
  <div class="view-head"><p class="kicker">For the team</p><h1 id="view-staff-title">Staff calendar</h1>
    <p>The detailed schedule the team sees, with every appointment on it. Everything on it is fictional and simulated for this demonstration. Use its own arrows and staff filter to move around.</p></div>
  <iframe id="staff-frame" class="staff-frame" title="Staff calendar (fictional, simulated)" data-src="{_e(cal)}" data-testid="staff-frame"></iframe>
  <p class="view-foot">The schedule reloads when you open it and by itself when the calendar changes, on the day you were looking at.</p>
  <div class="visit-actions"><button type="button" class="btn btn-ghost btn-small" id="staff-refresh" data-refresh="staff" data-testid="staff-refresh">Refresh schedule</button><a class="btn btn-ghost btn-small" href="#calendar">See open times</a></div>
</div></section>
</main>
<footer class="site-footer"><div class="wrap">
  <div class="footer-grid">
    <div><a class="brand" href="#top">{ICON_LOTUS}<span class="brand-name">{_e(showcase.name)}</span></a>
      <p style="margin-top:12px;max-width:44ch">{_e(showcase.name)} is a prototype used to demonstrate a voice assistant for spas and salons. It is not a real business.</p></div>
    <div class="credits"><strong style="color:#cfe8e5">Photo credits</strong> (Unsplash, free to use under the <a href="https://unsplash.com/license" rel="noopener noreferrer" target="_blank">Unsplash License</a>)
      <ul>{credit_items}</ul><p style="margin-top:8px">Stock photographs. The people shown are not {_e(showcase.name)} staff and no location is pictured.</p></div>
  </div>
  <p class="footer-bottom">{_e(showcase.prototype_label)}.</p>
</div></footer>
<div class="mobile-cta" aria-label="Quick actions"><button class="btn btn-primary" type="button" data-voice-start>{ICON_MIC}<span>Speak to Aria</span></button>
  <a class="btn btn-ghost" style="background:var(--white)" href="#calendar" data-testid="mobile-calendar">{ICON_CALENDAR}<span>Calendar</span></a></div>

<div class="call-bar" id="call-bar" hidden role="region" aria-label="Call with Aria" data-testid="call-bar"><span class="call-bar__dot" aria-hidden="true"></span><span class="call-bar__text" id="call-bar-text">On a call with Aria</span>
  <button type="button" class="btn btn-small btn-ghost" id="call-show">Show</button><button type="button" class="btn btn-small btn-primary" id="call-end">End call</button></div>

<aside class="aria" id="aria" data-state="idle" role="region" aria-label="Aria, the voice assistant" data-testid="aria-widget">
  <p class="aria__status" id="voice-status" role="status" aria-live="polite" hidden data-testid="aria-status"><span class="aria__dot" aria-hidden="true"></span>
    <span class="aria__body"><span class="aria__text"></span><span class="aria__fine">Aria is an AI voice assistant. Retell may retain this test call for review for up to {retention_days} days.</span></span></p>
  <div class="aria__stage">
    <img class="aria__img" src="assets/{avatar_file}" alt="Aria, the {_e(showcase.name)} voice assistant" width="400" height="400" decoding="async" data-testid="aria-avatar">
    <div class="aria__controls">
      <button type="button" class="aria-btn aria-btn--call" id="aria-call" data-voice-start data-testid="aria-call" aria-label="Call Aria" title="Call Aria, an AI voice assistant. Your browser will ask for microphone access.">{ICON_PHONE}</button>
      <button type="button" class="aria-btn aria-btn--hangup" id="aria-hangup" data-testid="aria-hangup" aria-label="Hang up" title="Hang up" hidden>{ICON_HANGUP}</button>
    </div>
    <button type="button" class="aria__hide" id="aria-hide" data-testid="aria-hide" aria-label="Hide Aria" title="Hide Aria (a call in progress keeps going)"><span aria-hidden="true">&times;</span></button>
  </div>
</aside>
<div class="toast" id="toast" role="status" hidden></div>
<script src="site.js" defer></script>
<script src="views.js" defer></script>
<script src="voice.js" type="module"></script>
</body>
</html>
"""


MAX_AVATAR_BYTES = 600_000  # a bigger picture slows the page for a visitor; `showcase-avatar` makes a web-sized copy


def _use_avatar(out: Path, avatar) -> str:
    """The avatar file name the page uses: the built-in illustration, or the supplied image copied in as assets/aria-avatar.<ext>."""
    if avatar in (None, ""):
        return DEFAULT_AVATAR
    source = Path(avatar)
    if source.suffix.lower() not in AVATAR_TYPES:
        raise ValueError("the avatar must be a .png, .webp or .svg with a transparent background")
    if not source.is_file():
        raise ValueError("the avatar file was not found")
    if source.stat().st_size > MAX_AVATAR_BYTES:
        raise ValueError(f"the avatar is {source.stat().st_size // 1024} KB: make a web-sized copy first with `python -m aria_booking showcase-avatar --source <file> --out <file.webp>`")
    name = "aria-avatar" + source.suffix.lower()
    shutil.copyfile(source, out / "assets" / name)
    return name


def build_site(out_dir, *, showcase: Showcase = SHOWCASE, catalog: Catalog = SHOWCASE_CATALOG, rows=ROSTER_ROWS, settings: Optional[dict] = None,
               calendar_port: int = 8790, retention_days: int = 7, avatar=None, refresh_seconds: int = 5) -> list:
    """Write the page into ``out_dir`` (created if needed) and return the files written. ``settings`` are the PUBLIC voice settings (public key, agent id, version):
    never a secret, and none at all is fine: the page then says voice is not configured instead of pretending. ``avatar`` is an optional image (png, webp or svg,
    square, transparent background) that replaces the built-in illustration; it is copied in, never referenced from outside the folder."""
    out = Path(out_dir)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    shutil.copytree(ASSET_DIR, out / "assets", dirs_exist_ok=True)
    avatar_file = _use_avatar(out, avatar)
    written = []
    index = out / "index.html"
    index.write_text(render_index(showcase, catalog, rows, calendar_port=calendar_port, retention_days=retention_days, avatar_file=avatar_file,
                                          refresh_seconds=refresh_seconds), encoding="utf-8", newline="\n")
    written.append(index)
    for name in ("styles.css", "voice.js", "site.js", "views.js"):
        shutil.copyfile(SITE_DIR / name, out / name)
        written.append(out / name)
    written.extend(sorted(p for p in (out / "assets").rglob("*") if p.is_file()))
    config = out / "demo-config.js"
    config.write_text("/* Public-only settings for the prototype page. Generated; not for Git. */\nwindow.RETELL_DEMO = " + json.dumps(settings or {}) + ";\n", encoding="utf-8", newline="\n")
    written.append(config)
    return written
