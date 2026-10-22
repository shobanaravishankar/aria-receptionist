# Serenity showcase on Netlify: deploy pack

Status of this folder: **a candidate, not a deployment.** Nothing here has been uploaded, no database exists on Netlify for it, no agent or tunnel has been changed, nothing has been paid for. The
local demo and its web folder, store, agent and tunnel are untouched and remain the fallback. What is and is not proven is stated in section 7; read it before relying on any of this.

## 1. What it is

One Netlify site (the existing Free-plan project), three parts on one address:

| Part | Where | Notes |
|---|---|---|
| The website | a prepared folder (`site/`, built by `scripts/build_site.py`, **not committed**) | the same page as the local demo; its calendar frame and open times read from its own address; polls only during a live call |
| The voice tools and the calendar | one Function, `netlify/functions/api.mts` (TypeScript) | `POST /tools/<eleven names>`, `GET /demo/open-times`, `/demo/changes`, `/demo/health`, the staff page `/pro/en-us/9000001/calendar`, `/healthz`, `/readyz` |
| The records | Netlify Database (Postgres), schema in `netlify/database/migrations/0001_showcase/migration.sql` | overlap is refused by the database itself (exclusion constraints), every write is one atomic statement |

No Python runs on Netlify. `src/` is a port of the Python backend's logic, held to the Python code by golden vectors (`oracle/`), recorded call scenarios and tests (section 6).

## 2. What you need (nothing else)

