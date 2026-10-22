/* The Netlify Function end to end, on the local Postgres (PGlite): the real adapter (netlify/functions/api.mts) is imported and called with real Request objects. It proves the wiring and the
 * HTTP rules (authentication before any database access, the bearer, the lazy daily upkeep, a full book and cancel over HTTP, the staff calendar page, the read-only answers). It does not prove
 * anything about Netlify's runtime, bundling, cold start or limits: those are verified on a draft deploy. */

import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHmac } from "node:crypto";
import { createServer } from "node:http";
import { promisify } from "node:util";
import { after, before, test } from "node:test";

import { startPg } from "./pg.ts";

const KEY = "test-retell-key-not-a-secret";
const TOKEN = "test-tool-token-not-a-secret";
let pg: Awaited<ReturnType<typeof startPg>>;
let api: (request: Request) => Promise<Response>;

before(async () => {
  pg = await startPg();
  process.env.NETLIFY_DB_URL = pg.url;
  process.env.ARIA_RETELL_API_KEY = KEY;
  process.env.ARIA_RETELL_TOOL_TOKEN = TOKEN;
  delete process.env.NETLIFY_DB_DRIVER;
  api = (await import("../../netlify/functions/api.mts")).default;
});
after(async () => pg.close());

const url = (path: string) => `https://showcase.example${path}`;
const stamp = (body: string, key = KEY, at = Date.now()) => `v=${at},d=${createHmac("sha256", key).update(body).update(String(at)).digest("hex")}`;
function tool(name: string, args: Record<string, unknown>, call = "call-http", opts: { key?: string; token?: string | null; at?: number; name?: string | null } = {}): Request {
  const body = JSON.stringify({ name: opts.name === undefined ? name : opts.name, call: { call_id: call }, args });
  const headers: Record<string, string> = { "content-type": "application/json", "x-retell-signature": stamp(body, opts.key ?? KEY, opts.at) };
  if (opts.token !== null) headers.authorization = `Bearer ${opts.token ?? TOKEN}`;
  return new Request(url(`/tools/${name}`), { method: "POST", headers, body });
}
const call = async (request: Request) => {
  const response = await api(request);
  const text = await response.text();
  let body: any = null;
  try { body = JSON.parse(text); } catch { /* html or text */ }
  return { status: response.status, body, text, headers: response.headers };
};
const count = async (table: string) => Number((await pg.pool.query(`select count(*) as n from ${table}`)).rows[0].n);
function tomorrow(): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(Date.now() + 86_400_000);
  return parts; // YYYY-MM-DD
}

test("nothing opens the database before a request is authenticated, and a bad request is turned away", async () => {
  await pg.reset();
  const cases: Array<[Request, number]> = [
    [new Request(url("/tools/check_slot"), { method: "POST", body: "{}" }), 401],
    [tool("check_slot", {}, "c", { key: "wrong-key" }), 401],
    [tool("check_slot", {}, "c", { at: Date.now() - 10 * 60_000 }), 401],
    [tool("check_slot", {}, "c", { token: "wrong-token" }), 401],
    [tool("check_slot", {}, "c", { token: null }), 401],
    [new Request(url("/tools/check_slot"), { method: "GET" }), 405],
    [new Request(url("/tools/not_a_tool"), { method: "POST", body: "{}" }), 404],
    [new Request(url("/tools/book_slot"), { method: "POST", body: "{}" }), 404],
  ];
  for (const [request, status] of cases) assert.equal((await call(request)).status, status, `${request.method} ${request.url}`);
  assert.equal(await count("clients"), 0, "no database work was done for any of them");
  assert.equal(await count("meta"), 0);
  const big = new Request(url("/tools/check_slot"), { method: "POST", body: "x".repeat(300 * 1024), headers: { "x-retell-signature": "v=1,d=" + "0".repeat(64) } });
  assert.equal((await call(big)).status, 413);
});

test("a signed, bearer-authorised call with a malformed body is a 400, and a mismatched function name too", async () => {
  const sign = (body: string) => new Request(url("/tools/check_slot"), { method: "POST", body, headers: { "x-retell-signature": stamp(body), authorization: `Bearer ${TOKEN}` } });
  assert.equal((await call(sign("not json"))).status, 400);
  assert.equal((await call(sign("[1,2]"))).status, 400);
  assert.equal((await call(sign(JSON.stringify({ name: "check_slot", call: {}, args: {} })))).status, 400);
  assert.equal((await call(sign(JSON.stringify({ name: "check_slot", call: { call_id: "c" }, args: [] })))).status, 400);
  assert.equal((await call(tool("check_slot", {}, "c", { name: "find_alternatives" }))).status, 400);
});

