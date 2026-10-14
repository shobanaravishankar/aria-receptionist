"""The ONE configurable demo roster: fictional FIRST names only. The calendar page, the voice answers, the prompt, the website and every test read it from here.

Names are display labels. The stable internal staff id is the identity: schedules, service eligibility and scripted days are keyed by id, never by name, so a name can
change without touching who is allowed to do what. None of these is a real person.
"""

from __future__ import annotations

from typing import NamedTuple


class StaffRow(NamedTuple):
    staff_id: str  # synthetic and stable
    name: str  # fictional first name, the only name shown or spoken
    weekdays: tuple  # Monday = 0
    hours: tuple  # minutes since midnight
    categories: tuple  # the service categories this person is assigned in the DEMO (synthetic eligibility)
    hours_known: bool = True  # False: the column shows no hours, so availability is UNKNOWN (like a calendar column that lists none)


ROSTER_ROWS = (
    StaffRow("910001", "Marisol", (0, 1, 2, 3, 4, 5), (600, 1200), ("massage", "body-scrub", "head-spa")),
    StaffRow("910002", "Tamsin", (0, 1, 2, 3, 4), (600, 1200), ("massage", "facial")),
    StaffRow("910003", "Odette", (1, 3, 5), (600, 1200), ("facial", "lash")),
    StaffRow("910004", "Priya", (2, 4, 6), (660, 1140), ("facial", "waxing")),
    StaffRow("910005", "Noor", (0, 1, 2, 3, 4, 5), (600, 1200), ("facial", "lash")),
    StaffRow("910006", "Sienna", (0, 1, 2, 3, 4, 5, 6), (600, 1200), ("massage", "head-spa")),
    StaffRow("910007", "Rowan", (0, 1, 2, 3, 4), (600, 1200), ("massage", "waxing")),
    StaffRow("910008", "Imogen", (0, 1, 3, 4, 5, 6), (600, 1200), ("lash", "waxing", "massage")),
    StaffRow("910009", "Calista", (1, 2, 3, 4, 5), (720, 1200), ("facial", "waxing")),
    StaffRow("910010", "Delphine", (0, 1, 2, 3, 4), (600, 1200), ("facial",), hours_known=False),
)

ROLE_BY_CATEGORY = {
    "massage": "Massage therapist", "facial": "Esthetician", "lash": "Lash and brow artist", "waxing": "Waxing specialist",
    "head-spa": "Scalp specialist", "body-scrub": "Body treatment specialist",
}

NAME_BY_ID = {row.staff_id: row.name for row in ROSTER_ROWS}


def name_of(staff_id: str) -> str:
    """The fictional first name for a staff id (KeyError for an id that is not in the roster: a typo must not become a silent blank)."""
    return NAME_BY_ID[staff_id]


def role_of(row: StaffRow) -> str:
    return ROLE_BY_CATEGORY[row.categories[0]]


# A name that is NOT on the roster but sounds like one: a near miss must never be treated as that person.
NEAR_MISS_NAME = "Tamzin"


def validate_roster(rows=ROSTER_ROWS) -> None:
    """Refuse a roster the voice tools could not tell apart. A display name is ONE word and unique: the tools let an exact name beat a longer name that shares it, so a pair
    such as 'Imogen' and 'Imogen Lin' would hide one person behind the other. Ids are unique and every category is a known one."""
    ids = [r.staff_id for r in rows]
    names = [r.name.casefold() for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate staff id in the roster")
    if len(set(names)) != len(names):
        raise ValueError("duplicate display name in the roster")
    for row in rows:
        if not row.name.strip() or len(row.name.split()) != 1:
            raise ValueError(f"staff {row.staff_id}: a display name must be one word (a first name)")
        unknown = set(row.categories) - set(ROLE_BY_CATEGORY)
        if unknown:
            raise ValueError(f"staff {row.staff_id}: unknown categories {sorted(unknown)}")


validate_roster()
