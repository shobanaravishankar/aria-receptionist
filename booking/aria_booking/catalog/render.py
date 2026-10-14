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
    return [
        "## What you may say about the salon (the prototype menu's information)",
        "",
        f"Source: the {showcase.name} prototype menu. Its prices, lengths and hours are example data that can be edited. Prices and lengths are the menu's LISTED ones. "
        "Use them for questions about services, lengths and prices. If a caller asks about something that is not below, say you do not have that information.",
        "",
        f"- Salon: {showcase.name}.",
        "- Address and location: none is configured. Do not state, guess or look up an address, town or directions; say you do not have one to give.",
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
    from ..appointments.tool_defs import LIFECYCLE_DESCRIPTIONS, LIFECYCLE_TOOLS

    kept = []
    for tool in availability_tools["tools"]:
        tool = dict(tool)
        if tool["name"] in LIFECYCLE_DESCRIPTIONS:
            tool["description"] = LIFECYCLE_DESCRIPTIONS[tool["name"]]
        kept.append(tool)
    out = {k: v for k, v in availability_tools.items() if k != "tools"}
    out["_status"] = LIFECYCLE_TOOLS_STATUS
    out["tools"] = kept + [json.loads(json.dumps(tool)) for tool in LIFECYCLE_TOOLS]
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


def build_demo_prompt(template: str, catalog: Catalog | None = None, salon_name: str | None = None, showcase=None, lifecycle: bool = False) -> str:
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
    text = build_prompt(template, catalog=catalog or SHOWCASE_CATALOG, registry=demo_registry(), mode="lifecycle" if lifecycle else "availability",
                        intro=showcase_intro(showcase))
    heading = "## While a tool is working" if lifecycle else "## While the calendar is being checked"
    if text.count(heading) != 1:
        raise ValueError(f"the prompt template must contain the '{heading[3:]}' section exactly once")
    from ..showcase.discovery import discovery_section

    text = text.replace(heading, (LIFECYCLE_SECTION if lifecycle else DEMO_SECTION) + discovery_section(catalog or SHOWCASE_CATALOG, CATEGORY_TITLES) + heading)
    early = "## Two sources, never mixed"
    if text.count(early) != 1:
        raise ValueError("the prompt template must contain the 'Two sources, never mixed' section exactly once")
    text = text.replace(early, opening_section(name) + early)
    marker = "## Who you are"
    if text.count(marker) != 1:
        raise ValueError("the prompt template must contain the 'Who you are' section exactly once")
    head, body = text.split(marker)
    if lifecycle:
        # the lifecycle template is written for the showcase: it already says "menu", has no legacy example, and uses two placeholders
        body = body.replace("{SALON}", name).replace("{TECH}", name_of("910006"))
        if re.search(r"\{[A-Z]{2,}\}", body):
            raise ValueError("the lifecycle template has a placeholder that was not filled")
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
    leaks = find_leaks(body, allowed=(showcase.contact_phone,) if showcase.contact_phone else ())
    if leaks:
        raise ValueError("the demo prompt still contains the earlier salon's identifiers: " + ", ".join(leaks))
    return (LIFECYCLE_HEADER if lifecycle else DEMO_HEADER).format(name=name) + marker + body


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
        tools = json.loads((DRAFT_DIR / "tools.json").read_text(encoding="utf-8"))
        availability = build_availability_tools(tools)
        (DRAFT_DIR / AVAILABILITY_TOOLS_FILE).write_text(json.dumps(availability, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print("wrote", DRAFT_DIR / AVAILABILITY_TOOLS_FILE)
        (DRAFT_DIR / LIFECYCLE_TOOLS_FILE).write_text(json.dumps(build_lifecycle_tools(availability), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print("wrote", DRAFT_DIR / LIFECYCLE_TOOLS_FILE)
    else:
        print(render_knowledge(registry=registry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
