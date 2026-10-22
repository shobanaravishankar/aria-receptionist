/* The day as the calendar page shows it and the voice reads it: the generated schedule with the stored appointments drawn in (the overlay), and the availability snapshot derived from it.
 * Ports of the DayPlan types in demo/dataset.py, appointments/overlay.py and demo/driver.py::snapshot_from_plan. Pure: the stored rows are handed in by the caller.
 *
 * Overlay rules:
 *  - A stored (confirmed) appointment is authoritative. A generated filler visit that overlaps one is dropped, so the page never shows two people in one chair.
 *  - Cancelled, completed and no-show rows are not drawn and block nothing: a cancelled slot is free again immediately.
 *  - A stored appointment's cleanup time (persisted buffers) is drawn as a shaded "Cleanup" break next to the card, so every reader that treats breaks as time nobody can book refuses it.
 *  - If a technician has a stored appointment on a day she would not otherwise appear, she is drawn with it but with NO hours shown: her availability then reads as unknown, so nothing NEW
 *    can be booked with her that day, while the existing appointment stays visible and keeps blocking. */

import { CONFIRMED, instantOf, epochOf } from "./appointments.ts";
import type { StoredAppointment } from "./appointments.ts";
import { Interval } from "./models.ts";
import type { Appointment, DaySnapshot, StaffDay } from "./models.ts";
import { addDays, dayStart, fromLocal, localParts } from "./time.ts";
import type { DateParts, Instant } from "./time.ts";

export interface DemoStaff {
  staffId: string;
  name: string;
  weekdays: ReadonlySet<number>; // Monday = 0
  hours: readonly [number, number]; // minutes since midnight
  categories: ReadonlySet<string>;
  hoursKnown: boolean; // false (a diagnostic only): her column shows no hours, so her availability is unknown, never free
}

export interface Visit {
  start: number; // minutes since midnight
  end: number;
  service: string;
  category: string;
  client: string;
  reference: string; // set only for an appointment held in the store; the generated filler visits have none
}
export interface Break {
  start: number;
  end: number;
  label: string;
}
export interface StaffPlan {
  staff: DemoStaff;
  hours: readonly [number, number] | null; // null: not shown today
  breaks: readonly Break[];
  visits: readonly Visit[];
}
export interface DayPlan {
  day: DateParts;
  plans: readonly StaffPlan[]; // the staff SHOWN that day (the page's "working staff" filter), in roster order
}

/** What the column header displays: null for a person whose hours the calendar does not show. */
export const shownHours = (plan: StaffPlan): readonly [number, number] | null => (plan.staff.hoursKnown ? plan.hours : null);

/** How the calendar card shows a client: 'Willa Hartwell' becomes 'Willa H.', a single name stays as it is. */
export function displayName(full: string): string {
  const parts = full.split(/\s+/).filter(Boolean);
  if (parts.length <= 1) return full.trim();
  return `${parts[0]} ${parts[parts.length - 1][0].toUpperCase()}.`;
}

/** [start, end) of a local calendar day as epoch seconds (a day is 23 or 25 hours across a clock change; the wall clock decides, not 24 x 3600). */
export function dayBounds(day: DateParts): [number, number] {
  return [epochOf(dayStart(day)), epochOf(dayStart(addDays(day, 1)))];
}

/** Breaks with overlapping ones joined (the readers assume breaks never overlap one another). Touching breaks stay separate; a join of different labels is a plain Break. */
function union(breaks: readonly Break[]): Break[] {
  const merged: Break[] = [];
  for (const block of [...breaks].sort((a, b) => a.start - b.start || a.end - b.end)) {
    const last = merged[merged.length - 1];
    if (last && block.start < last.end) merged[merged.length - 1] = { start: last.start, end: Math.max(last.end, block.end), label: last.label === block.label ? last.label : "Break" };
    else merged.push(block);
  }
  return merged;
}

/** `plan` (a DayPlan) with the stored appointments drawn in. `roster` is the dataset's staff in display order. */
export function mergeRows(plan: DayPlan, rows: Iterable<StoredAppointment>, roster: readonly DemoStaff[]): DayPlan {
  const confirmed = [...rows].filter((r) => r.status === CONFIRMED);
  if (!confirmed.length) return plan;
  const byStaff = new Map<string, StoredAppointment[]>();
  for (const row of confirmed) {
    if (!byStaff.has(row.staffId)) byStaff.set(row.staffId, []);
    byStaff.get(row.staffId)!.push(row);
  }
  const known = new Map(roster.map((s) => [s.staffId, s]));
  const plans = new Map<string, StaffPlan>(plan.plans.map((p) => [p.staff.staffId, p]));
  for (const [staffId, mine] of byStaff) {
    const staff = known.get(staffId);
    if (staff === undefined) continue; // a technician who is no longer on the roster cannot be drawn; the store still holds the row
    const base: StaffPlan = plans.get(staffId) ?? { staff, hours: null, breaks: [], visits: [] }; // no hours shown: see the module note
    const stored: Visit[] = [];
    const cleanup: Break[] = [];
    for (const row of mine) {
      const local = localParts(instantOf(row.startUtc));
      const first = local.hour * 60 + local.minute;
      const last = first + row.durationMinutes;
      stored.push({ start: first, end: last, service: row.serviceName, category: row.category, client: displayName(row.clientName), reference: row.reference });
      if (row.bufferBeforeMinutes) cleanup.push({ start: Math.max(0, first - row.bufferBeforeMinutes), end: first, label: "Cleanup" });
      if (row.bufferAfterMinutes) cleanup.push({ start: last, end: Math.min(24 * 60, last + row.bufferAfterMinutes), label: "Cleanup" });
    }
    const kept = base.visits.filter((v) => !stored.some((s) => v.start < s.end && s.start < v.end));
    plans.set(staffId, { ...base, visits: [...kept, ...stored].sort((a, b) => a.start - b.start || a.end - b.end), breaks: union([...base.breaks, ...cleanup]) });
  }
  const order = new Map(roster.map((s, i) => [s.staffId, i]));
  return { day: plan.day, plans: [...plans.values()].sort((a, b) => (order.get(a.staff.staffId) ?? order.size) - (order.get(b.staff.staffId) ?? order.size)) };
}

const at = (day: DateParts, minutes: number): Instant => fromLocal(day.year, day.month, day.day, Math.floor(minutes / 60), minutes % 60);

/** The header window minus its breaks (the real reader derives the same from the shaded 'non-working' blocks). null when no hours are shown. */
function working(plan: StaffPlan, day: DateParts): Interval[] | null {
  const window = shownHours(plan);
  if (window === null) return null;
  const segments: Array<[number, number]> = [];
  let cursor = window[0];
  for (const block of [...plan.breaks].sort((a, b) => a.start - b.start)) {
    if (block.start > cursor) segments.push([cursor, block.start]);
    cursor = block.end; // breaks never overlap one another
  }
  if (cursor < window[1]) segments.push([cursor, window[1]]);
  return segments.map(([a, b]) => new Interval(at(day, a), at(day, b)));
}

export function snapshotFromPlan(plan: DayPlan, capturedAt: Instant): DaySnapshot {
  const staffDays: StaffDay[] = plan.plans.map((p) => {
    const appointments: Appointment[] = p.visits.map((v) => ({ staff: p.staff.name, interval: new Interval(at(plan.day, v.start), at(plan.day, v.end)), service: "", note: "", blocksTime: true }));
    return { staff: p.staff.name, working: working(p, plan.day), timeOff: [], appointments, staffId: p.staff.staffId };
  });
  return { day: plan.day, staffDays, capturedAt };
}
