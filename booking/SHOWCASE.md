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
