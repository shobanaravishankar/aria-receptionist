"""The ONE supported way to assemble the voice endpoint, shared by the CLI and by any wrapper that bypasses it.

Tools and routes are built together from one decision, so a wrapper cannot end up with a server that cannot write but still
exposes a booking route (or the reverse). ``RetellEndpoint`` also refuses such a pair on its own, as a second line.

    availability only (the demo):  build_endpoint(..., booking_approved=False)
        tools: availability_only=True, booking disabled, no option ids; routes: lookup_service, check_slot, find_alternatives
    booking test line:             build_endpoint(..., booking_approved=True)
        tools: booking enabled; routes: all four
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional

from ..catalog.bookable import BookableRegistry
from ..config import Config
from .retell_http import READ_ONLY_ROUTES, ROUTES, RetellEndpoint
from .tools import VoiceTools


def build_tools(
    cfg: Config,
    driver: Any,
    service_factory: Callable[..., Any],
    clock: Callable[[], datetime],
    *,
    booking_approved: bool,
    registry: Optional[BookableRegistry] = None,
    **kwargs: Any,
) -> VoiceTools:
    return VoiceTools(
        cfg, driver, service_factory, clock, registry=registry, require_service_id=True,
        booking_enabled=booking_approved, availability_only=not booking_approved, **kwargs,
    )


def build_endpoint(
    cfg: Config,
    driver: Any,
    service_factory: Callable[..., Any],
    clock: Callable[[], datetime],
    api_key: str,
    *,
    booking_approved: bool = False,
    bearer_token: Optional[str] = None,
    registry: Optional[BookableRegistry] = None,
    log: Callable[[str], None] = lambda message: None,
    **tool_kwargs: Any,
) -> RetellEndpoint:
    """The signed endpoint for ONE mode. ``booking_approved`` defaults to False: the safe, availability-only mode."""
    tools = build_tools(cfg, driver, service_factory, clock, booking_approved=booking_approved, registry=registry, **tool_kwargs)
    routes = ROUTES if booking_approved else READ_ONLY_ROUTES
    return RetellEndpoint(tools, api_key, bearer_token=bearer_token, routes=routes, log=log)
