"""Assemble the SIMULATED-lifecycle voice endpoint: the same signed, bearer-protected Retell endpoint as the read-only demo, with the lifecycle routes and NO Booksy route.

``build_lifecycle_demo`` is the only place these tools are wired. ``cli.serve`` (the live command) never calls it, and ``demo.launch.build_demo`` (the reviewed read-only
demo) is untouched, so the three modes cannot be swapped by accident.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..config import Config
from ..demo import DEMO_BUSINESS_ID, SPA_TIMEZONE
from ..demo.driver import DemoCalendarDriver
from ..voice.retell_http import RetellEndpoint
from .lifecycle import Lifecycle
from .preference_tools import PREFERENCE_ROUTES, PreferenceTools
from .tools import LIFECYCLE_ROUTES, LifecycleTools


def build_lifecycle_demo(
    api_key: str, lifecycle: Lifecycle, *, bearer_token: Optional[str] = None, log: Callable[[str], None] = lambda message: None,
    driver: Optional[Any] = None, contact: Optional[str] = None, preferences: bool = False, **tool_kwargs: Any,
):
    """(endpoint, driver). ``driver`` may be a page-reading driver pointed at the demo site; by default it reads the shared calendar directly.
    ``preferences`` adds the three preferred-technician routes (opt-in: without it those addresses do not exist and nothing about the lifecycle changes)."""
    cfg = Config(business_id=DEMO_BUSINESS_ID, timezone=SPA_TIMEZONE)
    driver = driver or DemoCalendarDriver(lifecycle.dataset, timezone=SPA_TIMEZONE, clock=lifecycle.clock)
    tools = (PreferenceTools if preferences else LifecycleTools)(lifecycle, cfg, driver, lifecycle.clock, contact=contact, **tool_kwargs)
    return RetellEndpoint(tools, api_key, bearer_token=bearer_token, routes=PREFERENCE_ROUTES if preferences else LIFECYCLE_ROUTES, log=log), driver
