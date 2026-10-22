/* Is this placement really bookable? ONE judgement, used for the dry run before a confirmation and again at commit. Port of aria_booking/appointments/judge.py.
 *
 * It rebuilds the day exactly as the calendar page and the voice read driver show it (the generated schedule plus the stored appointments, from the SAME merge) and applies the SAME
 * availability rules check_slot uses (known working hours, breaks, the whole service plus its cleanup, minimum notice, the quarter-hour grid). On top it checks what only a writer must:
 * the service's length and buffers are the registry's (never the caller's), the technician exists and is eligible by stable id, and the day is one the demo calendar covers. Anything it
 * cannot establish is a refusal; unknown is never free. The database's own constraint stays the last arbiter of overlap; this is what explains a refusal to the caller. */

import { CONFIRMED, instantOf, Refused } from "./appointments.ts";
import type { Placement, StoredAppointment } from "./appointments.ts";
import { validateSlot } from "./availability.ts";
import { isEligible } from "./bookable.ts";
import type { BookableService } from "./bookable.ts";
import type { DemoDataset } from "./dataset.ts";
import { dayBounds, snapshotFromPlan } from "./dayplan.ts";
import { forStaff } from "./models.ts";
import type { DaySnapshot, ServiceSpec } from "./models.ts";
import { addDays, compareDates, dateOf, localParts } from "./time.ts";
import type { Instant } from "./time.ts";

export interface Judgement {
  service: BookableService;
  staffId: string;
  staffName: string;
  start: Instant;
  snapshot: DaySnapshot;
}

/** The confirmed appointments whose service time falls between two epoch-second marks: a transaction's own reader, or the store's. */
export type RowsBetween = (low: number, high: number, statuses: readonly string[]) => Promise<StoredAppointment[]>;

export class PlacementJudge {
  readonly dataset: DemoDataset;
  private readonly clock: () => Instant;
  readonly minLead: number;
  readonly grid: number;
  readonly horizon: number;

  constructor(dataset: DemoDataset, opts: { clock: () => Instant; minLeadMinutes?: number; gridMinutes?: number; horizonDays?: number }) {
    this.dataset = dataset;
    this.clock = opts.clock;
    this.minLead = opts.minLeadMinutes ?? 60;
    this.grid = opts.gridMinutes ?? 15;
    this.horizon = opts.horizonDays ?? 60;
  }

  now(): Instant {
    return this.clock();
  }

  snapshot(day: ReturnType<typeof dateOf>, rows: readonly StoredAppointment[]): DaySnapshot {
    return snapshotFromPlan(this.dataset.dayPlan(day, rows), this.now());
  }

  /** The judgement, or a thrown `Refused`. `rowsBetween` is a transaction's (or the store's) reader, so commit-time judgement sees the transaction's own state. */
  async require(placement: Placement, rowsBetween: RowsBetween): Promise<Judgement> {
    const service = this.dataset.registry.get(placement.serviceId);
    if (service === null || !service.verified) throw new Refused("refused", "unknown_service");
    if (placement.durationMinutes !== service.durationMinutes || placement.bufferBeforeMinutes !== service.bufferBeforeMinutes || placement.bufferAfterMinutes !== service.bufferAfterMinutes) {
      throw new Refused("refused", "service_facts_mismatch"); // the length and cleanup time are the registry's; a caller cannot shorten them
    }
    const staff = this.dataset.roster.find((s) => s.staffId === placement.staffId);
    if (staff === undefined) throw new Refused("refused", "unknown_staff");
    if (!isEligible(service, staff.name, staff.staffId)) throw new Refused("refused", "staff_not_eligible", null, { staff: staff.name });
    const start = instantOf(placement.startUtc);
    const day = dateOf(start);
    if (!this.dataset.covers(day)) throw new Refused("unknown", "not_covered");
    const local = localParts(start);
    if (local.minute % this.grid || local.second || placement.startUtc % 60) throw new Refused("refused", "off_grid");
    if (compareDates(day, addDays(dateOf(this.now()), this.horizon)) > 0) throw new Refused("refused", "too_far");
    const [low, high] = dayBounds(day);
    const rows = (await rowsBetween(low, high, [CONFIRMED])).filter((r) => r.reference !== placement.ignoreReference);
    const snapshot = this.snapshot(day, rows);
    const spec: ServiceSpec = { name: service.booksyName, durationMinutes: service.durationMinutes, bufferBeforeMinutes: service.bufferBeforeMinutes, bufferAfterMinutes: service.bufferAfterMinutes };
    if (forStaff(snapshot, staff.name) === null) { // not on that day's schedule at all (a day off), which is KNOWN, unlike a column that shows no hours
      throw new Refused("unavailable", "not_working", null, { snapshot, staff: staff.name });
    }
    const verdict = validateSlot(snapshot, spec, staff.name, start, { now: this.now(), minLeadMinutes: this.minLead });
    if (verdict.unknown.length) throw new Refused("unknown", "calendar_unknown", null, { snapshot, staff: staff.name, unknown: verdict.unknown });
    if (!verdict.ok) throw new Refused("unavailable", "not_free", null, { snapshot, staff: staff.name, reasons: verdict.reasons });
    return { service, staffId: staff.staffId, staffName: staff.name, start, snapshot };
  }
}
