"""The public backend: ONE small server for the Serenity website, the calendar (page and read-only JSON) and the signed voice tools, on one https origin.

Why one origin: nothing is cross-origin, so there is no permission to get wrong, no mixed-content block, and no second address that can disappear (a tunnel that changes name
overnight). The same server can also be the backend of a website hosted elsewhere (the existing Netlify demo): it then allows exactly the websites listed in ARIA_SITE_ORIGINS to
read its JSON and to show its staff-calendar page in a frame, and no others.

What it never does: write anything except through the signed tool routes (Retell's signature and the bearer token are both checked before anything runs), send a message to anyone,
talk to Booksy, or put a secret, a phone number or a name in a log line, a health answer or a page.
"""

from __future__ import annotations

import json
import mimetypes
import signal
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from ..appointments.contact import contact_phrase
from ..appointments.dispatcher import AlertWorker, OutboxDispatcher
from ..appointments.launch import build_lifecycle_demo
from ..appointments.lifecycle import Lifecycle, open_lifecycle
from ..demo import DEMO_LABEL, SPA_TIMEZONE
from ..demo.server import CalendarSite, Reply
from ..voice.retell_http import MAX_BODY_BYTES
from .checks import as_json, readiness
from .config import PublicConfig

JSON = "application/json"
SITE_PATHS_NOT_FILES = ("/tools/", "/demo/", "/pro/", "/healthz", "/readyz")
SECURITY = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "strict-origin-when-cross-origin"}
TYPES = {".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".webp": "image/webp",
         ".woff2": "font/woff2", ".json": JSON, ".jpg": "image/jpeg", ".png": "image/png"}


@dataclass
class Upkeep:
    """What the daily upkeep has done, for /readyz (dates and counts only)."""

    date: str = ""
    error: str = ""


class PublicApp:
    def __init__(self, config: PublicConfig, lifecycle: Lifecycle, endpoint, *, site_dir: Optional[Path] = None, log: Callable[[str], None] = lambda message: None,
                 version: str = "", clock: Optional[Callable[[], datetime]] = None):
        self.config, self.lifecycle, self.endpoint, self.site_dir, self.log, self.version = config, lifecycle, endpoint, site_dir, log, version
        self.zone = ZoneInfo(SPA_TIMEZONE)
        self.clock = clock or lifecycle.clock
        self.calendar = CalendarSite(lifecycle.dataset, allowed_origins=config.allowed_origins, clock=self.clock)
        self.upkeep = Upkeep()
        self._upkeep_lock = threading.Lock()
        self.started = time.time()

    # ---- daily upkeep ------------------------------------------------------------------------------------
    def spa_today(self) -> str:
        return self.clock().astimezone(self.zone).date().isoformat()

    def maintain(self, *, force: bool = False) -> None:
        """Once per spa day (and at start): settle fictional visits whose time has passed and top up the fictional upcoming ones. Never raises: a failure is remembered for /readyz."""
        today = self.spa_today()
        if not force and self.upkeep.date == today:
            return
        with self._upkeep_lock:
            if not force and self.upkeep.date == today:
                return
            try:
                report = self.lifecycle.maintain()
                self.upkeep = Upkeep(today, "")
                self.log(f"upkeep for {today}: {len(report.upcoming_added)} upcoming fictional visit(s) made, {len(report.upcoming_skipped)} could not be placed")
            except Exception as exc:  # the server keeps answering; /readyz says upkeep did not run
                self.upkeep = Upkeep(self.upkeep.date, type(exc).__name__)
                self.log(f"upkeep failed ({type(exc).__name__})")

    # ---- one request -------------------------------------------------------------------------------------
    def respond(self, method: str, target: str, headers: Mapping[str, str], body: bytes = b"") -> Reply:
        lowered = {k.lower(): v for k, v in headers.items()}
        parsed = urlparse(target)
        path, verb = parsed.path, method.upper()
        if path == "/healthz":
            return self._secure(Reply(200, b'{"ok": true}', JSON, {"Cache-Control": "no-store"}))
        if path == "/readyz":
            return self._secure(self._ready())
        self.maintain()
        if path.startswith("/tools/"):
            status, payload = self.endpoint.handle(method, path, headers, body)
            return self._secure(Reply(status, json.dumps(payload).encode("utf-8"), JSON, {"Cache-Control": "no-store"}))
        if self.site_dir is not None and self.config.serve_site and verb in ("GET", "HEAD"):
            served = self._site_file(path)
            if served is not None:
                return self._secure(served, site=True)
        reply = self.calendar.respond(method, target, lowered.get("origin")) or Reply(404, b"not found", "text/plain")
        return self._secure(reply)

    def _secure(self, reply: Reply, *, site: bool = False) -> Reply:
        extra = {**SECURITY, **reply.extra}
        if site:
            extra.setdefault("Permissions-Policy", "microphone=(self)")
        return Reply(reply.status, reply.body, reply.kind, extra)

    def _ready(self) -> Reply:
        checks = readiness(self.config, self.lifecycle, site_dir=self.site_dir, maintenance={"date": self.upkeep.date}, now=self.clock())
        payload = as_json(checks, spa_date=self.spa_today(), version=self.version)
        payload["uptime_seconds"] = int(time.time() - self.started)
        payload["label"] = DEMO_LABEL
        return Reply(200 if payload["ok"] else 503, json.dumps(payload).encode("utf-8"), JSON, {"Cache-Control": "no-store"})

    def _site_file(self, path: str) -> Optional[Reply]:
        if path in ("/", "", "/index.html"):
            relative = "index.html"
        elif any(path.startswith(prefix) or path == prefix.rstrip("/") for prefix in SITE_PATHS_NOT_FILES):
            return None
        else:
            relative = path.lstrip("/")
        root = self.site_dir.resolve()
        candidate = (root / relative).resolve()
        if root not in candidate.parents and candidate != root:  # no way out of the site folder, whatever the address says
            return None
        if not candidate.is_file():
            return None
        kind = TYPES.get(candidate.suffix.lower()) or mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        cache = "no-cache" if candidate.suffix.lower() in (".html", ".js", ".css") else "public, max-age=300"
        return Reply(200, candidate.read_bytes(), kind, {"Cache-Control": cache})


