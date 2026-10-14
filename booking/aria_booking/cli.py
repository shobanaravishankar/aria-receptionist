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
from pathlib import Path
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
from .playwright_driver import PlaywrightBooksyDriver
from .selenium_driver import SeleniumBooksyDriver
from .voice import retell_http
from .voice.launch import build_endpoint
from .catalog.mapping_table import MappingTableError, load_mapping_table

EXIT_OK, EXIT_REFUSED, EXIT_REVIEW, EXIT_NOT_SAVED = 0, 2, 3, 4

DIAGNOSTIC_UNKNOWN = ("910010",)  # --diagnostic-incomplete-calendar: Delphine's hours are not shown
SERVE_LOAD_TIMEOUT = 10.0  # the voice server gives up on a day page sooner than the interactive commands do: a caller is on the line

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


def _add_browser_flag(parser) -> None:
    parser.add_argument(
        "--browser", choices=("playwright", "selenium"), default=None,
        help="the browser adapter: playwright (default; headful, read-only) or selenium (the original). Booking and discovery use selenium only.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aria_booking", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("config")
    p = sub.add_parser("login", help="open the dedicated browser window and wait for a PERSON to sign in; verifies the business")
    _add_browser_flag(p)
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
    _add_browser_flag(p)
    p = sub.add_parser("serve", help="serve Retell custom-function requests on the loopback interface; read-only unless booking is approved")
    p.add_argument("--confirm-business-id", required=True)
    p.add_argument("--port", type=int, default=8787)
    _add_browser_flag(p)
    for flag, text in BOOK_APPROVALS.items():
        p.add_argument("--approve-" + flag, action="store_true", help=text + " (all five together enable booking; none = read-only)")
    p = sub.add_parser("demo-serve", help="serve the DEMO calendar page and the read-only voice tools over invented data; never touches Booksy")
    p.add_argument("--port", type=int, default=8787, help="the voice tools' loopback port")
    p.add_argument("--site-port", type=int, default=8790, help="the demo calendar page's loopback port")
    p.add_argument("--today", default=None, help="the demo date, YYYY-MM-DD (default: the real today); 'tomorrow' means the day after it")
    p.add_argument("--via-page", action="store_true", help="answer by reading the demo PAGE in a Playwright window (the live code path) instead of the dataset")
    p.add_argument("--page-port", type=int, default=5174, help="the port the prepared website is opened at (default 5174): the only page allowed to read the calendar's open times")
    p.add_argument("--diagnostic-incomplete-calendar", action="store_true", help="DIAGNOSTIC ONLY: one technician's working hours are not shown on the calendar, to exercise the "
                                                                                 "'cannot confirm' path (unknown is never free). Off by default: the normal demo's schedules are complete.")
    p.add_argument("--lifecycle", action="store_true", help="the SIMULATED appointment lifecycle: find, book, move and cancel appointments in the prototype calendar (a local file, invented clients; "
                                                            "never Booksy). Without it the demo stays read-only.")
    p.add_argument("--store", default=None, help="with --lifecycle: the appointment file (default: .local/demo-appointments/appointments.sqlite3, git-ignored)")
    p = sub.add_parser("demo-agent", help="operate the demo's SEPARATE Retell test agent and voice page (plan, create once, verify, preflight, prepare-web); never places a call")
    p.add_argument("action", choices=("plan", "store-secret", "create", "update", "verify", "preflight", "prepare-web", "call-summary"))
    p.add_argument("--config", default=None, help="a JSON file of non-secret settings kept outside Git (see demo_agent.example.json); or set ARIA_DEMO_CONFIG")
    p.add_argument("--tunnel-url", default=None, help="the public https address of YOUR tunnel to the tool port (or ARIA_DEMO_TUNNEL_URL)")
    p.add_argument("--state-dir", default=None, help="where created ids, markers and results are kept (default: .local/demo-agent, git-ignored)")
    p.add_argument("--web-dir", default=None, help="where prepare-web writes the page (default: <state dir>/web)")
    p.add_argument("--calendar-port", type=int, default=None, help="the demo calendar page's port, for the link in the page banner (default 8790)")
    p.add_argument("--page-port", type=int, default=None, help="the port the prepared voice page is served on; it is opened at http://localhost:<port>/ (default 5174)")
    p.add_argument("--mode", choices=("availability", "lifecycle"), default=None, help="which reviewed configuration the agent is held to: the read-only availability demo (default) or the "
                                                                                       "simulated appointment lifecycle")
    p.add_argument("--confirm-create", action="store_true", help="required by `create`, which makes one Retell LLM and one agent")
    p.add_argument("--confirm-update", action="store_true", help="required by `update`, which sends the reviewed prompt, tools and opening line to the EXISTING LLM (no new agent)")
    p.add_argument("--secret-name", default="retell_api_key", help="for store-secret: retell_api_key, tool_token or retell_public_key")
    p.add_argument("--base-url", default=None, help="for preflight: check this loopback or https address instead of the tunnel, e.g. http://127.0.0.1:8787")
    p = sub.add_parser("demo-day", help="print who works and what is booked on one demo day (invented data), so a demo can ask grounded questions")
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: the day after the demo date)")
    p.add_argument("--today", default=None, help="the demo date, YYYY-MM-DD (default: the real today)")
    p = sub.add_parser("demo-screenshots", help="photograph the demo calendar page at the reference viewports (PNG files); invented data, loopback only")
    p.add_argument("--out", required=True, help="the folder for the PNG files")
    p.add_argument("--today", default=None, help="the demo date, YYYY-MM-DD (default: the real today)")
    p = sub.add_parser("showcase-preview", help="build the Serenity page and photograph it at desktop and phone widths (JPEG files); loopback only, no call, no network")
    p.add_argument("--out", default="showcase_evidence", help="the folder for the JPEG files (default: showcase_evidence)")
    p = sub.add_parser("demo-compare", help="diff the demo's data-independent regions (sidebar, toolbar, hour axis) against a user-supplied screenshot of the real day view")
    p.add_argument("--reference", required=True, help="a PNG screenshot of the real day view; its size sets the viewport")
    p.add_argument("--today", default=None, help="the demo date, YYYY-MM-DD (default: the real today)")
    p = sub.add_parser("mapping-check", help="validate the reviewed services/eligibility table offline (no browser, no network)")
    p.add_argument("path", nargs="?", help="the table file; defaults to ARIA_MAPPING_TABLE")
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

    if args.command == "mapping-check":
        return _mapping_check(args, cfg, now_fn, out)

    if args.command == "demo-agent":
        from .demo.agent_ops import run as run_demo_agent

        return run_demo_agent(
            args.action, env, out, config_file=args.config, confirm_create=args.confirm_create, confirm_update=args.confirm_update,
            secret_name=args.secret_name, base_url=args.base_url,
            overrides={"tunnel_url": args.tunnel_url, "state_dir": args.state_dir, "web_dir": args.web_dir,
                       "calendar_port": args.calendar_port, "page_port": args.page_port, "mode": args.mode},
        )

    if args.command in ("demo-screenshots", "demo-compare", "demo-day"):
        return _demo_visual(args, cfg, out)

    if args.command == "showcase-preview":
        from pathlib import Path

        from .showcase.site.preview import capture as capture_showcase

        try:
            for path in capture_showcase(Path(args.out), channel=cfg.browser_channel):
                out(f"wrote {path}")
        except RuntimeError as exc:
            out(f"stopped: {exc}")
            return EXIT_REFUSED
        return EXIT_OK

    if args.command == "demo-serve":  # decided before anything else: this command never builds a Booksy driver, and `serve` never builds the demo
        return _demo_serve(args, cfg, env, out)

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
        args.registry = None  # None: only the verified test service is checkable/bookable
        if cfg.mapping_table is not None:
            try:
                args.registry, report = load_mapping_table(cfg.mapping_table, now=now_fn())
            except MappingTableError as exc:
                out(f"refused: the mapping table cannot be used ({exc}). Fix it or unset ARIA_MAPPING_TABLE.")
                return EXIT_REFUSED
            out("mapping table: " + report.summary())

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
    browser = _browser_for(args)
    if browser == "refused":
        out("refused: booking is not available on the Playwright adapter (it is read-only). Booking and discovery use --browser selenium.")
        return EXIT_REFUSED
    if driver_factory:
        driver = driver_factory(cfg)
    elif browser == "playwright":
        driver = PlaywrightBooksyDriver(cfg, notify=out, load_timeout_seconds=SERVE_LOAD_TIMEOUT if args.command == "serve" else 40.0)
    else:
        driver = SeleniumBooksyDriver(cfg, approvals=approvals, load_timeout_seconds=SERVE_LOAD_TIMEOUT if args.command == "serve" else 40.0)
    try:
        return _run(args, cfg, tz, now_fn, driver, out)
    except (ConfigError, DriverError, LedgerError) as exc:
        out(f"stopped: {exc}")
        return EXIT_REFUSED
    finally:
        driver.close()


