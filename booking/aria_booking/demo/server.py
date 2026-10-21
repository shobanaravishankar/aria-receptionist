"""A loopback-only web server for the demo calendar page. GET only; nothing it serves can change anything."""

from __future__ import annotations

import json
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


def calendar_url(port: int, day: str = "today") -> str:
    return f"http://127.0.0.1:{port}{CALENDAR_PATH}?date={day}&view=day&staffers=working"


def page_origins(page_port: int) -> tuple[str, ...]:
    """The addresses the prepared website is opened at ('localhost', never the numeric address, for the Retell key): the only origins allowed to read open times from here."""
    return (f"http://localhost:{page_port}", f"http://127.0.0.1:{page_port}")


def make_site(
    dataset: DemoDataset, port: int, host: str = "127.0.0.1", *, allowed_origins: Iterable[str] = (), clock: Optional[Callable[[], datetime]] = None,
) -> ThreadingHTTPServer:
    """The calendar page, and three small READ-ONLY JSON answers for the website: ``/demo/health``, ``/demo/open-times`` and ``/demo/changes``. A browser page on one of ``allowed_origins`` may read them
    (CORS); no other origin is told it may. Nothing here writes, and the open times are derived from the same calendar the voice reads (see open_times.py)."""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("the demo calendar binds to the loopback interface only")
    origins = frozenset(allowed_origins)
    zone = ZoneInfo(SPA_TIMEZONE)
    now = clock or (lambda: datetime.now(zone))

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, kind: str = "text/html; charset=utf-8", extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Demo-Source", "demo_calendar")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _cors(self) -> dict:
            origin = self.headers.get("Origin")
            return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} if origin in origins else {"Vary": "Origin"}

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            if parsed.path == OPEN_TIMES_PATH:
                return self._open_times(query)
            if parsed.path == CHANGES_PATH:  # has anything changed? a few bytes; lets the website refresh a view only when it must
                return self._send(200, json.dumps({"stamp": dataset.change_stamp(), "today": dataset.today.isoformat()}).encode("utf-8"), "application/json", self._cors())
            if parsed.path in ("/", ""):
                return self._send(302, b"", extra={"Location": f"{CALENDAR_PATH}?date=today&view=day&staffers=working"})
            if parsed.path == "/demo/health":
                body = json.dumps({"source": "demo_calendar", "label": DEMO_LABEL, "today": dataset.today.isoformat(),
                                   "covers": [dataset.first_day.isoformat(), dataset.last_day.isoformat()]}).encode()
                return self._send(200, body, "application/json", self._cors())
            if parsed.path != CALENDAR_PATH:
                return self._send(404, b"not found", "text/plain")
            text = query.get("date", "today")
            try:
                day = dataset.today if text == "today" else date.fromisoformat(text)
            except ValueError:
                return self._send(400, b"the date must be YYYY-MM-DD or 'today'", "text/plain")
            return self._send(200, render_page(dataset, day).encode("utf-8"))

        def _open_times(self, query: dict) -> None:
            service_id, text, staff = query.get("service_id", ""), query.get("date", ""), query.get("staff") or None
            if not service_id or len(service_id) > MAX_PARAM or (staff is not None and len(staff) > MAX_PARAM):
                return self._send(400, b'{"status": "error", "message": "service_id is required"}', "application/json", self._cors())
            try:
                day = date.fromisoformat(text)
            except ValueError:
                return self._send(400, b'{"status": "error", "message": "date must be YYYY-MM-DD"}', "application/json", self._cors())
            reply = open_times(dataset, service_id, day, tz=zone, now=now(), staff=staff)
            return self._send(200, json.dumps(reply).encode("utf-8"), "application/json", self._cors())

        def _refuse(self) -> None:
            try:  # read (and drop) whatever body was sent, so the refusal is delivered instead of the connection being reset
                self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 65536))
            except (ValueError, OSError):
                pass
            self._send(405, b"read-only", "text/plain")

        do_POST = do_PUT = do_PATCH = do_DELETE = _refuse

        def log_message(self, *args) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
