"""Command line for the Aria booking prototype.

    python -m aria_booking config                      show effective settings (business id masked)
    python -m aria_booking login                       open the test browser; a PERSON signs in; verify the business
    python -m aria_booking discover [--date today]     dump the calendar page STRUCTURE (redacted) for review
    python -m aria_booking find [--from D] [--days N]  list free slots (unknown days are reported, never "free")
    python -m aria_booking book --start "YYYY-MM-DD HH:MM" --confirm-business-id ID
                                                       create ONE fictional test booking (needs ARIA_LIVE_BOOKSY=1)

Nothing here runs a live action by default, and nothing here ever asks for or stores a password.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta
from typing import Callable, Mapping, Optional
from zoneinfo import ZoneInfo

from .booking_service import BookingService, Status
from .availability import describe
from .config import Config, ConfigError
from .discover import write_report
from .discover_interactive import ALLOWED_STEPS, parse_allow, run_interactive_discovery
from .discover_interactive import RunStopped
from .form_rehearsal import run_rehearsal
from .ledger import ref_from_key, request_key
from .models import AppointmentSpec
from .safety import SafetyViolation, build_note, check_request
from .driver import DriverError
from .ledger import Ledger, LedgerError
from .selenium_driver import SeleniumBooksyDriver

EXIT_OK, EXIT_REFUSED, EXIT_REVIEW, EXIT_NOT_SAVED = 0, 2, 3, 4


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_day(text: Optional[str], tz: ZoneInfo, now: datetime) -> date:
    if not text or text == "today":
        return now.astimezone(tz).date()
    return date.fromisoformat(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aria_booking", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("config")
    sub.add_parser("login")
    p = sub.add_parser("discover")
    p.add_argument("--date", default="today")
    p = sub.add_parser("discover-steps", help="click-through discovery; every click type must be approved with --allow")
    p.add_argument("--allow", default="", help="comma list of approved steps: " + ", ".join(ALLOWED_STEPS))
    p = sub.add_parser("rehearse", help="fill the New Appointment form completely, NEVER save, then discard the draft")
    p.add_argument("--start", required=True, help='local start, e.g. "2026-10-12 11:00"')
    p.add_argument("--confirm-business-id", required=True)
    p.add_argument("--approve-note-typing", action="store_true", help="allow typing the ARIA TEST note into the unsaved form")
    p.add_argument("--approve-draft-discard", action="store_true", help="allow clicking Discard on the unsaved draft")
    p.add_argument("--approve-tour-popups", action="store_true", help="allow closing product-tour popups that cover the form")
    p = sub.add_parser("find")
    p.add_argument("--from", dest="first", default="today")
    p.add_argument("--days", type=int, default=7)
    p = sub.add_parser("book")
    p.add_argument("--start", required=True, help='local start, e.g. "2026-10-12 10:00"')
    p.add_argument("--confirm-business-id", required=True)
    return parser


def main(
    argv: Optional[list[str]] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    driver_factory: Optional[Callable[[Config], object]] = None,
    clock: Optional[Callable[[], datetime]] = None,
    out: Callable[[str], None] = print,
) -> int:
    args = build_parser().parse_args(argv)
    env = os.environ if environ is None else environ
    try:
        cfg = Config.from_env(env)
        tz = ZoneInfo(cfg.timezone)
    except (ValueError, KeyError) as exc:  # ConfigError (a ValueError), unknown timezone (a KeyError)
        out(f"configuration error: {exc}")
        return EXIT_REFUSED
    now_fn = clock or (lambda: datetime.now(tz))

    if args.command == "config":
        out(json.dumps(cfg.redacted_summary(), indent=2))
        return EXIT_OK

    if args.command == "book":
        # Refuse BEFORE any browser is created.
        if not _truthy(env.get("ARIA_LIVE_BOOKSY")):
            out("refused: live booking needs ARIA_LIVE_BOOKSY=1 in the environment.")
            return EXIT_REFUSED
        if not cfg.business_id or args.confirm_business_id != cfg.business_id:
            out("refused: --confirm-business-id must equal the configured ARIA_BOOKSY_BUSINESS_ID.")
            return EXIT_REFUSED
        try:
            args.start_dt = datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except ValueError:
            out('refused: --start must look like "2026-10-12 10:00" (local time).')
            return EXIT_REFUSED

    if args.command == "rehearse":
        approvals = (
            ("--approve-note-typing", args.approve_note_typing),
            ("--approve-draft-discard", args.approve_draft_discard),
            ("--approve-tour-popups", args.approve_tour_popups),
        )
        missing = [flag for flag, given in approvals if not given]
        if not _truthy(env.get("ARIA_LIVE_BOOKSY")):
            out("refused: the rehearsal needs ARIA_LIVE_BOOKSY=1 in the environment.")
            return EXIT_REFUSED
        if not cfg.business_id or args.confirm_business_id != cfg.business_id:
            out("refused: --confirm-business-id must equal the configured ARIA_BOOKSY_BUSINESS_ID.")
            return EXIT_REFUSED
        if missing:
            out("refused: the rehearsal types a note and discards a draft, so it needs: " + ", ".join(missing))
            return EXIT_REFUSED
        try:
            args.start_dt = datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except ValueError:
            out('refused: --start must look like "2026-10-12 11:00" (local time).')
            return EXIT_REFUSED

    if args.command == "discover-steps":
        try:
            args.allowed_steps = parse_allow(args.allow)
        except ValueError as exc:
            out(f"refused: {exc}")
            return EXIT_REFUSED

    driver = driver_factory(cfg) if driver_factory else SeleniumBooksyDriver(cfg)
    try:
        return _run(args, cfg, tz, now_fn, driver, out)
    except (ConfigError, DriverError, LedgerError) as exc:
        out(f"stopped: {exc}")
        return EXIT_REFUSED
    finally:
        driver.close()


def _run(args, cfg: Config, tz: ZoneInfo, now_fn, driver, out) -> int:
    cfg.require_business_id()
    observed = driver.verify_business()
    if observed != cfg.business_id:
        out("refused: the browser is signed in to a DIFFERENT business than the configured test business. Nothing was read or changed.")
        return EXIT_REFUSED

    if args.command == "login":
        out("signed in to the configured test business.")
        return EXIT_OK

    if args.command == "discover":
        report = driver.discover(args.date)
        path = write_report(cfg.evidence_dir, report)
        out(f"wrote {report['node_count']} redacted structure nodes to {path} (local, git-ignored)")
        if not report.get("page_ready", True):
            out("WARNING: the page was still loading when captured, so the structure is partial.")
        return EXIT_OK

    if args.command == "rehearse":
        key = request_key(cfg.business_id, cfg.staff_name, cfg.service_name, args.start_dt, cfg.service_duration_minutes)
        spec = AppointmentSpec(
            cfg.staff_name, cfg.service_name, args.start_dt, cfg.service_duration_minutes, build_note(ref_from_key(key))
        )
        try:  # the same guards as a real booking: right business, test service, future slot, ARIA TEST note
            check_request(cfg, spec, now=now_fn(), observed_business_id=observed, bookings_this_run=0)
        except SafetyViolation as violation:
            out("refused by safety checks: " + "; ".join(violation.reasons))
            return EXIT_REFUSED
        try:
            result = run_rehearsal(driver, cfg, spec, out=out)
        except RunStopped:
            return EXIT_REVIEW
        out("RESULT: " + ("everything matched the plan." if not result.problems else "DIFFERENCES FOUND:"))
        for problem in result.problems:
            out(f"  - {problem}")
        out(f"draft discarded: {result.discarded}. Nothing was saved.")
        return EXIT_OK if (not result.problems and result.discarded) else EXIT_REVIEW

    if args.command == "discover-steps":
        paths = run_interactive_discovery(driver, cfg, allow=args.allowed_steps, out=out)
        out(f"done: {len(paths)} redacted capture(s) in {cfg.evidence_dir} (local, git-ignored)")
        return EXIT_OK

    service = BookingService(cfg, driver, Ledger(cfg.ledger_path, clock=now_fn), clock=now_fn)

    if args.command == "find":
        for item in service.search(_parse_day(args.first, tz, now_fn()), args.days):
            out(f"{item.day}: {describe(item.search)}")
        return EXIT_OK

    # book
    result = service.book(args.start_dt)
    out(f"{result.status.value}: {result.message}")
    for detail in result.details:
        out(f"  - {detail}")
    if result.ref:
        out(f"  reference: {result.ref}")
    if result.ok:
        return EXIT_OK
    if result.status in (Status.UNCERTAIN_NEEDS_REVIEW, Status.VERIFY_MISMATCH, Status.BOOKED_CONFLICT_DETECTED):
        return EXIT_REVIEW
    if result.status is Status.NOT_SAVED:
        return EXIT_NOT_SAVED
    return EXIT_REFUSED
