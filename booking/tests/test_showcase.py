"""The generic Serenity showcase source: brand, fictional roster and independently written menu. Nothing here may carry the earlier salon's identity, and the menu must stay
consistent with the calendar's service, duration and eligibility logic. Offline, no browser, no network."""

from __future__ import annotations

import ast
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from aria_booking.catalog.lookup import DEFAULT_CATALOG
from aria_booking.demo import SALON_NAME, SPA_TIMEZONE, greeting_word
from aria_booking.demo.dataset import DEMO_FAMILIES, ROSTER, DemoDataset, demo_registry
from aria_booking.showcase import SHOWCASE, Showcase, load_showcase
from aria_booking.showcase.catalog import SHOWCASE_CATALOG, SHOWCASE_ITEMS
from aria_booking.showcase.leaks import find_leaks
from aria_booking.showcase.roster import NAME_BY_ID, NEAR_MISS_NAME, ROSTER_ROWS, StaffRow, name_of, validate_roster

BOOKING = Path(__file__).resolve().parent.parent
SHOWCASE_DIR = BOOKING / "aria_booking" / "showcase"


# ---------------------------------------------------------------- the brand


def test_the_brand_is_serenity_with_no_address_no_phone_and_one_timezone():
    assert SHOWCASE.name == SALON_NAME == "Serenity Spa" and SHOWCASE.timezone == SPA_TIMEZONE == "America/New_York"
    assert SHOWCASE.location is None and SHOWCASE.contact_phone is None, "nothing is invented: no town, no address, no number"
    assert "prototype" in SHOWCASE.prototype_label.lower() and "example" in SHOWCASE.prototype_label.lower()


