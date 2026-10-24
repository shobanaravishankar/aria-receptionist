"""Render the catalogue into the knowledge section of the voice prompt, and build the generated prompt and tool files. The prompt
is GENERATED from the catalogue, so a price or length can never be changed in one place and forgotten in the other.

Two modes share one template and one catalogue:
  * ``booking``       the earlier booking-capable test line (check, offer, confirm, book);
  * ``availability``  the availability-only demo: it can check times and name technicians, and has NO booking function at all.

    python -m aria_booking.catalog.render            print the knowledge section
    python -m aria_booking.catalog.render --write    rebuild the generated files in booking/voice_agent_draft/
"""

from __future__ import annotations

import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

from .bookable import BookableRegistry
from .lookup import DEFAULT_CATALOG, Catalog, descriptive_label
from .website_data import NOT_PUBLISHED, SITE, duration_label

CATEGORY_TITLES = OrderedDict([
    ("facial", "Facials"), ("massage", "Massages"), ("package", "Spa packages"), ("body-scrub", "Body scrubs"), ("lash", "Eyelashes and brows"),
    ("head-spa", "Head spa"), ("waxing", "Waxing"), ("permanent-makeup", "Permanent makeup"), ("couples", "Couples packages"),
    ("membership", "Visit packages (memberships)"),
])
MARKER = "<!-- KNOWLEDGE -->"
DRAFT_DIR = Path(__file__).resolve().parents[2] / "voice_agent_draft"
MODES = ("booking", "availability", "lifecycle")  # 'lifecycle' (the simulated-appointment demo) has its own template with no mode markers; it only changes the knowledge heading
GENERATED = {"booking": "prompt_test_only.md", "availability": "prompt_availability_only.md"}
AVAILABILITY_TOOLS_FILE = "tools_availability_only.json"
DEMO_PROMPT_FILE = "prompt_demo_calendar.md"  # the availability-only prompt for the DEMO calendar (invented data); it uses the same tool file
LIFECYCLE_TEMPLATE_FILE = "prompt_template_lifecycle.md"  # the conversation template of the SIMULATED-appointment demo (find, book, move, cancel)
LIFECYCLE_PROMPT_FILE = "prompt_demo_lifecycle.md"
LIFECYCLE_TOOLS_FILE = "tools_demo_lifecycle.json"
BREVITY_PROMPT_FILE = "prompt_demo_lifecycle_preferences_multilingual_brief.md"  # the multilingual prompt with brief replies and no spoken prices (user-authorized; the live text since 2026-10-22 11:38)
MULTILINGUAL_PROMPT_FILE = "prompt_demo_lifecycle_preferences_multilingual.md"  # the lifecycle-with-preferences prompt in English, Spanish and Mandarin Chinese (user-authorized; speech and transaction fidelity unproven)
SPANISH_PROMPT_FILE = "prompt_demo_lifecycle_preferences_spanish.md"  # the preferences prompt + an optional Spanish mini-demonstration section (menu conversation only; opt-in)
PREFERENCES_PROMPT_FILE = "prompt_demo_lifecycle_preferences.md"  # the lifecycle prompt + a 'Preferred technicians' section (demo only, opt-in; the lifecycle files are unchanged)
PREFERENCES_TOOLS_FILE = "tools_demo_lifecycle_preferences.json"
DEMO_SECTION = """## You are connected to a DEMO calendar

- The calendar behind your tools is a prototype calendar with invented appointments and invented clients, shown on a page that is labelled
  "Demo calendar - simulated availability". It is not the salon's real schedule, and nothing in it is a real person's appointment.
- Do NOT announce this and do not say "demo", "simulated" or "prototype" on your own, in the greeting or in any answer: speak naturally as the receptionist and
  give availability as the tools return it. If the caller asks whether the schedule, the availability or an appointment is real or live, or whether this is a
  demo, answer truthfully: this is a prototype with an invented calendar, not the salon's real schedule. Never describe an invented appointment as a real one,
  and never imply anything was booked.
- Names, times and who is free come only from the tool. If the tool says it does not see the technician the caller named, say so and ask who they meant; never
  swap in a similar-sounding name. If the tool asks which person (several share a first name), ask the caller.
- You cannot book, hold, change or cancel anything here either.
- Offer or run a calendar check ONLY for a service under "What you can check availability for right now", or one that `lookup_service` returns with
  `bookable` true. This replaces the one-time "Would you like me to check availability?" offer for every other service: for those, give the information,
  say plainly that you cannot check availability for it, suggest the caller contact the salon, and do NOT offer a check, say you will check, or call a
  calendar tool. Never build a `service_id` yourself from a service name or length.
- If the caller asks who does a service, call `lookup_service`: for a service you can check it returns `technicians`, the calendar's own list. Give those
  names, and never say there is no list when it returned one. If the caller names a technician who is not on that list, say that technician does not do that
  service and name those who do; never swap in someone else.
- If the caller gave a time earlier in this call and then asks for other times or another day, call `find_alternatives` WITHOUT a time: the server keeps the time they asked
  for. Pass a `time` only when they give a new one, and `any_time: true` only when they say any time works. Never invent a preferred time.
- If a check answers `unknown` and also returns `options`, say what the tool said (it names what could and could not be confirmed) and offer those times. Never say the
  calendar is down, not responding or fully booked unless the tool says so, and never guess about a schedule it says it could not read.

"""

