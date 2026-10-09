"""The reviewed services/eligibility table: validation, review rules, freshness, the offline check command, and `serve` using it."""

from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aria_booking import cli
from aria_booking.catalog.mapping_table import MappingTableError, build_registry, load_mapping_table
from aria_booking.config import Config
from aria_booking.voice import retell_http
from aria_booking.voice.tools import VoiceTools

from fakes import FakeMultiCalendar

TZ = ZoneInfo("America/New_York")
EXAMPLE = Path(__file__).resolve().parent.parent / "mapping_table.example.json"
DATA = json.loads(EXAMPLE.read_text(encoding="utf-8"))
CAPTURED = datetime.fromisoformat(DATA["captured_at"])
FRESH = CAPTURED + timedelta(days=1)
DAY = date(2026, 10, 12)


def table(**changes):
    data = copy.deepcopy(DATA)
    data.update(changes)
    return data


def row(**changes):
    r = copy.deepcopy(DATA["rows"][1])
    r.update(changes)
    return r


# ---------------------------------------------------------------- the example and what "usable" means


def test_the_synthetic_example_loads_and_its_verified_rows_are_usable():
    registry, report = load_mapping_table(EXAMPLE, now=FRESH)
    assert report.rows == 4 and report.verified == 3 and not report.stale
    assert registry.get("massage-deep-tissue-massage-60").verified and registry.get("massage-deep-tissue-massage-60").duration_minutes == 60
    assert registry.get("massage-deep-tissue-massage-60").eligible("anyone", "900002") and not registry.get("massage-deep-tissue-massage-60").eligible("anyone", "900003")
    assert report.unverified == ("facial-signature-facial-60: not yet reviewed",)
    assert not registry.get("facial-signature-facial-60").verified


def test_the_example_is_obviously_synthetic_and_holds_nothing_real():
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "SYNTHETIC EXAMPLE ONLY" in text and "EXAMPLE-ONLY" in text
    assert all(i.startswith("9000") for i in DATA["staff"]) and all(n.startswith("Example") for n in DATA["staff"].values())
    import re

    assert "@" not in text
    long_numbers = re.findall(r"\b\d{6,}\b", text)
    assert long_numbers and all(n.startswith("9000") for n in long_numbers), "every long number in the example is a made-up 9000xx id"


def test_an_unreviewed_row_is_listed_but_never_usable():
    registry, report = build_registry(table(), now=FRESH)
    assert not registry.get("facial-signature-facial-60").verified and "not yet reviewed" in report.unverified[0]


def test_a_verified_row_must_name_its_reviewer_and_date():
    for bad in ({"reviewed_by": ""}, {"reviewed_by": None}, {"reviewed_on": ""}, {"reviewed_on": None}):
        with pytest.raises(MappingTableError, match="reviewed_by"):
            build_registry(table(rows=[row(**bad)]), now=FRESH)
    with pytest.raises(MappingTableError, match="ISO date"):
        build_registry(table(rows=[row(reviewed_on="yesterday")]), now=FRESH)


# ---------------------------------------------------------------- freshness


def test_a_stale_table_makes_every_row_unverified():
    registry, report = build_registry(table(), now=CAPTURED + timedelta(days=14, minutes=1))
    assert report.stale and report.verified == 0 and not any(registry.get(i).verified for i in registry.ids())
    assert all("table is stale" in line for line in report.unverified[:3]) and "STALE" in report.summary()


def test_the_age_limit_is_exact_and_configurable():
    assert not build_registry(table(), now=CAPTURED + timedelta(days=14))[1].stale
    assert build_registry(table(max_age_days=2), now=CAPTURED + timedelta(days=3))[1].stale
    with pytest.raises(MappingTableError, match="max_age_days"):
        build_registry(table(max_age_days=0), now=FRESH)
    with pytest.raises(MappingTableError, match="max_age_days"):
        build_registry(table(max_age_days=True), now=FRESH)


def test_a_table_dated_in_the_future_is_refused():
    with pytest.raises(MappingTableError, match="in the future"):
        build_registry(table(), now=CAPTURED - timedelta(days=1))


# ---------------------------------------------------------------- malformed tables fail loudly


@pytest.mark.parametrize("data, match", [
    ([], "JSON object"), ({}, "version"), (table(version=2), "version"), (table(captured_at=None), "ISO"), (table(captured_at="2026-10-09T09:00:00"), "UTC offset"),
    (table(captured_at="not a date"), "ISO"), (table(rows=[]), "non-empty"), (table(rows="x"), "non-empty"), (table(rows=["x"]), "must be an object"),
])
def test_a_malformed_table_is_refused(data, match):
    with pytest.raises(MappingTableError, match=match):
        build_registry(data, now=FRESH)


