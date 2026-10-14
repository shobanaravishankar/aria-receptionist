"""demo-serve --via-page: the page reader must interpret times in the SAME zone as the voice tools and the dataset, whatever the LIVE calendar's environment says.

The live commands read ARIA_TIMEZONE for the real salon's calendar. The demo has its own one zone (SPA_TIMEZONE); a reader left in another zone would shift every appointment on
the page relative to what the tools say, for the read-only demo and for the simulated lifecycle alike."""

from __future__ import annotations

import pytest

from aria_booking import cli
from aria_booking.demo import DEMO_BUSINESS_ID, SPA_TIMEZONE
from aria_booking.demo import server as demo_server
from aria_booking.voice import retell_http

from test_launch import KEY


class Quiet:
    def serve_forever(self):
        return

    def shutdown(self):
        return

    def server_close(self):
        return


class ToolServer(Quiet):
    def serve_forever(self):
        raise KeyboardInterrupt


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    class PageReader:
        def __init__(self, config, **kwargs):
            seen["reader_timezone"] = config.timezone

        def verify_business(self):
            return DEMO_BUSINESS_ID

        def close(self):
            return

    def tool_server(endpoint, *args, **kwargs):
        seen["tool_timezone"] = endpoint._tools.cfg.timezone
        return ToolServer()

    monkeypatch.setattr(demo_server, "make_site", lambda *a, **k: Quiet())
    monkeypatch.setattr(retell_http, "make_http_server", tool_server)
    monkeypatch.setattr(cli, "PlaywrightBooksyDriver", PageReader)
    return seen


@pytest.mark.parametrize("extra", [[], ["--lifecycle"]], ids=["read-only demo", "simulated lifecycle"])
def test_the_page_reader_and_the_tools_share_the_demo_zone_despite_the_live_calendars_environment(captured, tmp_path, extra):
    lines = []
    result = cli.main(
        ["demo-serve", "--via-page", "--store", str(tmp_path / "a.sqlite3"), *extra] if extra else ["demo-serve", "--via-page"],
        environ={"ARIA_RETELL_API_KEY": KEY, "ARIA_TIMEZONE": "America/Los_Angeles", "ARIA_LOCAL_DIR": str(tmp_path)}, out=lines.append,
    )
    assert result == cli.EXIT_OK, lines
    assert captured["reader_timezone"] == captured["tool_timezone"] == SPA_TIMEZONE, captured


def test_the_default_direct_reader_is_unaffected_by_the_environment(captured, tmp_path):
    result = cli.main(["demo-serve"], environ={"ARIA_RETELL_API_KEY": KEY, "ARIA_TIMEZONE": "America/Los_Angeles"}, out=lambda m: None)
    assert result == cli.EXIT_OK and "reader_timezone" not in captured and captured["tool_timezone"] == SPA_TIMEZONE
