# Free-Netlify sales showcase: architecture, risks and feasibility

> **Where this stands (2026-10-22).** The sections below are the ORIGINAL assessment, kept for the record. The port they describe now exists as a **candidate** in this folder (TypeScript
> logic held to the Python backend by golden vectors; a Postgres store; the eleven tools; the HTTP surface; the Function; the site-bundle builder; the import; the operator's smoke script).
> **Nothing has been deployed or run on Netlify.** Where the original text and this box differ, THIS BOX is current:
>
> - **Conflicts are refused by the database, not by cells.** Each confirmed appointment carries two generated `int8range` columns with `EXCLUDE USING gist` constraints (per technician: the whole
>   OCCUPIED time including cleanup; per client: the service time), so no stored row, however it got there (booked, seeded, imported), can overlap another; a reschedule that overlaps its OWN
>   old time is one `UPDATE`. Every write is ONE statement, so nothing depends on interactive transactions. (The `occupancy` cell table of section 3 does not exist.) Preference writes are one
>   statement too: the request key is claimed in the same statement as the change, and a change names the saved state the caller confirmed (technician + a never-reused *generation*), so a stale
>   or retried confirmation changes nothing.
> - **A call's memory is a row** (`call_state`: numbers tried as digests, failed matches, changes, attempts, identity, the start time last asked for) **saved by compare-and-set** (`rev`).
>   Every tool step takes a short lease on its call (a field of that row), so steps of one call run one at a time on any instance and the per-call limits are checked before effects. A step that
>   lost the compare-and-set is redone if it made no effect, and never redone if it did (its changes are folded into the newer memory). If a step is abandoned mid-way (its lease runs out
>   unreported) the call is **closed**: every later step answers `needs_review / call_interrupted`; nothing is invented and no budget is reset.
> - **Daily upkeep** is claimed by an expiring lease in `meta` and marked done only AFTER it succeeded; a failed holder releases at once, a dead one is taken over, callers meanwhile wait and are never
>   served from a half-made day. No scheduled function, no polling, no keep-alive.
> - **Public reads** are validated before any database is opened (an unknown treatment, a date outside the demo window, a technician nobody has are answered from the fixed schedule and menu);
>   only answers that need no record may be kept, briefly, by the visitor's own browser (`private, max-age`); everything read from the records is `no-store`.
> - **The site bundle is same-origin by default**: the page resolves the calendar's address to its own at run time, so one build serves a draft and the live site; polling is call-only (one question
>   when a view opens, one when a call ends, and Refresh buttons; while a call is live it asks as before).
> - **The preserved demo data is imported, not re-seeded**: `scripts/export_store.py` (a read-only snapshot of the SQLite store with a manifest of counts and digests) and `scripts/import_store.mjs`
>   (one transaction; dry run by default; verified table by table by digest; replay-safe; `--undo`). See DEPLOY_PACK.md section 3.
> - **Evidence so far is local**: the 4,400-request replay of Python-recorded conversations on the in-memory model AND on Postgres (PGlite), store contracts on both backends, races, crash and
>   recovery cases, the import on a real copy of the preserved store, 130+ Node tests, and mutation checks of each fix. PGlite is an emulator (PostgreSQL 17.5 on WebAssembly, one session at a
>   time): it shows the SQL and the logic are right, **not** hosted cold resume, the hosted connector, true concurrency or lease timing. Voice behaviour is unproven.

## 1. Verdict

1. **A free, Netlify-only version is architecturally possible** and the smallest sound shape is: the existing static site + Netlify Functions (TypeScript) + Netlify Database (managed Postgres, listed as
   included on the Free plan). It is a **port of the backend's business logic and storage to TypeScript and Postgres**, not an adapter: Python is not a documented Functions runtime, and there is no
   persistent process to run the current server.
2. The port is large (about **5,400 lines of Python** carry the behaviour; section 4) and the platform has **unverified behaviours that can only be settled by running on Netlify** (section 7).
3. **Credits, not features, are the binding limit**: 300 a month, a hard stop that pauses every project on the team. A sales showcase on the Free plan is workable for demonstrations by a person,
   **not** for open public traffic (section 5).
4. **Not credible for tomorrow's meeting** as a deployed, independently reviewed system. Tomorrow should use what already works (the reviewed local version). A first reviewable increment (the
   dependency-free logic port with parity evidence) can be produced; the platform spike and the database tests need access or permission described in section 8.

## 2. What the official pages say

