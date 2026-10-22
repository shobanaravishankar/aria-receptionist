"""Golden vectors for the menu lookup: a large corpus of customer phrasings resolved by the PYTHON catalogue (the oracle), plus the demo's bookable-service derivation.

Run from the booking folder:  python netlify_showcase/oracle/gen_catalog.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aria_booking.catalog.lookup import duration_hint  # noqa: E402
from aria_booking.demo import dataset as ds  # noqa: E402
from aria_booking.showcase.catalog import SHOWCASE_CATALOG as CAT  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "test" / "vectors"

LENGTH_PHRASES = [
    "30 minutes", "45 minutes", "60 minutes", "90 minutes", "75 minutes", "15 min", "30 min", "an hour", "one hour", "1 hour", "1 hr", "two hours", "2 hours", "three hours",
    "half an hour", "a half hour", "half hour", "an hour and a half", "one and a half hours", "1 and a half hours", "two and a half hours", "2 and a half hours", "1.5 hours",
    "0.5 hours", "0.375 hours", "0.125 hours", "1.25 hr", "3 and a half hours", "four and a half hours", "half a day", "half of an hour", "30 or 60 minutes", "45-minute", "60-minute",
    "90 minute", "1-hour", "an hour or two", "hour and a half", "a half", "half", "ninety minutes", "sixty minutes", "forty five minutes", "20 minutes", "5 minutes", "300 minutes",
]
FILLERS = ["", "I'd like to book a {}", "do you offer {}", "how much is a {}", "can I get {} please", "what's the price of {}", "tell me about your {}", "{} appointment", "I want {} for tomorrow", "the {}"]
WORDS = ["massage", "facial", "wax", "waxing", "lash", "lashes", "brow", "eyebrow", "scrub", "head", "spa", "combo", "couples", "bundle", "membership", "refill", "set", "full", "tinting", "lifting",
         "deep", "tissue", "hot", "stone", "oil", "express", "signature", "swedish", "back", "leg", "arm", "bikini", "brazilian", "lip", "chin", "face", "scalp", "detox", "visit", "visits", "5", "10",
         "2", "3", "4", "week", "weeks", "2-week", "3 week", "4weeks", "initial", "classic", "volume", "natural", "hybrid", "lymphatic", "wood", "therapy", "mini", "x", "zzz", "the", "please", ""]


def resolution(query) -> dict:
    r = CAT.resolve(query)
    return {"kind": r.kind, "ids": [i.item_id for i in r.items], "hintUnmatched": r.hint_unmatched}


def corpus() -> list:
    rng = random.Random(424242)
    queries: list = [None, "", "   ", "nonsense", "swedish massage", "SWEDISH MASSAGE", "  Swedish   Massage  ", "massage", "facial", "wax", "lash", "eyelash", "the", "an hour", "60", "5-visit", "10 visit bundle"]
    queries += [i.item_id for i in CAT.items] + [" " + CAT.items[0].item_id + " ", CAT.items[0].item_id.upper()]
    for item in CAT.items:
        for text in (item.family, item.name, item.family.lower(), item.name.upper(), item.family + " please", "a " + item.family):
            queries.append(text)
        if item.duration_minutes:
            for phrase in (f"{item.duration_minutes} minute {item.family}", f"{item.family} for {item.duration_minutes} minutes", f"{item.family} {item.duration_minutes} min"):
                queries.append(phrase)
    for family in CAT.families():
        for phrase in rng.sample(LENGTH_PHRASES, 14):
            queries.append(f"{family} {phrase}")
            queries.append(f"{phrase} {family.lower()}")
        for filler in FILLERS:
            queries.append(filler.format(family) if filler else family)
    for _ in range(2500):
        parts = rng.sample(WORDS, rng.randint(1, 4))
        if rng.random() < 0.35:
            parts.append(rng.choice(LENGTH_PHRASES))
        rng.shuffle(parts)
        queries.append(rng.choice(FILLERS).format(" ".join(parts)) if rng.random() < 0.4 else " ".join(parts))
    for _ in range(600):
        item = rng.choice(CAT.items)
        words = item.name.replace(",", "").split()
        rng.shuffle(words)
        if rng.random() < 0.5:
            words = words[: rng.randint(1, len(words))]
        queries.append(" ".join(words))
    for family in CAT.families():  # the same length said twice, and contradictory variant words (a variant cannot be both an initial set and a refill)
        queries += [f"{family} an hour or one hour", f"{family} 1 hour or an hour", f"{family} half an hour or a half hour", f"{family} initial refill", f"{family} initial set 2 week refill",
                    f"{family} full set refill 3w", f"{family} refill initial 4 weeks"]
    queries += ["lash refill 2 weeks", "classic full set 2 week refill", "volume 3-week refill", "natural 4 weeks refill", "hybrid initial set", "full set", "refill", "2w", "3w refill",
                "éclair massage", "swedish massage ☃", "swedish massage", "swedish\tmassage\n", "couples massage 1 hour",
                "5 visit bundle", "10-visit bundle", "membership", "bundle", "lip", "lip wax", "wax lip", "full face", "full leg wax 40 minutes", "eyebrows", "eyebrow wax", "eyebrow tinting"]
    seen, unique = set(), []
    for q in queries:
        key = (type(q).__name__, q)
        if key not in seen:
            seen.add(key)
            unique.append(q)
    return unique


def services_vector() -> list:
    out = []
    for service, category in ds.demo_services(ds.ROSTER):
        out.append({"serviceId": service.service_id, "booksyName": service.booksy_name, "duration": service.duration_minutes, "bufferBefore": service.buffer_before_minutes,
                    "bufferAfter": service.buffer_after_minutes, "eligibleIds": sorted(service.eligible_staff_ids), "catalogItem": service.catalog_item, "priceUsd": service.price_usd, "category": category})
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    queries = corpus()
    resolutions = [{"q": q, **resolution(q)} for q in queries]
    hints = [{"q": q, "minutes": duration_hint(q)} for q in queries]
    kinds = {}
    for r in resolutions:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    (OUT / "catalog.json").write_text(json.dumps({"resolutions": resolutions, "hints": hints, "services": services_vector(), "families": CAT.families()}), encoding="utf-8")
    print("wrote catalog.json:", len(queries), "queries;", kinds, "; hint unmatched:", sum(r["hintUnmatched"] for r in resolutions), "; hints found:", sum(h["minutes"] is not None for h in hints))


if __name__ == "__main__":
    main()
