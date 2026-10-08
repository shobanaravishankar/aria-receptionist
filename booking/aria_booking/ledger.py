"""Local booking ledger: the memory that makes retries safe.

Why it exists: a browser "Save" can succeed while the script never learns it did (page hang,
timeout, crash). Before every save we write SAVING here; afterwards VERIFIED or UNCERTAIN. A later
retry consults the ledger and the calendar instead of blindly creating a second appointment.

It is a plain JSON file under the git-ignored local directory. Writes are atomic, and a lock file
(created exclusively) stops two runs from booking at the same time. A corrupt file is reported, never
silently replaced.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional


class LedgerError(Exception):
    pass


class LedgerLocked(LedgerError):
    pass


class State:
    SAVING = "saving"  # intent recorded, outcome not known yet
    VERIFIED = "verified"  # appointment re-read from the calendar and confirmed
    UNCERTAIN = "uncertain"  # save may or may not have happened; never retry blindly
    FAILED = "failed"  # known NOT saved (failed before the Save click)


@dataclass
class Entry:
    key: str
    ref: str
    state: str
    staff: str
    service: str
    start: str
    end: str
    created_at: str
    updated_at: str
    detail: str = ""
    # True once a calendar record carrying this entry's reference has EVER been seen. It is never cleared, and it is
    # separate from `state`: the state describes the latest judgement, this records that the booking really existed,
    # so a later cancellation or move can never be mistaken for "the save never happened".
    observed: bool = False


def request_key(business_id: str, staff: str, service: str, start: datetime, duration_minutes: int) -> str:
    """Stable identity of an intended booking. Same slot => same key, which is what lets a retry
    be recognised as the same request."""
    start_utc = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    raw = "|".join([business_id, staff.casefold(), service.casefold(), start_utc, str(duration_minutes)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ref_from_key(key: str) -> str:
    return "ARIA-" + key[:8].upper()


def _real_now() -> datetime:
    return datetime.now(timezone.utc)


class Ledger:
    def __init__(self, path: Path, clock: Callable[[], datetime] = _real_now):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(".lock")
        self._clock = clock

    def _now_text(self) -> str:
        return self._clock().astimezone(timezone.utc).isoformat(timespec="seconds")

    # ---- locking -------------------------------------------------------------------------
    @contextmanager
    def locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise LedgerLocked(
                f"another booking run holds {self.lock_path.name}. If no run is active, "
                "delete that lock file after confirming nothing is booking."
            ) from exc
        try:
            os.write(fd, f"pid={os.getpid()} at={self._now_text()}".encode("ascii"))
            os.close(fd)
            yield
        finally:
            try:
                os.remove(self.lock_path)
            except FileNotFoundError:
                pass

    # ---- storage -------------------------------------------------------------------------
    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LedgerError(f"{self.path} is corrupt ({exc}); not overwriting it. Inspect or move it.") from exc
        if not isinstance(data, dict):
            raise LedgerError(f"{self.path} has an unexpected shape; not overwriting it.")
        return data

    def _save(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic on the same volume

    def get(self, key: str) -> Optional[Entry]:
        raw = self._load().get(key)
        return Entry(**raw) if raw else None

    def put(self, entry: Entry) -> None:
        data = self._load()
        data[entry.key] = asdict(entry)
        self._save(data)

    def create(self, key: str, ref: str, staff: str, service: str, start: str, end: str, state: str, detail: str = "") -> Entry:
        stamp = self._now_text()
        entry = Entry(key, ref, state, staff, service, start, end, stamp, stamp, detail)
        self.put(entry)
        return entry

    def set_state(self, key: str, state: str, detail: str = "") -> Entry:
        entry = self.get(key)
        if entry is None:
            raise LedgerError(f"no ledger entry for key {key[:8]}")
        entry.state = state
        entry.detail = detail
        entry.updated_at = self._now_text()
        self.put(entry)
        return entry

    def mark_observed(self, key: str) -> Entry:
        entry = self.get(key)
        if entry is None:
            raise LedgerError(f"no ledger entry for key {key[:8]}")
        if not entry.observed:
            entry.observed = True
            self.put(entry)
        return entry

    def all_entries(self) -> list[Entry]:
        return [Entry(**raw) for raw in self._load().values()]
