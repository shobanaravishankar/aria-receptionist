"""The generic Serenity Spa showcase: ONE place for the brand, the spa's timezone, example hours, contact placeholders and the teal theme.

Everything here is prototype content for demonstrating the voice assistant to any prospective salon. It is not a real business: there is NO address or location,
the phone number is unset until the owner supplies one, and hours and prices are editable example data. The website, the voice prompt and the greeting all read this.

The fictional address and phone (``location``, ``contact_phone``) come from ONE optional JSON file named by ``ARIA_SHOWCASE_CONFIG`` and kept outside Git; ``resolve_showcase`` is the one
place that finds, reads and checks it, and the page build, the server's contact fallback, the voice prompt and the agent-update check all take the same result. Unset stays unset.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping, Optional

from ..history.phone import normalize_phone

LIFECYCLE_BOOKING_NOTE = "Appointments made here exist only in this prototype calendar"  # the page label's ending when the simulated lifecycle is on
ENV_VAR = "ARIA_SHOWCASE_CONFIG"
MAX_LOCATION = 120
MAX_NAME = 60


@dataclass(frozen=True)
class Theme:
    """Teal-led palette with light neutral surfaces. Contrast of text on its background is checked by a test, not by eye."""

    teal_900: str = "#0b3d3b"
    teal_700: str = "#0f6b68"
    teal_600: str = "#13807c"
    teal_500: str = "#1a9a95"
    teal_100: str = "#d9f0ee"
    teal_50: str = "#eef8f7"
    sand_100: str = "#f6f1ea"
    sand_200: str = "#ece3d6"
    ink: str = "#18302f"
    ink_soft: str = "#46605e"
    white: str = "#ffffff"
    mist: str = "#e8f5f3"  # light text on the dark teal surfaces
    mist_dim: str = "#cfe8e5"
    mist_faint: str = "#a9cfcb"
    amber: str = "#c98a1b"  # call status colours
    green: str = "#1f9d6b"
    red: str = "#b3382c"


@dataclass(frozen=True)
class Showcase:
    name: str = "Serenity Spa"
    tagline: str = "Unhurried time, well spent."
    timezone: str = "America/New_York"
    example_hours: str = "every day, 10:00 AM to 8:00 PM"
    contact_phone: Optional[str] = None  # None until the owner supplies one: nothing is spoken or shown as a number, and none is invented
    contact_name: Optional[str] = None  # the name spoken with that number in a fallback ("contact <name> at ..."); None = the brand name
    location: Optional[str] = None  # deliberately none: the showcase belongs to no town
    demo_note: str = "Demonstration site. Appointments and business details are sample data."  # the ONE discreet note in the page footer; nothing else on the page says it
    prototype_label: str = "Prototype showcase: example content and a simulated calendar"  # no longer shown on the page (kept so existing files and callers still load)
    booking_note: str = "Booking is not part of this prototype"  # no longer shown on the page (kept for the same reason)
    theme: Theme = field(default_factory=Theme)


_KEYS = frozenset({"name", "tagline", "timezone", "example_hours", "contact_phone", "contact_name", "location", "demo_note", "prototype_label", "booking_note"})


def _line(value: object, field_name: str, limit: int, *, required: bool = False) -> Optional[str]:
    """One printable line of at most ``limit`` characters, trimmed. An empty optional value is unset. Nothing about the value is echoed in a refusal."""
    if value is None or (isinstance(value, str) and not value.strip() and not required):
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"the showcase {field_name} must be text")
    text = value.strip()
    if len(text) > limit or re.search(r"[\x00-\x1f\x7f]", text):
        raise ValueError(f"the showcase {field_name} must be one line of at most {limit} characters")
    return text


def check_showcase(showcase: Showcase) -> Showcase:
    """The values the page, the prompt and the spoken fallback all print or say, checked once: text is one short line, a phone number is complete (area code, or a + and a country code)."""
    for key in ("tagline", "timezone", "example_hours", "demo_note", "prototype_label", "booking_note"):
        if not isinstance(getattr(showcase, key), str):
            raise ValueError(f"the showcase {key} must be text")
    phone = _line(showcase.contact_phone, "contact_phone", 40)
    if phone is not None and normalize_phone(phone) is None:
        raise ValueError("the showcase contact_phone is not a complete phone number (include the area code, or a + and the country code)")
    return replace(
        showcase, name=_line(showcase.name, "name", MAX_NAME, required=True), contact_phone=phone, contact_name=_line(showcase.contact_name, "contact_name", MAX_NAME),
        location=_line(showcase.location, "location", MAX_LOCATION),
    )


def load_showcase(path: Optional[str] = None, overrides: Optional[Mapping[str, object]] = None, base: Optional[Showcase] = None) -> Showcase:
    """The defaults (or ``base``), then an optional JSON file (kept outside Git when it holds a real phone number), then explicit overrides. Unknown keys are refused; the result is checked."""
    values: dict = {}
    if path:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"the showcase file could not be read ({type(exc).__name__})") from None
        if not isinstance(raw, dict):
            raise ValueError("the showcase file must hold one JSON object")
        unknown = sorted(k for k in raw if k not in _KEYS and not str(k).startswith("_"))
        if unknown:
            raise ValueError("unknown showcase keys: " + ", ".join(unknown))
        values.update({k: v for k, v in raw.items() if k in _KEYS})
    values.update({k: v for k, v in (overrides or {}).items() if k in _KEYS and v is not None})
    return check_showcase(replace(base or Showcase(), **values))


def resolve_showcase(environ: Optional[Mapping[str, str]] = None, *, path: Optional[str] = None, lifecycle: bool = False) -> Showcase:
    """THE showcase: the optional file (``path``, else ``ARIA_SHOWCASE_CONFIG`` in ``environ``) over the defaults, with the lifecycle label ending when the simulated lifecycle is on.
    Every consumer calls this, so the page, the spoken fallback, the prompt and the update check can never disagree. With no file, nothing is configured."""
    chosen = (path or (environ or {}).get(ENV_VAR) or "").strip()
    return load_showcase(chosen or None, base=replace(Showcase(), booking_note=LIFECYCLE_BOOKING_NOTE) if lifecycle else None)


SHOWCASE = Showcase()
