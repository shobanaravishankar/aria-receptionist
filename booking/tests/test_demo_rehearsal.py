"""The rehearsal sheet: examples for a supervised walk-through, each with the answer the REAL tools give. Nothing is forced and nothing is hard-coded: the sheet must follow
the schedule it is given, agree with the engine when it is asked again independently, never open the demo's own appointment file, and say what it is. No browser, no network."""

from __future__ import annotations

import functools
import json
import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aria_booking import cli
from aria_booking.appointments.launch import build_lifecycle_demo
from aria_booking.appointments.lifecycle import open_lifecycle
from aria_booking.appointments.seed import SEEDED_CLIENTS
from aria_booking.demo import rehearsal
from aria_booking.demo.dataset import ROSTER, DemoDataset, DayPlan, StaffPlan
from aria_booking.demo.rehearsal import AVAILABLE_SERVICE, BLOCKED_SERVICE, INELIGIBLE_SERVICE, rehearsal_plan, render_text

TZ = ZoneInfo("America/New_York")
THURSDAY = date(2026, 10, 22)
WEEK = [THURSDAY + timedelta(days=n) for n in range(-8, 8)]
plan_for = functools.lru_cache(maxsize=None)(lambda day: rehearsal_plan(day))  # one sheet per date: the checks below only read it


def independent_tools(day: date, folder: Path):
    now = datetime(day.year, day.month, day.day, 10, 0, tzinfo=TZ)
    lifecycle = open_lifecycle(folder / "again.sqlite3", today=day, real_now=lambda: now)
    endpoint, _driver = build_lifecycle_demo("independent-key", lifecycle, bearer_token=None)
    return lifecycle, endpoint._tools


# ---------------------------------------------------------------- deterministic, and following the schedule


def test_the_same_date_always_gives_the_same_sheet_and_text():
    assert rehearsal_plan(THURSDAY) == rehearsal_plan(THURSDAY)
    assert render_text(rehearsal_plan(THURSDAY)) == render_text(rehearsal_plan(THURSDAY))


def test_different_dates_give_their_own_sheets():
    sheets = {d: plan_for(d) for d in WEEK[:6]}
    assert len({json.dumps(s["asked_day"], sort_keys=True) for s in sheets.values()}) == 6
    for d, s in sheets.items():
        assert s["demo_date"] == d.isoformat() and s["asked_day"]["date"] == (d + timedelta(days=1)).isoformat() and s["day_after"]["date"] == (d + timedelta(days=2)).isoformat()


@pytest.mark.parametrize("day", WEEK)
def test_every_example_agrees_with_the_engine_when_it_is_asked_again_independently(day, tmp_path):
    sheet = plan_for(day)
    lifecycle, tools = independent_tools(day, tmp_path)
    asked = (day + timedelta(days=1)).isoformat()
    registry = lifecycle.dataset.registry
    examples = sheet["examples"]

    def ask(service_id, at, staff=None):
        args = {"service_id": service_id, "date": asked, "time": at}
        if staff:
            args["staff"] = staff
        return tools.check_slot("again", args)

    if examples["available"]:
        item = examples["available"]
        reply = ask(AVAILABLE_SERVICE, item["time"], item["technician"])
        assert reply["status"] == item["status"] == "available" and reply["speak"] == item["answer"]
        service = registry.get(AVAILABLE_SERVICE)
        assert any(s.name == item["technician"] and service.eligible(s.name, s.staff_id) for s in ROSTER), "she really does that service"
    if examples["blocked"]:
        item = examples["blocked"]
        reply = ask(BLOCKED_SERVICE, item["time"])
        assert reply["status"] == item["status"] != "available" and reply["speak"] == item["answer"]
    if examples["ineligible"]:
        item = examples["ineligible"]
        reply = ask(INELIGIBLE_SERVICE, item["time"], item["technician"])
        assert reply["status"] == item["status"] and reply.get("reason") == item["reason"] == "staff_not_eligible" and reply["speak"] == item["answer"]
        service = registry.get(INELIGIBLE_SERVICE)
        staff = next(s for s in ROSTER if s.name == item["technician"])
        assert not service.eligible(staff.name, staff.staff_id), "she really does not do that service"
        assert item["technician"] in {w["name"] for w in sheet["asked_day"]["working"]}, "and she is working that day"


@pytest.mark.parametrize("day", WEEK)
def test_the_cleanup_boundary_is_blocked_only_by_the_cleanup_and_clears_right_after_it(day):
    cleanup = plan_for(day)["examples"]["cleanup_boundary"]
    if cleanup is None:
        pytest.skip("this day has no technician with room for the whole sequence")
    service = DemoDataset(day).registry.get(AVAILABLE_SERVICE)
    assert cleanup["cleanup_minutes"] == service.buffer_after_minutes > 0
    assert cleanup["at_end"]["time"] == cleanup["booked_end"] and cleanup["at_end"]["status"] != "available"
    assert "get ready" in cleanup["at_end"]["answer"], "it says why: the technician is still cleaning up"
    start_minutes = int(cleanup["booked_start"][:2]) * 60 + int(cleanup["booked_start"][3:])
    after_minutes = int(cleanup["after_cleanup"]["time"][:2]) * 60 + int(cleanup["after_cleanup"]["time"][3:])
    assert after_minutes - start_minutes == service.duration_minutes + service.buffer_after_minutes
    assert cleanup["after_cleanup"]["status"] == "available"


