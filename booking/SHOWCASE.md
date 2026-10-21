# The Serenity Spa showcase (prototype)

A generic spa used to demonstrate the voice assistant (Aria) to any prospective salon. **Nothing in it is a real business**: there is no address or location, the phone
number is unset until the owner supplies one, the staff are fictional first names, the menu's descriptions are freshly written and its prices and lengths are example data,
and the photographs are stock images (credited, free to use) that do not show Serenity staff or premises. The page carries a discreet "Prototype showcase" label.

## One source, read by everything

| What | Where | Used by |
|---|---|---|
| Brand, tagline, example hours, timezone, contact placeholders, teal theme | `aria_booking/showcase/config.py` (`SHOWCASE`; `load_showcase()` reads an optional JSON file) | website, prompt, greeting, `demo-serve` |
| Fictional roster (ten first names; stable ids are the identity) | `showcase/roster.py` | calendar page, voice answers, website team section |
| Independent menu (89 items, 64 services) | `showcase/catalog.py` | website menu, voice lookup, prompt knowledge, the calendar's service and length logic |
| The page: HTML builder, styles, scripts, pictures, self-hosted fonts, credits | `showcase/site/` | `demo-agent prepare-web` |

Names are display labels only. Schedules, eligibility and the scripted days are keyed by staff id. A roster validator refuses duplicate or multi-word display names
(the voice tools let an exact name beat a longer one, so such a pair would hide a person).

## Building and previewing

```bash
python -m aria_booking showcase-preview --out showcase_evidence   # builds the page and photographs it at desktop and phone widths (needs a browser; loopback only)
python -m aria_booking demo-agent prepare-web                     # builds the page into the operator's folder with the PUBLIC voice settings
```

The page makes no request to anything but this machine (fonts are self-hosted) apart from the voice SDK on a visitor's click. The committed previews are in
`showcase_evidence/`; they are a picture of the page, not a claim about any real spa.

## The two calendar views (same page, same tab)

The page has two in-page views, opened from the header, the hero, the Hours section and the phone action bar, and left with **Back to <name>** or the browser's Back button
(`#calendar`, `#staff-calendar`; a link straight to either works):

* **Calendar** (customer view): choose a treatment, a length, a day and, if wanted, a technician; it lists the open times for the *full* length, each with the technician(s)
  who can take it. It reads `/demo/open-times` on the demo calendar server, which uses the **same availability engine and rules as the voice assistant** (`demo/open_times.py`;
  `tests/test_open_times.py` checks the two agree for many services, days and times). It shows no other client's details, says "no openings" only when the calendar is
  readable and nothing is open, and says it *cannot confirm* when a schedule cannot be read (unknown is never shown as free). It books nothing.
* **Staff calendar**: the existing detailed schedule, in a frame (loaded the first time it is opened), from the configured calendar address.

They are views of the one page, not new tabs or pages, so a call with Aria is never interrupted: the floating Aria avatar (below) stays on the page through both views.

**Home, and what the staff frame does to history.** **Back to <name>** is explicit: it makes the home page the current entry itself and never goes "back" in browser history,
because the staff calendar frame's own day navigation lives in the same session history. The framed calendar page also changes day by *replacing* its history entry (only
when it is framed; on its own it navigates as before), so the browser's Back button from a view is one step home however many days the frame moved through.

**Fresh, not stale.** Opening a view shows what the calendar holds now: the open times are asked again, and the staff frame is reloaded on the day it was showing (the frame
tells the page which day it is on; the page accepts that message only from its own frame, from the calendar's own address). While a view is open the page asks
`GET /demo/changes` (a few bytes: a stamp that changes with every stored booking, cancellation or move, and the demo date) every `refresh_seconds` (default 5; it backs off when
the calendar cannot be reached, pauses while the tab is hidden, and asks nothing on the home page) and refreshes the open view only when the stamp has changed, so a booking a call
has just made shows within a few seconds without a flash of reloading in between. Each view also has a Refresh button. A refresh of the open times keeps them on screen and
says they may be out of date if the calendar cannot be reached; an answer to an older search never replaces the answer to a newer one, whether it succeeds or fails; a refresh
of the schedule keeps its scroll position.

The browser reads only synthetic open times from the calendar server, which allows only the prepared page's own address (`--page-port`, default 5174; `localhost` and
`127.0.0.1`) to read them. No credential is in any of this: the page's script holds none, and the calendar answers are read-only.

## The floating Aria avatar

Aria is a small avatar floating at the bottom corner of every page, on a genuinely transparent background (no card, no dark scrim, no scroll lock), with separate icon
controls: **Call** (green handset), **Hang up** (red, shown only while a call is on or being set up) and a small **x** that hides the avatar. Each has an accessible name
and tooltip, a visible keyboard focus ring and a touch target of at least 44 px (52-56 px for the call controls).

* Nothing is called by loading the page. A call starts only from a click on **Call** or on any "Speak to Aria" button, and repeated clicks never start a second call.
* Every state comes from the voice SDK: *Connecting to Aria...*, *Listening...*, *Aria is speaking...* (the avatar's glow follows the SDK's own start/stop-talking events
  and nothing else), *Call ended.*, or a specific error (blocked microphone, allowed-domain problem, network). Reduced motion switches the animations off.
* The status bubble keeps the notice that Aria is an AI assistant and that the test call may be retained for review (`retention_days`).
* The **x** hides the avatar and **never ends a call**: while a call is on and the avatar is hidden, a call bar says so and offers **Show** and **End call**. A call that
  ends while the avatar is hidden brings it back to say how it ended.
* Hanging up while the SDK is still loading creates no call at all.

**The picture.** The default is a small original illustration (`showcase/site/assets/aria-avatar.svg`). To use your own (a square image with a transparent background),
make a web-sized copy and point the operator at it:

```bash
python -m aria_booking showcase-avatar --source my-avatar.png --out .local/avatar/aria-avatar.webp     # a small transparent WebP, made offline in a local browser
python -m aria_booking showcase-preview --out .local/avatar-preview --avatar .local/avatar/aria-avatar.webp   # photograph the page with it (loopback only)
# then, for the page the operator builds: set ARIA_AVATAR_IMAGE (or "avatar_image" in the operator config) to that file and run `demo-agent prepare-web`
```

**This repository is public: never commit a real person's likeness.** Keep a supplied avatar under `booking/.local/` (git-ignored) or anywhere outside the repository; the
tests fail if a picture other than the credited stock photographs is added to `showcase/site/assets/`. The build refuses an avatar over 600 KB (run `showcase-avatar`).

## Changing things

* **Name, hours, tagline, phone:** `showcase/config.py` (or a JSON file via `load_showcase`). A configured phone appears as a `tel:` link and is the only number Aria may give.
* **Staff:** edit `showcase/roster.py` (keep the ids; names must be single distinct first names).
* **Menu:** edit `showcase/catalog.py`. Keep ids unchanged for unchanged services; a service the calendar can check must also be in `demo.dataset.DEMO_FAMILIES`.
  After any of these, run `python -m aria_booking.catalog.render --write` so the committed prompt matches (a test fails otherwise).
* **Pictures:** add the file to `showcase/site/assets/`, record its photographer, page, licence and size in `assets/credits.json` (a test checks every picture on disk
  is recorded and that each record is an Unsplash-licence photo page, never Unsplash+).

## What it does not claim

Prices and lengths are example data; hours are example hours; the team is fictional. The simulated calendar, appointments and clients are invented. Booking is not part of
this prototype (the label says so, and `booking_note` in the config is the one place to change it when a simulated booking mode really exists).
