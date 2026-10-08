"""Who are the account's staff? Establishes, from the Staff page, which staff member the calendar's single
column must be. Pure parsing; the browser navigation lives in the driver.

The calendar page itself never names the staff member of a column. If the account has exactly ONE staff
member and that person is the configured one, then a calendar with exactly one column can only be that person.
Anything else (zero, several, a different name, an unreadable list) is NOT confirmed, and the reader refuses.
"""

from __future__ import annotations

from typing import Any, Iterable

Node = dict[str, Any]

LIST_TESTID = "resources-list"
ITEM_NAME_PREFIX = "resources-list-item-name-"


def parse_staff_list(nodes: Iterable[Node]) -> list[str]:
    """The displayed staff names, in page order. Empty if the staff list is not on the page."""
    nodes = list(nodes)
    if not any(n.get("testid") == LIST_TESTID for n in nodes):
        return []
    return [(n.get("text") or "").strip() for n in nodes if (n.get("testid") or "").startswith(ITEM_NAME_PREFIX)]


def confirms_single_staff(names: list[str], configured: str) -> bool:
    """True only for exactly one staff member whose first name is the configured staff name."""
    if len(names) != 1 or not names[0]:
        return False
    first_word = names[0].split()[0]
    return first_word.casefold() == configured.strip().casefold()
