/* The one demo dataset: staff, services, eligibility and a deterministic multi-day schedule. Invented; no real customer data. Port of aria_booking/demo/dataset.py.
 *
 * Schedules are generated per (date, staff) from a fixed seed with Python's own random generator (see pyrandom.ts), so any run produces the same calendar for the same date as the
 * Python demo does. The window the dataset covers is explicit; any other date throws NotCovered and the voice answers "can't confirm", never "free". Two SCRIPTED days (tomorrow and the
 * day after) pin the interesting cases so a demo is reliable. The tables (families, names, scripted days) are DATA exported from the Python module (oracle/export_data.py). */

import { bookable, BookableRegistry } from "./bookable.ts";
import type { BookableService } from "./bookable.ts";
import { SHOWCASE_CATALOG } from "./catalog.ts";
import data from "./data/dataset.json" with { type: "json" };
import { ROSTER_ROWS } from "./roster.ts";

export const GRID_START = data.gridStart; // the day view draws 9:00 AM ...
export const GRID_END = data.gridEnd; // ... to 9:00 PM
export const COVERAGE_BEFORE_DAYS = data.coverageBeforeDays;
export const COVERAGE_AFTER_DAYS = data.coverageAfterDays;
export const SEED = data.seed;
export const DEMO_FAMILIES = data.demoFamilies as Array<[category: string, family: string, bufferAfter: number]>;
export const FIRST_NAMES: readonly string[] = data.firstNames;
export const INITIALS: string = data.initials;

export class NotCovered extends Error {}

export interface DemoStaff {
  staffId: string;
  name: string;
  weekdays: ReadonlySet<number>; // Monday = 0
  hours: readonly [number, number]; // minutes since midnight
  categories: ReadonlySet<string>;
  hoursKnown: boolean; // false (a diagnostic only): her column shows no hours, so her availability is unknown, never free
}

export const ROSTER: readonly DemoStaff[] = ROSTER_ROWS.map((row) => ({
  staffId: row.staffId, name: row.name, weekdays: new Set(row.weekdays), hours: row.hours, categories: new Set(row.categories), hoursKnown: row.hoursKnown,
}));

/** (service, category) for every duration variant of the demo's menu services. Eligibility is the synthetic category assignment of the roster. */
export function demoServices(roster: readonly DemoStaff[] = ROSTER): Array<[BookableService, string]> {
  const out: Array<[BookableService, string]> = [];
  for (const [category, family, bufferAfter] of DEMO_FAMILIES) {
    for (const item of SHOWCASE_CATALOG.items) {
      if (item.category === category && item.family === family && item.durationMinutes) {
        const ids = new Set(roster.filter((s) => s.categories.has(category)).map((s) => s.staffId));
        out.push([
          bookable({
            serviceId: item.itemId, booksyName: item.name, durationMinutes: item.durationMinutes, bufferAfterMinutes: bufferAfter, eligibleStaffIds: ids, verified: true,
            catalogItem: item.itemId, priceUsd: item.priceUsd,
          }),
          category,
        ]);
      }
    }
  }
  return out;
}

/** The services the demo can check. `verified` here means 'part of the DEMO mapping', nothing more. */
export function demoRegistry(roster: readonly DemoStaff[] = ROSTER): BookableRegistry {
  return new BookableRegistry(demoServices(roster).map(([service]) => service), { catalog: SHOWCASE_CATALOG });
}
