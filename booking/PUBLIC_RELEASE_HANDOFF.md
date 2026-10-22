# Public release handoff (preparation only)

Nothing here has been run against the public internet: no site was uploaded, no host or domain was created, no agent, service or tunnel was changed, nothing was paid for. It is the order of work,
the exact settings and the checks, so the release can be finished by whoever holds the accounts. It sits on top of `DEPLOY.md` (the full runbook); where they differ, this file is newer.

Release order (decided by the project lead on 2026-10-21): **public first, then the guided walkthrough.** Independent review and public verification still apply.

## 1. Exact source

- Code: commit `e32488ad2628fd457514ce4ba2c79c30f5a1f03d`. It was independently reviewed and applied locally (review archive `66ffd1b`; every later commit is tests or documentation only).
- A deploy checkout is acceptable only if the code is identical to it: `git diff e32488a <your commit> -- booking/aria_booking booking/voice_agent_draft` prints nothing.
- Agent files for the mode in use (`lifecycle-preferences`): `voice_agent_draft/prompt_demo_lifecycle_preferences.md` and `voice_agent_draft/tools_demo_lifecycle_preferences.json` (eleven functions). The personal
  avatar and every secret stay out of Git; nothing in the repository contains either.

## 2. What exists, and what is still missing

| Piece | State |
|---|---|
| Website target | The existing Netlify site, `https://glamour-dayspa-aria-demo.netlify.app/`. No project file, CLI state or deploy hook is in the repository, so it is a **manual upload** by whoever owns the Netlify account (access is being sorted by the reviewer and the project lead). |
| Backend host | **Not chosen. No paid infrastructure is authorised.** The project lead declined recurring Fly.io hosting on 2026-10-21 and asked whether the calendar can live on the existing Netlify site; that is assessed separately in `NETLIFY_ONLY_ASSESSMENT.md` and nothing is decided. Until it is, the backend must still be reachable at a stable `https` address (section 5). |
| Retell | The existing test agent already has the eleven functions, pointing at a local tunnel. Its public key's **allowed domains** must also list `glamour-dayspa-aria-demo.netlify.app` (hostname only). |
| Records | The current synthetic appointment file (fictional clients and appointments, with the preferences tables in the same file). |
| Avatar | The approved modern-headset WebP is private and outside Git. A website bundle that includes it publishes it (it is a page asset): confirm the project lead intends that. |
| Salon number and address | Unset. The page and the widget say so plainly ("contact the salon directly") and invent nothing. |

## 3. Backend settings for `lifecycle-preferences`

Run `python -m aria_booking demo-public-serve` with these (host variables; the two secrets are typed in the host's secret store, never written to a file):

| Name | Value |
|---|---|
| `ARIA_RETELL_API_KEY`, `ARIA_RETELL_TOOL_TOKEN` | secrets: the key that owns the agent, and the bearer token the agent's tools already send |
| `ARIA_PUBLIC_ORIGIN` | the backend's own stable https address, no path |
| `ARIA_SITE_ORIGINS` | `https://glamour-dayspa-aria-demo.netlify.app` (so that site may read the calendar and frame the staff page, and no other) |
| `ARIA_STORE_PATH` | the appointment file on the host's persistent volume |
| `ARIA_PREFERENCES` | `1` (the agent has the three preferred-technician functions; without this their addresses answer "not found") |
| `ARIA_SERVE_SITE` | `0` (the website is on Netlify) |
| `ARIA_SHOWCASE_CONFIG` | leave unset until a fictional number and address are supplied |

A wrong or missing setting stops the backend at start and names every problem.

## 4. Order of work, with a check and a way back for each step

1. **Copy the records.** `python -m aria_booking demo-store backup --path <current store> --dest <new file>` (SQLite's online backup: read-only on the original, never overwrites), then
   `python -m aria_booking demo-store check --path <new file>`. Put the copy at `ARIA_STORE_PATH`. **One writer per file:** never point the public backend and a local server at the same file; from
   the switch on, the public file is the one that grows. *Way back:* the original is untouched.
   *Dry run done (offline, loopback, throwaway copy):* a read-only backup of the current store (15 appointments, 4 clients, integrity ok) opened under this code with preferences on; `/healthz` and
   `/readyz` answered 200 with no failed required check; signed read-only lookups answered; counts unchanged.
2. **Start the backend** with section 3, then `python -m aria_booking demo-public-check --url <backend> --site-origin https://glamour-dayspa-aria-demo.netlify.app` must say **all checks passed**.
3. **Move the agent's tools** (the same recorded agent; nothing is created): `ARIA_DEMO_TUNNEL_URL=<backend> python -m aria_booking demo-agent update --mode lifecycle-preferences --confirm-update`, then
   `demo-agent verify --mode lifecycle-preferences` and `demo-agent preflight --mode lifecycle-preferences --base-url <backend>` (reads and read-backs only; it saves nothing). Set `mode` in the operator
   config to `lifecycle-preferences` so later runs keep matching. The agent's tools point at **one place at a time**: from now on the local tunnel receives no calls. *Way back:* the same update with the
   local tunnel's address.
4. **Retell public key:** add the Netlify hostname to its allowed domains (keep `localhost`).
5. **Build the website bundle into its own folder.** `ARIA_BACKEND_URL=<backend> ARIA_AVATAR_IMAGE=<the approved WebP> python -m aria_booking demo-agent prepare-web --mode lifecycle-preferences --web-dir <new empty folder>`.
   **Always pass `--web-dir`:** the default folder is the page the local rehearsal serves, and a static-host build would overwrite it with one that reads the public backend. The folder includes `_headers`
   (microphone policy, cache rules). Check the folder holds `index.html`, `styles.css`, `voice.js`, `views.js`, `site.js`, `demo-config.js`, `_headers` and `assets/`.
6. **Upload** that folder on the site's Deploys page (Netlify keeps every earlier deploy). *Way back, instant:* Deploys, open the previous deploy, **Publish deploy**.
7. **Verify on the live address:** hard-refresh; the page shows no top bar and one footer note; the call icon, the avatar-led call entries and both calendar views work; `demo-public-check` still passes; one **supervised
   call** from the public address with the project lead present (only a call proves speech, latency and the microphone path).

## 5. A stable https backend: what is needed, nothing chosen

It must have: one https hostname that stays the same across restarts, a valid certificate, reachability from Retell and from browsers, one writer, a persistent file, and to be running during the meeting.

| Option | Notes |
|---|---|
| Fly.io (the recommendation in `DEPLOY.md`; **declined by the project lead**) | needs an account and a card; roughly $2 to $4 a month by third-party summaries (verify on the vendor's page); one machine, one volume; `deploy/fly.toml.example` |
| Render | Starter web service plus a disk; the free tier has no disk and sleeps, which would hurt voice latency; `deploy/render.yaml.example` |
| This PC behind a Cloudflare **named** tunnel | no hosting fee, but needs a domain on Cloudflare DNS (domain cost) and the PC awake and online; unverified here |
| A quick tunnel | **not stable**: the hostname is random and has expired twice already; fine for a local rehearsal only |

The choice, the account and any payment belong to the project lead. Until a stable address exists the only honest public state is the website upload without a working calendar or voice, which is not recommended.

## 6. Not done, not verified

No Docker image was built and no host was used; the dry run above is the only backend start with the real records, and it was a loopback test of a copy. No Netlify access was available to this work. Spoken
latency and the call itself are unverified. No Booksy, no notifications, no automatic paid calls, no monitoring (see `PRODUCTION_BACKLOG.md`).
