"""The Retell custom-function definitions for the lifecycle tools: what the voice model is told each one does and which arguments it takes.

The three read-only tools (lookup_service, check_slot, find_alternatives) keep their reviewed definitions from the availability-only tool file; only the five new ones are
written here. ``tools_demo_lifecycle.json`` is generated from both by ``python -m aria_booking.catalog.render --write``, and a test checks that every route the lifecycle
endpoint serves has exactly one definition, and that every argument a definition names is one the tool reads.
"""

from __future__ import annotations

HOST = "<YOUR-TUNNEL-HOST>"
AUTH = {"Authorization": "Bearer <set in the Retell dashboard>"}

PHONE_TEXT = (
    "The caller's COMPLETE phone number including the area code, exactly as they said it. Ask for it if you do not have it. Never complete, guess or repeat it back aloud."
)
NAME_TEXT = "Optional. The caller's full name, ONLY when the tool has asked for it because the number matches more than one record."
TOKEN_TEXT = "From the read-back this tool just returned. Send it only together with confirmed true, after the caller clearly said yes to that read-back."
CONFIRMED_TEXT = "Send true ONLY after the caller clearly said yes to the sentence the previous call returned. Never send it on the first call."
DATE_TEXT = "The day as YYYY-MM-DD in the salon's time zone (America/New_York). Resolve words like 'tomorrow' or 'next Tuesday' yourself using the current date; if ambiguous, ask first."
TIME_TEXT = "The START time in 24-hour HH:MM, on the quarter hour. If the caller said an ambiguous time such as 'at 3', ask whether they mean morning or afternoon first."
REFERENCE_TEXT = "The appointment's reference exactly as find_appointments returned it. Never invent one and do not read it aloud unless the caller asks."
NAME_CONFIRMED_TEXT = (
    "Optional. Send true ONLY after this tool asked the caller to spell or choose their name and they did: it says the name you are sending is exactly as the caller spelled or "
    "confirmed it. Never send it on a first attempt."
)
SERVICE_WORDS_TEXT = (
    "The treatment EXACTLY as the caller said it, in their own words and nothing else (no names, dates or times), for example 'a sixty minute massage' or 'deep tissue'. Never replace "
    "it with the name of a service you chose. If it only names a group (massage, facial) or only a length, the tool asks the caller which treatment, so do not pick one for them."
)
WINDOW_TEXT = (
    "Optional. A START-time bound for that ONE day, 24-hour HH:MM, for when the caller asks for a time later or earlier than something. Times outside it are never returned, and no "
    "other day is searched. {what}"
)
WINDOW_PROPERTIES = {
    "starting_at_or_after": {"type": "string", "description": WINDOW_TEXT.format(what="INCLUSIVE: 'at 5 or later', 'from 5 on'. The time itself is allowed.")},
    "starting_after": {"type": "string", "description": WINDOW_TEXT.format(what="EXCLUSIVE: 'later than my 3:45', 'after 5'. The time itself is NOT allowed.")},
    "starting_at_or_before": {"type": "string", "description": WINDOW_TEXT.format(what="INCLUSIVE: 'by 5', 'no later than 5'. The time itself is allowed.")},
    "starting_before": {"type": "string", "description": WINDOW_TEXT.format(what="EXCLUSIVE: 'before 5', 'earlier than my 3:45'. The time itself is NOT allowed.")},
}


def _tool(name, description, properties, required, *, timeout_ms, execution_message=None):
    tool = {
        "name": name,
        "description": description,
        "url": f"https://{HOST}/tools/{name}",
        "method": "POST",
        "headers": dict(AUTH),
        "timeout_ms": timeout_ms,
        "max_retry": 0,
        "speak_during_execution": execution_message is not None,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }
    if execution_message is not None:
        tool["execution_message_description"] = execution_message
    return tool


ONE_SENTENCE = (
    "Say ONE short sentence, once, as the lookup starts: 'One moment while I {what}.' Never guess a name or service the caller has not given. Give no estimate of how long it "
    "will take, do not repeat or extend it, add nothing else, and use no filler such as um or uh."
)