def test_the_showcase_can_be_configured_from_a_file_and_unknown_keys_are_refused(tmp_path):
    path = tmp_path / "showcase.json"
    path.write_text(json.dumps({"name": "Lotus Spa", "contact_phone": "+1 555 010 0100", "example_hours": "daily 9 to 5"}), encoding="utf-8")
    shown = load_showcase(str(path))
    assert shown.name == "Lotus Spa" and shown.contact_phone == "+1 555 010 0100" and shown.location is None and shown.theme == SHOWCASE.theme
    path.write_text(json.dumps({"nmae": "typo"}), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown showcase keys: nmae"):
        load_showcase(str(path))
    with pytest.raises(ValueError, match="could not be read"):
        load_showcase(str(tmp_path / "missing.json"))
    assert load_showcase(overrides={"name": "Override Spa", "tagline": None}).tagline == SHOWCASE.tagline


def test_the_theme_text_colours_have_readable_contrast_on_their_backgrounds():
    def luminance(hex_colour: str) -> float:
        channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def ratio(a: str, b: str) -> float:
        hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    t = SHOWCASE.theme
    for text, background, minimum in [(t.ink, t.white, 7), (t.ink, t.sand_100, 7), (t.ink, t.teal_50, 7), (t.ink_soft, t.white, 4.5), (t.ink_soft, t.sand_100, 4.5),
                                       (t.white, t.teal_700, 4.5), (t.white, t.teal_900, 7), (t.teal_900, t.teal_100, 7), (t.teal_700, t.white, 4.5),
                                       (t.mist, t.teal_900, 7), (t.mist_dim, t.teal_900, 7), (t.mist_faint, t.teal_900, 4.5), (t.teal_100, t.teal_900, 7),
                                       (t.green, t.white, 3), (t.red, t.white, 4.5), (t.teal_900, t.sand_100, 7)]:
        assert ratio(text, background) >= minimum, (text, background, ratio(text, background))


# ---------------------------------------------------------------- the fictional roster


def test_the_roster_is_ten_distinct_fictional_first_names_with_the_same_ids_schedules_and_eligibility():
    assert [r.staff_id for r in ROSTER_ROWS] == [f"9100{n:02d}" for n in range(1, 11)]
    assert len({r.name for r in ROSTER_ROWS}) == 10 and all(len(r.name.split()) == 1 for r in ROSTER_ROWS)
    by_id = {r.staff_id: r for r in ROSTER_ROWS}
    assert all(r.hours_known for r in ROSTER_ROWS), "every standard column shows its working days and hours (a missing-hours column is a diagnostic option only)"
    assert by_id["910010"].weekdays == (0, 1, 2, 3, 4) and by_id["910010"].hours == (600, 1200) and by_id["910010"].categories == ("facial",)
    assert by_id["910001"].categories == ("massage", "body-scrub", "head-spa") and by_id["910006"].categories == ("massage", "head-spa")
    assert by_id["910003"].weekdays == (1, 3, 5) and by_id["910004"].weekdays == (2, 4, 6) and by_id["910009"].hours == (720, 1200)


def test_the_dataset_roster_is_built_from_it_and_names_are_display_only():
    assert [(s.staff_id, s.name) for s in ROSTER] == [(r.staff_id, r.name) for r in ROSTER_ROWS]
    assert NAME_BY_ID["910006"] == name_of("910006") and NEAR_MISS_NAME not in NAME_BY_ID.values()
    with pytest.raises(KeyError):
        name_of("999999")
    from aria_booking.demo import dataset

    source = ast.parse((BOOKING / "aria_booking" / "demo" / "dataset.py").read_text(encoding="utf-8"))
    keys = [n.value for node in ast.walk(source) if isinstance(node, ast.Dict) for n in node.keys if isinstance(n, ast.Constant)]
    assert set(dataset._SCRIPT_FIRST) <= {r.staff_id for r in ROSTER_ROWS} and set(dataset._SCRIPT_SECOND) <= {r.staff_id for r in ROSTER_ROWS}, "scripts are keyed by id"
    assert not [k for k in keys if isinstance(k, str) and k in NAME_BY_ID.values()], "no schedule or script is keyed by a display name"


@pytest.mark.parametrize("rows,message", [
    ((StaffRow("1", "Ana", (0,), (600, 1200), ("massage",)), StaffRow("1", "Bea", (0,), (600, 1200), ("massage",))), "duplicate staff id"),
    ((StaffRow("1", "Ana", (0,), (600, 1200), ("massage",)), StaffRow("2", "ana", (0,), (600, 1200), ("massage",))), "duplicate display name"),
    ((StaffRow("1", "Ana Lee", (0,), (600, 1200), ("massage",)),), "one word"),
    ((StaffRow("1", " ", (0,), (600, 1200), ("massage",)),), "one word"),
    ((StaffRow("1", "Ana", (0,), (600, 1200), ("massage", "astrology")),), "unknown categories"),
])
def test_a_roster_the_voice_tools_could_not_tell_apart_is_refused(rows, message):
    with pytest.raises(ValueError, match=message):
        validate_roster(rows)


# ---------------------------------------------------------------- the menu


def test_the_menu_keeps_the_broad_offerings_and_every_calendar_family_exists_in_it():
    categories = {i.category for i in SHOWCASE_ITEMS}
    assert categories == {"facial", "massage", "package", "body-scrub", "lash", "head-spa", "waxing", "couples", "membership"}
    families = set(SHOWCASE_CATALOG.families())
    assert [f for _c, f, _b in DEMO_FAMILIES if f not in families] == []
    assert len(SHOWCASE_ITEMS) >= 80 and len(families) >= 60


def test_ids_are_unique_and_unchanged_for_unchanged_services_so_the_calendar_logic_keeps_working():
    ids = [i.item_id for i in SHOWCASE_ITEMS]
    assert len(set(ids)) == len(ids)
    for kept in ("massage-deep-tissue-massage-60", "facial-microdermabrasion-facial-75", "facial-signature-facial-60", "waxing-eyebrows-15", "lash-eyelash-lifting-45"):
        item = SHOWCASE_CATALOG.get(kept)
        assert item is not None and DEFAULT_CATALOG.get(kept) is not None and item.duration_minutes == DEFAULT_CATALOG.get(kept).duration_minutes


def test_every_item_has_a_plain_description_a_price_and_a_length_or_is_a_bundle():
    for item in SHOWCASE_ITEMS:
        assert item.description.strip() and len(item.description) <= 170 and item.price_usd and item.price_usd > 0, item.item_id
        assert (item.duration_minutes is None) == (item.category == "membership"), item.item_id
        assert item.source_url == "prototype example menu (editable)" and item.retrieved_on == "", "example data, not a website read"


def test_descriptions_are_independent_not_carried_over_and_make_no_claims():
    old = {i.description for i in DEFAULT_CATALOG.items} | {i.family for i in DEFAULT_CATALOG.items}
    assert not [i.item_id for i in SHOWCASE_ITEMS if i.description in old]
    claims = re.compile(r"\b(guarantee|cure|treat(?:s|ment of)? (?:acne|eczema)|clinically|proven|permanent(?:ly)?|years|FDA|award|best|certified|licensed)\b", re.I)
    assert [i.item_id for i in SHOWCASE_ITEMS if claims.search(i.description)] == [], "no clinical, guarantee, award or credential claims"


def test_branded_or_business_specific_offerings_of_the_earlier_salon_are_not_carried_over():
    names = " | ".join(i.name for i in SHOWCASE_ITEMS).lower()
    for branded in ("hydrafacial", "gm collin", "ulthera", "black doll", "microblading", "lip blush", "ombre powder", "self care", "stress release", "glamour"):
        assert branded not in names, branded
    assert find_leaks(" ".join(i.name + " " + i.description for i in SHOWCASE_ITEMS)) == []


def test_the_calendar_logic_runs_on_the_menu_lengths():
    registry = DemoDataset(date(2026, 10, 14)).registry
    for service_id in registry.ids():
        service, item = registry.get(service_id), SHOWCASE_CATALOG.get(registry.get(service_id).catalog_item)
        assert item is not None and item.duration_minutes == service.duration_minutes
    assert "facial-deep-hydration-facial-75" in demo_registry().ids() and "facial-hydrafacial-treatment-75" not in demo_registry().ids()


def test_the_lookup_speaks_of_the_menu_not_a_website_read_date():
    from test_demo_voice import make

    tools, *_ = make()
    reply = tools.lookup_service("c1", {"query": "swedish massage 60 minutes"})
    assert "as listed on our menu" in reply["speak"] and "website" not in reply["speak"].lower() and "2026-10" not in reply["speak"]
    assert find_leaks(reply["speak"]) == []


# ---------------------------------------------------------------- nothing of the earlier salon, anywhere in the new source


def test_the_showcase_source_and_the_generated_outputs_carry_no_old_identifier():
    for path in sorted(SHOWCASE_DIR.glob("*.py")):
        assert find_leaks(path.read_text(encoding="utf-8")) == [], path.name
    for text in (json.dumps([i.to_public() for i in SHOWCASE_ITEMS]), (BOOKING / "voice_agent_draft" / "prompt_demo_calendar.md").read_text(encoding="utf-8")):
        assert find_leaks(text) == []
    dataset = DemoDataset(date(2026, 10, 14))
    for offset in range(-3, 10):
        day = dataset.today + timedelta(days=offset)
        assert find_leaks("\n".join(dataset.describe_day(day))) == [], day


def test_the_leak_guard_sees_whole_words_and_phone_numbers_only():
    assert find_leaks("Ask Pinny about it") == ["Pinny"] and find_leaks("pinnacle and amyloid and glamorous") == []
    assert find_leaks("call 908-555-0147 now") == ["908-555-0147"] and find_leaks("call 908-555-0147 now", allow_phone=True) == []
    assert find_leaks("call +1 555 010 0199", allowed=("+1 555 010 0199",)) == []
    assert find_leaks("Warren, NJ 07059") == ["07059", "NJ", "Warren"]


def test_the_greeting_boundaries_belong_to_the_one_timezone_and_nothing_else_in_the_demo_hard_codes_it():
    assert greeting_word(11.99) == "Good morning"
    offenders = []
    for path in sorted((BOOKING / "aria_booking" / "demo").glob("*.py")) + [BOOKING / "aria_booking" / "cli.py"]:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "America/New_York" in line and not (path.name == "__init__.py" and line.startswith("SPA_TIMEZONE")) and not line.lstrip().startswith("#"):
                offenders.append(f"{path.name}:{number}")
    assert offenders == [], offenders
