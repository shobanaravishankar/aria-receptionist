"""Golden vectors for the placement judge and the open-times engine, produced by the PYTHON implementation (the oracle).

Run from the booking folder:  python netlify_showcase/oracle/gen_judge.py
"""

from __future__ import annotations

import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aria_booking.appointments.judge import PlacementJudge  # noqa: E402
from aria_booking.appointments.models import Placement, Refused, StoredAppointment  # noqa: E402
from aria_booking.demo.dataset import DemoDataset, NotCovered  # noqa: E402
from aria_booking.demo.open_times import open_times  # noqa: E402
from gen_dataset import local, ms, snapshot_json  # noqa: E402

TZ = ZoneInfo("America/New_York")
OUT = Path(__file__).resolve().parents[1] / "test" / "vectors"
NAMES = ["Willa Hartwell", "Ansel Hartwell", "Cedric Lowe", "Mary Jo Smith-Jones", "Prince", "zoe"]


def row_json(r: StoredAppointment) -> list:
    return [r.appt_id, r.reference, r.client_id, r.client_name, r.service_id, r.service_name, r.category, r.staff_id, r.start_utc, r.duration_minutes, r.buffer_before_minutes,
            r.buffer_after_minutes, r.status, r.version, r.origin, r.created_at, r.updated_at, r.cancelled_at, r.cancel_reason, r.seed_key]


def rows_for(rng: random.Random, dataset, around: date, count: int) -> list:
    services = list(dataset.registry._services.values())
    rows = []
    for i in range(count):
        service = rng.choice(services)
        day = around + timedelta(days=rng.choice([-1, 0, 0, 0, 1]))
        start = local(day, rng.choice([540, 600, 615, 660, 720, 780, 855, 900, 960, 1005, 1050, 1110]))
        staff_id = rng.choice(sorted(service.eligible_staff_ids) + ["910001", "999999"])
        rows.append(StoredAppointment(
            i + 1, f"REF{i:03d}", i + 1, rng.choice(NAMES), service.service_id, service.booksy_name, dataset.category_of(service.service_id), staff_id, int(start.timestamp()),
            service.duration_minutes, service.buffer_before_minutes, service.buffer_after_minutes, rng.choice(["confirmed"] * 6 + ["cancelled", "completed"]), 1, "voice", 1, 1, None, None, None,
        ))
    return rows


class Overlay:
    """What a store would provide, without a store: the given rows (or fixed ones) drawn onto the plan by the real merge."""

    def __init__(self, rows=()):
        self.rows = list(rows)

    def apply(self, plan, roster, rows=None):
        from aria_booking.appointments.overlay import merge_rows

        return merge_rows(plan, self.rows if rows is None else rows, TZ, roster)

    def stamp(self):
        return "fixed"


def refusal_json(r: Refused) -> dict:
    detail = {k: v for k, v in r.detail.items() if k != "snapshot"}
    out = {"status": r.status, "reason": r.reason, "detail": detail}
    if "snapshot" in r.detail:
        out["snapshot"] = snapshot_json(r.detail["snapshot"])
    return out


