"""The service catalogue, its lookup, and the separate bookable allowlist."""

from __future__ import annotations

import inspect
import re
from datetime import date

import pytest

from aria_booking.catalog import website_data
from aria_booking.catalog.bookable import DEFAULT_SERVICE_ID, BookableRegistry, BookableService, staff_key
from aria_booking.catalog.lookup import DEFAULT_CATALOG, Catalog, duration_hint
from aria_booking.catalog.models import CatalogItem
from aria_booking.config import Config

CAT = DEFAULT_CATALOG
CATEGORIES = {"facial", "massage", "package", "body-scrub", "lash", "head-spa", "waxing", "permanent-makeup", "couples", "membership"}


# ---------------------------------------------------------------- the data


def test_the_catalogue_is_substantial_unique_and_fully_sourced():
    items = CAT.items
    assert len(items) >= 90
    assert len({i.item_id for i in items}) == len(items)
    assert {i.category for i in items} == CATEGORIES
    for item in items:
        assert item.source_url.startswith("https://www.warrenglamourdayspanj.com/"), item
        assert date.fromisoformat(item.retrieved_on) == date(2026, 10, 8)
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", item.item_id), item.item_id
        assert item.description and item.name
        assert item.price_usd is None or item.price_usd > 0
        assert item.duration_minutes is None or item.duration_minutes > 0


@pytest.mark.parametrize("item_id, minutes, price", [
    ("facial-express-facial-30", 30, 49), ("facial-signature-facial-60", 60, 85), ("facial-hydrafacial-treatment-75", 75, 179),
    ("facial-ulthera-skin-tightening-75", 75, 250), ("massage-swedish-massage-30", 30, 49), ("massage-swedish-massage-60", 60, 85),
    ("massage-swedish-massage-90", 90, 120), ("massage-hot-stone-massage-60", 60, 99), ("massage-prenatal-massage-60", 60, 99),
    ("massage-meridian-massage-90", 90, 149), ("package-signature-combo-120", 120, 149), ("body-scrub-back-polish-30", 30, 58),
    ("lash-eyelash-removal-45", 45, 30), ("lash-hybrid-3d-5d-full-set-full-set", 90, 179), ("head-spa-head-scalp-detoxification-60", 60, 109),
    ("waxing-eyebrows-15", 15, 12), ("waxing-full-leg-40", 40, 60), ("permanent-makeup-microblading-150", 150, 590),
    ("couples-couples-massage-60", 60, 149),
])
def test_prices_and_durations_match_what_the_official_pages_listed(item_id, minutes, price):
    item = CAT.get(item_id)
    assert item is not None, item_id
    assert (item.duration_minutes, item.price_usd) == (minutes, price)


def test_values_the_website_does_not_show_are_none_not_guessed():
    refill = CAT.get("lash-classic-full-set-refill-2w")
    assert refill.duration_minutes is None and refill.price_usd == 55 and "not published" in refill.notes
    for member in ("membership-glamour-5-visit-package-5-visit", "membership-glamour-10-visit-package-10-visit"):
        assert CAT.get(member).duration_minutes is None
    assert CAT.get("membership-glamour-5-visit-package-5-visit").original_price_usd == 425


def test_known_inconsistencies_on_the_website_are_flagged_not_hidden():
    assert "disagree" in CAT.get("body-scrub-lavender-sugar-body-scrub-with-seaweed-mask-60").notes
    assert "90 minutes" in CAT.get("couples-couples-hot-stone-massage-60").notes
    assert "Permament" in CAT.get("permanent-makeup-lip-blush-permanent-30").notes
    assert CAT.get("massage-prenatal-massage-60").notes.startswith("Offered for the 2nd and 3rd trimesters")


def test_no_retail_products_are_invented_and_the_gaps_are_declared():
    assert website_data.PRODUCTS == ()
    assert any("retail products" in gap for gap in website_data.NOT_PUBLISHED)
    assert not [i for i in CAT.items if "product" in i.category]


def test_website_hours_are_labelled_as_not_calendar_availability():
    assert "NOT calendar availability" in website_data.SITE["website_hours"]


def test_the_data_holds_no_phone_numbers_or_email_addresses():
    source = inspect.getsource(website_data)
    assert not re.search(r"\(\d{3}\)\s*\d{3}-\d{4}|\b\d{3}-\d{3}-\d{4}\b", source) and "@" not in source


def test_public_view_has_only_informational_fields():
    public = CAT.get("massage-swedish-massage-60").to_public()
    assert set(public) == {"service_id", "name", "category", "duration_minutes", "price_usd", "description", "source_url", "retrieved_on"}


# ---------------------------------------------------------------- lookup


@pytest.mark.parametrize("text, minutes", [
    ("60 minutes", 60), ("a 90-minute massage", 90), ("1 hour", 60), ("1.5 hours", 90), ("half an hour", 30), ("an hour and a half", 90),
    ("two hours", 120), ("45 min", 45), ("massage", None), ("60", None), ("30 and 60 minutes", None), ("", None),
])
def test_only_explicit_lengths_count_as_a_duration_hint(text, minutes):
    assert duration_hint(text) == minutes


def ids(resolution):
    return [i.item_id for i in resolution.items]


def test_a_service_with_several_lengths_asks_which_length():
    r = CAT.resolve("Swedish massage")
    assert r.kind == "variants" and not r.hint_unmatched
    assert [i.duration_minutes for i in r.items] == [30, 60, 90]


@pytest.mark.parametrize("query", ["swedish massage 60 minutes", "I'd like a one hour Swedish massage", "swedish massage, 1 hour", "Swedish Massage 60 min"])
def test_a_named_length_selects_that_variant(query):
    r = CAT.resolve(query)
    assert r.kind == "exact" and ids(r) == ["massage-swedish-massage-60"]