LIFECYCLE_TOOLS = [
    _tool(
        "find_appointments",
        "Look up a caller's UPCOMING appointments by their complete phone number (a prototype record match, not proof of identity). Use it when the caller asks when their "
        "appointment is, what appointments they have, or before cancelling or moving one. Pass `service` in the caller's own words when they asked about one kind of "
        "appointment: the answer then leads with that one and briefly mentions the rest. It returns each appointment's `reference` and `version` (keep them for a cancel or a "
        "move) and a `speak` sentence to give. This only LOOKS: it changes nothing. If it asks for the full name, the number is on more than one record: ask for the name.",
        {"phone": {"type": "string", "description": PHONE_TEXT}, "name": {"type": "string", "description": NAME_TEXT},
         "service": {"type": "string", "description": "Optional. The kind of appointment the caller asked about, in their own words (for example waxing, my massage, a facial)."},
         "name_confirmed": {"type": "boolean", "description": NAME_CONFIRMED_TEXT}},
        ["phone"], timeout_ms=10000, execution_message=ONE_SENTENCE.format(what="look that up"),
    ),
    _tool(
        "last_completed_visit",
        "Answer 'when was my last <lash / massage / facial / waxing / head spa> appointment?' for a caller who gives their complete phone number. It reports only the most recent "
        "COMPLETED visit of that kind (never a cancelled, missed or upcoming one) and says plainly when it cannot confirm. This only LOOKS: it changes nothing.",
        {"service_category": {"type": "string", "description": "The kind of appointment, as the caller said it (lashes, massage, facial, waxing, head spa...)."},
         "phone": {"type": "string", "description": PHONE_TEXT}, "name": {"type": "string", "description": NAME_TEXT},
         "include_technician": {"type": "boolean", "description": "Send true ONLY if the caller asked who the visit was with."},
         "name_confirmed": {"type": "boolean", "description": NAME_CONFIRMED_TEXT}},
        ["service_category", "phone"], timeout_ms=10000, execution_message=ONE_SENTENCE.format(what="look that up"),
    ),
    _tool(
        "book_appointment",
        "Book an appointment in the prototype calendar, in TWO steps. Call it first with the chosen service, day, time, the caller's full name and complete phone number (and `staff` "
        "only if they asked for someone): it saves NOTHING and returns `confirmation_required` with one sentence and a `confirmation_token`. Read the sentence to the caller; "
        "only after a clear yes call it again with exactly the same details, the token and confirmed true. It is booked only if the result is `booked_verified` or `already_booked` "
        "with ok true. If the time was just taken it says so and offers real alternatives: nothing was booked. Never call it again just because it is slow.",
        {"service_id": {"type": "string", "description": "The canonical service_id exactly as listed under 'What you can check and book right now' or returned by lookup_service. Never invent or alter one."},
         "date": {"type": "string", "description": DATE_TEXT}, "time": {"type": "string", "description": TIME_TEXT},
         "staff": {"type": "string", "description": "Optional. The technician's name ONLY if the caller asked for a particular person."},
         "client_name": {"type": "string", "description": "The caller's full name for the booking, as they said it. If the tool asks them to spell it, send the spelling."},
         "phone": {"type": "string", "description": PHONE_TEXT}, "name_confirmed": {"type": "boolean", "description": NAME_CONFIRMED_TEXT},
         "service_words": {"type": "string", "description": SERVICE_WORDS_TEXT},
         "confirmation_token": {"type": "string", "description": TOKEN_TEXT}, "confirmed": {"type": "boolean", "description": CONFIRMED_TEXT}},
        ["service_id", "service_words", "date", "time", "client_name", "phone"], timeout_ms=20000, execution_message=ONE_SENTENCE.format(what="check that"),
    ),
    _tool(
        "cancel_appointment",
        "Cancel ONE upcoming appointment in the prototype calendar, in TWO steps. First call it with the appointment's `reference` (from find_appointments) and the caller's complete "
        "phone number: it saves nothing and returns `confirmation_required` with one sentence and a token. Read it; only after a clear yes call it again with the same details, the token, "
        "confirmed true, and `reason` if the caller gave one. The reason is optional and cancelling never depends on choosing another time. It is cancelled only if the result is "
        "`cancelled` or `already_cancelled` with ok true. Never mention a fee or policy.",
        {"reference": {"type": "string", "description": REFERENCE_TEXT}, "phone": {"type": "string", "description": PHONE_TEXT}, "name": {"type": "string", "description": NAME_TEXT},
         "reason": {"type": "string", "description": "Optional. Why they are cancelling, in a few words, if they chose to say. Never press for it."},
         "name_confirmed": {"type": "boolean", "description": NAME_CONFIRMED_TEXT},
         "confirmation_token": {"type": "string", "description": TOKEN_TEXT}, "confirmed": {"type": "boolean", "description": CONFIRMED_TEXT}},
        ["reference", "phone"], timeout_ms=20000, execution_message=ONE_SENTENCE.format(what="look at that"),
    ),
    _tool(
        "reschedule_appointment",
        "Move ONE upcoming appointment to a new day and time in the prototype calendar, in TWO steps. First call it with the appointment's `reference`, the caller's complete phone number "
        "and the new date and time (and `staff` only if they asked for someone): it saves nothing and returns `confirmation_required` with one sentence naming the old and the new time and "
        "a token. Read it; only after a clear yes call it again with the same details, the token and confirmed true. It is moved only if the result is `rescheduled` or `already_rescheduled` with ok true; if the new "
        "time is not available the original appointment stays exactly as it was and the tool offers real alternatives.",
        {"reference": {"type": "string", "description": REFERENCE_TEXT}, "phone": {"type": "string", "description": PHONE_TEXT}, "name": {"type": "string", "description": NAME_TEXT},
         "date": {"type": "string", "description": DATE_TEXT.replace("The day", "The NEW day")}, "time": {"type": "string", "description": TIME_TEXT.replace("The START", "The NEW START")},
         "staff": {"type": "string", "description": "Optional. The technician's name ONLY if the caller asked for a particular person; otherwise the same technician is tried first."},
         "name_confirmed": {"type": "boolean", "description": NAME_CONFIRMED_TEXT},
         "confirmation_token": {"type": "string", "description": TOKEN_TEXT}, "confirmed": {"type": "boolean", "description": CONFIRMED_TEXT}},
        ["reference", "phone", "date", "time"], timeout_ms=20000, execution_message=ONE_SENTENCE.format(what="check that"),
    ),
]