| Topic | What the pages state | Not stated on the pages read |
|---|---|---|
| Functions runtime | TypeScript (`.mts`) and JavaScript (`.mjs`); handlers take a web-standard `Request` and return a `Response`; routed with `config.path`; Go through a Lambda-compatible API. **Python is not mentioned.** | synchronous timeout, payload limits, bundle size, concurrency, cold-start time |
| Functions cost | default memory 1024 MB; compute is metered in GB-hours at **10 credits per GB-hour** | invocation limits on Free |
| Database | "a fully managed Postgres database built into the Netlify platform"; **included on the Free plan** (pricing page: "3 databases, 20 active branches"); migrations applied automatically on production deploys and deploy previews; deploy previews get their own database branch | Postgres version, extensions (for example `btree_gist`), isolation levels |
| Database client | `@netlify/database`: `getDatabase()` returns `{ sql, pool }`; `pool` is a `pg.Pool` and the page shows `BEGIN`, queries and `COMMIT` on one connection; `getConnectionString()` for your own driver | what connector is used **inside Functions** ("a different underlying connector" per environment), whether `FOR UPDATE` or advisory locks behave as in plain Postgres, pool size, query time limits |
| Free database limits | compute fixed at **1 unit**; **sleep after inactivity fixed at 5 minutes** (wakes on the next query; "well under a second, but this is not guaranteed"); **48 compute units per billing period**; 5 GB storage, 5 GB written and 5 GB bandwidth out per period | first-request behaviour after a sleep; what happens at the limits |
| Credits | Free: **300 credits a month, hard limit, no recharge**; database compute 10 credits per unit-hour; bandwidth 20 per GB (also database bandwidth); web requests 2 per 10,000; **production deploy 15 credits**; deploy previews and branch deploys not metered | exact behaviour at exhaustion on the pages I read (the reviewer's own source says all projects on the team pause until reset) |
| Storage cost | "free until July 1, 2026"; rate afterwards not given | the post-July rate; the pricing page says 7-day backup retention, the database page says 3 days (**they disagree**) |
| Local development | `netlify dev` starts a local Postgres-compatible database; tests can use `@netlify/database-dev` (in-memory, applies migrations) | the engine behind it |
| Deploy | Git, CLI (`netlify deploy --prod`), drag and drop (Netlify Drop), API | **whether a Drop or manual folder deploy includes Functions or applies Database migrations** (the page is silent and contains a note that a manual deploy runs no build) |

## 3. The smallest sound architecture

```
 visitor ──https──> the SAME site (static files, same origin)           Retell ──https, signed + bearer──> /tools/<name>
        │  /demo/open-times  /demo/changes  /demo/health  /pro/.../calendar          │
        └───────────────────────────────┬────────────────────────────────────────────┘
                                 Netlify Functions (TypeScript, ONE router function)
                                        │  pure logic: catalogue, roster, schedules, open times, judge, tools
                                        ▼
                              Netlify Database (Postgres): appointments, clients, occupancy, confirmations, preferences ...
```

**Routes (one function, `config.path`)**: `POST /tools/<name>` (the eleven functions), `GET /demo/open-times`, `/demo/changes`, `/demo/health`, the staff calendar page `/pro/en-us/9000001/calendar`,
`/healthz`, `/readyz`. Same origin as the website, so there is no CORS to configure and the calendar is a relative address (a small change to the site builder).

**Pure logic, no dependencies, no database** (runs and is tested with the Node already on this machine): the menu and service registry, roster and eligibility, the deterministic generated schedules and
scripted days, the open-times engine, the placement judge (hours, breaks, full length plus cleanup, notice, quarter-hour grid), phone normalisation, speech helpers, the discovery answers, the contact
fallback and the tool wording. Static answers (`lookup_service`, the menu, the roster) never read the database.

**Storage (Postgres, same data model as the SQLite file)**: `meta`, `clients`, `appointments`, `appointment_events`, `requests` (idempotency keys), `outbox` (rows only, never sent),
`client_preferences`, `preference_requests`, `preference_events`; plus three tables that exist only because invocations are independent:
`pending_confirmations(token, kind, call_id, payload, expires_at)`, `call_state(call_id, numbers_tried, failed_matches, changes)` (today these live in process memory: `_tokens`, `_numbers`, `_failed`,
`_changes`), and `occupancy(staff_id, cell)`.

**Atomic conflict prevention without relying on interactive transactions**: every appointment occupies 5-minute cells from its start to the end of its service plus cleanup. In this catalogue all
durations are 15, 30, 40, 45, 60, 75 or 90 minutes, cleanup is 0, 5, 10 or 15, and appointments start on the quarter hour, so everything sits on a 5-minute grid. `occupancy` has
`PRIMARY KEY (staff_id, cell)`. Booking is ONE statement (a CTE that inserts the appointment and its cells): two simultaneous bookings for overlapping times cannot both succeed, because the unique key
refuses the second, whatever the transaction behaviour of the connector. Cancel and reschedule are likewise single statements (free and take cells together). The judge still runs first, in application
code, for the reasons and wording the customer hears; the key is the last arbiter. This needs no extension and no `FOR UPDATE`.

**Idempotency and confirmations across invocations**: a request key is a unique row (`INSERT ... ON CONFLICT`), and a repeated confirmed request returns the first result. Confirmation tokens are rows bound to
the call and to exactly what was read; a newer read-back replaces the call's older one; they expire. Per-call limits (numbers tried, failed matches, changes per call) are rows too.

**Phone and name separation, history, preferences**: the same rules and the same keys (client id, family id, staff id) as today. Past and upcoming come from stored times.

**Daily upkeep**: lazy, once per spa day, on the first database request of the day, guarded by a unique `meta` row (no scheduled function, no polling): settle past fictional visits, top up fictional upcoming ones.

**Polling policy (protects the database from sleeping less)**: the website currently asks `/demo/changes` every few seconds while a calendar view is open. In this version the page asks only **while a call
with Aria is live** (when the database is awake anyway) and once when it ends or the view is opened; otherwise there are Refresh buttons. Public read endpoints send short `Cache-Control` so the CDN
absorbs repeats. No keep-alive of any kind.

**Secrets**: `ARIA_RETELL_API_KEY` and `ARIA_RETELL_TOOL_TOKEN` as Netlify environment variables (never in Git or the page); the Retell signature is checked with Web Crypto (HMAC-SHA-256 over the body and
timestamp) and the bearer token with a constant-time compare, before any logic or database access.

**Synthetic-data export and import**: the existing SQLite file is exported to a documented JSON snapshot (a Python command, offline-testable), imported idempotently by one function module that is unit-tested
against an in-memory database and runnable by whoever holds database access; a Postgres export writes the same JSON. Only the fictional records, no secrets.

## 4. Scope: what has to be reproduced

| Python module (lines) | Becomes |
|---|---|
| `voice/tools.py` 981, `catalog/lookup.py` 163, `catalog/bookable.py` 103, `showcase/discovery.py` 144 | catalogue lookup, `lookup_service`, `check_slot`, `find_alternatives`, group answers |
| `appointments/tools.py` 694, `preference_tools.py` 268 | `find_appointments`, `last_completed_visit`, `book/cancel/reschedule_appointment`, `get/save/remove_preference` |
| `appointments/store.py` 573, `overlay.py` 111, `judge.py` 83, `lifecycle.py` 61, `seed.py` 120, `preferences.py` 146, `models.py` 170 | schema, atomic writes, idempotency, merge of schedule and stored appointments, judge, upkeep, seeds |
| `demo/dataset.py` 304, `open_times.py` 79, `driver.py` 81 | deterministic schedules, open times, the reader |
| `demo/page.py` 295, `server.py` 150 | the staff calendar page and the JSON routes |
| `voice/retell_http.py` 170, `history/*` 306, `showcase/catalog.py` 146, `roster.py` 72 | signature and routing, phone and history, menu and roster data |
| **about 5,400 lines** | **an estimated 5,000 to 7,000 lines of TypeScript, plus the tests below** |

The 2,475 Python tests are **not** evidence for the port. Evidence has to be new (section 6).

## 5. The credit budget (estimates; the assumptions are stated, nothing was measured)

- The binding costs are **database compute** (10 credits per unit-hour while awake, and it stays awake for 5 minutes after the last query) and **production deploys** (15 each). Functions compute
  (10 per GB-hour) and web requests are negligible for this traffic; bandwidth is small (a full page visit is about 0.65 MB; 20 credits per GB is about 0.013 credits a visit).
- One voice session (a call of up to five minutes with tool calls, plus the five-minute tail) keeps the database awake for about 10 minutes, about **1.7 credits**. A visitor who only opens the calendar
  view for a minute, with polling off while idle, about **1 credit**.
- With five production deploys (75 credits) and a few tens for bandwidth and functions, roughly **190 credits remain: on the order of 100 voice sessions or 190 calendar visits a month.** The
  compute cap of 48 unit-hours (about 288 ten-minute sessions) is looser than the credits, so the credits bind first.
- **Exhaustion is the real hazard.** The hard limit stops the whole team's projects until reset; a link shared widely, a crawler or a loop could do it. Mitigations that cost nothing: short CDN caching,
  no idle polling, no endpoint that wakes the database without a valid Retell signature or a bounded read, a per-IP and per-call ceiling in the function, and a plan of few production deploys (previews are
  not metered). **No promise of unlimited demonstrations and no always-on database is possible on this plan.**

## 6. Tests that would justify the port

1. **Parity against the Python oracle** (no network): the Python implementation writes golden vectors (schedules for many dates, open times for every bookable service, technician and length, judge decisions
   including DST days, and full tool conversations with their normalised replies); the TypeScript implementation must reproduce them exactly.
2. **Contract**: all eleven tool definitions against the handlers (names, required arguments, statuses, `ok` rules, wording invariants), the Retell signature and bearer checks, the routes and headers.
3. **Persistence and concurrency against a real Postgres engine**: simultaneous bookings for one slot (exactly one wins), repeated confirmed requests (one effect), confirmations and counters surviving
   separate invocations, a cold resume, the shared-phone name separation, preferences, export then import.
   **These need a Postgres engine, which this machine does not have** (section 8). A stand-in that fakes the unique key would test the application's logic but would **not** prove Postgres behaviour,
   and will not be presented as if it did.
4. **A platform spike on a draft deploy** (not metered): a trivial function and table, 20 concurrent booking attempts for one slot, a cold resume timing, the credits used.

## 7. Risks, most serious first

1. **Unverified platform behaviour**: interactive transactions and locks inside Functions (the single-statement design above avoids needing them, but that must be demonstrated), the Functions timeout and
   payload limits (not on the pages read), and the cold-start and database-resume time as a caller hears it.
2. **Deploy path**: the site was made by drag and drop and the pages do not say that a Drop or manual deploy includes Functions or applies Database migrations. A CLI deploy of a built folder
   (which can carry the private avatar) or Git-based builds (which cannot carry it) are the documented alternatives; one of them must be proven by the spike.
3. **Credit exhaustion pauses all team projects**; the post-July storage rate is unknown; the backup retention figures disagree. Nothing should rely on retention.
4. **Behavioural drift in a 5,000-line port**: the reason for the parity vectors, and the reason the port should be reviewed independently before publication.
5. **Spoken latency** (two cold starts on a first tool call) is unverified and cannot be tested offline.
6. **Dependencies**: the handlers import `@netlify/functions` and `@netlify/database` at deploy time (installed by Netlify's build) and cannot be imported locally without installing them.

## 8. Blockers, and the honest feasibility for tomorrow

- **No Node packages and no Postgres on this machine** (no `npm`, `netlify`, `psql`, Docker). A Node 24 runtime does exist (bundled with the browser-test tool) and runs TypeScript directly with its built-in test runner,
  so the **dependency-free logic port, the parity vectors and the contract tests can be built and run here with no download**. The database layer cannot be proven here. To test it for real I would
  need permission to download dev packages from the npm registry (`@netlify/database-dev`, which Netlify's own pages use for tests, plus `pg`, `@netlify/database`, `@netlify/functions`; sizes unknown until
  fetched), or the reviewer to run the spike and the database tests where Netlify access exists.
- **Netlify access and a deploy method** belong to the reviewer and the project lead (draft deploy via the CLI, or an agreed alternative).
- **Tomorrow**: not credible to publish an independently reviewed, spiked, parity-tested port by the meeting; even the first stage depends on a spike nobody has run. Recommended: use the reviewed local
  version for the meeting, and treat this as a separate workstream that does not gate it.

**Staged plan (each stage ends in an immutable commit and evidence; no date is promised)**: S0 the platform spike (draft deploy: function, table, 20 concurrent bookings, resume time, credits, deploy path)
-> S1 dependency-free logic port with parity vectors -> S2 persistence, occupancy atomicity, persisted confirmations, upkeep, export and import, tested against Postgres -> S3 handlers, signature, routes,
same-origin site build with the polling policy -> S4 deploy pack, credit measurement plan and rollback.

## 9. Rejected, and why

Python in Functions (no documented runtime); a Python server elsewhere (declined, and a paid or local dependency); whole-database blob writes (not transactional); a relay to the local PC (a local-PC
dependency, a random tunnel address that expires, two extra hops of latency); keep-alive requests or polling to hold the database awake (defeats the credit limit, and is not wanted).

## Decisions needed

1. Permission to download dev packages from the npm registry for the database tests (or confirmation that the reviewer will run them with Netlify access).
2. Who runs the S0 spike on a draft deploy, and by which deploy method.
3. Agreement that the calendar views stop polling while idle (live updates only during a call, plus Refresh).
4. Confirmation that tomorrow's meeting uses the reviewed local version.
