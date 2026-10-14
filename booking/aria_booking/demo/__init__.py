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
