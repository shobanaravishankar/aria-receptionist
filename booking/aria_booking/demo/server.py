"""A loopback-only web server for the demo calendar page. GET only; nothing it serves can change anything."""

from __future__ import annotations

import json
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import DEMO_BUSINESS_ID, DEMO_LABEL
from .dataset import DemoDataset
from .page import render_page

CALENDAR_PATH = f"/pro/en-us/{DEMO_BUSINESS_ID}/calendar"


def calendar_url(port: int, day: str = "today") -> str:
    return f"http://127.0.0.1:{port}{CALENDAR_PATH}?date={day}&view=day&staffers=working"


def make_site(dataset: DemoDataset, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("the demo calendar binds to the loopback interface only")

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

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            if parsed.path in ("/", ""):
                return self._send(302, b"", extra={"Location": f"{CALENDAR_PATH}?date=today&view=day&staffers=working"})
            if parsed.path == "/demo/health":
                body = json.dumps({"source": "demo_calendar", "label": DEMO_LABEL, "today": dataset.today.isoformat(),
                                   "covers": [dataset.first_day.isoformat(), dataset.last_day.isoformat()]}).encode()
                return self._send(200, body, "application/json")
            if parsed.path != CALENDAR_PATH:
                return self._send(404, b"not found", "text/plain")
            text = query.get("date", "today")
            try:
                day = dataset.today if text == "today" else date.fromisoformat(text)
            except ValueError:
                return self._send(400, b"the date must be YYYY-MM-DD or 'today'", "text/plain")
            return self._send(200, render_page(dataset, day).encode("utf-8"))

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
