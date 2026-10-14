"""The generic Serenity Spa showcase: ONE place for the brand, the spa's timezone, example hours, contact placeholders and the teal theme.

Everything here is prototype content for demonstrating the voice assistant to any prospective salon. It is not a real business: there is NO address or location,
the phone number is unset until the owner supplies one, and hours and prices are editable example data. The website, the voice prompt and the greeting all read this.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping, Optional


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


@dataclass(frozen=True)
class Showcase:
    name: str = "Serenity Spa"
    tagline: str = "Unhurried time, well spent."
    timezone: str = "America/New_York"
    example_hours: str = "every day, 10:00 AM to 8:00 PM"
    contact_phone: Optional[str] = None  # None until the owner supplies one: nothing is spoken or shown as a number, and none is invented
    location: Optional[str] = None  # deliberately none: the showcase belongs to no town
    prototype_label: str = "Prototype showcase: example content and a simulated calendar"
    theme: Theme = field(default_factory=Theme)


_KEYS = frozenset({"name", "tagline", "timezone", "example_hours", "contact_phone", "location", "prototype_label"})


def load_showcase(path: Optional[str] = None, overrides: Optional[Mapping[str, object]] = None) -> Showcase:
    """The defaults, then an optional JSON file (kept outside Git when it holds a real phone number), then explicit overrides. Unknown keys are refused."""
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
    return replace(Showcase(), **values)


SHOWCASE = Showcase()
