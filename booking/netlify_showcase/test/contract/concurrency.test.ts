/* Concurrency and retry on Postgres (PGlite through @netlify/database-dev, the local emulator) and on the in-memory model: the three findings of the independent review of e75b48e.
 *  - one preference confirmation retried six times at once has ONE effect (key and effect are one statement);
 *  - an interrupted removal cannot leave a removal without its key, and a retry of an old confirmation cannot remove a newer choice (the removal names the state it confirmed);
 *  - parallel tool calls of ONE call cannot lose each other's counters (a call's memory is saved by compare-and-set; a loser redoes its step against the newer memory).
 * Local emulation shows the SQL and the logic are right; it does not show hosted concurrency. */

import assert from "node:assert/strict";
import { after, before, beforeEach, describe, test } from "node:test";

import { CallConflict, emptyCall, MemoryCallStore } from "../../src/callState.ts";
import type { CallStore } from "../../src/callState.ts";
import { MemoryStore } from "../../src/memoryStore.ts";
import { PgCallStore, PgStore } from "../../src/pgStore.ts";
import type { Preference, Store } from "../../src/store.ts";
import { openWorld } from "../../src/world.ts";
import { startPg } from "./pg.ts";

const NOW_MS = Date.parse("2026-10-22T12:00:00Z");
const NOW = NOW_MS / 1000;
const FAMILY = "massage-deep-tissue-massage";

function barrier(count: number) {
  let n = 0;
  let release!: () => void;
  const ready = new Promise<void>((r) => { release = r; });
  return async () => { if (++n === count) release(); await ready; };
}
const willa = (startUtc: number) => ({
  clientName: "Willa Hartwell", phone: "+12125550142", serviceId: "massage-deep-tissue-massage-60", serviceName: "Deep Tissue Massage, 1 hour", category: "massage", staffId: "910001",
  startUtc, durationMinutes: 60, bufferAfterMinutes: 15,
});

let pg: Awaited<ReturnType<typeof startPg>>;
before(async () => { pg = await startPg(); });
after(async () => { await pg?.close(); });
beforeEach(async () => { await pg.reset(); });

const pgStore = (db: any = pg.pool) => new PgStore(db, { clock: () => NOW });
async function client(store: Store): Promise<number> {
  const result = await store.book(willa(NOW + 86400));
  assert.equal(result.status, "booked");
  return result.appointment!.clientId;
}