def _browser_for(args) -> str:
    """Which adapter runs this command. The read-only commands (login, find, serve without booking) default to Playwright; everything that
    clicks or discovers (discover, discover-steps, rehearse, verify, book, and serve WITH booking) stays on Selenium. 'refused' = the user
    explicitly asked for the read-only adapter on a booking server."""
    chosen = getattr(args, "browser", None)
    if args.command == "serve" and getattr(args, "booking_enabled", False):
        return "refused" if chosen == "playwright" else "selenium"
    if args.command in ("login", "find", "serve"):
        return chosen or "playwright"
    return "selenium"


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

    def service_factory(service_cfg=None) -> BookingService:
        # the per-request configuration comes from the bookable allowlist (service, duration, technician), never from a caller
        return BookingService(service_cfg or cfg, driver, Ledger(cfg.ledger_path, clock=now_fn), clock=now_fn)

    # no five approvals = the availability-only demo: tools and routes are built together and cannot write
    endpoint = build_endpoint(
        cfg, driver, service_factory, now_fn, args.retell_key,
        booking_approved=args.booking_enabled, bearer_token=args.tool_token, registry=args.registry, log=lambda message: out("  " + message),
    )
    server = retell_http.make_http_server(endpoint, args.port)
    out(f"serving Retell function calls on 127.0.0.1:{args.port} (loopback only). Mode: "
        + ("BOOKING ENABLED (a real, fictional ARIA TEST appointment can be created)" if args.booking_enabled
           else "READ-ONLY (availability only: there is no booking route, and booking is refused)"))
    out(f"signatures: Retell's official v=<ms>,d=<hex> scheme only; extra bearer token: {'yes' if args.tool_token else 'no'}. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        out("stopped.")
    finally:
        server.server_close()
    return EXIT_OK


def _demo_serve(args, cfg: Config, env, out) -> int:
    """The demo: an invented calendar page plus the read-only voice tools over the SAME dataset. No Booksy, no booking route, no fall-back."""
    import threading

    from .demo import DEMO_BUSINESS_ID, DEMO_LABEL, SPA_TIMEZONE
    from .demo.launch import build_demo, demo_clock
    from .demo.dataset import DemoDataset
    from .demo.server import calendar_url, make_site, page_origins

    from .demo.secret_store import serve_credentials

    key, tool_token = serve_credentials(env)  # environment variables, or the encrypted-file folder ARIA_SECRET_DIR (the same source `demo-agent` uses)
    if not key:
        out("refused: ARIA_RETELL_API_KEY must be set in the environment (never on the command line, in chat or in the repo), or stored with `demo-agent store-secret` under ARIA_SECRET_DIR.")
        return EXIT_REFUSED
    if not (1 <= args.port <= 65535 and 1 <= args.site_port <= 65535 and args.port != args.site_port):
        out("refused: --port and --site-port must be different numbers between 1 and 65535.")
        return EXIT_REFUSED
    try:
        today = date.fromisoformat(args.today) if args.today else None
    except ValueError:
        out("refused: --today must look like 2026-10-14.")
        return EXIT_REFUSED
    if args.lifecycle:
        return _demo_serve_lifecycle(args, cfg, key, tool_token, today, out)
    tz = ZoneInfo(SPA_TIMEZONE)  # the demo's one timezone: the same zone the greeting and the session clock use
    clock = demo_clock(today, tz)
    dataset = DemoDataset(clock().date(), diagnostic_unknown_hours=DIAGNOSTIC_UNKNOWN if args.diagnostic_incomplete_calendar else ())
    if today is not None and today != datetime.now(tz).date():
        out(f"WARNING: --today {today.isoformat()} is not the real date. A Retell agent's own clock is the real date, so for a live voice test leave --today off; "
            "otherwise 'tomorrow' will mean different days to the agent and to the calendar.")
    site = make_site(dataset, args.site_port, allowed_origins=page_origins(args.page_port), clock=clock)
    threading.Thread(target=site.serve_forever, name="demo-site", daemon=True).start()
    driver = None
    if args.via_page:  # the same reader the live calendar uses, pointed at the demo page; if it cannot start, the command stops (no fall-back to the dataset)
        from dataclasses import replace

        driver = PlaywrightBooksyDriver(
            replace(cfg, business_id=DEMO_BUSINESS_ID, timezone=SPA_TIMEZONE, local_dir=cfg.local_dir / "demo"), notify=out, load_timeout_seconds=SERVE_LOAD_TIMEOUT,
            url_for_day=lambda day: calendar_url(args.site_port, day),
        )
    try:
        if driver is not None:
            if driver.verify_business() != DEMO_BUSINESS_ID:
                out("refused: the demo page did not identify itself as the demo business.")
                return EXIT_REFUSED
        endpoint, driver, dataset = build_demo(
            key, today=today, timezone=SPA_TIMEZONE, bearer_token=tool_token or None,
            log=lambda message: out("  " + message), driver=driver, dataset=dataset,
        )
        server = retell_http.make_http_server(endpoint, args.port)
    except (DriverError, OSError) as exc:
        site.shutdown()
        site.server_close()
        if driver is not None:
            driver.close()
        out(f"stopped: {exc}")
        return EXIT_REFUSED
    out(f"{DEMO_LABEL.upper()}: invented data only; this is NOT Booksy and nothing here can book, hold, change or cancel anything.")
    out(f"calendar page (open in a browser): {calendar_url(args.site_port)}")
    out(f"voice tools: 127.0.0.1:{args.port} (loopback only; expose it with a tunnel you control). Reads come from: "
        + ("the demo PAGE through a Playwright window" if args.via_page else "the demo dataset directly"))
    first, second = dataset.scripted_days
    out(f"demo date {dataset.today.isoformat()}; scripted days {first.isoformat()} and {second.isoformat()}; covered {dataset.first_day.isoformat()} to {dataset.last_day.isoformat()}")
    out("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        out("stopped.")
    finally:
        server.server_close()
        site.shutdown()
        site.server_close()
        driver.close()
    return EXIT_OK


def _demo_serve_lifecycle(args, cfg: Config, key: str, tool_token: str, today, out) -> int:
    """The simulated appointment lifecycle: the same calendar page and signed voice endpoint, over ONE local appointment file, with find / book / move / cancel tools. There is no
    Booksy driver and no Booksy route. Owner alerts wait in the file's outbox until an owner number AND an SMS provider exist (neither does yet); nothing is ever sent to a client."""
    import threading

    from .appointments.contact import contact_phrase
    from .appointments.dispatcher import AlertWorker, OutboxDispatcher
    from .appointments.launch import build_lifecycle_demo
    from .appointments.lifecycle import default_store_path, open_lifecycle
    from .appointments.sender import OwnerDestinationError, load_owner_destination
    from .appointments.store import StoreError
    from .demo import DEMO_BUSINESS_ID, DEMO_LABEL, SPA_TIMEZONE
    from .demo.server import calendar_url, make_site, page_origins
    from .showcase import SHOWCASE

    try:
        destination = load_owner_destination(cfg.local_dir)
        contact = contact_phrase(SHOWCASE)
        lifecycle = open_lifecycle(Path(args.store) if args.store else default_store_path(cfg.local_dir), today=today,
                                   diagnostic_unknown_hours=DIAGNOSTIC_UNKNOWN if args.diagnostic_incomplete_calendar else ())
    except (OwnerDestinationError, ValueError, StoreError) as exc:
        out(f"refused: {exc}")
        return EXIT_REFUSED
    if today is not None and today != datetime.now(lifecycle.tz).date():
        out(f"WARNING: --today {today.isoformat()} is not the real date. A Retell agent's own clock is the real date, so for a live voice test leave --today off; "
            "otherwise 'tomorrow' will mean different days to the agent and to the calendar.")
    site = make_site(lifecycle.dataset, args.site_port, allowed_origins=page_origins(args.page_port), clock=lifecycle.clock)
    threading.Thread(target=site.serve_forever, name="demo-site", daemon=True).start()
    driver = None
    if args.via_page:
        from dataclasses import replace

        driver = PlaywrightBooksyDriver(
            replace(cfg, business_id=DEMO_BUSINESS_ID, timezone=SPA_TIMEZONE, local_dir=cfg.local_dir / "demo"), notify=out, load_timeout_seconds=SERVE_LOAD_TIMEOUT,
            url_for_day=lambda day: calendar_url(args.site_port, day),
        )
    dispatcher = OutboxDispatcher(lifecycle.store, None, destination, log=lambda message: out("  " + message))  # no provider adapter exists: nothing can be sent
    worker = AlertWorker(dispatcher, log=lambda message: out("  " + message))
    try:
        if driver is not None and driver.verify_business() != DEMO_BUSINESS_ID:
            out("refused: the demo page did not identify itself as the demo business.")
            return EXIT_REFUSED
        endpoint, driver = build_lifecycle_demo(
            key, lifecycle, bearer_token=tool_token or None, log=lambda message: out("  " + message), driver=driver, contact=contact, on_committed=worker.trigger,
        )
        server = retell_http.make_http_server(endpoint, args.port)
    except (DriverError, OSError) as exc:
        site.shutdown()
        site.server_close()
        if driver is not None:
            driver.close()
        out(f"stopped: {exc}")
        return EXIT_REFUSED
    counts = lifecycle.store.counts()
    out(f"{DEMO_LABEL.upper()}: SIMULATED APPOINTMENT LIFECYCLE. Invented clients and appointments in a local file; this is NOT Booksy and no real salon calendar can be reached or changed.")
    out(f"calendar page (open in a browser): {calendar_url(args.site_port)}")
    out(f"voice tools: 127.0.0.1:{args.port} (loopback only; expose it with a tunnel you control). Appointments are kept in {lifecycle.store.path}")
    out(f"appointments: {sum(counts['appointments'].values())} records (seeded: {len(lifecycle.seeded.upcoming_added)} upcoming and {lifecycle.seeded.history_added} past made now; "
        f"{len(lifecycle.seeded.upcoming_skipped)} could not be placed)")
    waiting = counts["outbox"].get("pending", 0)
    out("owner alerts: " + ("NOT sent: no SMS provider is set up yet" + (" (an owner number is configured)" if destination else " and no owner number is configured") + f"; {waiting} alert(s) waiting in the outbox"))
    out("contact fallback: " + ("the configured salon name and number" if contact else "no salon number is configured, so Aria says to contact the salon and gives none"))
    first, second = lifecycle.dataset.scripted_days
    out(f"demo date {lifecycle.dataset.today.isoformat()}; scripted days {first.isoformat()} and {second.isoformat()}; covered {lifecycle.dataset.first_day.isoformat()} to {lifecycle.dataset.last_day.isoformat()}")
    out("Press Ctrl+C to stop.")
    worker.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        out("stopped.")
    finally:
        worker.stop()
        server.server_close()
        site.shutdown()
        site.server_close()
        driver.close()
    return EXIT_OK


def _demo_visual(args, cfg: Config, out) -> int:
    """Screenshots of, and screenshot comparison for, the demo page. Standalone: no tools, no Booksy, no booking."""
    import tempfile
    from pathlib import Path

    from .demo import SPA_TIMEZONE
    from .demo.dataset import DemoDataset
    from .demo.launch import demo_clock
    from .demo.screenshots import capture
    from .demo.visual import BANNER_PX, compare_to_reference, read_png

    try:
        today = date.fromisoformat(args.today) if args.today else None
    except ValueError:
        out("refused: --today must look like 2026-10-14.")
        return EXIT_REFUSED
    dataset = DemoDataset(demo_clock(today, ZoneInfo(SPA_TIMEZONE))().date())
    if args.command == "demo-day":
        from .demo.dataset import NotCovered

        try:
            day = date.fromisoformat(args.date) if args.date else dataset.scripted_days[0]
            for line in dataset.describe_day(day):
                out(line)
        except ValueError:
            out("refused: --date must look like 2026-10-14.")
            return EXIT_REFUSED
        except NotCovered as exc:
            out(f"refused: {exc}")
            return EXIT_REFUSED
        return EXIT_OK
    try:
        if args.command == "demo-screenshots":
            for path in capture(dataset, Path(args.out), channel=cfg.browser_channel):
                out(f"wrote {path}")
            return EXIT_OK
        reference = read_png(args.reference)
        with tempfile.TemporaryDirectory() as folder:
            (rendered,) = capture(dataset, Path(folder), viewports=((reference.width, reference.height + BANNER_PX),), channel=cfg.browser_channel,
                                  scroll_afternoon=False)
            diffs = compare_to_reference(reference, read_png(rendered))
    except (DriverError, OSError, ValueError) as exc:
        out(f"stopped: {exc}")
        return EXIT_REFUSED
    out("difference from the reference in the regions that do not depend on data (0 = identical; the demo's colours, fonts and icons are approximations):")
    for d in diffs:
        out(f"  {d.name:10} mean difference {d.mean_abs_diff:6.2f} / 255, {d.differing_ratio * 100:5.1f}% of pixels clearly different")
    return EXIT_OK


def _mapping_check(args, cfg: Config, now_fn, out) -> int:
    """Offline validation of the reviewed table: 0 = loaded, fresh, with verified rows; 3 = loaded but stale or nothing usable; 2 = malformed."""
    path = args.path or (str(cfg.mapping_table) if cfg.mapping_table else None)
    if not path:
        out("refused: give the table path, or set ARIA_MAPPING_TABLE.")
        return EXIT_REFUSED
    try:
        registry, report = load_mapping_table(path, now=now_fn())
    except MappingTableError as exc:
        out(f"invalid: {exc}")
        return EXIT_REFUSED
    out(report.summary())
    for line in report.unverified:
        out(f"  not usable: {line}")
    return EXIT_REVIEW if (report.stale or report.verified == 0) else EXIT_OK
