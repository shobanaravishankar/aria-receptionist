"""Reading a staff member's Services tab: which Booksy services they perform. Parsing is pure; the collector drives an injected
``scroll`` and ``capture`` so it can be tested with a fake lazy list and used with the real page by a human-run discovery command.

Evidence (read-only look at a live staff detail page, by Sol):
  * the tab label ``[data-testid="services"]`` reads "Services (N)": N is the number of PARENT services this person performs;
  * the list is ``div[data-testid="services-option-list"]``, an infinite scroll: about a dozen rows at first, more rows are appended as it
    scrolls (12, 27, 36, 51, 61, 63 in one look), earlier rows are kept, and the DOM can briefly show the OLD count right after a scroll;
  * each row is ``div[data-testid="services-list-item-<numeric service id>"]`` with name / duration / price children
    (``_optionName_``, ``.duration`` / ``_optionDuration_``, ``_optionPrice_``); a parent row shows a summary such as "30min+" and
    "from $49.00", NOT a specific length. Names can contain repeated whitespace;
  * variant nodes are NOT inside the parent row and their ids are NOT established: this module does not read or invent variants.
A list is COMPLETE only when its unique row ids reconcile with N. A filtered search result, a list that stopped growing short of N, or a
page without the total is INCOMPLETE and must never be used as the person's full eligibility. This module only reads and scrolls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Optional

TOTAL_RE = re.compile(r"Services\s*\((\d+)\)")
ROW_RE = re.compile(r"^services-list-item-(\d+)$")

# Scrolls the services list to its end. A scroll inside an already-open page: no navigation, no click, nothing is changed.
SCROLL_SERVICES_JS = r"""
const el = document.querySelector('[data-testid="services-option-list"]');
if (!el) return false;
el.scrollTop = el.scrollHeight;
return true;
"""

Node = dict[str, Any]


def _clean(text: Optional[str]) -> str:
    return " ".join((text or "").split())


@dataclass(frozen=True)
class ListedService:
    service_id: str
    name: str
    duration_text: str  # the parent's summary as shown ("30min+"), not a specific length
    price_text: str  # as shown ("from $49.00")


@dataclass(frozen=True)
class ServiceList:
    expected_total: Optional[int]
    services: tuple[ListedService, ...]
    problems: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return self.expected_total is not None and len(self.services) == self.expected_total and not self.problems

    def ids(self) -> frozenset:
        return frozenset(s.service_id for s in self.services)


def _subtree(nodes: list[Node], index: int) -> list[Node]:
    depth = nodes[index]["depth"]
    out = []
    for node in nodes[index + 1:]:
        if node["depth"] <= depth:
            break
        out.append(node)
    return out


def _cls(node: Node, needle: str) -> bool:
    return any(needle in c for c in node.get("cls") or [])


def parse_services(nodes: Iterable[Node]) -> ServiceList:
    nodes = list(nodes)
    problems: list[str] = []
    total: Optional[int] = None
    for i, node in enumerate(nodes):
        if node.get("testid") == "services":
            texts = [node.get("text") or ""] + [n.get("text") or "" for n in _subtree(nodes, i)]
            match = TOTAL_RE.search(_clean(" ".join(texts)))
            if match:
                total = int(match.group(1))
                break
    if total is None:
        problems.append("the Services (N) total was not found, so completeness cannot be established")
    list_index = next((i for i, n in enumerate(nodes) if n.get("testid") == "services-option-list"), None)
    if list_index is None:
        return ServiceList(total, (), tuple(problems + ["the services list was not found"]))

    rows: list[ListedService] = []
    seen: set[str] = set()
    for i in range(list_index + 1, list_index + 1 + len(_subtree(nodes, list_index))):
        match = ROW_RE.match(nodes[i].get("testid") or "")
        if not match:
            continue
        service_id = match.group(1)
        if service_id in seen:
            problems.append(f"service id {service_id} appears more than once")
            continue
        seen.add(service_id)
        sub = _subtree(nodes, i)
        name = next((_clean(n.get("text")) for n in sub if _cls(n, "_optionName_") and n.get("text")), "")
        duration = next((_clean(n.get("text")) for n in sub if (_cls(n, "_optionDuration_") or "duration" in (n.get("cls") or [])) and n.get("text")), "")
        price = next((_clean(n.get("text")) for n in sub if _cls(n, "_optionPrice_") and n.get("text")), "")
        if not name:
            problems.append(f"service id {service_id} has no readable name")
            continue
        rows.append(ListedService(service_id, name, duration, price))
    if total is not None and len(rows) > total:
        problems.append(f"{len(rows)} rows but the tab says {total}; the list and the total disagree")
    return ServiceList(total, tuple(rows), tuple(problems))


def collect_services(
    capture: Callable[[], Iterable[Node]],
    scroll: Callable[[], bool],
    *,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    settle_seconds: float = 2.5,
    poll_seconds: float = 0.4,
    max_seconds: float = 90.0,
    max_stagnant_rounds: int = 3,
    max_rounds: int = 80,
) -> ServiceList:
    """Scroll until the list reconciles with its total, or give up with an INCOMPLETE list that says why.

    Never stops on an unchanged DOM right after a scroll (lazy rendering lags): it keeps looking for up to ``settle_seconds`` per
    scroll, and gives up only after ``max_stagnant_rounds`` scrolls in a row that added nothing. Bounded by time and rounds, never
    retries a page load, and reads/scrolls only."""
    deadline = monotonic() + max_seconds
    current = parse_services(capture())
    stagnant = rounds = 0
    while not current.complete:
        if monotonic() >= deadline:
            return replace(current, problems=current.problems + (f"gave up after {int(max_seconds)} seconds at {len(current.services)} rows",))
        rounds += 1
        if rounds > max_rounds:
            return replace(current, problems=current.problems + (f"gave up after {max_rounds} scrolls at {len(current.services)} rows",))
        before = len(current.services)
        if not scroll():
            return replace(current, problems=current.problems + ("the services list could not be scrolled",))
        grew = False
        waited_from = monotonic()
        while monotonic() - waited_from < settle_seconds and monotonic() < deadline:
            sleep(poll_seconds)
            latest = parse_services(capture())
            current = latest
            if len(latest.services) != before:
                grew = True
                break
        stagnant = 0 if grew else stagnant + 1
        if stagnant >= max_stagnant_rounds and not current.complete:
            expected = current.expected_total if current.expected_total is not None else "an unknown total"
            return replace(current, problems=current.problems + (f"the list stopped growing at {len(current.services)} of {expected}",))
    return current
