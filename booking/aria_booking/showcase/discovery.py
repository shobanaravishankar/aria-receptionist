"""How Aria answers 'what kinds of massage / facials / waxing... do you have': a FEW featured choices, ONE simple question about what the caller wants, a wait, and then ONE recommendation.

Nothing here ranks anything. The featured examples are editorial: unless a group is configured below, they are simply the first three on the menu, and the section that tells the
voice agent about them says plainly that they are examples, not popularity. The recommendation itself is made from the menu's own descriptions, so no treatment, variation or
claim can be invented, and nothing here diagnoses or promises a result.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Mapping, Optional

from ..catalog.lookup import duration_hint, strip_lengths

FEATURED_LIMIT = 3

# category -> the ONE goal question asked after the featured choices (every category the menu has needs one: a test checks)
QUESTIONS = {
    "massage": "Are you looking to relax, ease everyday muscle tension, or focus on something else?",
    "facial": "Are you thinking about hydration, brightness, or the appearance of fine lines?",
    "lash": "Are you thinking about your lashes or your brows, and what kind of look do you want?",
    "waxing": "Which area would you like waxed?",
    "head-spa": "Are you after a calming scalp massage, or a deeper cleanse for your scalp?",
    "body-scrub": "Is this for your whole body or just your back, and do you want a polish or something more relaxing?",
    "package": "Is there one treatment you'd like to start with, and how much time do you have?",
    "couples": "Would the two of you like massages, facials, or a mix?",
    "membership": "How often do you expect to visit?",
}

# category -> the family names to feature, when the first three on the menu are not the best examples to give. Editorial, NOT popularity data.
FEATURED: Mapping[str, tuple[str, ...]] = {
    "massage": ("Swedish Massage", "Deep Tissue Massage", "Hot Stone Massage"),
    "facial": ("Express Facial", "Signature Facial", "Deep Hydration Facial"),
}


# How a caller names a whole group (never a specific treatment: "Swedish massage" is not a group word, "massage" is).
GROUP_WORDS = {
    "massage": ("massage", "massages"),
    "facial": ("facial", "facials"),
    "lash": ("lash", "lashes", "eyelash", "eyelashes", "lash and brow", "lashes and brows", "eyelashes and brows"),
    "waxing": ("wax", "waxing", "waxes"),
    "head-spa": ("head spa", "head spas", "scalp", "scalp treatment", "scalp treatments"),
    "body-scrub": ("body scrub", "body scrubs", "scrub", "scrubs", "body polish", "body polishes"),
    "package": ("combo", "combos", "package", "packages", "spa package", "spa packages"),
    "couples": ("couples", "couple", "couples treatment", "couples treatments", "couples package", "couples packages"),
    "membership": ("membership", "memberships", "bundle", "bundles", "visit bundle", "visit bundles", "visit package", "visit packages"),
}
# how a group reads after "We have 11 kinds of ..."
LABELS = {
    "massage": "massage", "facial": "facials", "lash": "lash and brow services", "waxing": "waxing", "head-spa": "head spa treatments", "body-scrub": "body scrubs",
    "package": "combos", "couples": "couples treatments", "membership": "visit bundles",
}
FILLER = frozenset(
    "what which kinds kind types type sorts sort of do you have offer a an the any options option are there is available services service treatments treatment your "
    "can i get tell me about show list all different variety varieties choices choice and or for please "
    "d ll m ve to like want would love looking book booking appointment appointments schedule need interested".split()  # how a request is worded ("I'd like a massage")
)


def _core(text: str) -> str:
    """The words that name a group: lower case, no punctuation, no filler ('what kinds of ... do you have')."""
    import re

    return " ".join(w for w in re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split() if w not in FILLER)


def group_of(query: str) -> Optional[str]:
    """The ONE group a caller is naming as a whole ('what kinds of facials do you have', 'massage', 'head spa'), or None. A specific treatment ('swedish massage') is not a group."""
    rest = _core(strip_lengths(query))  # "a sixty minute massage" is still the group 'massage': the length is kept apart, not allowed to hide the group
    if not rest:
        return None
    hits = [category for category, names in GROUP_WORDS.items() if rest in {_core(n) for n in names}]
    return hits[0] if len(hits) == 1 else None


def make_group_answer(catalog, titles: Mapping[str, str], configured: Optional[Mapping[str, tuple[str, ...]]] = None):
    """The ``lookup_service`` hook of the demo: a bare group question gets at most three featured examples and the one question, whatever the model does with its own prompt."""

    def answer(query: str) -> Optional[dict]:
        category = group_of(query)
        if category is None or not families(catalog, category) or category not in QUESTIONS:
            return None
        names = featured(catalog, category, configured)
        total = len(families(catalog, category))
        if len(names) == 1:
            examples = names[0]
        else:
            examples = ", ".join(names[:-1]) + " or " + names[-1]
        reply = {
            "status": "service_group", "category": category, "featured": names, "question": QUESTIONS[category], "total_kinds": total,
            "speak": f"We have {total} kinds of {LABELS[category]}. For example, {examples}. {QUESTIONS[category]}",
            "instruction": "Say this sentence, then STOP and wait for the caller's answer. Do not list more kinds unless the caller asks for them; then recommend ONE treatment from the menu.",
        }
        minutes = duration_hint(query)
        if minutes is not None:  # the caller already gave a length: keep it, but a length does not choose a treatment
            reply["requested_minutes"] = minutes
            reply["instruction"] += (
                f" The caller asked for {minutes} minutes: remember that length, but it does NOT choose a treatment. Do not pick one yourself and do not check the calendar "
                "until the caller has chosen which treatment they want."
            )
        return reply

    return answer


def families(catalog, category: str) -> list[str]:
    """The menu's family names for one category, in menu order."""
    return list(OrderedDict.fromkeys(item.family for item in catalog.items if item.category == category))


