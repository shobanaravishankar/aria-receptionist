"""The simulated appointment store: ONE local SQLite file shared by the calendar page and the voice tools.

Why this shape (the reviewed engineering boundary):
  * Every change is ONE transaction opened with BEGIN IMMEDIATE, so two callers racing for the same slot are serialised by the database itself: the second
    one re-checks against the first one's committed row and is refused. Nothing is held in memory that a restart could lose.
  * Stable identity. A row gets its reference when it is created and keeps it for life (a reschedule moves the SAME row). Nothing is regenerated from
    the date, so a restart or a new day cannot renumber anything.
  * Commit-time re-check. ``book`` and ``reschedule`` call a validator INSIDE the write transaction. The validator sees the transaction's own rows, so
    "free" is decided against exactly the state the insert will join. The store also keeps two invariants of its own, whatever the validator says: one
    technician is never in two overlapping appointments, and one client is never in two.
  * Idempotency. A request key is saved in the same transaction as its result; a repeat returns that result and changes nothing.
  * Notifications are an OUTBOX: the message row is written in the same transaction as the change it reports, so it exists if and only if the change
    committed, and a repeat or retry (the dedupe key) can never produce a second one. Sending is a separate step that never rolls a saved appointment back.
  * Logs and events carry references and times only: no names, no phone numbers.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

from .models import (
    CANCELLED, COMPLETED, CONFIRMED, NO_SHOW, ORIGIN_SEED, ORIGIN_TEST, STATUSES, ClientRecord, NewBooking, Outcome, OutboxMessage, Placement, Refused,
    StoredAppointment, name_key,
)

SCHEMA_VERSION = 1
REFERENCE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # no 0 / 1 / I / L / O: a reference read aloud or copied by hand is not misheard
REFERENCE_LENGTH = 6
BUSY_TIMEOUT_SECONDS = 20.0
MAX_REASON_LENGTH = 200

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS clients (
    client_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    phone TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE (phone, name_key)
);
CREATE INDEX IF NOT EXISTS clients_phone ON clients (phone);
CREATE TABLE IF NOT EXISTS appointments (
    appt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL UNIQUE,
    client_id INTEGER NOT NULL REFERENCES clients (client_id),
    service_id TEXT NOT NULL,
    service_name TEXT NOT NULL,
    category TEXT NOT NULL,
    staff_id TEXT NOT NULL,
    start_utc INTEGER NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    buffer_before_minutes INTEGER NOT NULL DEFAULT 0,
    buffer_after_minutes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'cancelled', 'completed', 'no_show')),
    version INTEGER NOT NULL DEFAULT 1,
    origin TEXT NOT NULL,
    seed_key TEXT UNIQUE,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    cancelled_at INTEGER,
    cancel_reason TEXT
);
CREATE INDEX IF NOT EXISTS appointments_staff_time ON appointments (staff_id, start_utc);
CREATE INDEX IF NOT EXISTS appointments_client_time ON appointments (client_id, start_utc);
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL,
    at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    request_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    reference TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    reference TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'uncertain')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at INTEGER NOT NULL,
    claimed_at INTEGER,
    sent_at INTEGER
);
"""


class StoreError(Exception):
    """The store could not do what was asked (the file is locked past the timeout, unreadable, or from a newer version). Nothing was changed."""


