"""Export the Python backend's DATA (the showcase menu, the roster, the demo dataset's constants and scripted days) as JSON for the TypeScript port.

The data is never retyped: this script reads it from the Python modules, and a Python test (tests/test_netlify_showcase_data.py) fails if the committed JSON no longer
equals what the Python modules say, so the two implementations cannot drift apart silently.

Run from the booking folder:  python netlify_showcase/oracle/export_data.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aria_booking.demo import dataset as ds  # noqa: E402
from aria_booking.showcase import roster as ro  # noqa: E402
from aria_booking.showcase.catalog import SHOWCASE_CATALOG  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "src" / "data"


def _items() -> list:
    return [
        {
            "itemId": i.item_id, "family": i.family, "category": i.category, "name": i.name, "description": i.description, "durationMinutes": i.duration_minutes,
            "priceUsd": i.price_usd, "sourceUrl": i.source_url, "retrievedOn": i.retrieved_on, "originalPriceUsd": i.original_price_usd, "notes": i.notes,
        }
        for i in SHOWCASE_CATALOG.items
    ]


def _roster() -> dict:
    return {
        "rows": [
            {"staffId": r.staff_id, "name": r.name, "weekdays": list(r.weekdays), "hours": list(r.hours), "categories": list(r.categories), "hoursKnown": r.hours_known}
            for r in ro.ROSTER_ROWS
        ],
        "roleByCategory": dict(ro.ROLE_BY_CATEGORY),
        "nearMissName": ro.NEAR_MISS_NAME,
    }


def _script_entry(entry) -> object:
    if entry == "off":
        return "off"
    hours, breaks, visits = entry
    return {"hours": list(hours), "breaks": [list(b) for b in breaks], "visits": [list(v) for v in visits]}


def _dataset() -> dict:
    return {
        "seed": ds.SEED, "gridStart": ds.GRID_START, "gridEnd": ds.GRID_END, "coverageBeforeDays": ds.COVERAGE_BEFORE_DAYS, "coverageAfterDays": ds.COVERAGE_AFTER_DAYS,
        "demoFamilies": [list(f) for f in ds.DEMO_FAMILIES], "firstNames": list(ds.FIRST_NAMES), "initials": ds.INITIALS,
        "scriptFirst": {k: _script_entry(v) for k, v in ds._SCRIPT_FIRST.items()},
        "scriptSecond": {k: _script_entry(v) for k, v in ds._SCRIPT_SECOND.items()},
        "diagnosticUnknownFirst": _script_entry(ds._DIAGNOSTIC_UNKNOWN_FIRST),
    }


def build() -> dict:
    """File name -> the JSON-able content it must hold."""
    return {"showcase_items.json": _items(), "roster.json": _roster(), "dataset.json": _dataset()}


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    for name, content in build().items():
        (DATA / name).write_text(json.dumps(content, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote", name)


if __name__ == "__main__":
    main()
