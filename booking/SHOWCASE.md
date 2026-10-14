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

They are views of the one page, not new tabs or pages, so a call with Aria is never interrupted. **Hide while I look around** on the call panel puts the panel away and
shows a small call bar (**Show**, **End call**); the call itself is untouched.

The browser reads only synthetic open times from the calendar server, which allows only the prepared page's own address (`--page-port`, default 5174; `localhost` and
`127.0.0.1`) to read them. No credential is in any of this: the page's script holds none, and the calendar answers are read-only.

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
