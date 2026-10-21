# Putting the Serenity demo on a public address (prototype)

Everything here is **invented data**: a fictional spa, fictional clients, a simulated calendar. Nothing reaches a real salon calendar, Booksy or a phone. This document is the plan and the
runbook for giving the demo a public address; **nothing in this repository provisions, publishes or pays for anything**. Every step that needs an account, a card or a dashboard is yours.

## The shape

```
 visitor's browser ──https──> the website (static files)         Netlify (the existing demo)  OR  served by the backend itself
        │  reads open times, shows the staff calendar in a frame
        └──────https──────> the BACKEND  ── one small container, one volume ──  calendar page + /demo/* JSON + /tools/* + /healthz /readyz
 Retell (the existing test agent) ──https, signed + bearer──> the BACKEND  /tools/<name>
```

* The **backend** (`python -m aria_booking demo-public-serve`) is one process: the calendar the voice and the website both read, the signed voice tools, readiness, and (optionally) the website
  itself. Appointments are one SQLite file on a **persistent volume**. It needs only Python and `tzdata` (no browser). It writes only through the signed tool routes.
* The **website** can be served by the backend (one address, nothing cross-origin) **or** uploaded to a static host. For the existing Netlify demo it is uploaded, and the backend lists that
  site's address in `ARIA_SITE_ORIGINS` so the site may read the calendar and show its staff page in a frame (and no other site may).
* Publishing only the website does **not** connect anything: the calendar, the open times, bookings and lookups all need the backend.

## What you must decide, and what it costs (verify prices on each vendor's page; these come from third-party summaries and were not checked on the vendors' own pages)

| Choice | About | Notes |
|---|---|---|
| **Fly.io** (recommended) | shared-cpu-1x 256 MB about $1.94/month, volume $0.15/GB-month (1 GB is plenty), egress $0.02/GB; roughly $2 to $4/month with 256 to 512 MB | account + payment card + the `flyctl` command line; a free `*.fly.dev` https hostname and certificate are included; always on |
| Render | Starter web service $7/month + disk $0.25/GB-month | dashboard only, no command line; the **free tier cannot attach a disk and sleeps after about 15 minutes idle**, which would ruin voice-tool latency |
| Your own PC + a Cloudflare *named* tunnel | no hosting fee | needs a domain on Cloudflare DNS (domain cost) and the PC awake; quick tunnels get a random hostname that changes on restart: avoid |

You also need: the Retell public key's **allowed domains** to include the website's hostname (below), and the existing test agent's tool addresses moved to the backend (below).

## Settings (host variables and host secrets; never in Git, never in the page)

| Name | Kind | Meaning |
|---|---|---|
| `ARIA_RETELL_API_KEY` | **secret** | the Retell key that owns the agent; the server checks Retell's signature on every tool call with it |
| `ARIA_RETELL_TOOL_TOKEN` | **secret** | the bearer token the agent's tools send (the same one `demo-agent` puts in the tool headers) |
| `ARIA_PUBLIC_ORIGIN` | variable | this backend's own https address, e.g. `https://your-app.fly.dev` (no path) |
| `ARIA_SITE_ORIGINS` | variable | websites allowed to read the calendar and to frame its staff page, comma-separated, https, as the address bar shows them (e.g. the Netlify address) |
| `ARIA_STORE_PATH` | variable | the appointment file, on the volume: `/data/appointments.sqlite3` |
| `ARIA_RETELL_PUBLIC_KEY`, `ARIA_RETELL_AGENT_ID`, `ARIA_RETELL_AGENT_VERSION` | public values | for the website the backend serves; public by design and restricted by Retell to its allowed domains |
| `PORT` / `ARIA_PORT`, `ARIA_HOST` | variable | the host sets `PORT`; the default listen address is `0.0.0.0` |
| `ARIA_SERVE_SITE` | variable | `0` for a backend that only serves the calendar and the tools (the website is elsewhere) |
| `ARIA_AVATAR_IMAGE` | variable | a web-sized avatar file inside the image/volume, for the website the backend serves (see SHOWCASE.md); the likeness is never committed |

A wrong or missing setting stops the server at start with every problem named and what to change (`python -m aria_booking demo-public-serve` with nothing set shows them all).

## Setting it up on Fly.io (one time)

```bash
cd booking
cp deploy/fly.toml.example fly.toml                     # edit the three REPLACE values (app name, public origin, agent id); fly.toml is git-ignored
flyctl launch --no-deploy --copy-config --name <your-app>
flyctl volumes create aria_data --size 1 --region iad    # the persistent volume (one machine only)
flyctl secrets set ARIA_RETELL_API_KEY=<paste> ARIA_RETELL_TOOL_TOKEN=<paste>    # typed by you; they never touch a file
flyctl deploy
flyctl scale count 1                                      # one machine: the appointment file is one SQLite file on one volume
python -m aria_booking demo-public-check --url https://<your-app>.fly.dev --site-origin https://glamour-dayspa-aria-demo.netlify.app
```

