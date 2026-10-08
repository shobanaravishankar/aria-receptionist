"""Hard guards that run before any appointment is created.

These exist because the repository is public and the target is a real Booksy account (even if a
test one): every booking must be recognisable as a fictional test, aimed at the right business,
in the future, and capped per run. A violation refuses the booking; it never "fixes" it.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .config import Config
from .models import AppointmentSpec, add_minutes, to_utc

NOTE_PREFIX = "ARIA TEST - fictional appointment, no real customer or payment, safe to cancel after testing."
REF_PATTERN = re.compile(r"\bRef: (ARIA-[0-9A-F]{8})\b")


class SafetyViolation(Exception):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def build_note(ref: str) -> str:
    return f"{NOTE_PREFIX} Ref: {ref}"


def extract_ref(note: str) -> Optional[str]:
    match = REF_PATTERN.search(note or "")
    return match.group(1) if match else None


def run_limit_reason(cfg: Config, bookings_this_run: int) -> Optional[str]:
    if bookings_this_run >= cfg.max_bookings_per_run:
        return f"run limit of {cfg.max_bookings_per_run} booking(s) reached"
    return None


def check_request(
    cfg: Config,
    spec: AppointmentSpec,
    *,
    now: datetime,
    observed_business_id: str,
    bookings_this_run: int,
) -> None:
    """Raise SafetyViolation listing every failed rule (not just the first)."""
    reasons: list[str] = []

    if not cfg.business_id:
        reasons.append("no business id configured")
    elif observed_business_id != cfg.business_id:
        reasons.append("signed-in business does not match the configured test business")

    if spec.staff.casefold() != cfg.staff_name.casefold():
        reasons.append("staff is not the configured test staff member")
    if spec.service_name != cfg.service_name:
        reasons.append("service is not the configured test service")
    if spec.duration_minutes != cfg.service_duration_minutes:
        reasons.append("duration differs from the configured service duration")

    if to_utc(spec.start) < to_utc(add_minutes(now, cfg.min_lead_minutes)):
        reasons.append("slot is not far enough in the future")

    if not spec.note.startswith(NOTE_PREFIX) or extract_ref(spec.note) is None:
        reasons.append("internal note must carry the ARIA TEST marker and a reference")

    limit = run_limit_reason(cfg, bookings_this_run)
    if limit:
        reasons.append(limit)

    if reasons:
        raise SafetyViolation(reasons)
