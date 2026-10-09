"""Metadata-only phase timing: WHICH phase took HOW LONG, and nothing else.

Used to find out where the seconds go in an availability answer (waiting for the browser lock, loading the page, waiting for it to
paint, capturing it, parsing it, searching for alternatives) so a speed claim can rest on measurements, not guesses. A timer holds
phase NAMES (a fixed vocabulary chosen by the code) and durations in milliseconds. It never holds arguments, ids, names, page
content, headers, signatures or keys, and ``summary()`` is safe to put in a log line.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from contextlib import contextmanager
from typing import Callable, Iterator

# The only words a phase may be called. Anything else is refused, so a careless call can never smuggle content into a log.
PHASES = frozenset({
    "lock_wait", "read", "search", "total",
    "navigate", "page_ready", "paint_wait", "capture", "roster", "parse", "click_nav",
})


class PhaseTimer:
    def __init__(self, monotonic: Callable[[], float] = time.monotonic):
        self._mono = monotonic
        self._start = monotonic()
        self.ms: "OrderedDict[str, float]" = OrderedDict()
        self.counts: dict[str, int] = {}

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if name not in PHASES:
            raise ValueError(f"unknown timing phase {name!r}")
        began = self._mono()
        try:
            yield
        finally:
            self.add(name, (self._mono() - began) * 1000.0)

    def add(self, name: str, milliseconds: float) -> None:
        if name not in PHASES:
            raise ValueError(f"unknown timing phase {name!r}")
        self.ms[name] = self.ms.get(name, 0.0) + max(0.0, float(milliseconds))
        self.counts[name] = self.counts.get(name, 0) + 1

    def merge(self, other: dict) -> None:
        """Fold another component's whole-millisecond phase totals (from its own timer) into this one."""
        for name, value in other.items():
            if name in PHASES and name != "total" and isinstance(value, (int, float)):
                self.add(name, value)

    def total_ms(self) -> float:
        return (self._mono() - self._start) * 1000.0

    def as_dict(self) -> dict[str, int]:
        out = {name: int(round(value)) for name, value in self.ms.items()}
        out["total"] = int(round(self.total_ms()))
        return out

    def summary(self) -> str:
        parts = [f"{name}={value}ms" + (f"(x{self.counts[name]})" if self.counts.get(name, 0) > 1 else "") for name, value in self.as_dict().items() if name != "total"]
        return f"total={int(round(self.total_ms()))}ms" + (" " + " ".join(parts) if parts else "")
