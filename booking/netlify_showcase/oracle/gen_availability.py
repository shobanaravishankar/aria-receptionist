"""Golden vectors for the time helpers and the availability rules, produced by the PYTHON implementation (the oracle). The TypeScript port must reproduce every value exactly.

Run from the booking folder:  python netlify_showcase/oracle/gen_availability.py
Nothing here touches a store, a network or a secret: seeded random scenarios over fictional staff.
"""

from __future__ import annotations

import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aria_booking.availability import _ceil_to_grid, find_slots, validate_slot  # noqa: E402
from aria_booking.models import Appointment, DaySnapshot, Interval, ServiceSpec, StaffDay, add_minutes, fmt  # noqa: E402

TZ = ZoneInfo("America/New_York")
OUT = Path(__file__).resolve().parents[1] / "test" / "vectors"


def ms(moment: datetime) -> int:
    return int(round(moment.timestamp() * 1000))


def local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def time_vectors() -> dict:
    """Wall-clock to instant (including the skipped and the repeated hour), weekday, and the formatted strings."""
    cases = []
    days = [date(2026, 3, 7), date(2026, 3, 8), date(2026, 3, 9), date(2026, 10, 22), date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2), date(2026, 12, 31), date(2027, 1, 1)]
    for day in days:
        for hour in (0, 1, 2, 3, 4, 9, 10, 12, 18, 20, 23):
            for minute in (0, 15, 30, 45, 59):
                moment = local(day, hour, minute)
                # a wall time inside the skipped spring hour keeps its un-normalised fields in Python (02:30 prints as 02:30 although the instant is 03:30 local); the instant is the thing compared
                exists = moment.astimezone(ZoneInfo("UTC")).astimezone(TZ).replace(tzinfo=None) == moment.replace(tzinfo=None)
                cases.append({"y": day.year, "m": day.month, "d": day.day, "h": hour, "mi": minute, "epoch": ms(moment), "exists": exists, "weekday": day.weekday(), "fmt": fmt(moment),
                              "hhmm": moment.strftime("%H:%M"), "minuteOfDay": moment.hour * 60 + moment.minute})
    # instants every 7 minutes across the two DST changes, read back as local fields
    reads = []
    for start in (local(date(2026, 3, 8), 0), local(date(2026, 11, 1), 0)):
        for step in range(0, 6 * 60, 7):
            moment = add_minutes(start, step)
            reads.append({"epoch": ms(moment), "fmt": fmt(moment), "hhmm": moment.strftime("%H:%M"), "weekday": moment.weekday(), "minuteOfDay": moment.hour * 60 + moment.minute,
                          "offset": int(moment.utcoffset().total_seconds() // 60)})
    ceil = []
    rng = random.Random(7)
    for _ in range(400):
        base = local(rng.choice(days), rng.randint(0, 23), rng.randint(0, 59))
        moment = add_minutes(base, rng.choice([0, 0, 0, 1]))  # whole minutes
        extra = timedelta(seconds=rng.choice([0, 0, 1, 30, 59]), microseconds=rng.choice([0, 0, 1, 500000]))
        moment = (moment.astimezone(ZoneInfo("UTC")) + extra).astimezone(TZ)
        grid = rng.choice([5, 15, 30, 60])
        ceil.append({"epoch": ms(moment) if not moment.microsecond else moment.timestamp() * 1000, "grid": grid, "expected": ms(_ceil_to_grid(moment, grid))})
    # real elapsed time over spans that cross a DST change, forwards and backwards
    adds = []
    for start in (local(date(2026, 3, 7), 12), local(date(2026, 3, 8), 0), local(date(2026, 10, 31), 12), local(date(2026, 11, 1), 0)):
        for minutes in (-3000, -1440, -90, -1, 0, 1, 90, 1440, 1441, 3000, 6000):
            moved = add_minutes(start, minutes)
            adds.append({"start": ms(start), "minutes": minutes, "epoch": ms(moved), "fmt": fmt(moved)})
    return {"fromLocal": cases, "reads": reads, "ceil": ceil, "adds": adds}


def interval(start: datetime, end: datetime) -> Interval:
    return Interval(start, end)


def scenario(rng: random.Random, day: date) -> dict:
    staff_names = rng.sample(["Marisol", "Tamsin", "Odette", "Priya", "Noor"], rng.randint(1, 3))
    staff_days = []
    for name in staff_names:
        def maybe_unknown(chance: float) -> bool:
            return rng.random() < chance

        if maybe_unknown(0.06):
            working = None
        else:
            working = []
            cursor = rng.choice([8, 9, 10, 11])
            for _ in range(rng.choice([0, 1, 1, 1, 2])):
                start_h = cursor
                end_h = min(23, start_h + rng.randint(2, 8))
                working.append(interval(local(day, start_h, rng.choice([0, 0, 15, 30])), local(day, end_h, rng.choice([0, 0, 30]))))
                cursor = end_h + 1
                if cursor > 20:
                    break
        time_off = None if maybe_unknown(0.05) else [
            interval(local(day, h, 0), local(day, h, 0) + timedelta(minutes=rng.choice([30, 60, 90]))) for h in rng.sample(range(8, 20), rng.choice([0, 0, 1, 2]))
        ]
        # (a wall-clock timedelta on an aware datetime is wall-clock arithmetic in Python; the time-off ends here are read back through add_minutes so they are real elapsed time)
        time_off = None if time_off is None else [interval(t.start, add_minutes(t.start, int((t.end - t.start).total_seconds() // 60))) for t in time_off]
        if maybe_unknown(0.05):
            appointments = None
        else:
            appointments = []
            for h in rng.sample(range(8, 21), rng.choice([0, 1, 2, 3, 4])):
                start = local(day, h, rng.choice([0, 15, 30, 45]))
                appointments.append(Appointment(name, interval(start, add_minutes(start, rng.choice([30, 45, 60, 75]))), "svc", "", rng.random() > 0.15))
        staff_days.append(StaffDay(name, None if working is None else tuple(working), None if time_off is None else tuple(time_off),
                                   None if appointments is None else tuple(appointments), f"9{len(staff_days)}"))
    now = add_minutes(local(day, 9), rng.choice([-24 * 60, -300, -60, 0, 30, 120, 480, 900]))
    service = ServiceSpec("svc", rng.choice([15, 30, 40, 45, 60, 75, 90]), rng.choice([0, 0, 5]), rng.choice([0, 5, 10, 15]))
    snapshot = DaySnapshot(day, tuple(staff_days), now)
    min_lead = rng.choice([60, 60, 0, 30, 120])
    checks, searches = [], []
    for sd in staff_days:
        for _ in range(6):
            start = add_minutes(local(day, rng.randint(7, 21), rng.choice([0, 15, 30, 45, 5, 10, 20])), rng.choice([0, 0, 0, 3]))
            result = validate_slot(snapshot, service, sd.staff, start, now=now, min_lead_minutes=min_lead)
            checks.append({"staff": sd.staff, "start": ms(start), "ok": result.ok, "reasons": list(result.reasons), "unknown": list(result.unknown)})
        for _ in range(3):
            earliest = None if rng.random() < 0.5 else add_minutes(local(day, rng.randint(8, 18), rng.choice([0, 30])), rng.choice([0, 7]))
            latest_end = None if rng.random() < 0.6 else add_minutes(local(day, rng.randint(12, 21), rng.choice([0, 30])), 0)
            grid = rng.choice([15, 15, 30, 5])
            found = find_slots(snapshot, service, sd.staff, now=now, earliest=earliest, latest_end=latest_end, grid_minutes=grid, min_lead_minutes=min_lead)
            searches.append({"staff": sd.staff, "earliest": None if earliest is None else ms(earliest), "latestEnd": None if latest_end is None else ms(latest_end), "grid": grid,
                             "slots": [[s.staff, ms(s.service.start), ms(s.service.end), ms(s.blocked.start), ms(s.blocked.end)] for s in found.slots],
                             "unknown": list(found.unknown), "notes": list(found.notes)})
    # the minimum-notice boundary itself: a start exactly at, one millisecond before and one after now + lead
    for sd in staff_days[:1]:
        for nudge in (-1, 0, 1):
            start = add_minutes(now, min_lead) + timedelta(milliseconds=nudge)
            result = validate_slot(snapshot, service, sd.staff, start, now=now, min_lead_minutes=min_lead)
            checks.append({"staff": sd.staff, "start": ms(start), "ok": result.ok, "reasons": list(result.reasons), "unknown": list(result.unknown)})
    missing = validate_slot(snapshot, service, "Nobody O'Neil", add_minutes(now, 600), now=now)
    checks.append({"staff": "Nobody O'Neil", "start": ms(add_minutes(now, 600)), "ok": missing.ok, "reasons": list(missing.reasons), "unknown": list(missing.unknown)})
    return {
        "day": [day.year, day.month, day.day], "now": ms(now), "minLead": min_lead,
        "service": {"name": service.name, "duration": service.duration_minutes, "before": service.buffer_before_minutes, "after": service.buffer_after_minutes},
        "staffDays": [{
            "staff": sd.staff, "staffId": sd.staff_id,
            "working": None if sd.working is None else [[ms(i.start), ms(i.end)] for i in sd.working],
            "timeOff": None if sd.time_off is None else [[ms(i.start), ms(i.end)] for i in sd.time_off],
            "appointments": None if sd.appointments is None else [{"staff": a.staff, "start": ms(a.interval.start), "end": ms(a.interval.end), "blocksTime": a.blocks_time} for a in sd.appointments],
        } for sd in staff_days],
        "checks": checks, "searches": searches,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261022)
    days = [date(2026, 3, 7), date(2026, 3, 8), date(2026, 3, 9), date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2), date(2026, 12, 24)]
    scenarios = [scenario(rng, rng.choice(days)) for _ in range(260)]
    (OUT / "time.json").write_text(json.dumps(time_vectors()), encoding="utf-8")
    (OUT / "availability.json").write_text(json.dumps({"scenarios": scenarios}), encoding="utf-8")
    print("wrote time.json and availability.json:", sum(len(s["checks"]) for s in scenarios), "checks,", sum(len(s["searches"]) for s in scenarios), "searches")


if __name__ == "__main__":
    main()