- The existing Netlify project and a Netlify CLI that is logged in with access to it (pinned version in the reviewer's tool folder; not installed here).
- Three **site environment variables** (typed in the Netlify UI or `netlify env:set`; never in Git, never in a page): `ARIA_RETELL_API_KEY` (the key that owns the agent), `ARIA_RETELL_TOOL_TOKEN` (the
  bearer token the agent's tools already send). Optional: `ARIA_CONTACT` (how the agent says to reach the salon, once a number exists), `ARIA_SITE_ORIGINS` (other websites allowed to read the calendar).
- The Retell **public** key and the agent id (public by design) for the page build.
- Node 22 or later to run the scripts (the bundled Node 24 is fine). `pnpm install` in this folder installs the pinned packages from `pnpm-lock.yaml`.

## 3. Order of work, with the check after each step and the way back

Do the draft first. A draft deploy has no production-publish charge, but function, database and traffic activity are still metered.

1. **Build the website bundle** into a new folder (from the `booking` folder):
   `python netlify_showcase/scripts/build_site.py --out netlify_showcase/site --origin https://<the site>.netlify.app --public-key <public key> --agent-id <agent id> [--avatar <approved image>]`
   It refuses a non-empty folder and a non-public key. *Check:* the folder holds `index.html styles.css voice.js views.js site.js demo-config.js _headers assets/`. *Way back:* delete the folder.
2. **Deploy a DRAFT** (not production) from `booking/netlify_showcase`: `netlify deploy --dir site --functions netlify/functions` (no `--prod`). Netlify prints a draft URL.
   *The first thing this settles, and the docs do not say:* whether a CLI deploy of this kind (a) bundles the TypeScript Function, (b) provisions the database and applies `migrations/`, (c) gives the
   Function its database address. If (b) did not happen, apply the schema with `NETLIFY_DB_URL=... node scripts/apply_migrations.mjs` (a fallback that records what it applied in the table the
   emulator uses). If the CLI cannot do this at all, the next route is a Git-connected build, which is a decision for the project lead, not something prepared here.
3. **Set the environment variables** on the site, then redeploy the draft.
4. **Smoke test the draft** (read-only first): `ARIA_RETELL_API_KEY=... ARIA_RETELL_TOOL_TOKEN=... node scripts/smoke_public.mjs https://<the draft url>`. Then with `--write` to make one
   clearly synthetic booking ("Smoke Test", 555-01xx) and cancel it. Every check must say PASS. This also shows the cold start: the first database request after five idle minutes is slower
   (the database sleeps); `readyz` and `check_slot` timings are printed.
5. **Look at it** in a browser on the draft URL: the page, both calendar views, the staff frame, the call button (voice will not connect until step 7).
6. **Review before anything public.** Nothing below happens until the reviewer has the evidence from 4 and 5.
7. **Point the one test agent at the site** (a bounded configuration transition: take a fresh rollback snapshot of the agent first; no duplicate agent, no new tunnel) and add the site's hostname to the
   public key's allowed domains. *Way back:* point the agent's tools at the local tunnel again.
8. **Publish to production** (`netlify deploy --prod ...`): a production deploy costs 15 credits. *Way back, instant:* Deploys, open the previous deploy, **Publish deploy**.
9. **One supervised call** from the public address. Only a call proves speech, latency and the microphone path.

## 4. Free-plan credits (the hard limit pauses ALL projects on the team)

Estimates, not measurements; the draft run in step 4 is where to measure. Per the plan pages read earlier: Free = 300 credits a month, no recharge, hard stop; database compute 10 credits per
unit-hour (it sleeps after 5 idle minutes); bandwidth 20/GB; web requests 2 per 10k; production deploy 15; previews and branch deploys are not metered for deploys.

- One voice session of a few minutes keeps the database awake for its length: about 1.7 credits. One calendar visit about 1. After a handful of deploys: roughly 100 voice sessions or 190 visits a month.
- The design spends nothing while idle: no scheduled function, no keep-alive, no polling by an idle page (the page asks the calendar only while a call is live, once when a view opens, once when a call
  ends, and on the Refresh buttons), `/healthz` and `/demo/health` never touch the database, `/readyz` needs the bearer token (a public address cannot wake the database), and an unsigned tool call is
  refused before any database access. Check the credit usage page after step 4 and again after the supervised call; stop if it moves faster than the estimate.
- Public traffic is not unlimited demonstration capacity: this is a sales showcase for a handful of salons, not an always-on service.

## 5. Rollback and preservation

The local demo is the baseline and is never touched by anything here. On Netlify every production deploy is kept; republishing the previous one is instant. The agent's tools point at one place at a time;
pointing them back at the local tunnel restores the local setup. The database holds only fictional data; deleting it loses nothing real.

## 6. Evidence in this folder (what each test really compares)

`node --test "test/**/*.test.ts"` (Node 22+; the contract tests need the pinned packages):

- `test/logic`: calendar, overlay, snapshots, judge, open times, menu lookup, speech, phone, the staff page (byte for byte) and **40 recorded random walks of 4,400 requests** against the Python tools'
  answers and final store state.
- `test/contract/store.contract.test.ts`: 17 store-contract tests on the in-memory model **and on Postgres**, including races for one slot, identical request keys racing, two moves racing, a client racing
  against itself.
- `test/contract/scenarios.pg.test.ts`: the same 4,400 recorded requests on the Postgres store and the Postgres call-state store, every response and the final database compared.
- `test/contract/function.test.ts`: the real Function adapter on the local Postgres: authentication before any database access, the daily upkeep once, book and cancel over HTTP, the calendar page, the
  smoke script itself.
- `tests/test_netlify_showcase_data.py` (Python): the exported data files equal what the Python modules say.

## 7. What is NOT proven (read this)

- The Postgres used by every test is **PGlite** (PostgreSQL 17.5 compiled to WebAssembly) behind `@netlify/database-dev`. It proves the SQL is correct, overlap is refused and a lost race is reported.
  It does **not** prove hosted cold start and resume, the hosted connector (`@netlify/database` chooses a pg pool or the Neon serverless pool by environment), true concurrency, query time limits, or
  that Netlify applies the migration on a CLI deploy.
- Nothing has run on Netlify: bundling the Function (TypeScript, a JSON import, the `@netlify/database` package), its time and payload limits, and the draft deploy route are unverified until step 2.
- Voice behaviour is unproven: what the recogniser hears, whether the model passes `service_words` and the `starting_*` bounds faithfully, the spoken wording and latency. The agent's prompt and tool
  definitions must be the regenerated ones from the rehearsal corrections (`voice_agent_draft/*_lifecycle_preferences.*`).
- The Python test suite is not evidence for any of this folder, and this folder's tests are not evidence for the Python backend.
- Pinned versions: `@netlify/database 2.0.1`, `@netlify/functions 6.0.3`, `@netlify/database-dev 1.0.1`, `pg 8.23.1` (see `pnpm-lock.yaml`).
