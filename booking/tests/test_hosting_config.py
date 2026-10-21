"""The public backend's settings: every wrong or missing one is refused with a message that names it and says what to change, and no secret is ever in a message or a repr."""

from __future__ import annotations

from pathlib import Path

import pytest

from aria_booking.hosting.config import DEFAULT_PORT, HostingError, load_public_config, parse_origin

KEY = "key_" + "a1b2c3d4e5f6a7b8c9d0e1f2"
TOKEN = "token_" + "0f1e2d3c4b5a69788796a5b4"
PUBLIC = "public_key_" + "abcdef0123456789"
GOOD = {
    "ARIA_RETELL_API_KEY": KEY, "ARIA_RETELL_TOOL_TOKEN": TOKEN, "ARIA_PUBLIC_ORIGIN": "https://serenity-demo.fly.dev",
    "ARIA_SITE_ORIGINS": "https://glamour-dayspa-aria-demo.netlify.app", "ARIA_STORE_PATH": "/data/appointments.sqlite3",
}


def refused(env, **kw) -> str:
    with pytest.raises(HostingError) as caught:
        load_public_config(env, **kw)
    return str(caught.value)


# ---------------------------------------------------------------- origins


@pytest.mark.parametrize("text,expected", [
    ("https://serenity-demo.fly.dev", "https://serenity-demo.fly.dev"), ("https://serenity-demo.fly.dev/", "https://serenity-demo.fly.dev"),
    ("HTTPS://Serenity-Demo.Fly.Dev", "https://serenity-demo.fly.dev"), ("https://demo.example.com:8443", "https://demo.example.com:8443"),
    ("  https://a.netlify.app  ", "https://a.netlify.app"),
])
def test_a_clean_https_origin_is_accepted_and_normalised(text, expected):
    assert parse_origin(text, setting="X") == expected


@pytest.mark.parametrize("text,fragment", [
    ("", "is not an origin"), ("serenity-demo.fly.dev", "is not an origin"), ("https://serenity-demo.fly.dev/app", "is not an origin"), ("https://user:pw@serenity-demo.fly.dev", "is not an origin"),
    ("https://serenity-demo.fly.dev?x=1", "is not an origin"), ("https://serenity-demo.fly.dev#top", "is not an origin"),
    ("http://serenity-demo.fly.dev", "must be https"), ("ftp://serenity-demo.fly.dev", "must be https"),
    ("https://localhost", "is not public"), ("https://127.0.0.1", "is not public"), ("https://machine.local", "is not public"), ("https://10.1.2.3", "private address"),
    ("https://192.168.0.5", "private address"), ("https://169.254.1.1", "private address"),
])
def test_anything_that_is_not_a_public_https_origin_is_refused_with_the_reason(text, fragment):
    with pytest.raises(HostingError, match=fragment):
        parse_origin(text, setting="X")


def test_development_allows_loopback_over_http_and_nothing_else_loosened():
    assert parse_origin("http://localhost:5174", setting="X", dev=True) == "http://localhost:5174"
    assert parse_origin("http://127.0.0.1:5174", setting="X", dev=True) == "http://127.0.0.1:5174"
    with pytest.raises(HostingError, match="must be https"):
        parse_origin("http://serenity-demo.fly.dev", setting="X", dev=True)
    with pytest.raises(HostingError, match="private address"):
        parse_origin("https://10.0.0.1", setting="X", dev=True)


# ---------------------------------------------------------------- the whole configuration


def test_a_complete_configuration_loads_with_sensible_defaults():
    config = load_public_config(GOOD)
    assert config.public_origin == "https://serenity-demo.fly.dev" and config.site_origins == ("https://glamour-dayspa-aria-demo.netlify.app",)
    assert config.store_path == Path("/data/appointments.sqlite3") and config.host == "0.0.0.0" and config.port == DEFAULT_PORT and config.serve_site is True
    assert config.allowed_origins == ("https://glamour-dayspa-aria-demo.netlify.app", "https://serenity-demo.fly.dev"), "the websites, and the backend itself"
    assert config.voice_settings is None, "no public key or agent id: the page will say voice is not set up"


def test_the_page_settings_are_public_values_only_and_default_to_version_zero():
    config = load_public_config({**GOOD, "ARIA_RETELL_PUBLIC_KEY": PUBLIC, "ARIA_RETELL_AGENT_ID": "agent_test"})
    assert config.voice_settings == {"publicKey": PUBLIC, "agentId": "agent_test", "agentVersion": 0}
    assert load_public_config({**GOOD, "ARIA_RETELL_PUBLIC_KEY": PUBLIC, "ARIA_RETELL_AGENT_ID": "agent_test", "ARIA_RETELL_AGENT_VERSION": "7"}).voice_settings["agentVersion"] == 7


