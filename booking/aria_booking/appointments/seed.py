"""The demo's seeded fictional clients and their appointments, so a supervised test has something to find, change and cancel.

Everything here is invented. The names are fictional, and the phone numbers are in the 555-01xx range North America reserves for fiction, so no number can
reach a real person. Nothing in this module sends anything: seeded rows never write a notification.

Identity is stable. Every seeded row has a seed key and its reference is derived from that key, so a restart (or a rebuilt file) gives the same reference.
Past visits are seeded once. Upcoming visits are topped up when the demo starts: for each template, if the store holds no seeded row after now, the next
matching weekday is booked. A row that was cancelled is a row, so it is not made again until it has passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from .judge import PlacementJudge
from .models import CANCELLED, COMPLETED, NO_SHOW, NewBooking, ORIGIN_SEED, to_epoch

WILLA = ("Willa Hartwell", "+12125550142")
ANSEL = ("Ansel Hartwell", "+12125550142")  # the same household number: a name is needed to tell them apart
CEDRIC = ("Cedric Lowe", "+12125550177")  # history only: no upcoming appointment
SEEDED_CLIENTS = (WILLA, ANSEL, CEDRIC)

# (template, client, weekday with Monday = 0, service id, candidate start times in minutes since midnight, tried in this order)
UPCOMING = (
    ("tue-waxing", WILLA, 1, "waxing-full-leg-40", (900, 840, 960, 780, 1020)),
    ("fri-head-spa", WILLA, 4, "head-spa-head-scalp-massage-30", (660, 720, 780, 840, 900)),
    ("sun-massage", WILLA, 6, "massage-swedish-massage-60", (840, 780, 900, 720, 960)),
    ("thu-express-facial", ANSEL, 3, "facial-express-facial-30", (1020, 960, 900, 840, 780)),
)

# (template, client, days before the demo starts, service id, final status): past visits, made once
HISTORY = (
    ("lash-lifting", WILLA, 21, "lash-eyelash-lifting-45", COMPLETED),
    ("swedish-massage", WILLA, 14, "massage-swedish-massage-60", COMPLETED),
    ("signature-facial", WILLA, 42, "facial-signature-facial-60", COMPLETED),
    ("cancelled-lash", WILLA, 7, "lash-eyelash-lifting-45", CANCELLED),
    ("no-show-facial", WILLA, 28, "facial-signature-facial-60", NO_SHOW),
    ("deep-tissue", ANSEL, 10, "massage-deep-tissue-massage-60", COMPLETED),
    ("signature-facial", CEDRIC, 30, "facial-signature-facial-60", COMPLETED),
)


WEEKS_TRIED = 4
FALLBACK_TIMES = tuple(range(10 * 60, 19 * 60 + 1, 15))  # after the preferred times, any quarter hour of the working day


@dataclass
class SeedReport:
    upcoming_added: list = field(default_factory=list)  # (template, reference)
    upcoming_skipped: list = field(default_factory=list)  # templates that could not be placed
    history_added: int = 0


def _key(prefix: str, template: str, client: tuple, suffix: str = "") -> str:
    return f"{prefix}:{client[0].split()[0].casefold()}:{template}" + (f":{suffix}" if suffix else "")


def seed_demo(store, judge: PlacementJudge, clock: Callable[[], datetime], tz: ZoneInfo) -> SeedReport:
    """Make whatever seeded rows are missing. Safe to run on every start: a row that exists is never changed, duplicated or revived."""
    report = SeedReport()
    now = clock().astimezone(tz)
    registry = judge.dataset.registry
    roster = judge.dataset.roster

    def eligible_staff(service):
        return [s for s in roster if s.hours_known and service.eligible(s.name, s.staff_id)]

    for template, client, days_ago, service_id, status in HISTORY:
        service = registry.get(service_id)
        staff = eligible_staff(service)[0]
        when = (now - timedelta(days=days_ago)).replace(hour=11, minute=0, second=0, microsecond=0)
        outcome = store.add_history(
            NewBooking(
                client[0], client[1], service.service_id, service.booksy_name, _category(judge, service.service_id), staff.staff_id, to_epoch(when),
                service.duration_minutes, service.buffer_before_minutes, service.buffer_after_minutes, origin=ORIGIN_SEED, seed_key=_key("history", template, client),
            ),
            status,
        )
        if outcome.status == "booked":
            report.history_added += 1

    for template, client, weekday, service_id, times in UPCOMING:
        if store.seeded_after(_key("upcoming", template, client) + ":", to_epoch(now)):
            continue
        service = registry.get(service_id)
        first = (now + timedelta(days=2)).date()  # at least two days out, so the minimum-notice rule is never what stops a seed
        while first.weekday() != weekday:
            first += timedelta(days=1)
        placed = False
        for week in range(WEEKS_TRIED):  # a crowded generated day moves the seed to the same weekday a week later, never to another weekday
            day = first + timedelta(days=7 * week)
            for minutes in dict.fromkeys(times + FALLBACK_TIMES):
                for staff in eligible_staff(service):
                    start = datetime(day.year, day.month, day.day, minutes // 60, minutes % 60, tzinfo=tz)
                    outcome = store.book(
                        NewBooking(
                            client[0], client[1], service.service_id, service.booksy_name, _category(judge, service.service_id), staff.staff_id, to_epoch(start),
                            service.duration_minutes, service.buffer_before_minutes, service.buffer_after_minutes, origin=ORIGIN_SEED,
                            seed_key=_key("upcoming", template, client, day.isoformat()),
                        ),
                        validate=judge.validator(),
                    )
                    if outcome.status in ("booked", "replayed"):
                        report.upcoming_added.append((template, outcome.appointment.reference))
                        placed = True
                        break
                if placed:
                    break
            if placed:
                break
        if not placed:
            report.upcoming_skipped.append(template)
    return report


def _category(judge: PlacementJudge, service_id: str) -> str:
    return judge.dataset.category_of(service_id)