LIFECYCLE_SECTION = """## You are connected to a prototype calendar

- The calendar behind your tools is a prototype calendar with invented appointments and invented clients, shown on a page that is labelled "Prototype showcase". It is not the
  salon's real schedule, and nothing in it is a real person's appointment. The appointments you book, move or cancel with your tools are saved in that prototype calendar and
  appear on its page, and nowhere else.
- Do NOT announce this and do not say "demo", "simulated" or "prototype" on your own, in the greeting or in any answer: speak naturally as the receptionist and give
  availability and appointments as the tools return them. If the caller asks whether the schedule, the availability or an appointment is real or live, or whether this is a demo,
  answer truthfully: this is a prototype with an invented calendar, not the salon's real schedule, and the appointments made here exist only in it. Never describe an invented
  appointment as a real one.
- Names, times and who is free come only from the tool. If the tool says it does not see the technician the caller named, say so and ask who they meant; never swap in a
  similar-sounding name. If the tool asks which person (several share a first name), ask the caller.
- Offer or run a calendar check or a booking ONLY for a service under "What you can check and book right now", or one that `lookup_service` returns with `bookable` true. For every
  other service: give the information, say plainly that you cannot book it here, suggest the caller contact the salon, and do NOT offer a check or call a calendar tool. Never
  build a `service_id` yourself from a service name or length.
- If the caller asks who does a service, call `lookup_service`: for a service you can book it returns `technicians`, the calendar's own list. Give those names, and never say there
  is no list when it returned one. If the caller names a technician who is not on that list, say that technician does not do that service and name those who do; never swap in
  someone else.
- If the caller gave a time earlier in this call and then asks for other times or another day, call `find_alternatives` WITHOUT a time: the server keeps the time they asked
  for. Pass a `time` only when they give a new one, and `any_time: true` only when they say any time works. Never invent a preferred time.
- If a check answers `unknown` and also returns `options`, say what the tool said (it names what could and could not be confirmed) and offer those times. Never say the
  calendar is down, not responding or fully booked unless the tool says so, and never guess about a schedule it says it could not read.

"""

PREFERENCES_SECTION = """## Preferred technicians

You also have three functions for a returning client's PREFERRED TECHNICIAN, kept for one service at a time (for example Swedish Massage): `get_preferences`, `save_preference` and
`remove_preference`. They keep a note of who the client would like. They change only that note, never an appointment.

- **Only on request.** Save, change or remove a preference only when the caller asks for it in their own words ("always book me with {OTHER} for my Swedish Massage", "make {TECH} my
  usual for Deep Tissue Massage", "forget my usual for Swedish Massage"). Never infer one from a booking or a past visit, never suggest saving one, and never save one because the
  caller chose a technician for a single appointment.
- **Saving or changing.** You need the **service** (one service, not a whole group such as "massage": ask which one), the **technician**, and the caller's complete phone number (and the
  full name only when the tool asks for it). If the tool answers `needs_clarification` (a service that is unclear, or a technician who is unknown or does not do that service), ask
  its question and give the technicians it names. Call `save_preference`: it **saves nothing yet** and returns `confirmation_required` with a sentence and a `confirmation_token`. Read that
  sentence, and only after a clear yes call it again with exactly the same details, the token and `confirmed: true`. It is saved only on `preference_saved` or `preference_unchanged` with
  `ok: true`. If the caller says no, say that nothing was saved.
- **Removing.** Call `remove_preference` the same way: a read-back, a clear yes, then the same details with the token and `confirmed: true`. It is removed only on `preference_removed`
  with `ok: true`; `nothing_to_remove` means there was nothing saved.
- **What is saved.** When the caller asks who their usual technician is, or what is saved for them, call `get_preferences` (with `service` for one service) and say what it returned.
  Never say another client's preferences.
- **Using one when booking.** When a caller is booking a service and did not name a technician, and you have their complete phone number, call `get_preferences` with that service. If it
  returns a preferred technician with `eligible: true`, offer that person first ("Your usual for Swedish Massage is {OTHER}: shall I check with {OTHER}?"), and pass `staff` to `check_slot`
  only after the caller says yes. If you do not have their number yet, carry on as normal and do this once you have it, before you read the booking back.
- **A preference is only who, never when.** It never guarantees a time. If the preferred technician is not free or does not fit, say why from the tool, offer the alternatives it
  returned, and leave the saved preference as it is. If the caller asks for a different technician for this appointment, book that person: it does not change the saved preference.
- Saving, changing or removing a preference never changes an appointment that is already booked.

"""

# Descriptions for the availability-only tool file: they only CHECK, and say so. (The booking file keeps tools.json's wording.)
ANY_TIME_PROPERTY = {
    "type": "boolean",
    "description": (
        "Optional. Send true ONLY if the caller says any time works, to drop the time they asked for earlier in this call. Leave it out otherwise: when you omit `time`, the server keeps "
        "the time the caller last asked for in THIS call, so do not guess one."
    ),
}
AVAILABILITY_DESCRIPTIONS = {
    "check_slot": (
        "Check whether the caller's requested date and start time is OPEN for the FULL chosen service, and with which technician. Call this ONLY when "
        "the caller wants an appointment or asks about availability, never for a service, price or product question. Always call it before saying "
        "anything about availability. If the time is not open it explains why (closed, service would run past closing, already taken, too soon, "
        "named technician booked) and returns up to three genuinely open alternatives, each with the service, its length and the technician. This "
        "only CHECKS: nothing is booked, held or changed. Never state availability from your own knowledge."
    ),
    "find_alternatives": (
        "List up to three genuinely open times for the chosen service starting from a day (and optionally a preferred start time), each with the "
        "service, its length and the technician. Use when the caller has no specific time, or asks for other times. This only CHECKS: nothing is "
        "booked, held or changed. Call only on appointment or availability intent."
    ),
}


def _short(minutes) -> str:
    if minutes is None:
        return "length not listed"
    return duration_label(minutes).replace(" hours", " hr").replace(" hour", " hr").replace(" minutes", " min")


def _variant_text(v) -> str:
    label = descriptive_label(v)  # '2-week refill', 'initial set': equal lengths and prices must still be told apart
    text = _variant_core(v)
    return f"{label}: {text}" if label else text


def _variant_core(v) -> str:
    if v.price_usd is None:
        text = f"{_short(v.duration_minutes)}, price not listed"
    elif v.category == "membership":
        text = f"package ${v.price_usd}"
    else:
        text = f"{_short(v.duration_minutes)} ${v.price_usd}"
    return text + (f" (was ${v.original_price_usd})" if v.original_price_usd is not None else "")


def website_intro() -> list[str]:
    """The salon facts block for the website knowledge (the earlier salon). The services follow it."""
    return [
        "## What you may say about the salon (the website's own information)",
        "",
        f"Source: the salon's official website, read on {SITE['retrieved_on']}. Prices and lengths are the website's LISTED ones. "
        "Use them for questions about services, lengths and prices. If a caller asks about something that is not below, say you do not have that information.",
        "",
        f"- Salon: {SITE['name']}, {SITE['address']}.",
        f"- Website opening hours: {SITE['website_hours']}. These are information only. NEVER use them to say whether an appointment is or is not available; "
        "availability comes only from the calendar tools.",
        "- Not published on the website, so you do not have it: " + "; ".join(NOT_PUBLISHED) + ".",
        "- Retail products: the website lists none, so you cannot describe, price or promise any product or its stock.",
    ]


