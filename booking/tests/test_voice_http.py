"""Authentication, validation and routing for Retell's requests, a real loopback round-trip, and the `serve` gating.

No Retell account, no key of value and no browser are used: the signing key here is a made-up test string.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import urllib.error
import urllib.request

import pytest

from aria_booking import cli
from aria_booking.voice import retell_http
from aria_booking.voice.retell_http import MAX_BODY_BYTES, RetellEndpoint, make_http_server, verify_signature

from conftest import NOW, TZ

KEY = "test-key-not-a-real-secret"
NOW_MS = 1_790_000_000_000


def legacy_plain(body: bytes, key=KEY) -> str:
    """A bare HMAC of the body: NOT Retell's scheme. Used only to prove it is rejected."""
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def stamped(body: bytes, key=KEY, ts=NOW_MS) -> str:
    """Retell's official form (retell-python-sdk lib/webhook_auth.py): v=<ms>,d=<hmac-sha256(key, body + str(ms))>."""
    return f"v={ts},d=" + hmac.new(key.encode(), body + str(ts).encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- signature verification (the official scheme only)


def test_the_official_signature_form_verifies():
    body = b'{"args":{}}'
    assert verify_signature(body, stamped(body), KEY, now_ms=NOW_MS)


def test_a_known_answer_pins_the_exact_construction():
    """HMAC-SHA256(key, body || decimal timestamp), hex, header v=<ts>,d=<hex>. Computed independently of the code under test."""
    import binascii

    key, body, ts = "k", b"{}", 1_700_000_000_000
    inner = bytes(b ^ 0x36 for b in key.encode().ljust(64, b"\0"))
    outer = bytes(b ^ 0x5C for b in key.encode().ljust(64, b"\0"))
    expected = hashlib.sha256(outer + hashlib.sha256(inner + body + str(ts).encode()).digest()).hexdigest()
    assert binascii.unhexlify(expected) and verify_signature(body, f"v={ts},d={expected}", key, now_ms=ts)


def test_the_bare_body_hmac_is_not_accepted():
    """There is no plain-body fallback in Retell's official SDK, so there is none here."""
    body = b'{"args":{}}'
    assert not verify_signature(body, legacy_plain(body), KEY, now_ms=NOW_MS)


def test_the_header_must_match_the_official_pattern_in_full():
    body = b"x"
    good = stamped(body)
    assert not verify_signature(body, good.upper(), KEY, now_ms=NOW_MS), "the SDK accepts lowercase hex only"
    assert not verify_signature(body, " " + good, KEY, now_ms=NOW_MS)
    assert not verify_signature(body, good + " ", KEY, now_ms=NOW_MS)
    assert not verify_signature(body, good + ",extra=1", KEY, now_ms=NOW_MS)
    assert not verify_signature(body, "prefix" + good, KEY, now_ms=NOW_MS)
    assert not verify_signature(body, good[:-1], KEY, now_ms=NOW_MS), "a 63-character digest"
    assert not verify_signature(body, good + "0", KEY, now_ms=NOW_MS), "a 65-character digest"


def test_a_tampered_body_or_wrong_key_fails():
    body = b'{"args":{"a":1}}'
    sig = stamped(body)
    assert not verify_signature(body + b" ", sig, KEY, now_ms=NOW_MS)
    assert not verify_signature(body, sig, KEY + "x", now_ms=NOW_MS)
    assert not verify_signature(body, stamped(body, key="other"), KEY, now_ms=NOW_MS)


def test_the_timestamp_is_part_of_what_is_signed_and_must_be_fresh():
    body = b"{}"
    assert verify_signature(body, stamped(body, ts=NOW_MS - 299_000), KEY, now_ms=NOW_MS)
    assert verify_signature(body, stamped(body, ts=NOW_MS + 299_000), KEY, now_ms=NOW_MS)
    assert not verify_signature(body, stamped(body, ts=NOW_MS - 301_000), KEY, now_ms=NOW_MS)
    assert not verify_signature(body, stamped(body, ts=NOW_MS + 301_000), KEY, now_ms=NOW_MS)
    digest = stamped(body).split("d=")[1]
    assert not verify_signature(body, f"v={NOW_MS + 1},d={digest}", KEY, now_ms=NOW_MS), "a timestamp cannot be swapped"


def test_the_body_is_verified_as_the_raw_bytes_not_as_re_serialised_json():
    raw = b'{ "args" :  {"b":1,  "a":2}}'
    signature = stamped(raw)
    assert verify_signature(raw, signature, KEY, now_ms=NOW_MS)
    reserialised = json.dumps(json.loads(raw)).encode()
    assert reserialised != raw and not verify_signature(reserialised, signature, KEY, now_ms=NOW_MS)


@pytest.mark.parametrize("header", [None, "", "   ", "v=", "v=abc,d=", "v=1,d=zz", "d=abc", ",,,", "v=123", "\x00", "é" * 10, "v=1,d=" + "g" * 64, 5, b"v=1"])
def test_garbage_headers_fail_without_raising(header):
    assert verify_signature(b"{}", header, KEY, now_ms=NOW_MS) is False


def test_an_empty_key_never_verifies():
    assert not verify_signature(b"{}", stamped(b"{}", key=""), "", now_ms=NOW_MS)


# ---------------------------------------------------------------- the endpoint


class SpyTools:
    def __init__(self):
        self.calls = []

    def _record(self, name, call_id, args):
        self.calls.append((name, call_id, args))
        return {"status": "available", "ok": False, "speak": f"{name} done"}

    def check_slot(self, call_id, args):
        return self._record("check_slot", call_id, args)

    def find_alternatives(self, call_id, args):
        return self._record("find_alternatives", call_id, args)

    def book_slot(self, call_id, args):
        return self._record("book_slot", call_id, args)


def make_endpoint(tools=None, **kwargs):
    log = []
    kwargs.setdefault("now_ms", lambda: NOW_MS)
    endpoint = RetellEndpoint(tools or SpyTools(), KEY, log=log.append, **kwargs)
    endpoint.log_lines = log
    return endpoint


def payload(name="check_slot", call_id="call_1", args=None, **extra):
    return json.dumps({"name": name, "call": {"call_id": call_id}, "args": args if args is not None else {"date": "2026-10-12"}, **extra}).encode()


def post(endpoint, route="check_slot", body=None, sign=stamped, headers=None):
    body = payload(route) if body is None else body
    hdrs = {"X-Retell-Signature": sign(body)} if sign else {}
    hdrs.update(headers or {})
    return endpoint.handle("POST", f"/tools/{route}", hdrs, body)


def test_a_valid_signed_request_reaches_the_tool_with_the_call_id_and_args():
    tools = SpyTools()
    status, body = post(make_endpoint(tools), body=payload(args={"date": "2026-10-12", "time": "11:00"}, call_id="abc"))
    assert status == 200 and body["speak"] == "check_slot done"
    assert tools.calls == [("check_slot", "abc", {"date": "2026-10-12", "time": "11:00"})]


@pytest.mark.parametrize("route", ["check_slot", "find_alternatives", "book_slot"])
def test_each_route_reaches_its_own_function(route):
    tools = SpyTools()
    assert post(make_endpoint(tools), route=route, body=payload(name=route))[0] == 200
    assert [c[0] for c in tools.calls] == [route]


def test_the_header_name_is_case_insensitive():
    body = payload()
    status, _ = make_endpoint().handle("POST", "/tools/check_slot", {"x-retell-signature": stamped(body)}, body)
    assert status == 200


@pytest.mark.parametrize("sign", [None, lambda b: "0" * 64, lambda b: stamped(b, key="wrong")])
def test_an_unsigned_or_wrongly_signed_request_is_rejected_before_anything_runs(sign):
    tools = SpyTools()
    status, body = post(make_endpoint(tools), sign=sign)
    assert status == 401 and body["ok"] is False and tools.calls == []
    assert body["speak"] == "", "nothing is spoken to the caller on an authentication failure"


def test_authentication_happens_before_the_body_is_parsed():
    """A garbage body with no signature is 401 (not 400): nothing about the body is revealed to an unauthenticated sender."""
    assert post(make_endpoint(), body=b"{not json", sign=None)[0] == 401
    assert post(make_endpoint(), body=b"{not json")[0] == 400


def test_an_oversized_body_is_refused_even_if_signed():
    big = b" " * (MAX_BODY_BYTES + 1)
    assert post(make_endpoint(), body=big)[0] == 413


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "PATCH"])
def test_only_post_is_accepted(method):
    body = payload()
    assert make_endpoint().handle(method, "/tools/check_slot", {"X-Retell-Signature": stamped(body)}, body)[0] == 405