test("the first call that needs data runs the daily upkeep once; the fictional clients and their appointments are there", async () => {
  await pg.reset();
  const first = await call(tool("lookup_service", { query: "swedish massage" }));
  assert.equal(first.status, 200);
  assert.equal(first.body.status, "choose_variant");
  assert.equal(await count("clients"), 3, "the world was opened and the fictional clients seeded");
  assert.equal(await count("meta"), 1);
  const appointments = await count("appointments");
  assert.ok(appointments >= 10, `${appointments} appointments seeded`);
  await call(tool("check_slot", { service_id: "massage-swedish-massage-60", date: tomorrow(), time: "16:00", staff: "Sienna", service_words: "swedish massage" }, "c2"));
  assert.equal(await count("appointments"), appointments, "the upkeep does not run again the same day");
  assert.equal(await count("meta"), 1);
});

test("book and cancel over HTTP: the read-back saves nothing, the yes saves once, a repeat is the same answer, the calendar shows it and shows it gone", async () => {
  const day = tomorrow();
  const args = { service_id: "massage-swedish-massage-60", date: day, time: "17:00", staff: "Sienna", client_name: "Mira Quillfeather", phone: "+1 212 555 0188", service_words: "swedish massage" };
  const before = await count("appointments");
  const readBack = await call(tool("book_appointment", args, "http-flow"));
  assert.equal(readBack.body.status, "confirmation_required");
  assert.equal(await count("appointments"), before);
  const done = await call(tool("book_appointment", { ...args, confirmed: true, confirmation_token: readBack.body.confirmation_token }, "http-flow"));
  assert.equal(done.body.status, "booked_verified");
  assert.equal(done.body.ok, true);
  assert.equal(await count("appointments"), before + 1);
  const again = await call(tool("book_appointment", { ...args, confirmed: true, confirmation_token: readBack.body.confirmation_token }, "http-flow"));
  assert.equal(again.body.status, "already_booked");
  assert.equal(await count("appointments"), before + 1, "a repeated yes saves nothing more");
  const reference = done.body.appointment.reference;

  const changes = await call(new Request(url("/demo/changes")));
  assert.equal(changes.status, 200);
  assert.match(changes.body.stamp, /^\d{4}-\d{2}-\d{2}\|\d+\.\d+\.\d+\.\d+$/);
  assert.equal(changes.headers.get("cache-control"), "no-store");

  const page = await call(new Request(url(`/pro/en-us/9000001/calendar?date=${day}&view=day&staffers=working`)));
  assert.equal(page.status, 200);
  assert.match(page.text, /Mira Q\./, "the new booking is drawn on the staff calendar, by first name and initial");
  assert.ok(page.text.includes(`data-appointment-id="${reference}"`));
  assert.doesNotMatch(page.text, /0188|Quillfeather/, "no number and no surname on the page");
  assert.match(page.headers.get("content-security-policy") ?? "", /frame-ancestors 'self'/);

  const times = await call(new Request(url(`/demo/open-times?service_id=massage-swedish-massage-60&date=${day}&staff=Sienna`)));
  assert.equal(times.body.status, "open");
  assert.equal(times.headers.get("cache-control"), "no-store", "an answer read from the records is never kept");
  assert.equal(page.headers.get("cache-control"), "no-store");
  assert.ok(!times.body.times.some((t: any) => t.start === "17:00"), "the booked time is no longer offered");

  const cancelArgs = { reference, phone: "+1 212 555 0188", name: "Mira Quillfeather" };
  const cancelRead = await call(tool("cancel_appointment", cancelArgs, "http-flow-2"));
  assert.equal(cancelRead.body.status, "confirmation_required");
  const cancelled = await call(tool("cancel_appointment", { ...cancelArgs, confirmed: true, confirmation_token: cancelRead.body.confirmation_token }, "http-flow-2"));
  assert.equal(cancelled.body.status, "cancelled");
  const after = await call(new Request(url(`/demo/open-times?service_id=massage-swedish-massage-60&date=${day}&staff=Sienna`)));
  assert.ok(after.body.times.some((t: any) => t.start === "17:00"), "and the time is open again");
  assert.notEqual((await call(new Request(url("/demo/changes")))).body.stamp, changes.body.stamp);
});

test("state lives in the database, not in the instance: a read-back from one invocation is confirmed by another with no memory", async () => {
  const day = tomorrow();
  const args = { service_id: "massage-swedish-massage-60", date: day, time: "18:00", staff: "Sienna", client_name: "Zora Test", phone: "+1 212 555 0155" };
  const read = await call(tool("book_appointment", args, "stateless"));
  assert.equal(read.body.status, "confirmation_required");
  assert.equal(await count("pending_confirmations") >= 1, true);
  await pg.pool.query("update call_state set doc = doc"); // (nothing changes; the call's memory is a row)
  assert.equal((await call(tool("book_appointment", { ...args, confirmed: true, confirmation_token: read.body.confirmation_token }, "another-call"))).body.status, "confirmation_required", "a token belongs to the call that was read to");
  assert.equal((await call(tool("book_appointment", { ...args, confirmed: true, confirmation_token: read.body.confirmation_token }, "stateless"))).body.status, "booked_verified");
});

