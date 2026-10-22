# Serenity showcase on Netlify: deploy pack

Status of this folder: **a candidate, not a deployment.** Nothing here has been uploaded, no database exists on Netlify for it, no agent or tunnel has been changed, nothing has been paid for. The
local demo and its web folder, store, agent and tunnel are untouched and remain the fallback. What is and is not proven is stated in section 7; read it before relying on any of this.

## 1. What it is

One Netlify site (the existing Free-plan project), three parts on one address:

| Part | Where | Notes |
|---|---|---|
| The website | a prepared folder (`site/`, built by `scripts/build_site.py`, **not committed**) | the same page as the local demo; **same-origin by default** (its calendar frame and open times are read from whatever address it is served from, so one build serves a draft and the live site); polls only during a live call |
| The voice tools and the calendar | one Function, `netlify/functions/api.mts` (TypeScript) | `POST /tools/<eleven names>`, `GET /demo/open-times`, `/demo/changes`, `/demo/health`, the staff page `/pro/en-us/9000001/calendar`, `/healthz`, `/readyz` |
| The records | Netlify Database (Postgres), schema in `netlify/database/migrations/0001..0003` | overlap is refused by the database itself (exclusion constraints), every write is one atomic statement |

No Python runs on Netlify. `src/` is a port of the Python backend's logic, held to the Python code by golden vectors (`oracle/`), recorded call scenarios and tests (section 6).

## 2. What you need (nothing else)

