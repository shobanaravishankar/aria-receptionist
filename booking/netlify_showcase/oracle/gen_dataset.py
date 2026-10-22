"""Golden vectors for the speech helpers, phone normalisation, the generated demo calendar (many dates, the scripted days, the diagnostic), the store overlay and the availability
snapshot, all produced by the PYTHON implementation (the oracle).

Run from the booking folder:  python netlify_showcase/oracle/gen_dataset.py
"""

from __future__ import annotations

import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aria_booking.appointments.models import StoredAppointment  # noqa: E402
from aria_booking.appointments.overlay import day_bounds, display_name, merge_rows  # noqa: E402
from aria_booking.demo.dataset import DemoDataset, NotCovered  # noqa: E402
from aria_booking.demo.driver import snapshot_from_plan  # noqa: E402
from aria_booking.history.phone import normalize_phone  # noqa: E402
from aria_booking.voice.speech import join_choices, join_names, spoken_day, spoken_duration, spoken_price, spoken_slot, spoken_time  # noqa: E402

TZ = ZoneInfo("America/New_York")
OUT = Path(__file__).resolve().parents[1] / "test" / "vectors"


def ms(moment: datetime) -> int:
    return int(round(moment.timestamp() * 1000))


def local(day: date, minutes: int) -> datetime:
    return datetime(day.year, day.month, day.day, minutes // 60, minutes % 60, tzinfo=TZ)


def speech_vectors() -> dict:
    rng = random.Random(11)
    moments = []
    for _ in range(500):
        base = datetime(2026, 1, 1, tzinfo=TZ) + timedelta(days=rng.randint(0, 700), minutes=rng.randint(0, 24 * 60 - 1))
        moments.append({"epoch": ms(base), "time": spoken_time(base), "day": spoken_day(base), "slot": spoken_slot(base)})
    labels = [["a"], ["a", "b"], ["a", "b", "c"], ["Tue 3 PM with Noor", "Wed 4 PM with Sienna", "Thu 10 AM with Marisol", "Fri 2 PM with Rowan"]]
    return {
        "moments": moments,
        "durations": [{"minutes": m, "text": spoken_duration(m)} for m in list(range(0, 400, 5)) + [1, 59, 61, 119, 121, 1440]],
        "joins": [{"labels": l, "choices": join_choices(l), "names": join_names(l)} for l in labels],
        "prices": [{"price": p, "text": spoken_price(p)} for p in (None, 0, 49, 149, 1000)],
    }


def phone_vectors() -> list:
    rng = random.Random(99)
    cases = ["", " ", "212-555-0142", "(212) 555-0142", "212.555.0142", "2125550142", "12125550142", "+12125550142", "+1 (212) 555-0142", "1-212-555-0142", "555-0142", "0125550142", "1125550142",
             "2120550142", "2121550142", "+442071234567", "+0442071234567", "+1234567", "+12345678", "+1234567890123456", "212 555 0142 ext 5", "five five five", "212-555-0142x9", "++12125550142",
             "212+5550142", "1 212 555 0142 ", "  2125550142  ", "212/555/0142", "212 555 01421", "9", "+", "()", "-", "١٢٣", "21255501424" * 5, "2" * 41, "\t2125550142\n"]
    alphabet = "0123456789 ()-.+"
    for _ in range(900):
        length = rng.choice([0, 3, 7, 8, 9, 10, 10, 10, 11, 11, 12, 15, 16])
        text = "".join(rng.choice(alphabet) for _ in range(length))
        if rng.random() < 0.3:
            text = "+" + text
        if rng.random() < 0.15:
            text = text + rng.choice(["x", "a", "#", "*"])
        cases.append(text)
    for _ in range(300):  # well-formed numbers in the ways people say and write them
        area, exch, line = f"{rng.randint(2, 9)}{rng.randint(0, 9)}{rng.randint(0, 9)}", f"{rng.randint(2, 9)}{rng.randint(0, 9)}{rng.randint(0, 9)}", f"{rng.randint(0, 9999):04d}"
        cases.append(rng.choice(["({a}) {e}-{l}", "{a}-{e}-{l}", "1 {a} {e} {l}", "+1{a}{e}{l}", "{a}.{e}.{l}", "{a}{e}{l}", "+1 ({a}) {e}-{l}", " {a} {e} {l} ", "1-{a}-{e}-{l}", "+44 {a} {e} {l}"]).format(a=area, e=exch, l=line))
    cases = [c for c in cases if not any(ch.isdigit() and not ch.isascii() for ch in c)]  # other scripts' digits are a documented difference
    return [{"text": c, "phone": normalize_phone(c)} for c in cases] + [{"text": None, "phone": normalize_phone(None)}, {"text": 5551234567, "phone": normalize_phone(5551234567)}]


def plan_json(plan) -> list:
    return [
        {"staffId": p.staff.staff_id, "hours": None if p.hours is None else list(p.hours), "breaks": [[b.start, b.end, b.label] for b in p.breaks],
         "visits": [[v.start, v.end, v.service, v.category, v.client, v.reference] for v in p.visits]}
        for p in plan.plans
    ]


def snapshot_json(snapshot) -> list:
    return [
        {"staff": sd.staff, "staffId": sd.staff_id, "working": None if sd.working is None else [[ms(i.start), ms(i.end)] for i in sd.working],
         "timeOff": None if sd.time_off is None else [[ms(i.start), ms(i.end)] for i in sd.time_off],
         "appointments": None if sd.appointments is None else [[ms(a.interval.start), ms(a.interval.end), a.blocks_time] for a in sd.appointments]}
        for sd in snapshot.staff_days
    ]


def random_rows(rng: random.Random, day: date, dataset) -> list:
    names = ["Willa Hartwell", "Ansel Hartwell", "Cedric Lowe", "Mary Jo Smith-Jones", "Prince", "  Spaced   Out  ", "zoe", "Ana Maria De La Cruz"]
    rows = []
    for i in range(rng.randint(1, 7)):
        staff_id = rng.choice([s.staff_id for s in dataset.roster] + ["999999"])
        first = rng.choice([540, 600, 615, 630, 660, 705, 720, 780, 855, 900, 960, 1005, 1050, 1110, 1140])
        start = local(day, first) + timedelta(seconds=rng.choice([0, 0, 0, 0]))
        duration = rng.choice([15, 30, 40, 45, 60, 75, 90])
        rows.append(StoredAppointment(
            i + 1, f"REF{i:03d}", i + 1, rng.choice(names), "svc", f"Service {i}", rng.choice(["massage", "facial", "lash", "waxing"]), staff_id, int(start.timestamp()), duration,
            rng.choice([0, 0, 5, 10]), rng.choice([0, 5, 10, 15]), rng.choice(["confirmed"] * 6 + ["cancelled", "completed", "no_show"]), 1, "voice", 1, 1, None, None, None,
        ))
    return rows


def dataset_vectors() -> dict:
    rng = random.Random(2026)
    out = []
    todays = [date(2026, 3, 5), date(2026, 3, 8), date(2026, 10, 21), date(2026, 10, 29), date(2026, 11, 1), date(2026, 12, 30), date(2027, 6, 15)]
    for today in todays:
        for diagnostic in ((), ("910003",), ("910001", "910006")):
            dataset = DemoDataset(today, diagnostic_unknown_hours=diagnostic)
            for offset in range(-9, 38):
                day = today + timedelta(days=offset)
                entry = {"today": [today.year, today.month, today.day], "diagnostic": list(diagnostic), "day": [day.year, day.month, day.day], "covered": dataset.covers(day)}
                try:
                    plan = dataset.base_day_plan(day)
                except NotCovered:
                    entry["plan"] = None
                    out.append(entry)
                    continue
                entry["plan"] = plan_json(plan)
                if rng.random() < 0.5 or offset in (1, 2):
                    rows = random_rows(rng, day, dataset)
                    merged = merge_rows(plan, rows, TZ, dataset.roster)
                    entry["rows"] = [[r.appt_id, r.reference, r.client_id, r.client_name, r.service_id, r.service_name, r.category, r.staff_id, r.start_utc, r.duration_minutes,
                                      r.buffer_before_minutes, r.buffer_after_minutes, r.status, r.version, r.origin, r.created_at, r.updated_at, r.cancelled_at, r.cancel_reason, r.seed_key] for r in rows]
                    entry["merged"] = plan_json(merged)
                    now = local(day, rng.choice([480, 600, 900])) - timedelta(days=rng.choice([0, 1]))
                    entry["now"] = ms(now)
                    entry["snapshot"] = snapshot_json(snapshot_from_plan(merged, TZ, now))
                else:
                    now = local(day, 600)
                    entry["now"] = ms(now)
                    entry["snapshot"] = snapshot_json(snapshot_from_plan(plan, TZ, now))
                out.append(entry)
    return {"days": out, "displayNames": [{"full": n, "shown": display_name(n)} for n in ["Willa Hartwell", "Prince", "  Spaced   Out  ", "Ana Maria De La Cruz", "", "x", "zoe quinn"]],
            "bounds": [{"day": [d.year, d.month, d.day], "bounds": list(day_bounds(d, TZ))} for d in (date(2026, 3, 7), date(2026, 3, 8), date(2026, 3, 9), date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2), date(2026, 10, 21))]}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = dataset_vectors()
    (OUT / "speech.json").write_text(json.dumps(speech_vectors()), encoding="utf-8")
    (OUT / "phone.json").write_text(json.dumps(phone_vectors()), encoding="utf-8")
    (OUT / "dataset.json").write_text(json.dumps(data), encoding="utf-8")
    print("wrote speech.json, phone.json, dataset.json:", len(data["days"]), "day entries,", sum(1 for d in data["days"] if d.get("merged")), "with overlay rows")


if __name__ == "__main__":
    main()
