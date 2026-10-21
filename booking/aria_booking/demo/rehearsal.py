"""A rehearsal sheet for a supervised walk-through of the simulated salon: fictional identities, who works, and a few things to ASK, each with the answer the real tools give.

Nothing here is a script for the assistant and nothing is forced: the sheet opens a throw-away appointment file (a temporary folder, never the demo's own file), seeds it the way a
real start does for the chosen demo date, and asks the SAME voice tools the call uses. What it prints is what they answered. The examples are chosen by rule from the real catalog,
roster and schedules (roster order, then the first time that gives the wanted kind of answer), so the same date always gives the same sheet and a different date gives its own.

Kinds of example (each only when the day has one):
  * an available time, for a named technician and a service she does;
  * a known blocked time, with the actual reason and real alternatives;
  * a technician who does not do the chosen service;
  * the cleanup boundary: right after a booked appointment ends, the technician is still cleaning up, so the next start is refused, and the first quarter hour after the cleanup is not.
"""

from __future__ import annotations

import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from . import SPA_TIMEZONE
from ..appointments.launch import build_lifecycle_demo
from ..appointments.lifecycle import open_lifecycle
from ..appointments.models import NewBooking, to_epoch
from ..appointments.seed import SEEDED_CLIENTS

AVAILABLE_SERVICE = "massage-deep-tissue-massage-60"
BLOCKED_SERVICE = "facial-microdermabrasion-facial-75"
INELIGIBLE_SERVICE = "facial-signature-facial-60"
PREFERRED_TIMES = ("16:00", "15:00", "14:00", "17:00", "13:00", "11:00", "12:00", "18:00")
SCAN_TIMES = tuple(f"{minutes // 60:02d}:{minutes % 60:02d}" for minutes in range(10 * 60, 19 * 60 + 1, 15))


def _clock(text: str) -> str:
    hour, minute = int(text[:2]), int(text[3:])
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def _plus(text: str, minutes: int) -> str:
    total = int(text[:2]) * 60 + int(text[3:]) + minutes
    return f"{total // 60:02d}:{total % 60:02d}"


def _day_line(dataset, day: date) -> dict:
    plan = dataset.day_plan(day)
    return {
        "date": day.isoformat(), "weekday": day.strftime("%A"),
        "working": [{"name": p.staff.name, "hours": f"{_clock(f'{p.hours[0] // 60:02d}:{p.hours[0] % 60:02d}')} to {_clock(f'{p.hours[1] // 60:02d}:{p.hours[1] % 60:02d}')}"}
                    for p in plan.plans if p.shown_hours],
        "not_working": [s.name for s in dataset.roster if s.name not in {p.staff.name for p in plan.plans}],
    }


def rehearsal_plan(today: date, *, tz: Optional[ZoneInfo] = None) -> dict:
    """The sheet for demo date ``today`` (the day the walk-through is given, in the spa's time zone, at 10 AM). Deterministic."""
    tz = tz or ZoneInfo(SPA_TIMEZONE)
    now = datetime(today.year, today.month, today.day, 10, 0, tzinfo=tz)
    day = today + timedelta(days=1)
    with tempfile.TemporaryDirectory() as folder:
        lifecycle = open_lifecycle(Path(folder) / "rehearsal.sqlite3", today=today, real_now=lambda: now)
        endpoint, _driver = build_lifecycle_demo("rehearsal-only-key", lifecycle, bearer_token=None)
        tools = endpoint._tools
        dataset, registry = lifecycle.dataset, lifecycle.dataset.registry

        def ask(service_id: str, at: str, staff: Optional[str] = None) -> dict:
            args = {"service_id": service_id, "date": day.isoformat(), "time": at}
            if staff:
                args["staff"] = staff
            return tools.check_slot("rehearsal", args)

        def working_and_eligible(service_id: str) -> list[str]:
            service = registry.get(service_id)
            shown = {p.staff.name for p in dataset.day_plan(day).plans if p.shown_hours}
            return [s.name for s in dataset.roster if s.name in shown and service.eligible(s.name, s.staff_id)]

        sheet: dict = {
            "demo_date": today.isoformat(), "asked_day": _day_line(dataset, day), "day_after": _day_line(dataset, today + timedelta(days=2)),
            "identities": [{"name": name, "phone": phone} for name, phone in SEEDED_CLIENTS],
            "seeded_upcoming": [{"template": template, "reference": reference} for template, reference in lifecycle.seeded.upcoming_added] if lifecycle.seeded else [],
            "examples": {},
        }

        available = None
        service = registry.get(AVAILABLE_SERVICE)
        for staff in working_and_eligible(AVAILABLE_SERVICE):
            for at in dict.fromkeys(PREFERRED_TIMES + SCAN_TIMES):
                reply = ask(AVAILABLE_SERVICE, at, staff)
                if reply.get("status") == "available":
                    available = {"service": service.booksy_name, "technician": staff, "time": at, "say": f"Is {staff} available tomorrow at {_clock(at)} for a one-hour {service.booksy_name.split(',')[0].lower()}?",
                                 "status": reply["status"], "answer": reply.get("speak")}
                    break
            if available:
                break
        sheet["examples"]["available"] = available

        blocked = None
        blocked_service = registry.get(BLOCKED_SERVICE)
        for at in dict.fromkeys(PREFERRED_TIMES + SCAN_TIMES):
            reply = ask(BLOCKED_SERVICE, at)
            if reply.get("status") != "available":
                blocked = {"service": blocked_service.booksy_name, "time": at, "say": f"Is a {blocked_service.booksy_name.split(',')[0]} available tomorrow at {_clock(at)}?",
                           "status": reply.get("status"), "reason": reply.get("reason"), "answer": reply.get("speak")}
                break
        sheet["examples"]["blocked"] = blocked

        ineligible = None
        ineligible_service = registry.get(INELIGIBLE_SERVICE)
        shown = {p.staff.name for p in dataset.day_plan(day).plans if p.shown_hours}
        for staff in dataset.roster:
            if staff.name in shown and not ineligible_service.eligible(staff.name, staff.staff_id):
                reply = ask(INELIGIBLE_SERVICE, "14:00", staff.name)
                ineligible = {"service": ineligible_service.booksy_name, "technician": staff.name, "time": "14:00", "say": f"Is {staff.name} available tomorrow at 2 for a {ineligible_service.booksy_name.split(',')[0]}?",
                              "status": reply.get("status"), "reason": reply.get("reason"), "answer": reply.get("speak")}
                break
        sheet["examples"]["ineligible"] = ineligible

        cleanup = None
        for staff_name in working_and_eligible(AVAILABLE_SERVICE):
            staff = next(s for s in dataset.roster if s.name == staff_name)
            for start in dict.fromkeys(PREFERRED_TIMES + SCAN_TIMES):
                end = _plus(start, service.duration_minutes)
                after = _plus(end, service.buffer_after_minutes)
                if after >= "20:00":
                    continue
                # a start, the moment it ends and the first quarter hour after its cleanup are all open BEFORE the booking, so only the cleanup can be what blocks the second one
                if any(ask(AVAILABLE_SERVICE, at, staff_name).get("status") != "available" for at in (start, end, after)):
                    continue
                booked = lifecycle.store.book(
                    NewBooking("Rehearsal Person", "+12125550100", service.service_id, service.booksy_name, dataset.category_of(service.service_id), staff.staff_id,
                               to_epoch(datetime(day.year, day.month, day.day, int(start[:2]), int(start[3:]), tzinfo=tz)), service.duration_minutes,
                               service.buffer_before_minutes, service.buffer_after_minutes),
                    validate=lifecycle.judge.validator())
                if booked.status != "booked":
                    continue
                at_end, at_after = ask(AVAILABLE_SERVICE, end, staff_name), ask(AVAILABLE_SERVICE, after, staff_name)
                cleanup = {"technician": staff_name, "service": service.booksy_name, "booked_start": start, "booked_end": end, "cleanup_minutes": service.buffer_after_minutes,
                           "say_after_booking": f"Is {staff_name} available at {_clock(end)}?", "at_end": {"time": end, "status": at_end.get("status"), "reason": at_end.get("reason"), "answer": at_end.get("speak")},
                           "after_cleanup": {"time": after, "status": at_after.get("status"), "answer": at_after.get("speak")}}
                break
            if cleanup:
                break
        sheet["examples"]["cleanup_boundary"] = cleanup
    return sheet


