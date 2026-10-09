"""Which kind of service is a visit, and which kind did the caller ask about? Built from the website catalogue, not from a lash-only path."""

from __future__ import annotations

import re
from typing import Iterable, Optional

from ..catalog.lookup import DEFAULT_CATALOG, Catalog

# Words that name a category in speech or in a service title. Deliberately explicit; anything not covered is "not understood", never guessed.
ALIASES: dict[str, tuple[str, ...]] = {
    "lash": ("lash", "lashes", "eyelash", "eyelashes"),
    "massage": ("massage", "massages"),
    "facial": ("facial", "facials"),
    "waxing": ("wax", "waxing"),
    "permanent-makeup": ("microblading", "permanent makeup", "lip blush", "ombre powder"),
    "head-spa": ("head spa", "scalp"),
    "body-scrub": ("scrub", "body polish"),
    "couples": ("couples", "couple"),
}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _has(phrase: str, text: str) -> bool:
    return f" {phrase} " in f" {text} "


class Classifier:
    def __init__(self, catalog: Catalog = DEFAULT_CATALOG):
        self._families = sorted(((_norm(i.family), i.category) for i in catalog.items), key=lambda pair: -len(pair[0]))
        self._categories = {i.category for i in catalog.items}

    def categories_of(self, service_text: Optional[str]) -> frozenset[str]:
        """Every category a service title belongs to. Empty = not understood (the caller must treat that as uncertain)."""
        text = _norm(service_text or "")
        if not text:
            return frozenset()
        found: set[str] = set()
        for family, category in self._families:
            if _has(family, text):
                found.add(category)
        for category, words in ALIASES.items():
            if any(_has(_norm(word), text) for word in words):
                found.add(category)
        return frozenset(found)

    def category_of_request(self, words: Optional[str]) -> Optional[str]:
        """The ONE category the caller asked about, or None when it is missing, ambiguous or not on the menu."""
        text = _norm(words or "")
        if not text:
            return None
        hits = {category for category, aliases in ALIASES.items() if any(_has(_norm(a), text) for a in aliases)}
        if len(hits) > 1:
            return None
        if hits:
            return next(iter(hits))
        direct = {category for category in self._categories if _norm(category) == text}
        if len(direct) == 1:
            return next(iter(direct))
        named = self.categories_of(words)
        return next(iter(named)) if len(named) == 1 else None

    def known(self) -> Iterable[str]:
        return sorted(self._categories)