def test_the_private_key_is_never_accepted_where_a_browser_could_read_it():
    message = refused({**GOOD, "ARIA_RETELL_PUBLIC_KEY": KEY})
    assert "must be a Retell PUBLIC key" in message and KEY not in message


def test_everything_missing_is_reported_at_once_each_with_what_to_change():
    message = refused({})
    for name in ("ARIA_RETELL_API_KEY", "ARIA_RETELL_TOOL_TOKEN", "ARIA_PUBLIC_ORIGIN", "ARIA_STORE_PATH"):
        assert name in message, name
    assert "host SECRET" in message and "persistent volume" in message and "https://your-app.fly.dev" in message


@pytest.mark.parametrize("name,value,fragment", [
    ("ARIA_STORE_PATH", "relative/appointments.sqlite3", "not an absolute path"), ("ARIA_SITE_ORIGINS", "http://site.example", "must be https"),
    ("ARIA_SITE_ORIGINS", "https://ok.example,not-an-origin", "is not an origin"), ("PORT", "eighty", "is not a number"), ("PORT", "70000", "outside 1-65535"),
    ("ARIA_RETELL_AGENT_VERSION", "-1", "outside"), ("ARIA_PUBLIC_ORIGIN", "https://localhost", "is not public"),
])
def test_each_bad_value_is_named(name, value, fragment):
    assert fragment in refused({**GOOD, name: value})


def test_several_websites_may_be_listed_with_commas_or_semicolons():
    config = load_public_config({**GOOD, "ARIA_SITE_ORIGINS": "https://a.netlify.app; https://b.example.com,https://a.netlify.app"})
    assert config.site_origins == ("https://a.netlify.app", "https://b.example.com", "https://a.netlify.app")
    assert config.allowed_origins == ("https://a.netlify.app", "https://b.example.com", "https://serenity-demo.fly.dev"), "listed once each"


def test_the_port_comes_from_the_host_and_a_flag_wins():
    assert load_public_config({**GOOD, "PORT": "9090"}).port == 9090
    assert load_public_config({**GOOD, "ARIA_PORT": "9191"}).port == 9191
    assert load_public_config({**GOOD, "PORT": "9090"}, overrides={"port": 7000}).port == 7000


def test_development_needs_no_tool_token_and_allows_a_relative_store_and_local_origins():
    env = {"ARIA_RETELL_API_KEY": KEY, "ARIA_PUBLIC_ORIGIN": "http://localhost:8080", "ARIA_STORE_PATH": "local.sqlite3"}
    config = load_public_config(env, dev=True)
    assert config.tool_token == "" and config.dev is True and config.store_path == Path("local.sqlite3")
    assert "ARIA_RETELL_TOOL_TOKEN" in refused(env), "but a real deployment must have it"


def test_the_site_can_be_switched_off_for_a_backend_that_only_serves_the_calendar_and_the_tools():
    assert load_public_config({**GOOD, "ARIA_SERVE_SITE": "0"}).serve_site is False
    assert load_public_config({**GOOD, "ARIA_SERVE_SITE": "yes"}).serve_site is True


# ---------------------------------------------------------------- no secret anywhere it should not be


def test_no_secret_is_in_the_repr_the_text_or_any_refusal():
    config = load_public_config({**GOOD, "ARIA_RETELL_PUBLIC_KEY": PUBLIC, "ARIA_RETELL_AGENT_ID": "agent_test"})
    shown = repr(config) + str(config)
    assert KEY not in shown and TOKEN not in shown and PUBLIC not in shown
    assert config.api_key == KEY and config.tool_token == TOKEN  # it holds them, it just never shows them
    for env in ({**GOOD, "PORT": "x"}, {**GOOD, "ARIA_STORE_PATH": "rel"}, {"ARIA_RETELL_API_KEY": KEY, "ARIA_RETELL_TOOL_TOKEN": TOKEN}):
        message = refused(env)
        assert KEY not in message and TOKEN not in message


def test_the_preferred_technician_functions_are_off_unless_the_host_turns_them_on():
    assert load_public_config(GOOD).preferences is False
    for text in ("1", "true", "YES", " on "):
        assert load_public_config({**GOOD, "ARIA_PREFERENCES": text}).preferences is True, text
    for text in ("0", "", "false", "no", "off", "maybe"):
        assert load_public_config({**GOOD, "ARIA_PREFERENCES": text}).preferences is False, text
