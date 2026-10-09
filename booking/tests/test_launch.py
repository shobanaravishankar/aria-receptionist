"""The shared launch entry point: tools and routes are built together, and a mismatched pair is impossible."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from aria_booking import cli
from aria_booking.catalog.bookable import BookableRegistry, BookableService
from aria_booking.config import Config
from aria_booking.voice import launch
from aria_booking.voice.launch import build_endpoint, build_tools
from aria_booking.voice.retell_http import READ_ONLY_ROUTES, ROUTES, RetellEndpoint
from aria_booking.voice.tools import VoiceTools

from fakes import FakeMultiCalendar

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 8, 12, tzinfo=TZ)
KEY = "test-key-not-real"
TS = 1_790_000_000_000


def signed(body: bytes) -> dict:
    import hashlib
    import hmac

    return {"X-Retell-Signature": f"v={TS},d=" + hmac.new(KEY.encode(), body + str(TS).encode(), hashlib.sha256).hexdigest()}


def parts(**kw):
    cfg = Config(business_id="9999999")
    calendar = FakeMultiCalendar(TZ, ["Ana"], business_id="9999999", now=NOW)
    registry = BookableRegistry([BookableService("m60", "M 60", 60, verified=True)])
    return cfg, calendar, registry


def test_the_default_is_the_availability_only_pair():
    cfg, calendar, registry = parts()
    endpoint = build_endpoint(cfg, calendar, lambda *a: None, lambda: NOW, KEY, registry=registry)
    tools = endpoint._tools
    assert tools.availability_only is True and tools.booking_enabled is False and tools._read_only is True
    assert endpoint._routes == READ_ONLY_ROUTES and "book_slot" not in endpoint._routes.values()


def test_a_signed_request_to_the_booking_route_is_a_404_on_the_default_pair():
    cfg, calendar, registry = parts()
    endpoint = RetellEndpoint(build_tools(cfg, calendar, lambda *a: None, lambda: NOW, booking_approved=False, registry=registry), KEY, now_ms=lambda: TS)
    body = json.dumps({"name": "book_slot", "call": {"call_id": "c"}, "args": {"option_id": "x", "confirmed": True}}).encode()
    assert endpoint.handle("POST", "/tools/book_slot", signed(body), body)[0] == 404


def test_the_booking_pair_is_all_or_nothing():
    cfg, calendar, registry = parts()
    endpoint = build_endpoint(cfg, calendar, lambda *a: None, lambda: NOW, KEY, booking_approved=True, registry=registry)
    assert endpoint._tools.booking_enabled is True and endpoint._tools.availability_only is False and endpoint._routes == ROUTES


def test_the_service_id_is_always_required_through_the_launch_path():
    cfg, calendar, registry = parts()
    for approved in (False, True):
        tools = build_endpoint(cfg, calendar, lambda *a: None, lambda: NOW, KEY, booking_approved=approved, registry=registry)._tools
        reply = tools.check_slot("c", {"date": "2026-10-12", "time": "11:00"})
        assert reply["status"] == "needs_clarification" and reply["reason"] == "service_required"


def test_a_booking_route_on_tools_that_cannot_write_is_refused_outright():
    cfg, calendar, registry = parts()
    for kwargs in ({"availability_only": True}, {"booking_enabled": False}):
        tools = VoiceTools(cfg, calendar, lambda *a: None, lambda: NOW, registry=registry, require_service_id=True, **kwargs)
        with pytest.raises(ValueError, match="cannot write"):
            RetellEndpoint(tools, KEY, routes=ROUTES)


def test_without_explicit_routes_the_endpoint_derives_them_from_what_the_tools_can_do():
    cfg, calendar, registry = parts()
    read_only = VoiceTools(cfg, calendar, lambda *a: None, lambda: NOW, registry=registry, require_service_id=True, availability_only=True)
    assert RetellEndpoint(read_only, KEY)._routes == READ_ONLY_ROUTES
    writer = VoiceTools(cfg, calendar, lambda *a: None, lambda: NOW, registry=registry, require_service_id=True, booking_enabled=True)
    assert RetellEndpoint(writer, KEY)._routes == ROUTES


def test_fewer_routes_than_the_tools_could_serve_is_allowed():
    cfg, calendar, registry = parts()
    writer = VoiceTools(cfg, calendar, lambda *a: None, lambda: NOW, registry=registry, require_service_id=True, booking_enabled=True)
    assert "book_slot" not in RetellEndpoint(writer, KEY, routes=READ_ONLY_ROUTES)._routes.values()


def test_the_cli_builds_its_endpoint_through_the_shared_entry_point(monkeypatch):
    seen = {}

    class Stub:
        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    real = launch.build_endpoint

    def capture(*args, **kwargs):
        seen["kwargs"] = kwargs
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "build_endpoint", capture)
    monkeypatch.setattr(cli.retell_http, "make_http_server", lambda endpoint, port, host="127.0.0.1": Stub())

    class Driver:
        def __init__(self, cfg):
            pass

        def verify_business(self):
            return "1234567"

        def close(self):
            pass

    env = {"ARIA_BOOKSY_BUSINESS_ID": "1234567", "ARIA_LIVE_BOOKSY": "1", "ARIA_RETELL_API_KEY": KEY}
    lines = []
    assert cli.main(["serve", "--confirm-business-id", "1234567"], environ=env, driver_factory=Driver, clock=lambda: NOW, out=lines.append) == cli.EXIT_OK
    assert seen["kwargs"]["booking_approved"] is False
    lines2 = []
    five = ["--approve-" + f for f in cli.BOOK_APPROVALS]
    assert cli.main(["serve", "--confirm-business-id", "1234567", *five], environ=env, driver_factory=Driver, clock=lambda: NOW, out=lines2.append) == cli.EXIT_OK
    assert seen["kwargs"]["booking_approved"] is True
