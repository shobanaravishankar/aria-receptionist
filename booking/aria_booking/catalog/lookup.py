"""Match a customer's words to the catalogue. Pure; no calendar, no network.

The point is to NEVER guess: if the words fit several different services, or one service with several lengths, the answer
says so (``ambiguous`` / ``variants``) and the caller must be asked. A length the customer names that no variant has is
reported (``hint_unmatched``), not silently replaced by a different length.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable, Optional

from .models import CatalogItem, Resolution
from .website_data import ALL_ITEMS, slug

FILLER = frozenset(
    "a an the of for with and or to please i id i'd would like want wish book booking appointment appointments schedule do you does have "
    "offer offering how much is are what whats your service services treatment treatments session sessions price cost prices costs about "
    "tell me can could get need some any it there this that one".split()
)
_UNIT_MIN = re.compile(r"(\d+)\s*[- ]?\s*(?:min|mins|minute|minutes)\b")
_UNIT_HR = re.compile(r"(\d+(?:\.\d+)?)\s*[- ]?\s*(?:hr|hrs|hour|hours)\b")
_WORDS = {"half hour": 30, "half an hour": 30, "hour and a half": 90, "an hour and a half": 90, "one hour": 60, "an hour": 60, "two hours": 120, "one and a half hours": 90}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("'", ""))


def duration_hint(query: str) -> Optional[int]:
    """A length the customer mentioned, in minutes, or None. Only explicit units or phrases count (a bare number does not)."""
    low = query.lower()
    if len(re.findall(r"\d+(?:\.\d+)?", low)) > 1:
        return None  # two numbers ("30 or 60 minutes"): the customer has not chosen, so do not pick for them
    for phrase, minutes in _WORDS.items():
        if phrase in low:
            return minutes
    found = [int(m.group(1)) for m in _UNIT_MIN.finditer(low)]
    found += [int(round(float(m.group(1)) * 60)) for m in _UNIT_HR.finditer(low)]
    return found[0] if len(found) == 1 else None


def _strip_lengths(query: str) -> str:
    low = query.lower()
    for phrase in sorted(_WORDS, key=len, reverse=True):
        low = low.replace(phrase, " ")
    low = _UNIT_MIN.sub(" ", low)
    return _UNIT_HR.sub(" ", low)


class Catalog:
    def __init__(self, items: Iterable[CatalogItem] = ALL_ITEMS):
        self.items: tuple[CatalogItem, ...] = tuple(items)
        self.by_id = {i.item_id: i for i in self.items}
        if len(self.by_id) != len(self.items):
            raise ValueError("duplicate catalogue ids")
        self._families: dict[str, list[CatalogItem]] = defaultdict(list)
        for item in self.items:
            self._families[item.family].append(item)
        self._family_words = {name: frozenset(_words(name)) for name in self._families}

    def get(self, item_id: str) -> Optional[CatalogItem]:
        return self.by_id.get(item_id) if isinstance(item_id, str) else None

    def families(self) -> list[str]:
        return list(self._families)

    def resolve(self, query: str) -> Resolution:
        if not isinstance(query, str) or not query.strip():
            return Resolution("unknown")
        direct = self.get(query.strip())
        if direct is not None:
            return Resolution("exact", (direct,))
        hint = duration_hint(query)
        wanted = [w for w in _words(_strip_lengths(query)) if w not in FILLER and not w.isdigit()]  # a bare number is a length, not a name
        if not wanted:
            return Resolution("unknown")
        wanted_set = set(wanted)
        matching = [name for name, words in self._family_words.items() if wanted_set <= words]
        if len(matching) > 1:  # "Lymphatic Drainage Massage" also fits "... with Wood Therapy": an exact name wins
            exact = [name for name in matching if slug(name) == slug(" ".join(wanted))]
            if len(exact) == 1:
                matching = exact
        if not matching:
            return Resolution("unknown")
        if len(matching) > 1:
            return Resolution("ambiguous", tuple(self._families[name][0] for name in matching))
        variants = self._families[matching[0]]
        if hint is not None:
            chosen = [v for v in variants if v.duration_minutes == hint]
            if len(chosen) == 1:
                return Resolution("exact", (chosen[0],))
            return Resolution("variants", tuple(variants), hint_unmatched=True)
        return Resolution("exact", (variants[0],)) if len(variants) == 1 else Resolution("variants", tuple(variants))


DEFAULT_CATALOG = Catalog()
