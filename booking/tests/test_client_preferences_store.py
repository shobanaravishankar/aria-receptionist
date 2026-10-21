"""Preferred technician per client and service family, at the storage level (demo only): additive, keyed by stable ids, idempotent, nothing inferred, nothing else touched."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aria_booking.appointments.lifecycle import open_lifecycle
from aria_booking.appointments.models import NewBooking, to_epoch
from aria_booking.appointments.preferences import NOTHING, REMOVED, REPLAYED, SAVED, UNCHANGED, PreferenceStore
from aria_booking.appointments.seed import ANSEL, WILLA
from aria_booking.appointments.store import AppointmentStore, StoreError

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 22, 10, 0, tzinfo=TZ)
SIENNA, ROWAN, MARISOL = "910006", "910007", "910001"
SWEDISH, DEEP = "massage-swedish-massage", "massage-deep-tissue-massage"


@pytest.fixture
def world(tmp_path):
    lifecycle = open_lifecycle(tmp_path / "prefs.sqlite3", today=NOW.date(), real_now=lambda: NOW)
    willa = next(c for c in lifecycle.store.clients_with_phone(WILLA[1]) if c.name == WILLA[0])
    ansel = next(c for c in lifecycle.store.clients_with_phone(ANSEL[1]) if c.name == ANSEL[0])
    return lifecycle, PreferenceStore(lifecycle.store), willa, ansel


# ---------------------------------------------------------------- additive, nothing else touched


def test_opening_it_adds_only_its_own_tables_and_leaves_the_schema_version_and_every_row_alone(tmp_path):
    lifecycle = open_lifecycle(tmp_path / "a.sqlite3", today=NOW.date(), real_now=lambda: NOW)
    before = lifecycle.store.counts()
    stamp = lifecycle.store.change_stamp()
    PreferenceStore(lifecycle.store)
    conn = sqlite3.connect(lifecycle.store.path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
    finally:
        conn.close()
    assert {"client_preferences", "preference_requests", "preference_events"} <= tables and version == "1", "no schema-version bump: an older build still opens the file"
    assert lifecycle.store.counts() == before and lifecycle.store.change_stamp() == stamp
    PreferenceStore(lifecycle.store)  # opening again changes nothing and does not fail
    assert AppointmentStore(lifecycle.store.path).counts() == before, "a build that knows nothing about preferences opens the same file"


def test_a_preference_never_changes_an_appointment_a_client_or_the_calendars_change_stamp(world):
    lifecycle, prefs, willa, _ansel = world
    before = (lifecycle.store.counts(), lifecycle.store.change_stamp(), [(a.reference, a.staff_id, a.start_utc, a.status, a.version) for a in lifecycle.store.for_client(willa.client_id)])
    prefs.set(willa.client_id, SWEDISH, SIENNA)
    prefs.set(willa.client_id, SWEDISH, ROWAN)
    prefs.remove(willa.client_id, SWEDISH)
    after = (lifecycle.store.counts(), lifecycle.store.change_stamp(), [(a.reference, a.staff_id, a.start_utc, a.status, a.version) for a in lifecycle.store.for_client(willa.client_id)])
    assert after == before, "existing bookings are exactly as they were"


# ---------------------------------------------------------------- save, read, change, remove


def test_save_read_change_and_remove_across_service_families(world):
    _lc, prefs, willa, _ = world
    assert prefs.get(willa.client_id, SWEDISH) is None and prefs.for_client(willa.client_id) == []
    first = prefs.set(willa.client_id, SWEDISH, SIENNA)
    assert first.status == SAVED and first.preference.staff_id == SIENNA and first.preference.version == 1 and first.previous is None
    other = prefs.set(willa.client_id, DEEP, ROWAN)
    assert other.status == SAVED and {p.family_id: p.staff_id for p in prefs.for_client(willa.client_id)} == {SWEDISH: SIENNA, DEEP: ROWAN}, "each family has its own"
    changed = prefs.set(willa.client_id, SWEDISH, MARISOL)
    assert changed.status == SAVED and changed.previous == SIENNA and changed.preference.version == 2 and prefs.get(willa.client_id, SWEDISH).staff_id == MARISOL
    assert prefs.get(willa.client_id, DEEP).staff_id == ROWAN, "the other family is untouched"
    removed = prefs.remove(willa.client_id, SWEDISH)
    assert removed.status == REMOVED and removed.previous == MARISOL and prefs.get(willa.client_id, SWEDISH) is None
    assert prefs.get(willa.client_id, DEEP).staff_id == ROWAN


def test_saving_the_same_technician_again_is_unchanged_and_removing_nothing_says_so(world):
    _lc, prefs, willa, _ = world
    prefs.set(willa.client_id, SWEDISH, SIENNA)
    again = prefs.set(willa.client_id, SWEDISH, SIENNA)
    assert again.status == UNCHANGED and again.preference.version == 1
    assert prefs.remove(willa.client_id, DEEP).status == NOTHING


def test_two_clients_who_share_a_household_number_never_share_a_preference(world):
    _lc, prefs, willa, ansel = world
    assert willa.phone == ansel.phone and willa.client_id != ansel.client_id
    prefs.set(willa.client_id, SWEDISH, SIENNA)
    assert prefs.get(ansel.client_id, SWEDISH) is None
    prefs.set(ansel.client_id, SWEDISH, ROWAN)
    assert prefs.get(willa.client_id, SWEDISH).staff_id == SIENNA and prefs.get(ansel.client_id, SWEDISH).staff_id == ROWAN
    prefs.remove(ansel.client_id, SWEDISH)
    assert prefs.get(willa.client_id, SWEDISH).staff_id == SIENNA


def test_a_preference_for_a_client_who_does_not_exist_is_refused_and_saves_nothing(world):
    _lc, prefs, _w, _a = world
    with pytest.raises(StoreError, match="does not exist"):
        prefs.set(999_999, SWEDISH, SIENNA)
    assert prefs.counts() == {"preferences": 0}


# ---------------------------------------------------------------- repeats and restarts


def test_a_repeated_request_is_answered_from_the_first_and_changes_nothing(world):
    _lc, prefs, willa, _ = world
    first = prefs.set(willa.client_id, SWEDISH, SIENNA, request_key="k1")
    repeat = prefs.set(willa.client_id, SWEDISH, SIENNA, request_key="k1")
    assert first.status == SAVED and repeat.status == REPLAYED and repeat.preference.version == 1
    prefs.set(willa.client_id, SWEDISH, ROWAN, request_key="k2")
    late_repeat = prefs.set(willa.client_id, SWEDISH, SIENNA, request_key="k1")  # an old request arriving again must not undo the newer change
    assert late_repeat.status == REPLAYED and prefs.get(willa.client_id, SWEDISH).staff_id == ROWAN
    assert prefs.remove(willa.client_id, SWEDISH, request_key="r1").status == REMOVED
    prefs.set(willa.client_id, SWEDISH, SIENNA, request_key="k3")
    assert prefs.remove(willa.client_id, SWEDISH, request_key="r1").status == REPLAYED and prefs.get(willa.client_id, SWEDISH).staff_id == SIENNA, "an old removal cannot remove a newer save"


def test_preferences_survive_a_restart_with_the_appointments(tmp_path):
    first = open_lifecycle(tmp_path / "r.sqlite3", today=NOW.date(), real_now=lambda: NOW)
    willa = next(c for c in first.store.clients_with_phone(WILLA[1]) if c.name == WILLA[0])
    PreferenceStore(first.store).set(willa.client_id, SWEDISH, SIENNA)
    counts = first.store.counts()
    second = open_lifecycle(tmp_path / "r.sqlite3", today=NOW.date(), real_now=lambda: NOW)
    again = PreferenceStore(second.store)
    assert second.store.counts() == counts, "nothing lost or seeded twice"
    assert again.get(willa.client_id, SWEDISH).staff_id == SIENNA and again.counts() == {"preferences": 1}


# ---------------------------------------------------------------- nothing is inferred from bookings


def test_booking_cancelling_and_moving_never_create_or_change_a_preference(world):
    lifecycle, prefs, willa, _ = world
    svc = lifecycle.dataset.registry.get("massage-swedish-massage-60")
    day = datetime(2026, 10, 23)
    booked = lifecycle.store.book(NewBooking(willa.name, willa.phone, svc.service_id, svc.booksy_name, "massage", SIENNA, to_epoch(datetime(2026, 10, 23, 16, 0, tzinfo=TZ)),
                                             svc.duration_minutes, svc.buffer_before_minutes, svc.buffer_after_minutes), validate=lifecycle.judge.validator())
    assert booked.status == "booked"
    lifecycle.store.reschedule(booked.appointment.reference, start_utc=to_epoch(datetime(2026, 10, 23, 17, 0, tzinfo=TZ)), validate=lifecycle.judge.validator())
    lifecycle.store.cancel(booked.appointment.reference)
    assert prefs.get(willa.client_id, SWEDISH) is None and prefs.counts() == {"preferences": 0}, "a booking with a technician is not a preference"
    prefs.set(willa.client_id, SWEDISH, ROWAN)
    other = lifecycle.store.book(NewBooking(willa.name, willa.phone, svc.service_id, svc.booksy_name, "massage", SIENNA, to_epoch(datetime(2026, 10, 24, 16, 0, tzinfo=TZ)),
                                            svc.duration_minutes, svc.buffer_before_minutes, svc.buffer_after_minutes), validate=lifecycle.judge.validator())
    assert other.status == "booked" and prefs.get(willa.client_id, SWEDISH).staff_id == ROWAN, "a one-off different technician leaves the saved preference alone"
    del day


# ---------------------------------------------------------------- what is logged


def test_the_events_carry_ids_and_versions_only(world):
    lifecycle, prefs, willa, _ = world
    prefs.set(willa.client_id, SWEDISH, SIENNA)
    prefs.set(willa.client_id, SWEDISH, ROWAN)
    prefs.remove(willa.client_id, SWEDISH)
    conn = sqlite3.connect(lifecycle.store.path)
    try:
        rows = conn.execute("SELECT client_id, family_id, kind, detail FROM preference_events ORDER BY event_id").fetchall()
    finally:
        conn.close()
    assert [r[2] for r in rows] == ["set", "set", "remove"] and all(r[0] == willa.client_id and r[1] == SWEDISH for r in rows)
    text = json.dumps(rows)
    for private in ("Willa", "Hartwell", "2125550142", "Sienna", "Rowan"):
        assert private not in text, "ids and versions only"
