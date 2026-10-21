# Demo calendar and voice demo (invented data) — runbook

A working voice demo that does not depend on Booksy: a calendar page that looks like a Booksy day view, and the same read-only voice tools answering
from the **same** invented data. Everything on it is simulated and says so.

## What it is, and what it is not

- **One dataset drives both** the page and the voice answers (`aria_booking/demo/dataset.py`). A test reads the *page* with the real capture script and the
  real parser, in a real browser, and requires it to equal what the voice tools answer from, for twelve days covering every weekday.
- **Labelled, not announced:** a persistent banner on the page ("Demo calendar - simulated availability"), `source: "demo_calendar"` on every availability reply
  and `technicians_source` on the provider list. Nothing is spoken about "demo" or "simulated" unprompted, in the greeting or in an answer (a test sweeps 157 replies
  for those words). Asked whether the schedule is real or live, or whether this is a demo, the agent answers truthfully: a prototype with an invented calendar, not the
  salon's real schedule (its prompt says so). The label also gates the demo-only provider lookup, so it is never removed; the old spoken one-time note can be
  brought back with `build_demo(..., announce_source=True)`.
- **The brand is "Serenity Spa", from one source:** `aria_booking/showcase` holds the name, example hours, the teal theme, the fictional roster and an independent menu,
  and the website, the calendar, the voice prompt and the tool answers all read it (see `SHOWCASE.md`). The earlier salon's website knowledge, staff, address and phone
  numbers are not used anywhere in the demo, and a build-time guard fails if any of them reappears. To change the name, edit `showcase/config.py`, run
  `python -m aria_booking.catalog.render --write`, then `demo-agent update --confirm-update` and `demo-agent prepare-web`.
- **The opening is spoken per call:** "Good morning/afternoon/evening! This is Aria at Serenity Spa. I'd love to help you get some relaxation on the calendar. To get started,
  are you looking for a massage, a facial, or maybe a full spa day?" (no "Hello", no "AI receptionist", warm and upbeat). The word follows the spa's local hour at the START
  of each call: Retell fills `{{current_hour_America/New_York}}` in per call (before 12 morning, 12 up to 17 afternoon, 17 on evening; `demo.SPA_TIMEZONE` is the one
  configured zone). An opening message cannot branch on a variable, and the docs show per-call variables only on the server-side create-web-call API, so the agent's
  opening message is left **unset** (Retell then has the model speak first) and the prompt's "Your opening line" section carries the wording and the hour rule.
  The tests prove the prompt, the unset message in create/update/read-back and the hour rule; that the model follows it is proved only in a call. "A full spa day"
  is an invitation, not a package: the prompt forbids inventing one.
- **No Booksy, no fall-back:** `demo-serve` can only build the dataset (or, with `--via-page`, a reader pointed at the demo page); it can never build a
  Booksy browser. The live `serve` can never use the dataset. If `--via-page` cannot start, the command stops; it never quietly answers from the dataset.
- **No booking, ever:** the endpoint has no booking route and the tool file has no booking function. The booking approvals do not exist on this command.
- **Fictional staff and menu:** the ten staff are fictional FIRST names (Marisol, Tamsin, Odette, Priya, Noor, Sienna, Rowan, Imogen, Calista, Delphine) from ONE roster
  (`aria_booking/showcase/roster.py`); ids are synthetic (`9100xx`) and are the identity: schedules, eligibility and scripted days are keyed by id, never by name. The menu
  is a separate, independently written Serenity menu (`showcase/catalog.py`) with example prices and lengths. Clients are fictional first names with an initial; no contact
  details, notes or real appointments. Nothing is copied from any real salon's staff, website text, address or phone numbers; a test scans every generated output for the
  earlier salon's identifiers (kept only as one-way digests).
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

1. **Agent prompt:** `voice_agent_draft/prompt_demo_calendar.md` (generated from the shared conversation template and the Serenity menu, over the demo's 35 services; it says how to treat the
   prototype calendar and that a name the tool does not find is asked about, never swapped).
2. **Functions:** `voice_agent_draft/tools_availability_only.json` — `lookup_service`, `check_slot`, `find_alternatives`. There is no booking function.
   Replace `<YOUR-TUNNEL-HOST>` with the host of a tunnel to port 8787 that Sol or Shobana starts; the Retell key (with the webhook badge) is the signing key.
   Retell's signing of custom-function calls is an assumption that fails closed (401) if wrong; this has not been confirmed live.
3. **Page:** open the demo page next to the voice page. Pointing the existing website's voice widget at the new agent id is a change outside this repository
   and is not made here; the old own-account demo and any live agent stay as they are.

## Operator setup: reproduce the mock voice demo on a clean machine

