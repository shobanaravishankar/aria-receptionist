/* The one demo dataset: staff, services, eligibility and a deterministic multi-day schedule. Invented; no real customer data. Port of aria_booking/demo/dataset.py.
 *
 * * Every standard column has complete working days and hours; a person who is not working that day is simply not on that day's schedule (off duty is a KNOWN state, distinct from missing
 *   data). A column whose hours are MISSING exists only as a deliberate diagnostic (`diagnosticUnknownHours`): her availability is then UNKNOWN and never free.
 * * Schedules are generated per (date, staff) from a fixed seed with Python's own random generator (pyrandom.ts), so any run produces the same calendar for the same date as the Python demo.
 *   The window the dataset covers is explicit; any other date throws NotCovered and the voice answers "can't confirm", never "free".
 * * Two SCRIPTED days (tomorrow and the day after the demo date) pin the interesting cases so a demo is reliable.
 * The tables (families, names, scripted days) are DATA exported from the Python module (oracle/export_data.py). */

import { bookable, BookableRegistry } from "./bookable.ts";
import type { BookableService } from "./bookable.ts";
import { SHOWCASE_CATALOG } from "./catalog.ts";
import data from "./data/dataset.json" with { type: "json" };
import { mergeRows } from "./dayplan.ts";
import type { Break, DayPlan, DemoStaff, StaffPlan, Visit } from "./dayplan.ts";
import { PyRandom, seededRandom } from "./pyrandom.ts";
import { ROSTER_ROWS } from "./roster.ts";
import type { StoredAppointment } from "./appointments.ts";
import { addDays, compareDates, isoDate, weekdayOf } from "./time.ts";
import type { DateParts } from "./time.ts";

export type { Break, DayPlan, DemoStaff, StaffPlan, Visit } from "./dayplan.ts";

export const GRID_START = data.gridStart; // the day view draws 9:00 AM ...
export const GRID_END = data.gridEnd; // ... to 9:00 PM
export const COVERAGE_BEFORE_DAYS = data.coverageBeforeDays;
export const COVERAGE_AFTER_DAYS = data.coverageAfterDays;
export const SEED = data.seed;
export const DEMO_FAMILIES = data.demoFamilies as Array<[category: string, family: string, bufferAfter: number]>;
export const FIRST_NAMES: readonly string[] = data.firstNames;
export const INITIALS: string = data.initials;

export class NotCovered extends Error {}

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

type ScriptEntry = "off" | { hours: [number, number]; breaks: Array<[number, number, string]>; visits: Array<[number, number, string, string]> };
const SCRIPT_FIRST = data.scriptFirst as Record<string, ScriptEntry>;
const SCRIPT_SECOND = data.scriptSecond as Record<string, ScriptEntry>;
const DIAGNOSTIC_UNKNOWN_FIRST = data.diagnosticUnknownFirst as ScriptEntry;

function client(rng: PyRandom): string {
  const first = rng.choice(FIRST_NAMES);
  const initial = rng.choice([...INITIALS]);
  return `${first} ${initial}.`;
}

export interface DatasetOptions {
  /** The spa's current calendar date. A server that runs for days passes its clock here, so the coverage window and the scripted days follow the real date. */
  today: () => DateParts;
  extraStaff?: readonly DemoStaff[];
  diagnosticUnknownHours?: Iterable<string>;
}

export class DemoDataset {
  readonly roster: readonly DemoStaff[];
  readonly diagnosticUnknownHours: ReadonlySet<string>;
  readonly registry: BookableRegistry;
  private readonly todaySource: () => DateParts;
  private readonly services = new Map<string, BookableService[]>();
  private readonly categories = new Map<string, string>();

  constructor(opts: DatasetOptions) {
    this.todaySource = opts.today;
    this.diagnosticUnknownHours = new Set(opts.diagnosticUnknownHours ?? []);
    const everyone = [...ROSTER, ...(opts.extraStaff ?? [])];
    const missing = [...this.diagnosticUnknownHours].filter((id) => !everyone.some((s) => s.staffId === id)).sort();
    if (missing.length) throw new Error("diagnosticUnknownHours names staff who are not on the roster: " + missing.join(", "));
    this.roster = everyone.map((s) => (this.diagnosticUnknownHours.has(s.staffId) ? { ...s, hoursKnown: false } : s));
    const pairs = demoServices(this.roster);
    this.registry = new BookableRegistry(pairs.map(([service]) => service), { catalog: SHOWCASE_CATALOG });
    for (const [service, category] of pairs) {
      if (!this.services.has(category)) this.services.set(category, []);
      this.services.get(category)!.push(service);
      this.categories.set(service.serviceId, category);
    }
  }

  // ---- what is covered -----------------------------------------------------------------------------------
  get today(): DateParts {
    return this.todaySource();
  }
  get firstDay(): DateParts {
    return addDays(this.today, -COVERAGE_BEFORE_DAYS);
  }
  get lastDay(): DateParts {
    return addDays(this.today, COVERAGE_AFTER_DAYS);
  }
  covers(day: DateParts): boolean {
    return compareDates(this.firstDay, day) <= 0 && compareDates(day, this.lastDay) <= 0;
  }