class Txn:
    """The view a validator gets INSIDE the write transaction. Reads see this transaction's own state, and no one else can write until it ends."""

    def __init__(self, conn: sqlite3.Connection, now_utc: int):
        self._conn, self.now_utc = conn, now_utc

    def _all(self, where: str, args: Sequence = ()) -> list[StoredAppointment]:
        rows = self._conn.execute(
            "SELECT a.*, c.name AS client_name FROM appointments a JOIN clients c ON c.client_id = a.client_id " + where, tuple(args)
        ).fetchall()
        return [_appointment(r) for r in rows]

    def get(self, reference: str) -> Optional[StoredAppointment]:
        found = self._all("WHERE a.reference = ?", (reference,))
        return found[0] if found else None

    def between(self, start_utc: int, end_utc: int, *, staff_id: Optional[str] = None, statuses: Sequence[str] = (CONFIRMED,)) -> list[StoredAppointment]:
        """Appointments (in the given statuses) whose SERVICE time overlaps [start, end), oldest first."""
        marks = ",".join("?" for _ in statuses)
        where = f"WHERE a.status IN ({marks}) AND a.start_utc < ? AND a.start_utc + a.duration_minutes * 60 > ?"
        args: list = [*statuses, end_utc, start_utc]
        if staff_id is not None:
            where += " AND a.staff_id = ?"
            args.append(staff_id)
        return self._all(where + " ORDER BY a.start_utc, a.appt_id", args)

    def for_client(self, client_id: int, statuses: Sequence[str] = STATUSES) -> list[StoredAppointment]:
        marks = ",".join("?" for _ in statuses)
        return self._all(f"WHERE a.client_id = ? AND a.status IN ({marks}) ORDER BY a.start_utc, a.appt_id", (client_id, *statuses))

    def find_client(self, name: str, phone: str) -> Optional[ClientRecord]:
        row = self._conn.execute("SELECT * FROM clients WHERE phone = ? AND name_key = ?", (phone, name_key(name))).fetchone()
        return _client(row) if row else None

    def clients_with_phone(self, phone: str) -> list[ClientRecord]:
        return [_client(r) for r in self._conn.execute("SELECT * FROM clients WHERE phone = ? ORDER BY client_id", (phone,)).fetchall()]

    def client(self, client_id: int) -> Optional[ClientRecord]:
        row = self._conn.execute("SELECT * FROM clients WHERE client_id = ?", (client_id,)).fetchone()
        return _client(row) if row else None

    def seeded_after(self, prefix: str, after_utc: int) -> bool:
        """Is there a seeded row (any status) whose seed key starts with ``prefix`` and whose start is after the given moment?"""
        row = self._conn.execute(
            "SELECT 1 FROM appointments WHERE substr(seed_key, 1, ?) = ? AND start_utc > ? LIMIT 1", (len(prefix), prefix, after_utc)
        ).fetchone()
        return row is not None


def _client(row: sqlite3.Row) -> ClientRecord:
    return ClientRecord(row["client_id"], row["name"], row["phone"])


def _appointment(row: sqlite3.Row) -> StoredAppointment:
    return StoredAppointment(
        row["appt_id"], row["reference"], row["client_id"], row["client_name"], row["service_id"], row["service_name"], row["category"], row["staff_id"],
        row["start_utc"], row["duration_minutes"], row["buffer_before_minutes"], row["buffer_after_minutes"], row["status"], row["version"], row["origin"],
        row["created_at"], row["updated_at"], row["cancelled_at"], row["cancel_reason"], row["seed_key"],
    )


Validator = Callable[[Txn, Placement], None]
OutboxBuilder = Callable[[StoredAppointment, Optional[StoredAppointment]], Optional[OutboxMessage]]


