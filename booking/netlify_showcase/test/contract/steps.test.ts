/* One tool step of one call at a time (S4-A, S4-B): the lease on a call's memory, the budget of changes per call enforced before the effects, a step that already made an effect is never redone
 * (its change is counted once and its answer stays true), and a confirmation read back before a removal-and-recreation does not apply to the recreated choice. Both backends where it matters;
 * local emulation only, not hosted concurrency. */

import assert from "node:assert/strict";
import { after, before, beforeEach, describe, test } from "node:test";

import { MemoryCallStore } from "../../src/callState.ts";
import type { CallStore } from "../../src/callState.ts";
import { MemoryStore } from "../../src/memoryStore.ts";
import { PgCallStore, PgStore } from "../../src/pgStore.ts";
import type { Store } from "../../src/store.ts";
import { MAX_CHANGES_PER_CALL } from "../../src/tools.ts";
import { openWorld } from "../../src/world.ts";
import { startPg } from "./pg.ts";

const NOW_MS = Date.parse("2026-10-22T12:00:00Z");
const NOW = NOW_MS / 1000;
const FAMILY = "massage-deep-tissue-massage";
const args = { phone: "2125550142", name: "Willa Hartwell", service: "Deep Tissue Massage" };
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

let pg: Awaited<ReturnType<typeof startPg>>;
before(async () => { pg = await startPg(); });
after(async () => { await pg?.close(); });
beforeEach(async () => { await pg.reset(); });

