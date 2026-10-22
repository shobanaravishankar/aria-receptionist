"""Scenario recorder: drive the PYTHON lifecycle + preference tools (the oracle) through seeded random walks of mixed calls (good, wrong, misspelled, off-grid, repeated, confirmed and stale
requests, several callers and several calls), and record every request with the clock and every response. The TypeScript tools replay the same requests on their own store and must answer
identically. Randomness inside the tools (confirmation tokens, appointment references) is replaced by counters here and by the same counters in the replay.

Run from the booking folder:  python netlify_showcase/oracle/gen_scenarios.py
"""

from __future__ import annotations

import itertools
import json
import random
import secrets
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aria_booking.appointments.launch import build_lifecycle_demo  # noqa: E402
from aria_booking.appointments.lifecycle import open_lifecycle  # noqa: E402
from aria_booking.showcase.catalog import SHOWCASE_CATALOG  # noqa: E402

TZ = ZoneInfo("America/New_York")
OUT = Path(__file__).resolve().parents[1] / "test" / "vectors"
KEY = "oracle-key-not-a-secret"

SERVICES = None  # filled from the registry
STAFF = [None, None, "", "Sienna", "Tamsin", "Tamsen", "Zed", "Marisol", "Imogen", "Noor", "Priya", "Rowan", "tamsin ", "Odette"]
WORDS = [None, None, None, None, None, "", "massage", "sixty minutes", "deep tissue massage", "deep tissue massage ninety minutes", "head scalp", "a facial", "swedish", "zzz nothing", "sixty minute massage"]
TIMES = ["10:00", "10:15", "11:30", "12:00", "13:00", "14:00", "14:45", "15:45", "16:00", "16:45", "17:00", "18:30", "19:00", "19:45", "20:00", "07:00", "25:00", "9:5", "16:07", "9:00"]
GOOD_TIMES = ["10:00", "10:15", "10:30", "11:00", "11:30", "12:00", "12:45", "13:00", "14:00", "14:15", "15:00", "15:45", "16:00", "16:30", "17:00", "17:15", "18:00", "18:30"]
WILLA_PHONES = ["(212) 555-0142", "212-555-0142", "+1 212 555 0142"]
PERSONAS = [
    ("(212) 555-0142", "Willa Hartwell"), ("212-555-0142", "Ansel Hartwell"), ("212-555-0177", "Cedric Lowe"), ("+1 212 555 0188", "Mira Quillfeather"),
    ("(212) 555-0142", "Villa Hartwell"), ("(212) 555-0142", "Quentin Hartwell"), ("212-555-0177", "Sedric Lowe"), ("+1 646 555 0111", "Priya Nair"),
]
JUNK_PHONES = ["", "555-0142", "call me", "+1 212", "21255501", None]
WINDOW_ARGS = ["starting_at_or_after", "starting_after", "starting_at_or_before", "starting_before"]
ROUTES = [
    ("lookup_service", 9), ("check_slot", 14), ("find_alternatives", 11), ("find_appointments", 10), ("last_completed_visit", 6), ("book_appointment", 17), ("cancel_appointment", 8),
    ("reschedule_appointment", 9), ("get_preferences", 5), ("save_preference", 6), ("remove_preference", 4),
]
LOOKUPS = ["swedish massage", "massage", "sixty minute massage", "I'd like a ninety minute massage", "deep tissue massage sixty minutes", "facial", "lymphatic", "eyelash lifting", "5-visit bundle",
           "sixty minutes", "an hour", "wax", "zzz", "", None, "deep tissue", "classic full set 2 week refill", "couples", "head spa", "thirty or sixty minute massage"]
CATEGORIES = ["massage", "lashes", "facial", "waxing", "head spa", "nonsense", None]
PREF_SERVICES = ["Swedish Massage", "swedish massage", "massage", "Signature Facial", "deep tissue massage", "Eyelash Lifting", "zzz", "", None]
STAFF_PREF = ["Sienna", "Rowan", "Marisol", "Priya", "Zed", "", None, "tamsin"]


def lc_prefs(tools):
    return tools._prefs


