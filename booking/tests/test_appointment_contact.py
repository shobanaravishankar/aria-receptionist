"""The salon-contact fallback: the configured name and number are given when Aria cannot answer or a service fails, nothing is guessed when none is configured, and nothing
is ever said to have been transferred, called back or left as a message."""

from __future__ import annotations

import re

import pytest

from aria_booking.appointments.contact import contact_phrase, spoken_phone
from aria_booking.appointments.store import StoreError
from aria_booking.showcase import SHOWCASE, load_showcase

from test_appointment_tools import Env, WILLA, WILLA_PHONE

CONFIGURED = "(310) 555-0123"
PHRASE = "contact Serenity Spa at 3 1 0, 5 5 5, 0 1 2 3"
NEVER = re.compile(r"transfer|call you back|call back|calling you|leave a message|left a message|someone will|we will call|has been notified", re.I)


def phrase(**kw):
    return contact_phrase(load_showcase(overrides={"contact_phone": CONFIGURED, **kw}))


def test_no_number_configured_means_no_phrase_and_nothing_is_invented():
    assert SHOWCASE.contact_phone is None and contact_phrase(SHOWCASE) is None


def test_a_configured_number_is_spoken_in_groups_with_the_configured_name():
    assert phrase() == PHRASE
    assert phrase(contact_name="Glamour Day Spa") == "contact Glamour Day Spa at 3 1 0, 5 5 5, 0 1 2 3"
    assert spoken_phone("+12125550100") == "2 1 2, 5 5 5, 0 1 0 0" and spoken_phone("+442079460958") == "4 4 2 0 7 9 4 6 0 9 5 8"


@pytest.mark.parametrize("bad", ["555-0100", "call us", "212 555"])
def test_an_incomplete_configured_number_is_refused_not_completed(bad):
    with pytest.raises(ValueError, match="complete phone number"):
        phrase(contact_phone=bad)


def lines_of(env):
    """Several replies where the tool cannot help or confirm."""
    out = [
        env.call("lookup_service", {"query": "zzzz not a service"})["speak"],
        env.call("find_appointments", {"phone": "212-555-0199"})["speak"],
        env.call("cancel_appointment", {"reference": "ZZZZZZ", "phone": WILLA_PHONE, "name": WILLA})["speak"],
        env.call("last_completed_visit", {"service_category": "facial", "phone": "212-555-0198"}, "h1")["speak"],
        env.call("book_appointment", {**env.book_args(), "service_id": "waxing-brazilian-line-30"})["speak"],
    ]
    return out


def test_every_fallback_names_the_salon_and_gives_the_configured_number(tmp_path):
    env = Env(tmp_path, contact=PHRASE)
    spoken = lines_of(env)
    assert all(PHRASE in line for line in spoken[:4]), spoken
    assert not any("contact the salon directly" in line for line in spoken)
    assert not any(NEVER.search(line) for line in spoken)


def test_a_service_outage_gives_the_number_and_claims_no_follow_up(tmp_path, monkeypatch):
    env = Env(tmp_path, contact=PHRASE)
    monkeypatch.setattr(env.lc.store, "clients_with_phone", lambda *a: (_ for _ in ()).throw(StoreError("locked")))
    out = env.call("find_appointments", {"phone": WILLA_PHONE, "name": WILLA})
    assert out["status"] == "system_unavailable" and out["speak"].endswith(f"Please {PHRASE}.") and "can't confirm" in out["speak"] and not NEVER.search(out["speak"])
    assert env.counts()["appointments"]["confirmed"] == 4


def test_without_a_configured_number_the_fallback_says_to_contact_the_salon_and_gives_no_digits(tmp_path):
    env = Env(tmp_path)
    spoken = lines_of(env)
    assert any("contact the salon directly" in line for line in spoken)
    assert not any(re.search(r"\d", line) for line in spoken if "Please" in line and "contact" in line), "no number is invented"
    assert not any(NEVER.search(line) for line in spoken)


def test_only_the_spoken_sentence_changes(tmp_path):
    plain, branded = Env(tmp_path / "a"), Env(tmp_path / "b", contact=PHRASE)
    one = plain.call("find_appointments", {"phone": "212-555-0199"})
    two = branded.call("find_appointments", {"phone": "212-555-0199"})
    assert {k: v for k, v in one.items() if k != "speak"} == {k: v for k, v in two.items() if k != "speak"}
    assert one["speak"].replace("contact the salon directly", PHRASE) == two["speak"]
