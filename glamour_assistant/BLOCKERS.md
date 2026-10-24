# Dependencies, decisions and exact blockers

Nothing below has been done by the author of this candidate. Each item says who must do it, why, and what happens if it is not done. The first release needs 1-6; 7 onward are decisions or later phases.

| # | What is needed | Who | Why | If missing |
|---|---|---|---|---|
| 1 | **A Retell response engine (LLM)** holding `agent/prompt.md` (model chosen; see COSTS.md), with only the `end_call` tool | operator | one prompt for both channels, so voice and chat answer from the same reviewed information | nothing can answer |
| 2 | **A Retell VOICE agent** for it (languages en-US, es-419, zh-CN; 5-minute call cap; 20 s silence cut-off) and its **id** | operator | the Talk to Aria button | the page says voice is unavailable and offers the phone |
| 3 | **A Retell CHAT agent** on the same response engine and its **id** (`GLAMOUR_CHAT_AGENT_ID`). A chat agent is a different resource from a voice agent: the demo's voice agent cannot be reused for typed chat | operator | typed chat | typed chat answers with the phone number |
| 4 | **The Retell secret API key as a server-side environment variable** (`RETELL_API_KEY`) on the hosting site | operator | the documented chat API needs the secret key; a browser must never hold it | typed chat is "not configured" |
| 5 | **The hostname of the hosted page added to the Retell PUBLIC key's allowed domains** | operator | browser voice calls are authorised by the public key plus an allow-list of domains | voice fails with a domain error |
| 6 | **The approved portrait file** (`--portrait`), a web-sized image | operator | requested interface; the repository is public, so it is never committed | the neutral placeholder is shown |
| 7 | **A hosting target**: a separate Netlify site for this project (recommended: independent deploys and rollback, no change to the Serenity site), within the Free plan (shared 300-credit pool; no purchase) | operator | isolation and independent reversibility | |
| 8 | **Decision on the booking link.** The salon's "Book Online" page is a Wix Bookings widget; no page of the site mentions Booksy and no Booksy address was found. The assistant sends people to the Book Online page and the phone. If bookings actually go through a Booksy profile, its public URL is needed | owner / Shobana | the assistant must name the correct place to book | it names the Book Online page |
| 9 | **Decision on which number to give for calls.** The site lists (908) 668-8828 (contact page, SMS terms) and (908) 636-8199 with the note "Text us at the number above". The candidate gives (908) 668-8828 for calls and does not call the other a text line unless the owner says so | owner | accuracy | |
| 10 | **Model choice** for voice and chat (gpt-4.1 vs gpt-4.1-mini; COSTS.md); a supervised comparison in Spanish and Mandarin | Shobana / operator | cost vs quality | gpt-4.1 assumed |
| 11 | **Burst concurrency** (observed enabled) and **account balance / auto-recharge**: decide whether to leave them as they are | Shobana / operator | they decide the real cost ceiling (COSTS.md section 3) | exposure stays as observed |
| 12 | **reCAPTCHA v3** (a Google site key, then enable it on the Retell public key and add the token to the page) | owner / operator | slows bots from starting paid calls if the link is shared widely | not included |

## Known unknowns that only a real test can settle

- Whether a voice agent and a chat agent can share one response engine as assumed (Retell's chat agent documentation says "response engine"; not exercised).
- The exact shape of the SDK's transcript event (the page accepts a list of `{role, content}` or a single such object and replaces earlier lines; if the real shape differs the transcript will not
  show, and nothing else breaks).
- Whether chat sessions count toward the 20-call concurrency pool (the page does not say).
- Whether Retell's stock widget (`retell-widget-v2.js`, public keys only, voice and chat in one floating window) would be a smaller integration: it supports a logo but, per its documentation, not a
  portrait beside the conversation, an inline layout or a transcript area of our own, so it was not used. It is the fallback if the custom page proves too costly to finish.
- **A real cap on typed messages needs shared state.** The relay is stateless, so its 20-message counter can be replayed within a token's 30 minutes and is only a cooperative limit; a true atomic per-chat or per-day counter needs a store (new infrastructure, not part of this release). Nothing in the release claims otherwise.
- Typed chat and voice are **separate conversations**; the page says so. Carrying context across is not claimed.

## Phase 2 (explicitly NOT in the first release, nothing is built or enabled)

- Daily refresh of the knowledge: `knowledge/refresh.py --check` already says whether the site changed; scheduling it (for example a free GitHub Actions job that opens a reviewable change) and applying an
  approved change to the Retell response engine are separate steps. A failed refresh keeps the last verified facts.
- Collecting a name, callback number and question with permission; owner SMS (needs an approved sender: 2-3 weeks and fees, COSTS.md); weekly reports. No promise that staff have been notified is made anywhere.
- A second salon: its identity and site are unknown. The code keeps all salon facts in `knowledge/` (the site, the page list and the category names are constants at the top of `refresh.py`); a second
  salon means a second folder of that kind, its own facts, its own prompt and its own Retell resources, never shared answers or reports.
