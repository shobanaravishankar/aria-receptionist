"""Corrections from the supervised rehearsal of 2026-10-21 (synthetic data only; no network, no Retell, no Booksy).

The recorded call: the caller asked for "a sixty minute massage with Tamsen" (the roster has Tamsin), the agent chose Swedish itself, spoke a roster list as if it were checked
availability at 4 PM, and the booking was then saved under the recognised spelling "Villa" instead of the existing client "Willa" on a number two people share.

  1. a technician name that matches nobody on the team is a question against the real roster, never an invented person and never a list that sounds like availability;
  2. a length plus a group word ("sixty minute massage") is ambiguous and asks which treatment before any service is chosen;
  3. a name that matches nobody on a number that already has a record is resolved by spelling it, never by silently making a second client or fuzzy-merging one.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from test_appointment_tools import (  # noqa: F401  (the lifecycle harness the other suites use)
    ANSEL, CEDRIC_PHONE, KEY, SWEDISH_60, TOMORROW, TODAY, TZ, WILLA, WILLA_PHONE, Env,
)

DEEP_TISSUE_60 = "massage-deep-tissue-massage-60"
MASSAGE_DOERS = {"Marisol", "Tamsin", "Sienna", "Rowan", "Imogen"}  # the demo roster's massage people (synthetic eligibility)
ROSTER_NAMES = {"Marisol", "Tamsin", "Odette", "Priya", "Noor", "Sienna", "Rowan", "Imogen", "Calista", "Delphine"}


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def check(env, staff, *, hour=16, service=DEEP_TISSUE_60, call_id="call-1", **extra):
    args = {"service_id": service, "date": TOMORROW.isoformat(), "time": f"{hour:02d}:00", **extra}
    if staff:
        args["staff"] = staff
    return env.call("check_slot", args, call_id)


# ---------------------------------------------------------------- 1. a name that matches nobody on the team


def test_a_misheard_technician_name_asks_about_the_real_roster_and_invents_nobody(env):
    reply = check(env, "Tamsen")  # the recorded call: "Tamsen" for the real Tamsin
    assert reply["status"] == "needs_clarification" and reply["reason"] == "unknown_staff" and reply["ok"] is False
    assert not reply.get("options") and reply["times_checked"] is False
    assert reply["closest"] == ["Tamsin"] and "Did you mean Tamsin?" in reply["speak"]
    assert set(reply["candidates"]) == MASSAGE_DOERS <= ROSTER_NAMES  # only real people who do this service
    named = {word for word in reply["speak"].replace("?", " ").replace(".", " ").split() if word in ROSTER_NAMES | {"Tamsen"}}
    assert named <= {"Tamsin", "Tamsen"}, "no second person is invented, and no roster list is spoken as if it were availability"
    assert "available" not in reply["speak"].lower() and "open" not in reply["speak"].lower() and "free" not in reply["speak"].lower()
    assert "never say that anyone is free or available" in reply["instruction"]


def test_a_name_that_sounds_like_nobody_lists_the_real_people_who_do_the_service_without_checking_a_time(env):
    reply = check(env, "Zed")
    assert reply["status"] == "needs_clarification" and reply["reason"] == "unknown_staff" and reply["closest"] == [] and reply["times_checked"] is False
    assert "I don't have a technician called Zed" in reply["speak"] and "Which did you mean?" in reply["speak"]
    for name in MASSAGE_DOERS:
        assert name in reply["speak"]
    assert not (ROSTER_NAMES - MASSAGE_DOERS) & set(reply["speak"].replace(".", " ").split()), "people who do not do this service are not offered for it"
    assert not reply.get("options")


def test_the_real_name_works_and_a_person_off_that_day_is_not_presented_as_available(env):
    real = check(env, "Tamsin", hour=15)
    assert real["status"] == "available" and real["options"][0]["technician"] == "Tamsin"
    off = check(env, "Imogen")  # on the roster, but off tomorrow
    assert off["status"] == "staff_unavailable" and off["reason"] == "staff_not_on_schedule" and not off.get("options")
    assert off["times_checked"] is False and "technicians_on_schedule" in off and "Imogen" not in off["technicians_on_schedule"]
    assert "NO time has been checked" in off["instruction"] and "Never say that any of them is free or available" in off["instruction"] and "call check_slot with that name" in off["instruction"]
    assert "available_staff" not in off


def test_a_technician_who_does_not_do_the_service_is_not_offered_as_free(env):
    reply = check(env, "Noor")  # a facial and lash person
    assert reply["status"] == "staff_unavailable" and reply["reason"] == "staff_not_eligible" and reply["times_checked"] is False
    assert "Noor doesn't do" in reply["speak"] and "Noor" not in reply["technicians_on_schedule"]


def test_the_unknown_name_is_also_a_question_when_searching_booking_or_moving(env):
    before = env.counts()
    found = env.call("find_alternatives", {"service_id": DEEP_TISSUE_60, "date": TOMORROW.isoformat(), "staff": "Tamsen"})
    assert found["status"] == "needs_clarification" and found["reason"] == "unknown_staff" and "openings" not in found["speak"]
    booked = env.call("book_appointment", env.book_args(16, staff="Tamsen", service=DEEP_TISSUE_60))
    assert booked["status"] == "needs_clarification" and booked["reason"] == "unknown_staff" and "confirmation_token" not in booked
    assert env.counts() == before, "nothing was saved"
    mine = env.upcoming()[0]
    moved = env.call("reschedule_appointment", {"reference": mine["reference"], "phone": WILLA_PHONE, "name": WILLA, "date": TOMORROW.isoformat(), "time": "12:00", "staff": "Tamsen"})
    assert moved["status"] == "needs_clarification" and moved["reason"] == "unknown_staff" and "stays on" in moved["speak"]
    assert env.lc.store.get(mine["reference"]).start_utc == int(datetime.fromisoformat(mine["start"]).timestamp()), "the appointment did not move"


def test_only_a_close_sound_alike_is_offered_as_a_question_and_a_short_fragment_is_not(env):
    assert check(env, "Odete", service="facial-signature-facial-60")["closest"] == ["Odette"]  # one letter off
    fragment = check(env, "Tam")  # a fragment three letters from the name is not guessed at
    assert fragment["reason"] == "unknown_staff" and fragment["closest"] == [] and "Did you mean" not in fragment["speak"]
    assert check(env, "Tamsen", hour=15)["closest"] == ["Tamsin"], "and asking again gives the same question: nothing was chosen in between"


# ---------------------------------------------------------------- 2. a length and a group word is not a choice of treatment


def lookup(env, words, call_id="call-1"):
    return env.call("lookup_service", {"query": words}, call_id)


@pytest.mark.parametrize("words,minutes", [
    ("sixty minute massage", 60), ("a sixty minute massage", 60), ("I'd like a ninety minute massage", 90), ("a 60 minute massage", 60),
    ("forty five minute facial", 45), ("forty-five minute facial", 45), ("I want to book a massage", None), ("thirty or sixty minute massage", None),
    ("an hour and a half massage", 90),
])
def test_a_length_with_a_group_word_is_answered_as_the_group_and_keeps_the_length(env, words, minutes):
    reply = lookup(env, words)
    assert reply["status"] == "service_group" and reply["category"] in ("massage", "facial") and reply["ok"] is False
    assert reply.get("requested_minutes") == minutes and len(reply["featured"]) == 3
    assert "choose_service" not in json.dumps(reply) and "items" not in reply, "the whole list of massages is not handed over"
    if minutes:
        assert "does NOT choose a treatment" in reply["instruction"] and f"{minutes} minutes" in reply["instruction"]


def test_spoken_lengths_are_understood_and_two_of_them_are_not_a_choice():
    from aria_booking.catalog.lookup import duration_hint

    for text, minutes in [("sixty minute", 60), ("ninety minutes", 90), ("forty five minutes", 45), ("forty-five minutes", 45), ("seventy five minute", 75), ("twenty five minutes", 25),
                          ("fifteen minute", 15), ("1 hour", 60), ("30 minutes", 30)]:
        assert duration_hint(f"deep tissue massage {text}") == minutes, text
    for text in ("thirty or sixty minutes", "sixty minute or ninety minute", "sixty ninety", "thirty minutes or an hour"):
        assert duration_hint(f"massage {text}") is None, text


def test_a_specific_treatment_with_a_spoken_length_still_resolves_to_that_one_service(env):
    reply = lookup(env, "deep tissue massage sixty minutes")
    assert reply["status"] == "service_info" and reply["item"]["service_id"] == DEEP_TISSUE_60
    assert lookup(env, "swedish massage")["status"] == "choose_variant"


@pytest.mark.parametrize("words", ["sixty minute massage", "a massage", "massage", "I'd like a ninety minute massage"])
def test_the_agent_cannot_choose_the_treatment_when_the_callers_words_only_name_a_group(env, words):
    before = env.counts()
    for route, args in (
        ("check_slot", {"service_id": SWEDISH_60, "date": TOMORROW.isoformat(), "time": "16:00", "service_words": words}),
        ("find_alternatives", {"service_id": SWEDISH_60, "date": TOMORROW.isoformat(), "service_words": words}),
        ("book_appointment", {**env.book_args(16, service=SWEDISH_60), "service_words": words}),
    ):
        reply = env.call(route, args)
        assert reply["status"] == "needs_clarification" and reply["reason"] == "service_not_chosen" and reply["service_chosen"] is False, (route, reply)
        assert not reply.get("options") and "confirmation_token" not in reply and reply["ok"] is False
        assert "We have" in reply["speak"] and "kinds of massage" in reply["speak"]
    assert env.counts() == before


def test_words_that_name_another_treatment_than_the_id_are_asked_about(env):
    reply = check(env, None, service=SWEDISH_60, service_words="deep tissue massage")
    assert reply["status"] == "needs_clarification" and reply["reason"] == "service_mismatch" and not reply.get("options")
    assert "Deep Tissue Massage" in reply["speak"] and "Swedish Massage" in reply["speak"]
    for words in ("deep tissue massage sixty minutes", "Deep Tissue", "zzz nothing like it", ""):
        ok = check(env, None, service=DEEP_TISSUE_60 if words != "zzz nothing like it" and words != "" else SWEDISH_60, service_words=words)
        assert ok["status"] in ("available", "alternatives"), (words, ok)


def test_two_services_that_fit_the_words_are_asked_about_and_nothing_is_carried_between_calls(env):
    reply = check(env, None, service=SWEDISH_60, service_words="lymphatic")  # "Lymphatic Drainage Massage": one family, several lengths: not ambiguous
    assert reply["status"] in ("needs_clarification", "available", "alternatives")
    # an earlier call chose Deep Tissue and checked it; a FRESH call with only "sixty minute massage" starts from nothing
    assert check(env, None, service=DEEP_TISSUE_60, call_id="earlier-call", service_words="deep tissue massage 60 minutes")["status"] in ("available", "alternatives")
    fresh = check(env, None, service=DEEP_TISSUE_60, call_id="fresh-call", service_words="sixty minute massage")
    assert fresh["status"] == "needs_clarification" and fresh["reason"] == "service_not_chosen"
    assert lookup(env, "sixty minute massage", "fresh-call")["status"] == "service_group"


# ---------------------------------------------------------------- 3. a name that matches nobody on a shared number is spelled, never silently a second client

WILLA_PHONE_E164 = "+12125550142"


def clients_on(env, phone=WILLA_PHONE_E164):
    return sorted(c.name for c in env.lc.store.clients_with_phone(phone))


def book_for(env, name, *, call_id="call-1", hour=15, minute=45, staff="Tamsin", phone=WILLA_PHONE, **extra):
    args = {"service_id": DEEP_TISSUE_60, "date": TOMORROW.isoformat(), "time": f"{hour:02d}:{minute:02d}", "client_name": name, "phone": phone, "staff": staff, **extra}
    return args, env.call("book_appointment", args, call_id)


def test_a_misrecognised_name_on_a_shared_number_is_spelled_and_never_becomes_a_second_client(env):
    assert clients_on(env) == [ANSEL, WILLA]
    args, first = book_for(env, "Villa Hartwell")
    assert first["status"] == "needs_clarification" and first["reason"] == "name_unmatched" and "confirmation_token" not in first and first["ok"] is False
    assert "spell" in first["speak"]
    assert not any(word in json.dumps(first) for word in ("Willa", "Ansel", "Hartwell")), "the question never says who is on the number, or how many"
    assert clients_on(env) == [ANSEL, WILLA], "no client was made for the misrecognised spelling"
    spelled_args, second = book_for(env, "Willa Hartwell", name_confirmed=True)
    assert second["status"] == "confirmation_required" and "Willa Hartwell" in second["speak"] and "new client" not in second["speak"]
    done = env.call("book_appointment", {**spelled_args, "confirmed": True, "confirmation_token": second["confirmation_token"]})
    assert done["status"] == "booked_verified"
    assert clients_on(env) == [ANSEL, WILLA], "the booking went to the EXISTING client"
    willa_id = next(c.client_id for c in env.lc.store.clients_with_phone(WILLA_PHONE_E164) if c.name == WILLA)
    assert env.lc.store.get(done["appointment"]["reference"]).client_id == willa_id


def test_a_genuinely_new_person_on_a_used_number_is_added_only_after_the_name_is_spelled_and_read_back_as_spelled(env):
    _args, first = book_for(env, "Quentin Hartwell")
    assert first["status"] == "needs_clarification" and first["reason"] == "name_unmatched"
    spelled, second = book_for(env, "Quentin Hartwell", name_confirmed=True)
    assert second["status"] == "confirmation_required" and "spelled Q U E N T I N, H A R T W E L L" in second["speak"] and "as a new client on this number" in second["speak"]
    assert clients_on(env) == [ANSEL, WILLA], "nothing is created until the caller says yes to the read-back"
    # the confirming call may leave name_confirmed out: the call already settled on this name
    plain = {k: v for k, v in spelled.items() if k != "name_confirmed"}
    done = env.call("book_appointment", {**plain, "confirmed": True, "confirmation_token": second["confirmation_token"]})
    assert done["status"] == "booked_verified" and clients_on(env) == [ANSEL, "Quentin Hartwell", WILLA]
    mine = env.call("find_appointments", {"phone": WILLA_PHONE})  # same call, number only: the call knows who this is
    assert mine["status"] == "found" and mine["count"] == 1 and "Deep Tissue" in mine["speak"]


def test_a_brand_new_number_needs_no_spelling_step(env):
    args = env.book_args(16, name="Mira Quillfeather", phone="+1 212 555 0188")
    first = env.call("book_appointment", args)
    assert first["status"] == "confirmation_required" and "new client" not in first["speak"]


def test_what_a_call_settled_on_stays_and_a_different_name_is_asked_about_not_used(env):
    mine = env.call("find_appointments", {"phone": WILLA_PHONE, "name": WILLA})
    assert mine["status"] == "found"
    again = env.call("find_appointments", {"phone": WILLA_PHONE})  # the number alone: this call already knows it is Willa
    assert again["status"] == "found" and again["appointments"] == mine["appointments"]
    switch = env.call("find_appointments", {"phone": WILLA_PHONE, "name": ANSEL})
    assert switch["status"] == "needs_clarification" and switch["reason"] == "name_changed" and "appointments" not in switch
    assert "Willa Hartwell" in switch["speak"], "it repeats only what THIS caller said earlier in this call"
    assert "Express Facial" not in json.dumps(switch) and "Ansel" not in json.dumps(switch)
    chosen = env.call("find_appointments", {"phone": WILLA_PHONE, "name": ANSEL, "name_confirmed": True})
    assert chosen["status"] == "found" and chosen["count"] == 1 and "Express Facial" in chosen["speak"]
    assert env.call("find_appointments", {"phone": WILLA_PHONE})["count"] == 1, "and now the call is Ansel's"
    assert env.call("find_appointments", {"phone": WILLA_PHONE, "name": WILLA}, "other-call")["count"] == 3, "another call is not affected"


def test_a_single_record_number_with_a_name_that_does_not_match_it_is_asked_about_too(env):
    odd = env.call("find_appointments", {"phone": CEDRIC_PHONE, "name": "Sedric Lowe"})
    assert odd["status"] == "needs_clarification" and odd["reason"] == "name_unmatched" and "appointments" not in odd and "Cedric" not in json.dumps(odd)
    assert env.call("find_appointments", {"phone": CEDRIC_PHONE})["status"] in ("found", "no_upcoming"), "the number alone still identifies a single record, as before"
    assert env.call("find_appointments", {"phone": CEDRIC_PHONE, "name": "Cedric Lowe"}, "third-call")["status"] in ("found", "no_upcoming")


def test_the_last_visit_lookup_follows_the_same_rules(env):
    unmatched = env.call("last_completed_visit", {"service_category": "massage", "phone": WILLA_PHONE, "name": "Villa Hartwell"})
    assert unmatched["status"] == "needs_clarification" and unmatched["reason"] == "name_unmatched" and "Willa" not in json.dumps(unmatched)
    found = env.call("last_completed_visit", {"service_category": "massage", "phone": WILLA_PHONE, "name": WILLA, "name_confirmed": True})
    assert found["status"] == "found"
    swapped = env.call("last_completed_visit", {"service_category": "massage", "phone": WILLA_PHONE, "name": ANSEL})
    assert swapped["status"] == "needs_clarification" and swapped["reason"] == "name_changed"


def test_cancelling_follows_the_name_the_call_settled_on(env):
    mine = env.call("find_appointments", {"phone": WILLA_PHONE, "name": WILLA})["appointments"][0]
    other = env.call("cancel_appointment", {"reference": mine["reference"], "phone": WILLA_PHONE, "name": ANSEL})
    assert other["status"] == "needs_clarification" and other["reason"] == "name_changed"
    assert env.lc.store.get(mine["reference"]).status == "confirmed"
    ok = env.call("cancel_appointment", {"reference": mine["reference"], "phone": WILLA_PHONE})  # no name given: the call's own
    assert ok["status"] == "confirmation_required"


# ---------------------------------------------------------------- the recorded sequence, replayed through the real tools


def test_the_recorded_call_replayed(env):
    # "sixty minute massage with Tamsen": the agent picks Swedish itself, but its own record of the caller's words names a group, not a treatment
    first = env.call("check_slot", {"service_id": SWEDISH_60, "date": TOMORROW.isoformat(), "time": "16:00", "staff": "Tamsen", "service_words": "sixty minute massage"})
    assert first["status"] == "needs_clarification" and first["reason"] == "service_not_chosen" and not first.get("options")
    group = lookup(env, "sixty minute massage")
    assert group["status"] == "service_group" and group["requested_minutes"] == 60
    chosen = lookup(env, "deep tissue massage sixty minutes")  # the caller said Deep Tissue
    assert chosen["status"] == "service_info" and chosen["item"]["service_id"] == DEEP_TISSUE_60
    # then the technician: "Tamsen" is not on the team
    unknown = env.call("check_slot", {"service_id": DEEP_TISSUE_60, "date": TOMORROW.isoformat(), "time": "16:00", "staff": "Tamsen", "service_words": "deep tissue massage"})
    assert unknown["status"] == "needs_clarification" and unknown["reason"] == "unknown_staff" and unknown["closest"] == ["Tamsin"] and not unknown.get("options")
    # "yes, Tamsin": 4 PM does not fit her day (a 5 PM visit; the cleanup after a 60 minute service), 3:45 does
    real = env.call("check_slot", {"service_id": DEEP_TISSUE_60, "date": TOMORROW.isoformat(), "time": "16:00", "staff": "Tamsin", "service_words": "deep tissue massage"})
    assert real["status"] == "alternatives" and real["unavailable_reason"] == "conflicts_next"
    assert any(o["start"].startswith(f"{TOMORROW.isoformat()}T15:45") and o["technician"] == "Tamsin" for o in real["options"])
    # booking: the recogniser writes the name as "Villa"
    _args, villa = book_for(env, "Villa Hartwell", service_words="deep tissue massage")
    assert villa["status"] == "needs_clarification" and villa["reason"] == "name_unmatched"
    spelled_args, readback = book_for(env, "Willa Hartwell", name_confirmed=True, service_words="deep tissue massage")
    assert readback["status"] == "confirmation_required" and "Deep Tissue Massage" in readback["speak"] and "Tamsin" in readback["speak"] and "Willa Hartwell" in readback["speak"]
    done = env.call("book_appointment", {**spelled_args, "confirmed": True, "confirmation_token": readback["confirmation_token"]})
    assert done["status"] == "booked_verified" and done["appointment"]["service_id"] == DEEP_TISSUE_60
    assert clients_on(env) == [ANSEL, WILLA], "no 'Villa'"
    # the later lookup finds the SAME client's appointments, the new one among them
    found = env.call("find_appointments", {"phone": WILLA_PHONE})
    assert found["status"] == "found" and any(a["reference"] == done["appointment"]["reference"] for a in found["appointments"])


# ---------------------------------------------------------------- 4. "a later time" is a rule about the start time, not a hint about where to look


def alternatives(env, staff="Sienna", day=TOMORROW, call_id="call-1", **extra):
    args = {"service_id": DEEP_TISSUE_60, "date": day.isoformat(), **extra}
    if staff:
        args["staff"] = staff
    return env.call("find_alternatives", args, call_id)


def starts(reply):
    return [o["start"][:16] for o in reply.get("options", [])]


def at(day, hhmm):
    return f"{day.isoformat()}T{hhmm}"


def test_the_nearest_time_is_a_preference_and_not_a_bound_which_is_why_a_bound_is_needed(env):
    loose = alternatives(env, time="17:00")  # the recorded call: "after five" was only passed as a preferred time
    assert any(s < at(TOMORROW, "17:00") for s in starts(loose)), "without a bound an earlier time is a normal answer, and must not be called 'later'"


def test_at_or_after_includes_the_time_and_after_excludes_it(env):
    inclusive = alternatives(env, starting_at_or_after="17:00")
    assert starts(inclusive)[0] == at(TOMORROW, "17:00") and all(s >= at(TOMORROW, "17:00") for s in starts(inclusive))
    strict = alternatives(env, starting_after="17:00")
    assert starts(strict)[0] == at(TOMORROW, "17:15") and all(s > at(TOMORROW, "17:00") for s in starts(strict))
    assert inclusive["status"] == strict["status"] == "alternatives" and len(starts(inclusive)) == 3


def test_the_recorded_requests_for_a_later_time_are_answered_inside_the_bound(env):
    later = alternatives(env, "Tamsin", starting_after="15:45")  # "move my 3:45 to a later time with Tamsin"
    assert later["status"] == "alternatives" and starts(later) and all(s > at(TOMORROW, "15:45") for s in starts(later))
    assert at(TOMORROW, "12:00") not in starts(later), "the noon opening that was offered as 'later' is not offered"
    assert "later than 3:45 PM" in later["speak"] and later["window"] == {"date": TOMORROW.isoformat(), "from": "15:46", "to": None, "described_as": "later than 3:45 PM"}
    other = alternatives(env, None, starting_after="15:45")  # "later, with another therapist"
    assert other["status"] == "alternatives" and all(s > at(TOMORROW, "15:45") for s in starts(other))
    assert all(s.startswith(TOMORROW.isoformat()) for s in starts(other)), "no other day is offered as a later time"
    five = alternatives(env, "Sienna", time="17:00", starting_at_or_after="17:00")  # "at five", also passing the time
    assert all(s >= at(TOMORROW, "17:00") for s in starts(five)) and starts(five)[0] == at(TOMORROW, "17:00")


def test_before_and_between_and_the_stricter_bound_wins(env):
    before = alternatives(env, starting_before="12:00")
    assert starts(before) and all(s < at(TOMORROW, "12:00") for s in starts(before)) and "earlier than 12 PM" in before["speak"]
    by = alternatives(env, starting_at_or_before="10:15")
    assert starts(by) and max(starts(by)) <= at(TOMORROW, "10:15")
    between = alternatives(env, starting_at_or_after="13:00", starting_at_or_before="14:00")
    assert starts(between) and all(at(TOMORROW, "13:00") <= s <= at(TOMORROW, "14:00") for s in starts(between)) and "at 1 PM or later and at 2 PM or earlier" in between["speak"]
    strictest = alternatives(env, starting_at_or_after="12:00", starting_after="15:00")
    assert starts(strictest)[0] == at(TOMORROW, "15:15") and strictest["window"]["from"] == "15:01"


def test_nothing_inside_the_bound_says_so_and_does_not_wander_to_other_days(env):
    none = alternatives(env, starting_after="19:45")  # a 60 minute service cannot start after 7:45 PM in a day that closes at 8
    assert none["status"] == "no_alternatives" and none["options"] == [] and none["days_searched"] == 1
    assert "starting later than 7:45 PM on" in none["speak"] and "other days" in none["speak"]
    assert none["window"]["from"] == "19:46" and "nothing outside that was searched or offered" in none["instruction"]


def test_a_bad_bound_is_asked_about(env):
    for bad in ("5pm", "25:00", "17:75", 1700):
        reply = alternatives(env, starting_after=bad)
        assert reply["status"] == "needs_clarification" and reply["reason"] == "bad_time" and not reply.get("options")


def test_without_a_bound_nothing_about_the_answer_changes(env):
    plain = alternatives(env, time="17:00")
    assert "window" not in plain and "instruction" not in plain and plain["status"] == "alternatives"


# ---------------------------------------------------------------- R-2: the independent review of ce84613 (history limit, a length alone, a named length or variant)

DEEP_TISSUE_90 = "massage-deep-tissue-massage-90"


def test_the_history_limit_comes_before_any_identity_answer_and_a_changed_number_does_not_get_round_it(env):
    ask = {"phone": WILLA_PHONE, "name": WILLA, "service_category": "massage"}
    for _ in range(3):
        assert env.call("last_completed_visit", ask, "history-limit")["status"] == "found"
    assert env.call("last_completed_visit", ask, "history-limit")["status"] == "refused"
    for other in (
        {"phone": CEDRIC_PHONE, "name": "Sedric Lowe", "service_category": "massage"},  # unmatched name on another number
        {"phone": CEDRIC_PHONE, "name": "Cedric Lowe", "service_category": "massage"},  # the right name on another number
        {"phone": WILLA_PHONE, "name": "Villa Hartwell", "service_category": "massage"},  # an unmatched name on the first number
    ):
        reply = env.call("last_completed_visit", other, "history-limit")
        assert reply["status"] == "refused" and reply["reason"] == "too_many_attempts", reply
        assert "spell" not in reply["speak"] and "Cedric" not in json.dumps(reply)
    assert env.call("last_completed_visit", {"phone": CEDRIC_PHONE, "service_category": "massage"}, "another-call")["status"] in ("found", "no_completed_visit")


def test_an_unmatched_name_counts_against_the_history_limit_too(env):
    for _ in range(3):
        reply = env.call("last_completed_visit", {"phone": WILLA_PHONE, "name": "Villa Hartwell", "service_category": "massage"}, "probe")
        assert reply["status"] == "needs_clarification" and reply["reason"] == "name_unmatched"
    assert env.call("last_completed_visit", {"phone": WILLA_PHONE, "name": "Willa Hartwell", "name_confirmed": True, "service_category": "massage"}, "probe")["status"] == "refused"


def test_guessing_names_against_a_number_is_bounded_in_every_tool(env):
    for guess in ("Villa", "Wilma", "Willow", "Wanda", "Wynn"):
        assert env.call("find_appointments", {"phone": WILLA_PHONE, "name": guess}, "guesser")["reason"] == "name_unmatched"
    stopped = env.call("find_appointments", {"phone": WILLA_PHONE, "name": WILLA}, "guesser")  # even the right name, once the limit is reached
    assert stopped["status"] == "refused" and stopped["reason"] == "too_many_attempts" and "appointments" not in stopped
    again = book_for(env, "Willa Hartwell", call_id="guesser", name_confirmed=True)[1]
    assert again["status"] == "refused" and again["reason"] == "too_many_attempts"
    assert env.call("find_appointments", {"phone": WILLA_PHONE, "name": WILLA}, "a-different-call")["status"] == "found"


@pytest.mark.parametrize("words", ["sixty minutes", "sixty minute", "a 60 minute", "an hour", "ninety minutes please", "an appointment please"])
def test_a_length_alone_is_not_a_treatment_and_the_id_does_not_stand_in_for_one(env, words):
    before = env.counts()
    for route, args in (
        ("check_slot", {"service_id": DEEP_TISSUE_60, "date": TOMORROW.isoformat(), "time": "16:00", "service_words": words}),
        ("find_alternatives", {"service_id": DEEP_TISSUE_60, "date": TOMORROW.isoformat(), "service_words": words}),
        ("book_appointment", {**env.book_args(16, service=DEEP_TISSUE_60), "service_words": words}),
    ):
        reply = env.call(route, args)
        assert reply["status"] == "needs_clarification" and reply["reason"] == "service_not_chosen" and reply["service_chosen"] is False, (route, words, reply)
        assert "confirmation_token" not in reply and not reply.get("options") and reply["speak"].startswith("Which treatment would you like")
    assert env.counts() == before


def test_the_lookup_of_a_length_alone_asks_which_treatment(env):
    reply = lookup(env, "sixty minutes")
    assert reply["status"] == "needs_clarification" and reply["reason"] == "service_required" and reply["requested_minutes"] == 60
    assert reply["speak"] == "Which treatment would you like for 1 hour?"
    assert lookup(env, "zzz")["status"] == "unknown_service", "words that are simply not understood are still 'not on the menu'"


def test_a_length_the_caller_named_must_agree_with_the_service_chosen(env):
    before = env.counts()
    wrong = env.call("book_appointment", {**env.book_args(16, service=DEEP_TISSUE_60), "service_words": "deep tissue massage ninety minutes"})
    assert wrong["status"] == "needs_clarification" and wrong["reason"] == "service_mismatch" and "confirmation_token" not in wrong
    assert "1 hour 30 minutes" in wrong["speak"] and "1 hour" in wrong["speak"], "both lengths are put to the caller"
    assert env.counts() == before
    right = env.call("book_appointment", {**env.book_args(16, service=DEEP_TISSUE_90), "service_words": "deep tissue massage ninety minutes"})
    assert right["status"] == "confirmation_required" and "1 hour 30 minutes" in right["speak"]
    same = env.call("book_appointment", {**env.book_args(15, service=DEEP_TISSUE_60), "service_words": "deep tissue massage sixty minutes"})
    assert same["status"] == "confirmation_required"
    no_length = env.call("book_appointment", {**env.book_args(14, service=DEEP_TISSUE_60), "service_words": "deep tissue massage"})
    assert no_length["status"] == "confirmation_required", "no length said: any length of that treatment is the caller's to choose later"
    other_family = env.call("check_slot", {"service_id": DEEP_TISSUE_60, "date": TOMORROW.isoformat(), "time": "16:00", "service_words": "swedish massage thirty minutes"})
    assert other_family["status"] == "needs_clarification" and other_family["reason"] == "service_mismatch"


def test_a_length_the_treatment_does_not_come_in_is_said_and_never_replaced(env):
    reply = env.call("find_alternatives", {"service_id": DEEP_TISSUE_60, "date": TOMORROW.isoformat(), "service_words": "deep tissue massage forty five minutes"})
    assert reply["status"] == "needs_clarification" and reply["reason"] == "length_not_offered" and reply["requested_minutes"] == 45
    assert "I don't have Deep Tissue Massage at 45 minutes" in reply["speak"] and reply["lengths"] == ["30 minutes", "1 hour", "1 hour 30 minutes"]
    assert not reply.get("options")


def test_sols_three_independent_reproductions(env):
    """The three reproductions from the independent review, as given."""
    for _ in range(3):
        assert env.call("last_completed_visit", {"phone": WILLA_PHONE, "name": WILLA, "service_category": "massage"}, "history-limit")["status"] == "found"
    assert env.call("last_completed_visit", {"phone": WILLA_PHONE, "name": WILLA, "service_category": "massage"}, "history-limit")["status"] == "refused"
    assert env.call("last_completed_visit", {"phone": CEDRIC_PHONE, "name": "Sedric Lowe", "service_category": "massage"}, "history-limit")["status"] == "refused"
    length_only = env.call("book_appointment", env.book_args(16, service=DEEP_TISSUE_60, service_words="sixty minutes"), "length-only")
    assert length_only["status"] == "needs_clarification" and "confirmation_token" not in length_only
    wrong_length = env.call("book_appointment", env.book_args(16, service=DEEP_TISSUE_60, service_words="deep tissue massage ninety minutes"), "wrong-length")
    assert wrong_length["status"] == "needs_clarification" and "confirmation_token" not in wrong_length
