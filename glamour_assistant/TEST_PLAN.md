# Acceptance and test plan

Two stages. Stage 1 runs on this machine with **mocked providers** (no network, no Retell, no cost) and is repeatable. Stage 2 is **one short supervised real test**, done only after Stage 1 passes
on the candidate that will be deployed, by people, with a timed stop and a cost estimate (COSTS.md). Stage 2 is the only thing that can show what real speech, the real SDK and the real relay do.

## Stage 1: automated, mocked (what exists, and what each proves)

| Check | Where | Result at delivery |
|---|---|---|
| Five typed visitors at once, each with several messages: every answer goes to its own visitor, in order; five separate chats; nothing leaks across chats | `test/chat.test.ts` | passes (see the delivery report for the exact count) |
| Twenty-five typed visitors at once (five times the requirement) | same | passes |
| A flood from one visitor is cut off by the per-visitor limit and does not block or mix up the others | same | passes |
| Unique per-session authorization: forged, edited, expired, other-key and malformed session tokens are refused; refused requests never reach the provider | same | passes |
| Replay of a session token: **documented, not prevented.** The relay keeps no state, so an old token is accepted again within its 30 minutes and the "20 messages" counter is a cooperative flow limit, not a cap (shown by tests that replay a token past the mark and send two requests with the same count) | `test/chat.test.ts` | the limitation is asserted, not hidden |
| Duplicate submission: a repeated Enter or click, or a second question sent while one is on its way, sends nothing and loses nothing typed | `booking/tests/test_glamour_page.py` | passes |
| Retry behaviour: a failed answer keeps what was typed and works on the next press; an expired chat is replaced and the question answered; a full chat opens a fresh one | same | passes |
| A chat connection that never answers (to the relay, or from the relay): the page gives up at its own deadline, restores Send, keeps the question, says truthfully that it cannot tell whether the answer was lost, and never sends again by itself | `test_glamour_page.py` | passes |
| Voice transcript: only spoken lines (user, agent) are shown; tool results, injected notes and node transitions are never shown as speech; updates keep their place | same | passes |
| Provider-full / rate-limit / payment / server error / malformed reply / no answer at all (timeout): each becomes a short answer with the phone number, no provider text, no key, no id; simulated, never by opening real calls | `test/chat.test.ts`, page tests | passes |
| Five voice sessions at once (five pages, five stub calls started together): one call each, no second call from a repeated click, a late event from an older call cannot touch a newer one, transcript lines stay in their own page | `test_glamour_page.py` | passes |
| Mixed: five pages each with a typed question in flight AND a voice call started | same | passes |
| Nothing starts on load: no chat session, no SDK load, no call, no microphone | same | passes |
| Output is text only (no HTML injection); only the salon's own address and phone become links | same | passes |
| Phone layout: header and portrait above the conversation, no sideways scroll; computer layout: portrait beside the label | same | passes |
| Secrets stay server side: the relay's responses and logs never contain the key; the page and its settings hold only the public key and the voice agent id | `test/chat.test.ts`, `test_glamour_knowledge.py` | passes |
| Knowledge: no prices, the salon's own contact facts, every service present, page text treated as data, a failed or inconsistent refresh keeps the last verified facts | `test_glamour_knowledge.py` | passes |
| Actual public endpoint response times and cold start | **not yet possible**: needs the draft deploy | pending; to be measured with the smoke script on the draft |

What Stage 1 cannot show: the real SDK's event shapes (especially the transcript hook), real speech recognition, Spanish and Mandarin quality, latency, the content-security policy against the real SDK,
whether chats count toward the workspace concurrency pool, or any behaviour of Retell when its quota is full.

## Stage 2: one supervised five-device test (after Stage 1 passes on the deployed candidate)

Prerequisites: the customer-specific Retell resources exist (BLOCKERS.md), the draft is deployed, the public key allows the draft's hostname, burst is not relied on, and the operator has
read the cost estimate in COSTS.md (about $1.15 for voice and $0.23 for text at the planning rates, $2.76 with one retry).

1. Five people, five devices (two phones, three computers), headphones, the draft address, a person timing. A **hard stop at 2 minutes per call**; the operator ends any call still running.
2. Each person asks distinct fictional questions (for example: opening hours; whether a prenatal massage is offered; where the salon is; one question in Spanish; one in Mandarin; one the website does not answer).
   Then each types two questions. One person switches from voice to typing and back.
3. Record, per device: time to connected, connected count (should reach 5 at once), time to first spoken answer, answers received, errors, disconnections, whether the transcript appears in the conversation
   area, and whether any person sees another person's words (it must not). Keep screenshots of the page, not recordings of people.
4. Pass: all five connect, no cross-visitor content, every unknown fact is said to be unknown with the phone number, nothing is said about booking being done, no price is spoken, calls stop at the limit.
5. Afterwards read the Retell usage for those calls and compare with the estimate; stop and report if it is higher.

No retry of an unchanged candidate; any fix is a new immutable commit and a new, smaller test.
