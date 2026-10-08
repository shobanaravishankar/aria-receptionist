"""Voice-agent (Retell) integration for the Booksy test-account prototype.

Nothing in this package opens a network port by itself or talks to Retell. ``tools`` holds the narrow, honest
functions a voice agent may call; ``retell_http`` verifies and routes Retell's signed requests to them.
"""