  /** Who does this service in the DEMO: the roster members whose synthetic category assignment covers it, in roster order. Demo-only facts, not the salon's. */
  providers(service: BookableService): string[] {
    return this.roster.filter((s) => service.eligibleStaffIds!.has(s.staffId)).map((s) => s.name);
  }

  get scriptedDays(): [DateParts, DateParts] {
    return [addDays(this.today, 1), addDays(this.today, 2)];
  }

  categoryOf(serviceId: string): string {
    const category = this.categories.get(serviceId);
    if (category === undefined) throw new Error(`unknown service ${serviceId}`);
    return category;
  }

  // ---- the schedule ---------------------------------------------------------------------------------------
  /** The day as the page shows it and the voice reads it: the generated schedule with the given stored appointments drawn in. */
  dayPlan(day: DateParts, rows: Iterable<StoredAppointment> = []): DayPlan {
    return mergeRows(this.baseDayPlan(day), rows, this.roster);
  }

  baseDayPlan(day: DateParts): DayPlan {
    if (!this.covers(day)) throw new NotCovered(`the demo calendar covers ${isoDate(this.firstDay)} to ${isoDate(this.lastDay)} only`);
    const plans: StaffPlan[] = [];
    for (const staff of this.roster) {
      const entry = this.scriptEntry(day, staff);
      if (entry === "off") continue;
      const plan = entry !== null ? this.scripted(day, staff, entry) : this.generated(day, staff);
      if (plan !== null) plans.push(plan);
    }
    return { day, plans };
  }

  private generated(day: DateParts, staff: DemoStaff): StaffPlan | null {
    const rng = seededRandom(SEED, isoDate(day), staff.staffId);
    if (!staff.weekdays.has(weekdayOf(day)) || rng.random() < 0.07) return null; // a rostered day off, or an occasional day away
    const [start, end] = staff.hours;
    const breaks: Break[] = [];
    if (rng.random() < 0.5) {
      const at = rng.choice([750, 780, 810, 840]);
      breaks.push({ start: at, end: at + rng.choice([30, 60]), label: "Lunch" });
    }
    const visits: Visit[] = [];
    if (staff.hoursKnown) {
      const utilisation = weekdayOf(day) >= 5 ? 0.7 : 0.5;
      // canonical order (categories sorted, services in the registry's fixed order)
      const pool = [...staff.categories].sort().flatMap((c) => this.services.get(c) ?? []);
      let cursor = start;
      while (pool.length && cursor + 30 <= end) {
        const blocked = breaks.find((b) => b.start <= cursor && cursor < b.end);
        if (blocked) {
          cursor = blocked.end;
          continue;
        }
        if (rng.random() < utilisation) {
          const long = pool.filter((x) => x.durationMinutes >= 45);
          const service = rng.choice(long.length && rng.random() < 0.7 ? long : pool); // mostly full treatments, some quick add-ons
          const finish = cursor + service.durationMinutes;
          if (finish <= end && !breaks.some((b) => cursor < b.end && b.start < finish)) {
            visits.push({ start: cursor, end: finish, service: service.booksyName, category: this.categoryOf(service.serviceId), client: client(rng), reference: "" });
            cursor = Math.ceil((finish + rng.choice([0, 0, 15, 30])) / 15) * 15; // the next appointment starts on a quarter hour
            continue;
          }
        }
        cursor += rng.choice([15, 30, 45, 60]);
      }
    }
    return { staff, hours: [start, end], breaks, visits };
  }

  // ---- the two scripted days ------------------------------------------------------------------------------
  private scriptEntry(day: DateParts, staff: DemoStaff): ScriptEntry | null {
    const [first, second] = this.scriptedDays;
    if (compareDates(day, first) !== 0 && compareDates(day, second) !== 0) return null;
    const isFirst = compareDates(day, first) === 0;
    if (this.diagnosticUnknownHours.has(staff.staffId) && isFirst) return DIAGNOSTIC_UNKNOWN_FIRST; // present on the scripted day with no hours shown
    return (isFirst ? SCRIPT_FIRST : SCRIPT_SECOND)[staff.staffId] ?? null; // null: this person follows the generated schedule that day
  }

  private scripted(day: DateParts, staff: DemoStaff, entry: Exclude<ScriptEntry, "off">): StaffPlan {
    const rng = seededRandom(SEED, isoDate(day), staff.staffId, "scripted");
    const built: Visit[] = entry.visits.map(([s, e, label, category]) => ({ start: s, end: e, service: label, category, client: client(rng), reference: "" }));
    return { staff, hours: entry.hours, breaks: entry.breaks.map(([s, e, label]) => ({ start: s, end: e, label })), visits: staff.hoursKnown ? built : [] };
  }
}
