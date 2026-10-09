"""The REVIEWED mapping table: which real Booksy services (and lengths) Aria may check availability for, and which staff ids perform them.

Why a table and not live scraping: the account's services and who performs them change rarely, reading them means many page loads
(a staff member may have dozens of services that load as you scroll), and Booksy should not be hit unnecessarily. So the facts are
captured ONCE by a read-only discovery run, reviewed by a person, saved to a local file, and the voice server only reads the file.

What is trusted, and when:
  * a row counts as VERIFIED only if a person marked it so, named themselves and dated the review; anything else is listed but is not
    available to check (the voice can describe it from the website, never check or book it);
  * the table has a capture date and a maximum age; once stale, EVERY row is treated as unverified until it is refreshed and re-reviewed;
  * eligibility is by stable staff id (the calendar's own ids), so renames do not matter and a person missing from the live calendar
    simply does not appear;
  * a row that disagrees with the website's listed length for its catalogue item is refused until reconciled (BookableRegistry checks).
The file is local and git-ignored (it holds real staff ids). A synthetic example lives in the repository for tests and documentation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .bookable import BookableRegistry, BookableService
from .lookup import DEFAULT_CATALOG, Catalog

SUPPORTED_VERSION = 1
DEFAULT_MAX_AGE_DAYS = 14


class MappingTableError(ValueError):
    """The table is malformed or inconsistent. The server refuses to start rather than guess."""


@dataclass(frozen=True)
class TableReport:
    captured_at: datetime
    age_days: float
    stale: bool
    rows: int
    verified: int
    unverified: tuple[str, ...]  # service ids that are listed but not usable, with the reason

    def summary(self) -> str:
        state = "STALE: every row is treated as unverified until refreshed" if self.stale else "fresh"
        return f"{self.rows} rows, {self.verified} verified for availability checks, {len(self.unverified)} not usable; table is {state} ({self.age_days:.1f} days old)"


def _text(row: dict, key: str, where: str, *, required: bool = True) -> str:
    value = row.get(key)
    if value is None or value == "":
        if required:
            raise MappingTableError(f"{where}: '{key}' is required")
        return ""
    if not isinstance(value, str):
        raise MappingTableError(f"{where}: '{key}' must be text")
    return value.strip()


def _int(row: dict, key: str, where: str, *, minimum: int, default: Optional[int] = None) -> int:
    value = row.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MappingTableError(f"{where}: '{key}' must be a whole number >= {minimum}")
    return value


def _parse_instant(value: Any, where: str) -> datetime:
    if not isinstance(value, str):
        raise MappingTableError(f"{where}: must be an ISO date-time with a UTC offset")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MappingTableError(f"{where}: not an ISO date-time ({value!r})") from exc
    if moment.tzinfo is None:
        raise MappingTableError(f"{where}: must include a UTC offset")
    return moment


def build_registry(data: Any, *, now: datetime, catalog: Catalog = DEFAULT_CATALOG) -> tuple[BookableRegistry, TableReport]:
    """Validate a parsed table and build the registry the voice tools use. Raises MappingTableError for anything malformed."""
    if not isinstance(data, dict):
        raise MappingTableError("the mapping table must be a JSON object")
    if data.get("version") != SUPPORTED_VERSION:
        raise MappingTableError(f"unsupported mapping table version {data.get('version')!r}; expected {SUPPORTED_VERSION}")
    captured = _parse_instant(data.get("captured_at"), "captured_at")
    max_age = _int(data, "max_age_days", "table", minimum=1, default=DEFAULT_MAX_AGE_DAYS)
    age = now - captured
    if age < timedelta(minutes=-5):
        raise MappingTableError("captured_at is in the future; refusing a table whose date cannot be trusted")
    stale = age > timedelta(days=max_age)
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise MappingTableError("'rows' must be a non-empty list")

    services: list[BookableService] = []
    unusable: list[str] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        where = f"rows[{index}]"
        if not isinstance(row, dict):
            raise MappingTableError(f"{where}: must be an object")
        service_id = _text(row, "service_id", where)
        if service_id in seen:
            raise MappingTableError(f"{where}: duplicate service_id {service_id!r}")
        seen.add(service_id)
        where = f"rows[{index}] ({service_id})"
        name = _text(row, "booksy_name", where)
        duration = _int(row, "duration_minutes", where, minimum=5)
        before = _int(row, "buffer_before_minutes", where, minimum=0, default=0)
        after = _int(row, "buffer_after_minutes", where, minimum=0, default=0)
        ids = row.get("eligible_staff_ids")
        if not isinstance(ids, list) or any(not isinstance(i, str) or not i.isdigit() for i in ids):
            raise MappingTableError(f"{where}: 'eligible_staff_ids' must be a list of numeric staff ids (an empty list means nobody)")
        if len(set(ids)) != len(ids):
            raise MappingTableError(f"{where}: 'eligible_staff_ids' repeats an id")
        catalog_item = _text(row, "catalog_item", where, required=False) or None
        price = row.get("price_usd")
        if price is not None and (isinstance(price, bool) or not isinstance(price, int) or price <= 0):
            raise MappingTableError(f"{where}: 'price_usd' must be a positive whole number or omitted")
        claimed = row.get("verified")
        if not isinstance(claimed, bool):
            raise MappingTableError(f"{where}: 'verified' must be true or false")

        reason = ""
        if claimed:
            reviewer = _text(row, "reviewed_by", where, required=False)
            reviewed_on = _text(row, "reviewed_on", where, required=False)
            if not reviewer or not reviewed_on:
                raise MappingTableError(f"{where}: a verified row must name who reviewed it (reviewed_by) and when (reviewed_on)")
            try:
                date.fromisoformat(reviewed_on)
            except ValueError as exc:
                raise MappingTableError(f"{where}: reviewed_on must be an ISO date") from exc
            if stale:
                reason = "table is stale"
        else:
            reason = "not yet reviewed"
        usable = claimed and not reason
        if not usable:
            unusable.append(f"{service_id}: {reason}")
        try:
            services.append(
                BookableService(
                    service_id, name, duration, before, after, eligible_staff_ids=frozenset(ids), verified=usable,
                    catalog_item=catalog_item, price_usd=price,
                )
            )
        except ValueError as exc:
            raise MappingTableError(f"{where}: {exc}") from exc
    try:
        registry = BookableRegistry(services, catalog=catalog)
    except ValueError as exc:
        raise MappingTableError(str(exc)) from exc
    report = TableReport(captured, age.total_seconds() / 86400, stale, len(services), sum(1 for s in services if s.verified), tuple(unusable))
    return registry, report


def load_mapping_table(path: Path | str, *, now: datetime, catalog: Catalog = DEFAULT_CATALOG) -> tuple[BookableRegistry, TableReport]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MappingTableError(f"mapping table not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MappingTableError(f"mapping table is not valid JSON: {exc}") from exc
    return build_registry(data, now=now, catalog=catalog)