# What the inherited read tools gain on this line: the caller's own words for the treatment, and (for find_alternatives) an explicit same-day start-time bound.
LIFECYCLE_EXTRA_PROPERTIES = {
    "check_slot": {"service_words": {"type": "string", "description": SERVICE_WORDS_TEXT}},
    "find_alternatives": {"service_words": {"type": "string", "description": SERVICE_WORDS_TEXT}, **WINDOW_PROPERTIES},
}
LIFECYCLE_EXTRA_REQUIRED = {"check_slot": ["service_words"], "find_alternatives": ["service_words"]}
LIFECYCLE_TIME_TEXT = {
    "find_alternatives": (
        "Optional preferred START time in 24-hour HH:MM: which times to list first. It is only a preference and NEVER a limit: for 'later than', 'after', 'earlier than' or 'before' "
        "use the starting_* fields instead. If you leave it out, the time the caller last asked for in this call is kept."
    ),
}

# What the three inherited read tools say when this line CAN book: the availability-only wording ("nothing is booked") stays true of them, but 'this line only checks' does not.
LIFECYCLE_DESCRIPTIONS = {
    "check_slot": (
        "Check whether the caller's requested date and start time is OPEN for the FULL chosen service, and with which technician. Call this when the caller wants an appointment or asks "
        "about availability, never for a service, price or product question. Always call it before saying anything about availability. If the time is not open it explains why (closed, "
        "service would run past closing, already taken, too soon, named technician booked) and returns up to three genuinely open alternatives, each with the service, its length and the "
        "technician. This only CHECKS: nothing is booked, held or changed (use book_appointment to book). Never state availability from your own knowledge."
    ),
    "find_alternatives": (
        "List up to three genuinely open times for the chosen service on a day (and optionally a preferred start time), each with the service, its length and the technician. Use "
        "when the caller has no specific time, or asks for other times. When the caller asks for something LATER or EARLIER than a time (for example 'a later time than my 3:45'), pass "
        "the bound with the starting_* fields: only starts inside it, on that day, are returned. This only CHECKS: nothing is booked, held or changed. Call only on appointment or availability intent."
    ),
}