def make_public_server(app: PublicApp, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, reply: Reply, *, head: bool = False, started: float = 0.0) -> None:
            # Logged BEFORE the answer goes out: the line exists as soon as anyone can have read the answer, and also when the caller has already gone away.
            app.log(f"{self.command} {urlparse(self.path).path} {reply.status} {int((time.time() - started) * 1000)}ms")
            try:
                self.send_response(reply.status)
                self.send_header("Content-Type", reply.kind)
                self.send_header("Content-Length", str(len(reply.body)))
                for key, value in reply.extra.items():
                    self.send_header(key, value)
                self.end_headers()
                if not head:
                    self.wfile.write(reply.body)
            except (ConnectionError, TimeoutError):  # the caller went away mid-answer: there is nobody to send the rest to
                self.close_connection = True

        def _body(self) -> Optional[bytes]:
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                self._send(Reply(411, b'{"error": "length_required"}', JSON))
                return None
            if length < 0 or length > MAX_BODY_BYTES:
                self._send(Reply(413, b'{"error": "too_large"}', JSON))
                return None
            return self.rfile.read(length) if length else b""

        def _handle(self, *, head: bool = False, with_body: bool = False) -> None:
            started = time.time()
            body = b""
            if with_body:
                read = self._body()
                if read is None:
                    return
                body = read
            try:
                reply = app.respond(self.command if not head else "GET", self.path, dict(self.headers.items()), body)
            except Exception as exc:  # never leak internals; the type name is enough for the operator
                app.log(f"{self.command} {urlparse(self.path).path} failed ({type(exc).__name__})")
                reply = Reply(500, b'{"error": "server_error"}', JSON)
            self._send(reply, head=head, started=started)

        def do_GET(self) -> None:  # noqa: N802
            self._handle()

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle(head=True)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._handle()

        def do_POST(self) -> None:  # noqa: N802
            self._handle(with_body=True)

        do_PUT = do_PATCH = do_DELETE = do_POST

        def log_message(self, *args) -> None:  # request lines carry call ids and queries: the app logs only method, path and status
            return

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address) -> None:
            """A connection that ends early (a caller closing a tab, a reset) is one plain log line, not a traceback on stderr; nothing about the caller or the request is recorded."""
            kind = sys.exc_info()[0]
            app.log("a connection ended early" if kind is not None and issubclass(kind, (ConnectionError, TimeoutError))
                    else f"a request failed ({kind.__name__ if kind is not None else 'unknown'})")

    return Server((host, port), Handler)


