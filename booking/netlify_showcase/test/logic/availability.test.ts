/* The availability rules against the Python oracle: 260 seeded day scenarios (including both DST days, unknown data and buffers), every slot check and every slot search compared value for value. */

import assert from "node:assert/strict";
import { test } from "node:test";

import { describe, findSlots, pyRepr, validateSlot } from "../../src/availability.ts";
import { Interval } from "../../src/models.ts";
import type { DaySnapshot, ServiceSpec, StaffDay } from "../../src/models.ts";
import { vectors } from "./support.ts";

const v = vectors("availability");

function snapshotOf(s: any): DaySnapshot {
  const intervals = (rows: number[][] | null) => (rows === null ? null : rows.map(([a, b]) => new Interval(a, b)));
  const staffDays: StaffDay[] = s.staffDays.map((sd: any) => ({
    staff: sd.staff, staffId: sd.staffId, working: intervals(sd.working), timeOff: intervals(sd.timeOff),
    appointments: sd.appointments === null ? null : sd.appointments.map((a: any) => ({ staff: a.staff, interval: new Interval(a.start, a.end), service: "svc", note: "", blocksTime: a.blocksTime })),
  }));
  return { day: { year: s.day[0], month: s.day[1], day: s.day[2] }, staffDays, capturedAt: s.now };
}

const serviceOf = (s: any): ServiceSpec => ({ name: s.service.name, durationMinutes: s.service.duration, bufferBeforeMinutes: s.service.before, bufferAfterMinutes: s.service.after });

test("every slot check (accepted, refused with reasons, or unknown) matches the Python rules exactly", () => {
  let checks = 0, accepted = 0, refused = 0, unknown = 0;
  for (const s of v.scenarios) {
    const snapshot = snapshotOf(s), service = serviceOf(s);
    for (const c of s.checks) {
      const got = validateSlot(snapshot, service, c.staff, c.start, { now: s.now, minLeadMinutes: s.minLead });
      const expected = { ok: c.ok, reasons: c.reasons, unknown: c.unknown };
      if (c.staff === "Nobody O'Neil") assert.deepEqual(got, { ok: false, reasons: [], unknown: c.unknown });
      else assert.deepEqual(got, expected, `scenario ${s.now} ${c.staff} ${c.start}`);
      checks += 1;
      if (c.ok) accepted += 1; else if (c.unknown.length) unknown += 1; else refused += 1;
    }
  }
  // the vectors must exercise all three outcomes, or the comparison proves little
  assert.ok(checks > 3000 && accepted > 200 && refused > 500 && unknown > 20, JSON.stringify({ checks, accepted, refused, unknown }));
});

test("every slot search returns the same slots, unknown reasons and notes as Python", () => {
  let searches = 0, withSlots = 0, withUnknown = 0, notWorking = 0;
  for (const s of v.scenarios) {
    const snapshot = snapshotOf(s), service = serviceOf(s);
    for (const q of s.searches) {
      const got = findSlots(snapshot, service, q.staff, { now: s.now, earliest: q.earliest, latestEnd: q.latestEnd, gridMinutes: q.grid, minLeadMinutes: s.minLead });
      const shaped = { slots: got.slots.map((x) => [x.staff, x.service.start, x.service.end, x.blocked.start, x.blocked.end]), unknown: got.unknown, notes: got.notes };
      assert.deepEqual(shaped, { slots: q.slots, unknown: q.unknown, notes: q.notes }, `search ${s.now} ${q.staff} ${q.earliest} ${q.latestEnd} ${q.grid}`);
      searches += 1;
      if (q.slots.length) withSlots += 1;
      if (q.unknown.length) withUnknown += 1;
      if (q.notes.length) notWorking += 1;
    }
  }
  assert.ok(searches > 1400 && withSlots > 400 && withUnknown > 20 && notWorking > 20, JSON.stringify({ searches, withSlots, withUnknown, notWorking }));
});

test("the spoken description of a search follows Python's wording", () => {
  const s = v.scenarios.find((x: any) => x.searches.some((q: any) => q.slots.length > 2))!;
  const snapshot = snapshotOf(s), service = serviceOf(s);
  const q = s.searches.find((x: any) => x.slots.length > 2)!;
  const got = findSlots(snapshot, service, q.staff, { now: s.now, earliest: q.earliest, latestEnd: q.latestEnd, gridMinutes: q.grid, minLeadMinutes: s.minLead });
  assert.match(describe(got), /^\d\d:\d\d(, \d\d:\d\d)+$/);
  assert.equal(describe({ slots: [], unknown: ["working hours unknown"], notes: [] }), "UNKNOWN (not free): working hours unknown");
  assert.equal(describe({ slots: [], unknown: [], notes: ["known not working this day"] }), "no slots (known not working this day)");
  assert.equal(describe({ slots: [], unknown: [], notes: [] }), "no slots");
});

test("Python's repr of a name in the 'not found' reason", () => {
  assert.equal(pyRepr("Nobody"), "'Nobody'");
  assert.equal(pyRepr("O'Neil"), `"O'Neil"`);
  assert.equal(pyRepr(`a'b"c`), `'a\\'b"c'`);
  assert.equal(pyRepr("back\\slash"), "'back\\\\slash'");
  assert.equal(pyRepr("two\nlines"), "'two\\nlines'");
});

test("an interval must end after it starts and touching intervals do not overlap", () => {
  assert.throws(() => new Interval(10, 10));
  assert.throws(() => new Interval(10, 5));
  assert.equal(new Interval(0, 10).overlaps(new Interval(10, 20)), false);
  assert.equal(new Interval(0, 11).overlaps(new Interval(10, 20)), true);
});
