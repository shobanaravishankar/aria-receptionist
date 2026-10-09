"""The allowlist of services Aria may BOOK. Separate from, and stricter than, the website catalogue.

A service is bookable only if it is mapped to a real Booksy service by exact name with a duration, optional buffers and a
staff-eligibility set. Duration, price and the Booksy service name used for booking come ONLY from here: never from the
caller and never from the voice model. A website service with no entry here can be discussed and never booked.

Today exactly one service is verified against the real test account: the fictional ``Aria Salon`` (150 minutes) with the
single staff member. Any other entry is synthetic (tests) or must be verified read-only against the real account first.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Optional

from ..config import Config
from .lookup import Catalog, DEFAULT_CATALOG

DEFAULT_SERVICE_ID = "test-aria-salon"


def staff_key(name: str) -> str:
    """A comparable key for a staff display name. (Stable staff ids replace this once the roster is verified.)"""
    return " ".join((name or "").casefold().split())


@dataclass(frozen=True)
class BookableService:
    service_id: str
    booksy_name: str  # the exact service name in Booksy, chosen from the service list by exact match
    duration_minutes: int
    buffer_before_minutes: int = 0
    buffer_after_minutes: int = 0
    # staff keys allowed to perform it; None means "any staff member the calendar verifiably lists"; empty means nobody
    eligible_staff: Optional[frozenset] = None
    # STABLE staff ids allowed to perform it (from the calendar's own ids). When set it takes precedence over names: a person is
    # eligible only if their column's id is listed, so a rename, or two people with one display name, cannot change who qualifies.
    eligible_staff_ids: Optional[frozenset] = None
    verified: bool = False  # confirmed against the real account (name, duration, eligibility), not just configured
    catalog_item: Optional[str] = None  # the website item it corresponds to, if any
    test_only: bool = False
    price_usd: Optional[int] = None

    def eligible(self, staff_name: str, staff_id: str = "") -> bool:
        if self.eligible_staff_ids is not None:
            return bool(staff_id) and staff_id in self.eligible_staff_ids  # a person whose id is unknown is never eligible
        return self.eligible_staff is None or staff_key(staff_name) in self.eligible_staff

    def booking_config(self, cfg: Config, staff_name: str) -> Config:
        """The configuration BookingService/safety use for THIS service and THIS staff member (trusted data only)."""
        return replace(
            cfg,
            service_name=self.booksy_name,
            service_duration_minutes=self.duration_minutes,
            buffer_before_minutes=self.buffer_before_minutes,
            buffer_after_minutes=self.buffer_after_minutes,
            staff_name=staff_name,
        )


class BookableRegistry:
    def __init__(self, services: Iterable[BookableService], *, catalog: Catalog = DEFAULT_CATALOG, default_id: Optional[str] = None):
        self._services = {}
        for service in services:
            if service.service_id in self._services:
                raise ValueError(f"duplicate bookable service id {service.service_id!r}")
            if service.duration_minutes <= 0:
                raise ValueError(f"{service.service_id}: duration must be positive")
            if service.catalog_item is not None:
                item = catalog.get(service.catalog_item)
                if item is None:
                    raise ValueError(f"{service.service_id}: unknown catalogue item {service.catalog_item!r}")
                if item.duration_minutes is not None and item.duration_minutes != service.duration_minutes:
                    raise ValueError(
                        f"{service.service_id}: the Booksy duration ({service.duration_minutes}) differs from the website's listed "
                        f"duration ({item.duration_minutes}) for {item.item_id}; reconcile before enabling"
                    )
            self._services[service.service_id] = service
        if default_id is not None and default_id not in self._services:
            raise ValueError(f"default service {default_id!r} is not in the registry")
        self.default_id = default_id

    def get(self, service_id) -> Optional[BookableService]:
        return self._services.get(service_id) if isinstance(service_id, str) else None

    def is_bookable(self, service_id) -> bool:
        return self.get(service_id) is not None

    def ids(self) -> list[str]:
        return list(self._services)

    def for_catalog_item(self, item_id: str) -> Optional[BookableService]:
        """The bookable service mapped to a website item, if any (a website item with none can only be discussed)."""
        return next((svc for svc in self._services.values() if svc.catalog_item == item_id), None)

    @classmethod
    def from_config(cls, cfg: Config) -> "BookableRegistry":
        """The real, verified registry today: only the fictional test service with the single configured staff member."""
        only = BookableService(
            DEFAULT_SERVICE_ID, cfg.service_name, cfg.service_duration_minutes, cfg.buffer_before_minutes, cfg.buffer_after_minutes,
            eligible_staff=frozenset({staff_key(cfg.staff_name)}), verified=True, test_only=True,
        )
        return cls([only], default_id=DEFAULT_SERVICE_ID)
