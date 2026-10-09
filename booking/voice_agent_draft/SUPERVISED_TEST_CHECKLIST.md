# Supervised voice-to-Booksy test: handover checklist

Status words in this file are exact: **Authorized** means someone with the right to decide said it may be done;
**Verified** means it was actually done and observed. Nothing below is **Verified** until its box is ticked by whoever
watched it happen. Permission is not verification.

## What was reviewed

| | |
|---|---|
| Branch | `feature/retell-booking-functions` |
| Reviewed product commit | `f5f531263a0d162840362678d3101346be827e4a` (`f5f5312`) |
| Independent review | Sol, V1-V3 re-review PASS at that exact commit |
| Offline tests at that commit | 525 passed, 3 live tests deselected (repository tests + Sol's original 8 Booksy probes + 6 voice probes) |
| Claude's own run | 511 passed, 3 live deselected; mutation checks 13/13 caught on the V1-V3 logic |
| Commits after `f5f5312` | documentation and one documentation test only (this folder, a README row, `tests/test_voice_draft_config.py`). `git diff f5f5312..HEAD -- booking/aria_booking` is empty. |

## What the review does and does not cover

Covered: the offline voice logic, the signed-request boundary, the `serve` gating, the honesty rules in the spoken text.
**Not covered, not yet verified live:** Retell configuration, signing of *custom-function* calls by Retell, real
function timeouts, behaviour under an actual call, the owner alert, the one-minute hold cutoff. This is not production
approval and not unattended approval.

## Roles (do not overlap)

- **Sol** owns the live setup: the read-only server, the temporary authenticated HTTPS tunnel, and the separate test agent.
- **Shobana** is present for every live step and owns every decision about her accounts and money.
- **Claude** prepares documents and the pull request only. Claude does **not** start a browser, server or tunnel and does
  not change Retell while Sol is setting up. One browser profile can be driven by one process at a time.
- The Retell API key (with the webhook badge) is already stored by Sol in ignored local setup. **Do not ask for it again,
  print it, paste it into chat, or put it in the repository, the website or this folder.**

## Stages

Tick a box only after observing the result. Stop at the first failure and say so in the shared channel.

### Stage 0: before anything is started
- [ ] The working tree is on `feature/retell-booking-functions`, the reviewed product code is `f5f5312` (`git diff f5f5312..HEAD -- booking/aria_booking` is empty), and `python -m pytest booking` passes.
- [ ] No other server, tunnel or automated browser is running on this PC (the browser profile is locked by whichever has it).
- [ ] The original spa agent ("Glamour Day Spa Receptionist", Single Prompt, V0/V1) is untouched and not pointed at the test functions.

### Stage 1: read-only server and tunnel
- [ ] Start the server READ-ONLY: `python -m aria_booking serve --confirm-business-id <id>` with `ARIA_LIVE_BOOKSY=1` and the key supplied from Sol's ignored local setup. No `--approve-*` flags. The console says `READ-ONLY (booking refused)` and `127.0.0.1` only.
- [ ] A temporary, authenticated HTTPS tunnel forwards to that port. It is started by Sol with Shobana present and stopped when the test ends.
- [ ] A separate test-only Retell agent (or unpublished draft) uses `prompt_test_only.md` and the three functions from `tools.json`, with the tunnel host and the bearer token (set in the Retell dashboard, not in any file). The public website agent is not changed.

### Stage 2: one spoken availability query (read-only)
Ask something like: "Do you have anything Monday at 11?"
- [ ] The server log shows a **signed request accepted** for `check_slot` (a status line, not `rejected ... bad or missing signature`). A **401** means the signing scheme for custom-function calls differs from the documented webhook scheme: **stop and inspect one real request's header format; do not loosen the check.**
- [ ] The spoken answer matches what the calendar shows (open, or why not, with real alternatives). It never states availability before the tool answers.
- [ ] Timing noted: how long the caller waited for `check_slot`. If it approaches the function timeout (90 s in the draft), raise the timeout before booking.
- [ ] Ask an after-hours or past-closing request; the explanation and the latest valid start are correct.
- [ ] No appointment was created. The ledger is unchanged.

### Stage 3: the one explicitly confirmed fictional booking (only after Stage 2 is fully ticked)
- [ ] Restart the server with all five booking approvals (`--approve-save --approve-note-typing --approve-tour-popups --approve-not-now --approve-note-readback`). The console says `BOOKING ENABLED`.
- [ ] The caller chooses one offered option and hears the read-back sentence, then says a clear yes.
- [ ] Exactly **one** appointment is created, in the test business only, with the ARIA TEST note.
- [ ] The agent says it is booked **only** after the tool returned `booked_verified` (or `already_booked`) with `ok: true`.
- [ ] `python -m aria_booking verify --start "<YYYY-MM-DD HH:MM>" --confirm-business-id <id> --approve-tour-popups --approve-note-readback` (server stopped first) finds it by reference. Ledger: `verified`, `observed`.
- [ ] Stop the server and the tunnel. Cancel the test appointment by hand in Booksy. Sign out and remove the local browser profile when finished.

## Stop conditions

Stop and report in the shared channel, do not work around, if: any request is rejected with 401; any tool answers `unknown`,
`needs_review`, `system_unavailable` or `error` when the calendar looked fine; the agent speaks a time the tool did not
return, a spa hour or price, or a promise of a callback, text or alert; two servers or browsers would run at once; the
call runs long (the account balance was reported as $1.42 and no top-up is authorized); the live Booksy page looks different
from what the reader expects.

## Authorization versus verification (current status)

| Step | Authorized | Verified |
|---|---|---|
| Open the pull request for the reviewed voice branch (unmerged) | per Sol's direction `aria-retell-supervised-setup-direction-20261008-01` | PR opened by Claude; merge not authorized |
| Start the reviewed server read-only | per Sol's direction, Shobana present | not yet |
| Temporary authenticated HTTPS tunnel | per Sol's direction | not yet |
| Separate test-only Retell agent | per Sol's direction | not yet |
| Spoken read-only availability query | per Sol's direction | not yet |
| Signing of custom-function calls accepted | n/a | **not yet; the main open question** |
| Function timings acceptable | n/a | not yet |
| One confirmed fictional booking | per Sol's direction, only after the read-only path works | not yet |
| Owner SMS alert | not authorized to send; mock only | not built or verified |
| One-minute hold cutoff | pending | not built or verified |

An optional metadata-only helper that records the verification outcome and timing of a request (never the key, the
signature or the body) may be proposed in the shared channel before any product change, only if the existing console
output cannot establish those two facts.
