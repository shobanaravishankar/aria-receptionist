"""Open the simulated appointment world: the store, the calendar that draws it, the judge that guards it, and the seeded fictional clients.

The demo clock decides 'now' for everything here (the store's past/started checks, the judge's minimum notice, the seeds' dates), so a ``--today`` demo date is
one consistent world. Nothing here talks to Booksy, a browser or the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from ..config import DEFAULT_LOCAL_DIR
from ..demo import SPA_TIMEZONE
from ..demo.dataset import DemoDataset
from ..demo.launch import demo_clock
from .judge import PlacementJudge
from .models import to_epoch
from .overlay import StoreOverlay
from .seed import SeedReport, seed_demo
from .store import AppointmentStore

STORE_FILE = "appointments.sqlite3"


def default_store_path(local_dir: Path = DEFAULT_LOCAL_DIR) -> Path:
    return Path(local_dir) / "demo-appointments" / STORE_FILE  # under the git-ignored local folder: it holds invented clients, but still no reason to commit it


@dataclass
class Lifecycle:
    store: AppointmentStore
    dataset: DemoDataset
    judge: PlacementJudge
    tz: ZoneInfo
    clock: Callable[[], datetime]
    seeded: Optional[SeedReport] = None

    def maintain(self) -> SeedReport:
        """Daily upkeep for a server that stays up for days: fictional visits whose time has passed are settled, and the fictional upcoming ones are topped up, by the same rules as a
        start. A client's own appointment is never touched. Safe to run any number of times."""
        self.store.settle_past_seeds()
        self.seeded = seed_demo(self.store, self.judge, self.clock, self.tz)
        return self.seeded


def open_lifecycle(
    path: Path, *, today: Optional[date] = None, timezone: str = SPA_TIMEZONE, real_now: Optional[Callable[[], datetime]] = None, seed: bool = True,
    min_lead_minutes: int = 60, grid_minutes: int = 15, diagnostic_unknown_hours=(),
) -> Lifecycle:
    tz = ZoneInfo(timezone)
    clock = demo_clock(today, tz, real_now)
    store = AppointmentStore(path, clock=lambda: to_epoch(clock()))
    store.settle_past_seeds()  # the fictional fixtures only: a caller's appointment is never marked completed for them
    # with no explicit demo date the calendar's 'today' follows the real date in the spa's time zone, however long the server stays up
    dataset = DemoDataset(clock().date(), overlay=StoreOverlay(store, tz), diagnostic_unknown_hours=diagnostic_unknown_hours,
                          today_source=(lambda: clock().date()) if today is None else None)
    judge = PlacementJudge(dataset, tz=tz, clock=clock, min_lead_minutes=min_lead_minutes, grid_minutes=grid_minutes)
    return Lifecycle(store, dataset, judge, tz, clock, seed_demo(store, judge, clock, tz) if seed else None)
