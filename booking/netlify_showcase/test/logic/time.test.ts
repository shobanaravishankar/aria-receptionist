/* The time helpers against the Python oracle: wall-clock to instant (including the skipped and the repeated hour on the two DST days), local fields read back, and the formatted strings. */

import assert from "node:assert/strict";
import { test } from "node:test";

import { ceilToGrid } from "../../src/availability.ts";
import { addDays, addMinutes, compareDates, dateOf, daysBetween, fmt, fromLocal, hhmm, isoDate, localParts, minuteOfDay, nextDay, offsetMinutes, parseIsoDate, weekdayOf, MINUTE } from "../../src/time.ts";
import { vectors } from "./support.ts";

const v = vectors("time");

test("wall-clock fields become the same instant as Python's aware datetime (fold=0), on ordinary and DST days", () => {
  assert.ok(v.fromLocal.length > 400);
  for (const c of v.fromLocal) {
    const got = fromLocal(c.y, c.m, c.d, c.h, c.mi);
    assert.equal(got, c.epoch, `fromLocal ${c.y}-${c.m}-${c.d} ${c.h}:${c.mi}`);
  }
});

test("local fields, weekday and the formatted strings read back identically for every instant across both DST changes", () => {
  const skipped = v.fromLocal.filter((c: any) => !c.exists);
  assert.equal(skipped.length, 5, "the vectors include the five wall times inside the skipped spring hour (compared by instant above)");
  for (const c of [...v.fromLocal.filter((c: any) => c.exists), ...v.reads]) {
    const p = localParts(c.epoch);
    assert.equal(fmt(c.epoch), c.fmt);
    assert.equal(hhmm(c.epoch), c.hhmm);
    assert.equal(p.weekday, c.weekday);
    assert.equal(minuteOfDay(c.epoch), c.minuteOfDay);
  }
  for (const c of v.reads) assert.equal(offsetMinutes(c.epoch), c.offset, `offset at ${c.fmt}`);
});

test("the spring-forward day has its hour missing and the fall-back day repeats one, in real elapsed time", () => {
  const springStart = fromLocal(2026, 3, 8, 0, 0);
  const springNext = fromLocal(2026, 3, 9, 0, 0);
  assert.equal((springNext - springStart) / MINUTE, 23 * 60);
  const fallStart = fromLocal(2026, 11, 1, 0, 0);
  const fallNext = fromLocal(2026, 11, 2, 0, 0);
  assert.equal((fallNext - fallStart) / MINUTE, 25 * 60);
  assert.equal(offsetMinutes(fallStart), -240);
  assert.equal(offsetMinutes(fallNext), -300);
});

test("adding minutes is real elapsed time, also over spans that cross a DST change", () => {
  assert.ok(v.adds.length >= 40);
  for (const c of v.adds) {
    assert.equal(addMinutes(c.start, c.minutes), c.epoch);
    assert.equal(fmt(addMinutes(c.start, c.minutes)), c.fmt);
  }
});

test("grid rounding matches Python for whole minutes, stray seconds and fractions, and a 60-minute grid across DST", () => {
  assert.ok(v.ceil.length === 400);
  for (const c of v.ceil) assert.equal(ceilToGrid(c.epoch, c.grid), c.expected, `ceil ${c.epoch} grid ${c.grid}`);
});

test("calendar-date helpers", () => {
  assert.deepEqual(parseIsoDate("2026-10-22"), { year: 2026, month: 10, day: 22 });
  assert.equal(isoDate({ year: 2026, month: 3, day: 8 }), "2026-03-08");
  assert.deepEqual(nextDay({ year: 2026, month: 12, day: 31 }), { year: 2027, month: 1, day: 1 });
  assert.deepEqual(addDays({ year: 2026, month: 2, day: 27 }, 2), { year: 2026, month: 3, day: 1 });
  assert.equal(weekdayOf({ year: 2026, month: 10, day: 22 }), 3); // a Thursday
  assert.equal(weekdayOf({ year: 2026, month: 10, day: 25 }), 6); // a Sunday
  assert.equal(daysBetween({ year: 2026, month: 3, day: 7 }, { year: 2026, month: 3, day: 9 }), 2);
  assert.ok(compareDates({ year: 2026, month: 1, day: 2 }, { year: 2026, month: 1, day: 1 }) > 0);
  assert.deepEqual(dateOf(fromLocal(2026, 11, 1, 23, 59)), { year: 2026, month: 11, day: 1 });
  for (const bad of ["2026-02-30", "2026-13-01", "26-01-01", "2026-1-1", "", "2026-01-01T00:00"]) assert.throws(() => parseIsoDate(bad), bad);
});
