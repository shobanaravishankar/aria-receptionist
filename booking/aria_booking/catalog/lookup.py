"""Match a customer's words to the catalogue. Pure; no calendar, no network.

The point is to NEVER guess: if the words fit several different services, or one service with several lengths or named variants
(for example 2-week and 3-week refills), the answer says so (``ambiguous`` / ``variants``) and the caller must be asked. A length the
customer names that no variant has is reported (``hint_unmatched``), not silently replaced by a different length. A spoken length is
read by LONGEST match ("one and a half hours" is 90, never the "half hour" inside it), and a length phrase the code does not
understand yields no length at all, never a shorter one.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Callable, Iterable, Optional

from .models import CatalogItem, Resolution
from .website_data import ALL_ITEMS, duration_label, slug

FILLER = frozenset(
    "a an the of for with and or to please i id i'd would like want wish book booking appointment appointments schedule do you does have "
    "offer offering how much is are what whats your service services treatment treatments session sessions price cost prices costs about "
    "tell me can could get need some any it there this that one".split()
)
# Every way a length can be said, as (pattern, minutes). Matches are reconciled by longest span, so a phrase never loses to a piece of itself.
_LENGTHS: list[tuple[re.Pattern, Optional[int]]] = [
    (re.compile(rf"\b{p}\b"), m)
    for p, m in [
        (r"two\s+and\s+a\s+half\s+hours?", 150), (r"2\s+and\s+a\s+half\s+hours?", 150),
        (r"(?:one|1)\s+and\s+a\s+half\s+hours?", 90), (r"(?:an?\s+)?hours?\s+and\s+a\s+half", 90),
        (r"half\s+an\s+hour", 30), (r"(?<!and a )(?<!and )(?:a\s+)?half\s+hours?", 30),  # never the tail of "<number> and a half hours"
        (r"(?:one|an?)\s+hours?", 60), (r"two\s+hours?", 120), (r"three\s+hours?", 180),
        (r"(\d+(?:\.\d+)?)\s*-?\s*(?:hr|hrs|hours?)", None),  # digits + hours: computed
        (r"(\d+)\s*-?\s*(?:min|mins|minutes?)", None),  # digits + minutes: computed
    ]
]
_HOURS_RE, _MINS_RE = _LENGTHS[-2][0], _LENGTHS[-1][0]
# Minutes said in words ("sixty minute massage"): a transcript spells a number out as often as it writes digits, and a length that is not understood must never be left behind as if it
# were part of a service's name.
_SPOKEN_MINUTES = {
    "ten": 10, "fifteen": 15, "twenty": 20, "twenty five": 25, "thirty": 30, "thirty five": 35, "forty": 40, "forty five": 45, "fifty": 50, "fifty five": 55,
    "sixty": 60, "sixty five": 65, "seventy": 70, "seventy five": 75, "eighty": 80, "ninety": 90,
}
_SPOKEN_WORD = r"(?:ten|fifteen|(?:twenty|thirty|forty|fifty|sixty|seventy)(?:[\s-]+five)?|eighty|ninety)"
_SPOKEN_RE = re.compile(rf"\b({_SPOKEN_WORD})\s*-?\s*(?:min|mins|minutes?)\b")
_SPOKEN_ANY = re.compile(rf"\b{_SPOKEN_WORD}\b")
_WEEKS = re.compile(r"\b(\d+)\s*-?\s*weeks?\b")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("'", ""))


def _norm(text: str) -> str:
    """Lowercase, single-spaced, with 'N week(s)' turned into the single token 'Nw' so refills ('2-week refill') can be matched."""
    return " ".join(_WEEKS.sub(r"\1w", text.lower()).split())


def _length_matches(low: str) -> list[tuple[int, int, int]]:
    """(start, end, minutes) for every spoken length, with any match inside a longer one dropped."""
    found = []
    for pattern, minutes in _LENGTHS:
        for m in pattern.finditer(low):
            if minutes is None:
                value = float(m.group(1))
                minutes_here = int(round(value * 60)) if pattern is _HOURS_RE else int(value)
            else:
                minutes_here = minutes
            found.append((m.start(), m.end(), minutes_here))
    for m in _SPOKEN_RE.finditer(low):
        found.append((m.start(), m.end(), _SPOKEN_MINUTES[re.sub(r"[\s-]+", " ", m.group(1))]))
    return [a for a in found if not any(b is not a and b[0] <= a[0] and a[1] <= b[1] and (b[1] - b[0]) > (a[1] - a[0]) for b in found)]


def duration_hint(query: str) -> Optional[int]:
    """A length the customer mentioned, in minutes, or None. None also when the words are unclear or name several lengths:
    only an unambiguous, understood length counts, and a bare number never does."""
    if not isinstance(query, str):
        return None
    low_no_weeks = re.sub(r"\b\d+w\b", " ", _norm(query))  # "2w" is a refill's name, not a length
    if len(re.findall(r"\d+(?:\.\d+)?", low_no_weeks)) > 1:
        return None  # two numbers ("30 or 60 minutes"): the customer has not chosen, so do not pick for them
    matches = _length_matches(low_no_weeks)
    uncovered_half = [m for m in re.finditer(r"\bhalf\b", low_no_weeks) if not any(s <= m.start() and m.end() <= e for s, e, _ in matches)]
    if uncovered_half:
        return None  # "half" in a phrase we do not understand: never guess a shorter length
    if any(not any(s <= m.start() and m.end() <= e for s, e, _ in matches) for m in _SPOKEN_ANY.finditer(low_no_weeks)):
        return None  # "thirty or sixty minutes": a second number said in words, so the customer has not chosen
    distinct = {minutes for _s, _e, minutes in matches}
    return distinct.pop() if len(distinct) == 1 and len(matches) == 1 else None


# Length-like phrases we do NOT turn into minutes (duration_hint returns None for them) must still not be mistaken for part of a service
# name: "deep tissue massage three and a half hours" is a deep tissue massage of an unclear length, not an unknown service.
_NUMBER = r"(?:\d+(?:\.\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|an?)"
_UNKNOWN_LENGTHS = [
    re.compile(rf"\b{_NUMBER}\s+and\s+a\s+half\s+(?:hours?|hrs?)\b"),
    re.compile(rf"\b{_NUMBER}\s+(?:hours?|hrs?|minutes?|mins?)\b"),
    re.compile(r"\bhalf\s+(?:a|an|of\s+an?)\s+(?:hours?|day)\b"),
]


def _strip_lengths(query: str) -> str:
    low = _norm(query)
    for start, end, _m in sorted(_length_matches(re.sub(r"\b\d+w\b", lambda m: " " * len(m.group(0)), low)), reverse=True):
        low = low[:start] + " " + low[end:]
    for pattern in _UNKNOWN_LENGTHS:
        low = pattern.sub(" ", low)
    return _SPOKEN_ANY.sub(" ", low)  # a number said in words that no length phrase took ("thirty or sixty minute massage") is not part of a service's name either


def strip_lengths(query: str) -> str:
    """The customer's words with every length they said removed (lower case, single-spaced): what is left names the service."""
    return _strip_lengths(query) if isinstance(query, str) else ""


