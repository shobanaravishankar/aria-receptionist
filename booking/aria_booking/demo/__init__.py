"""The DEMO calendar: simulated availability that looks like a Booksy day view and answers the same voice tools.

Everything here is invented. Staff NAMES are labels observed on the real account's calendar (used as reference, as the project owner asked) but
their ids are synthetic; every client is fictional; nothing is read from, or sent to, Booksy. One dataset (``dataset.py``) drives BOTH the page
(``page.py``) and the voice answers (``driver.py``), and a test proves the page, read with the real capture script and parser, equals the answers.

The demo can never be mistaken for the real thing: the page carries a persistent "Demo calendar - simulated availability" banner, every tool reply
carries ``source: demo_calendar``, and ``demo-serve`` can only construct this dataset, never a Booksy browser (and ``serve`` can never use it). The
conversation itself does not announce "demo" or "simulated" unprompted; asked whether the schedule is real, the agent answers truthfully.
"""

DEMO_BUSINESS_ID = "9000001"  # synthetic: used only in the demo page's address
DEMO_LABEL = "Demo calendar - simulated availability"
SALON_NAME = "Serenity Spa"  # the name Aria SPEAKS (greeting and prompt) in the prototype; the website knowledge underneath is unchanged
SPA_TIMEZONE = "America/New_York"  # the spa's own local time: the session clock, the greeting's time of day and the checks all use this one zone

# The greeting's time of day, from the spa's local hour AT THE START OF EACH CALL (Retell fills the hour in per call): [0, 12) morning, [12, 17) afternoon, [17, 24) evening.
MORNING_UNTIL, AFTERNOON_UNTIL = 12, 17


def greeting_word(hour: float) -> str:
    """The word Aria opens with for a local hour (0 <= hour < 24, fractions allowed, as Retell's current_hour variable gives). The prompt states the same boundaries."""
    return "Good morning" if hour < MORNING_UNTIL else "Good afternoon" if hour < AFTERNOON_UNTIL else "Good evening"
