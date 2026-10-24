# Costs and usage accounting (planning figures, not invoices)

Sources, read on 2026-10-24: Retell pricing page (https://www.retellai.com/pricing), the concurrency page (https://docs.retellai.com/deploy/concurrency), the SMS page, and the owner's
account read-back via the operator (standard concurrency 20, burst enabled to 60, nothing purchased). **Nothing in this folder has spent anything.** Every figure below is a listed rate
applied to a stated assumption; the real invoice depends on the configuration actually chosen.

## 1. Recurring, by channel (the owner would bear these)

| Channel | Listed rate | Basis | Planning figure |
|---|---|---|---|
| Voice (web call), gpt-4.1 | $0.055 infra + $0.015 platform voice + $0.045 model = **$0.115 / connected minute** | the operator's reading of the list; the Cimo voice's own rate is NOT STATED on the page; the page does not give a separate web-call rate; billing covers the whole call including silence | $0.115/min |
| Voice, gpt-4.1-mini | $0.055 + $0.015 + $0.0128 = **$0.083 / min** | same basis | $0.083/min |
| Typed chat, gpt-4.1 | **$0.015 per AI message** | per AI message, not per token; a visitor's message is not billed | $0.015/msg |
| Typed chat, gpt-4.1-mini | **$0.004 per AI message** | | $0.004/msg |
| Hosting (Netlify Free) | 300 credits/month, a hard stop that pauses the team's sites | chat relay requests are tiny: a web request is 2 credits per 10,000 and compute is negligible | **$0 added** |
| Owner SMS (phase 2, NOT built) | $20/month per number + $0.01/SMS, plus one-time brand ($4 low-volume or $45 standard) and campaign ($15) fees; approval usually 2-3 weeks | from the SMS page; the shared-number option is limited to preset templates during PHONE calls and is not a verified answer for website alerts | **not part of the first release** |

Worked examples (planning only): 100 two-minute website calls = 200 min = **$23.00** (gpt-4.1) or **$16.60** (mini). 300 typed conversations of 4 AI answers = 1,200 messages = **$18.00** (gpt-4.1) or
**$4.80** (mini). Voice dominates: a typed conversation of four answers (about $0.06) costs about a quarter of a two-minute call ($0.23).

## 2. The five-client supervised test (only after the automated checks pass; see TEST_PLAN.md)

| Item | Assumption | Cost |
|---|---|---|
| 5 voice sessions | each stopped at a **timed 2-minute limit** (of which about 1 minute is conversation); billed for the whole call: worst case 5 x 2 min = 10 min | 10 x $0.115 = **$1.15** (gpt-4.1) |
| 5 typed sessions | 3 questions each = 15 AI messages | 15 x $0.015 = **$0.23** |
| One retry round if something fails | repeat of the above, only after a fix | +$1.38 (stated up front, not hidden) |
| **Total, worst case with one retry** | | **about $2.76** |

Nothing is rerun when unchanged. Burst capacity is NOT used and the quota is NOT raised (five calls fit the 20 standard).

## 3. Warnings versus enforceable limits

| Control | What it is | Enforceable? |
|---|---|---|
| Call length: `max_call_duration_ms` = 5 minutes, end after 20 s of silence (agent configuration) | caps one call | **yes**, by Retell, per call |
| Typed chat: 20 messages per chat, 500 characters per message, 30-minute chat lifetime, at most 6 chat starts and 40 messages per visitor in any 10 minutes (per warm instance) | caps one chat and one visitor on one instance | chat length and size: **yes**. Per-visitor rates: **best effort only** (kept in memory per instance, not shared) |
| Total spend per day or month | the relay has no database and does not count | **no.** The only hard ceiling Retell offers that I could verify is the account balance: if the owner's workspace runs on a prepaid balance with auto-recharge OFF, an empty balance stops all calls and chats. The operator should verify this setting; I could not |
| Dashboard usage alerts / the operator's weekly read of the usage | a warning | **warning only** |
| Burst concurrency (observed ENABLED at 60; calls above 20 would carry a $0.10/min surcharge) | would allow more simultaneous calls at an extra price | it **raises** exposure; the operator should decide whether to turn it off for this release. I changed nothing |
| reCAPTCHA v3 on the Retell public key | slows bots from starting calls | optional, not included (needs a Google key); worth adding if the link is shared widely |

Because website voice calls start in the browser with a public key, **no server of ours sits in front of a voice call**: a person who finds the page can start calls up to the quota and the call-length cap.
That is the main cost exposure; the mitigations are the 5-minute cap, the domain allow-list on the public key, reCAPTCHA if needed, a short balance, and watching the dashboard.

## 4. Usage accounting

The relay logs only provider status codes (no content, no id). Usage is read from the Retell dashboard / API (calls: minutes and cost per call; chats: `chat_cost`). A weekly read of those totals, by channel,
is the accounting; a script for it is phase 2 together with the weekly report.
