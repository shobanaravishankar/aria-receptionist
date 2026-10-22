"""Golden HTML for the staff calendar page, rendered by the PYTHON page module (the oracle), over many days, stored appointments and the diagnostic.

Run from the booking folder:  python netlify_showcase/oracle/gen_page.py
"""

from __future__ import annotations

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aria_booking.demo.dataset import DemoDataset  # noqa: E402
from aria_booking.demo.page import render_page  # noqa: E402
from gen_judge import Overlay, row_json, rows_for  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "test" / "vectors"


def main() -> None:
    rng = random.Random(5150)
    todays = [date(2026, 3, 5), date(2026, 10, 21), date(2026, 11, 1), date(2026, 12, 20)]
    samples = []
    for _ in range(36):
        today = rng.choice(todays)
        diagnostic = rng.choice([(), (), ("910003",)])
        dataset = DemoDataset(today, diagnostic_unknown_hours=diagnostic, overlay=Overlay())
        day = today + timedelta(days=rng.choice([-9, -2, 0, 0, 1, 1, 2, 2, 3, 7, 20, 35, 36, 50]))
        rows = rows_for(rng, dataset, day, rng.randint(0, 6)) if rng.random() < 0.7 else []
        dataset.overlay = Overlay([r for r in rows if r.status == "confirmed"])
        samples.append({"today": [today.year, today.month, today.day], "diagnostic": sorted(diagnostic), "day": [day.year, day.month, day.day], "rows": [row_json(r) for r in rows],
                        "html": render_page(dataset, day)})
    (OUT / "page.json").write_text(json.dumps({"samples": samples}), encoding="utf-8")
    print("wrote page.json:", len(samples), "pages,", sum(len(s["html"]) for s in samples) // 1024, "KB")


if __name__ == "__main__":
    main()
