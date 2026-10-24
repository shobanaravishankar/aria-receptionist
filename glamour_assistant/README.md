# Glamour Day Spa information assistant (candidate)

Status: **a candidate for independent review. Nothing is deployed, nothing is provisioned, no Retell resource exists for it, no call or chat has been made, nothing has been spent.** It is
isolated from the Serenity demo: its own folder, its own configuration, no shared runtime, and it can be deployed and rolled back on its own.

## What it is

One hosted, responsive page for Glamour Day Spa (Warren, NJ): the owner's approved portrait beside "Aria - virtual salon assistant", a conversation area, a text box with **Send**, and a
**Talk to Aria / End call** button. A visitor can type or talk; both answer from the SAME reviewed information, taken from the salon's own website. It is **information only**:

- it answers questions about services, lengths, hours, location and how to book, in English, Spanish or Mandarin where each channel supports it;
- it **cannot book, change, cancel, hold or check availability**, never says anything was booked, and sends people to the salon's Book Online page or its phone number;
- it states **no prices** (the fact sheet holds none), gives no medical advice, and says plainly when something is not stated on the website;
- it has **no calendar tool and none of the demo's tools**; the only tool is `end_call` (voice).

Nothing starts by itself: no microphone, no call and no chat session until the visitor presses Send or Talk to Aria.

## How it works

```
 visitor's browser (site/)  ── typed chat ──>  Netlify Function /api/chat/*  ──(secret key, server side)──>  Retell chat API  ── customer's CHAT agent
        │                                        src/chat.ts: signed session token, limits, no content stored
        └── voice (public key, browser SDK) ──────────────────────────────────────────────────────────────>  Retell web call  ── customer's VOICE agent
                                                                                   both agents use ONE response engine = ONE prompt (agent/prompt.md)
 knowledge/refresh.py  ── reads the salon's public pages (read only) ──> knowledge/facts.json ──> agent/build_prompt.py ──> agent/prompt.md + agent/resources.json
```

| Part | Where | Notes |
|---|---|---|
| The page | `site/` (built into a new folder by `scripts/build_site.py`) | static; relative addresses only, so one build serves a draft and the live address; the approved portrait is passed at build time and never committed |
| Typed chat | `src/chat.ts`, `netlify/functions/chat.mts` | no dependencies; the Retell secret key is a server-side environment variable; sessions are signed, expiring tokens; each message is length-checked; no message is stored or logged. The "20 messages" counter is a cooperative flow limit, NOT a cap (the relay keeps no state: see COSTS.md) |
| Voice | the Retell browser SDK with the PUBLIC key (as the demo does), `site/app.js` | transcript lines are shown in the same conversation area; typed chat and a call are separate conversations and the page says so |
| Knowledge | `knowledge/refresh.py` -> `knowledge/facts.json` | deterministic extraction; page text is data, never obeyed; no prices kept; the last verified facts survive a failed refresh; every page's sha256 is recorded |
| Putting it on the salon site | `EMBED.md` | a link, or an iframe snippet for Fiona; not needed for the first release |
| The one prompt | `agent/build_prompt.py` -> `agent/prompt.md`, `agent/resources.json` | a specification of the resources to create (no key, token or id in it) |

## Run the tests (no network, no Retell, no cost)

```
node --test "test/**/*.test.ts"                                  from glamour_assistant: the chat relay against a fake Retell server (sessions, validation, every provider failure, 5 and 25 simultaneous visitors)
python -m pytest booking/tests/test_glamour_knowledge.py booking/tests/test_glamour_page.py       the fact pipeline, the prompt, the page build, and the page in a headless browser
```

The page tests use a fake relay and a stubbed Retell SDK. They show the page's behaviour; they cannot show how the real SDK's events, real speech or the real relay behave: that is what the one
supervised test is for (TEST_PLAN.md).

## Deploying (the operator's steps; none has been done)

1. Create the customer's Retell resources from `agent/resources.json` and `agent/prompt.md` (BLOCKERS.md lists exactly what and why). 
2. `python glamour_assistant/scripts/build_site.py --out <new folder> --public-key <public key> --voice-agent-id <agent id> --portrait <approved portrait>`.
3. On the Netlify site set `RETELL_API_KEY` (secret), `GLAMOUR_CHAT_AGENT_ID`, optionally `SALON_PHONE`; add the site's hostname to the Retell public key's allowed domains.
4. Draft deploy (no `--prod`), run the checks in TEST_PLAN.md, then the supervised test, then production. Rollback: republish the previous deploy.

## What is NOT proven

Everything on Retell and Netlify: that the chat agent and voice agent can share one response engine, the shape of the SDK's transcript events, voice quality in Spanish and Mandarin, translation of
treatment words in practice, latency, the content-security policy against the real SDK (it ships report-only), and cost in practice. See BLOCKERS.md and COSTS.md.

## Declared missing features (not silently dropped, not described as working)

| Requested | State in this candidate |
|---|---|
| **Daily / periodic refresh of the knowledge** (a requested product requirement) | **Not automatic.** The first candidate's facts were refreshed by hand (`knowledge/refresh.py`, a live run on 2026-10-24; a second live `--check` reproduced identical facts). What exists: the refresh and its change summary, and `.github/workflows/glamour-knowledge-check.yml`, a daily CHECK that only reads the salon's public pages and opens or updates one GitHub issue when something changed or the check failed. That workflow is INERT until it is merged to the repository's default branch, and it never commits, never changes the fact sheet or the prompt, and never touches Retell. **What is still missing: applying an approved change to the live response engine on a schedule**; that stays a manual, reviewed operator step. Both channels will always use the same reviewed version. |
| Owner SMS for unanswered questions | **Deferred by Shobana; not built.** Needs an approved sender (see COSTS.md). No page text or answer implies that staff were notified. |
| Collecting a name, callback number and question | **Not built** (phase 2, with the owner's permission flow and a verified destination). |
| Weekly reports | **Not built** (phase 2). |
| A second salon | **Not started**; its identity is unknown. Facts and reports must stay separate per salon. |
| The booking destination | **Unverified.** The salon's own Book Online page is a Wix Bookings page, not Booksy; the assistant names that page and the phone. The correct Booksy address, if bookings go there, must be supplied and verified before it is mentioned. |