- The existing Netlify project and a Netlify CLI that is logged in with access to it (pinned version in the reviewer's tool folder; not installed here).
- Three **site environment variables** (typed in the Netlify UI or `netlify env:set`; never in Git, never in a page): `ARIA_RETELL_API_KEY` (the key that owns the agent), `ARIA_RETELL_TOOL_TOKEN` (the
  bearer token the agent's tools already send). Optional: `ARIA_CONTACT` (how the agent says to reach the salon, once a number exists), `ARIA_SITE_ORIGINS` (other websites allowed to read the calendar).
- The Retell **public** key and the agent id (public by design) for the page build, and the approved avatar image (`--avatar`; never commit it).
- Node 22 or later to run the scripts (the bundled Node 24 is fine). `pnpm install --frozen-lockfile` in this folder installs the pinned packages from `pnpm-lock.yaml` (the lockfile and
  `package.json` agree; CI keeps the frozen install).
- Python 3.12 only for the site build and for exporting the preserved store.

## 3. Order of work, with the check after each step and the way back

Do the draft first. A draft deploy has no production-publish charge, but function, database and traffic activity are still metered.

1. **Build the website bundle** into a new folder (from the `booking` folder):
   `python netlify_showcase/scripts/build_site.py --out netlify_showcase/site --public-key <public key> --agent-id <agent id> --avatar <approved image>`
   (`--origin` defaults to `same-origin`; pass an https address only to pin a build to one site.) It refuses a non-empty folder and a non-public key. *Check:* the folder holds
   `index.html styles.css voice.js views.js site.js demo-config.js _headers assets/`, and `grep -ri localhost site/` finds nothing. *Way back:* delete the folder.
2. **Deploy a DRAFT** (not production) from `booking/netlify_showcase`: `netlify deploy --dir site --functions netlify/functions` (no `--prod`). Netlify prints a draft URL.
   *The first thing this settles, and the docs do not say:* whether a CLI deploy of this kind (a) bundles the TypeScript Function, (b) provisions the database and applies `migrations/`
   (0001 the schema, 0002 the call-memory revision, 0003 the preference generation), (c) gives the Function its database address. If (b) did not happen, apply the schema with
   `NETLIFY_DB_URL=... node scripts/apply_migrations.mjs` (a fallback that applies them in order and records them in the table the emulator uses). If the CLI cannot do this at all, the next
   route is a Git-connected build, which is a decision for the project lead, not something prepared here.
3. **Set the environment variables** on the site, then redeploy the draft.
4. **Import the preserved demo data, BEFORE any request that opens the records.** The first request that needs data runs the daily upkeep, which tops up missing fictional seeds; importing first
   keeps the existing history, references, ids, retry keys and alert rows exactly as they are (importing after is also safe on an empty or fictional database with `--replace-existing`).
   a. From a **fresh read-only copy** of the local store (made with SQLite's backup API; never the live file): `python netlify_showcase/scripts/export_store.py --db <copy> --out <name>.snapshot.json`
      (prints table names, row counts and digests only; the snapshot is data about people: `*.snapshot.json` is git-ignored; do not commit, post or paste it).
   b. **Dry run** against the Netlify database: `NETLIFY_DB_URL=... node netlify_showcase/scripts/import_store.mjs <name>.snapshot.json`. It does everything in one transaction, checks the snapshot against
      its own digests first, reads every table back from Postgres and compares row counts and digests with the manifest, then ROLLS BACK. Every line must say PASS and the last must say DRY RUN OK.
      A row the database refuses (for example an overlap) is reported by table and reference only, and nothing is kept.
   c. `--apply` the same command to keep it (it commits only if everything verified). Running it again says "already imported" and changes nothing. A database that already holds other data is
      refused unless `--replace-existing` is added (it empties the showcase's tables first: it is a fictional demo database).
   d. *Way back:* `--undo` (removes the import, only if nothing was added or changed since), or restore the database from a Netlify backup. The importer never sends or touches an alert row.
5. **Smoke test the draft.** Read-only first: `ARIA_RETELL_API_KEY=... ARIA_RETELL_TOOL_TOKEN=... node scripts/smoke_public.mjs https://<the draft url>`. It exercises all eleven tools in their
   read-only paths, authentication, the calendar reads and input validation. Then with `--write --record smoke-record.json`: one synthetic client ("Smoke Test <letters>", a 555-01xx number, both
   unique to the run; every call id carries the run id) races a second synthetic client for the same slot (exactly one may win: the hosted write-concurrency check), then repeats a yes, moves,
   saves / reads / removes a preference and cancels. It prints and records every change it made; cancelled synthetic history stays in the records (it cannot delete). Every check must say PASS.
   `--after-idle 330` waits for the database to sleep and prints how long the first request after that took (the cold-resume time). Stop and read the credit usage page afterwards.
6. **Look at it** in a browser on the draft URL: the page, both calendar views, the staff frame, the call button (voice will not connect until step 8).
7. **Review before anything public.** Nothing below happens until the reviewer has the evidence from 4, 5 and 6.
8. **Point the one test agent at the site** (a bounded configuration transition: take a fresh rollback snapshot of the agent first; no duplicate agent, no new tunnel; the agent needs the REGENERATED
   prompt and tool files, `voice_agent_draft/*_lifecycle_preferences.*`) and add the site's hostname to the Retell public key's allowed domains. *Way back:* point the agent's tools at the local tunnel again.
9. **Publish to production** (`netlify deploy --prod ...`): a production deploy costs 15 credits. The same bundle works (same-origin). *Way back, instant:* Deploys, open the previous deploy, **Publish deploy**.
10. **One supervised call** from the public address. Only a call proves speech, latency and the microphone path.

## 4. Free-plan credits (the hard limit pauses ALL projects on the team)

Estimates, not measurements; the draft run in step 5 is where to measure. Per the plan pages read earlier: Free = 300 credits a month, no recharge, hard stop; database compute 10 credits per
unit-hour (it sleeps after 5 idle minutes); bandwidth 20/GB; web requests 2 per 10k; production deploy 15; previews and branch deploys are not metered for deploys.

- One voice session of a few minutes keeps the database awake for its length: about 1.7 credits. One calendar visit about 1. After a handful of deploys: roughly 100 voice sessions or 190 visits a month.
  Each tool call now makes one extra small write (the step's lease on its call): a few milliseconds of an awake database, not a measurable change in the estimate; measure it in step 5.
- The design spends nothing while idle: no scheduled function, no keep-alive, no polling by an idle page (the page asks the calendar only while a call is live, once when a view opens, once when a call
  ends, and on the Refresh buttons), `/healthz` and `/demo/health` never touch the database, `/readyz` needs the bearer token (a public address cannot wake the database), an unsigned tool call is
  refused before any database access, and a malformed or record-free public read (an unknown treatment, a date outside the demo window, a technician nobody has) is answered or refused without opening
  the database. Check the credit usage page after step 5 and again after the supervised call; stop if it moves faster than the estimate.
- Public traffic is not unlimited demonstration capacity: this is a sales showcase for a handful of salons, not an always-on service.

## 5. Rollback and preservation

The local demo is the baseline and is never touched by anything here. On Netlify every production deploy is kept; republishing the previous one is instant. The agent's tools point at one place at a time;
pointing them back at the local tunnel restores the local setup. The database holds only fictional data (plus whatever names and numbers were given on rehearsal calls: treat the snapshot as data about
people); `--undo` or a restore removes the import.

## 6. Evidence in this folder (what each test really compares)

`node --test "test/**/*.test.ts"` (Node 22+; the contract tests need the pinned packages):

- `test/logic`: calendar, overlay, snapshots, judge, open times, menu lookup, speech, phone, the staff page (byte for byte), the HTTP validation and caching rules, and **40 recorded random walks of
  4,400 requests** against the Python tools' answers and final store state.
- `test/contract/store.contract.test.ts`: store-contract tests on the in-memory model **and on Postgres**: races for one slot, identical request keys racing, two moves racing, a client racing
  against itself, preference writes (one effect per key, only against the confirmed state, never-reused generations).
- `test/contract/concurrency.test.ts`, `steps.test.ts`: one statement per preference write; parallel tool calls of one call (limits hold, a step at a time); a step that already made its effect is never
  redone and its change is counted once; a step abandoned mid-way closes the call (needs_review); stale confirmations at the tool layer.
- `test/contract/upkeep.test.ts`: the daily upkeep lease (failure retry, waiting callers, takeover, the reviewer's failed-seed reproduction through the real Function, a poisoned-instance recovery).
- `test/contract/import.test.ts`: the import (digests agree between Python and Node, damaged files refused, dry run, apply, replay, refusals, a rewriting trigger caught by the read-back, undo).
- `test/contract/scenarios.pg.test.ts`: the same 4,400 recorded requests on the Postgres store and the Postgres call-state store, every response and the final database compared.
- `test/contract/function.test.ts`: the real Function adapter on the local Postgres: authentication before any database access, the daily upkeep once, book and cancel over HTTP, the calendar page,
  and the operator's smoke script itself (run twice, write path included, mutations recorded, nothing left confirmed).
- Python: `tests/test_netlify_showcase_data.py` (the exported data files equal what the Python modules say), `tests/test_same_origin_site.py` (the same-origin bundle, in a headless browser too).

## 7. What is NOT proven (read this)

- The Postgres used by every test is **PGlite** (PostgreSQL 17.5 compiled to WebAssembly) behind `@netlify/database-dev`. It proves the SQL is correct, overlap is refused, a lost race is reported and
  the recovery logic behaves. It does **not** prove hosted cold start and resume, the hosted connector (`@netlify/database` chooses a pg pool or the Neon serverless pool by environment), true
  concurrency, query time limits, **lease timing** (the call lease is 20 s, the upkeep lease 90 s; whether real hosted latencies ever outlast them is unmeasured), or that Netlify applies the migrations on
  a CLI deploy.
- Nothing has run on Netlify: bundling the Function (TypeScript, a JSON import, the `@netlify/database` package), its time and payload limits, and the draft deploy route are unverified until step 2.
- The import was exercised on a copy of the preserved store (5 clients, 16 appointments, 20 events, 4 request keys, 4 alert rows, 1 preference and its records) against a LOCAL emulated Postgres: dry run,
  apply, replay and a world opened over it. Importing into the hosted database is unverified until step 4.
- Voice behaviour is unproven: what the recogniser hears, whether the model passes `service_words` and the `starting_*` bounds faithfully, the spoken wording and latency. The agent's prompt and tool
  definitions must be the regenerated ones from the rehearsal corrections (`voice_agent_draft/*_lifecycle_preferences.*`).
- A call closed by an abandoned step stays closed for the rest of that call (`call_interrupted`): a deliberate fail-closed choice, so a rare stall can cost a caller their call, never an invented result.
- The Python test suite is not evidence for any of this folder, and this folder's tests are not evidence for the Python backend.
- Pinned versions: `@netlify/database 2.0.1`, `@netlify/functions 6.0.3`, `@netlify/database-dev 1.0.1`, `pg 8.23.1` (see `pnpm-lock.yaml`).
