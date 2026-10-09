"""Plain data types for the catalogue. No I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CatalogItem:
    """One orderable unit as the website lists it: a service in ONE duration/price variant.

    ``duration_minutes`` / ``price_usd`` are None when the website does not show them; they are never guessed.
    """

    item_id: str  # stable canonical id, e.g. "massage-swedish-60"; the ONLY way a service is referred to by tools
    family: str  # the service as a customer names it, e.g. "Swedish Massage"
    category: str  # "facial", "massage", "package", ...
    name: str  # display name including the variant, e.g. "Swedish Massage, 1 hour"
    description: str  # a short plain-English paraphrase of the website's description
    duration_minutes: Optional[int]
    price_usd: Optional[int]
    source_url: str
    retrieved_on: str  # ISO date the page was read
    original_price_usd: Optional[int] = None  # a struck-through price the page shows, if any
    notes: str = ""  # conditions or inconsistencies on the page, stated honestly

    def to_public(self) -> dict:
        """What a tool may return about this item. Informational facts only."""
        data = {
            "service_id": self.item_id,
            "name": self.name,
            "category": self.category,
            "duration_minutes": self.duration_minutes,
            "price_usd": self.price_usd,
            "description": self.description,
            "source_url": self.source_url,
            "retrieved_on": self.retrieved_on,
        }
        if self.original_price_usd is not None:
            data["original_price_usd"] = self.original_price_usd
        if self.notes:
            data["notes"] = self.notes
        return data


@dataclass(frozen=True)
class Resolution:
    """The outcome of matching a customer's words against the catalogue.

    kind: ``exact`` (one item), ``variants`` (one service family, several durations/prices: ask which),
    ``ambiguous`` (several different services match: ask which), ``unknown`` (nothing matched).
    """

    kind: str
    items: tuple[CatalogItem, ...] = ()
    hint_unmatched: bool = False  # the customer named a length that no variant of the matched service has
