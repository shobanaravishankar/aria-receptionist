"""Retell's custom-function requests: authenticate, validate, route. No booking logic lives here.

SIGNATURE (resolved against Retell's OFFICIAL Python SDK, retell-python-sdk ``lib/webhook_auth.py``, read 2026-10-08, and
the "secure webhook" docs page, which tell servers to call ``retell.verify(raw_body, api_key=..., signature=...)``):
  * header ``X-Retell-Signature`` must fully match ``v=<unix ms>,d=<64 lowercase hex>``;
  * the digest is HMAC-SHA256 with the Retell API key over the RAW request body with the timestamp string appended
    directly (no separator); compared in constant time;
  * the timestamp must be within 5 minutes of now (either direction).
There is no legacy or plain-body fallback in the SDK, so there is none here: a request in any other form is rejected.
REMAINING UNCERTAINTY, stated plainly: the SDK/webhook page do not themselves say that custom-function calls are signed
with this same scheme (the custom-function pages only say "HMAC-SHA256 of the request body"). If a real call is rejected,
this fails CLOSED (401, nothing runs); the cure is to look at one real signed request, not to loosen the check. Replay:
within the 5-minute window a captured request could be sent again; every route is idempotent (tools.py) and the optional
bearer token is a second secret, so a replay cannot create a second booking.

Other facts from Retell's docs: the body is ``{name, call, args}``; any 2xx is success and other statuses may be retried
(max_retry 0-5, default 0), so domain outcomes (refusals, unknowns) are 200 with a ``status`` field and only
authentication/format problems are 4xx; Retell blocks localhost, so this server sits behind a tunnel that Shobana
starts herself and binds only to the loopback interface.
The signing key is passed in from the environment. It is never logged, stored, or echoed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Mapping, Optional

MAX_BODY_BYTES = 256 * 1024  # Retell includes the transcript so far in `call`; a long call must not be refused for size
TOLERANCE_MS = 5 * 60 * 1000
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
ROUTES = {
    "/tools/lookup_service": "lookup_service",
    "/tools/check_slot": "check_slot",
    "/tools/find_alternatives": "find_alternatives",
    "/tools/book_slot": "book_slot",
}
# The availability-only demo has NO writer route at all: a request to /tools/book_slot is a plain 404, not a refusal.
READ_ONLY_ROUTES = {path: fn for path, fn in ROUTES.items() if fn != "book_slot"}
SIGNATURE_RE = re.compile(r"v=(\d+),d=([0-9a-f]{64})")  # the official SDK's pattern, matched in full


def _hex(key: str, message: bytes) -> str:
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_signature(raw_body: bytes, header: Optional[str], api_key: str, *, now_ms: int) -> bool:
    """True only for a signature in the official ``v=<ms>,d=<hex>`` form that matches the raw body and is fresh.
    Never raises (a malformed or non-ASCII header is simply not a match)."""
    if not api_key or not header or not isinstance(header, str):
        return False
    match = SIGNATURE_RE.fullmatch(header)
    if match is None:
        return False
    timestamp, digest = match.group(1), match.group(2)
    if abs(now_ms - int(timestamp)) > TOLERANCE_MS:
        return False
    expected = _hex(api_key, raw_body + timestamp.encode("ascii"))
    return hmac.compare_digest(expected.encode("ascii"), digest.encode("ascii"))


class RetellEndpoint:
    def __init__(
        self,
        tools: Any,
        api_key: str,
        *,
        bearer_token: Optional[str] = None,
        routes: Optional[Mapping[str, str]] = None,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        log: Callable[[str], None] = lambda message: None,
    ):
        if not api_key:
            raise ValueError("a Retell API key is required to verify requests; refusing to run without one")
        self._tools, self._key, self._bearer, self._now, self._log = tools, api_key, bearer_token, now_ms, log
        self._routes = dict(ROUTES if routes is None else routes)

    def handle(self, method: str, path: str, headers: Mapping[str, str], raw_body: bytes) -> tuple[int, dict]:
        """(http status, JSON body). Order matters: nothing is parsed or executed before authentication."""
        lowered = {k.lower(): v for k, v in headers.items()}
        if method.upper() != "POST":
            return 405, {"status": "error", "ok": False, "speak": "", "error": "method_not_allowed"}
        route = self._routes.get(path.split("?", 1)[0])
        if route is None:
            return 404, {"status": "error", "ok": False, "speak": "", "error": "not_found"}
        if len(raw_body) > MAX_BODY_BYTES:
            return 413, {"status": "error", "ok": False, "speak": "", "error": "too_large"}
        if not verify_signature(raw_body, lowered.get("x-retell-signature"), self._key, now_ms=self._now()):
            self._log(f"rejected {route}: bad or missing signature")
            return 401, {"status": "error", "ok": False, "speak": "", "error": "unauthorized"}
        if self._bearer is not None:
            supplied = lowered.get("authorization", "")
            if not hmac.compare_digest(supplied.encode("utf-8"), f"Bearer {self._bearer}".encode("utf-8")):
                self._log(f"rejected {route}: bad or missing bearer token")
                return 401, {"status": "error", "ok": False, "speak": "", "error": "unauthorized"}
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, {"status": "error", "ok": False, "speak": "", "error": "bad_json"}
        if not isinstance(body, dict):
            return 400, {"status": "error", "ok": False, "speak": "", "error": "bad_shape"}
        name, call, args = body.get("name"), body.get("call"), body.get("args", {})
        if name is not None and name != route:
            return 400, {"status": "error", "ok": False, "speak": "", "error": "name_mismatch"}
        call_id = call.get("call_id") if isinstance(call, dict) else None
        if not isinstance(call_id, str) or not call_id.strip() or not isinstance(args, dict):
            return 400, {"status": "error", "ok": False, "speak": "", "error": "bad_shape"}
        try:
            result = getattr(self._tools, route)(call_id, args)
            json.dumps(result)  # must be serialisable
        except Exception as exc:  # never leak internals to the caller; the type name is enough for the operator log
            self._log(f"{route} failed: {type(exc).__name__}")
            return 200, {
                "status": "error", "ok": False,
                "speak": "I'm not able to check that right now, so I can't confirm anything. Please try again shortly or contact the salon directly.",
            }
        self._log(f"{route}: {result.get('status')}")
        return 200, result


def make_http_server(endpoint: RetellEndpoint, port: int, host: str = "127.0.0.1") -> HTTPServer:
    """A single-threaded server bound to the loopback interface only (requests are served one at a time, which also
    keeps the one dedicated browser from being driven twice at once)."""
    if host not in LOOPBACK:
        raise ValueError("the booking endpoint binds to the loopback interface only; expose it with a tunnel you control")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802 (http.server naming)
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._send(411, {"status": "error", "ok": False, "speak": "", "error": "length_required"})
            if length < 0 or length > MAX_BODY_BYTES:
                return self._send(413, {"status": "error", "ok": False, "speak": "", "error": "too_large"})
            raw = self.rfile.read(length)
            status, payload = endpoint.handle("POST", self.path, dict(self.headers.items()), raw)
            self._send(status, payload)

        def do_GET(self) -> None:  # noqa: N802
            self._send(405, {"status": "error", "ok": False, "speak": "", "error": "method_not_allowed"})

        do_PUT = do_PATCH = do_DELETE = do_GET

        def log_message(self, *args: Any) -> None:  # request lines can carry call ids; keep the console quiet
            return

    return HTTPServer((host, port), Handler)
