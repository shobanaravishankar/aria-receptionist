/* The ONE configurable demo roster: fictional FIRST names only (data exported from aria_booking/showcase/roster.py; see oracle/export_data.py). Names are display labels; the stable
 * internal staff id is the identity: schedules, service eligibility and scripted days are keyed by id, never by name. None of these is a real person. */

import rosterData from "./data/roster.json" with { type: "json" };

export interface StaffRow {
  staffId: string; // synthetic and stable
  name: string; // fictional first name, the only name shown or spoken
  weekdays: number[]; // Monday = 0
  hours: [number, number]; // minutes since midnight
  categories: string[]; // the service categories this person is assigned in the DEMO (synthetic eligibility)
  hoursKnown: boolean; // false is a DIAGNOSTIC state: availability is then UNKNOWN, never free
}

export const ROSTER_ROWS: readonly StaffRow[] = rosterData.rows as StaffRow[];
export const ROLE_BY_CATEGORY: Readonly<Record<string, string>> = rosterData.roleByCategory;
/** A name that is NOT on the roster but sounds like one: a near miss must never be treated as that person. */
export const NEAR_MISS_NAME: string = rosterData.nearMissName;

const NAME_BY_ID = new Map(ROSTER_ROWS.map((row) => [row.staffId, row.name]));

/** The fictional first name for a staff id (throws for an id that is not in the roster: a typo must not become a silent blank). */
export function nameOf(staffId: string): string {
  const name = NAME_BY_ID.get(staffId);
  if (name === undefined) throw new Error(`unknown staff id ${staffId}`);
  return name;
}

export function roleOf(row: StaffRow): string {
  return ROLE_BY_CATEGORY[row.categories[0]];
}

/** Refuse a roster the voice tools could not tell apart: a display name is ONE word and unique, ids are unique and every category is a known one. */
export function validateRoster(rows: readonly StaffRow[] = ROSTER_ROWS): void {
  if (new Set(rows.map((r) => r.staffId)).size !== rows.length) throw new Error("duplicate staff id in the roster");
  if (new Set(rows.map((r) => r.name.toLowerCase())).size !== rows.length) throw new Error("duplicate display name in the roster");
  for (const row of rows) {
    if (!row.name.trim() || row.name.trim().split(/\s+/).length !== 1) throw new Error(`staff ${row.staffId}: a display name must be one word (a first name)`);
    const unknown = row.categories.filter((c) => !(c in ROLE_BY_CATEGORY)).sort();
    if (unknown.length) throw new Error(`staff ${row.staffId}: unknown categories ${JSON.stringify(unknown)}`);
  }
}

validateRoster();