def showcase_intro(showcase) -> list[str]:
    """The salon facts block for the generic showcase: a name, example hours, and explicit statements of what is NOT configured (no address, no phone) so none is invented."""
    phone = (f"- Phone: {showcase.contact_phone}. Give it when a caller needs the salon directly, and never say a transfer, callback or message has happened."
             if showcase.contact_phone else
             "- Phone: no number is configured yet. Do not give, guess or invent one; say you do not have a number to give.")
    location = (f"- Address and location: {showcase.location}. Give it exactly as written when a caller asks where the salon is; add no directions, town, landmark, map or travel time of your own."
                if showcase.location else
                "- Address and location: none is configured. Do not state, guess or look up an address, town or directions; say you do not have one to give.")
    return [
        "## What you may say about the salon (the prototype menu's information)",
        "",
        f"Source: the {showcase.name} prototype menu. Its prices, lengths and hours are example data that can be edited. Prices and lengths are the menu's LISTED ones. "
        "Use them for questions about services, lengths and prices. If a caller asks about something that is not below, say you do not have that information.",
        "",
        f"- Salon: {showcase.name}.",
        location,
        phone,
        f"- Opening hours (example data): {showcase.example_hours}. These are information only. NEVER use them to say whether an appointment is or is not available; "
        "availability comes only from the calendar tools.",
        "- Not on the menu, so you do not have it: " + "; ".join(NOT_PUBLISHED) + ".",
        "- Retail products: the menu lists none, so you cannot describe, price or promise any product or its stock.",
    ]


def render_knowledge(catalog: Catalog = DEFAULT_CATALOG, registry: BookableRegistry | None = None, mode: str = "booking", intro: list[str] | None = None) -> str:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    lines = list(intro if intro is not None else website_intro()) + ["", "### Services and listed prices"]
    for category, title in CATEGORY_TITLES.items():
        families: "OrderedDict[str, list]" = OrderedDict()
        for item in catalog.items:
            if item.category == category:
                families.setdefault(item.family, []).append(item)
        if not families:
            continue
        lines += ["", f"**{title}**"]
        for family, variants in families.items():
            options = " | ".join(_variant_text(v) for v in variants)
            notes = " ".join(dict.fromkeys(v.notes for v in variants if v.notes))
            lines.append(f"- {family}: {options}. {variants[0].description}" + (f" Note: {notes}" if notes else ""))
    if mode == "booking":
        lines += ["", "### What can be booked online right now", ""]
        none, action = "Nothing. Online booking is not available; tell the caller to contact the salon directly.", "check availability for it or book it"
    elif mode == "lifecycle":
        lines += ["", "### What you can check and book right now", ""]
        none, action = "Nothing. Availability checks and booking are not available; tell the caller to contact the salon directly.", "check availability for it or book it"
    else:
        lines += ["", "### What you can check availability for right now", ""]
        none, action = "Nothing. Availability checks are not available; tell the caller to contact the salon directly.", "check availability for it"
    if registry is None:
        lines.append("- " + none)
    else:
        for service_id in registry.ids():
            svc = registry.get(service_id)
            if not svc.verified:
                continue
            tag = " (a fictional TEST service for this demo line)" if svc.test_only else ""
            lines.append(f"- service_id `{svc.service_id}`: {svc.booksy_name}, {_short(svc.duration_minutes)}{tag}.")
        lines.append(
            f"- Every other service listed above is INFORMATION ONLY for now: you can describe it and give its listed price, but you cannot {action}. "
            "Say so plainly and suggest the caller contact the salon directly."
        )
    return "\n".join(lines) + "\n"


def apply_mode(template: str, mode: str) -> str:
    """Keep the blocks for this mode (<!--B--> for booking, <!--A--> for availability) and drop the other mode's."""
    keep, drop = ("B", "A") if mode == "booking" else ("A", "B")
    template = re.sub(rf"<!--{drop}-->.*?<!--/{drop}-->\n?", "", template, flags=re.S)
    template = re.sub(rf"<!--{keep}-->(.*?)<!--/{keep}-->", r"\1", template, flags=re.S)
    if re.search(r"<!--/?[AB]-->", template):
        raise ValueError("unbalanced mode markers in the prompt template")
    return re.sub(r"\n{3,}", "\n\n", template)


def build_prompt(template: str, catalog: Catalog = DEFAULT_CATALOG, registry: BookableRegistry | None = None, mode: str = "booking",
                 intro: list[str] | None = None) -> str:
    if template.count(MARKER) != 1:
        raise ValueError("the prompt template must contain the knowledge marker exactly once")
    return apply_mode(template, mode).replace(MARKER, render_knowledge(catalog, registry, mode, intro).rstrip("\n"))


# Retell gives up on a tool after this long. Above the server's worst case (lock wait + read deadline, see voice/launch.py), far below 90 s.
AVAILABILITY_TIMEOUTS_MS = {"lookup_service": 5000, "check_slot": 20000, "find_alternatives": 20000}


def build_availability_tools(tools: dict) -> dict:
    """tools.json minus every writer, with descriptions that say the functions only check. Nothing here can book."""
    kept = []
    for tool in tools["tools"]:
        if tool["name"] == "book_slot":
            continue
        tool = dict(tool)
        if tool["name"] in AVAILABILITY_DESCRIPTIONS:
            tool["description"] = AVAILABILITY_DESCRIPTIONS[tool["name"]]
        if tool["name"] == "find_alternatives":
            tool["parameters"] = json.loads(json.dumps(tool["parameters"]))
            tool["parameters"]["properties"]["time"]["description"] = (
                "Optional preferred START time in 24-hour HH:MM. Pass it when the caller gives or changes a time; if you leave it out, the time the caller last asked for in this call is kept."
            )
            tool["parameters"]["properties"]["any_time"] = ANY_TIME_PROPERTY
        if tool["name"] in AVAILABILITY_TIMEOUTS_MS:
            tool["timeout_ms"] = AVAILABILITY_TIMEOUTS_MS[tool["name"]]
        kept.append(tool)
    out = {k: v for k, v in tools.items() if k != "tools"}
    out["_status"] = "AVAILABILITY-ONLY demo tools: no booking function exists in this file. " + tools["_status"]
    out["tools"] = kept
    return out


