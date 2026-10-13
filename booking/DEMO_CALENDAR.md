# Demo calendar and voice demo (invented data) — runbook

A working voice demo that does not depend on Booksy: a calendar page that looks like a Booksy day view, and the same read-only voice tools answering
from the **same** invented data. Everything on it is simulated and says so.

## What it is, and what it is not

- **One dataset drives both** the page and the voice answers (`aria_booking/demo/dataset.py`). A test reads the *page* with the real capture script and the
  real parser, in a real browser, and requires it to equal what the voice tools answer from, for twelve days covering every weekday.
- **Source is obvious:** a persistent banner on the page ("Demo calendar - simulated availability"), `source: "demo_calendar"` on every availability reply,
  and the first availability answer in each call says out loud: "This is the simulated demo calendar, not the salon's real schedule." The server adds that
  sentence itself, so it does not depend on the model.
- **No Booksy, no fall-back:** `demo-serve` can only build the dataset (or, with `--via-page`, a reader pointed at the demo page); it can never build a
  Booksy browser. The live `serve` can never use the dataset. If `--via-page` cannot start, the command stops; it never quietly answers from the dataset.
- **No booking, ever:** the endpoint has no booking route and the tool file has no booking function. The booking approvals do not exist on this command.
- **Fiona-like, not Fiona's data:** staff names are the labels observed on the real calendar (reference only); ids are synthetic (`9100xx`); clients are
  fictional first names with an initial; no contact details, notes or real appointments. The hidden internal test account is deliberately not included.
- **What is NOT claimed:** the page reproduces the real day view's *geometry and structure* (sidebar, toolbar, staff headers, 250 px columns, 80 px hour rows,
  card sizes, shaded non-working time) from a redacted capture. Its **colours, fonts, icons and borders are approximations**: the capture holds none of them and
  no reference screenshot has been supplied. Nothing here says the styling matches Booksy. `demo-compare` (below) measures it once a screenshot exists.

## Start and stop

From the `booking` folder, with the project's virtual environment and one of `python -m playwright install chromium` **or** `ARIA_BROWSER_CHANNEL=chrome`
(only needed for the screenshot commands and `--via-page`; the default needs no browser at all):

```bash
# the Retell key stays in the environment (Sol holds it); never type it on the command line or put it in a file in the repo
python -m aria_booking demo-serve                        # demo date = the real today
python -m aria_booking demo-serve --today 2026-10-14     # or pin the demo date ("tomorrow" = the day after it)
python -m aria_booking demo-serve --via-page             # answer by reading the demo PAGE in a Playwright window (the live code path)
```

It prints the page address, e.g. `http://127.0.0.1:8790/pro/en-us/9000001/calendar?date=today&view=day&staffers=working`, and serves the voice tools on
`127.0.0.1:8787` (loopback only). **Ctrl+C stops both.** Ports: `--port` (tools, default 8787) and `--site-port` (page, default 8790).

## Wiring a NEW test agent and the page (no live agent is touched by this repo)

1. **Agent prompt:** `voice_agent_draft/prompt_demo_calendar.md` (generated from the same template as the other prompts, over the demo's 34 services; it adds a
   section saying the calendar is simulated and that a name the tool does not find is asked about, never swapped).
2. **Functions:** `voice_agent_draft/tools_availability_only.json` — `lookup_service`, `check_slot`, `find_alternatives`. There is no booking function.
   Replace `<YOUR-TUNNEL-HOST>` with the host of a tunnel to port 8787 that Sol or Shobana starts; the Retell key (with the webhook badge) is the signing key.
   Retell's signing of custom-function calls is an assumption that fails closed (401) if wrong; this has not been confirmed live.
3. **Page:** open the demo page next to the voice page. Pointing the existing website's voice widget at the new agent id is a change outside this repository
   and is not made here; the old own-account demo and any live agent stay as they are.

## What to ask (the scripted days: the demo date + 1 and + 2)

On the demo date + 1 (a massage at 4 PM unless stated). `python -m aria_booking demo-day [--date YYYY-MM-DD] [--today YYYY-MM-DD]` prints who works and what is booked on any covered day:

| ask | expected |
|---|---|
| "Is Lily available tomorrow at 4 for a one-hour deep tissue massage?" | **yes**, with Lily Xie (free from 3 PM) |
| "What about Elva?" | **booked** at 4 (3:45-5:15 PM); offers her other times |
| "And Amy?" | Amy Wu is **off** that day: "I don't see Amy on the schedule" |
| "Pinny at 4 for 90 minutes?" | the visit would **run into** her 5 PM appointment |
| "Pinny at 4 for an hour?" | fits before 5 PM but **not with the 15 minutes of cleanup** a massage needs; a 30-minute one is available |
| "Linda at 4?" | **off between 3 and 5 PM** (a break), so not available; offers 5 PM |
| "Fiona for a facial?" | **unknown**: her column shows no hours, so the answer is "I can't confirm" |
| "Who is free at 4?" | the first eligible technician who really is (Lily) |
| "Is Tinny free?" | "I don't see Tinny": it does **not** become Pinny Du; the agent asks who was meant |
| a date more than 35 days out | **unknown** ("I couldn't confirm that day"), never free; further than 60 days: asks for a nearer date |

Also ask general questions (prices, lengths, what a treatment is, opening hours): those come from the reviewed website knowledge and make no calendar read.
Salon opening hours and a technician's own hours are kept distinct.

## Checking the look

```bash
python -m aria_booking demo-screenshots --out demo_evidence --today 2026-10-13        # photographs the page at 1600x817 (= the 1600x789 reference + the 28 px banner) and 1280x720
python -m aria_booking demo-compare --reference real-day-view-1600x789.png            # only when a user-supplied screenshot of the REAL day view exists
```

`demo_evidence/` holds the current pictures. `demo-compare` renders the demo at the reference's size (plus the banner), crops the banner and diffs the regions that do
not depend on data (sidebar, toolbar, hour axis), printing a number per region; it claims nothing. The geometry checks (`tests/test_demo_browser.py`) compare 22
element rectangles with the redacted capture at 1600x789, within 2 px, and check that nothing is clipped, overlapping or hidden at 1600x817 and 1280x720.

## Latency: three different things

- **Tool latency (measured here):** about 1 ms per `check_slot` with the dataset driver; about 0.45-0.5 s per read when answering through the page in a local
  window (`--via-page`). Local, synthetic, no network.
- **Live Booksy latency:** not measured; there is no live access.
- **Spoken-call latency** (the caller stops speaking -> first audio of the answer): cannot be measured offline. The server log carries `received_ms` /
  `sent_ms` wall-clock marks and per-phase timings so a supervised call can be lined up with Retell's own call record. **The 2-3 s meaningful-answer requirement
  is not proven by any of the above.**
