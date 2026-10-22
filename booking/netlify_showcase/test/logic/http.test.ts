/* The public read answers are validated BEFORE any database is opened (free-plan credits: a malformed or unanswerable anonymous request must never wake it), and only answers that depend
 * on no record may be cached, briefly. The "database" here is a world() that counts how often it is asked for. */

import assert from "node:assert/strict";
import { test } from "node:test";

import { DemoDataset } from "../../src/dataset.ts";
import { handle } from "../../src/http.ts";
import type { HttpRequest } from "../../src/http.ts";
import { dateOf, isoDate, addDays } from "../../src/time.ts";

const NOW = Date.parse("2026-10-22T16:00:00Z");
const dataset = new DemoDataset({ today: () => dateOf(NOW) });
const tomorrow = isoDate(addDays(dateOf(NOW), 1));
let opens = 0;
const app = (unavailable = true) => ({
  world: async () => { opens += 1; if (unavailable) throw new Error("the database is not reachable"); throw new Error("no world in this test"); },
  dataset: () => dataset, apiKey: "k", bearer: "t", now: () => NOW,
});
const get = (path: string, query: Record<string, string> = {}): HttpRequest => ({ method: "GET", path, query: new URLSearchParams(query), headers: {}, body: new Uint8Array() });
const SWEDISH = "massage-swedish-massage-60";

test("a malformed or unanswerable request never opens the database", async () => {
  const cases: Array<[string, HttpRequest, number]> = [
    ["no service, bad date", get("/demo/open-times", { date: "invalid" }), 400],
    ["service, bad date", get("/demo/open-times", { service_id: SWEDISH, date: "2026-13-45" }), 400],
    ["service, no date", get("/demo/open-times", { service_id: SWEDISH }), 400],
    ["no service", get("/demo/open-times", { date: tomorrow }), 400],
    ["service id too long", get("/demo/open-times", { service_id: "x".repeat(200), date: tomorrow }), 400],
    ["staff name too long", get("/demo/open-times", { service_id: SWEDISH, date: tomorrow, staff: "y".repeat(200) }), 400],
    ["an unknown treatment", get("/demo/open-times", { service_id: "not-a-treatment", date: tomorrow }), 200],
    ["a date outside the demo window", get("/demo/open-times", { service_id: SWEDISH, date: "2099-01-01" }), 200],
    ["a technician nobody has", get("/demo/open-times", { service_id: SWEDISH, date: tomorrow, staff: "Nobody Here" }), 200],
    ["a technician who does not do it", get("/demo/open-times", { service_id: SWEDISH, date: tomorrow, staff: "Zzyzx" }), 200],
    ["calendar page, bad date", get("/pro/en-us/9000001/calendar", { date: "tomorrow-ish" }), 400],
    ["calendar page, date outside the window", get("/pro/en-us/9000001/calendar", { date: "2099-01-01" }), 200],
    ["health", get("/demo/health"), 200],
    ["liveness", get("/healthz"), 200],
    ["an unknown path", get("/demo/nothing"), 404],
    ["a write method on a read path", { ...get("/demo/open-times"), method: "POST" }, 405],
  ];
  for (const [what, request, status] of cases) {
    opens = 0;
    const response = await handle(request, app());
    assert.equal(response.status, status, what);
    assert.equal(opens, 0, `${what}: the database was opened`);
  }
});

test("the answers that need no record say what they should", async () => {
  const unknown = JSON.parse((await handle(get("/demo/open-times", { service_id: "not-a-treatment", date: tomorrow }), app())).body);
  assert.equal(unknown.reason, "unknown_service");
  const outside = JSON.parse((await handle(get("/demo/open-times", { service_id: SWEDISH, date: "2099-01-01" }), app())).body);
  assert.equal(outside.status, "outside");
  const staff = JSON.parse((await handle(get("/demo/open-times", { service_id: SWEDISH, date: tomorrow, staff: "Nobody Here" }), app())).body);
  assert.deepEqual([staff.status, staff.reason], ["error", "staff_not_eligible"]);
  const page = await handle(get("/pro/en-us/9000001/calendar", { date: "2099-01-01" }), app());
  assert.match(page.body, /demo-no-data/);
  assert.match(page.headers["content-security-policy"], /frame-ancestors 'self'/);
});

test("a request that does need records opens the database once, and says so plainly when it cannot", async () => {
  for (const request of [
    get("/demo/open-times", { service_id: SWEDISH, date: tomorrow }),
    get("/demo/open-times", { service_id: SWEDISH, date: tomorrow, staff: "Sienna" }),
    get("/pro/en-us/9000001/calendar", { date: tomorrow }),
    get("/demo/changes"),
  ]) {
    opens = 0;
    const response = await handle(request, app());
    assert.equal(opens, 1, request.path);
    assert.equal(response.status, 503, request.path);
    assert.equal(response.headers["cache-control"], "no-store", `${request.path}: an error is never kept`);
  }
});

test("caching: only answers that depend on no record are kept, briefly, and only by the browser", async () => {
  const cache = async (request: HttpRequest) => (await handle(request, app())).headers["cache-control"];
  assert.equal(await cache(get("/demo/health")), "public, max-age=60");
  assert.equal(await cache(get("/demo/open-times", { service_id: "not-a-treatment", date: tomorrow })), "public, max-age=300");
  assert.equal(await cache(get("/demo/open-times", { service_id: SWEDISH, date: "2099-01-01" })), "public, max-age=300");
  assert.equal(await cache(get("/pro/en-us/9000001/calendar", { date: "2099-01-01" })), "public, max-age=300");
  assert.equal(await cache(get("/healthz")), "no-store");
  for (const response of [await handle(get("/demo/health"), app()), await handle(get("/demo/open-times", { service_id: "not-a-treatment", date: tomorrow }), app())]) {
    assert.ok(!("netlify-cdn-cache-control" in response.headers) && !/s-maxage/.test(response.headers["cache-control"]), "no shared-cache directive: the answer varies with the Origin");
  }
});