describe("preferences on Postgres: one statement, one effect", () => {
  test("S2-A: six simultaneous retries of one confirmation, all reading the same initial state, save once", async () => {
    const id = await client(pgStore());
    const wait = barrier(6);
    const db = { async query(sql: string, values?: any[]) {
      const result = await pg.pool.query(sql, values);
      if (sql.startsWith("select * from client_preferences where")) await wait(); // every request reads the same initial state before any write
      return result;
    } };
    const results = await Promise.all(Array.from({ length: 6 }, () => pgStore(db).preferences.set(id, FAMILY, "910001", { requestKey: "same-confirmation" })));
    assert.deepEqual(results.map((r) => r.status).sort(), ["replayed", "replayed", "replayed", "replayed", "replayed", "saved"]);
    assert.equal((await pgStore().preferences.get(id, FAMILY))!.version, 1);
    assert.equal((await pg.pool.query("select count(*)::int as n from preference_events")).rows[0].n, 1);
    assert.equal((await pg.pool.query("select count(*)::int as n from preference_requests")).rows[0].n, 1);
  });

  test("S2-B: a removal and its key commit together, so a failed removal leaves neither, and a retry of an old removal cannot remove a newer choice", async () => {
    const store = pgStore();
    const id = await client(store);
    await store.preferences.set(id, FAMILY, "910001");
    const confirmed = { staffId: "910001", version: 1 }; // what the caller was read back
    let interrupt = true;
    const interrupted = pgStore({ async query(sql: string, values?: any[]) {
      if (interrupt && sql.includes("insert into preference_requests") && sql.includes("'remove'")) { interrupt = false; throw new Error("simulated function interruption"); }
      return pg.pool.query(sql, values);
    } });
    await assert.rejects(interrupted.preferences.remove(id, FAMILY, { requestKey: "old-remove", expected: confirmed }), /simulated function interruption/);
    // the interruption happened before ANYTHING committed: no removal, no key, no event
    assert.equal((await store.preferences.get(id, FAMILY))!.staffId, "910001");
    assert.equal((await pg.pool.query("select count(*)::int as n from preference_requests where kind = 'remove'")).rows[0].n, 0);
    assert.equal((await pg.pool.query("select count(*)::int as n from preference_events where kind = 'remove'")).rows[0].n, 0);
    // a newer choice is saved; the old confirmation is retried
    await store.preferences.set(id, FAMILY, "910002", { requestKey: "new-save" });
    const retry = await store.preferences.remove(id, FAMILY, { requestKey: "old-remove", expected: confirmed });
    assert.equal(retry.status, "stale");
    assert.equal((await store.preferences.get(id, FAMILY))!.staffId, "910002");
    // and when the removal DID commit, its retry replays instead of removing again
    const first = await store.preferences.remove(id, FAMILY, { requestKey: "good-remove", expected: { staffId: "910002", version: 2 } });
    assert.equal(first.status, "removed");
    await store.preferences.set(id, FAMILY, "910003", { requestKey: "later-save" });
    assert.equal((await store.preferences.remove(id, FAMILY, { requestKey: "good-remove", expected: { staffId: "910002", version: 2 } })).status, "replayed");
    assert.equal((await store.preferences.get(id, FAMILY))!.staffId, "910003");
  });

  test("a save and a removal racing on the same preference leave one consistent state", async () => {
    const store = pgStore();
    const id = await client(store);
    await store.preferences.set(id, FAMILY, "910001");
    const [removed, saved] = await Promise.all([
      store.preferences.remove(id, FAMILY, { requestKey: "r", expected: { staffId: "910001", version: 1 } }),
      store.preferences.set(id, FAMILY, "910002", { requestKey: "s", expected: { staffId: "910001", version: 1 } }),
    ]);
    const final = await store.preferences.get(id, FAMILY);
    const events = (await pg.pool.query("select kind from preference_events order by event_id")).rows.map((r) => r.kind);
    // exactly one of the two applied to the state both had confirmed; the other found it changed and changed nothing
    assert.equal([removed.status, saved.status].filter((s) => s === "removed" || s === "saved").length, 1);
    assert.equal([removed.status, saved.status].filter((s) => s === "stale" || s === "nothing_to_remove").length, 1);
    assert.equal(events[events.length - 1], final === null ? "remove" : "set"); // the last event says what the state is
  });
});

describe("a call's memory: compare-and-set", () => {
  for (const [name, make] of [["memory model", () => new MemoryCallStore()], ["postgres (PGlite)", () => new PgCallStore(pg.pool, () => NOW_MS)]] as const) {
    test(`${name}: a save made against an old revision is refused and writes nothing`, async () => {
      const calls: CallStore = make();
      const a = await calls.load("c1");
      const b = await calls.load("c1");
      assert.equal(a.rev, 0);
      a.failed = 1;
      await calls.save("c1", a);
      assert.equal(a.rev, 1);
      b.failed = 7;
      await assert.rejects(calls.save("c1", b), CallConflict);
      const now = await calls.load("c1");
      assert.deepEqual([now.failed, now.rev], [1, 1]);
      now.changes = 2;
      await calls.save("c1", now);
      assert.deepEqual([(await calls.load("c1")).failed, (await calls.load("c1")).changes, (await calls.load("c1")).rev], [1, 2, 2]);
      const fresh = emptyCall();
      await assert.rejects(calls.save("c1", fresh), CallConflict); // a document that thinks the call is new cannot overwrite a stored one
    });
  }
});

