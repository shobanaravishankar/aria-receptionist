"""Make the import test's snapshot: run the PYTHON tools through a recorded walk of mixed calls on a real SQLite store (the fictional seed fixtures plus bookings, cancellations, moves,
preferences, owner-alert rows and idempotency keys), then export that store with scripts/export_store.py. The snapshot is synthetic (the oracle's invented callers) and safe to commit; a real
export of the demo's store is data about people and must never be.

Run from the booking folder:  python netlify_showcase/oracle/gen_import_fixture.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))

from export_store import snapshot  # noqa: E402
from gen_scenarios import Recorder, OUT  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        Recorder(7919 * 11, 110).run(Path(tmp))
        data = snapshot(Path(tmp) / "s.sqlite3")
    data["exported_at"] = "2026-10-22T00:00:00Z"  # fixed, so the file only changes when the data does
    (OUT / "store_snapshot.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for name, entry in data["manifest"].items():
        print(f"{name:22s} rows={entry['count']:5d} digest={entry['digest'][:16]}")


if __name__ == "__main__":
    main()