for (const backend of ["memory model", "postgres (PGlite)"] as const) {
  describe(`one step of a call at a time (${backend})`, () => {
    const setup = async (wrap: (calls: CallStore) => CallStore = (c) => c) => {
      const store: Store = backend === "memory model" ? new MemoryStore({ clock: () => NOW }) : new PgStore(pg.pool, { clock: () => NOW });
      const raw: CallStore = backend === "memory model" ? new MemoryCallStore() : new PgCallStore(pg.pool, () => NOW_MS);
      const booked = await store.book({
        clientName: "Willa Hartwell", phone: "+12125550142", serviceId: "massage-deep-tissue-massage-60", serviceName: "Deep Tissue Massage, 1 hour", category: "massage", staffId: "910001",
        startUtc: NOW + 86400, durationMinutes: 60, bufferAfterMinutes: 15,
      });
      const clientId = booked.appointment!.clientId;
      const world = await openWorld({ clock: () => NOW_MS, store, calls: wrap(raw), seed: false });
      const roster = world.dataset.roster.filter((s) => s.categories.has("massage"));
      const tamsin = roster.find((s) => s.name.startsWith("Tamsin"))!;
      const other = roster.find((s) => !s.name.startsWith("Tamsin"))!;
      return { world, store, raw, clientId, tamsin, other, run: (callId: string, work: (doc: any) => Promise<any>) => (world.tools as any).run(callId, work) as Promise<any> };
    };

    test("the budget of changes holds under parallel steps: it is checked before the effect, one step at a time", async () => {
      const { world, raw, run } = await setup();
      const tools = world.tools as any;
      const results = await Promise.all(Array.from({ length: 8 }, () => run("budget-call", async (doc) => {
        if (doc.changes >= MAX_CHANGES_PER_CALL) return { status: "refused" };
        await sleep(15); // the time an effect takes
        tools.willChange(doc);
        tools.countChange(doc);
        return { status: "done" };
      })));
      assert.equal(results.filter((r) => r.status === "done").length, MAX_CHANGES_PER_CALL);
      assert.equal(results.filter((r) => r.status === "refused").length, 8 - MAX_CHANGES_PER_CALL);
      const doc = await raw.load("budget-call");
      assert.deepEqual([doc.changes, doc.lock], [MAX_CHANGES_PER_CALL, null], "and the lease is released");
    });

    test("a step waits for the one ahead of it, and takes over from one that died", async () => {
      const { raw, run } = await setup();
      const order: string[] = [];
      const slow = run("lease-call", async () => { order.push("slow-start"); await sleep(120); order.push("slow-end"); return { status: "a" }; });
      await sleep(30);
      const quick = run("lease-call", async () => { order.push("quick"); return { status: "b" }; });
      await Promise.all([slow, quick]);
      assert.deepEqual(order, ["slow-start", "slow-end", "quick"]);
      // a holder that died: its lease is in the past
      const dead = await raw.load("dead-call");
      dead.lock = { holder: "dead-instance", until: Date.now() - 1000 };
      await raw.save("dead-call", dead);
      assert.equal((await run("dead-call", async () => ({ status: "ok" }))).status, "ok");
    });

    test("S4-A: a step that made its effect and then lost the compare-and-set is not redone: its change is counted once and its answer stays 'saved'", async () => {
      let interfere = false;
      const { world, raw, clientId, tamsin, store } = await setup((calls) => ({
        load: calls.load.bind(calls),
        putPending: calls.putPending.bind(calls),
        getPending: calls.getPending.bind(calls),
        async save(callId, doc) {
          if (interfere && doc.lock === null) { // the step's final save: a concurrent read-only request saves the same prior revision just before it
            interfere = false;
            await calls.save(callId, await calls.load(callId));
          }
          return calls.save(callId, doc);
        },
      }));
      const readback = await world.call("save_preference", "c1", { ...args, staff: tamsin.name });
      assert.equal(readback.status, "confirmation_required");
      interfere = true;
      const confirmed = await world.call("save_preference", "c1", { ...args, staff: tamsin.name, confirmed: true, confirmation_token: readback.confirmation_token });
      assert.equal(confirmed.status, "preference_saved", "the answer is the one the effect earned, not 'unchanged'");
      assert.equal((await store.preferences.get(clientId, FAMILY))!.staffId, tamsin.staffId);
      const doc = await raw.load("c1");
      assert.deepEqual([doc.changes, doc.lock], [1, null], "the change was counted once, and the lease released");
    });

    test("S4-A (removal): the same holds for a confirmed removal", async () => {
      let interfere = false;
      const { world, raw, clientId, tamsin, store } = await setup((calls) => ({
        load: calls.load.bind(calls), putPending: calls.putPending.bind(calls), getPending: calls.getPending.bind(calls),
        async save(callId, doc) {
          if (interfere && doc.lock === null) { interfere = false; await calls.save(callId, await calls.load(callId)); }
          return calls.save(callId, doc);
        },
      }));
      await store.preferences.set(clientId, FAMILY, tamsin.staffId);
      const readback = await world.call("remove_preference", "c3", args);
      interfere = true;
      const confirmed = await world.call("remove_preference", "c3", { ...args, confirmed: true, confirmation_token: readback.confirmation_token });
      assert.equal(confirmed.status, "preference_removed");
      assert.equal(await store.preferences.get(clientId, FAMILY), null);
      assert.equal((await raw.load("c3")).changes, 1);
    });

    test("a step that made an effect and lost the compare-and-set has EVERYTHING it changed folded into the newer memory, once", async () => {
      let interfere = true;
      const { raw, run, world } = await setup((calls) => ({
        load: calls.load.bind(calls), putPending: calls.putPending.bind(calls), getPending: calls.getPending.bind(calls),
        async save(callId, doc) {
          if (interfere && doc.lock === null) { interfere = false; const other = await calls.load(callId); other.failed = 4; other.attempts = 1; other.numbers = ["theirs"]; await calls.save(callId, other); }
          return calls.save(callId, doc);
        },
      }));
      let runs = 0;
      const tools = world.tools as any;
      const result = await run("fold-call", async (doc) => {
        runs += 1;
        tools.willChange(doc);
        doc.failed += 1; doc.attempts += 2; doc.changes += 1; doc.numbers.push("ours"); doc.preferred = 600; doc.identity = { phone: "+12125550142", name: "Willa Hartwell", key: "k", clientId: 7 };
        return { status: "done" };
      });
      assert.equal(runs, 1, "an effect is never repeated");
      assert.equal(result.status, "done");
      const doc = await raw.load("fold-call");
      assert.deepEqual([doc.failed, doc.attempts, doc.changes, doc.numbers.sort(), doc.preferred, doc.identity?.clientId, doc.lock], [5, 3, 1, ["ours", "theirs"], 600, 7, null]);
    });

    test("a step that made NO effect and lost the compare-and-set is redone against the newer memory", async () => {
      let interfere = true;
      let runs = 0;
      const { raw, run } = await setup((calls) => ({
        load: calls.load.bind(calls), putPending: calls.putPending.bind(calls), getPending: calls.getPending.bind(calls),
        async save(callId, doc) {
          if (interfere && doc.lock === null) { interfere = false; const other = await calls.load(callId); other.failed = 2; await calls.save(callId, other); }
          return calls.save(callId, doc);
        },
      }));
      const result = await run("redo-call", async (doc) => { runs += 1; doc.failed += 1; return { status: "ok", seenFailed: doc.failed }; });
      assert.equal(runs, 2);
      assert.equal(result.seenFailed, 3, "the redo saw the other request's failure");
      assert.equal((await raw.load("redo-call")).failed, 3);
    });

    test("S4-B: a removal read back for one choice does not apply to the same technician saved again afterwards", async () => {
      const { world, store, clientId, tamsin } = await setup();
      await store.preferences.set(clientId, FAMILY, tamsin.staffId);
      const readback = await world.call("remove_preference", "c2", args); // read back: remove Tamsin
      assert.equal(readback.status, "confirmation_required");
      // another confirmed action removes it, then a newer save restores the same technician (version starts again at 1)
      const first = (await store.preferences.get(clientId, FAMILY))!;
      await store.preferences.remove(clientId, FAMILY, { expected: { staffId: first.staffId, generation: first.generation } });
      await store.preferences.set(clientId, FAMILY, tamsin.staffId);
      const recreated = (await store.preferences.get(clientId, FAMILY))!;
      assert.equal(recreated.version, first.version, "the version alone cannot tell them apart");
      const confirmed = await world.call("remove_preference", "c2", { ...args, confirmed: true, confirmation_token: readback.confirmation_token });
      assert.equal(confirmed.status, "confirmation_required", "the old confirmation needs a new read-back");
      assert.match(confirmed.speak, /changed since I read it out/);
      assert.equal((await store.preferences.get(clientId, FAMILY))!.staffId, tamsin.staffId, "the newly saved choice survived");
    });
  });
}