for (const backend of ["memory model", "postgres (PGlite)"] as const) {
  describe(`parallel tool calls of one call (${backend})`, () => {
    const setup = async () => {
      const store: Store = backend === "memory model" ? new MemoryStore({ clock: () => NOW }) : pgStore();
      const raw: CallStore = backend === "memory model" ? new MemoryCallStore() : new PgCallStore(pg.pool, () => NOW_MS);
      await client(store);
      const wait = barrier(6);
      const concurrent: CallStore = { // every one of six requests loads the same initial memory before any of them saves
        async load(id: string) { const snapshot = await raw.load(id); await wait(); return snapshot; },
        save: raw.save.bind(raw), putPending: raw.putPending.bind(raw), getPending: raw.getPending.bind(raw),
      };
      return { world: await openWorld({ clock: () => NOW_MS, store, calls: concurrent, seed: false }), raw, store };
    };

    test("S2-C: six unmatched-name lookups keep five failures and refuse the sixth", async () => {
      const { world, raw } = await setup();
      const results = await Promise.all(Array.from({ length: 6 }, (_, i) => world.call("find_appointments", "one-call", { phone: "2125550142", name: `Unknown Person ${i}` })));
      const doc = await raw.load("one-call");
      assert.equal(doc.failed, 5, "five failed matches are retained");
      assert.equal(results.filter((r) => r.reason === "too_many_attempts").length, 1);
      assert.equal(results.filter((r) => r.reason === "name_unmatched").length, 5);
    });

    test("parallel last-visit lookups count every attempt", async () => {
      const { world, raw } = await setup();
      const results = await Promise.all(Array.from({ length: 6 }, () => world.call("last_completed_visit", "visit-call", { phone: "2125550142", name: "Willa Hartwell", service_category: "massage" })));
      const doc = await raw.load("visit-call");
      assert.equal(doc.attempts, 3, "the limit of three lookups holds under parallel calls");
      assert.equal(results.filter((r) => r.reason === "too_many_attempts").length, 3);
    });
  });
}

/** A store whose preference reads can be made to lie once or twice: the view a tool had just BEFORE a racing write landed. */
function withStaleReads(store: Store, lies: { pretend: Preference | null; left: number }): Store {
  const prefs = new Proxy(store.preferences, {
    get(target, prop) {
      const value = Reflect.get(target, prop, target);
      if (prop === "get") return async (c: number, f: string) => (lies.left > 0 ? (lies.left--, lies.pretend) : target.get(c, f));
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
  return new Proxy(store, {
    get(target, prop) {
      if (prop === "preferences") return prefs;
      const value = Reflect.get(target, prop, target);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

for (const backend of ["memory model", "postgres (PGlite)"] as const) {
  describe(`a confirmation overtaken between the check and the write (${backend})`, () => {
    const args = { phone: "2125550142", name: "Willa Hartwell", service: "Deep Tissue Massage" };
    const setup = async () => {
      const store: Store = backend === "memory model" ? new MemoryStore({ clock: () => NOW }) : pgStore();
      const clientId = await client(store);
      const lies = { pretend: null as Preference | null, left: 0 };
      const world = await openWorld({ clock: () => NOW_MS, store: withStaleReads(store, lies), calls: new MemoryCallStore(), seed: false });
      const ids = world.dataset.roster.filter((s) => s.categories.has("massage")).map((s) => [s.name, s.staffId] as const);
      const [tamsin, other] = [ids.find(([n]) => n.startsWith("Tamsin"))!, ids.find(([n]) => !n.startsWith("Tamsin"))!];
      return { world, store, clientId, lies, tamsin, other };
    };

    test("a saved choice that changed after the check is read back again, never overwritten", async () => {
      const { world, store, clientId, lies, tamsin, other } = await setup();
      const readback = await world.call("save_preference", "c1", { ...args, staff: tamsin[0] });
      assert.equal(readback.status, "confirmation_required");
      await store.preferences.set(clientId, FAMILY, other[1]); // someone else saves a different choice
      lies.left = 1; // ...but this confirmation's check still saw "nothing saved"
      const confirmed = await world.call("save_preference", "c1", { ...args, staff: tamsin[0], confirmed: true, confirmation_token: readback.confirmation_token });
      assert.equal(confirmed.status, "confirmation_required");
      assert.match(confirmed.speak, /changed since I read it out/);
      assert.equal((await store.preferences.get(clientId, FAMILY))!.staffId, other[1]);
    });

    test("a removal of a choice that changed after the check is read back again, never applied", async () => {
      const { world, store, clientId, lies, tamsin, other } = await setup();
      await store.preferences.set(clientId, FAMILY, tamsin[1]);
      const was = (await store.preferences.get(clientId, FAMILY))!;
      const readback = await world.call("remove_preference", "c2", args);
      assert.equal(readback.status, "confirmation_required");
      await store.preferences.set(clientId, FAMILY, other[1]);
      lies.pretend = was;
      lies.left = 1;
      const confirmed = await world.call("remove_preference", "c2", { ...args, confirmed: true, confirmation_token: readback.confirmation_token });
      assert.equal(confirmed.status, "confirmation_required");
      assert.match(confirmed.speak, /changed since I read it out/);
      assert.equal((await store.preferences.get(clientId, FAMILY))!.staffId, other[1]);
    });
  });
}
