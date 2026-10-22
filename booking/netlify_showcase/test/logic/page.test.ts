/* The staff calendar page against the Python page: the same HTML, byte for byte, over many days, with stored appointments (and their cleanup) drawn in, outside the covered window and for the
 * diagnostic person whose hours are not shown. */

import assert from "node:assert/strict";
import { test } from "node:test";

import type { StoredAppointment } from "../../src/appointments.ts";
import { DemoDataset } from "../../src/dataset.ts";
import { renderPage } from "../../src/page.ts";
import type { DateParts } from "../../src/time.ts";
import { vectors } from "./support.ts";

const { samples } = vectors<{ samples: any[] }>("page");
const parts = (d: number[]): DateParts => ({ year: d[0], month: d[1], day: d[2] });
const rowOf = (r: any[]): StoredAppointment => ({
  apptId: r[0], reference: r[1], clientId: r[2], clientName: r[3], serviceId: r[4], serviceName: r[5], category: r[6], staffId: r[7], startUtc: r[8], durationMinutes: r[9],
  bufferBeforeMinutes: r[10], bufferAfterMinutes: r[11], status: r[12], version: r[13], origin: r[14], createdAt: r[15], updatedAt: r[16], cancelledAt: r[17], cancelReason: r[18], seedKey: r[19],
});

test("the calendar page is Python's page", () => {
  let withRows = 0, outside = 0;
  for (const s of samples) {
    const dataset = new DemoDataset({ today: () => parts(s.today), diagnosticUnknownHours: s.diagnostic });
    const rows = s.rows.map(rowOf).filter((r: StoredAppointment) => r.status === "confirmed");
    const html = renderPage(dataset, parts(s.day), rows);
    assert.equal(html, s.html, `page for ${s.day} from ${s.today}`);
    if (rows.length) withRows += 1;
    if (html.includes("demo-no-data")) outside += 1;
  }
  assert.ok(samples.length >= 30 && withRows >= 10 && outside >= 3, JSON.stringify({ n: samples.length, withRows, outside }));
});

test("the page never carries a client's number or notes, and names the demo plainly", () => {
  const dataset = new DemoDataset({ today: () => ({ year: 2026, month: 10, day: 21 }) });
  const html = renderPage(dataset, { year: 2026, month: 10, day: 22 });
  assert.match(html, /data-testid="demo-banner"/);
  assert.match(html, /invented for a demo/);
  assert.doesNotMatch(html, /\+1\d{10}|555-01|password|token/i);
});