@pytest.mark.parametrize("path", ["/", "/tools/", "/tools/delete_everything", "/tools/check_slot/extra", "/admin", "/tools/Check_Slot"])
def test_unknown_paths_are_404(path):
    body = payload()
    assert make_endpoint().handle("POST", path, {"X-Retell-Signature": stamped(body)}, body)[0] == 404


def test_a_query_string_does_not_change_the_route():
    body = payload()
    status, _ = make_endpoint().handle("POST", "/tools/check_slot?x=1", {"X-Retell-Signature": stamped(body)}, body)
    assert status == 200


@pytest.mark.parametrize("body", [
    b"{not json", b"[]", b'"text"', b"null", b"123", b"\xff\xfe",
    json.dumps({"name": "check_slot", "call": {}, "args": {}}).encode(),
    json.dumps({"name": "check_slot", "call": {"call_id": ""}, "args": {}}).encode(),
    json.dumps({"name": "check_slot", "call": {"call_id": 7}, "args": {}}).encode(),
    json.dumps({"name": "check_slot", "call": "x", "args": {}}).encode(),
    json.dumps({"name": "check_slot", "call": {"call_id": "c"}, "args": []}).encode(),
    json.dumps({"name": "check_slot", "args": {}}).encode(),
])
def test_malformed_requests_are_400_and_never_reach_a_tool(body):
    tools = SpyTools()
    assert post(make_endpoint(tools), body=body)[0] == 400 and tools.calls == []


