"""The demo calendar's web answers. GET only; nothing it serves can change anything.

``CalendarSite`` is the whole of what the calendar says over HTTP (the page, ``/demo/health``, ``/demo/open-times``, ``/demo/changes``), as a plain function of (method, address,
the requesting page's origin). Two servers use it: ``make_site`` here (loopback only, for a person's own machine) and the public backend in ``aria_booking.hosting`` (the same
answers on a public address, next to the voice tools). One source, so the two can never disagree about what the calendar shows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterable, Optional
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from . import DEMO_BUSINESS_ID, DEMO_LABEL, SPA_TIMEZONE
from .dataset import DemoDataset
from .open_times import open_times
from .page import render_page

CALENDAR_PATH = f"/pro/en-us/{DEMO_BUSINESS_ID}/calendar"
OPEN_TIMES_PATH = "/demo/open-times"
CHANGES_PATH = "/demo/changes"
MAX_PARAM = 80
JSON = "application/json"
TEXT = "text/plain"


def calendar_url(port: int, day: str = "today") -> str:
    return f"http://127.0.0.1:{port}{CALENDAR_PATH}?date={day}&view=day&staffers=working"


def page_origins(page_port: int) -> tuple[str, ...]:
    """The addresses the prepared website is opened at ('localhost', never the numeric address, for the Retell key): the only origins allowed to read open times from here."""
    return (f"http://localhost:{page_port}", f"http://127.0.0.1:{page_port}")


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes = b""
    kind: str = "text/html; charset=utf-8"
    extra: dict = field(default_factory=dict)


class CalendarSite:
    """What the calendar answers. A browser page on one of ``allowed_origins`` may read the JSON (CORS) and may show the calendar page inside a frame; no other origin is told it may."""

    def __init__(self, dataset: DemoDataset, *, allowed_origins: Iterable[str] = (), clock: Optional[Callable[[], datetime]] = None):
        self.dataset = dataset
        self.origins = tuple(dict.fromkeys(allowed_origins))
        self.zone = ZoneInfo(SPA_TIMEZONE)
        self.now = clock or (lambda: datetime.now(self.zone))

    def cors(self, origin: Optional[str]) -> dict:
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} if origin and origin in self.origins else {"Vary": "Origin"}

    def respond(self, method: str, target: str, origin: Optional[str] = None) -> Optional[Reply]:
        """The reply for one request, or None when the address is not the calendar's (the caller decides what that means)."""
        parsed = urlparse(target)
        path = parsed.path
        calendar_paths = (OPEN_TIMES_PATH, CHANGES_PATH, "/demo/health", CALENDAR_PATH, "/", "")
        if method.upper() == "OPTIONS":  # a browser's permission question before a cross-origin read: yes only to the pages listed
            if path not in calendar_paths:
                return None
            allowed = bool(origin and origin in self.origins)
            return Reply(204, b"", TEXT, {**self.cors(origin), **({"Access-Control-Allow-Methods": "GET, OPTIONS", "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "600"} if allowed else {})})
        if method.upper() not in ("GET", "HEAD"):
            return Reply(405, b"read-only", TEXT)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if path == OPEN_TIMES_PATH:
            return self._open_times(query, origin)
        if path == CHANGES_PATH:  # has anything changed? a few bytes; lets the website refresh a view only when it must
            return Reply(200, json.dumps({"stamp": self.dataset.change_stamp(), "today": self.dataset.today.isoformat()}).encode("utf-8"), JSON, self.cors(origin))
        if path in ("/", ""):
            return Reply(302, b"", extra={"Location": f"{CALENDAR_PATH}?date=today&view=day&staffers=working"})
        if path == "/demo/health":
            body = json.dumps({"source": "demo_calendar", "label": DEMO_LABEL, "today": self.dataset.today.isoformat(),
                               "covers": [self.dataset.first_day.isoformat(), self.dataset.last_day.isoformat()]}).encode()
            return Reply(200, body, JSON, self.cors(origin))
        if path != CALENDAR_PATH:
            return None
        text = query.get("date", "today")
        try:
            day = self.dataset.today if text == "today" else date.fromisoformat(text)
        except ValueError:
            return Reply(400, b"the date must be YYYY-MM-DD or 'today'", TEXT)
        # only the pages listed may show this one inside a frame (the website's Staff calendar view); with none listed nothing is restricted, as before
        framing = {"Content-Security-Policy": "frame-ancestors 'self' " + " ".join(self.origins)} if self.origins else {}
        return Reply(200, render_page(self.dataset, day).encode("utf-8"), extra=framing)

    def _open_times(self, query: dict, origin: Optional[str]) -> Reply:
        service_id, text, staff = query.get("service_id", ""), query.get("date", ""), query.get("staff") or None
        if not service_id or len(service_id) > MAX_PARAM or (staff is not None and len(staff) > MAX_PARAM):
            return Reply(400, b'{"status": "error", "message": "service_id is required"}', JSON, self.cors(origin))
        try:
            day = date.fromisoformat(text)
        except ValueError:
            return Reply(400, b'{"status": "error", "message": "date must be YYYY-MM-DD"}', JSON, self.cors(origin))
        reply = open_times(self.dataset, service_id, day, tz=self.zone, now=self.now(), staff=staff)
        return Reply(200, json.dumps(reply).encode("utf-8"), JSON, self.cors(origin))


def make_site(
    dataset: DemoDataset, port: int, host: str = "127.0.0.1", *, allowed_origins: Iterable[str] = (), clock: Optional[Callable[[], datetime]] = None,
) -> ThreadingHTTPServer:
    """The calendar page and its read-only JSON answers on THIS machine only (the public backend is ``aria_booking.hosting``)."""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("the demo calendar binds to the loopback interface only")
    site = CalendarSite(dataset, allowed_origins=allowed_origins, clock=clock)

    class Handler(BaseHTTPRequestHandler):
        def _answer(self, reply: Reply, *, head: bool = False) -> None:
            self.send_response(reply.status)
            self.send_header("Content-Type", reply.kind)
            self.send_header("Content-Length", str(len(reply.body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Demo-Source", "demo_calendar")
            for key, value in reply.extra.items():
                self.send_header(key, value)
            self.end_headers()
            if not head:
                self.wfile.write(reply.body)

        def do_GET(self) -> None:  # noqa: N802
            self._answer(site.respond("GET", self.path, self.headers.get("Origin")) or Reply(404, b"not found", TEXT))

        def do_HEAD(self) -> None:  # noqa: N802
            self._answer(site.respond("GET", self.path, self.headers.get("Origin")) or Reply(404, b"not found", TEXT), head=True)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._answer(site.respond("OPTIONS", self.path, self.headers.get("Origin")) or Reply(404, b"not found", TEXT))

        def _refuse(self) -> None:
            try:  # read (and drop) whatever body was sent, so the refusal is delivered instead of the connection being reset
                self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 65536))
            except (ValueError, OSError):
                pass
            self._answer(Reply(405, b"read-only", TEXT))

        do_POST = do_PUT = do_PATCH = do_DELETE = _refuse

        def log_message(self, *args) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