@pytest.mark.parametrize("changes, match", [
    ({"service_id": ""}, "service_id"), ({"booksy_name": ""}, "booksy_name"), ({"booksy_name": 5}, "booksy_name"),
    ({"duration_minutes": 0}, "duration_minutes"), ({"duration_minutes": 4}, "duration_minutes"), ({"duration_minutes": "60"}, "duration_minutes"),
    ({"duration_minutes": True}, "duration_minutes"), ({"buffer_before_minutes": -1}, "buffer_before_minutes"),
    ({"eligible_staff_ids": "900001"}, "eligible_staff_ids"), ({"eligible_staff_ids": [900001]}, "eligible_staff_ids"),
    ({"eligible_staff_ids": ["abc"]}, "eligible_staff_ids"), ({"eligible_staff_ids": ["900001", "900001"]}, "repeats"),
    ({"eligible_staff_ids": None}, "eligible_staff_ids"), ({"verified": "yes"}, "verified"), ({"verified": None}, "verified"),
    ({"price_usd": 0}, "price_usd"), ({"price_usd": "85"}, "price_usd"),
    ({"catalog_item": "no-such-item"}, "unknown catalogue item"),
    ({"duration_minutes": 75}, "differs from the website"),
])
def test_each_kind_of_bad_row_is_refused_with_its_reason(changes, match):
    with pytest.raises(MappingTableError, match=match):
        build_registry(table(rows=[row(**changes)]), now=FRESH)


def test_duplicate_service_ids_are_refused():
    with pytest.raises(MappingTableError, match="duplicate service_id"):
        build_registry(table(rows=[row(), row()]), now=FRESH)


def test_an_empty_eligibility_list_is_legal_and_means_nobody():
    registry, _ = build_registry(table(rows=[row(eligible_staff_ids=[])]), now=FRESH)
    assert not registry.get("massage-deep-tissue-massage-60").eligible("x", "900001")


def test_a_missing_or_broken_file_is_refused(tmp_path):
    with pytest.raises(MappingTableError, match="not found"):
        load_mapping_table(tmp_path / "nope.json", now=FRESH)
    broken = tmp_path / "t.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(MappingTableError, match="valid JSON"):
        load_mapping_table(broken, now=FRESH)


# ---------------------------------------------------------------- the voice uses it


def stack(now_offset_days=1):
    registry, _ = build_registry(table(), now=CAPTURED + timedelta(days=now_offset_days))
    ids = {"Example Lily": "900001", "Example Maya": "900002", "Example Noor": "900003"}
    calendar = FakeMultiCalendar(TZ, list(ids), ids=ids, business_id="9999999", now=datetime(2026, 10, 8, 12, tzinfo=TZ))
    calendar.set_all_hours(DAY)
    tools = VoiceTools(Config(business_id="9999999"), calendar, lambda *a: None, lambda: datetime(2026, 10, 8, 12, tzinfo=TZ), registry=registry,
                       require_service_id=True, availability_only=True)
    return tools, calendar


def check(tools, service, staff=None):
    args = {"service_id": service, "date": DAY.isoformat(), "time": "11:00"}
    if staff:
        args["staff"] = staff
    return tools.check_slot("c", args)


def test_availability_follows_the_reviewed_eligibility_by_id():
    tools, _ = stack()
    assert check(tools, "massage-deep-tissue-massage-60")["options"][0]["technician"] == "Example Lily"
    assert check(tools, "massage-deep-tissue-massage-90", "Maya")["reason"] == "staff_not_eligible", "only Lily is reviewed for the 90-minute length"
    assert check(tools, "massage-deep-tissue-massage-90")["options"][0]["technician"] == "Example Lily"


def test_an_unreviewed_row_cannot_be_checked_even_though_it_is_listed():
    tools, calendar = stack()
    reply = check(tools, "facial-signature-facial-60")
    assert reply["status"] == "not_bookable" and calendar.read_calls == 0


def test_a_stale_table_stops_every_availability_check_without_touching_the_calendar():
    tools, calendar = stack(now_offset_days=30)
    reply = check(tools, "massage-deep-tissue-massage-60")
    assert reply["status"] == "not_bookable" and calendar.read_calls == 0
    assert "can't book it online yet" in reply["speak"] and "contact the salon directly" in reply["speak"]