class AppointmentStore:
    def __init__(self, path: Path, *, clock: Callable[[], int] = lambda: int(time.time())):
        self.path = Path(path)
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(DDL)
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if row is None:
                conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            elif int(row["value"]) != SCHEMA_VERSION:
                raise StoreError(f"this appointment file is schema version {row['value']}, but this code reads version {SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise StoreError(f"cannot prepare the appointment file: {type(exc).__name__}") from None
        finally:
            conn.close()

    # ---- connections and transactions --------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
            conn.execute("PRAGMA foreign_keys = ON")
            return conn
        except sqlite3.Error as exc:
            raise StoreError(f"cannot open the appointment file: {type(exc).__name__}") from None

    @contextmanager
    def _write(self) -> Iterator[Txn]:
        conn = self._connect()
        try:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise StoreError(f"the appointment file is busy or unreadable: {type(exc).__name__}") from None
            try:
                yield Txn(conn, self._clock())
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        finally:
            conn.close()

    @contextmanager
    def _read(self) -> Iterator[Txn]:
        conn = self._connect()
        try:
            conn.execute("BEGIN")  # a consistent snapshot for the whole read; it takes no write lock
            try:
                yield Txn(conn, self._clock())
            finally:
                conn.execute("ROLLBACK")
        finally:
            conn.close()

    # ---- reads ---------------------------------------------------------------------------------------------
    def get(self, reference: str) -> Optional[StoredAppointment]:
        with self._read() as tx:
            return tx.get(reference)

    def between(self, start_utc: int, end_utc: int, *, staff_id: Optional[str] = None, statuses: Sequence[str] = (CONFIRMED,)) -> list[StoredAppointment]:
        with self._read() as tx:
            return tx.between(start_utc, end_utc, staff_id=staff_id, statuses=statuses)

    def clients_with_phone(self, phone: str) -> list[ClientRecord]:
        with self._read() as tx:
            return tx.clients_with_phone(phone)

    def client(self, client_id: int) -> Optional[ClientRecord]:
        with self._read() as tx:
            return tx.client(client_id)

    def seeded_after(self, prefix: str, after_utc: int) -> bool:
        with self._read() as tx:
            return tx.seeded_after(prefix, after_utc)

    def for_client(self, client_id: int, statuses: Sequence[str] = STATUSES) -> list[StoredAppointment]:
        with self._read() as tx:
            return tx.for_client(client_id, statuses)

    def counts(self) -> dict:
        """Totals only: safe to log."""
        with self._read() as tx:
            conn = tx._conn
            by_status = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM appointments GROUP BY status")}
            outbox = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM outbox GROUP BY status")}
            return {"appointments": by_status, "clients": conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0], "outbox": outbox}

    def outbox_rows(self, status: Optional[str] = None) -> list[dict]:
        with self._read() as tx:
            sql, args = "SELECT * FROM outbox", ()
            if status is not None:
                sql, args = sql + " WHERE status = ?", (status,)
            return [dict(r) for r in tx._conn.execute(sql + " ORDER BY outbox_id", args).fetchall()]

    def events(self, reference: str) -> list[dict]:
        with self._read() as tx:
            rows = tx._conn.execute("SELECT kind, detail, at FROM events WHERE reference = ? ORDER BY event_id", (reference,)).fetchall()
            return [{"kind": r["kind"], "detail": json.loads(r["detail"]), "at": r["at"]} for r in rows]

    # ---- the writers ----------------------------------------------------------------------------------------
    def book(self, booking: NewBooking, *, validate: Optional[Validator] = None, outbox: Optional[OutboxBuilder] = None) -> Outcome:
        """Save one appointment, or say precisely why not. ``validate(txn, placement)`` runs inside the write transaction and raises ``Refused``."""
        try:
            with self._write() as tx:
                replay = self._replayed(tx, booking.request_key, "book")
                if replay is not None:
                    return replay
                if booking.seed_key:  # a seed is saved once: running the seeding again (or after the row was cancelled) never makes it again
                    seeded = tx._conn.execute("SELECT reference FROM appointments WHERE seed_key = ?", (booking.seed_key,)).fetchone()
                    if seeded:
                        return Outcome("replayed", tx.get(seeded["reference"]))
                placement = Placement.of(booking)
                if validate is not None:
                    validate(tx, placement)
                client = self._client_for(tx, booking.client_name, booking.phone)
                self._guard(tx, placement, client.client_id)
                reference = self._new_reference(tx, booking.seed_key)
                now = tx.now_utc
                tx._conn.execute(
                    "INSERT INTO appointments (reference, client_id, service_id, service_name, category, staff_id, start_utc, duration_minutes, "
                    "buffer_before_minutes, buffer_after_minutes, status, version, origin, seed_key, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', 1, ?, ?, ?, ?)",
                    (reference, client.client_id, booking.service_id, booking.service_name, booking.category, booking.staff_id, booking.start_utc,
                     booking.duration_minutes, booking.buffer_before_minutes, booking.buffer_after_minutes, booking.origin, booking.seed_key, now, now),
                )
                saved = tx.get(reference)
                self._event(tx, reference, "booked", {"start": saved.start_utc, "staff": saved.staff_id, "service": saved.service_id, "origin": saved.origin})
                self._remember(tx, booking.request_key, "book", reference)
                self._notify(tx, "booked", saved, None, outbox)
                return Outcome("booked", saved)
        except Refused as refusal:
            return Outcome(refusal.status, refusal.appointment, reason=refusal.reason, detail=refusal.detail)

    def add_history(self, booking: NewBooking, status: str) -> Outcome:
        """Seed or test only: a PAST visit with a final status. It is never validated against availability and never notifies anyone."""
        if status not in (COMPLETED, NO_SHOW, CANCELLED) or booking.origin not in (ORIGIN_SEED, ORIGIN_TEST):
            raise ValueError("history rows are for seeding and tests only, with a final status")
        try:
            with self._write() as tx:
                if booking.seed_key:
                    existing = tx._conn.execute("SELECT reference FROM appointments WHERE seed_key = ?", (booking.seed_key,)).fetchone()
                    if existing:
                        return Outcome("replayed", tx.get(existing["reference"]))
                client = self._client_for(tx, booking.client_name, booking.phone)
                reference = self._new_reference(tx, booking.seed_key)
                now = tx.now_utc
                tx._conn.execute(
                    "INSERT INTO appointments (reference, client_id, service_id, service_name, category, staff_id, start_utc, duration_minutes, "
                    "buffer_before_minutes, buffer_after_minutes, status, version, origin, seed_key, created_at, updated_at, cancelled_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
                    (reference, client.client_id, booking.service_id, booking.service_name, booking.category, booking.staff_id, booking.start_utc,
                     booking.duration_minutes, booking.buffer_before_minutes, booking.buffer_after_minutes, status, booking.origin, booking.seed_key, now, now,
                     now if status == CANCELLED else None),
                )
                saved = tx.get(reference)
                self._event(tx, reference, "history", {"status": status, "origin": saved.origin})
                return Outcome("booked", saved)
        except Refused as refusal:
            return Outcome(refusal.status, refusal.appointment, reason=refusal.reason, detail=refusal.detail)

    def cancel(
        self, reference: str, *, expected_version: Optional[int] = None, reason: Optional[str] = None, request_key: Optional[str] = None,
        outbox: Optional[OutboxBuilder] = None,
    ) -> Outcome:
        """Cancel ONE appointment by its reference. A repeat changes nothing and touches no other appointment."""
        try:
            with self._write() as tx:
                replay = self._replayed(tx, request_key, "cancel")
                if replay is not None:
                    return replay
                row = tx.get(reference)
                if row is None:
                    raise Refused("not_found", "no_such_reference")
                if row.status == CANCELLED:
                    return Outcome("already_cancelled", row)  # before the version check: a repeat carries the version from BEFORE the first cancel
                if row.status != CONFIRMED:
                    raise Refused("not_cancellable", row.status, appointment=row)
                if expected_version is not None and row.version != expected_version:
                    raise Refused("stale", "changed_since_confirmation", appointment=row)
                if row.start_utc <= tx.now_utc:
                    raise Refused("not_cancellable", "already_started", appointment=row)
                now = tx.now_utc
                cleaned = _clean_reason(reason)
                tx._conn.execute(
                    "UPDATE appointments SET status = 'cancelled', version = version + 1, updated_at = ?, cancelled_at = ?, cancel_reason = ? WHERE reference = ?",
                    (now, now, cleaned, reference),
                )
                saved = tx.get(reference)
                self._event(tx, reference, "cancelled", {"start": row.start_utc, "staff": row.staff_id, "reason_given": cleaned is not None})
                self._remember(tx, request_key, "cancel", reference)
                self._notify(tx, "cancelled", saved, row, outbox)
                return Outcome("cancelled", saved, previous=row)
        except Refused as refusal:
            return Outcome(refusal.status, refusal.appointment, reason=refusal.reason, detail=refusal.detail)

    def reschedule(
        self, reference: str, *, start_utc: int, staff_id: Optional[str] = None, expected_version: Optional[int] = None,
        validate: Optional[Validator] = None, request_key: Optional[str] = None, outbox: Optional[OutboxBuilder] = None,
    ) -> Outcome:
        """Move ONE appointment to a new start (and optionally another technician) in a single step: either it is moved, or the original is untouched."""
        try:
            with self._write() as tx:
                replay = self._replayed(tx, request_key, "reschedule")
                if replay is not None:
                    return replay
                row = tx.get(reference)
                if row is None:
                    raise Refused("not_found", "no_such_reference")
                if row.status != CONFIRMED:
                    raise Refused("not_reschedulable", row.status, appointment=row)
                if expected_version is not None and row.version != expected_version:
                    raise Refused("stale", "changed_since_confirmation", appointment=row)
                if row.start_utc <= tx.now_utc:
                    raise Refused("not_reschedulable", "already_started", appointment=row)
                target_staff = staff_id or row.staff_id
                if (start_utc, target_staff) == (row.start_utc, row.staff_id):
                    return Outcome("unchanged", row)
                placement = Placement(
                    target_staff, start_utc, row.duration_minutes, row.buffer_before_minutes, row.buffer_after_minutes, row.service_id, row.category,
                    ignore_reference=reference,
                )
                if validate is not None:
                    validate(tx, placement)
                self._guard(tx, placement, row.client_id)
                now = tx.now_utc
                tx._conn.execute(
                    "UPDATE appointments SET start_utc = ?, staff_id = ?, version = version + 1, updated_at = ? WHERE reference = ?",
                    (start_utc, target_staff, now, reference),
                )
                saved = tx.get(reference)
                self._event(tx, reference, "rescheduled", {
                    "from_start": row.start_utc, "to_start": saved.start_utc, "from_staff": row.staff_id, "to_staff": saved.staff_id,
                })
                self._remember(tx, request_key, "reschedule", reference)
                self._notify(tx, "rescheduled", saved, row, outbox)
                return Outcome("rescheduled", saved, previous=row)
        except Refused as refusal:
            return Outcome(refusal.status, refusal.appointment, reason=refusal.reason, detail=refusal.detail)

    # ---- the outbox: claiming and finishing (the dispatcher's side; see dispatcher.py) ------------------------------
    def outbox_claim(self, limit: int = 10) -> list[dict]:
        """Atomically take up to ``limit`` pending rows (pending -> sending, attempts + 1). A row can be claimed by exactly one caller: the claim is one write transaction."""
        with self._write() as tx:
            rows = tx._conn.execute("SELECT * FROM outbox WHERE status = 'pending' ORDER BY outbox_id LIMIT ?", (limit,)).fetchall()
            claimed = []
            for row in rows:
                tx._conn.execute("UPDATE outbox SET status = 'sending', attempts = attempts + 1, claimed_at = ? WHERE outbox_id = ? AND status = 'pending'", (tx.now_utc, row["outbox_id"]))
                claimed.append({**dict(row), "status": "sending", "attempts": row["attempts"] + 1})
            return claimed

    def outbox_finish(self, outbox_id: int, status: str, error: Optional[str] = None) -> None:
        """Record what happened to a claimed row. 'pending' puts a definitely-unsent row back for another try; 'sent', 'failed' and 'uncertain' are final."""
        if status not in ("pending", "sent", "failed", "uncertain"):
            raise ValueError("a finished outbox row is pending (to retry), sent, failed or uncertain")
        with self._write() as tx:
            tx._conn.execute(
                "UPDATE outbox SET status = ?, last_error = ?, sent_at = ? WHERE outbox_id = ? AND status = 'sending'",
                (status, (error or None) and error[:120], tx.now_utc if status == "sent" else None, outbox_id),
            )

    def outbox_expire_sending(self, older_than_seconds: int) -> int:
        """A row 'sending' for too long belongs to a dispatcher that died after claiming it: whether it went out is unknown, so it becomes 'uncertain' and is never retried."""
        with self._write() as tx:
            cursor = tx._conn.execute(
                "UPDATE outbox SET status = 'uncertain', last_error = 'the sender stopped before recording the result' WHERE status = 'sending' AND claimed_at <= ?",
                (tx.now_utc - older_than_seconds,),
            )
            return cursor.rowcount

    def settle_past(self) -> int:
        """SIMULATED-WORLD CONVENTION, run when the demo opens: a confirmed appointment whose time has passed is marked completed (the salon is assumed to have done it).
        Without it, a past appointment nobody recorded an outcome for would stay 'unknown' and, by the reviewed history rule, block every older answer. Never notifies."""
        with self._write() as tx:
            rows = tx._all("WHERE a.status = 'confirmed' AND a.start_utc + a.duration_minutes * 60 <= ?", (tx.now_utc,))
            for row in rows:
                tx._conn.execute("UPDATE appointments SET status = 'completed', version = version + 1, updated_at = ? WHERE reference = ?", (tx.now_utc, row.reference))
                self._event(tx, row.reference, "settled", {"start": row.start_utc, "as": "completed"})
            return len(rows)

    # ---- inside a transaction ---------------------------------------------------------------------------------
    @staticmethod
    def _client_for(tx: Txn, name: str, phone: str) -> ClientRecord:
        found = tx.find_client(name, phone)
        if found is not None:
            return found
        now = tx.now_utc
        cursor = tx._conn.execute("INSERT INTO clients (name, name_key, phone, created_at) VALUES (?, ?, ?, ?)", (" ".join(name.split()), name_key(name), phone, now))
        return ClientRecord(cursor.lastrowid, " ".join(name.split()), phone)

    @staticmethod
    def _guard(tx: Txn, placement: Placement, client_id: int) -> None:
        """The store's own invariants, whatever the caller's validator did: no technician and no client is ever in two overlapping appointments."""
        end = placement.start_utc + placement.duration_minutes * 60
        for other in tx.between(placement.start_utc, end, staff_id=placement.staff_id):
            if other.reference != placement.ignore_reference:
                raise Refused("unavailable", "slot_taken", appointment=None)
        for other in tx.between(placement.start_utc, end):
            if other.client_id == client_id and other.reference != placement.ignore_reference:
                raise Refused("unavailable", "client_already_booked", appointment=other)

    @staticmethod
    def _new_reference(tx: Txn, seed_key: Optional[str]) -> str:
        for attempt in range(50):
            if seed_key:
                import hashlib

                digest = hashlib.sha256(f"{seed_key}|{attempt}".encode("utf-8")).digest()
                candidate = "".join(REFERENCE_ALPHABET[b % len(REFERENCE_ALPHABET)] for b in digest[:REFERENCE_LENGTH])  # stable: the same seed, the same reference
            else:
                candidate = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_LENGTH))
            if tx._conn.execute("SELECT 1 FROM appointments WHERE reference = ?", (candidate,)).fetchone() is None:
                return candidate
        raise StoreError("could not make a unique reference")

    @staticmethod
    def _event(tx: Txn, reference: str, kind: str, detail: dict) -> None:
        tx._conn.execute("INSERT INTO events (reference, kind, detail, at) VALUES (?, ?, ?, ?)", (reference, kind, json.dumps(detail, sort_keys=True), tx.now_utc))

    @staticmethod
    def _remember(tx: Txn, request_key: Optional[str], kind: str, reference: str) -> None:
        if request_key:
            tx._conn.execute("INSERT INTO requests (request_key, kind, reference, created_at) VALUES (?, ?, ?, ?)", (request_key, kind, reference, tx.now_utc))

    @staticmethod
    def _replayed(tx: Txn, request_key: Optional[str], kind: str) -> Optional[Outcome]:
        """The earlier result of this very request, if there is one. A key reused for a different kind of request is refused, never reinterpreted."""
        if not request_key:
            return None
        row = tx._conn.execute("SELECT kind, reference FROM requests WHERE request_key = ?", (request_key,)).fetchone()
        if row is None:
            return None
        if row["kind"] != kind:
            raise Refused("request_key_reused", "that request key belongs to a different kind of request")
        return Outcome("replayed", tx.get(row["reference"]))

    @staticmethod
    def _notify(tx: Txn, kind: str, saved: StoredAppointment, previous: Optional[StoredAppointment], builder: Optional[OutboxBuilder]) -> None:
        """Write the owner notification in THIS transaction. Creating a seed never notifies (nobody booked anything), but cancelling or moving a seeded
        appointment does, like any other. The dedupe key makes a second row for the same change impossible."""
        if builder is None or (kind == "booked" and saved.origin == ORIGIN_SEED):
            return
        message = builder(saved, previous)
        if message is None:
            return
        tx._conn.execute(
            "INSERT OR IGNORE INTO outbox (dedupe_key, kind, reference, body, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
            (f"{message.kind}:{saved.reference}:{saved.version}", message.kind, saved.reference, message.body, tx.now_utc),
        )


def _clean_reason(reason: Optional[str]) -> Optional[str]:
    if not isinstance(reason, str):
        return None
    cleaned = " ".join("".join(ch for ch in reason if ch.isprintable()).split())[:MAX_REASON_LENGTH]
    return cleaned or None


__all__ = ["AppointmentStore", "StoreError", "Txn"]
