"""Configuration, read from environment variables. No secrets are stored or accepted here.

The business id is deliberately NOT a committed default: this repository is public, and live
runs must be pointed at the test account explicitly (ARIA_BOOKSY_BUSINESS_ID).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

BOOKING_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_DIR = BOOKING_DIR / ".local"  # git-ignored: profile, ledger, evidence

CALENDAR_URL_TEMPLATE = "https://booksy.com/pro/en-us/{business_id}/calendar?date={date}&view=day&staffers=working"


class ConfigError(ValueError):
    pass


def _bool(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _int(environ: Mapping[str, str], name: str, default: int, *, minimum: int = 0) -> int:
    raw = environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


@dataclass(frozen=True)
class Config:
    business_id: str = ""
    timezone: str = "America/New_York"
    staff_name: str = "Shobs"
    service_name: str = "Aria Salon"
    service_duration_minutes: int = 150
    buffer_before_minutes: int = 0
    buffer_after_minutes: int = 0
    grid_minutes: int = 15
    min_lead_minutes: int = 60
    max_bookings_per_run: int = 1
    settle_seconds: int = 30
    local_dir: Path = field(default=DEFAULT_LOCAL_DIR)
    chromedriver_path: Optional[str] = None
    allow_driver_download: bool = False
    sign_in_wait_seconds: int = 600

    @property
    def chrome_profile_dir(self) -> Path:
        return self.local_dir / "chrome-profile"

    @property
    def ledger_path(self) -> Path:
        return self.local_dir / "ledger.json"

    @property
    def evidence_dir(self) -> Path:
        return self.local_dir / "evidence"

    def calendar_url(self, day_text: str = "today") -> str:
        self.require_business_id()
        return CALENDAR_URL_TEMPLATE.format(business_id=self.business_id, date=day_text)

    def require_business_id(self) -> str:
        if not re.fullmatch(r"\d{3,}", self.business_id or ""):
            raise ConfigError(
                "ARIA_BOOKSY_BUSINESS_ID is not set to a numeric business id. "
                "Live runs must name the test business explicitly (never committed)."
            )
        return self.business_id

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "Config":
        env = os.environ if environ is None else environ
        local_dir = Path(env["ARIA_LOCAL_DIR"]) if env.get("ARIA_LOCAL_DIR") else DEFAULT_LOCAL_DIR
        base = cls()
        return cls(
            business_id=(env.get("ARIA_BOOKSY_BUSINESS_ID") or "").strip(),
            timezone=env.get("ARIA_TIMEZONE") or base.timezone,
            staff_name=env.get("ARIA_STAFF_NAME") or base.staff_name,
            service_name=env.get("ARIA_SERVICE_NAME") or base.service_name,
            service_duration_minutes=_int(env, "ARIA_SERVICE_MINUTES", base.service_duration_minutes, minimum=5),
            buffer_before_minutes=_int(env, "ARIA_BUFFER_BEFORE_MINUTES", 0),
            buffer_after_minutes=_int(env, "ARIA_BUFFER_AFTER_MINUTES", 0),
            grid_minutes=_int(env, "ARIA_GRID_MINUTES", base.grid_minutes, minimum=1),
            min_lead_minutes=_int(env, "ARIA_MIN_LEAD_MINUTES", base.min_lead_minutes),
            max_bookings_per_run=_int(env, "ARIA_MAX_BOOKINGS_PER_RUN", base.max_bookings_per_run, minimum=1),
            settle_seconds=_int(env, "ARIA_SETTLE_SECONDS", base.settle_seconds),
            local_dir=local_dir,
            chromedriver_path=env.get("ARIA_CHROMEDRIVER_PATH") or None,
            allow_driver_download=_bool(env.get("ARIA_ALLOW_DRIVER_DOWNLOAD")),
            sign_in_wait_seconds=_int(env, "ARIA_SIGN_IN_WAIT_SECONDS", base.sign_in_wait_seconds, minimum=10),
        )

    def redacted_summary(self) -> dict:
        """Safe to print: the business id is masked to its last 3 digits."""
        masked = ("*" * max(len(self.business_id) - 3, 0) + self.business_id[-3:]) if self.business_id else "(not set)"
        if self.chromedriver_path:
            driver_note = self.chromedriver_path
        elif self.allow_driver_download:
            driver_note = "(Selenium Manager download ALLOWED)"
        else:
            driver_note = "(none; automatic download disabled)"
        return {
            "business_id": masked,
            "timezone": self.timezone,
            "staff_name": self.staff_name,
            "service_name": self.service_name,
            "service_duration_minutes": self.service_duration_minutes,
            "buffers_minutes": [self.buffer_before_minutes, self.buffer_after_minutes],
            "grid_minutes": self.grid_minutes,
            "min_lead_minutes": self.min_lead_minutes,
            "max_bookings_per_run": self.max_bookings_per_run,
            "local_dir": str(self.local_dir),
            "chromedriver": driver_note,
        }
