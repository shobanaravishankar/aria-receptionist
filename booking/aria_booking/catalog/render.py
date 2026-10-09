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
MODES = ("booking", "availability")
GENERATED = {"booking": "prompt_test_only.md", "availability": "prompt_availability_only.md"}
AVAILABILITY_TOOLS_FILE = "tools_availability_only.json"

# Descriptions for the availability-only tool file: they only CHECK, and say so. (The booking file keeps tools.json's wording.)
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


def render_knowledge(catalog: Catalog = DEFAULT_CATALOG, registry: BookableRegistry | None = None, mode: str = "booking") -> str:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    lines = [
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
        "",
        "### Services and listed prices",
    ]
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


def build_prompt(template: str, catalog: Catalog = DEFAULT_CATALOG, registry: BookableRegistry | None = None, mode: str = "booking") -> str:
    if template.count(MARKER) != 1:
        raise ValueError("the prompt template must contain the knowledge marker exactly once")
    return apply_mode(template, mode).replace(MARKER, render_knowledge(catalog, registry, mode).rstrip("\n"))


def build_availability_tools(tools: dict) -> dict:
    """tools.json minus every writer, with descriptions that say the functions only check. Nothing here can book."""
    kept = []
    for tool in tools["tools"]:
        if tool["name"] == "book_slot":
            continue
        tool = dict(tool)
        if tool["name"] in AVAILABILITY_DESCRIPTIONS:
            tool["description"] = AVAILABILITY_DESCRIPTIONS[tool["name"]]
        kept.append(tool)
    out = {k: v for k, v in tools.items() if k != "tools"}
    out["_status"] = "AVAILABILITY-ONLY demo tools: no booking function exists in this file. " + tools["_status"]
    out["tools"] = kept
    return out


def main(argv: list[str]) -> int:
    from ..config import Config

    registry = BookableRegistry.from_config(Config(business_id="0000000"))
    if "--write" in argv:
        template = (DRAFT_DIR / "prompt_template.md").read_text(encoding="utf-8")
        for mode, name in GENERATED.items():
            (DRAFT_DIR / name).write_text(build_prompt(template, registry=registry, mode=mode), encoding="utf-8", newline="\n")
            print("wrote", DRAFT_DIR / name)
        tools = json.loads((DRAFT_DIR / "tools.json").read_text(encoding="utf-8"))
        (DRAFT_DIR / AVAILABILITY_TOOLS_FILE).write_text(json.dumps(build_availability_tools(tools), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print("wrote", DRAFT_DIR / AVAILABILITY_TOOLS_FILE)
    else:
        print(render_knowledge(registry=registry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
