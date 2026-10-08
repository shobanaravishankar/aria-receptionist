# Retell voice-agent draft (NOT deployed)

Reviewable drafts only. Nothing here has been imported into Retell, nothing is reachable from the internet, and no
Retell account, agent or key has been touched.

| File | What it is |
|---|---|
| `tools.json` | The three custom functions (`check_slot`, `find_alternatives`, `book_slot`): descriptions, parameters, timeouts, no retries. Placeholders for the tunnel host and bearer token. |
| `prompt_test_only.md` | A standalone TEST-ONLY prompt for a separate test agent / unpublished draft. It carries no spa hours, prices or menu, so the real-spa demo information cannot override calendar results. The existing public agent and its prompt are not touched. |

## How the pieces fit

```
caller -> Retell agent -> POST https://<tunnel>/tools/<name>   (signed by Retell: X-Retell-Signature)
                                |
                  python -m aria_booking serve      binds 127.0.0.1 only, read-only unless all five booking approvals are given
                                |
             VoiceTools -> BookingService + ledger -> the one dedicated, signed-in browser (one request at a time)
```

## Start it (only when Shobana is present and has started her own tunnel)

```
set ARIA_BOOKSY_BUSINESS_ID=<id>        (never committed)
set ARIA_LIVE_BOOKSY=1
set ARIA_RETELL_API_KEY=<Retell API key with the webhook badge>     (environment only; never in chat, the repo or the website)
set ARIA_RETELL_TOOL_TOKEN=<optional extra bearer secret>
python -m aria_booking serve --confirm-business-id <id>                       # READ-ONLY: checking and alternatives only
python -m aria_booking serve --confirm-business-id <id> --approve-save --approve-note-typing --approve-tour-popups --approve-not-now --approve-note-readback
                                                                              # booking ENABLED (a real fictional ARIA TEST appointment)
```

## What was inspected and decided

- The only agent in the account is **Glamour Day Spa Receptionist**: type **Single Prompt**, GPT 4.1, voice Cimo, listed at about $0.132/min.
  Its draft V1 derives from published V0 "Aria Basic Spa Receptionist": a public spa-information demo with its own hours (9:30 AM-8 PM) and
  a multi-service menu that are **not** the Booksy test calendar. It must be preserved. Booking is therefore prepared as a separate
  test-only prompt plus three functions, never as an edit to V0.
- **Signatures** follow Retell's official Python SDK exactly: `X-Retell-Signature: v=<unix ms>,d=<64 lowercase hex>`, HMAC-SHA256 with the API key over the
  raw body with the timestamp appended, 5-minute window. There is no plain-body or "auto" fallback. **Remaining uncertainty:** the SDK and the webhook page
  do not state that *custom-function* calls use this same scheme; the function pages only say "HMAC-SHA256 of the request body". If a real call is rejected the server
  fails closed (401). The fix is then to inspect one real signed request, not to loosen the check.
- **Cost:** the account balance was reported as $1.42 and no top-up is authorized. Offline tests cost nothing. A live test call is billed per minute, so
  plan very short supervised calls only after review.

## Open items before anything is connected

1. Hosting/tunnel decision (Shobana). No tunnel, public endpoint, credential or live Retell change is authorized yet.
2. A dedicated test agent or unpublished draft for `prompt_test_only.md`, and where its three functions are added.
3. Real timings of a live read and a live booking, to set the function timeouts (the values in `tools.json` are estimates).
4. Sol's independent review of this branch.
