"""Client-history lookup ("when was my last lash appointment?"): an OFFLINE design with synthetic sources only.

NOT wired into the voice endpoint, the Retell tool files or the demo. No adapter for the real Booksy client screens exists, because their
structure has not been captured (see ../HISTORY_DESIGN.md for the evidence required first). Nothing in this package reads Booksy.

Policy (the supervised-demo policy set by the user, relayed 2026-10-09): the caller gives the COMPLETE phone number; the number, normalised
without guessing digits, is matched to exactly one approved demo client record. That locates a record; it is not proof of identity, and
no one-time code is involved. Anything missing, ambiguous or unreadable is "I can't confirm", never a guess.
"""