class Recorder:
    def __init__(self, seed: int, steps: int):
        self.rng = random.Random(seed)
        self.steps = steps
        self.log: list = []

    def run(self, workdir: Path) -> dict:
        rng = self.rng
        tokens, chars = itertools.count(1), itertools.count(0)
        secrets.token_urlsafe = lambda n=None: f"tok{next(tokens):05d}"  # type: ignore[assignment]
        secrets.choice = lambda seq: seq[next(chars) % len(seq)]  # type: ignore[assignment]
        start_day = rng.choice([date(2026, 10, 13), date(2026, 10, 22), date(2026, 10, 30), date(2027, 3, 12), date(2026, 3, 6), date(2026, 12, 20)])
        now = [datetime(start_day.year, start_day.month, start_day.day, rng.choice([8, 9, 10, 11, 14]), rng.choice([0, 20, 40]), tzinfo=TZ)]
        opened_at = int(now[0].timestamp() * 1000)
        lc = open_lifecycle(workdir / "s.sqlite3", today=None, real_now=lambda: now[0], seed=True)
        endpoint, _driver = build_lifecycle_demo(KEY, lc, preferences=True)
        tools = endpoint._tools
        services = sorted(lc.dataset.registry._services)
        calls = [f"call-{i}" for i in range(rng.randint(2, 4))]
        persona_of = {c: rng.choice(PERSONAS[:4]) for c in calls}
        last_token: dict = {}
        references: list = []
        steps = []
        roster = lc.dataset.roster
        eligible_names = {sid: [s.name for s in roster if s.staff_id in lc.dataset.registry.get(sid).eligible_staff_ids] for sid in services}
        family_of = {sid: SHOWCASE_CATALOG.get(sid).family for sid in services}
        ref_owner: dict = {}
        queue: list = []
        for _ in range(self.steps):
            r = rng.random()
            if r < 0.08:
                now[0] = now[0] + timedelta(minutes=rng.choice([1, 3, 6, 6, 30, 90, 600, 1440]))
            elif r < 0.10:
                now[0] = now[0] + timedelta(days=rng.choice([2, 8]))
            elif r < 0.13:
                steps.append({"op": "maintain", "now": int(now[0].timestamp() * 1000)})
                lc.maintain()
                continue
            call = rng.choice(calls) if rng.random() > 0.02 else rng.choice(["", "   ", "fresh-call"])
            route = rng.choices([n for n, _ in ROUTES], [w for _, w in ROUTES])[0]
            phone, name = persona_of.get(call, rng.choice(PERSONAS))
            if rng.random() < 0.10:
                phone, name = rng.choice(PERSONAS)
            if rng.random() < 0.03:
                phone = rng.choice(JUNK_PHONES)
            tomorrow_ish = now[0].date() + timedelta(days=rng.choice([1, 1, 1, 2, 2, 3, 4, 5]))
            day = tomorrow_ish if rng.random() > 0.12 else now[0].date() + timedelta(days=rng.choice([-1, 0, 8, 35, 40, 65]))
            date_text = day.isoformat() if rng.random() > 0.04 else rng.choice(["2026-02-30", "tomorrow", "", None])
            service = rng.choice(services) if rng.random() > 0.03 else rng.choice(["nope", "", None, 5, "massage"])
            time = rng.choice(GOOD_TIMES) if rng.random() > 0.08 else rng.choice(TIMES)
            staff = None
            if rng.random() < 0.45:
                staff = rng.choice(eligible_names.get(service, [])) if service in eligible_names and rng.random() < 0.8 else rng.choice(STAFF)
            args: dict = {}
            if route == "lookup_service":
                args = {"query": rng.choice(LOOKUPS)}
            elif route in ("check_slot", "find_alternatives", "book_appointment", "reschedule_appointment"):
                if route != "reschedule_appointment":
                    args["service_id"] = service
                    roll = rng.random()
                    if roll < 0.25 and service in family_of:
                        args["service_words"] = family_of[service] if rng.random() < 0.7 else family_of[service].lower() + " sixty minutes"
                    elif roll < 0.35:
                        args["service_words"] = rng.choice(WORDS)
                args["date"] = date_text
                if route != "find_alternatives" or rng.random() < 0.5:
                    args["time"] = time
                if staff is not None:
                    args["staff"] = staff
                if route == "find_alternatives":
                    if rng.random() < 0.35:
                        args[rng.choice(WINDOW_ARGS)] = rng.choice(GOOD_TIMES + ["bad"])
                    if rng.random() < 0.08:
                        args["any_time"] = True
                if route == "book_appointment":
                    args["client_name"] = name if rng.random() > 0.04 else rng.choice(["", None, "   ", "x" * 90])
                    args["phone"] = phone
                if route == "reschedule_appointment":
                    args["phone"] = phone
                    if references and rng.random() > 0.1:
                        ref = rng.choice(references)
                        args["reference"] = ref
                        if ref in ref_owner and rng.random() < 0.85:
                            args["phone"], owner_name = ref_owner[ref]
                            if rng.random() < 0.7:
                                args["name"] = owner_name
                    else:
                        args["reference"] = rng.choice(["ABCDEF", "", None, "23456A"])
            else:
                args["phone"] = phone
                if rng.random() < 0.6:
                    args["name"] = name
                if route == "find_appointments" and rng.random() < 0.3:
                    args["service"] = rng.choice(["waxing", "massage", "facial", "my lashes", None, "zzz"])
                if route == "last_completed_visit":
                    args["service_category"] = rng.choice(CATEGORIES)
                    if rng.random() < 0.3:
                        args["include_technician"] = True
                if route == "cancel_appointment":
                    if references and rng.random() > 0.1:
                        ref = rng.choice(references)
                        args["reference"] = ref
                        if ref in ref_owner and rng.random() < 0.85:
                            args["phone"], owner_name = ref_owner[ref]
                            if rng.random() < 0.7:
                                args["name"] = owner_name
                    else:
                        args["reference"] = rng.choice(["ABCDEF", "", None])
                    if rng.random() < 0.3:
                        args["reason"] = rng.choice(["busy", "  too   many\tspaces ", "x" * 300, None, 5])
                if route in ("get_preferences", "save_preference", "remove_preference") and rng.random() < 0.8:
                    args["service"] = rng.choice(PREF_SERVICES)
                if route == "save_preference":
                    args["staff"] = rng.choice(STAFF_PREF)
            if rng.random() < 0.08 and route != "lookup_service":
                args["name_confirmed"] = True
            # a confirmation: the token of the last read-back on this call and route
            if route in ("book_appointment", "cancel_appointment", "reschedule_appointment", "save_preference", "remove_preference") and rng.random() < 0.6:
                token = last_token.get((call, route))
                if token is not None:
                    chosen_token = token if rng.random() > 0.06 else "wrong-token"
                    echo = last_token.get((call, route, "args"))
                    if echo is not None and rng.random() < 0.9:
                        args = {**echo, "confirmed": True, "confirmation_token": chosen_token}
                    else:
                        args["confirmed"] = True
                        args["confirmation_token"] = chosen_token
            if queue:
                call, route, args = queue.pop(0)
            request = {"now": int(now[0].timestamp() * 1000), "route": route, "call": call, "args": args}
            try:
                response = getattr(tools, route)(call, args)
                response = json.loads(json.dumps(response))
            except Exception as exc:  # recorded, never hidden: the replay must fail the same way
                response = {"exception": type(exc).__name__}
            steps.append({**request, "response": response})
            if isinstance(response, dict):
                if response.get("status") == "confirmation_required" and response.get("confirmation_token") and route in ("book_appointment", "cancel_appointment", "reschedule_appointment", "save_preference", "remove_preference"):
                    if rng.random() < 0.8:
                        confirm = {**{k: v for k, v in args.items() if k not in ("confirmed", "confirmation_token")}, "confirmed": True, "confirmation_token": response["confirmation_token"]}
                        queue.append((call, route, confirm))
                        if rng.random() < 0.3:
                            queue.append((call, route, confirm))  # the same yes arriving twice
                if response.get("status") in ("booked_verified", "already_booked") and isinstance(response.get("appointment"), dict) and rng.random() < 0.5:
                    ref = response["appointment"]["reference"]
                    who = (args.get("phone"), args.get("client_name"))
                    later = now[0].date() + timedelta(days=rng.choice([1, 2, 3, 4]))
                    if rng.random() < 0.6:
                        queue.append((call, "reschedule_appointment", {"reference": ref, "phone": who[0], "name": who[1], "date": later.isoformat(), "time": rng.choice(GOOD_TIMES), **({"staff": rng.choice(["Sienna", "Rowan", "Tamsin", "Marisol"])} if rng.random() < 0.4 else {})}))
                    else:
                        queue.append((call, "cancel_appointment", {"reference": ref, "phone": who[0], "name": who[1], **({"reason": "plans changed"} if rng.random() < 0.5 else {})}))
                if response.get("confirmation_token"):
                    last_token[(call, route)] = response["confirmation_token"]
                    last_token[(call, route, "args")] = {k: v for k, v in args.items() if k not in ("confirmed", "confirmation_token")}
                for a in ([response["appointment"]] if isinstance(response.get("appointment"), dict) else []) + list(response.get("appointments") or []):
                    if a.get("reference"):
                        if a["reference"] not in references:
                            references.append(a["reference"])
                        who = args.get("client_name") or args.get("name")
                        if args.get("phone") and who:
                            ref_owner.setdefault(a["reference"], (args.get("phone"), who))
        from aria_booking.appointments.models import STATUSES

        store = lc.store
        final = {
            "outbox": sorted([r["dedupe_key"], r["kind"], r["body"], r["status"]] for r in store.outbox_rows()),
            "counts": store.counts(),
            "appointments": sorted([a.reference, a.status, a.version, a.staff_id, a.start_utc, a.client_name, a.origin, a.service_id, a.cancel_reason] for a in store.between(0, 4_000_000_000, statuses=STATUSES)),
            "preferences": sorted(
                [c.name, p.family_id, p.staff_id, p.version]
                for c in (store.client(i) for i in range(1, 40)) if c is not None
                for p in lc_prefs(tools).for_client(c.client_id)
            ),
        }
        return {"seed": None, "startDay": [start_day.year, start_day.month, start_day.day], "start": opened_at, "steps": steps, "final": final}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    scenarios, statuses = [], {}
    for seed in range(1, 41):
        with tempfile.TemporaryDirectory() as tmp:
            scenario = Recorder(seed * 7919, 110).run(Path(tmp))
        scenario["seed"] = seed
        scenarios.append(scenario)
        for step in scenario["steps"]:
            if "op" in step:
                continue
            key = f"{step['route']}/{step['response'].get('status', step['response'].get('exception'))}"
            statuses[key] = statuses.get(key, 0) + 1
    (OUT / "scenarios.json").write_text(json.dumps({"scenarios": scenarios}), encoding="utf-8")
    print("scenarios", len(scenarios), "steps", sum(len(s["steps"]) for s in scenarios), "distinct route/status pairs", len(statuses))
    for key in sorted(statuses):
        print(f"  {statuses[key]:4d} {key}")


if __name__ == "__main__":
    main()