def test_the_lookup_reports_the_reviewed_mapping_so_the_agent_can_use_its_id():
    tools, _ = stack()
    info = tools.lookup_service("c", {"query": "deep tissue massage 60 minutes"})
    assert info["bookable"] is True and info["bookable_service_id"] == "massage-deep-tissue-massage-60"
    assert tools.lookup_service("c", {"query": "signature facial"})["bookable"] is False


# ---------------------------------------------------------------- the offline check command and serve


NOW = datetime(2026, 10, 10, 12, tzinfo=TZ)


def run(argv, env):
    lines = []
    code = cli.main(argv, environ=env, clock=lambda: NOW, out=lines.append, driver_factory=lambda cfg: (_ for _ in ()).throw(AssertionError("no browser")))
    return code, "\n".join(lines)


def test_mapping_check_reports_a_good_table_with_exit_zero():
    code, text = run(["mapping-check", str(EXAMPLE)], {})
    assert code == cli.EXIT_OK and "3 verified" in text and "not usable: facial-signature-facial-60: not yet reviewed" in text


def test_mapping_check_exits_three_for_a_stale_table_and_two_for_a_bad_one(tmp_path):
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps(table(captured_at="2026-01-01T00:00:00-05:00")), encoding="utf-8")
    code, text = run(["mapping-check", str(stale)], {})
    assert code == cli.EXIT_REVIEW and "STALE" in text
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(table(rows=[row(duration_minutes=0)])), encoding="utf-8")
    code, text = run(["mapping-check", str(bad)], {})
    assert code == cli.EXIT_REFUSED and text.startswith("invalid:")
    assert run(["mapping-check", str(tmp_path / "missing.json")], {})[0] == cli.EXIT_REFUSED


def test_mapping_check_uses_the_environment_path_and_needs_one():
    assert run(["mapping-check"], {"ARIA_MAPPING_TABLE": str(EXAMPLE)})[0] == cli.EXIT_OK
    code, text = run(["mapping-check"], {})
    assert code == cli.EXIT_REFUSED and "ARIA_MAPPING_TABLE" in text


def test_mapping_check_exits_three_when_nothing_in_the_table_is_usable(tmp_path):
    none = tmp_path / "none.json"
    none.write_text(json.dumps(table(rows=[row(verified=False, reviewed_by="", reviewed_on="")])), encoding="utf-8")
    assert run(["mapping-check", str(none)], {})[0] == cli.EXIT_REVIEW


ENV = {"ARIA_BOOKSY_BUSINESS_ID": "1234567", "ARIA_LIVE_BOOKSY": "1", "ARIA_RETELL_API_KEY": "test-key-not-real"}
SERVE = ["serve", "--confirm-business-id", "1234567", "--port", "18787"]


def test_serve_refuses_a_malformed_table_before_any_browser_exists(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    created = []
    lines = []
    code = cli.main(SERVE, environ={**ENV, "ARIA_MAPPING_TABLE": str(bad)}, driver_factory=lambda cfg: created.append(1), clock=lambda: NOW, out=lines.append)
    assert code == cli.EXIT_REFUSED and created == [] and "mapping table cannot be used" in "\n".join(lines)


def test_serve_loads_the_table_and_the_endpoint_uses_its_registry(monkeypatch):
    captured = {}

    class Stub:
        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    class Driver:
        def __init__(self, cfg):
            pass

        def verify_business(self):
            return "1234567"

        def close(self):
            pass

    monkeypatch.setattr(retell_http, "make_http_server", lambda endpoint, port, host="127.0.0.1": captured.setdefault("endpoint", endpoint) and Stub())
    lines = []
    code = cli.main(SERVE, environ={**ENV, "ARIA_MAPPING_TABLE": str(EXAMPLE)}, driver_factory=Driver, clock=lambda: NOW, out=lines.append)
    assert code == cli.EXIT_OK and "mapping table: 4 rows, 3 verified" in "\n".join(lines)
    assert "massage-deep-tissue-massage-60" in captured["endpoint"]._tools.registry.ids()


def test_the_config_exposes_the_table_path_without_other_secrets():
    cfg = Config.from_env({"ARIA_MAPPING_TABLE": "C:/somewhere/table.json", "ARIA_BOOKSY_BUSINESS_ID": "1234567"})
    assert str(cfg.mapping_table).replace("\\", "/").endswith("somewhere/table.json")
    summary = json.dumps(cfg.redacted_summary())
    assert "table.json" in summary and "1234567" not in summary
    assert Config.from_env({}).mapping_table is None and Config.from_env({"ARIA_MAPPING_TABLE": "  "}).mapping_table is None
