/* The daily upkeep (S3-A): claimed by a lease, marked done only AFTER it succeeded, retried after a failure, taken over when its holder dies, and never skipped or half-served.
 * Local Postgres emulation (PGlite); it does not show hosted cold start or true concurrency. */

import assert from "node:assert/strict";
import { after, before, beforeEach, describe, test } from "node:test";

import { ensureUpkeep } from "../../src/upkeep.ts";
import { startPg } from "./pg.ts";

const KEY = "test-retell-key-not-a-secret";
const TOKEN = "test-tool-token-not-a-secret";
let pg: Awaited<ReturnType<typeof startPg>>;
before(async () => { pg = await startPg(); });
after(async () => pg.close());
beforeEach(async () => pg.reset());

const meta = async () => Object.fromEntries((await pg.pool.query("select key, value from meta order by key")).rows.map((r) => [r.key, r.value]));
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe("ensureUpkeep", () => {
  test("the day is marked done only after the work succeeded, and the lease is gone", async () => {
    let ran = 0;
    await ensureUpkeep(pg.pool, "2026-10-22", async () => {
      ran += 1;
      assert.deepEqual(Object.keys(await meta()), ["upkeep-lease:2026-10-22"], "while it runs: a lease, and no 'done'");
    });
    assert.equal(ran, 1);
    assert.deepEqual(await meta(), { "upkeep:2026-10-22": "done" });
    await ensureUpkeep(pg.pool, "2026-10-22", async () => { ran += 1; });
    assert.equal(ran, 1, "once done, nothing runs again that day");
  });

  test("a failed upkeep releases its lease, marks nothing done, and the next caller does the work", async () => {
    await assert.rejects(ensureUpkeep(pg.pool, "2026-10-22", async () => { throw new Error("transient failure"); }), /transient failure/);
    assert.deepEqual(await meta(), {}, "no 'done', no lease");
    let ran = 0;
    await ensureUpkeep(pg.pool, "2026-10-22", async () => { ran += 1; });
    assert.equal(ran, 1);
    assert.deepEqual(await meta(), { "upkeep:2026-10-22": "done" });
  });

  test("callers that arrive while it runs wait for it, run nothing themselves, and are served only after it finished", async () => {
    let ran = 0;
    let finished = false;
    const first = ensureUpkeep(pg.pool, "2026-10-22", async () => { ran += 1; await sleep(400); finished = true; }, { pollMs: 20 });
    await sleep(60);
    const waiters = [1, 2, 3].map(async () => {
      await ensureUpkeep(pg.pool, "2026-10-22", async () => { ran += 1; }, { pollMs: 20 });
      return finished;
    });
    assert.deepEqual(await Promise.all(waiters), [true, true, true], "nobody was served from a half-made day");
    await first;
    assert.equal(ran, 1);
  });

  test("waiters retry when the holder fails: one of them does the work", async () => {
    let ran = 0;
    const failing = ensureUpkeep(pg.pool, "2026-10-22", async () => { await sleep(200); throw new Error("holder failed"); }, { pollMs: 20 });
    await sleep(50);
    const waiter = ensureUpkeep(pg.pool, "2026-10-22", async () => { ran += 1; }, { pollMs: 20 });
    await assert.rejects(failing, /holder failed/);
    await waiter;
    assert.equal(ran, 1);
    assert.deepEqual(await meta(), { "upkeep:2026-10-22": "done" });
  });

  test("a holder that died is taken over once its lease expires; a live lease is respected until then", async () => {
    await pg.pool.query("insert into meta (key, value) values ('upkeep-lease:2026-10-22', 'dead-instance|' || ((extract(epoch from clock_timestamp()) * 1000)::bigint + 400)::text)");
    let ranAt = 0;
    const started = Date.now();
    await ensureUpkeep(pg.pool, "2026-10-22", async () => { ranAt = Date.now() - started; }, { pollMs: 25, waitMs: 5000 });
    assert.ok(ranAt >= 300, `ran after the lease expired, not before (${ranAt} ms)`);
    assert.deepEqual(await meta(), { "upkeep:2026-10-22": "done" });
  });

  test("a caller gives up (and a later one retries) if another instance holds the lease for too long", async () => {
    await pg.pool.query("insert into meta (key, value) values ('upkeep-lease:2026-10-22', 'slow-instance|' || ((extract(epoch from clock_timestamp()) * 1000)::bigint + 60000)::text)");
    let ran = 0;
    await assert.rejects(ensureUpkeep(pg.pool, "2026-10-22", async () => { ran += 1; }, { pollMs: 20, waitMs: 150 }), /still running elsewhere/);
    assert.equal(ran, 0);
  });

  test("a holder never releases someone else's lease", async () => {
    await ensureUpkeep(pg.pool, "2026-10-22", async () => {
      await pg.pool.query("update meta set value = 'other-instance|' || split_part(value, '|', 2) where key = 'upkeep-lease:2026-10-22'");
    });
    assert.ok("upkeep-lease:2026-10-22" in (await meta()), "the other instance's lease was left alone");
  });
});

describe("the Function: a transient startup failure does not lose the spa day", () => {
  test("S3-A: a failed first seed, then a retry: ready with the fixtures; never 'done' with zero clients", async () => {
    process.env.NETLIFY_DB_URL = pg.url;
    process.env.ARIA_RETELL_API_KEY = KEY;
    process.env.ARIA_RETELL_TOOL_TOKEN = TOKEN;
    delete process.env.NETLIFY_DB_DRIVER;
    const api = (await import("../../netlify/functions/api.mts")).default;
    const ready = async () => {
      const response = await api(new Request("https://showcase.example/readyz", { headers: { authorization: `Bearer ${TOKEN}` } }));
      return { status: response.status, body: await response.json() };
    };
    await pg.pool.query("create function fail_client_insert() returns trigger language plpgsql as $$ begin raise exception 'injected failure'; end $$");
    await pg.pool.query("create trigger fail_first_client before insert on clients for each row execute function fail_client_insert()");
    const failed = await ready();
    assert.equal(failed.status, 503);
    assert.ok(!("upkeep:" + new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(Date.now()) in (await meta())), "nothing was marked done");
    assert.equal((await meta())["upkeep-lease:" + new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(Date.now())], undefined, "and the lease was released");
    await pg.pool.query("drop trigger fail_first_client on clients");
    const retried = await ready();
    assert.equal(retried.status, 200);
    assert.equal(retried.body.counts.clients, 3, "the fictional clients are there after the retry");
    assert.ok(Object.values(retried.body.counts.appointments as Record<string, number>).reduce((a, b) => a + b, 0) >= 10);
  });

  test("a failed start does not poison the instance: once the database is reachable the next request works", async () => {
    const saved = process.env.NETLIFY_DB_URL;
    delete process.env.NETLIFY_DB_URL;
    delete process.env.NETLIFY_DB_DRIVER;
    const fresh = (await import("../../netlify/functions/api.mts?fresh=poison")).default; // a separate instance of the module, as a new Function instance would be
    const ready = async () => (await fresh(new Request("https://showcase.example/readyz", { headers: { authorization: `Bearer ${TOKEN}` } }))).status;
    try {
      assert.equal(await ready(), 503, "no database address yet");
      process.env.NETLIFY_DB_URL = saved;
      assert.equal(await ready(), 200, "and the same instance recovers");
    } finally {
      process.env.NETLIFY_DB_URL = saved;
    }
  });
});
