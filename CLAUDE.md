# ARIA — Claude working instructions

This repository is the shared working channel for ARIA development.

## Current priority
Continue **GitHub Issue #3: "Finish Retell↔Booksy staff availability and fix latency"** from the current `main` branch.

Do not redesign the work from scratch. Review the merged Booksy prototype and continue from it.

## Required outcomes
1. Connect Retell to the existing Booksy availability capability.
2. Support named-staff and multi-staff questions such as:
   - "Is Amy available at 4?"
   - "Who is available at 4?"
   - "If Amy is busy, what is her next opening?"
   - "If Amy is busy, who else is available?"
3. Fix performance/latency. The current user experience has had pauses of roughly 30–40 seconds, which is unacceptable for a receptionist.
4. Preserve fail-closed behavior: if Booksy cannot be read confidently, never invent availability.
5. Keep live real-customer booking disabled until read-only voice availability is reliable.
6. Carry forward the September tester fixes: remove filler words, never guess, honor closing time, no medical advice, immediate human transfer when requested, and complete/correct service information.

## Coordination protocol
- Treat GitHub Issue #3 as the task specification and progress log.
- Post a concise status comment on Issue #3 when you start, when you find a blocker, and when the implementation is ready for review.
- Commit all code/tests/docs to this repository.
- Update `booking/README.md` with what works end-to-end, measured latency, remaining limitations, and manual test steps for Shobana.
- Do not put passwords, session cookies, or other secrets in GitHub.
- Do not make live real-customer bookings while implementing or testing this task.

## Review loop
Sol/ChatGPT will review the commits and test evidence after each implementation round and will identify gaps or regressions. Continue iterating against those review findings until Issue #3 acceptance criteria are met.
