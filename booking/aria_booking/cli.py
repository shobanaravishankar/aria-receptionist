"""Command line for the Aria booking prototype.

    python -m aria_booking config                      show effective settings (business id masked)
    python -m aria_booking login                       open the test browser; a PERSON signs in; verify the business
    python -m aria_booking discover [--date today]     dump the calendar page STRUCTURE (redacted) for review
    python -m aria_booking find [--from D] [--days N]  list free slots (unknown days are reported, never "free")
    python -m aria_booking verify --start "YYYY-MM-DD HH:MM" --confirm-business-id ID --approve-tour-popups --approve-note-readback
                                                       READ-ONLY: find a recorded booking by its reference; creates nothing
    python -m aria_booking serve --confirm-business-id ID [--port N] [the five --approve-* flags to ALLOW booking]
                                                       Retell function endpoint on 127.0.0.1 only; READ-ONLY unless all five
                                                       booking approvals are given. Needs ARIA_LIVE_BOOKSY=1 and ARIA_RETELL_API_KEY.
    python -m aria_booking book --start "YYYY-MM-DD HH:MM" --confirm-business-id ID --approve-save --approve-note-typing
           --approve-tour-popups --approve-not-now --approve-note-readback
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
from .voice import retell_http
from .voice.tools import VoiceTools

EXIT_OK, EXIT_REFUSED, EXIT_REVIEW, EXIT_NOT_SAVED = 0, 2, 3, 4

# Every one of these must be given for `book`; each names one thing the run is allowed to do to the real account.
BOOK_APPROVALS = {
    "save": "allow the ONE click on Save that creates the appointment",
    "note-typing": "allow typing the ARIA TEST note into the unsaved form",
    "tour-popups": "allow closing product-tour popups that cover the page",
    "not-now": "allow answering the new-client prompt after Save with NOT NOW (nothing else)",
    "note-readback": "allow opening appointment details (read-only) to read the saved note back",
}


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
    p = sub.add_parser("serve", help="serve Retell custom-function requests on the loopback interface; read-only unless booking is approved")
    p.add_argument("--confirm-business-id", required=True)
    p.add_argument("--port", type=int, default=8787)
    for flag, text in BOOK_APPROVALS.items():
        p.add_argument("--approve-" + flag, action="store_true", help=text + " (all five together enable booking; none = read-only)")
    p = sub.add_parser("verify", help="READ-ONLY: find a previously booked slot by its reference and check it; creates nothing")
    p.add_argument("--start", required=True, help='local start of the recorded booking, e.g. "2026-10-12 11:00"')
    p.add_argument("--confirm-business-id", required=True)
    p.add_argument("--approve-tour-popups", action="store_true", help="allow closing product-tour popups that cover the page")
    p.add_argument("--approve-note-readback", action="store_true", help="allow opening appointment details (read-only) to read notes")
    p = sub.add_parser("book", help="create ONE real (fictional, ARIA TEST) appointment; every approval flag is required")
    p.add_argument("--start", required=True, help='local start, e.g. "2026-10-12 10:00"')
    p.add_argument("--confirm-business-id", required=True)
    for flag, text in BOOK_APPROVALS.items():
        p.add_argument("--approve-" + flag, action="store_true", help=text)
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
        missing = [f"--approve-{flag}" for flag in BOOK_APPROVALS if not getattr(args, "approve_" + flag.replace("-", "_"))]
        if missing:
            out("refused: a real booking needs every approval to be given explicitly; missing: " + ", ".join(missing))
            return EXIT_REFUSED
        args.approvals = frozenset(BOOK_APPROVALS)
        try:
            args.start_dt = datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except ValueError:
            out('refused: --start must look like "2026-10-12 10:00" (local time).')
            return EXIT_REFUSED

    if args.command == "serve":
        if not _truthy(env.get("ARIA_LIVE_BOOKSY")):
            out("refused: serving needs ARIA_LIVE_BOOKSY=1 in the environment.")
            return EXIT_REFUSED
        if not cfg.business_id or args.confirm_business_id != cfg.business_id:
            out("refused: --confirm-business-id must equal the configured ARIA_BOOKSY_BUSINESS_ID.")
            return EXIT_REFUSED
        key = (env.get("ARIA_RETELL_API_KEY") or "").strip()
        if not key:
            out("refused: ARIA_RETELL_API_KEY must be set in the environment (never on the command line, in chat or in the repo). "
                "Use a Retell API key that has the webhook badge.")
            return EXIT_REFUSED
        if not 1 <= args.port <= 65535:
            out("refused: --port must be between 1 and 65535.")
            return EXIT_REFUSED
        given = [flag for flag in BOOK_APPROVALS if getattr(args, "approve_" + flag.replace("-", "_"))]
        if given and len(given) != len(BOOK_APPROVALS):
            missing = [f"--approve-{flag}" for flag in BOOK_APPROVALS if flag not in given]
            out("refused: booking needs ALL five approvals (or none, for read-only). Missing: " + ", ".join(missing))
            return EXIT_REFUSED
        args.booking_enabled = len(given) == len(BOOK_APPROVALS)
        args.approvals = frozenset(BOOK_APPROVALS) if args.booking_enabled else frozenset()
        args.retell_key = key
        args.tool_token = (env.get("ARIA_RETELL_TOOL_TOKEN") or "").strip() or None

    if args.command == "verify":
        if not cfg.business_id or args.confirm_business_id != cfg.business_id:
            out("refused: --confirm-business-id must equal the configured ARIA_BOOKSY_BUSINESS_ID.")
            return EXIT_REFUSED
        if not (args.approve_tour_popups and args.approve_note_readback):
            out("refused: verify opens appointment details, so it needs --approve-tour-popups and --approve-note-readback")
            return EXIT_REFUSED
        try:
            args.start_dt = datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except ValueError:
            out('refused: --start must look like "2026-10-12 11:00" (local time).')
            return EXIT_REFUSED
        args.approvals = frozenset({"tour-popups", "note-readback"})

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

    approvals = getattr(args, "approvals", frozenset())
    driver = driver_factory(cfg) if driver_factory else SeleniumBooksyDriver(cfg, approvals=approvals)
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

    if args.command == "serve":
        return _serve(args, cfg, now_fn, driver, out)

    service = BookingService(cfg, driver, Ledger(cfg.ledger_path, clock=now_fn), clock=now_fn)

    if args.command == "find":
        for item in service.search(_parse_day(args.first, tz, now_fn()), args.days):
            out(f"{item.day}: {describe(item.search)}")
        return EXIT_OK

    if args.command == "verify":
        result = service.verify(args.start_dt)
    else:  # book
        result = service.book(args.start_dt)
    out(f"{result.status.value}: {result.message}")
    for detail in result.details:
        out(f"  - {detail}")
    if result.ref:
        out(f"  reference: {result.ref}")
    if result.status in (Status.UNCERTAIN_NEEDS_REVIEW, Status.VERIFY_MISMATCH, Status.BOOKED_CONFLICT_DETECTED):
        out("  The browser window may have been left open on purpose. Look at it and the ledger before doing anything else.")
    if result.ok:
        return EXIT_OK
    if result.status in (Status.UNCERTAIN_NEEDS_REVIEW, Status.VERIFY_MISMATCH, Status.BOOKED_CONFLICT_DETECTED):
        return EXIT_REVIEW
    if result.status is Status.NOT_SAVED:
        return EXIT_NOT_SAVED
    return EXIT_REFUSED


def _serve(args, cfg: Config, now_fn, driver, out) -> int:
    """Start the loopback endpoint. Blocks until interrupted. The signing key is never printed."""

    def service_factory() -> BookingService:
        return BookingService(cfg, driver, Ledger(cfg.ledger_path, clock=now_fn), clock=now_fn)

    tools = VoiceTools(cfg, driver, service_factory, now_fn, booking_enabled=args.booking_enabled)
    endpoint = retell_http.RetellEndpoint(
        tools, args.retell_key, bearer_token=args.tool_token,
        log=lambda message: out("  " + message),
    )
    server = retell_http.make_http_server(endpoint, args.port)
    out(f"serving Retell function calls on 127.0.0.1:{args.port} (loopback only). Mode: "
        + ("BOOKING ENABLED (a real, fictional ARIA TEST appointment can be created)" if args.booking_enabled else "READ-ONLY (booking refused)"))
    out(f"signatures: Retell's official v=<ms>,d=<hex> scheme only; extra bearer token: {'yes' if args.tool_token else 'no'}. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        out("stopped.")
    finally:
        server.server_close()
    return EXIT_OK