def test_a_body_naming_a_different_function_than_the_url_is_refused():
    tools = SpyTools()
    assert post(make_endpoint(tools), route="check_slot", body=payload(name="book_slot"))[0] == 400
    assert tools.calls == []


def test_a_missing_name_and_missing_args_are_tolerated():
    tools = SpyTools()
    body = json.dumps({"call": {"call_id": "c"}}).encode()
    assert post(make_endpoint(tools), body=body)[0] == 200
    assert tools.calls == [("check_slot", "c", {})]


def test_the_optional_bearer_token_is_required_when_configured():
    tools = SpyTools()
    endpoint = make_endpoint(tools, bearer_token="tok-123")
    assert post(endpoint)[0] == 401
    assert post(endpoint, headers={"Authorization": "Bearer wrong"})[0] == 401
    assert post(endpoint, headers={"Authorization": "tok-123"})[0] == 401
    assert tools.calls == []
    assert post(endpoint, headers={"Authorization": "Bearer tok-123"})[0] == 200


def test_the_bearer_token_alone_is_not_enough_without_the_signature():
    assert post(make_endpoint(bearer_token="tok"), sign=None, headers={"Authorization": "Bearer tok"})[0] == 401


def test_a_tool_that_raises_gives_a_safe_spoken_answer_and_leaks_nothing():
    class Broken(SpyTools):
        def check_slot(self, call_id, args):
            raise RuntimeError("secret internals /home/user/key=abc")

    endpoint = make_endpoint(Broken())
    status, body = post(endpoint)
    assert status == 200 and body["status"] == "error" and body["ok"] is False
    assert "secret" not in json.dumps(body) and "can't confirm anything" in body["speak"]
    assert not any("secret" in line for line in endpoint.log_lines), "only the exception TYPE is logged"


def test_a_tool_returning_something_unserialisable_is_an_error_not_a_crash():
    class Bad(SpyTools):
        def check_slot(self, call_id, args):
            return {"status": "available", "speak": object()}

    status, body = post(make_endpoint(Bad()))
    assert status == 200 and body["status"] == "error" and body["ok"] is False


def test_the_key_and_the_signature_never_appear_in_the_log():
    endpoint = make_endpoint()
    body = payload()
    post(endpoint, body=body)
    post(endpoint, body=body, sign=None)
    joined = " ".join(endpoint.log_lines)
    assert KEY not in joined and stamped(body) not in joined