def test_a_length_that_does_not_exist_is_reported_not_substituted():
    r = CAT.resolve("swedish massage 45 minutes")
    assert r.kind == "variants" and r.hint_unmatched and [i.duration_minutes for i in r.items] == [30, 60, 90]


@pytest.mark.parametrize("query", ["facial", "massage", "hot stone", "scrub", "lymphatic"])
def test_generic_words_that_fit_several_services_are_ambiguous(query):
    r = CAT.resolve(query)
    assert r.kind == "ambiguous" and len(r.items) >= 2, query


def test_an_exact_service_name_beats_a_longer_name_that_contains_it():
    r = CAT.resolve("Lymphatic Drainage Massage")
    assert r.kind == "variants" and {i.family for i in r.items} == {"Lymphatic Drainage Massage"}
    assert CAT.resolve("Hot Stone Massage").kind == "variants"
    assert {i.family for i in CAT.resolve("Hot Stone Massage").items} == {"Hot Stone Massage"}


def test_a_single_variant_service_is_exact():
    assert ids(CAT.resolve("Express Facial")) == ["facial-express-facial-30"]
    assert ids(CAT.resolve("microblading")) == ["permanent-makeup-microblading-150"]


@pytest.mark.parametrize("query", ["haircut", "manicure", "Aria Salon", "", "   ", "the", "please book me", "xyzzy", None, 7])
def test_unknown_services_are_unknown(query):
    assert CAT.resolve(query).kind == "unknown"


def test_a_canonical_id_resolves_exactly():
    assert ids(CAT.resolve("massage-swedish-massage-90")) == ["massage-swedish-massage-90"]


def test_get_accepts_only_known_string_ids():
    assert CAT.get("nope") is None and CAT.get(None) is None and CAT.get(5) is None and CAT.get(["a"]) is None


def test_duplicate_ids_are_refused():
    one = CAT.items[0]
    with pytest.raises(ValueError):
        Catalog([one, one])


# ---------------------------------------------------------------- the bookable allowlist


def test_only_the_verified_test_service_is_bookable_today_and_no_website_service_is():
    registry = BookableRegistry.from_config(Config(business_id="1234567"))
    assert registry.ids() == [DEFAULT_SERVICE_ID] and registry.default_id == DEFAULT_SERVICE_ID
    only = registry.get(DEFAULT_SERVICE_ID)
    assert only.verified and only.test_only and only.booksy_name == "Aria Salon" and only.duration_minutes == 150
    assert only.eligible("Shobs") and only.eligible("  SHOBS ") and not only.eligible("Someone Else")
    assert not any(registry.is_bookable(item.item_id) for item in CAT.items)


def test_the_booking_configuration_for_a_service_comes_only_from_the_registry():
    cfg = Config(business_id="1234567")
    svc = BookableService("swedish-60", "Swedish Massage 60", 60, 5, 10, eligible_staff=frozenset({staff_key("Ana")}), catalog_item="massage-swedish-massage-60")
    bc = svc.booking_config(cfg, "Ana")
    assert (bc.service_name, bc.service_duration_minutes, bc.buffer_before_minutes, bc.buffer_after_minutes, bc.staff_name) == ("Swedish Massage 60", 60, 5, 10, "Ana")
    assert cfg.service_name == "Aria Salon" and cfg.service_duration_minutes == 150, "the original configuration is not modified"


def test_a_booksy_duration_that_differs_from_the_website_listing_is_refused_until_reconciled():
    with pytest.raises(ValueError, match="differs from the website"):
        BookableRegistry([BookableService("x", "Swedish Massage", 75, catalog_item="massage-swedish-massage-60")])


def test_registry_validation():
    with pytest.raises(ValueError, match="duplicate"):
        BookableRegistry([BookableService("a", "A", 30), BookableService("a", "A2", 30)])
    with pytest.raises(ValueError, match="positive"):
        BookableRegistry([BookableService("a", "A", 0)])
    with pytest.raises(ValueError, match="unknown catalogue item"):
        BookableRegistry([BookableService("a", "A", 30, catalog_item="no-such-item")])
    with pytest.raises(ValueError, match="default"):
        BookableRegistry([BookableService("a", "A", 30)], default_id="b")


def test_eligibility_none_means_any_verified_staff_and_empty_means_nobody():
    assert BookableService("a", "A", 30).eligible("Anyone")
    assert not BookableService("a", "A", 30, eligible_staff=frozenset()).eligible("Anyone")


def test_registry_get_rejects_non_string_ids():
    registry = BookableRegistry([BookableService("a", "A", 30)])
    assert registry.get(None) is None and registry.get(1) is None and not registry.is_bookable(["a"])


def test_bare_numbers_in_a_request_are_lengths_not_service_words():
    r = CAT.resolve("swedish massage 30 or 60 minutes")
    assert r.kind == "variants" and [i.duration_minutes for i in r.items] == [30, 60, 90] and not r.hint_unmatched
    assert CAT.resolve("hybrid 3d 5d full set").kind == "variants", "digits inside a real service name still match"
    assert CAT.resolve("60").kind == "unknown", "a number alone names no service"


def test_the_prompts_bookable_list_names_only_verified_services():
    from aria_booking.catalog.render import render_knowledge

    registry = BookableRegistry([
        BookableService("verified-one", "Verified One", 60, verified=True),
        BookableService("not-verified", "Not Verified", 45, verified=False),
    ])
    text = render_knowledge(registry=registry)
    assert "`verified-one`" in text and "not-verified" not in text and "Not Verified" not in text
    assert "Nothing. Online booking is not available" in render_knowledge(registry=None)
