# Preferred technicians (demo only, opt-in)

A returning client can ask Aria to remember **who they would like for one service** (for example "make Rowan my usual for Swedish Massage"). It lives only in the
simulated appointment file, for invented clients. It is **off** until the reviewed prompt and tools are put on the test agent *and* the server is started with the switch.

## What it is, and what it is not

- One preferred technician per **client** and per **service** (the menu's family, e.g. Swedish Massage: every length of it). A whole group such as "massage" is never saved:
  Aria asks which one.
- Kept in the same SQLite file as the appointments, in three new tables created the first time the switch is used (`client_preferences`, `preference_requests`,
  `preference_events`). The schema version does **not** change, so a build that knows nothing about preferences still opens the file and ignores them. No appointment or client row is
  read or changed by the preference code.
- Keyed by the client's stable id, the service family's id and the technician's stable staff id. Two clients who share a household number (Willa and Ansel Hartwell) never share a
  preference.
- **Nothing is inferred.** Booking, cancelling and moving never create, change or remove a preference; a one-off choice of technician, or a past visit, does not either. A row exists
  only because the caller asked for it and said yes to the read-back.
- **A preference is only who, never when.** It never guarantees a time. When a client who has one is booking and did not name a technician, Aria offers that technician first
  and checks their real hours, appointments, the full length and cleanup like any other request. If they are not free she says why, offers the alternatives the tool returned and leaves
  the saved preference alone.
- It is not shown on the calendar page, and it is a demo record match (a phone number and, when it is shared, a full name), not production authentication.

## The three functions

| function | does | confirmation |
| --- | --- | --- |
| `get_preferences` | lists what is saved (or one service's) | none: it only looks |
| `save_preference` | saves or changes the technician for one service | read-back, a clear yes, then the token with `confirmed: true` |
| `remove_preference` | removes the technician for one service | read-back, a clear yes, then the token with `confirmed: true` |

Rules the code enforces (the prompt repeats them, but nothing depends on the prompt alone): the service must be exactly one family the calendar can book; the technician must be someone
who does it (an unknown or ineligible name is asked about, with the real list); a confirmed request whose details changed since the read-back gets a fresh read-back; a repeated confirmed
request changes nothing more; success is reported only after the change is read back from the file; a file that fails is reported as failed, never as saved; at most three changes a call.

## Turning it on (reviewed first; the operator does these, in this order)

1. The files are already generated and committed: `voice_agent_draft/prompt_demo_lifecycle_preferences.md` and `tools_demo_lifecycle_preferences.json` (`python -m aria_booking.catalog.render --write`
   rebuilds them; a test fails if they differ). The lifecycle prompt and tool file are unchanged.
2. Start the server with the switch: `python -m aria_booking demo-serve --lifecycle --preferences` (the hosted backend: `ARIA_PREFERENCES=1`). Without it the three addresses do not exist.
3. Move the existing test agent to the mode in place (no new agent): `python -m aria_booking demo-agent update --mode lifecycle-preferences --confirm-update`, then
   `demo-agent verify --mode lifecycle-preferences` and `demo-agent preflight --mode lifecycle-preferences` (the preflight only reads and reads back; it saves nothing).
   `prepare-web --mode lifecycle-preferences` writes the supervised test steps (P1 to P7) next to the other instructions.

## Rolling back

Update the agent back (`demo-agent update --mode lifecycle --confirm-update`), then restart the server without `--preferences` (or without `ARIA_PREFERENCES`). Saved preferences stay in the
file, unused, and come back if the switch is turned on again. Do the agent first: an agent that still has the three functions would call addresses the server no longer has.
