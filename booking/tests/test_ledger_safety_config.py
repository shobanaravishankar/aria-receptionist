from __future__ import annotations

from datetime import datetime

import pytest

from aria_booking.config import Config, ConfigError
from aria_booking.ledger import Ledger, LedgerError, LedgerLocked, State, ref_from_key, request_key
from aria_booking.models import AppointmentSpec
from aria_booking.safety import NOTE_PREFIX, SafetyViolation, build_note, check_request, extract_ref

from conftest import NOW, TZ


# ---------------------------------------------------------------- ledger


def test_request_key_is_stable_and_sensitive_to_every_part():
    start = datetime(2026, 10, 12, 10, 0, tzinfo=TZ)
    base = request_key("1", "Shobs", "Aria Salon", start, 150)
    assert base == request_key("1", "shobs", "ARIA SALON", start, 150)  # case-insensitive names
    assert base != request_key("2", "Shobs", "Aria Salon", start, 150)
    assert base != request_key("1", "Other", "Aria Salon", start, 150)
    assert base != request_key("1", "Shobs", "Aria Salon", start.replace(minute=15), 150)
    assert base != request_key("1", "Shobs", "Aria Salon", start, 120)
    assert ref_from_key(base).startswith("ARIA-") and len(ref_from_key(base)) == 13


def test_ledger_round_trip_and_state_updates(tmp_path):
    ledger = Ledger(tmp_path / "l.json")
    ledger.create("k", "ARIA-AAAAAAAA", "Shobs", "Aria Salon", "s", "e", State.SAVING)
    assert ledger.get("k").state == State.SAVING
    ledger.set_state("k", State.VERIFIED, "ok")
    reopened = Ledger(tmp_path / "l.json")
    assert reopened.get("k").state == State.VERIFIED and reopened.get("k").detail == "ok"
    assert reopened.get("missing") is None


def test_ledger_lock_blocks_a_concurrent_run_and_is_released(tmp_path):
    ledger = Ledger(tmp_path / "l.json")
    with ledger.locked():
        with pytest.raises(LedgerLocked):
            with Ledger(tmp_path / "l.json").locked():
                pass
    with ledger.locked():  # released after the first block
        pass


def test_ledger_lock_is_released_even_if_the_run_fails(tmp_path):
    ledger = Ledger(tmp_path / "l.json")
    with pytest.raises(RuntimeError):
        with ledger.locked():
            raise RuntimeError("boom")
    with ledger.locked():
        pass


def test_corrupt_ledger_is_reported_and_never_overwritten(tmp_path):
    path = tmp_path / "l.json"
    path.write_text("{not json", encoding="utf-8")
    ledger = Ledger(path)
    with pytest.raises(LedgerError):
        ledger.get("k")
    with pytest.raises(LedgerError):
        ledger.create("k", "r", "s", "v", "a", "b", State.SAVING)
    assert path.read_text(encoding="utf-8") == "{not json"


# ---------------------------------------------------------------- safety


def _spec(cfg, **over):
    values = dict(
        staff=cfg.staff_name,
        service_name=cfg.service_name,
        start=datetime(2026, 10, 12, 10, 0, tzinfo=TZ),
        duration_minutes=cfg.service_duration_minutes,
        note=build_note("ARIA-0123ABCD"),
    )
    values.update(over)
    return AppointmentSpec(**values)


def _check(cfg, spec, **over):
    args = dict(now=NOW, observed_business_id=cfg.business_id, bookings_this_run=0)
    args.update(over)
    check_request(cfg, spec, **args)


def test_a_clean_request_passes(cfg):
    _check(cfg, _spec(cfg))


def test_every_violation_is_listed_not_just_the_first(cfg):
    spec = _spec(cfg, staff="Someone Else", service_name="Haircut", duration_minutes=30, note="hello", start=NOW)
    with pytest.raises(SafetyViolation) as err:
        _check(cfg, spec, observed_business_id="0000000", bookings_this_run=1)
    text = " | ".join(err.value.reasons)
    for expected in ("business", "staff", "service", "duration", "future", "ARIA TEST", "run limit"):
        assert expected in text


def test_note_without_the_marker_is_refused(cfg):
    with pytest.raises(SafetyViolation):
        _check(cfg, _spec(cfg, note="Ref: ARIA-0123ABCD"))
    with pytest.raises(SafetyViolation):
        _check(cfg, _spec(cfg, note=NOTE_PREFIX))  # marker but no reference


def test_extract_ref_round_trip():
    assert extract_ref(build_note("ARIA-DEADBEEF")) == "ARIA-DEADBEEF"
    assert extract_ref("no reference here") is None
    assert extract_ref("Ref: ARIA-deadbeef") is None  # lower-case is not a valid reference


# ---------------------------------------------------------------- config


def test_config_reads_environment_and_masks_the_business_id():
    cfg = Config.from_env({"ARIA_BOOKSY_BUSINESS_ID": "1234567", "ARIA_SERVICE_MINUTES": "90", "ARIA_ALLOW_DRIVER_DOWNLOAD": "yes"})
    assert cfg.business_id == "1234567" and cfg.service_duration_minutes == 90 and cfg.allow_driver_download
    summary = cfg.redacted_summary()
    assert summary["business_id"] == "****567" and "1234567" not in str(summary)


def test_config_defaults_require_an_explicit_business_id_and_forbid_driver_download():
    cfg = Config.from_env({})
    assert cfg.business_id == "" and not cfg.allow_driver_download
    with pytest.raises(ConfigError):
        cfg.require_business_id()
    with pytest.raises(ConfigError):
        cfg.calendar_url()


def test_config_rejects_bad_numbers():
    with pytest.raises(ConfigError):
        Config.from_env({"ARIA_SERVICE_MINUTES": "abc"})
    with pytest.raises(ConfigError):
        Config.from_env({"ARIA_MAX_BOOKINGS_PER_RUN": "0"})


def test_calendar_url_uses_the_configured_business():
    cfg = Config(business_id="1234567")
    assert "/1234567/calendar" in cfg.calendar_url("2026-10-12")
