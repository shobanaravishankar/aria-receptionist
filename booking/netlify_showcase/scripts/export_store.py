"""Export the Python demo's SQLite store (clients, appointments, their events, idempotency keys, the owner's alert rows, preferred technicians) to ONE snapshot file that
scripts/import_store.mjs loads into the showcase's Postgres. Python standard library only.

    python scripts/export_store.py --db <the SQLite file> --out <snapshot.json>

It only READS. The source is opened read-only and copied into memory with SQLite's own backup API (a consistent snapshot even if the file is in WAL mode), then closed; nothing is ever
written to it. Point it at a quiet copy of the store if you can. It prints table names, row counts and digests only: never a name, a number or any other row content.

The snapshot holds whatever is in the store: the fictional clients and, if any rehearsal or demo call booked under a spoken name and number, those too. Treat the snapshot file as data
about people: do not commit it, post it or paste it anywhere (`*.snapshot.json` is git-ignored). The digests in it are what the importer re-computes from Postgres to prove that
what was imported equals what was exported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

FORMAT = 1
SCHEMA_VERSION = "1"
# source table -> (target table, columns in canonical order, order by, columns whose text is JSON)
TABLES = {
    "clients": ("clients", ["client_id", "name", "name_key", "phone", "created_at"], "client_id", []),
    "appointments": ("appointments", ["appt_id", "reference", "client_id", "service_id", "service_name", "category", "staff_id", "start_utc", "duration_minutes", "buffer_before_minutes",
                                      "buffer_after_minutes", "status", "version", "origin", "seed_key", "created_at", "updated_at", "cancelled_at", "cancel_reason"], "appt_id", []),
    "events": ("appointment_events", ["event_id", "reference", "kind", "detail", "at"], "event_id", ["detail"]),
    "requests": ("requests", ["request_key", "kind", "reference", "created_at"], "request_key", []),
    "outbox": ("outbox", ["outbox_id", "dedupe_key", "kind", "reference", "body", "status", "attempts", "last_error", "created_at", "claimed_at", "sent_at"], "outbox_id", []),
    "client_preferences": ("client_preferences", ["client_id", "family_id", "staff_id", "version", "created_at", "updated_at"], "client_id, family_id", []),
    "preference_requests": ("preference_requests", ["request_key", "kind", "client_id", "family_id", "created_at"], "request_key", []),
    "preference_events": ("preference_events", ["event_id", "client_id", "family_id", "kind", "detail", "at"], "event_id", ["detail"]),
}


def canonical(value):
    """JSON text with sorted keys and no spaces: the same text the importer builds from Postgres' jsonb."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(rows: list[list]) -> str:
    return hashlib.sha256(json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def snapshot(db_path: Path) -> dict:
    source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        memory = sqlite3.connect(":memory:")
        source.backup(memory)
    finally:
        source.close()
    memory.row_factory = sqlite3.Row
    try:
        version = memory.execute("select value from meta where key = 'schema_version'").fetchone()
        if version is None or version["value"] != SCHEMA_VERSION:
            raise SystemExit(f"this store is schema version {version['value'] if version else 'unknown'}; this exporter reads version {SCHEMA_VERSION}")
        present = {r["name"] for r in memory.execute("select name from sqlite_master where type = 'table'")}
        tables: dict[str, dict] = {}
        manifest: dict[str, dict] = {}
        for source_name, (target, columns, order, json_columns) in TABLES.items():
            if source_name not in present:  # the preference tables only exist once preferences were used
                tables[target], manifest[target] = {"columns": columns, "rows": []}, {"count": 0, "digest": digest([])}
                continue
            rows, canon_rows = [], []
            for record in memory.execute(f"select {', '.join(columns)} from {source_name} order by {order}"):
                row = [record[c] for c in columns]
                shown = list(row)
                for name in json_columns:
                    parsed = json.loads(row[columns.index(name)])
                    shown[columns.index(name)] = parsed
                    row[columns.index(name)] = canonical(parsed)
                rows.append(shown)
                canon_rows.append(row)
            tables[target] = {"columns": columns, "rows": rows}
            manifest[target] = {"count": len(rows), "digest": digest(canon_rows)}
        return {
            "format": FORMAT, "schema_version": SCHEMA_VERSION, "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "note": "Data about people (fictional clients, and any names and numbers given on rehearsal calls). Do not commit, post or paste.",
            "tables": tables, "manifest": manifest,
        }
    finally:
        memory.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, type=Path, help="the SQLite store (read-only; a quiet copy is best)")
    parser.add_argument("--out", required=True, type=Path, help="the snapshot file to write (must not exist)")
    args = parser.parse_args(argv)
    if not args.db.is_file():
        print(f"no such store: {args.db}", file=sys.stderr)
        return 2
    if args.out.exists():
        print(f"{args.out} already exists; choose a new name", file=sys.stderr)
        return 2
    data = snapshot(args.db)
    args.out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for name, entry in data["manifest"].items():
        print(f"{name:22s} rows={entry['count']:6d} digest={entry['digest'][:16]}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
