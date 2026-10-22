/* The website's customer-facing 'open times' view: which start times are genuinely open for a chosen treatment and day, derived from the SAME calendar the voice assistant reads.
 * Port of aria_booking/demo/open_times.py. Nothing here is a second source of truth: the day is the dataset's merged plan (the generated schedule plus any stored appointments and their
 * cleanup, handed in by the caller), turned into the same snapshot the voice tools read, and each technician's slots come from the same findSlots the voice's alternatives use.
 * What a visitor may see is deliberately small: start times and technician first names. Never another client's name or appointment, never a count of anything hidden.
 *
 *   open     at least one time is open (`partial` says some technician's schedule could not be read, so nothing is claimed about them)
 *   none     every eligible schedule was read and none can take the full service that day (or nobody who does it works that day)
 *   unknown  nothing is open and at least one eligible schedule could NOT be read, so 'no openings' cannot be claimed
 *   outside  the day is not one the demo calendar covers
 * Unknown is never free. */

import type { StoredAppointment } from "./appointments.ts";
import { casefold } from "./appointments.ts";
import { findSlots } from "./availability.ts";
import { isEligible } from "./bookable.ts";
import type { DemoDataset } from "./dataset.ts";
import { snapshotFromPlan } from "./dayplan.ts";
import type { ServiceSpec } from "./models.ts";
import { spokenDay, spokenDuration, spokenTime } from "./speech.ts";
import { dayStart, hhmm, isoDate } from "./time.ts";
import type { DateParts, Instant } from "./time.ts";

export const MAX_TIMES = 48;

export function openTimes(
  dataset: DemoDataset, serviceId: string, day: DateParts,
  opts: { now: Instant; rows?: readonly StoredAppointment[]; staff?: string | null; gridMinutes?: number; minLeadMinutes?: number },
): Record<string, unknown> {
  const service = dataset.registry.get(serviceId);
  if (service === null || !service.verified) return { status: "error", reason: "unknown_service", message: "That treatment isn't on the list of treatments this calendar can check." };
  const dayText = spokenDay(dayStart(day));
  const base = { service_id: service.serviceId, service: service.booksyName, duration_minutes: service.durationMinutes, date: isoDate(day), day: dayText };
  if (!dataset.covers(day)) {
    return { ...base, status: "outside", times: [], message: `The calendar only covers ${isoDate(dataset.firstDay)} to ${isoDate(dataset.lastDay)}, so times for that day can't be shown.` };
  }
  const snapshot = snapshotFromPlan(dataset.dayPlan(day, opts.rows ?? []), opts.now);
  let eligible = snapshot.staffDays.filter((sd) => isEligible(service, sd.staff, sd.staffId));
  const staff = opts.staff;
  if (staff) {
    const chosen = eligible.filter((sd) => casefold(sd.staff.trim()) === casefold(staff.trim()));
    if (!chosen.length) {
      const whoDoes = dataset.roster.filter((s) => isEligible(service, s.name, s.staffId)).map((s) => s.name);
      if (whoDoes.some((n) => casefold(n) === casefold(staff.trim()))) return { ...base, status: "none", times: [], technician: staff, message: `${staff} isn't working on ${dayText}.` };
      return { ...base, status: "error", reason: "staff_not_eligible", message: `${staff} doesn't do ${service.booksyName}.` };
    }
    eligible = chosen;
  }
  if (!eligible.length) return { ...base, status: "none", times: [], message: `Nobody who does ${service.booksyName} is working on ${dayText}.` };
  const spec: ServiceSpec = { name: service.booksyName, durationMinutes: service.durationMinutes, bufferBeforeMinutes: service.bufferBeforeMinutes, bufferAfterMinutes: service.bufferAfterMinutes };
  const byStart = new Map<number, string[]>();
  const unreadable: string[] = [];
  for (const sd of eligible) {
    const found = findSlots(snapshot, spec, sd.staff, { now: opts.now, gridMinutes: opts.gridMinutes ?? 15, minLeadMinutes: opts.minLeadMinutes ?? 60 });
    if (found.unknown.length) {
      unreadable.push(sd.staff);
      continue;
    }
    for (const slot of found.slots) {
      const stamp = Math.trunc(slot.service.start / 1000);
      if (!byStart.has(stamp)) byStart.set(stamp, []);
      byStart.get(stamp)!.push(sd.staff);
    }
  }
  const times = [...byStart.keys()].sort((a, b) => a - b).slice(0, MAX_TIMES).map((stamp) => ({ start: hhmm(stamp * 1000), label: spokenTime(stamp * 1000), technicians: byStart.get(stamp)! }));
  const length = spokenDuration(service.durationMinutes);
  if (times.length) {
    const note = unreadable.length ? `Some schedules couldn't be read for this day (${unreadable.join(", ")}), so nothing is shown for them.` : "";
    return { ...base, status: "open", times, partial: unreadable.length > 0, unreadable, message: note };
  }
  if (unreadable.length) {
    return { ...base, status: "unknown", times: [], unreadable, message: `We can't confirm times for the full ${length} that day: ${unreadable.join(", ")}'s schedule can't be read, so nothing is claimed about it.` };
  }
  return { ...base, status: "none", times: [], message: `No openings for the full ${length} of ${service.booksyName} on ${dayText}.` };
}