# ---------------------------------------------------------------- assembling and running


def build_app(config: PublicConfig, *, log: Callable[[str], None] = print, clock: Optional[Callable[[], datetime]] = None, site_dir: Optional[Path] = None,
              version: str = "", showcase=None):
    """(app, worker, cleanup). Opens the appointment file (creating and seeding it if it is new), builds the website, and wires the signed tool endpoint. No network is touched."""
    from ..showcase.config import resolve_showcase
    from ..showcase.site import build_site

    config.store_path.parent.mkdir(parents=True, exist_ok=True)
    real_now = clock
    lifecycle = open_lifecycle(config.store_path, today=None, real_now=real_now)
    showcase = showcase or resolve_showcase(path=config.showcase_file, lifecycle=True)
    contact = contact_phrase(showcase)
    dispatcher = OutboxDispatcher(lifecycle.store, None, None, log=lambda message: log("  " + message))  # no SMS provider and no owner number: alerts wait in the file, nothing is sent
    worker = AlertWorker(dispatcher, log=lambda message: log("  " + message))
    endpoint, _driver = build_lifecycle_demo(config.api_key, lifecycle, bearer_token=config.tool_token or None, log=lambda message: log("  " + message), contact=contact,
                                             on_committed=worker.trigger, preferences=config.preferences)
    cleanup_dir = None
    if config.serve_site:
        if site_dir is None:
            cleanup_dir = tempfile.TemporaryDirectory()
            site_dir = Path(cleanup_dir.name)
        build_site(site_dir, showcase=showcase, settings=config.voice_settings, origin=config.public_origin, avatar=config.avatar_image or None)
    app = PublicApp(config, lifecycle, endpoint, site_dir=site_dir if config.serve_site else None, log=log, version=version, clock=clock)
    app.maintain(force=True)
    return app, worker, cleanup_dir


def serve_public(config: PublicConfig, *, out: Callable[[str], None] = print, version: str = "") -> int:
    """Run until stopped (Ctrl+C, or SIGTERM from the host during a deploy). Returns an exit code."""
    app, worker, cleanup_dir = build_app(config, log=out, version=version)
    server = make_public_server(app, config.host, config.port)
    stop = threading.Event()

    def upkeep_loop() -> None:
        while not stop.wait(60):
            app.maintain()

    def on_term(*_args) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGTERM, on_term)
    except ValueError:  # not the main thread (tests): the caller stops the server itself
        pass
    counts = app.lifecycle.store.counts()
    out(f"{DEMO_LABEL.upper()}: SIMULATED appointment lifecycle on a public address. Invented clients and appointments; no real salon calendar, no Booksy, no text messages.")
    out(f"listening on {config.host}:{config.port}; public address {config.public_origin}; websites allowed: {', '.join(config.site_origins) or 'only this backend'}")
    out(f"appointments kept in {config.store_path}: {sum(counts['appointments'].values())} records, {counts['clients']} clients")
    out(f"spa date {app.spa_today()}; readiness: {config.public_origin}/readyz; tool address for Retell: {config.public_origin}/tools/<name>")
    worker.start()
    threading.Thread(target=upkeep_loop, name="daily-upkeep", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        out("stopped.")
    finally:
        stop.set()
        worker.stop()
        server.server_close()
        if cleanup_dir is not None:
            cleanup_dir.cleanup()
    return 0