(`flyctl`'s options change over time; `flyctl help launch` is authoritative. These commands were not run here: neither `flyctl` nor Docker is available on this machine, so the image has been checked statically and by starting the backend from exactly the files it copies, not by a real build.)

Render: create a web service from `booking/Dockerfile` (see `deploy/render.yaml.example`), add a 1 GB disk at `/data`, and type the two secrets into the dashboard.

## Retell (the same existing test agent: nothing is created)

1. **Public key allowed domains**: in the Retell dashboard add the website's hostname exactly (for the existing demo `glamour-dayspa-aria-demo.netlify.app`: hostname only, no `https://`, no
   port) to the public key's allowed domains, and keep `localhost` for local tests. If a call fails with a domain error the page says so.
2. **Tool addresses**: with `ARIA_DEMO_TUNNEL_URL=https://<your-app>.fly.dev` run the existing `python -m aria_booking demo-agent update --mode lifecycle --confirm-update` (the recorded agent
   only; it verifies the read-back). **The agent's tools point at ONE place at a time**: after this the local tunnel no longer receives calls, so do the local rehearsal first, and move the agent
   back with the same command and the tunnel address if you rehearse locally again.

## Updating the existing Netlify demo, and rolling it back

The existing demo is a Netlify site (`Server: Netlify`). No Netlify project file, CLI state or deploy hook exists in this repository, and GitHub shows no deploy status for it, so it looks like a
**manual upload**. Whoever owns the Netlify account does the upload; nothing here has the account.

1. Build the bundle (the operator command, with the backend's address): 
   `ARIA_BACKEND_URL=https://<your-app>.fly.dev python -m aria_booking demo-agent prepare-web` → the folder printed (default `booking/.local/demo-agent/web`). It points the page at the backend
   and includes a `_headers` file (the microphone policy, cache rules). Add `ARIA_AVATAR_IMAGE=<the web-sized avatar>` if the personal avatar should appear.
2. Check the backend first: `python -m aria_booking demo-public-check --url https://<your-app>.fly.dev --site-origin https://glamour-dayspa-aria-demo.netlify.app` must say **all checks passed**.
3. In Netlify: the site's **Deploys** page → drag the whole folder onto the upload area. Netlify keeps every earlier deploy.
4. Open the live address, press the call icon, and ask for open times: see "What only a call can prove".
5. **Rollback** (instant, no rebuild): Deploys → open the previous (original) deploy → **Publish deploy**. The backend can stay up; the old page does not use it.

The original page (`index.html` and friends at the repository root) is not changed by any of this; it is only replaced on Netlify when you upload, and the replaced deploy stays one click away.

## Is it up? (start, readiness, recovery)

* `GET /healthz` → `{"ok": true}` when the process is up. `GET /readyz` → every check with a plain `fix` for each failure (HTTP 503 until the required ones pass): the appointment file reads and
  writes, the calendar's date is today's date in the spa's time zone, the daily upkeep ran, the secrets are set. It carries counts, dates and origins only.
* From any machine: `python -m aria_booking demo-public-check --url <backend> --site-origin <website>`. It is read-only, signs nothing and prints PASS/FAIL with the fix.

| What you see | Most likely cause | Do this |
|---|---|---|
| the check says `reachable` failed | app not running, wrong address, or not listening on the host's `PORT` | check the host's dashboard/logs; the app logs one line per request and its start-up lines |
| the website says the calendar could not be reached | the website's address is not in `ARIA_SITE_ORIGINS` | add it exactly as the address bar shows it, restart; the check names this |
| a call connects but tools say "I can't check that" | Retell's tool addresses point somewhere else, or the key/token differ from the agent's | `demo-agent update` against this backend; confirm the same `ARIA_RETELL_API_KEY` that owns the agent |
| `readyz` 503 `store_*` | no volume mounted, volume full, wrong path | mount the volume at `/data`; keep `ARIA_STORE_PATH` on it (a restart without a volume starts from an empty file) |
| a domain error on the call | the website's hostname is not on the Retell public key | add it (Retell section above) |
| yesterday's date on the calendar | should not happen (the date follows the clock) | `readyz` shows `spa_date`; restart once and report it |
| a bad deploy | | Netlify: Publish the previous deploy. Fly: `flyctl releases` then redeploy the earlier image. The appointment file is on the volume and is not touched by a deploy |

**Overnight:** the calendar's "today", its coverage window and the scripted days follow the real date in `America/New_York`; once a spa day the server settles the fictional visits whose time has
passed and tops up the fictional upcoming ones (the same rules as a start; a client's own appointment is never touched). A restart changes nothing.

**Copies:** `python -m aria_booking demo-store backup --path <file> --dest <new file>` makes a consistent copy (SQLite's online backup, read-only on the original, never overwrites) and
`demo-store check --path <file>` reports counts and the integrity check. To start the public backend from the **existing synthetic appointments**, make a copy of that file and put it at
`/data/appointments.sqlite3` on the volume before the first start (for Fly: `flyctl ssh sftp shell`); otherwise it starts with a fresh seeded file.

## What only a call can prove

Nothing here measures speech. Offline tests prove the tools, the pages, the date handling and the refusals; they do **not** prove how quickly Aria answers aloud, that the voice sounds natural, or that the
microphone, Retell's allowed domain and the tool addresses are all right on the day. Run one supervised call from the public address, with Shobana present, before the meeting.

## Not done here

No account, host, domain, certificate or payment is created; no agent is created or changed; no text message is sent (there is no SMS provider or owner number); Booksy is not touched; the real
address and phone of any business are not invented (the fictional address and contact stay unset until chosen: see the configuration notes in SHOWCASE.md).