# ---- preferred technicians (demo only; a separate, opt-in tool file: ``tools_demo_lifecycle_preferences.json`` = the lifecycle file + these three) ----
PREFERENCE_SERVICE_TEXT = (
    "ONE service, in the caller's own words (for example Swedish Massage, Signature Facial). A preference is kept per service, never for a whole group such as 'massage' or 'facial': "
    "if the caller only named a group, ask which one."
)
PREFERENCE_STAFF_TEXT = "The technician's name, exactly as the caller said it. Never guess or substitute one."

PREFERENCE_TOOLS = [
    _tool(
        "get_preferences",
        "Look up a returning client's saved PREFERRED TECHNICIANS by their complete phone number (a prototype record match, not proof of identity). Use it when the caller asks who their "
        "usual technician is or what is saved, and before offering a technician to a caller who is booking a service and did not name one. Pass `service` to ask about one service. "
        "A preference is only who the client would like: it never guarantees a time. This only LOOKS: it changes nothing. If it asks for the full name, the number is on more than one "
        "record: ask for the name.",
        {"phone": {"type": "string", "description": PHONE_TEXT}, "name": {"type": "string", "description": NAME_TEXT}, "name_confirmed": {"type": "boolean", "description": NAME_CONFIRMED_TEXT},
         "service": {"type": "string", "description": "Optional. One service, in the caller's own words, to ask about just that one. " + PREFERENCE_SERVICE_TEXT}},
        ["phone"], timeout_ms=10000, execution_message=ONE_SENTENCE.format(what="look that up"),
    ),
    _tool(
        "save_preference",
        "Save, or change, the caller's preferred technician for ONE service in the prototype calendar, in TWO steps, and ONLY when the caller asks for it in their own words. Call it first "
        "with their complete phone number, the service and the technician: it saves NOTHING and returns `confirmation_required` with one sentence and a `confirmation_token`. Read the "
        "sentence to the caller; only after a clear yes call it again with exactly the same details, the token and confirmed true. It is saved only if the result is `preference_saved` "
        "or `preference_unchanged` with ok true. If it asks which service or which technician, ask the caller. It never changes an existing appointment.",
        {"phone": {"type": "string", "description": PHONE_TEXT}, "name": {"type": "string", "description": NAME_TEXT}, "name_confirmed": {"type": "boolean", "description": NAME_CONFIRMED_TEXT},
         "service": {"type": "string", "description": PREFERENCE_SERVICE_TEXT}, "staff": {"type": "string", "description": PREFERENCE_STAFF_TEXT},
         "confirmation_token": {"type": "string", "description": TOKEN_TEXT}, "confirmed": {"type": "boolean", "description": CONFIRMED_TEXT}},
        ["phone", "service", "staff"], timeout_ms=20000, execution_message=ONE_SENTENCE.format(what="check that"),
    ),
    _tool(
        "remove_preference",
        "Remove the caller's saved preferred technician for ONE service, in TWO steps, and ONLY when the caller asks for it. Call it first with their complete phone number and the "
        "service: it removes NOTHING and returns `confirmation_required` with one sentence and a `confirmation_token`. Read it; only after a clear yes call it again with the same details, "
        "the token and confirmed true. It is removed only if the result is `preference_removed` with ok true. It never changes an existing appointment.",
        {"phone": {"type": "string", "description": PHONE_TEXT}, "name": {"type": "string", "description": NAME_TEXT}, "name_confirmed": {"type": "boolean", "description": NAME_CONFIRMED_TEXT},
         "service": {"type": "string", "description": PREFERENCE_SERVICE_TEXT},
         "confirmation_token": {"type": "string", "description": TOKEN_TEXT}, "confirmed": {"type": "boolean", "description": CONFIRMED_TEXT}},
        ["phone", "service"], timeout_ms=20000, execution_message=ONE_SENTENCE.format(what="check that"),
    ),
]

PREFERENCE_TOOL_NAMES = frozenset(tool["name"] for tool in PREFERENCE_TOOLS)
