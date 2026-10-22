/* Pure availability rules: which slots fit, and why a slot is refused. Port of aria_booking/availability.py, rule for rule.
 *
 *  - UNKNOWN IS NOT FREE: if working hours, time off or appointments could not be read, the day is unknown and no slot is offered.
 *  - A slot lies fully inside a known working interval including buffers, so a booking that would run past closing is rejected.
 *  - It does not overlap known time off or any appointment that blocks time (touching is fine).
 *  - It starts at least `minLeadMinutes` in the future and on the grid. Reasons contain only times, never customer data.
 */

import { forStaff, Interval } from "./models.ts";
import type { DaySnapshot, ServiceSpec, Slot, StaffDay } from "./models.ts";
import { addMinutes, fmt, minuteOfDay, MINUTE } from "./time.ts";
import type { Instant } from "./time.ts";

export interface SlotSearch {
  slots: Slot[];
  unknown: string[]; // reasons the day could not be fully evaluated
  notes: string[]; // e.g. "known not working"
}
export interface SlotCheck {
  ok: boolean;
  reasons: string[]; // why a slot is refused (known facts)
  unknown: string[]; // why it could not be judged
}

export function unknownReasons(staffDay: StaffDay): string[] {
  const reasons: string[] = [];
  if (staffDay.working === null) reasons.push("working hours unknown");
  if (staffDay.timeOff === null) reasons.push("time off unknown");
  if (staffDay.appointments === null) reasons.push("existing appointments unknown");
  return reasons;
}

export function blockedInterval(start: Instant, service: ServiceSpec): Interval {
  return new Interval(addMinutes(start, -service.bufferBeforeMinutes), addMinutes(start, service.durationMinutes + service.bufferAfterMinutes));
}

export function serviceInterval(start: Instant, service: ServiceSpec): Interval {
  return new Interval(start, addMinutes(start, service.durationMinutes));
}

/** Reasons a blocked interval is not free. Call only when nothing is unknown. */
function conflicts(staffDay: StaffDay, blocked: Interval): string[] {
  const reasons: string[] = [];
  if (!(staffDay.working ?? []).some((w) => w.contains(blocked))) reasons.push(`outside working hours (${blocked.label()} incl. buffers)`);
  for (const off of staffDay.timeOff ?? []) if (off.overlaps(blocked)) reasons.push(`overlaps time off ${off.label()}`);
  for (const appt of staffDay.appointments ?? []) if (appt.blocksTime && appt.interval.overlaps(blocked)) reasons.push(`overlaps existing appointment ${appt.interval.label()}`);
  return reasons;
}

/** The first grid time (by the wall clock) at or after `moment`; never earlier, so a lead time is never shortened. */
export function ceilToGrid(moment: Instant, gridMinutes: number): Instant {
  let base = Math.floor(moment / MINUTE) * MINUTE; // seconds and below dropped
  if (moment !== base) base = addMinutes(base, 1); // round up to the next whole minute FIRST, then onto the grid
  const minutes = minuteOfDay(base);
  return addMinutes(base, (((-minutes) % gridMinutes) + gridMinutes) % gridMinutes);
}

export function validateSlot(snapshot: DaySnapshot, service: ServiceSpec, staff: string, start: Instant, opts: { now: Instant; minLeadMinutes?: number }): SlotCheck {
  const minLead = opts.minLeadMinutes ?? 60;
  const staffDay = forStaff(snapshot, staff);
  if (staffDay === null) return { ok: false, reasons: [], unknown: [`staff ${pyRepr(staff)} not found in calendar snapshot`] };
  const unknown = unknownReasons(staffDay);
  if (unknown.length) return { ok: false, reasons: [], unknown };
  const reasons: string[] = [];
  if (addMinutes(opts.now, minLead) > start) reasons.push(`starts too soon (needs ${minLead} min lead)`);
  reasons.push(...conflicts(staffDay, blockedInterval(start, service)));
  return { ok: reasons.length === 0, reasons, unknown: [] };
}

export function findSlots(
  snapshot: DaySnapshot, service: ServiceSpec, staff: string,
  opts: { now: Instant; earliest?: Instant | null; latestEnd?: Instant | null; gridMinutes?: number; minLeadMinutes?: number },
): SlotSearch {
  const grid = opts.gridMinutes ?? 15;
  const minLead = opts.minLeadMinutes ?? 60;
  const staffDay = forStaff(snapshot, staff);
  if (staffDay === null) return { slots: [], unknown: [`staff ${pyRepr(staff)} not found in calendar snapshot`], notes: [] };
  const unknown = unknownReasons(staffDay);
  if (unknown.length) return { slots: [], unknown, notes: [] };
  if (!staffDay.working || staffDay.working.length === 0) return { slots: [], unknown: [], notes: ["known not working this day"] };

  let floor = addMinutes(opts.now, minLead);
  if (opts.earliest != null && opts.earliest > floor) floor = opts.earliest;

  const slots: Slot[] = [];
  for (const window of staffDay.working) {
    let candidate = ceilToGrid(Math.max(window.start, floor), grid);
    for (;;) {
      const blocked = blockedInterval(candidate, service);
      if (blocked.end > window.end) break;
      const svc = serviceInterval(candidate, service);
      if (opts.latestEnd != null && svc.end > opts.latestEnd) break;
      if (blocked.start >= window.start && conflicts(staffDay, blocked).length === 0) slots.push({ staff: staffDay.staff, service: svc, blocked });
      candidate = addMinutes(candidate, grid);
    }
  }
  return { slots, unknown: [], notes: [] };
}

export function describe(search: SlotSearch): string {
  if (search.unknown.length) return "UNKNOWN (not free): " + search.unknown.join("; ");
  if (!search.slots.length) return "no slots" + (search.notes.length ? ` (${search.notes.join("; ")})` : "");
  return search.slots.map((s) => fmt(s.service.start).slice(11)).join(", ");
}

/** Python's repr() of a string, for the one place a reason quotes a name: single quotes unless the text holds one and no double quote. */
export function pyRepr(text: string): string {
  const quote = text.includes("'") && !text.includes('"') ? '"' : "'";
  let out = "";
  for (const ch of text) {
    if (ch === "\\") out += "\\\\";
    else if (ch === quote) out += "\\" + ch;
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else out += ch;
  }
  return quote + out + quote;
}
