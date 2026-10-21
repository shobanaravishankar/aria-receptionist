# Production backlog (recorded, NOT built, NOT active)

Nothing in this file is implemented or switched on. It records what the project lead asked for so it is not lost; it is picked up only for go-live preparation, after the rehearsal and
the review. AR1 and AR2 remain deferred (not waived) and are tracked in the review thread, not here.

## PB-1: Outage and low-credit detection, alerts, recovery and bounded remediation for the voice service

Requested by Shobana on 2026-10-21. Priority: go-live preparation. It must not hold the demo.

**What is wanted**

1. Proactive detection that the voice service is unavailable or about to be (credit or balance low or exhausted, a refused key or domain, the service or its tools unreachable).
2. An alert to an **explicitly configured** owner or support destination (an SMS number or a support address), with **incident deduplication** (one incident, not one message per failed
   visitor) and a **recovery notification** when it clears.
3. **Bounded, authorised remediation** only (below).

**Constraints already decided**

- A visitor's own microphone problem is **not** an incident. A blocked microphone is a per-visitor condition; only service-wide signals (health checks, the provider's own failure
  reasons, repeated failures across callers) may open one. The widget (V-1) already keeps the two apart for the visitor; it must not be the detector, because a visitor's browser is not a
  reliable or private signal. A detector reads server-side signals.
- **Never claim staff were notified without a successful delivery.** Alerts go through the existing outbox, whose rows record whether a delivery happened; nothing may say "notified"
  from an attempt alone.
- **Prefer the provider's native auto-recharge** (a threshold and a target amount) and its monthly budget setting where available, as described in the provider's official billing
  documentation, over any browser-driven purchase automation. Confirm the exact options against that documentation when this is picked up (this note does not rely on them).
- A **payment failure or a reached monthly cap is escalated to a person**. Remediation never raises a cap, adds a payment method, buys credit or changes billing by itself.
- A remote helper (an assistant that can look at the service from outside) may be used for diagnosis within these limits; it is **not** authorised to pay, to change billing, or to run
  unattended without limits.
- No visitor-facing text changes: they keep the calm message (and the salon's number when configured).

**Not configured, and not to be guessed**

The recipient(s) and channel, the sender identity, the monitoring host, retry and escalation limits (how often, how many, who next), the credit thresholds and the budget, and who may
authorise a remediation step. None exists today; the SMS destination and provider for owner alerts are also still unset.

**What it must do when built (acceptance sketch)**

- Silent until a destination is configured; a dry-run mode that shows what it would send and sends nothing.
- One incident per cause with a stable key; at most one alert per incident per window, an escalation only after a stated time, one recovery message.
- Every alert and its delivery result recorded; a failed delivery is shown as failed and retried within the stated limit, never reported as sent.
- Distinct causes named plainly: balance, key or domain, network or provider, tools unreachable, a visitor-side microphone refusal (never an incident).
- Tests: deduplication, recovery, no message without a destination, no claim without a delivery, no remediation beyond the stated bounds, no secret or visitor detail in any alert.

**Reuse**

The outbox, dispatcher and owner-destination checks already exist for appointment alerts (`aria_booking/appointments/`); the public backend's `/readyz` and `demo-public-check`
(`aria_booking/hosting/`) already report plain-language health from outside. This item should extend those, not add a second mechanism.
