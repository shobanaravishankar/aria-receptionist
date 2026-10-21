"""A client's preferred technician for a service family, kept in the SAME simulated appointment file (demo only).

Why this shape:
  * Keys are stable ids, never names: the client's id (so two clients who share a household number never share a preference), the catalog's service FAMILY (every length of
    'Swedish Massage', not one length), and the technician's stable staff id.
  * It is purely additive. Two new tables are created in the existing file the first time this is opened (``CREATE TABLE IF NOT EXISTS``); the schema version stays 1, so a build
    that knows nothing about preferences still opens the file and ignores them. No appointment or client row is read or changed by anything here.
  * Every change is one write transaction (BEGIN IMMEDIATE) that also remembers its request key, so a repeated confirmed request is answered from the first result and changes nothing.
  * Nothing is ever inferred: a row exists only because ``set`` was called for it. Booking, cancelling and moving never call it, so a one-off choice of technician, or a past visit,
    can never create or change a preference.
  * Logs and events carry ids and versions only, no names and no phone numbers.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Optional

from .store import AppointmentStore, StoreError

DDL = (
    """CREATE TABLE IF NOT EXISTS client_preferences (
        client_id INTEGER NOT NULL REFERENCES clients (client_id),
        family_id TEXT NOT NULL,
        staff_id TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (client_id, family_id)
    )""",
    """CREATE TABLE IF NOT EXISTS preference_requests (
        request_key TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        client_id INTEGER NOT NULL,
        family_id TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS preference_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER NOT NULL,
        family_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        detail TEXT NOT NULL,
        at INTEGER NOT NULL
    )""",
)
SAVED, UNCHANGED, REMOVED, NOTHING, REPLAYED = "saved", "unchanged", "removed", "nothing_to_remove", "replayed"


@dataclass(frozen=True)
class Preference:
    client_id: int
    family_id: str
    staff_id: str
    version: int
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class PreferenceOutcome:
    status: str  # saved / unchanged / removed / nothing_to_remove / replayed
    preference: Optional[Preference] = None  # the state AFTER the call (None once removed)
    previous: Optional[str] = None  # the staff id it held before, for a change


def _preference(row: sqlite3.Row) -> Preference:
    return Preference(row["client_id"], row["family_id"], row["staff_id"], row["version"], row["created_at"], row["updated_at"])


class PreferenceStore:
    def __init__(self, store: AppointmentStore):
        self._store = store
        with store._write() as tx:  # additive: only creates what is missing
            for statement in DDL:
                tx._conn.execute(statement)

    # ---- reads ----------------------------------------------------------------------------------------------
    def get(self, client_id: int, family_id: str) -> Optional[Preference]:
        with self._store._read() as tx:
            row = tx._conn.execute("SELECT * FROM client_preferences WHERE client_id = ? AND family_id = ?", (client_id, family_id)).fetchone()
            return _preference(row) if row else None

    def for_client(self, client_id: int) -> list[Preference]:
        with self._store._read() as tx:
            return [_preference(r) for r in tx._conn.execute("SELECT * FROM client_preferences WHERE client_id = ? ORDER BY family_id", (client_id,)).fetchall()]

    def counts(self) -> dict:
        with self._store._read() as tx:
            return {"preferences": tx._conn.execute("SELECT COUNT(*) FROM client_preferences").fetchone()[0]}

    # ---- writes ---------------------------------------------------------------------------------------------
    def set(self, client_id: int, family_id: str, staff_id: str, *, request_key: Optional[str] = None) -> PreferenceOutcome:
        """Save (or change) the one preferred technician for this client and service family. A repeat of the same ``request_key`` returns the first result and writes nothing.
        A client who does not exist is a ``StoreError``: nothing is saved for nobody."""
        try:
            return self._set(client_id, family_id, staff_id, request_key)
        except sqlite3.IntegrityError:
            raise StoreError("that client does not exist, so nothing was saved") from None

    def _set(self, client_id: int, family_id: str, staff_id: str, request_key: Optional[str]) -> PreferenceOutcome:
        with self._store._write() as tx:
            conn = tx._conn
            if request_key:
                seen = conn.execute("SELECT 1 FROM preference_requests WHERE request_key = ? AND kind = 'set'", (request_key,)).fetchone()
                if seen:
                    row = conn.execute("SELECT * FROM client_preferences WHERE client_id = ? AND family_id = ?", (client_id, family_id)).fetchone()
                    return PreferenceOutcome(REPLAYED, _preference(row) if row else None)
            current = conn.execute("SELECT * FROM client_preferences WHERE client_id = ? AND family_id = ?", (client_id, family_id)).fetchone()
            now = tx.now_utc
            if current is not None and current["staff_id"] == staff_id:
                outcome = PreferenceOutcome(UNCHANGED, _preference(current))
            elif current is None:
                conn.execute("INSERT INTO client_preferences (client_id, family_id, staff_id, version, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                             (client_id, family_id, staff_id, now, now))
                outcome = PreferenceOutcome(SAVED, _preference(conn.execute("SELECT * FROM client_preferences WHERE client_id = ? AND family_id = ?", (client_id, family_id)).fetchone()))
            else:
                conn.execute("UPDATE client_preferences SET staff_id = ?, version = version + 1, updated_at = ? WHERE client_id = ? AND family_id = ?", (staff_id, now, client_id, family_id))
                outcome = PreferenceOutcome(SAVED, _preference(conn.execute("SELECT * FROM client_preferences WHERE client_id = ? AND family_id = ?", (client_id, family_id)).fetchone()),
                                            previous=current["staff_id"])
            if outcome.status == SAVED:
                conn.execute("INSERT INTO preference_events (client_id, family_id, kind, detail, at) VALUES (?, ?, 'set', ?, ?)",
                             (client_id, family_id, json.dumps({"staff": staff_id, "version": outcome.preference.version}), now))
            if request_key:
                conn.execute("INSERT OR IGNORE INTO preference_requests (request_key, kind, client_id, family_id, created_at) VALUES (?, 'set', ?, ?, ?)", (request_key, client_id, family_id, now))
            return outcome

    def remove(self, client_id: int, family_id: str, *, request_key: Optional[str] = None) -> PreferenceOutcome:
        with self._store._write() as tx:
            conn = tx._conn
            if request_key and conn.execute("SELECT 1 FROM preference_requests WHERE request_key = ? AND kind = 'remove'", (request_key,)).fetchone():
                return PreferenceOutcome(REPLAYED, None)
            current = conn.execute("SELECT * FROM client_preferences WHERE client_id = ? AND family_id = ?", (client_id, family_id)).fetchone()
            now = tx.now_utc
            if current is None:
                outcome = PreferenceOutcome(NOTHING, None)
            else:
                conn.execute("DELETE FROM client_preferences WHERE client_id = ? AND family_id = ?", (client_id, family_id))
                conn.execute("INSERT INTO preference_events (client_id, family_id, kind, detail, at) VALUES (?, ?, 'remove', ?, ?)",
                             (client_id, family_id, json.dumps({"staff": current["staff_id"], "version": current["version"]}), now))
                outcome = PreferenceOutcome(REMOVED, None, previous=current["staff_id"])
            if request_key:
                conn.execute("INSERT OR IGNORE INTO preference_requests (request_key, kind, client_id, family_id, created_at) VALUES (?, 'remove', ?, ?, ?)", (request_key, client_id, family_id, now))
            return outcome