def test_an_endpoint_cannot_be_built_without_a_key():
    with pytest.raises(ValueError):
        RetellEndpoint(SpyTools(), "")


def test_a_replayed_request_inside_the_window_is_accepted_but_is_harmless_by_idempotency():
    """Replay protection is only the 5-minute window; safety against a replay comes from idempotent routes (see tools)."""
    tools = SpyTools()
    endpoint = make_endpoint(tools)
    body = payload()
    assert post(endpoint, body=body)[0] == 200 and post(endpoint, body=body)[0] == 200
    later = RetellEndpoint(tools, KEY, now_ms=lambda: NOW_MS + 301_000)
    assert later.handle("POST", "/tools/check_slot", {"X-Retell-Signature": stamped(body)}, body)[0] == 401


# ---------------------------------------------------------------- a real loopback round trip


@pytest.fixture
def live_server():
    tools = SpyTools()
    server = make_http_server(RetellEndpoint(tools, KEY), 0)  # port 0: any free loopback port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, tools
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def http_post(server, path, body, headers=None):
    request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}", data=body, method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_the_server_is_bound_to_the_loopback_interface_only(live_server):
    server, _ = live_server
    assert server.server_address[0] == "127.0.0.1"


@pytest.mark.parametrize("host", ["0.0.0.0", "", "192.168.1.5", "example.com", "::"])
def test_binding_to_anything_but_loopback_is_refused(host):
    with pytest.raises(ValueError, match="loopback"):
        make_http_server(RetellEndpoint(SpyTools(), KEY), 0, host=host)


