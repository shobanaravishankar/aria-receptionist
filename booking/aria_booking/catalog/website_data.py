"""Services and listed prices as published on the salon's OFFICIAL website. Informational only.

Source of every row: the page named in ``URL`` below, read on ``RETRIEVED_ON``. Descriptions are short paraphrases, not the
site's marketing copy. Where a page does not show a value it is ``None`` (never guessed); where a page contradicts itself
that is said in ``notes``. Prices and durations on a website change: re-read the pages and bump ``RETRIEVED_ON`` before
telling a caller a price is current. This file says nothing about what the Booksy calendar can book (see ``bookable``).

Not published on the site (so Aria must say she does not have it): retail products and their prices, stock, cancellation
or late-arrival policy, deposits, parking. The site's opening hours are NOT the Booksy test calendar's hours; availability
always comes from the calendar tools, never from this file.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from .models import CatalogItem

BASE = "https://www.warrenglamourdayspanj.com"
RETRIEVED_ON = "2026-10-08"
SITE = {
    "name": "Glamour Day Spa (Warren, NJ)",
    "website_hours": "every day 9:30 AM to 8:00 PM (as published on the website; NOT calendar availability)",
    "address": "24 Mountain Blvd, Warren, NJ 07059",
    "source_url": BASE + "/contact-us",
    "retrieved_on": RETRIEVED_ON,
}
NOT_PUBLISHED = (
    "retail products and their prices or stock",
    "cancellation, late-arrival and deposit policies",
    "parking",
    "whether a given treatment suits a particular skin condition or medical situation",
)
PRODUCTS: tuple = ()  # the website lists no retail products; do not invent any


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def duration_label(minutes: Optional[int]) -> str:
    if minutes is None:
        return "duration not published"
    hours, rest = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append("1 hour" if hours == 1 else f"{hours} hours")
    if rest:
        parts.append(f"{rest} minutes")
    return " ".join(parts)


def _item(category, page, family, minutes, price, desc, *, key=None, variant=None, original=None, notes="") -> CatalogItem:
    suffix = key if key else (str(minutes) if minutes else "")
    item_id = "-".join(p for p in (category, slug(family), suffix) if p)
    name = family + (f", {variant}" if variant else "")
    return CatalogItem(item_id, family, category, name, desc, minutes, price, f"{BASE}/{page}", RETRIEVED_ON, original, notes)


def _single(category, page, rows: Iterable[tuple]) -> list[CatalogItem]:
    return [_item(category, page, name, minutes, price, desc, notes=(rest[0] if rest else "")) for name, minutes, price, desc, *rest in rows]


def _timed(category, page, family, desc, options: Iterable[tuple], notes="") -> list[CatalogItem]:
    return [_item(category, page, family, m, p, desc, variant=duration_label(m), notes=notes) for m, p in options]


FACIALS = _single("facial", "facials", [
    ("Express Facial", 30, 49, "A quick refresh: cleansing, exfoliation, mask, moisturizer and SPF."),
    ("Signature Facial", 60, 85, "A customized hour-long facial built around your skin type and concerns."),
    ("Back Facial", 60, 89, "A back-focused treatment for clogged pores, breakouts and dry or rough skin."),
    ("Sensitive Skin Facial", 60, 109, "A gentle essential-oil facial meant to calm irritation and support the skin barrier."),
    ("HydraFacial Treatment", 75, 179, "A non-invasive session that cleanses, exfoliates, extracts and hydrates the skin."),
    ("Bio Lifting Tightening Facial", 75, 149, "An oxygen-and-nutrient facial aimed at firming and brightening."),
    ("Ultra Sound and Photo Rejuvenation", 75, 149, "A facial using ultrasound and light to support collagen and smooth lines."),
    ("GM Collin Oxygenating Treatment", 75, 159, "A five-step treatment for acne and dullness using BHA and AHA acids."),
    ("GM Collin Sea \"C\" Spa Treatment", 75, 169, "An antioxidant anti-aging treatment with vitamin C and marine ingredients."),
    ("GM Collin Botinal Treatment", 90, 199, "A non-invasive treatment that softens expression lines for a glow."),
    ("LED Light Machine Facial", 75, 149, "An LED light therapy facial for acne, redness, fine lines and dullness."),
    ("Ulthera Skin Tightening", 75, 250, "A non-surgical focused-ultrasound treatment to lift and tighten face, neck and jawline."),
    ("Teen Facial", 45, 75, "A facial for young skin addressing breakouts and oil, with basic skincare tips."),
    ("Gentleman Facial", 60, 85, "A men's facial that cleanses, hydrates and soothes shaving irritation."),
    ("Acne Skin Facial", 60, 109, "A deep-cleansing facial for congested pores, breakouts and inflamed skin."),
    ("Pigment Facial", 75, 149, "A brightening facial for uneven tone, dark spots and sun-damaged skin."),
    ("Oxygen Machines Facial", 75, 149, "An oxygen-infused facial that hydrates, brightens and refreshes dull skin."),
    ("Microdermabrasion Facial", 75, 149, "A resurfacing exfoliation for fine lines, scars, sun damage and dullness."),
    ("GM Collin Algomask Treatment", 75, 149, "A cooling hydration treatment for redness and sensitive skin; also an add-on to a customized signature facial."),
    ("GM Collin Hydro-Lifting Treatment", 75, 169, "A clinical hydration treatment for face and neck aimed at firming and lifting."),
    ("GM Collin Collagen Treatment", 90, 189, "An intensive anti-aging treatment that hydrates, tightens and reduces the look of fine lines."),
    ("Ultrasonic Firm Up Skin Facial", 75, 149, "A facial using ultrasonic vibrations to firm, tone and help products absorb."),
    ("Black Doll Beauty", 75, 250, "A deep-cleansing and tightening treatment for acne, enlarged pores and uneven texture."),
])

_MASSAGE_30_60_90 = ((30, 49), (60, 85), (90, 120))
_MASSAGE_PREMIUM = ((30, 59), (60, 99), (90, 139))
MASSAGES = (
    _timed("massage", "massages", "Anti-Stress Massage", "A mix of Swedish and deep-tissue work aimed at relaxation and pain relief.", _MASSAGE_30_60_90)
    + _timed("massage", "massages", "Deep Tissue Massage", "Slower, firmer pressure that works into deeper muscle layers for chronic tension.", _MASSAGE_30_60_90)
    + _timed("massage", "massages", "Hot Stone Massage", "Warm stones loosen tight muscles and promote relaxation.", _MASSAGE_PREMIUM)
    + _timed("massage", "massages", "Prenatal Massage", "A gentle massage for pregnancy discomfort, supported with pillows and positioning.", ((60, 99),),
             notes="Offered for the 2nd and 3rd trimesters.")
    + _timed("massage", "massages", "Lymphatic Drainage Massage with Wood Therapy", "Wooden tools relax muscles, contour the body and improve circulation.", ((60, 119), (90, 139)))
    + _timed("massage", "massages", "Breast Enhancing Treatment", "A non-surgical service combining massage, collagen stimulation and a hydrating mask.", ((30, 65),))
    + _timed("massage", "massages", "Swedish Massage", "Flowing strokes with oils to ease muscle tension and encourage full-body relaxation.", _MASSAGE_30_60_90)
    + _timed("massage", "massages", "Aromatherapy Massage", "Swedish-style massage with concentrated plant oils you smell and absorb.", _MASSAGE_PREMIUM)
    + _timed("massage", "massages", "Reflexology Massage", "Targeted pressure on points of the feet, hands or ears to promote relaxation.", _MASSAGE_30_60_90)
    + _timed("massage", "massages", "Lymphatic Drainage Massage", "A light, rhythmic massage intended to reduce swelling and support circulation.", ((60, 99), (90, 139)))
    + _timed("massage", "massages", "Meridian Massage", "A holistic therapy that stimulates the body's energy channels.", ((90, 149),))
    + _timed("massage", "massages", "Abdominal Massage", "A gentle treatment with herbal oils to relax abdominal muscles and ease bloating.", ((30, 65),))
)

PACKAGES = _single("package", "spa-packages", [
    ("Express Self Care", 60, 88, "A 30-minute anti-stress massage plus a 30-minute classic signature facial."),
    ("Full Body Self Care", 120, 179, "A 60-minute massage, 30-minute reflexology and 30-minute head and scalp massage."),
    ("Signature Combo", 120, 149, "A one-hour anti-stress massage and a one-hour classic signature facial."),
    ("Stress Release Combo", 120, 169, "A one-hour massage plus a one-hour head massage and scalp detoxification.",
     "The page's description calls it the Stress Relief Combo; the heading says Stress Release Combo."),
])

BODY_SCRUBS = _single("body-scrub", "body-scrub", [
    ("Lavender Sugar Body Scrub", 45, 89, "A full-body exfoliation with micro-buffing beads and apricot seed powder to remove dead skin."),
    ("Back Polish", 30, 58, "A back-focused treatment that cleans, exfoliates and smooths the skin."),
    ("Lavender Sugar Body Scrub with Seaweed Mask", 60, 119, "A mineral-rich seaweed treatment followed by a 20-minute wrap meant to detoxify and nourish.",
     "The page's name and description for this item disagree (the description covers only the seaweed treatment); confirm with the salon."),
])

_LASH = "eyelash-extensions"
EYELASHES = (
    [_item("lash", _LASH, "Classic Full Set", 60, 99, "One extension per natural lash for added length and curl.", key="full-set")]
    + [_item("lash", _LASH, "Classic Full Set", None, p, "Refill for a Classic Full Set.", key=f"refill-{w}w", variant=f"{w}-week refill",
             notes="Refill duration is not published.") for w, p in ((2, 55), (3, 70))]
    + [_item("lash", _LASH, "Glamour Full Set", 75, 149, "Dense volume fans for a bold, dramatic, lightweight look (180 lashes).", key="full-set")]
    + [_item("lash", _LASH, "Glamour Full Set", m, p, "Refill for a Glamour Full Set.", key=f"refill-{w}w", variant=f"{w}-week refill")
       for w, m, p in ((2, 60, 75), (3, 60, 90), (4, 75, 105))]
    + [_item("lash", _LASH, "Natural Full Set", 75, 129, "A subtle, lightweight style that looks slightly longer and fuller (140 lashes).", key="full-set")]
    + [_item("lash", _LASH, "Natural Full Set", m, p, "Refill for a Natural Full Set.", key=f"refill-{w}w", variant=f"{w}-week refill")
       for w, m, p in ((2, 60, 65), (3, 60, 80), (4, 75, 95))]
    + [_item("lash", _LASH, "Hybrid 3D / 5D Full Set", 90, 179, "Classic extensions combined with 3D-5D volume fans for a textured, fuller result.", key="full-set")]
    + [_item("lash", _LASH, "Hybrid 3D / 5D Full Set", m, p, "Refill for a Hybrid 3D / 5D Full Set.", key=f"refill-{w}w", variant=f"{w}-week refill")
       for w, m, p in ((2, 60, 90), (3, 60, 110), (4, 75, 125))]
    + _single("lash", _LASH, [
        ("Eyelash Tinting", 30, 39, "A gentle dye darkens natural lashes so they look more defined."),
        ("Eyebrow Tinting", 30, 39, "A semi-permanent dye adds color and fills in sparse areas of the brows."),
        ("Eyelash Lifting", 45, 79, "A semi-permanent treatment that curls natural lashes from the base for several weeks."),
        ("Eyelash Removal", 45, 30, "A professional remover dissolves the adhesive so existing extensions come off."),
    ])
)

HEAD_SPA = _single("head-spa", "scalp-treatments", [
    ("Head Scalp Massage", 30, 65, "A calming scalp treatment using peppermint oil and a special comb to relieve tension."),
    ("Head Scalp Detoxification with Mini Facial", 75, 129, "A scalp detox, neck and shoulder massage and a hydrating mini facial."),
    ("Head Scalp Detoxification", 60, 109, "Scalp massage, gua sha, shampoo, conditioning and a neck and shoulder massage."),
])

WAXING = _single("waxing", "waxing", [
    ("Eyebrows", 15, 12, "Eyebrow waxing."), ("Lip", 15, 12, "Upper-lip waxing."), ("Chin", 15, 12, "Chin waxing."),
    ("Full Face", 30, 50, "Full-face waxing."), ("Under Arm", 15, 20, "Underarm waxing."), ("Half Arm", 20, 35, "Half-arm waxing."),
    ("Full Arm", 30, 50, "Full-arm waxing."), ("Chest", 15, 55, "Chest waxing."), ("Stomach", 15, 35, "Stomach waxing."),
    ("Upper Back", 15, 55, "Upper-back waxing."), ("Full Back", 20, 80, "Full-back waxing."), ("Half Leg", 25, 35, "Half-leg waxing."),
    ("Full Leg", 40, 60, "Full-leg waxing."), ("Toes", 10, 20, "Toe waxing."),
    ("Bikini Line", 20, 45, "Bikini-line waxing (women only).", "Listed as women only."),
    ("Brazilian Line", 30, 65, "Brazilian-line waxing (women only).", "Listed as women only."),
])

PERMANENT_MAKEUP = _single("permanent-makeup", "permanent-makeup", [
    ("Ombre Powder Brows", 120, 590, "A semi-permanent shaded, powdered brow look, typically lasting 1 to 3 years."),
    ("Ombre Powder Brow Refill", 90, 350, "A maintenance visit that adds pigment to existing ombre brows.", "Intended for existing ombre brows."),
    ("Lip Blush Permanent", 30, 790, "A semi-permanent lip treatment that adds a soft, natural tint; begins with a consultation and includes numbing.",
     "The page spells the name 'Lip Blush Permament'; the listed 30-minute duration is as published."),
    ("Microblading", 150, 590, "A handheld-blade brow treatment drawing fine hair-like strokes, lasting about 18 to 30 months."),
    ("Eyeliner", 120, 390, "A pigment treatment along the lash line, subtle to bold, typically lasting 1 to 3 years."),
])

COUPLES = _single("couples", "couples-packages", [
    ("Couples Massage", 60, 149, "A synchronized massage by two therapists side by side in a private room."),
    ("Couples Signature Facials", 60, 149, "Two signature facials with deep cleansing, extractions and a face, head, neck and shoulder massage."),
    ("Couples Hot Stone Massage", 60, 239, "A 60-minute hot stone massage followed by 30 minutes in a private suite with champagne and chocolates.",
     "Listed as 1 hour but the description adds 30 minutes in a suite (about 90 minutes in all); confirm with the salon."),
])

MEMBERSHIPS = [
    _item("membership", "memberships", "Glamour 5 Visit Package", None, 345, "Five one-hour visits, each a massage or facial.", key="5-visit", original=425,
          notes="Each visit may be a deep tissue, Swedish or anti-stress massage, scalp detoxification or signature facial. Price does not include tips."),
    _item("membership", "memberships", "Glamour 10 Visit Package", None, 650, "Ten one-hour visits, each a massage or facial.", key="10-visit", original=850,
          notes="Each visit may be a deep tissue, Swedish or anti-stress massage, scalp detoxification or signature facial. Price does not include tips."),
]

ALL_ITEMS: tuple[CatalogItem, ...] = tuple(
    FACIALS + MASSAGES + PACKAGES + BODY_SCRUBS + EYELASHES + HEAD_SPA + WAXING + PERMANENT_MAKEUP + COUPLES + MEMBERSHIPS
)
