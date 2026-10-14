"""The Serenity prototype MENU: independently written service descriptions for a generic spa, kept apart from the earlier salon's website knowledge.

* Same menu structure and broad offerings as before (facials, massages, body scrubs, lashes and brows, head spa, waxing, combos, couples, visit bundles), so the
  demonstration is realistic and the calendar's eligibility and duration logic keeps working. Service ids are unchanged where the service is unchanged.
* Descriptions are freshly written, short and plain: they say what a service involves, never what it will achieve, and make no clinical, product or brand claims.
* Branded or business-specific offerings of the earlier salon (trademarked machines and product lines, its named packages, semi-permanent makeup) are NOT carried
  over; they are neutral service types here, or left out. Nothing in this file claims that every spa offers them.
* Prices and lengths are EXAMPLE data for the prototype: they are editable here and are not statements about any real business.
"""

from __future__ import annotations

import re
from typing import Optional

from ..catalog.lookup import Catalog
from ..catalog.models import CatalogItem

PROTOTYPE_SOURCE = "prototype example menu (editable)"
MENU_DATE = ""  # example data has no retrieval date


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _item(category: str, family: str, minutes: Optional[int], price: int, desc: str, *, key: Optional[str] = None, variant: Optional[str] = None) -> CatalogItem:
    suffix = key if key else (str(minutes) if minutes else "")
    item_id = "-".join(p for p in (category, _slug(family), suffix) if p)
    return CatalogItem(item_id, family, category, family + (f", {variant}" if variant else ""), desc, minutes, price, PROTOTYPE_SOURCE, MENU_DATE, None, "")


def _timed(category: str, family: str, desc: str, options) -> list:
    """The same service in several lengths: ``options`` is ((minutes, price), ...). The variant label is the length, as the voice lookup expects."""
    out = []
    for minutes, price in options:
        hours, rest = divmod(minutes, 60)
        label = " ".join(p for p in ("1 hour" if hours == 1 else f"{hours} hours" if hours else "", f"{rest} minutes" if rest else "") if p)
        out.append(_item(category, family, minutes, price, desc, variant=label))
    return out


def _single(category: str, rows) -> list:
    return [_item(category, name, minutes, price, desc) for name, minutes, price, desc in rows]


def _lash_set(family: str, desc: str, initial, refills) -> list:
    """An initial set plus refills at 2, 3 (and 4) weeks. ``initial`` is (minutes, price); ``refills`` is ((weeks, minutes, price), ...)."""
    items = [_item("lash", family, initial[0], initial[1], desc, key="full-set", variant="initial set")]
    for weeks, minutes, price in refills:
        items.append(_item("lash", family, minutes, price, f"A refill visit for the {family.lower()}, timed for about {weeks} weeks after the last appointment.",
                           key=f"refill-{weeks}w", variant=f"{weeks}-week refill"))
    return items


FACIALS = _single("facial", [
    ("Express Facial", 30, 49, "A short, simple facial for when time is tight: cleanse, light exfoliation and a moisturizing finish."),
    ("Signature Facial", 60, 85, "Our classic full-length facial, adapted to your skin, with cleansing, exfoliation, a mask and a relaxing face massage."),
    ("Back Facial", 60, 89, "A cleansing treatment for the back, with exfoliation, a mask and a soothing massage."),
    ("Sensitive Skin Facial", 60, 109, "A gentle facial that keeps to mild, calming steps for skin that does not like strong products."),
    ("Teen Facial", 45, 75, "A friendly, uncomplicated facial for younger skin, with a thorough cleanse, a mask and easy tips for a daily routine."),
    ("Gentleman Facial", 60, 85, "A facial built around men's skin and grooming routines, with a cleanse, a refreshing mask and a calming finish."),
    ("Acne Skin Facial", 60, 109, "A deep-cleansing facial for congested or breakout-prone skin, followed by a calming mask."),
    ("Brightening Facial", 75, 149, "A longer facial with gentle exfoliation and a brightening mask, aimed at a fresh, even-looking complexion."),
    ("Oxygen Facial", 75, 149, "A refreshing facial that adds an oxygen-infused mist and mask to the cleanse and massage."),
    ("Microdermabrasion Facial", 75, 149, "A polishing facial that uses a gentle abrasive tool to exfoliate the surface of the skin, finished with a soothing mask."),
    ("LED Light Facial", 75, 149, "A facial that adds a session under an LED light panel to the cleanse, mask and massage."),
    ("Deep Hydration Facial", 75, 179, "A multi-step facial that cleanses, exfoliates and layers in moisture, finished with a hydrating mask."),
    ("Lifting Facial", 75, 149, "A massage-led facial with a firming mask and extra time on the jaw, neck and shoulders."),
    ("Ultrasonic Facial", 75, 149, "A facial that uses a gentle ultrasonic tool during the cleanse and mask steps."),
    ("Extended Care Facial", 90, 189, "Our longest facial, with extra time for massage, a treatment mask and a hydrating finish."),
])