LIFECYCLE_TOOLS_STATUS = (
    "SIMULATED-LIFECYCLE demo tools: find, book, move and cancel appointments in the PROTOTYPE calendar only. There is no function for the live Booksy calendar. "
    "DRAFT FOR REVIEW. Not imported into Retell, not deployed."
)


def build_lifecycle_tools(availability_tools: dict) -> dict:
    """The availability-only tool file's three read tools (reviewed definitions, descriptions adjusted for a line that can book) plus the five lifecycle tools."""
    from ..appointments.tool_defs import LIFECYCLE_DESCRIPTIONS, LIFECYCLE_EXTRA_PROPERTIES, LIFECYCLE_EXTRA_REQUIRED, LIFECYCLE_TIME_TEXT, LIFECYCLE_TOOLS

    kept = []
    for tool in availability_tools["tools"]:
        tool = json.loads(json.dumps(tool))
        if tool["name"] in LIFECYCLE_DESCRIPTIONS:
            tool["description"] = LIFECYCLE_DESCRIPTIONS[tool["name"]]
        if tool["name"] in LIFECYCLE_EXTRA_PROPERTIES:
            tool["parameters"]["properties"].update(json.loads(json.dumps(LIFECYCLE_EXTRA_PROPERTIES[tool["name"]])))
            tool["parameters"]["required"] = list(tool["parameters"].get("required", [])) + [r for r in LIFECYCLE_EXTRA_REQUIRED.get(tool["name"], []) if r not in tool["parameters"].get("required", [])]
        if tool["name"] in LIFECYCLE_TIME_TEXT:
            tool["parameters"]["properties"]["time"]["description"] = LIFECYCLE_TIME_TEXT[tool["name"]]
        kept.append(tool)
    out = {k: v for k, v in availability_tools.items() if k != "tools"}
    out["_status"] = LIFECYCLE_TOOLS_STATUS
    out["tools"] = kept + [json.loads(json.dumps(tool)) for tool in LIFECYCLE_TOOLS]
    return out


PREFERENCES_TOOLS_STATUS = (
    "SIMULATED-LIFECYCLE demo tools with PREFERRED TECHNICIANS (demo only, opt-in): find, book, move and cancel appointments and keep a preferred technician per service in the PROTOTYPE "
    "calendar only. There is no function for the live Booksy calendar. DRAFT FOR REVIEW. Not imported into Retell, not deployed."
)


def build_preference_tools(lifecycle_tools: dict) -> dict:
    """The lifecycle tool file exactly as it is, plus the three preferred-technician functions. Nothing in the eight lifecycle definitions changes."""
    from ..appointments.tool_defs import PREFERENCE_TOOLS

    out = {k: v for k, v in lifecycle_tools.items() if k != "tools"}
    out["_status"] = PREFERENCES_TOOLS_STATUS
    out["tools"] = [json.loads(json.dumps(tool)) for tool in lifecycle_tools["tools"]] + [json.loads(json.dumps(tool)) for tool in PREFERENCE_TOOLS]
    return out


def opening_section(salon_name: str) -> str:
    """How Aria opens every call. The greeting word comes from the spa's local hour at the START OF THAT CALL: Retell fills ``{{current_hour_<zone>}}`` in per call, so
    nothing is frozen when the server starts or this file is generated. (An opening message cannot branch on a variable, so the agent's opening message is left unset
    and the model speaks this first.)"""
    from ..demo import AFTERNOON_UNTIL, MORNING_UNTIL, SPA_TIMEZONE

    hour = "{{current_hour_" + SPA_TIMEZONE + "}}"
    return (
        "## Your opening line\n\n"
        "You speak first. Start every call with this wording, delivered warmly and with a bright, upbeat, smiling lift on the welcome:\n\n"
        f"> <Greeting>! This is Aria at {salon_name}. I'd love to help you get some relaxation on the calendar. To get started, are you looking for a massage, "
        "a facial, or maybe a full spa day?\n\n"
        f"- <Greeting> is \"Good morning\" when {hour} is below {MORNING_UNTIL}, \"Good afternoon\" from {MORNING_UNTIL} up to (not including) {AFTERNOON_UNTIL}, and "
        f"\"Good evening\" from {AFTERNOON_UNTIL} on. {hour} is the spa's own local hour (0 to 24, with fractions) at the start of THIS call: use it, never another "
        "time zone and never a time you assumed.\n"
        "- Do not begin with \"Hello\" or \"Hi\", and do not call yourself an AI receptionist in the opening. If a caller later asks directly whether you are an AI or "
        "an automated assistant, answer truthfully.\n"
        "- Sound conversational and unhurried: short sentences, a little extra lift on the greeting, then your normal warm, calm pace.\n"
        "- \"A full spa day\" is only an invitation to talk about services. Do not describe, price or promise a package you do not find in the services below: offer "
        "the listed services, say what you can check availability for, and say plainly when you do not have something.\n\n"
    )


SALON_NAME_ON_WEBSITE = "Glamour Day Spa"  # the exact salon name in the website knowledge and the template; service names such as "Glamour Full Set" do not contain it


DEMO_HEADER = """# Aria on the {name} showcase: agent prompt (GENERATED)

> Generated by `python -m aria_booking.catalog.render --write` from the shared conversation template and the {name} showcase menu (`aria_booking/showcase`). Do not edit it
> by hand: a test fails if it differs. It has NO booking function: it only checks availability on a prototype calendar with invented data. Only the text from
> the first section heading below onward is sent to the agent.

"""

# The shared template speaks of "the website"; in the showcase the source of services, lengths and prices is the prototype MENU. Each phrase must occur exactly once.
DEMO_WORDING = (
    ("salon's website information", "salon's menu information"),
    ("The website's opening hours are information only", "The example opening hours are information only"),
    ("the listed website prices", "the listed menu prices"),
    ("what the website says a treatment is for", "what the menu says a treatment is for"),
)
TEMPLATE_EXAMPLE_TECHNICIAN = "Lily"  # the first name the shared template uses in its two example sentences; the demo shows a fictional roster name there instead


