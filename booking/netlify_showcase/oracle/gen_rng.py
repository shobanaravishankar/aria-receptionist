"""Golden draws from Python's random.Random for the seeds the demo schedule uses (and a few edge seeds). The TypeScript PyRandom must reproduce every draw exactly.

Run from the booking folder:  python netlify_showcase/oracle/gen_rng.py
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aria_booking.demo.dataset import SEED  # noqa: E402
from aria_booking.showcase.roster import ROSTER_ROWS  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "test" / "vectors"
OPS = ["r", "c3", "c4", "c2", "r", "c10", "c1", "c7", "c2", "c5", "c35", "r", "c6", "c8", "c90"]


def draws(rng: random.Random) -> list:
    out = []
    for i in range(75):
        op = OPS[i % len(OPS)]
        out.append(rng.random() if op == "r" else rng.choice(range(int(op[1:]))))
    return out


def digest_seed(*parts: object) -> int:
    return int.from_bytes(hashlib.sha256("|".join(str(p) for p in (SEED, *parts)).encode()).digest()[:8], "big")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = []
    for seed in (0, 1, 2, 12345, 2**31, 2**32 - 1, 2**32, 2**32 + 1, 2**53 + 7, 2**63, 2**64 - 1):
        cases.append({"seed": str(seed), "draws": draws(random.Random(seed))})
    start = date(2026, 10, 1)
    for offset in range(0, 400, 3):
        day = start + timedelta(days=offset)
        for row in ROSTER_ROWS[:4]:
            seed = digest_seed(day.isoformat(), row.staff_id)
            cases.append({"seed": str(seed), "parts": [day.isoformat(), row.staff_id], "draws": draws(random.Random(seed))})
    for row in ROSTER_ROWS:
        for tag in ("scripted",):
            day = start.isoformat()
            seed = digest_seed(day, row.staff_id, tag)
            cases.append({"seed": str(seed), "parts": [day, row.staff_id, tag], "draws": draws(random.Random(seed))})
    (OUT / "rng.json").write_text(json.dumps({"seedText": SEED, "ops": OPS, "cases": cases}), encoding="utf-8")
    print("wrote rng.json:", len(cases), "seeds")


if __name__ == "__main__":
    main()