def render_text(sheet: dict) -> str:
    lines = [
        f"REHEARSAL SHEET for demo date {sheet['demo_date']} (invented data; computed now from the real tools, not a script).",
        "'Tomorrow' below is " + sheet["asked_day"]["weekday"] + f" {sheet['asked_day']['date']}. Ask in your own words; the assistant decides, and what is printed is what the tools answered.",
        "", "Fictional identities (reserved 555-01xx numbers; always give the COMPLETE number):",
    ]
    for person in sheet["identities"]:
        lines.append(f"  - {person['name']}: {person['phone'][2:5]}-{person['phone'][5:8]}-{person['phone'][8:]}" + ("  (shares this number with another client: a name is needed)" if sum(p['phone'] == person['phone'] for p in sheet['identities']) > 1 else ""))
    for label, key in (("Tomorrow", "asked_day"), ("The day after", "day_after")):
        day = sheet[key]
        lines += ["", f"{label}: {day['weekday']} {day['date']}", "  working: " + ", ".join(f"{p['name']} ({p['hours']})" for p in day["working"]), "  not working: " + (", ".join(day["not_working"]) or "nobody")]
    examples = sheet["examples"]
    lines += ["", "Things to ask, with the answer the tools give:"]
    for title, key in (("An open time", "available"), ("A known blocked time", "blocked"), ("A technician who does not do the service", "ineligible")):
        item = examples.get(key)
        lines.append(f"  * {title}: " + (f"\"{item['say']}\"  ->  {item['status']}" + (f" ({item['reason']})" if item.get("reason") else "") + f": {item['answer']}" if item else "(no such example on this day)"))
    cleanup = examples.get("cleanup_boundary")
    if cleanup:
        lines += [
            f"  * The cleanup boundary: book {cleanup['technician']} for {cleanup['service']} at {_clock(cleanup['booked_start'])} (it ends {_clock(cleanup['booked_end'])}, then {cleanup['cleanup_minutes']} minutes of cleanup).",
            f"      Then \"{cleanup['say_after_booking']}\"  ->  {cleanup['at_end']['status']}: {cleanup['at_end']['answer']}",
            f"      And at {_clock(cleanup['after_cleanup']['time'])}  ->  {cleanup['after_cleanup']['status']}: {cleanup['after_cleanup']['answer']}",
        ]
    else:
        lines.append("  * The cleanup boundary: (not available on this day)")
    lines += ["", "Other checks that need no fixture: give a number with fewer than ten digits (she asks for the full number); give the shared household number (she asks for the full name);",
              "decline at the read-back (nothing is saved); move a booking to a taken time (the original stays); cancel one of three (only that one goes).", ""]
    return "\n".join(lines)