LIFECYCLE_HEADER = """# Aria on the {name} showcase, simulated appointment lifecycle: agent prompt (GENERATED)

> Generated by `python -m aria_booking.catalog.render --write` from the lifecycle conversation template and the {name} showcase menu (`aria_booking/showcase`). Do not edit it
> by hand: a test fails if it differs. Its functions find, book, move and cancel appointments in a PROTOTYPE calendar with invented data, and none touches any real
> salon calendar. Only the text from the first section heading below onward is sent to the agent.

"""


PREFERENCES_HEADER = """# Aria on the {name} showcase, simulated appointment lifecycle with preferred technicians: agent prompt (GENERATED)

> Generated by `python -m aria_booking.catalog.render --write` from the lifecycle conversation template, the {name} showcase menu (`aria_booking/showcase`) and the 'Preferred
> technicians' section. Do not edit it by hand: a test fails if it differs. DEMO ONLY and opt-in: its functions find, book, move and cancel appointments and keep a preferred
> technician per service in a PROTOTYPE calendar with invented data, and none touches any real salon calendar. Only the text from the first section heading below onward is sent to the agent.

"""


SPANISH_SECTION = """## Spanish (an optional mini-demonstration; not a Spanish booking service)

You speak English. The ONE exception is a caller who speaks Spanish or asks for Spanish: for the three things below, and only those, you may answer in natural, friendly Spanish.

1. **A greeting**, and saying you can help in Spanish with questions about the menu.
2. **Which services we offer**: the same facts as the knowledge section, at most three choices, then one question.
3. **What a named service costs and how long it takes.**

How to do it:

- **Tools and their arguments stay English.** Translate the caller's words into the canonical English service words and an exact length in minutes, and call the tool exactly as you do
  in English: "masaje de tejido profundo de sesenta minutos" is `lookup_service` with the query `deep tissue massage 60 minutes`. Never put a Spanish word, or a guess, in a tool argument.
- **Say what the tool said, in Spanish, without changing a fact.** A tool's `speak` text arrives in English. Give it in Spanish with the same service names (you may add a plain Spanish
  description of the kind of service), the same lengths and the same prices, said as the tool gave them ("ochenta y cinco dólares"). If the tool did not give a price, a length or a service,
  say in Spanish that you don't have it. Never invent a service, a price, a length, a technician or an offer, and never repeat a price you were not given.
- **Spanish stops at the menu.** For checking a time, booking, finding, moving or cancelling an appointment, or a preferred technician: say once, in Spanish, "Para las citas, continuemos en
  inglés", and continue in English for the rest of the call, including every read-back and every yes, so that the exact name, number, date and time are the ones confirmed. Never say that
  you can complete a booking, a change or a cancellation in Spanish, and never turn a name, a number, a date or a time into a different value.
- If the caller says "Gracias, continuemos en inglés" (or anything like it), return to English at once.

"""

MULTILINGUAL_SENTENCE = ("in English, Spanish, or Mandarin Chinese according to the conversation-language rules below", ", in English. One natural conversation")
MULTILINGUAL_SECTION = """## Conversation languages: English, Spanish, and Mandarin Chinese

Start with the existing English opening. When the caller speaks Spanish or Mandarin Chinese, or asks for either language, switch immediately and continue in that language. Return to English whenever requested. These languages apply to menu questions, availability, appointment lookup, booking, rescheduling, cancellation, history, and preferences. If asked whether you speak Spanish or Mandarin, answer briefly in the requested language and ask how you can help. Do not describe yourself as English-only.

All existing calendar, identity, availability, and confirmation rules below remain in force in every language. Language switching never creates a booking or confirms a pending action.

The tools still use English service vocabulary and the existing schema. For lookup_service.query, service_words, service, and service_category, faithfully translate the caller's treatment words into English before calling the tool. This is the language-specific meaning of 'the caller's own words' below: preserve the exact treatment, duration, and level of ambiguity. Spanish 'masaje' and Mandarin '按摩' mean only 'massage', never Swedish or deep tissue. Spanish 'masaje de tejido profundo de sesenta minutos' and Mandarin '六十分钟深层组织按摩' mean 'deep tissue massage 60 minutes'. If the meaning is uncertain, ask rather than choosing a treatment. Use service IDs only from the existing menu or tool result.

Keep client and technician names, phone digits, references, service IDs, confirmation tokens, booleans, and tool field names unchanged. Do not translate or invent a person's name. Ask for spelling or digits when unclear. Resolve dates and times in the salon's America/New_York time zone; clarify ambiguous dates, morning versus afternoon, and time bounds in the caller's language.

Speak tool results and short waiting messages in the caller's chosen language, preserving every fact, price, duration, technician, date, time, uncertainty, and bookability restriction. Translate the full confirmation read-back, including old and new details when rescheduling. Only an explicit affirmative to that exact read-back, such as 'yes', Spanish 'sí, confirma', or Mandarin '是的，确认', permits the existing confirmation call with the unchanged token and details. A language request, a hesitant answer, or changed details is not confirmation. Never say an action succeeded without its verified tool success response.

If recognition or translation is uncertain, clarify in the chosen language. Offer English as an option if needed; do not silently switch languages or silently change treatment, identity, or appointment details.

"""

MULTILINGUAL_HEADER = """# Aria on the {name} showcase, simulated appointment lifecycle with preferred technicians, in English, Spanish and Mandarin Chinese: agent prompt (GENERATED)

> Generated by `python -m aria_booking.catalog.render --write`: the preferred-technicians prompt with the identity sentence widened and ONE 'Conversation languages' section added. This is the
> text the owner applied to the owned test agent on 2026-10-22 at the user's explicit request (English, Spanish and Mandarin for appointments as well as the menu); a test rebuilds it and checks that
> its digest equals the one recorded when it was applied. Do not edit it by hand. DEMO ONLY. Speech recognition, spoken Spanish and Mandarin, faithful translation of treatment words into
> the tools' English vocabulary, and transaction fidelity in these languages are NOT proven: they need supervised calls. The narrower menu-only Spanish variant stays beside it as an alternative.
> Only the text from the first section heading below onward is sent to the agent.

"""


