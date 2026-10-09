"""Render the catalogue into the knowledge section of the voice prompt. The prompt is GENERATED from the catalogue, so a price
or length can never be changed in one place and forgotten in the other.

    python -m aria_booking.catalog.render            print the knowledge section
    python -m aria_booking.catalog.render --write    rebuild booking/voice_agent_draft/prompt_test_only.md from the template
"""

from __future__ import annotations

import sys
from collections import OrderedDict
from pathlib import Path

from .bookable import BookableRegistry
from .lookup import DEFAULT_CATALOG, Catalog
from .website_data import NOT_PUBLISHED, SITE, duration_label

CATEGORY_TITLES = OrderedDict([
    ("facial", "Facials"), ("massage", "Massages"), ("package", "Spa packages"), ("body-scrub", "Body scrubs"), ("lash", "Eyelashes and brows"),
    ("head-spa", "Head spa"), ("waxing", "Waxing"), ("permanent-makeup", "Permanent makeup"), ("couples", "Couples packages"),
    ("membership", "Visit packages (memberships)"),
])
MARKER = "<!-- KNOWLEDGE -->"
DRAFT_DIR = Path(__file__).resolve().parents[2] / "voice_agent_draft"


def _short(minutes) -> str:
    if minutes is None:
        return "length not listed"
    return duration_label(minutes).replace(" hours", " hr").replace(" hour", " hr").replace(" minutes", " min")


def render_knowledge(catalog: Catalog = DEFAULT_CATALOG, registry: BookableRegistry | None = None) -> str:
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
            options = " | ".join(
                (f"package ${v.price_usd}" if v.category == "membership" else f"{_short(v.duration_minutes)} ${v.price_usd}")
                if v.price_usd is not None else f"{_short(v.duration_minutes)}, price not listed"
                + (f" (was ${v.original_price_usd})" if v.original_price_usd is not None else "")
                for v in variants
            )
            notes = " ".join(dict.fromkeys(v.notes for v in variants if v.notes))
            lines.append(f"- {family}: {options}. {variants[0].description}" + (f" Note: {notes}" if notes else ""))
    lines += ["", "### What can be booked online right now", ""]
    if registry is None:
        lines.append("- Nothing. Online booking is not available; tell the caller to contact the salon directly.")
    else:
        for service_id in registry.ids():
            svc = registry.get(service_id)
            if not svc.verified:
                continue
            tag = " (a fictional TEST service for this demo line)" if svc.test_only else ""
            lines.append(f"- service_id `{svc.service_id}`: {svc.booksy_name}, {_short(svc.duration_minutes)}{tag}.")
        lines += [
            "- Every other service listed above is INFORMATION ONLY for now: you can describe it and give its listed price, but you cannot check "
            "availability for it or book it. Say so plainly and suggest the caller contact the salon directly.",
        ]
    return "\n".join(lines) + "\n"


def build_prompt(template: str, catalog: Catalog = DEFAULT_CATALOG, registry: BookableRegistry | None = None) -> str:
    if template.count(MARKER) != 1:
        raise ValueError("the prompt template must contain the knowledge marker exactly once")
    return template.replace(MARKER, render_knowledge(catalog, registry).rstrip("\n"))


def main(argv: list[str]) -> int:
    from ..config import Config

    registry = BookableRegistry.from_config(Config(business_id="0000000"))
    if "--write" in argv:
        template = (DRAFT_DIR / "prompt_template.md").read_text(encoding="utf-8")
        (DRAFT_DIR / "prompt_test_only.md").write_text(build_prompt(template, registry=registry), encoding="utf-8", newline="\n")
        print("wrote", DRAFT_DIR / "prompt_test_only.md")
    else:
        print(render_knowledge(registry=registry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