_MASSAGE_BASIC = ((30, 49), (60, 85), (90, 120))
_MASSAGE_PREMIUM = ((30, 59), (60, 99), (90, 139))
MASSAGES = (
    _timed("massage", "Anti-Stress Massage", "A medium-pressure massage that blends long and kneading strokes to help you unwind.", _MASSAGE_BASIC)
    + _timed("massage", "Deep Tissue Massage", "Firm, focused pressure on the back, shoulders and legs, worked in slow strokes.", _MASSAGE_BASIC)
    + _timed("massage", "Hot Stone Massage", "A massage that pairs warm, smooth stones with hands-on work for a deeply relaxing session.", _MASSAGE_PREMIUM)
    + _timed("massage", "Swedish Massage", "A classic, flowing full-body massage with light to medium pressure.", _MASSAGE_BASIC)
    + _timed("massage", "Aromatherapy Massage", "A full-body massage with scented essential oils chosen to suit your mood.", _MASSAGE_PREMIUM)
    + _timed("massage", "Reflexology Massage", "A massage that concentrates on the feet and hands, pressing on specific points.", _MASSAGE_BASIC)
    + _timed("massage", "Prenatal Massage", "A gentle, well-supported massage for expectant clients, with pillows for comfort and lighter pressure.", ((60, 99),))
    + _timed("massage", "Lymphatic Drainage Massage", "A very light, rhythmic massage using slow, sweeping strokes.", ((60, 99), (90, 139)))
    + _timed("massage", "Wood Therapy Massage", "A massage that uses smooth wooden tools to knead and contour the muscles.", ((60, 119), (90, 139)))
    + _timed("massage", "Meridian Massage", "A long massage that follows traditional channels of the body with pressure and stretching.", ((90, 149),))
    + _timed("massage", "Abdominal Massage", "A gentle massage of the stomach area with warm oil.", ((30, 65),))
)

COMBOS = _single("package", [
    ("Quick Refresh Combo", 60, 88, "A 30-minute massage followed by a 30-minute express facial."),
    ("Massage and Facial Combo", 120, 149, "A one-hour massage and a one-hour signature facial in a single visit."),
    ("Massage and Scalp Combo", 120, 169, "A one-hour massage followed by a one-hour scalp treatment."),
])

BODY_SCRUBS = _single("body-scrub", [
    ("Sugar Body Scrub", 45, 89, "A full-body polish with a sugar scrub, finished with a moisturizing layer."),
    ("Back Polish", 30, 58, "A scrub and polish for the back and shoulders."),
    ("Scrub and Seaweed Wrap", 60, 119, "A body scrub followed by a seaweed wrap, then a rinse and moisturizer."),
])

EYELASHES = (
    _lash_set("Classic Full Set", "One extension applied to each natural lash for a soft, defined look.", (60, 99), ((2, 45, 55), (3, 60, 70)))
    + _lash_set("Volume Full Set", "Several fine extensions fanned onto each natural lash for a fuller look.", (75, 149), ((2, 60, 75), (3, 60, 90), (4, 75, 105)))
    + _lash_set("Natural Full Set", "A lighter set designed to look subtle and soft.", (75, 129), ((2, 60, 65), (3, 60, 80), (4, 75, 95)))
    + _lash_set("Hybrid Full Set", "A mix of single and fanned extensions for texture and fullness.", (90, 179), ((2, 60, 90), (3, 60, 110), (4, 75, 125)))
    + _single("lash", [
        ("Eyelash Tinting", 30, 39, "A gentle dye that darkens your natural lashes."),
        ("Eyebrow Tinting", 30, 39, "A dye that adds colour to the brows and helps fill sparse areas."),
        ("Eyelash Lifting", 45, 79, "A treatment that curls your natural lashes from the base."),
        ("Eyelash Removal", 45, 30, "A careful removal of existing lash extensions."),
    ])
)

HEAD_SPA = _single("head-spa", [
    ("Head Scalp Massage", 30, 65, "A calming scalp massage with a cooling oil and a fine comb."),
    ("Head Scalp Detoxification with Mini Facial", 75, 129, "A scalp cleanse and a neck and shoulder massage, followed by a short hydrating facial."),
    ("Head Scalp Detoxification", 60, 109, "A scalp massage and cleanse with a rinse and conditioning, ending with a neck and shoulder massage."),
])

_WAX_AREAS = (
    ("Eyebrows", 15, 12), ("Lip", 15, 12), ("Chin", 15, 12), ("Full Face", 30, 50), ("Under Arm", 15, 20), ("Half Arm", 20, 35), ("Full Arm", 30, 50),
    ("Chest", 15, 55), ("Stomach", 15, 35), ("Upper Back", 15, 55), ("Full Back", 20, 80), ("Half Leg", 25, 35), ("Full Leg", 40, 60), ("Toes", 10, 20),
    ("Bikini Line", 20, 45), ("Brazilian Line", 30, 65),
)
WAXING = [_item("waxing", name, minutes, price, f"Wax hair removal for the {name.lower()} area, worked quickly and with attention to comfort.") for name, minutes, price in _WAX_AREAS]

COUPLES = _single("couples", [
    ("Couples Massage", 60, 149, "Two massages given side by side in one room."),
    ("Couples Signature Facials", 60, 149, "Two signature facials given side by side in one room."),
    ("Couples Hot Stone Massage", 60, 189, "Two hot stone massages given side by side in one room."),
])

BUNDLES = [
    _item("membership", "5-Visit Bundle", None, 345, "Five one-hour visits to use on massages or facials, booked as you go.", key="5-visit"),
    _item("membership", "10-Visit Bundle", None, 650, "Ten one-hour visits to use on massages or facials, booked as you go.", key="10-visit"),
]

SHOWCASE_ITEMS: tuple = tuple(FACIALS + MASSAGES + COMBOS + BODY_SCRUBS + EYELASHES + HEAD_SPA + WAXING + COUPLES + BUNDLES)


def _listed_as(item: CatalogItem) -> str:
    return "as listed on our menu"


SHOWCASE_CATALOG = Catalog(SHOWCASE_ITEMS, listed_as=_listed_as)