def judge_vectors() -> list:
    rng = random.Random(77)
    out = []
    todays = [date(2026, 3, 5), date(2026, 10, 21), date(2026, 10, 30), date(2026, 12, 20)]
    for _ in range(900):
        today = rng.choice(todays)
        dataset = DemoDataset(today, diagnostic_unknown_hours=rng.choice([(), (), (), ("910003",)]), overlay=Overlay())
        now = local(today, rng.choice([480, 600, 900])) + timedelta(minutes=rng.choice([0, 7, 30]))
        min_lead, grid, horizon = rng.choice([(60, 15, 60), (60, 15, 60), (0, 15, 60), (120, 30, 10), (60, 5, 60)])
        judge = PlacementJudge(dataset, tz=TZ, clock=lambda: now, min_lead_minutes=min_lead, grid_minutes=grid, horizon_days=horizon)
        service = rng.choice(list(dataset.registry._services.values()))
        offset = rng.choice([-9, -2, 0, 1, 1, 2, 2, 3, 5, 12, 34, 36, 40, 70])
        day = today + timedelta(days=offset)
        rows = rows_for(rng, dataset, day, rng.randint(0, 6))
        staff_id = rng.choice(sorted(service.eligible_staff_ids) * 3 + [s.staff_id for s in dataset.roster] + ["999999"])
        start = local(day, rng.choice([480, 570, 600, 615, 630, 660, 705, 720, 780, 855, 900, 945, 960, 1005, 1050, 1110, 1140, 1170, 1230]))
        seconds = rng.choice([0, 0, 0, 0, 0, 30])
        start = start + timedelta(seconds=seconds)
        minute_shift = rng.choice([0, 0, 0, 0, 5, 10])
        start = start + timedelta(minutes=minute_shift)
        duration, before, after = service.duration_minutes, service.buffer_before_minutes, service.buffer_after_minutes
        if rng.random() < 0.06:
            duration -= 15  # a caller trying to shorten the service
        ignore = rng.choice([None, None, None, rng.choice(rows).reference if rows else None])
        placement = Placement(staff_id, int(start.timestamp()), duration, before, after, service.service_id if rng.random() > 0.03 else "no-such-service", dataset.category_of(service.service_id), ignore)

        def rows_between(low, high, statuses=("confirmed",)):
            return [r for r in rows if r.status in statuses and r.start_utc < high and r.start_utc + r.duration_minutes * 60 > low]

        entry = {"today": [today.year, today.month, today.day], "diagnostic": sorted(dataset.diagnostic_unknown_hours), "now": ms(now), "minLead": min_lead, "grid": grid, "horizon": horizon,
                 "rows": [row_json(r) for r in rows],
                 "placement": [placement.staff_id, placement.start_utc, placement.duration_minutes, placement.buffer_before_minutes, placement.buffer_after_minutes, placement.service_id, placement.category, placement.ignore_reference]}
        try:
            j = judge.require(placement, rows_between)
            entry["result"] = {"ok": True, "staffId": j.staff_id, "staffName": j.staff_name, "start": ms(j.start), "snapshot": snapshot_json(j.snapshot)}
        except Refused as r:
            entry["result"] = {"ok": False, **refusal_json(r)}
        out.append(entry)
    return out


def open_times_vectors() -> list:
    rng = random.Random(88)
    out = []
    todays = [date(2026, 3, 5), date(2026, 10, 21), date(2026, 11, 1), date(2026, 12, 20)]
    for _ in range(500):
        today = rng.choice(todays)
        dataset = DemoDataset(today, diagnostic_unknown_hours=rng.choice([(), (), (), ("910001", "910006")]))
        now = local(today, rng.choice([480, 600, 900])) + timedelta(minutes=rng.choice([0, 7, 30]))
        service = rng.choice(list(dataset.registry._services.values()))
        day = today + timedelta(days=rng.choice([-8, -3, 0, 1, 1, 2, 2, 3, 6, 20, 35, 36, 50]))
        rows = rows_for(rng, dataset, day, rng.randint(0, 5))
        staff = rng.choice([None, None, None, "Tamsin", "tamsin ", "Marisol", "Noor", "Nobody", "Sienna", "Imogen"])
        serviceId = service.service_id if rng.random() > 0.04 else "no-such-service"
        grid, lead = rng.choice([(15, 60), (15, 60), (30, 0), (5, 120)])
        dataset.overlay = Overlay([r for r in rows if r.status == "confirmed"])
        result = open_times(dataset, serviceId, day, tz=TZ, now=now, staff=staff, grid_minutes=grid, min_lead_minutes=lead)
        out.append({"today": [today.year, today.month, today.day], "diagnostic": sorted(dataset.diagnostic_unknown_hours), "serviceId": serviceId, "day": [day.year, day.month, day.day],
                    "now": ms(now), "rows": [row_json(r) for r in rows], "staff": staff, "grid": grid, "lead": lead, "result": result})
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    judge = judge_vectors()
    opens = open_times_vectors()
    (OUT / "judge.json").write_text(json.dumps(judge), encoding="utf-8")
    (OUT / "opentimes.json").write_text(json.dumps(opens), encoding="utf-8")
    kinds: dict = {}
    for e in judge:
        key = "ok" if e["result"]["ok"] else f"{e['result']['status']}/{e['result']['reason']}"
        kinds[key] = kinds.get(key, 0) + 1
    statuses: dict = {}
    for e in opens:
        statuses[e["result"]["status"]] = statuses.get(e["result"]["status"], 0) + 1
    print("judge", len(judge), kinds)
    print("opentimes", len(opens), statuses)


if __name__ == "__main__":
    main()
