# Client-history lookup — offline design (NOT built into the demo)

Request (user, relayed by Sol, 2026-10-09): Aria asks for the caller's **complete phone number**, finds the matching client record, and
answers questions such as *"when was my last lash appointment?"*. It must work for any service category (lashes, massage, facials, …), not a
lash-only path. This document and `aria_booking/history/` are a **separate, offline increment**. Nothing here is attached to the voice
endpoint, the Retell tool files or the availability-only demo, and **no adapter for the real Booksy client screens exists**.

## Policy (supervised demo only)

- The caller gives the **complete phone number**. It is normalised without guessing digits (`history/phone.py`): a full ten-digit US number
  (or eleven starting with 1) or an explicit `+` country number; seven digits, nine digits, letters or an extension are "not complete" and
  Aria asks again. A name is **not required** (many Booksy records have none). It is used only if several records share the number and the
  full name singles out exactly one; a record with no name can never be singled out by a name.
- A phone match **locates a record; it is not proof of who is calling**. No one-time code is part of this scope. The user set this as the
  current supervised-demo policy, not a production authentication approval. Risk to keep in view: anyone who knows a client's number could
  learn the date of that client's last appointment. That is acceptable only with agreed test records; production would need real
  verification.
- Use **agreed demo records only**. No customer record is opened, copied or exported for this work, and none belongs in the repo or in shared
  messages.

## Behaviour (`history/lookup.py`, tested offline with fictional records)

| situation | reply status | notes |
|---|---|---|
| category missing, ambiguous ("lashes and a massage") or not on the menu | `needs_clarification` | category words come from the website catalogue plus a small explicit alias table |
| phone missing | `needs_clarification` | asks for the complete number incl. area code |
| phone incomplete | `invalid_phone` | never completed by guessing; does not use the per-call allowance; never echoes digits |
| no record for the number | `not_found` | no invented history |
| several records share the number, no name | `needs_clarification` | does not say how many or who; nothing is read yet |
| several records, a full name singles out one | proceeds | only that client's history is read |
| several records, name matches none or more than one | `unable_to_confirm` | `record_ambiguous` |
| source unreachable / history unreadable / unexpected error | `unable_to_confirm` | no internal text leaks |
| source cannot tell completed from cancelled/no-show | `unable_to_confirm` | `completion_unavailable`: the limitation is stated |
| history cut short (pagination/loading) | `unable_to_confirm` | unless the order is verified newest-first **and** a match was found |
| a newer relevant visit has unknown status, unreadable date, "completed" in the future, or a service that cannot be placed | `unable_to_confirm` | uncertainty newer than the best match blocks it; older uncertainty does not |
| fully read, none completed in that category | `no_completed_visit` | only when nothing was uncertain or missing |
| found | `found` | date, service, and the technician **only if asked** |

Rules that hold throughout:
- **Latest completed** means status *completed*, dated in the past, in the requested category. Cancelled, no-show and upcoming visits are
  excluded; **a past date alone proves nothing**; any other or unrecognised status is *unknown*.
- Answers are limited to the requested visit's date, service and (if asked) technician. Never notes, medical, payment or other-client data;
  never the phone number, a name, a record handle or a count of matching records.
- A caller gets **3 lookups per call** (incomplete numbers do not count), so numbers cannot be tried one after another.
- When the number matches several records, **no record's history is read** until one is singled out.

## What is NOT known — evidence required before a real adapter

Official documentation (Booksy support article 25167278511250) confirms the path *Clients → client profile → Appointments*, and the user
reports **Upcoming** and **Past** tabs. That is user-observed layout, **not** a captured contract. Before any adapter is written, one bounded,
read-only look (Sol's session, **an agreed demo client only**, no edits, no repeated hits) must record, with names/phones/notes/ids removed
and only structure and label vocabulary kept:

1. **Finding a client by phone:** is there a search box; does it search phone numbers; how does it treat formatting (`(555) 234-5678` vs
   `+1555…`); what the results list looks like for 0, 1 and several matches; whether merged/deleted clients appear; how a client with **no name**
   is displayed; the stable client id and the profile URL shape.
2. **Reading what is stored:** how the phone is displayed (so stored and spoken numbers normalise identically), whether a client can hold
   several numbers, and whether two clients can share one.
3. **Appointments → Upcoming / Past tabs:** the container and row selectors; per row the date and time, service name(s) (and how a
   multi-service appointment is shown), staff name, and the **status label vocabulary** (completed / paid / cancelled / no-show / confirmed-but-past
   / anything else). Whether a *past, unpaid, never-closed* visit looks different from a completed one. If completion cannot be told from the
   screen, the feature must stay at `completion_unavailable`.
4. **Ordering, pagination and totals:** newest-first or not; infinite scroll or pages; a total count to reconcile against (as with the Services
   list); whether lazy rendering shows stale counts right after a scroll.
5. **Failure modes:** how an empty history, a signed-out session and a still-loading page look, so each maps to *unable to confirm* and never to
   *no appointments*.
6. **Privacy of the capture itself:** the evidence report must keep attribute and label names only; customer names, phone numbers, notes and
   appointment ids are masked in the page script before anything is stored (as the existing capture already does for the calendar).

Also still open (not mine to decide): whether reading client records through the web UI is within Booksy's terms for this use, and whether
the salon is comfortable with the phone-number-only policy beyond the supervised demo.

## How it would be attached later (after the evidence is reviewed — not now)

1. A real `HistorySource` adapter built to the captured structure, with the same fail-closed reader rules as the calendar (anything not
   understood → unknown), a hard deadline and the same request-budget idea as the availability path.
2. One new read-only function (`last_completed_visit`) added to the tool file and the prompt as a **separate, reviewed change**, with the
   one-sentence waiting acknowledgement and "I could not confirm that" wording; never a transfer or callback promise.
3. Added to the endpoint's read-only route set only then; until then a test pins that it is not a route, not in any Retell tool file and not
   imported by anything that serves the demo.