def descriptive_label(item: CatalogItem) -> str:
    """A variant name that says more than the length ('2-week refill', 'initial set'); empty when it is just the length."""
    label = item.variant_label
    if not label or (item.duration_minutes and label == duration_label(item.duration_minutes)):
        return ""
    return label


class Catalog:
    def __init__(self, items: Iterable[CatalogItem] = ALL_ITEMS, listed_as: Optional[Callable[[CatalogItem], str]] = None):
        self.items: tuple[CatalogItem, ...] = tuple(items)
        # How a price and length are described aloud. The website catalogue says where it read them and when; a prototype menu says it is just the menu.
        self.listed_as: Callable[[CatalogItem], str] = listed_as or (lambda item: f"as listed on our website ({item.retrieved_on})")
        self.by_id = {i.item_id: i for i in self.items}
        if len(self.by_id) != len(self.items):
            raise ValueError("duplicate catalogue ids")
        self._families: dict[str, list[CatalogItem]] = defaultdict(list)
        for item in self.items:
            self._families[item.family].append(item)
        self._family_words = {name: frozenset(_words(_norm(name))) for name in self._families}
        self._label_words = {id(i): frozenset(_words(_norm(descriptive_label(i)))) for i in self.items}
        self._variant_words = {name: frozenset().union(*(self._label_words[id(i)] for i in items)) for name, items in self._families.items()}

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
        wanted = [w for w in _words(_strip_lengths(query)) if w not in FILLER and not w.isdigit()]
        if not wanted:
            return Resolution("unknown")
        wanted_set = set(wanted)
        matching = [name for name in self._families if wanted_set <= (self._family_words[name] | self._variant_words[name])]
        if len(matching) > 1:  # "Lymphatic Drainage Massage" also fits "... with Wood Therapy": an exact name wins
            exact = [name for name in matching if slug(name) == slug(" ".join(wanted))]
            if len(exact) == 1:
                matching = exact
        if not matching:
            return Resolution("unknown")
        if len(matching) > 1:
            return Resolution("ambiguous", tuple(self._families[name][0] for name in matching))
        family = matching[0]
        variants = list(self._families[family])
        named = wanted_set & self._variant_words[family] - self._family_words[family]  # words that pick a variant, e.g. "refill", "2w"
        if named:
            narrowed = [v for v in variants if named <= self._label_words[id(v)]]
            variants = narrowed or variants
        if hint is not None:
            chosen = [v for v in variants if v.duration_minutes == hint]
            if len(chosen) == 1:
                return Resolution("exact", (chosen[0],))
            if chosen:
                return Resolution("variants", tuple(chosen))
            return Resolution("variants", tuple(variants), hint_unmatched=True)
        return Resolution("exact", (variants[0],)) if len(variants) == 1 else Resolution("variants", tuple(variants))


DEFAULT_CATALOG = Catalog()
