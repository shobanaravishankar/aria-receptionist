/* Speech, phone numbers, the generated demo calendar (many dates, both scripted days, the diagnostic), the store overlay and the availability snapshot, against the Python oracle. */

import assert from "node:assert/strict";
import { test } from "node:test";

import { epochOf } from "../../src/appointments.ts";
import type { StoredAppointment } from "../../src/appointments.ts";
import { DemoDataset, NotCovered } from "../../src/dataset.ts";
import { dayBounds, displayName, mergeRows, snapshotFromPlan } from "../../src/dayplan.ts";
import type { DayPlan } from "../../src/dayplan.ts";
import { normalizePhone } from "../../src/phone.ts";
import { joinChoices, joinNames, spokenDay, spokenDuration, spokenPrice, spokenSlot, spokenTime } from "../../src/speech.ts";
import type { DateParts } from "../../src/time.ts";
import { vectors } from "./support.ts";

const speech = vectors("speech");
const phones = vectors<Array<{ text: unknown; phone: string | null }>>("phone");
const dataset = vectors("dataset");

const parts = (d: number[]): DateParts => ({ year: d[0], month: d[1], day: d[2] });

test("spoken times, days, slots and durations are Python's words", () => {
  for (const m of speech.moments) {
    assert.equal(spokenTime(m.epoch), m.time);
    assert.equal(spokenDay(m.epoch), m.day);
    assert.equal(spokenSlot(m.epoch), m.slot);
  }
  for (const d of speech.durations) assert.equal(spokenDuration(d.minutes), d.text);
  for (const j of speech.joins) assert.deepEqual([joinChoices(j.labels), joinNames(j.labels)], [j.choices, j.names]);
  for (const p of speech.prices) assert.equal(spokenPrice(p.price), p.text);
});

test("phone numbers are normalised exactly as Python normalises them, and never by inventing a digit", () => {
  assert.ok(phones.length > 900);
  let complete = 0;
  for (const c of phones) {
    assert.equal(normalizePhone(c.text), c.phone, JSON.stringify(c.text));
    if (c.phone) complete += 1;
  }
  assert.ok(complete > 100 && phones.length - complete > 300, "the corpus holds both understood and refused numbers");
  assert.equal(normalizePhone("(212) 555-0142"), "+12125550142");
});

function planJson(plan: DayPlan) {
  return plan.plans.map((p) => ({
    staffId: p.staff.staffId, hours: p.hours === null ? null : [...p.hours], breaks: p.breaks.map((b) => [b.start, b.end, b.label]),
    visits: p.visits.map((v) => [v.start, v.end, v.service, v.category, v.client, v.reference]),
  }));
}
const snapshotJson = (s: ReturnType<typeof snapshotFromPlan>) =>
  s.staffDays.map((sd) => ({
    staff: sd.staff, staffId: sd.staffId, working: sd.working === null ? null : sd.working.map((i) => [i.start, i.end]), timeOff: sd.timeOff === null ? null : sd.timeOff.map((i) => [i.start, i.end]),
    appointments: sd.appointments === null ? null : sd.appointments.map((a) => [a.interval.start, a.interval.end, a.blocksTime]),
  }));
const rowOf = (r: any[]): StoredAppointment => ({
  apptId: r[0], reference: r[1], clientId: r[2], clientName: r[3], serviceId: r[4], serviceName: r[5], category: r[6], staffId: r[7], startUtc: r[8], durationMinutes: r[9],
  bufferBeforeMinutes: r[10], bufferAfterMinutes: r[11], status: r[12], version: r[13], origin: r[14], createdAt: r[15], updatedAt: r[16], cancelledAt: r[17], cancelReason: r[18], seedKey: r[19],
});

test("the generated calendar is Python's, day for day, for every staff member, across both DST days, the scripted days and the diagnostic", () => {
  let plans = 0, outside = 0, merged = 0, visits = 0;
  for (const d of dataset.days) {
    const ds = new DemoDataset({ today: () => parts(d.today), diagnosticUnknownHours: d.diagnostic });
    const day = parts(d.day);
    assert.equal(ds.covers(day), d.covered);
    if (d.plan === null) {
      assert.throws(() => ds.baseDayPlan(day), NotCovered);
      outside += 1;
      continue;
    }
    const base = ds.baseDayPlan(day);
    assert.deepEqual(planJson(base), d.plan, `plan ${d.day} from ${d.today} ${d.diagnostic}`);
    plans += 1;
    visits += base.plans.reduce((n, p) => n + p.visits.length, 0);
    if (d.merged) {
      const mergedPlan = mergeRows(base, d.rows.map(rowOf), ds.roster);
      assert.deepEqual(planJson(mergedPlan), d.merged, `overlay ${d.day}`);
      assert.deepEqual(snapshotJson(snapshotFromPlan(mergedPlan, d.now)), d.snapshot, `snapshot ${d.day}`);
      merged += 1;
    } else {
      assert.deepEqual(snapshotJson(snapshotFromPlan(base, d.now)), d.snapshot, `snapshot ${d.day}`);
    }
  }
  assert.ok(plans > 400 && outside > 50 && merged > 150 && visits > 3000, JSON.stringify({ plans, outside, merged, visits }));
});

test("a stored day's bounds follow the wall clock across the clock changes, and card names are shortened as the page shows them", () => {
  for (const b of dataset.bounds) assert.deepEqual(dayBounds(parts(b.day)), b.bounds);
  for (const n of dataset.displayNames) assert.equal(displayName(n.full), n.shown);
  assert.equal(epochOf(1_800_000_000_999), 1_800_000_000);
});

test("a diagnostic person without hours is named and an unknown id is refused", () => {
  assert.throws(() => new DemoDataset({ today: () => ({ year: 2026, month: 10, day: 21 }), diagnosticUnknownHours: ["000000"] }), /not on the roster/);
});
