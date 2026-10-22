"""The TypeScript port's DATA (menu, roster, dataset tables) is exported from this Python backend, never retyped. These tests fail if the committed JSON no longer equals what the
Python modules say, so the two implementations cannot drift apart silently. They prove nothing about the TypeScript logic: that is the job of the Node tests against the golden vectors."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

BOOKING = Path(__file__).resolve().parent.parent
SHOWCASE = BOOKING / "netlify_showcase"


def _exporter():
    spec = importlib.util.spec_from_file_location("netlify_showcase_export_data", SHOWCASE / "oracle" / "export_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_data_files_equal_what_the_python_modules_export():
    exporter = _exporter()
    built = exporter.build()
    assert set(built) == {"showcase_items.json", "roster.json", "dataset.json", "page.json"}
    for name, content in built.items():
        committed = json.loads((SHOWCASE / "src" / "data" / name).read_text(encoding="utf-8"))
        assert committed == content, f"{name} is out of date: run  python netlify_showcase/oracle/export_data.py  from the booking folder"


def test_the_exported_data_holds_no_personal_or_secret_looking_text():
    from aria_booking.showcase.leaks import find_leaks

    for name in ("showcase_items.json", "roster.json", "dataset.json", "page.json"):
        text = (SHOWCASE / "src" / "data" / name).read_text(encoding="utf-8")
        assert not find_leaks(text), (name, find_leaks(text))
