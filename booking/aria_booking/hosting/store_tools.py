"""Looking after the appointment file: a safe copy of it, and a check of it. For moving the demo's invented appointments onto the host's volume, taking a copy before a change, and
answering 'is this file all right?' without opening it the way the server does.

Both only READ the file they are given (it is opened read-only, so nothing in it can change, and a running server is not disturbed). The copy is made with SQLite's own online backup, which is
consistent even while the server is writing. Neither prints a name or a phone number: counts and versions only.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable


class StoreToolError(Exception):
    """The file could not be read or copied. The message says what to check."""


def _read_only(path: Path) -> sqlite3.Connection:
    if not Path(path).is_file():
        raise StoreToolError(f"there is no file at {path}. Check the path (the server's file is ARIA_STORE_PATH).")
    try:
        conn = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True, timeout=20)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return conn
    except sqlite3.Error as exc:
        raise StoreToolError(f"{path} cannot be opened for reading ({type(exc).__name__}). Is it an appointment file, and is it complete?") from None


def summarize(path: Path) -> dict:
    """What is in the file, as counts: nothing personal."""
    conn = _read_only(path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"appointments", "clients", "meta"} <= tables:
            raise StoreToolError(f"{path} is a SQLite file but not an appointment file (its tables are {sorted(tables)}).")
        schema = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        by_status = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM appointments GROUP BY status")}
        outbox = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM outbox GROUP BY status")} if "outbox" in tables else {}
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        return {"schema_version": schema[0] if schema else None, "appointments": by_status, "clients": conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0],
                "alerts_waiting": outbox, "integrity": integrity, "bytes": Path(path).stat().st_size}
    except sqlite3.Error as exc:
        raise StoreToolError(f"{path} could not be read ({type(exc).__name__}).") from None
    finally:
        conn.close()


def backup(source: Path, destination: Path, *, out: Callable[[str], None] = print) -> dict:
    """A consistent copy of ``source`` at ``destination`` (which must not exist: nothing is ever overwritten). Returns the summary of the copy, checked after it was made."""
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise StoreToolError(f"{destination} already exists. Choose a new name: a copy never overwrites a file.")
    if source.resolve() == destination.resolve():
        raise StoreToolError("the copy and the original are the same file.")
    summarize(source)  # refuse early, with a plain message, if the source is not an appointment file
    destination.parent.mkdir(parents=True, exist_ok=True)
    reader = _read_only(source)
    try:
        writer = sqlite3.connect(destination)
        try:
            reader.backup(writer)
        finally:
            writer.close()
    except sqlite3.Error as exc:
        destination.unlink(missing_ok=True)
        raise StoreToolError(f"the copy could not be made ({type(exc).__name__}); nothing was written.") from None
    finally:
        reader.close()
    copied = summarize(destination)
    if copied["integrity"] != "ok":
        destination.unlink(missing_ok=True)
        raise StoreToolError("the copy did not pass SQLite's integrity check and was removed; the original was not touched.")
    out(f"copied to {destination}: {sum(copied['appointments'].values())} appointments, {copied['clients']} clients, integrity {copied['integrity']}")
    return copied