def test_a_signed_request_over_real_http_works_and_an_unsigned_one_does_not(live_server):
    server, tools = live_server
    body = payload(args={"date": "2026-10-12", "time": "11:00"})
    status, answer = http_post(server, "/tools/check_slot", body, {"X-Retell-Signature": stamped(body, ts=retell_http.time.time_ns() // 1_000_000)})
    assert status == 200 and answer["speak"] == "check_slot done"
    status, answer = http_post(server, "/tools/check_slot", body)
    assert status == 401
    assert len(tools.calls) == 1


def test_an_oversized_or_missing_content_length_is_refused_over_real_http_before_the_body_is_read(live_server):
    import http.client

    server, tools = live_server
    for headers, expected in (({"Content-Length": str(MAX_BODY_BYTES + 10)}, 413), ({}, 411)):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        conn.putrequest("POST", "/tools/check_slot")
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()  # no body is ever sent: the refusal must not wait for one
        assert conn.getresponse().status == expected
        conn.close()
    assert tools.calls == []


def test_a_non_ascii_signature_header_is_rejected_cleanly_over_real_http(live_server):
    import http.client

    server, tools = live_server
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    body = payload()
    conn.putrequest("POST", "/tools/check_slot")
    conn.putheader("Content-Length", str(len(body)))
    conn.putheader("X-Retell-Signature", "café".encode("utf-8").decode("latin-1"))
    conn.endheaders(body)
    assert conn.getresponse().status == 401 and tools.calls == []
    conn.close()


def test_get_is_refused_over_real_http(live_server):
    server, _ = live_server
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/tools/check_slot", timeout=5)
    assert err.value.code == 405


# ---------------------------------------------------------------- `serve` gating (no browser, no port, until everything is set)


ENV = {"ARIA_BOOKSY_BUSINESS_ID": "1234567", "ARIA_LIVE_BOOKSY": "1", "ARIA_RETELL_API_KEY": KEY}
SERVE = ["serve", "--confirm-business-id", "1234567", "--port", "18787"]
FIVE = ["--approve-" + f for f in cli.BOOK_APPROVALS]


class Spy:
    created = 0

    def __init__(self, cfg):
        Spy.created += 1
        self.closed = False

    def verify_business(self):
        return "1234567"

    def close(self):
        self.closed = True


class StubServer:
    served = False
    closed = False

    def serve_forever(self):
        StubServer.served = True
        raise KeyboardInterrupt

    def server_close(self):
        StubServer.closed = True


def run(argv, env, monkeypatch=None, factory=Spy):
    lines = []
    code = cli.main(argv, environ=env, driver_factory=factory, clock=lambda: NOW, out=lines.append)
    return code, "\n".join(lines)


def refused_without_a_browser(argv, env, expect):
    Spy.created = 0
    code, text = run(argv, env)
    assert code == cli.EXIT_REFUSED and expect in text and Spy.created == 0, text
    assert KEY not in text


def test_serve_needs_the_live_switch():
    refused_without_a_browser(SERVE, {k: v for k, v in ENV.items() if k != "ARIA_LIVE_BOOKSY"}, "ARIA_LIVE_BOOKSY=1")


def test_serve_needs_the_matching_business_confirmation():
    refused_without_a_browser(["serve", "--confirm-business-id", "9999999"], ENV, "confirm-business-id")


def test_serve_needs_the_retell_key_in_the_environment():
    refused_without_a_browser(SERVE, {k: v for k, v in ENV.items() if k != "ARIA_RETELL_API_KEY"}, "ARIA_RETELL_API_KEY")
    refused_without_a_browser(SERVE, {**ENV, "ARIA_RETELL_API_KEY": "   "}, "ARIA_RETELL_API_KEY")


def test_the_key_is_not_a_command_line_option():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(SERVE + ["--api-key", KEY])


def test_serve_refuses_a_bad_port():
    refused_without_a_browser(["serve", "--confirm-business-id", "1234567", "--port", "70000"], ENV, "--port")
    refused_without_a_browser(["serve", "--confirm-business-id", "1234567", "--port", "0"], ENV, "--port")


@pytest.mark.parametrize("drop", FIVE)
def test_booking_needs_all_five_approvals_or_none(drop):
    refused_without_a_browser(SERVE + [f for f in FIVE if f != drop], ENV, drop)


def test_serve_without_approvals_is_read_only_and_says_so(monkeypatch):
    monkeypatch.setattr(retell_http, "make_http_server", lambda endpoint, port, host="127.0.0.1": StubServer())
    StubServer.served = StubServer.closed = False
    Spy.created = 0
    drivers = []
    code, text = run(SERVE, ENV, factory=lambda cfg: drivers.append(Spy(cfg)) or drivers[-1])
    assert code == cli.EXIT_OK and StubServer.served and StubServer.closed and drivers[0].closed
    assert "READ-ONLY" in text and "127.0.0.1:18787" in text and "BOOKING ENABLED" not in text
    assert KEY not in text


def test_serve_with_all_five_approvals_says_booking_is_enabled(monkeypatch):
    captured = {}

    def fake_make(endpoint, port, host="127.0.0.1"):
        captured["tools"] = endpoint._tools
        return StubServer()

    monkeypatch.setattr(retell_http, "make_http_server", fake_make)
    code, text = run(SERVE + FIVE, ENV)
    assert code == cli.EXIT_OK and "BOOKING ENABLED" in text and captured["tools"].booking_enabled is True
    assert KEY not in text


def test_the_real_driver_is_given_the_approvals_only_when_booking_is_enabled(monkeypatch):
    seen = []

    class Recording(Spy):
        def __init__(self, cfg, approvals=frozenset()):
            seen.append(approvals)
            super().__init__(cfg)

    monkeypatch.setattr(cli, "SeleniumBooksyDriver", Recording)
    monkeypatch.setattr(retell_http, "make_http_server", lambda endpoint, port, host="127.0.0.1": StubServer())
    lines = []
    cli.main(SERVE, environ=ENV, clock=lambda: NOW, out=lines.append)
    cli.main(SERVE + FIVE, environ=ENV, clock=lambda: NOW, out=lines.append)
    assert seen[0] == frozenset() and seen[1] == frozenset(cli.BOOK_APPROVALS)


def test_a_wrong_signed_in_business_stops_serve_before_any_port_is_opened(monkeypatch):
    opened = []
    monkeypatch.setattr(retell_http, "make_http_server", lambda *a, **k: opened.append(1) or StubServer())

    class Wrong(Spy):
        def verify_business(self):
            return "7777777"

    code, text = run(SERVE, ENV, factory=Wrong)
    assert code == cli.EXIT_REFUSED and "DIFFERENT business" in text and opened == []