def test_the_sheet_follows_the_schedule_it_is_given_and_nothing_is_forced_open(monkeypatch):
    """Only Tamsin works, with nothing booked: the open time must be hers, and the sheet must not invent a technician or a busy time."""

    def only_tamsin(self, day):
        tamsin = next(s for s in self.roster if s.name == "Tamsin")
        return DayPlan(day, (StaffPlan(tamsin, (600, 1200), (), ()),))

    monkeypatch.setattr(DemoDataset, "base_day_plan", only_tamsin)
    sheet = rehearsal_plan(THURSDAY)
    assert [w["name"] for w in sheet["asked_day"]["working"]] == ["Tamsin"]
    assert sheet["examples"]["available"]["technician"] == "Tamsin"
    assert sheet["examples"]["ineligible"] is None or sheet["examples"]["ineligible"]["technician"] == "Tamsin"
    assert sheet["examples"]["blocked"] is None or sheet["examples"]["blocked"]["time"] >= "17:00", "with an empty day, only the closing time can block a long facial"


def test_a_day_with_nobody_working_says_there_is_no_such_example_instead_of_making_one_up(monkeypatch):
    monkeypatch.setattr(DemoDataset, "base_day_plan", lambda self, day: DayPlan(day, ()))
    sheet = rehearsal_plan(THURSDAY)
    assert sheet["examples"]["available"] is None and sheet["examples"]["ineligible"] is None and sheet["examples"]["cleanup_boundary"] is None
    assert "(no such example on this day)" in render_text(sheet) and "(not available on this day)" in render_text(sheet)


# ---------------------------------------------------------------- safe and honest


def test_the_sheet_never_opens_the_demos_own_appointment_file(monkeypatch):
    opened = []
    real = rehearsal.open_lifecycle

    def spy(path, **kwargs):
        opened.append(Path(path))
        return real(path, **kwargs)

    monkeypatch.setattr(rehearsal, "open_lifecycle", spy)
    rehearsal_plan(THURSDAY)
    assert len(opened) == 1
    tmp_root = Path(tempfile.gettempdir()).resolve()
    assert tmp_root in opened[0].resolve().parents and ".local" not in opened[0].parts and not opened[0].exists(), "a temporary file, deleted afterwards"


def test_the_identities_are_the_fictional_seeded_ones_with_reserved_numbers():
    sheet = rehearsal_plan(THURSDAY)
    assert [(p["name"], p["phone"]) for p in sheet["identities"]] == list(SEEDED_CLIENTS)
    assert all(re.fullmatch(r"\+1212555\d{4}", p["phone"]) and 100 <= int(p["phone"][-4:]) <= 199 for p in sheet["identities"]), "555-01xx: reaches no real phone"
    text = render_text(sheet)
    assert "212-555-0142" in text and "shares this number" in text and "212-555-0177" in text


def test_the_text_says_what_it_is_and_carries_no_secret_or_real_business_detail():
    text = render_text(rehearsal_plan(THURSDAY))
    for needle in ("computed now from the real tools, not a script", "Fictional identities", "An open time", "A known blocked time", "A technician who does not do the service", "The cleanup boundary",
                   "she asks for the full number", "she asks for the full name", "the original stays", "only that one goes"):
        assert needle in text, needle
    assert not re.search(r"(?i)api[_ ]?key|bearer|token|secret|password|@|https?://", text)


def test_the_seeded_references_listed_are_those_a_real_start_makes_for_that_date(tmp_path):
    sheet = rehearsal_plan(THURSDAY)
    lifecycle, _tools = independent_tools(THURSDAY, tmp_path)
    assert sheet["seeded_upcoming"] == [{"template": t, "reference": r} for t, r in lifecycle.seeded.upcoming_added]


# ---------------------------------------------------------------- the command


def test_the_command_prints_the_text_or_json_without_a_key_a_browser_or_the_local_folder(tmp_path):
    lines: list = []
    code = cli.main(["demo-rehearsal", "--today", "2026-10-22"], environ={"ARIA_LOCAL_DIR": str(tmp_path / "local")}, out=lines.append)
    assert code == cli.EXIT_OK and "REHEARSAL SHEET for demo date 2026-10-22" in lines[0]
    lines.clear()
    assert cli.main(["demo-rehearsal", "--today", "2026-10-22", "--format", "json"], environ={}, out=lines.append) == cli.EXIT_OK
    data = json.loads(lines[0])
    assert data["demo_date"] == "2026-10-22" and set(data["examples"]) == {"available", "blocked", "ineligible", "cleanup_boundary"}
    assert not (tmp_path / "local").exists(), "nothing was written to the local folder"


def test_the_command_refuses_a_bad_date_plainly():
    lines: list = []
    assert cli.main(["demo-rehearsal", "--today", "tomorrow"], environ={}, out=lines.append) == cli.EXIT_REFUSED and lines == ["refused: --today must look like 2026-10-22."]