BREVITY_SECTION = "## Brief replies and no spoken prices\n\nThese speech rules apply in English, Spanish, and Mandarin, including when paraphrasing any tool result or knowledge text below.\n- Answer the caller's immediate question in one short sentence whenever possible; use a second sentence only for a needed question or essential explanation. Ask one question, then wait.\n- Do not state prices, currency amounts, price ranges, or fees. Omit price clauses even when a tool's speak text, service description, or confirmation contains them. If asked directly about a price, say briefly that prices are listed on the website.\n- Do not read treatment descriptions unless asked. When a treatment length must be selected, list just the available lengths. Offer at most three relevant choices at once.\n- Avoid repeated acknowledgements, enthusiastic filler, repeated summaries, repeated identity questions once resolved, and routine endings such as 'Is there anything else I can help with?' Answer, then pause. A caller who clearly finishes gets a brief goodbye.\n- For availability, give the requested result and up to three returned alternatives. If unavailable or uncertain, give one brief truthful reason; do not hide uncertainty or claim availability without a tool check.\n- Keep every essential booking or change confirmation: the action, service, duration when relevant, technician, date, time, and old/new details for a move, followed by one clear confirmation question. For preference changes, retain the treatment and therapist. Omit prices only; never omit a required identity clarification or confirmation, change tool arguments, or bypass an explicit yes.\n- Existing language switching, appointment safeguards, and successful-result requirements remain in force. No spoken price rule changes any stored menu or appointment data.\n\n"
BREVITY_ANCHOR = "## Conversation languages: English, Spanish, and Mandarin Chinese\n"
BREVITY_EDITS: tuple[tuple[str, str], ...] = (
    ("Speak tool results and short waiting messages in the caller's chosen language, preserving every fact, price, duration, technician, date, time, uncertainty, and bookability restriction. Translate the full confirmation read-back, including old and new details when rescheduling. Only an explicit affirmative to that exact read-back, such as 'yes', Spanish 'sí, confirma', or Mandarin '是的，确认', permits the existing confirmation call with the unchanged token and details. A language request, a hesitant answer, or changed details is not confirmation. Never say an action succeeded without its verified tool success response.\n",
     "Speak tool results and short waiting messages in the caller's chosen language, preserving the treatment, duration, technician, date, time, uncertainty, and bookability restriction, while omitting prices. Translate the confirmation read-back, including old and new details when rescheduling, omitting only monetary amounts and redundant filler. Only an explicit affirmative to that exact read-back, such as 'yes', Spanish 'sí, confirma', or Mandarin '是的，确认', permits the existing confirmation call with the unchanged token and details. A language request, a hesitant answer, or changed details is not confirmation. Never say an action succeeded without its verified tool success response.\n"),
    ("> <Greeting>! This is Aria at Serenity Spa. I'd love to help you get some relaxation on the calendar. To get started, are you looking for a massage, a facial, or maybe a full spa day?\n",
     "> <Greeting>! This is Aria at Serenity Spa. How can I help?\n"),
    ("- Answer questions such as \"do you offer X\", what it is, how long it takes, what it costs, from the knowledge section. Say prices are\n  the listed menu prices. If a service comes in several lengths, give them and ask which the caller means.\n",
     "- Answer service questions briefly from the knowledge section. If a service comes in several lengths and the caller needs to choose one, name only the lengths and ask which. Do not state prices. If directly asked about cost, say briefly that prices are listed on the website.\n"),
    ("  never touches the calendar. Its answers: `service_info` (give the length and price it says and whether it can be booked), `choose_variant` (several lengths: ask\n",
     "  never touches the calendar. Its answers: `service_info` (give only the requested service detail or relevant length, omit prices, and preserve whether it can be booked), `choose_variant` (several lengths: ask\n"),
    ("- Discussing a service is **not** a request to book. After answering you may ONCE offer: \"Would you like to book an appointment?\" Do not\n",
     "- Discussing a service is **not** a request to book. Answer briefly and wait; do not routinely append a booking offer. Do not\n"),
    ("  `confirmation_required` with a sentence and a `confirmation_token`. Read that sentence to the caller. Only after the caller clearly says yes to **that** sentence call\n",
     "  `confirmation_required` with a sentence and a `confirmation_token`. Read back its appointment details and confirmation question, omitting any price and redundant filler. Only after the caller clearly says yes to **that** sentence call\n"),
    ("- After the caller answers, recommend ONE listed treatment that fits what they said, in a sentence or two, from what the menu says it is for, and give its listed length and price (if it comes in several lengths, give them briefly and ask which). Offer other choices, or the full list, ONLY if the caller asks for more.\n",
     "- After the caller answers, recommend ONE listed treatment that fits what they said in one short sentence. Give the length only when relevant, and omit the price (if a length must be chosen, list the lengths briefly and ask which). Offer other choices, or the full list, ONLY if the caller asks for more.\n"),
    ("Source: the Serenity Spa prototype menu. Its prices, lengths and hours are example data that can be edited. Prices and lengths are the menu's LISTED ones. Use them for questions about services, lengths and prices. If a caller asks about something that is not below, say you do not have that information.\n",
     "Source: the Serenity Spa prototype menu. Its prices, lengths and hours are example data that can be edited. Prices and lengths are the menu's LISTED ones. Use the service facts and lengths when relevant; never speak the prices. If a caller asks about something that is not below, say you do not have that information.\n"),
    ("- Every other service listed above is INFORMATION ONLY for now: you can describe it and give its listed price, but you cannot check availability for it or book it. Say so plainly and suggest the caller contact the salon directly.\n",
     "- Every other service listed above is INFORMATION ONLY for now: you can describe it briefly without prices, but you cannot check availability for it or book it. Say so plainly and suggest the caller contact the salon directly.\n"),
)

