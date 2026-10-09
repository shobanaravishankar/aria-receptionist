"""Guard against real account identifiers reaching this PUBLIC repository.

The identifiers themselves must never be written in the repository, so they come from the environment of whoever runs the check:

    set ARIA_FORBIDDEN_IDS=<comma-separated ids, e.g. business ids, staff ids, booking references>
    python -m pytest tests/test_no_real_identifiers.py

It scans every text file under the repository's booking/ folder except the git-ignored local folder, and the commits not yet pushed.
Without the variable the repository checks are skipped (and say so), so run them before every push.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BOOKING = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {".py", ".md", ".json", ".txt", ".toml", ".cfg", ".ini", ".js", ".html", ".css", ".yml", ".yaml"}
SKIP_PARTS = {".local", "__pycache__", ".pytest_cache", ".venv"}


def forbidden() -> list[str]:
    return [item.strip() for item in os.environ.get("ARIA_FORBIDDEN_IDS", "").split(",") if item.strip()]


def scan(root: Path, ids: list[str]) -> list[tuple[str, int]]:
    """(relative path, length of the identifier found) for every text file containing any identifier. The id itself is never reported."""
    hits = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES or SKIP_PARTS & set(path.relative_to(root).parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits += [(str(path.relative_to(root)), len(ident)) for ident in ids if ident in text]
    return hits


@pytest.mark.skipif(not forbidden(), reason="ARIA_FORBIDDEN_IDS is not set: run this before every push")
def test_no_forbidden_identifier_appears_in_any_file():
    assert not scan(BOOKING, forbidden()), "forbidden identifiers found (see the file list above)"


@pytest.mark.skipif(not forbidden(), reason="ARIA_FORBIDDEN_IDS is not set: run this before every push")
def test_no_forbidden_identifier_appears_in_the_unpushed_history():
    ids = forbidden()
    log = None
    for base in ("@{upstream}", "origin/feature/retell-booking-functions", "origin/main"):  # the first base that exists
        try:
            attempt = subprocess.run(["git", "log", "-p", "--format=%H", f"{base}..HEAD"], cwd=BOOKING, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            pytest.skip("git history not available")
        if attempt.returncode == 0:
            log = attempt
            break
    if log is None:
        pytest.skip("no pushed base to compare this branch with")
    assert not [ident for ident in ids if ident in log.stdout], "a forbidden identifier is in a commit that has not been pushed yet"


def test_the_scanner_finds_an_identifier_in_a_file_and_ignores_the_local_folder(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text("business = '4242424242'\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("nothing sensitive here\n", encoding="utf-8")
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local" / "ledger.json").write_text('{"id": "4242424242"}', encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"4242424242")
    assert scan(tmp_path, ["4242424242"]) == [("tests/t.py", 10)] or scan(tmp_path, ["4242424242"]) == [(str(Path("tests") / "t.py"), 10)]
    assert scan(tmp_path, ["9999999999"]) == []
    assert scan(tmp_path, []) == []


def test_the_scanner_reports_the_length_not_the_identifier(tmp_path):
    (tmp_path / "a.txt").write_text("secret-1234567", encoding="utf-8")
    ((_name, length),) = scan(tmp_path, ["1234567"])
    assert length == 7