test("the read-only answers: health needs no database, the open-times and page inputs are validated, and nothing here can write", async () => {
  const health = await call(new Request(url("/demo/health")));
  assert.equal(health.status, 200);
  assert.equal(health.body.source, "demo_calendar");
  assert.equal(health.body.covers.length, 2);
  assert.equal((await call(new Request(url("/healthz")))).status, 200);
  assert.equal((await call(new Request(url("/demo/open-times?date=2026-10-22")))).status, 400);
  assert.equal((await call(new Request(url("/demo/open-times?service_id=x&date=nope")))).status, 400);
  assert.equal((await call(new Request(url("/demo/open-times?service_id=nope&date=" + tomorrow())))).body.reason, "unknown_service");
  assert.equal((await call(new Request(url("/pro/en-us/9000001/calendar?date=2026-13-45")))).status, 400);
  assert.equal((await call(new Request(url("/pro/en-us/9000001/calendar?date=2020-01-01")))).status, 200, "outside the covered window the page says so");
  for (const path of ["/demo/open-times", "/demo/changes", "/pro/en-us/9000001/calendar"]) assert.equal((await call(new Request(url(path), { method: "POST", body: "{}" }))).status, 405);
  assert.equal((await call(new Request(url("/demo/other")))).status, 404);
});

test("the readiness check needs the token, because it opens the database", async () => {
  assert.equal((await call(new Request(url("/readyz")))).status, 401);
  assert.equal((await call(new Request(url("/readyz"), { headers: { authorization: "Bearer nope" } }))).status, 401);
  const ready = await call(new Request(url("/readyz"), { headers: { authorization: `Bearer ${TOKEN}` } }));
  assert.equal(ready.status, 200);
  assert.equal(ready.body.status, "ready");
  assert.ok(ready.body.counts.clients >= 3);
});


test("the operator's smoke script passes against the real function (twice, with different run ids), exercises every write path, records its own mutations, and leaves nothing booked", async () => {
  const server = createServer(async (req, res) => {
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(chunk as Buffer);
    const body = Buffer.concat(chunks);
    const response = await api(new Request(`http://127.0.0.1${req.url}`, { method: req.method, headers: req.headers as Record<string, string>, body: req.method === "GET" || req.method === "HEAD" ? undefined : body }));
    res.writeHead(response.status, Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as { port: number }).port;
  const confirmedBefore = Number((await pg.pool.query("select count(*) as n from appointments where status = 'confirmed'")).rows[0].n);
  const { mkdtemp, readFile, rm } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const dir = await mkdtemp(`${tmpdir()}/smoke-`);
  try {
    const run = promisify(execFile);
    const script = new URL("../../scripts/smoke_public.mjs", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
    const runs: string[] = [];
    for (const n of [1, 2]) {
      const record = `${dir}/record-${n}.json`;
      const { stdout } = await run(process.execPath, [script, `http://127.0.0.1:${port}`, "--write", "--record", record], { env: { ...process.env, ARIA_RETELL_API_KEY: KEY, ARIA_RETELL_TOOL_TOKEN: TOKEN } });
      assert.match(stdout, /all checks passed/, stdout);
      assert.ok(!stdout.includes(KEY) && !stdout.includes(TOKEN), "the script prints no secret");
      assert.equal((stdout.match(/FAIL/g) ?? []).length, 0);
      for (const route of ["lookup_service", "check_slot", "find_alternatives", "find_appointments", "last_completed_visit", "book_appointment", "cancel_appointment", "reschedule_appointment", "get_preferences", "save_preference", "remove_preference"]) assert.ok(stdout.includes(route), `${route} was exercised`);
      const doc = JSON.parse(await readFile(record, "utf8"));
      assert.deepEqual(doc.mutations.map((m: any) => m.what), ["booked", "rescheduled", "preference saved", "preference removed", "cancelled"], "every change it made is on its record");
      runs.push(doc.run);
    }
    assert.notEqual(runs[0], runs[1], "each run has its own id");
  } finally {
    server.close();
    await rm(dir, { recursive: true, force: true });
  }
  const confirmedAfter = Number((await pg.pool.query("select count(*) as n from appointments where status = 'confirmed'")).rows[0].n);
  assert.equal(confirmedAfter, confirmedBefore, "every smoke booking was cancelled again");
  assert.equal(Number((await pg.pool.query("select count(*) as n from client_preferences")).rows[0].n), 0, "and every smoke preference was removed");
});