BREVITY_HEADER = """# Aria on the {name} showcase, simulated appointment lifecycle with preferred technicians, in English, Spanish and Mandarin Chinese, with brief replies and no spoken prices: agent prompt (GENERATED)

> Generated by `python -m aria_booking.catalog.render --write`: the multilingual prompt with one 'Brief replies and no spoken prices' section and nine small wording edits. This is the text the
> owner applied to the owned test agent on 2026-10-22 at the user's explicit request (shorter conversations, no spoken prices); it supersedes the multilingual-only text as the live one. A test
> rebuilds it and checks that its digest equals the one recorded when it was applied. Do not edit it by hand. DEMO ONLY. Spoken behaviour in these languages and the brevity rules are NOT
> proven offline: they need supervised calls. It changes no stored menu or appointment data and no tool. Only the text from the first section heading below onward is sent to the agent.

"""

SPANISH_HEADER = """# Aria on the {name} showcase, simulated appointment lifecycle with preferred technicians and an optional Spanish mini-demonstration: agent prompt (GENERATED)

> Generated by `python -m aria_booking.catalog.render --write`: the preferred-technicians prompt plus a 'Spanish' section that lets Aria answer a Spanish-speaking caller in Spanish for a
> greeting, the menu and prices ONLY, with English tool arguments and a return to English for any appointment. Do not edit it by hand: a test fails if it differs. DEMO ONLY and opt-in. It is NOT
> a validated Spanish booking service: speech recognition, argument fidelity and spoken Spanish need a supervised call. Only the text from the first section heading below onward is sent to the agent.

"""


MULTILINGUAL_TOOLS_FILE = "tools_demo_lifecycle_preferences_multilingual.json"
MULTILINGUAL_TOOL_OVERRIDES_FILE = "multilingual_tool_overrides.json"  # the live descriptions as recorded by the owner on 2026-10-22 (data; a test checks the builder reproduces them)
MULTILINGUAL_EXEC_SUFFIX = " Deliver this same brief message in the caller's currently selected English, Spanish, or Mandarin language, preserving all names and facts."
MULTILINGUAL_PARAM_SUFFIX = " For Spanish or Mandarin speech, use a faithful English translation of only the caller's treatment words, preserving ambiguity and duration. Never translate person names or invent a treatment."
MULTILINGUAL_PARAM_TARGETS = (
    ("lookup_service", "query"),
    ("check_slot", "service_words"),
    ("find_alternatives", "service_words"),
    ("find_appointments", "service"),
    ("last_completed_visit", "service_category"),
    ("book_appointment", "service_words"),
    ("get_preferences", "service"),
    ("save_preference", "service"),
    ("remove_preference", "service"),
)


def build_multilingual_tools(base: dict) -> dict:
    """The lifecycle-with-preferences tool file with the owner's multilingual wording: each spoken waiting message also says to speak it in the caller's language, and each treatment-words
    argument says to translate only the caller's treatment words faithfully into English. Descriptions only: no name, address, schema, requirement or timeout changes."""
    import copy

    out = copy.deepcopy(base)
    by = {tool["name"]: tool for tool in out["tools"]}
    for tool in out["tools"]:
        if tool.get("speak_during_execution") and tool.get("execution_message_description"):
            tool["execution_message_description"] += MULTILINGUAL_EXEC_SUFFIX
    for name, parameter in MULTILINGUAL_PARAM_TARGETS:
        by[name]["parameters"]["properties"][parameter]["description"] += MULTILINGUAL_PARAM_SUFFIX
    out["_variant"] = "multilingual: English, Spanish and Mandarin Chinese (user-authorized 2026-10-22); descriptions only, see render.build_multilingual_tools"
    return out


def build_demo_prompt(template: str, catalog: Catalog | None = None, salon_name: str | None = None, showcase=None, lifecycle: bool = False, preferences: bool = False,
                      spanish: bool = False, multilingual: bool = False, brief: bool = False) -> str:
    """The availability-only prompt for the Serenity showcase: the shared conversation rules, plus knowledge rendered from the SHOWCASE source.

    The salon facts (name, example hours, no address, phone only when configured) and the services (the independently written Serenity menu) come from the showcase,
    never from the earlier salon's website knowledge. The shared template's identity line and its two example technician names are adapted at rendering time, and
    the result is checked against the one-way list of old identifiers: a leak stops the build. The note above '## Who you are' is not part of the agent prompt.
    """
    from ..demo.dataset import demo_registry
    from ..showcase import SHOWCASE
    from ..showcase.catalog import SHOWCASE_CATALOG
    from ..showcase.leaks import find_leaks
    from ..showcase.roster import name_of

    from dataclasses import replace

    showcase = showcase or SHOWCASE
    if salon_name:
        showcase = replace(showcase, name=salon_name)  # the facts block and the opening both follow the one name
    name = showcase.name
    if preferences and not lifecycle:
        raise ValueError("preferred technicians belong to the lifecycle prompt only")
    if spanish and not (lifecycle and preferences):
        raise ValueError("the Spanish mini-demonstration is an addition to the lifecycle-with-preferences prompt only")
    if multilingual and not (lifecycle and preferences):
        raise ValueError("the multilingual variant is an addition to the lifecycle-with-preferences prompt only")
    if multilingual and spanish:
        raise ValueError("choose the multilingual variant or the menu-only Spanish one, not both")
    if brief and not multilingual:
        raise ValueError("brief replies with no spoken prices are an addition to the multilingual prompt only")
    text = build_prompt(template, catalog=catalog or SHOWCASE_CATALOG, registry=demo_registry(), mode="lifecycle" if lifecycle else "availability",
                        intro=showcase_intro(showcase))
    heading = "## While a tool is working" if lifecycle else "## While the calendar is being checked"
    if text.count(heading) != 1:
        raise ValueError(f"the prompt template must contain the '{heading[3:]}' section exactly once")
    from ..showcase.discovery import discovery_section

    text = text.replace(heading, (LIFECYCLE_SECTION if lifecycle else DEMO_SECTION) + discovery_section(catalog or SHOWCASE_CATALOG, CATEGORY_TITLES)
                        + (PREFERENCES_SECTION if preferences else "") + (SPANISH_SECTION if spanish else "") + heading)
    early = "## Two sources, never mixed"
    if text.count(early) != 1:
        raise ValueError("the prompt template must contain the 'Two sources, never mixed' section exactly once")
    text = text.replace(early, (MULTILINGUAL_SECTION if multilingual else "") + opening_section(name) + early)
    marker = "## Who you are"
    if text.count(marker) != 1:
        raise ValueError("the prompt template must contain the 'Who you are' section exactly once")
    head, body = text.split(marker)
    if lifecycle:
        # the lifecycle template is written for the showcase: it already says "menu", has no legacy example, and uses two placeholders
        body = body.replace("{SALON}", name).replace("{TECH}", name_of("910006")).replace("{OTHER}", name_of("910007"))
        if re.search(r"\{[A-Z]{2,}\}", body):
            raise ValueError("the lifecycle template has a placeholder that was not filled")
        if multilingual:
            new, old = MULTILINGUAL_SENTENCE
            if body.count(old) != 1:
                raise ValueError("the lifecycle template's identity sentence changed: update the multilingual adaptation")
            body = body.replace(old, ", " + new + ". One natural conversation")
        if brief:
            if body.count(BREVITY_ANCHOR) != 1:
                raise ValueError("the multilingual section moved: update the brevity adaptation")
            body = body.replace(BREVITY_ANCHOR, BREVITY_SECTION + BREVITY_ANCHOR)
            for old, new in BREVITY_EDITS:
                if body.count(old) != 1:
                    raise ValueError(f"the prompt wording changed ({old[:50]!r}): update the brevity adaptation")
                body = body.replace(old, new)
    else:
        body = body.replace(SALON_NAME_ON_WEBSITE, name)
        # the shared template illustrates "stated conditions" with a claim from the earlier salon's website; the showcase menu makes no such claim
        legacy = " (for example, that prenatal massage is offered in the second\n  and third trimesters)"
        if body.count(legacy) != 1:
            raise ValueError("the shared template's legacy 'stated conditions' example changed: update the demo prompt adaptation")
        body = body.replace(legacy, ", if the menu lists any")
        for old, new in DEMO_WORDING:
            if body.count(old) != 1:
                raise ValueError(f"the shared template's wording changed ({old!r}): update the demo prompt adaptation")
            body = body.replace(old, new)
        body = re.sub(rf"\b{TEMPLATE_EXAMPLE_TECHNICIAN}\b", name_of("910006"), body)
    leaks = find_leaks(body, allowed=tuple(value for value in (showcase.contact_phone, showcase.location) if value))
    if leaks:
        raise ValueError("the demo prompt still contains the earlier salon's identifiers: " + ", ".join(leaks))
    return (BREVITY_HEADER if brief else MULTILINGUAL_HEADER if multilingual else SPANISH_HEADER if spanish else PREFERENCES_HEADER if preferences else LIFECYCLE_HEADER if lifecycle else DEMO_HEADER).format(name=name) + marker + body