Everything the demo needs except secrets and live account state is in this repository. `python -m aria_booking demo-agent <action>` replaces the
operator's earlier out-of-repo scripts. **It never places a call, starts a service, starts a tunnel or opens a browser**; creating the agent, serving,
tunnelling and calling are separate, deliberate steps you take yourself. Actions: `plan`, `store-secret`, `create`, `update`, `verify`, `preflight`, `prepare-web`, `call-summary`.

**Inputs you supply (none of these is in Git):**

| Input | What it is | How it is supplied |
|---|---|---|
| `ARIA_RETELL_API_KEY` | the Retell private API key, which is also the signing key for custom-function calls | environment variable, **or** an encrypted file in `ARIA_SECRET_DIR` (Windows) |
| `ARIA_RETELL_TOOL_TOKEN` | the bearer token the three functions send; the same value must be in the environment of `demo-serve` | environment variable, or generated once into `ARIA_SECRET_DIR` by `create` |
| `ARIA_RETELL_PUBLIC_KEY` | the Retell browser (public) key for the voice page; it must start with `public_key` | environment variable, or encrypted file |
| `ARIA_SECRET_DIR` | a folder **outside the repository** for Windows-encrypted secret files (`retell-api-key.dpapi` etc., readable only by your Windows user) | environment variable; fill with `demo-agent store-secret --secret-name retell_api_key` (typed hidden, never echoed, never overwrites) |
| `ARIA_DEMO_TUNNEL_URL` | the public `https://` address of YOUR tunnel to the tool port 8787 (no path, no credentials) | environment variable, `--tunnel-url`, or `tunnel_url` in the config file |
| `ARIA_DEMO_CONFIG` | optional JSON file of non-secret settings (agent name, voice, model, call cap, retention days, ports, folders) | copy `demo_agent.example.json` somewhere outside Git, edit, point this at it |
| `ARIA_DEMO_STATE_DIR` | where ids, "create pending" markers and results are written (default `booking/.local/demo-agent`, git-ignored) | optional |

**Steps** (each is separate; stop at any refusal and read it):

1. `python -m aria_booking demo-agent plan` — offline. Prints exactly what `create` would send (tool URLs, prompt hash, caps), which secrets and state exist,
   and nothing secret. No network.
2. Serve the demo yourself, in a shell where `ARIA_RETELL_API_KEY` and `ARIA_RETELL_TOOL_TOKEN` hold the same values the operator uses: `python -m aria_booking demo-serve` (see above).
3. `python -m aria_booking demo-agent preflight --base-url http://127.0.0.1:8787` — signed requests call all three functions (`lookup_service`, `check_slot` three times, and
   `find_alternatives` for the busy 4 PM slot, which must offer other times and never the occupied one); every availability reply must say it is the demo
   calendar, an unsigned request is 401 and a booking route is 404. Records seconds per call (**tool latency, not spoken-call latency**). Run it again with the tunnel address (omit
   `--base-url`) once your tunnel exists.
4. `python -m aria_booking demo-agent create --confirm-create` — **the one-time creation** of one Retell LLM and one agent named "Aria - Simulated Calendar
   Demo" (separate from any live agent). It refuses without the flag. A `*-create-pending.json` marker is written before each Retell call: if the outcome is
   uncertain (network error, timeout, HTTP 5xx) the next run stops and tells you to inspect the Retell account; it never retries blindly. Both steps end in a
   read-back, and so does `demo-agent verify` at any time: the prompt must equal `prompt_demo_calendar.md` plus the clock line, only the three read-only
   functions exist (no `book_slot`), their URLs, headers, parameters and spoken-wait fields match, the call cap is 5 minutes, retention 7 days, contact
   memory off, and the opening line equals the configured one. Any difference is a refusal that lists what differs. The read-back also prints the agent-to-LLM
   binding (engine version, LLM version, published flag); `update` additionally refuses to continue if the voice, model, temperature, language, call cap, retention
   or contact memory changed, and says so when they were left exactly as found.
5. `python -m aria_booking demo-agent prepare-web` — builds the isolated Serenity Spa page (teal, responsive, with photos) from the showcase source plus a public-only
   `demo-config.js` (agent id, version, public key; never a secret), with the discreet "Prototype showcase" label and the retention sentence. The repository's own
   website files are neither read nor modified. It prints the serve command and writes `test-instructions.txt` (in the state
   folder) with the steps and scripted questions. **Open the page at `http://localhost:5174/` (`--page-port` or `page_port` changes the number), never at
   `http://127.0.0.1:5174/`:** the Retell public key's allowed-domain list names `localhost` (and the operator's published domain), not the numeric address, and
   a call from it fails with a domain error. The page now keeps that specific error on screen instead of replacing it with the generic microphone message.
6. A supervised call, after Sol or Shobana start it. `call-summary` can then list the newest call's tool events and timings (no transcript text is kept).

