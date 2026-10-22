/* The placement judge and the open-times engine against the Python oracle: random placements (wrong lengths, other people's times, off-grid starts, days outside the calendar, stored rows
 * and a move's own old place) and random day views, compared value for value including every reason and message. */

import assert from "node:assert/strict";
import { test } from "node:test";

import { Refused } from "../../src/appointments.ts";
import type { Placement, StoredAppointment } from "../../src/appointments.ts";
import { DemoDataset } from "../../src/dataset.ts";
import { snapshotFromPlan } from "../../src/dayplan.ts";
import { PlacementJudge } from "../../src/judge.ts";
import { openTimes } from "../../src/openTimes.ts";
import type { DateParts } from "../../src/time.ts";
import { vectors } from "./support.ts";

const judgeVectors = vectors<any[]>("judge");
const openVectors = vectors<any[]>("opentimes");
const parts = (d: number[]): DateParts => ({ year: d[0], month: d[1], day: d[2] });
const rowOf = (r: any[]): StoredAppointment => ({
  apptId: r[0], reference: r[1], clientId: r[2], clientName: r[3], serviceId: r[4], serviceName: r[5], category: r[6], staffId: r[7], startUtc: r[8], durationMinutes: r[9],
  bufferBeforeMinutes: r[10], bufferAfterMinutes: r[11], status: r[12], version: r[13], origin: r[14], createdAt: r[15], updatedAt: r[16], cancelledAt: r[17], cancelReason: r[18], seedKey: r[19],
});
const snapshotJson = (s: ReturnType<typeof snapshotFromPlan>) =>
  s.staffDays.map((sd) => ({
    staff: sd.staff, staffId: sd.staffId, working: sd.working === null ? null : sd.working.map((i) => [i.start, i.end]), timeOff: sd.timeOff === null ? null : sd.timeOff.map((i) => [i.start, i.end]),
    appointments: sd.appointments === null ? null : sd.appointments.map((a) => [a.interval.start, a.interval.end, a.blocksTime]),
  }));

test("every placement is judged as Python judges it: the same acceptance, the same refusal, the same reason and the same facts", async () => {
  const seen: Record<string, number> = {};
  for (const e of judgeVectors) {
    const dataset = new DemoDataset({ today: () => parts(e.today), diagnosticUnknownHours: e.diagnostic });
    const judge = new PlacementJudge(dataset, { clock: () => e.now, minLeadMinutes: e.minLead, gridMinutes: e.grid, horizonDays: e.horizon });
    const rows = e.rows.map(rowOf);
    const p = e.placement;
    const placement: Placement = { staffId: p[0], startUtc: p[1], durationMinutes: p[2], bufferBeforeMinutes: p[3], bufferAfterMinutes: p[4], serviceId: p[5], category: p[6], ignoreReference: p[7] };
    const rowsBetween = async (low: number, high: number, statuses: readonly string[]) =>
      rows.filter((r: StoredAppointment) => statuses.includes(r.status) && r.startUtc < high && r.startUtc + r.durationMinutes * 60 > low);
    let got: any;
    try {
      const j = await judge.require(placement, rowsBetween);
      got = { ok: true, staffId: j.staffId, staffName: j.staffName, start: j.start, snapshot: snapshotJson(j.snapshot) };
    } catch (error) {
      if (!(error instanceof Refused)) throw error;
      const { snapshot, ...detail } = error.detail;
      got = { ok: false, status: error.status, reason: error.reason, detail, ...(snapshot ? { snapshot: snapshotJson(snapshot) } : {}) };
    }
    assert.deepEqual(got, e.result, JSON.stringify(e.placement));
    const key = got.ok ? "ok" : `${got.status}/${got.reason}`;
    seen[key] = (seen[key] ?? 0) + 1;
  }
  for (const needed of ["ok", "unavailable/not_free", "unavailable/not_working", "unknown/not_covered", "refused/off_grid", "refused/too_far", "refused/staff_not_eligible", "refused/service_facts_mismatch", "refused/unknown_service", "refused/unknown_staff"]) {
    assert.ok((seen[needed] ?? 0) >= 3, `${needed}: ${JSON.stringify(seen)}`);
  }
});

test("the open times a visitor sees are Python's, with every message and status", () => {
  const seen: Record<string, number> = {};
  for (const e of openVectors) {
    const dataset = new DemoDataset({ today: () => parts(e.today), diagnosticUnknownHours: e.diagnostic });
    const rows = e.rows.map(rowOf).filter((r: StoredAppointment) => r.status === "confirmed");
    const got = openTimes(dataset, e.serviceId, parts(e.day), { now: e.now, rows, staff: e.staff, gridMinutes: e.grid, minLeadMinutes: e.lead });
    assert.deepEqual(got, e.result, JSON.stringify([e.serviceId, e.day, e.staff]));
    seen[e.result.status] = (seen[e.result.status] ?? 0) + 1;
  }
  for (const needed of ["open", "none", "outside", "error", "unknown"]) assert.ok((seen[needed] ?? 0) > 3, `${needed}: ${JSON.stringify(seen)}`);
});