def main(argv: list[str]) -> int:
    from ..config import Config

    registry = BookableRegistry.from_config(Config(business_id="0000000"))
    if "--write" in argv:
        template = (DRAFT_DIR / "prompt_template.md").read_text(encoding="utf-8")
        for mode, name in GENERATED.items():
            (DRAFT_DIR / name).write_text(build_prompt(template, registry=registry, mode=mode), encoding="utf-8", newline="\n")
            print("wrote", DRAFT_DIR / name)
        (DRAFT_DIR / DEMO_PROMPT_FILE).write_text(build_demo_prompt(template), encoding="utf-8", newline="\n")
        print("wrote", DRAFT_DIR / DEMO_PROMPT_FILE)
        lifecycle_template = (DRAFT_DIR / LIFECYCLE_TEMPLATE_FILE).read_text(encoding="utf-8")
        (DRAFT_DIR / LIFECYCLE_PROMPT_FILE).write_text(build_demo_prompt(lifecycle_template, lifecycle=True), encoding="utf-8", newline="\n")
        print("wrote", DRAFT_DIR / LIFECYCLE_PROMPT_FILE)
        (DRAFT_DIR / PREFERENCES_PROMPT_FILE).write_text(build_demo_prompt(lifecycle_template, lifecycle=True, preferences=True), encoding="utf-8", newline="\n")
        print("wrote", DRAFT_DIR / PREFERENCES_PROMPT_FILE)
        (DRAFT_DIR / SPANISH_PROMPT_FILE).write_text(build_demo_prompt(lifecycle_template, lifecycle=True, preferences=True, spanish=True), encoding="utf-8", newline="\n")
        print("wrote", DRAFT_DIR / SPANISH_PROMPT_FILE)
        (DRAFT_DIR / MULTILINGUAL_PROMPT_FILE).write_text(build_demo_prompt(lifecycle_template, lifecycle=True, preferences=True, multilingual=True), encoding="utf-8", newline="\n")
        print("wrote", DRAFT_DIR / MULTILINGUAL_PROMPT_FILE)
        (DRAFT_DIR / BREVITY_PROMPT_FILE).write_text(build_demo_prompt(lifecycle_template, lifecycle=True, preferences=True, multilingual=True, brief=True), encoding="utf-8", newline="\n")
        print("wrote", DRAFT_DIR / BREVITY_PROMPT_FILE)
        tools = json.loads((DRAFT_DIR / "tools.json").read_text(encoding="utf-8"))
        availability = build_availability_tools(tools)
        (DRAFT_DIR / AVAILABILITY_TOOLS_FILE).write_text(json.dumps(availability, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print("wrote", DRAFT_DIR / AVAILABILITY_TOOLS_FILE)
        lifecycle_tools = build_lifecycle_tools(availability)
        (DRAFT_DIR / LIFECYCLE_TOOLS_FILE).write_text(json.dumps(lifecycle_tools, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print("wrote", DRAFT_DIR / LIFECYCLE_TOOLS_FILE)
        (DRAFT_DIR / PREFERENCES_TOOLS_FILE).write_text(json.dumps(build_preference_tools(lifecycle_tools), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print("wrote", DRAFT_DIR / PREFERENCES_TOOLS_FILE)
        multilingual_tools = build_multilingual_tools(build_preference_tools(lifecycle_tools))
        (DRAFT_DIR / MULTILINGUAL_TOOLS_FILE).write_text(json.dumps(multilingual_tools, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print("wrote", DRAFT_DIR / MULTILINGUAL_TOOLS_FILE)
    else:
        print(render_knowledge(registry=registry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