**Intentionally local (not and never in Git):** the Retell API key, tool token and public key; encrypted `.dpapi` files and their folder; the config file with
the real tunnel address; `booking/.local/` (created ids, markers, preflight and call-summary results, the generated page); the generated `demo-config.js`;
tunnel logs; Booksy login profiles and session files; real account and agent ids; call recordings and transcripts. The repository's `.gitignore` covers the
state folder and `demo-config.js`, and a test fails if anything of that kind becomes trackable.

**Observed once, not a guarantee:** in the first supervised voice test the operator's review of the finished Retell call found that real function requests were
accepted by this server's signature and token checks (`find_alternatives` and `check_slot` answered in about 0.4-0.5 s each) and that no booking function was called.

**Not verified, and not claimed:** the field names in `call-summary`'s read of a call (copied from the operator's draft, not checked against Retell's
documentation); spoken final-answer speed (Retell's end-to-end response samples include the spoken "checking" sentence, so they do not show it); and how Retell
treats LLM versions after an update (its documentation does not say which LLM version a web call with `agent_version` 0 uses, so a change is proved only by a
supervised call).

**Changing the existing agent in place:** `python -m aria_booking demo-agent update --confirm-update` sends exactly the reviewed prompt, the three read-only
functions (plus `end_call`) and the opening line to the one recorded LLM (a single allow-listed PATCH to `update-retell-llm/<recorded id>`; no new agent or LLM, the
agent itself is never touched), then runs the same read-back as `create`. It is safe to repeat, removes the previous "verified" record first, and uses the recorded
tunnel address unless `--tunnel-url` gives a new one (which also moves the functions to the new address). Use it after the prompt, the demo services or the tool file
change; `verify` shows what differs beforehand and changes nothing. Restart `demo-serve` (not the tunnel) so it loads the same services, and re-run `prepare-web` for a
changed page; the page server needs no restart.

## The website's Calendar and Staff calendar views

The prepared website has a **Calendar** view (open times for a chosen treatment, length, day and optional technician) and a **Staff calendar** view (this schedule, in a frame), both
in the same tab with a **Back to ...** control. The open times come from `GET /demo/open-times?service_id=...&date=YYYY-MM-DD[&staff=Name]` on this server, derived from the same
data and rules the voice reads, so the page and Aria agree. The server answers read-only JSON and lets only the prepared page's own origin (`http://localhost:<page-port>`,
`http://127.0.0.1:<page-port>`) read it; start it with `--page-port N` if the page is served on a port other than 5174. `GET /demo/changes` answers a short stamp (changes with
every stored booking, cancellation or move, and with the demo date) that the website asks while a view is open so it can refresh only when it must. See SHOWCASE.md for the behaviour.

## What to ask (the scripted days: the demo date + 1 and + 2)

On the demo date + 1 (a massage at 4 PM unless stated). `python -m aria_booking demo-day [--date YYYY-MM-DD] [--today YYYY-MM-DD]` prints who works and what is booked on any covered day:

| ask | expected |
|---|---|
| "Is Sienna available tomorrow at 4 for a one-hour deep tissue massage?" | **yes**, with Sienna (free from 3 PM) |
| "What about Marisol?" | **booked** at 4 (3:45-5:15 PM); offers her other times |
| "And Imogen?" | Imogen is **off** that day: "I don't see Imogen on the schedule" |
| "Tamsin at 4 for 90 minutes?" | the visit would **run into** her 5 PM appointment |
| "Tamsin at 4 for an hour?" | fits before 5 PM but **not with the 15 minutes of cleanup** a massage needs; a 30-minute one is available |
| "Rowan at 4?" | **off between 3 and 5 PM** (a break), so not available; offers 5 PM |
| "Delphine for a facial?" | answered from her real (complete) schedule like everyone else's. Every standard column has working days and hours; a person not working that day is off duty, a KNOWN state |
| (diagnostic only) `demo-serve --diagnostic-incomplete-calendar` | Delphine's hours are not shown, so a time only she could take is **unknown** ("I can't confirm", never free); with others who are known-busy the answer says both things and offers real alternatives |
| "Who is free at 4?" | the first eligible technician who really is (Sienna) |
| "Who does the Microdermabrasion Facial?" | `lookup_service` lists the demo's **facial** technicians (Tamsin, Odette, Priya, Noor, Calista, Delphine) |
| "Is this schedule real?" | answered truthfully: a prototype with an invented calendar, not the salon's real schedule (never volunteered otherwise) |
| "Is Sienna available for a Microdermabrasion Facial?" | Sienna is a massage and head-spa technician in the demo roster, so **she does not do it**; it offers those who do (she is never made facial-eligible or swapped out) |
| "Is Tamzin free?" | "I don't see Tamzin": it does **not** become Tamsin; the agent asks who was meant |
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