def featured(catalog, category: str, configured: Optional[Mapping[str, tuple[str, ...]]] = None) -> list[str]:
    """At most three family names for this category: the configured ones (each must be on the menu), otherwise the first three on the menu."""
    configured = FEATURED if configured is None else configured
    on_menu = families(catalog, category)
    if category in configured:
        chosen = list(configured[category])
        unknown = [name for name in chosen if name not in on_menu]
        if unknown:
            raise ValueError(f"featured {category} choices are not on the menu: " + ", ".join(unknown))
        if not 1 <= len(chosen) <= FEATURED_LIMIT:
            raise ValueError(f"a featured list holds one to {FEATURED_LIMIT} choices")
        return chosen
    return on_menu[:FEATURED_LIMIT]


def discovery_section(catalog, titles: Mapping[str, str], configured: Optional[Mapping[str, tuple[str, ...]]] = None) -> str:
    """The generated prompt section. ``titles`` maps category -> the group's name as the knowledge section shows it (CATEGORY_TITLES)."""
    lines = [
        "## When a caller asks what kinds of a treatment you have",
        "",
        "- A caller may ask what kinds of massage, facials, lashes, waxing, head spa, body scrubs, couples treatments or any other group you offer. Name AT MOST THREE choices from the "
        "featured list for that group below, in one short sentence, then STOP. Ask the ONE question given for that group, and WAIT for the answer. Do not read out the whole group, do "
        "not describe the treatments you did not name, and do not start the next sentence until the caller has answered.",
        "- When the caller names a whole group, `lookup_service` answers `service_group`: say its `speak` sentence (at most three examples and the one question), then STOP and wait. "
        "If it ever answers `choose_service` for a broad word, do NOT read its list: follow this rule instead. Use `lookup_service` for a specific treatment.",
        "- After the caller answers, recommend ONE listed treatment that fits what they said, in a sentence or two, from what the menu says it is for, and give its listed length and "
        "price (if it comes in several lengths, give them briefly and ask which). Offer other choices, or the full list, ONLY if the caller asks for more.",
        "- If the caller has already said what they want (for example that they want to relax, or want hydration, or a particular area), do not ask the question again: recommend "
        "straight away.",
        "- The featured choices are examples, not a ranking. Never call a treatment popular, a favourite, a best seller or recommended by other customers. Recommend only from the "
        "menu and its descriptions; never diagnose, promise a result, say a treatment suits a medical or skin condition, or offer a treatment, a variation or an eligibility the menu "
        "does not list.",
        "- When the caller then wants a time, keep the treatment, its length and any technician they chose, and go on to check availability for that.",
        "",
        "Featured choices and the one question for each group:",
    ]
    for category, title in titles.items():
        if not families(catalog, category):
            continue
        if category not in QUESTIONS:
            raise ValueError(f"the menu has a {category} group with no discovery question")
        names = featured(catalog, category, configured)
        lines.append(f"- {title}: {', '.join(names)}. Ask: \"{QUESTIONS[category]}\"")
    return "\n".join(lines) + "\n\n"
