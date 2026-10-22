/* The store contract: the SAME tests run against the in-memory model of the Python store and against Postgres (PGlite through @netlify/database-dev, the local emulator). Each writer is atomic,
 * overlap is refused whoever asks first (the exclusion constraints, not the application, decide when requests race), a refusal leaves nothing behind, a repeated request returns the first answer,
 * and a reschedule may overlap its own old time. This says nothing about hosted Netlify Database: its connector, cold start and concurrency are verified separately. */

import assert from "node:assert/strict";
import { after, before, beforeEach, describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { Refused } from "../../src/appointments.ts";
import type { NewBooking } from "../../src/appointments.ts";
import { MemoryStore } from "../../src/memoryStore.ts";
import { PgStore } from "../../src/pgStore.ts";
import { StoreError } from "../../src/store.ts";
import type { Store } from "../../src/store.ts";

const NOW = 1_800_000_000; // epoch seconds, a whole number of minutes
const HOUR = 3600;
const DAY = 86400;
const T = (day: number, minutes: number) => NOW + day * DAY + minutes * 60;

const booking = (over: Partial<NewBooking> = {}): NewBooking => ({
  clientName: "Willa Hartwell", phone: "+12125550142", serviceId: "massage-swedish-massage-60", serviceName: "Swedish Massage, 1 hour", category: "massage", staffId: "910001",
  startUtc: T(1, 600), durationMinutes: 60, bufferBeforeMinutes: 0, bufferAfterMinutes: 15, origin: "voice", ...over,
});
const alert = (saved: { reference: string; version: number }) => ({ kind: "booked", body: `booked ${saved.reference} v${saved.version}` });
const allowAll = async () => undefined;

interface Backend {
  name: string;
  make(): Promise<{ store: Store; reset(): Promise<void>; close(): Promise<void> }>;
}

async function pgBackend(): Promise<Backend["make"] extends () => Promise<infer R> ? R : never> {
  const { NetlifyDB } = await import("@netlify/database-dev");
  const pg = (await import("pg")).default;
  const db = new NetlifyDB({ logger: () => undefined });
  const url = await db.start();
  await db.applyMigrations(fileURLToPath(new URL("../../netlify/database/migrations", import.meta.url)));
  const pool = new pg.Pool({ connectionString: url, max: 6 });
  const tables = "clients, appointments, appointment_events, requests, outbox, client_preferences, preference_requests, preference_events, call_state, pending_confirmations, meta";
  return {
    store: new PgStore(pool, { clock: () => NOW }),
    reset: async () => void (await pool.query(`truncate ${tables} restart identity cascade`)),
    close: async () => { await pool.end(); await db.stop(); },
  };
}

const backends: Backend[] = [
  { name: "memory model", make: async () => { const store = new MemoryStore({ clock: () => NOW }); return { store, reset: async () => undefined, close: async () => undefined }; } },
  { name: "postgres (PGlite)", make: pgBackend },
];

for (const backend of backends) {
  describe(`store contract: ${backend.name}`, () => {
    let ctx: Awaited<ReturnType<Backend["make"]>>;
    let store: Store;
    before(async () => {
      ctx = await backend.make();
    });
    after(async () => ctx.close());
    beforeEach(async () => {
      if (backend.name.startsWith("memory")) { ctx = await backend.make(); }
      else await ctx.reset();
      store = ctx.store;
    });

    test("a booking is saved once with its client, version 1, an event and (if asked) one alert", async () => {
      const out = await store.book(booking(), { outbox: alert });
      assert.equal(out.status, "booked");
      const a = out.appointment!;
      assert.equal(a.version, 1);
      assert.equal(a.status, "confirmed");
      assert.equal(a.clientName, "Willa Hartwell");
      assert.match(a.reference, /^[2-9A-HJKMNP-Z]{6}$/);
      assert.deepEqual((await store.events(a.reference)).map((e) => e.kind), ["booked"]);
      const rows = await store.outboxRows();
      assert.deepEqual(rows.map((r) => [r.dedupeKey, r.status]), [[`booked:${a.reference}:1`, "pending"]]);
      assert.deepEqual(await store.counts(), { appointments: { confirmed: 1 }, clients: 1, outbox: { pending: 1 } });
      assert.equal((await store.clientsWithPhone("+12125550142")).length, 1);
    });

    test("the same person by another spelling of case and spacing is the same client; a different name on the same number is another", async () => {
      await store.book(booking());
      const again = await store.book(booking({ clientName: "  willa   HARTWELL ", staffId: "910002", startUtc: T(2, 600) }));
      assert.equal(again.status, "booked");
      assert.equal(again.appointment!.clientName, "Willa Hartwell", "the stored name is kept");
      await store.book(booking({ clientName: "Ansel Hartwell", staffId: "910003", startUtc: T(3, 600) }));
      assert.deepEqual((await store.clientsWithPhone("+12125550142")).map((c) => c.name), ["Willa Hartwell", "Ansel Hartwell"]);
    });

    test("a repeated request returns the first result and saves nothing more; a key cannot change kind", async () => {
      const first = await store.book(booking({ requestKey: "k1" }), { outbox: alert });
      const second = await store.book(booking({ requestKey: "k1" }), { outbox: alert });
      assert.equal(second.status, "replayed");
      assert.equal(second.appointment!.reference, first.appointment!.reference);
      assert.deepEqual((await store.counts()).appointments, { confirmed: 1 });
      assert.equal((await store.outboxRows()).length, 1);
      assert.equal(await store.requestReference("k1", "book"), first.appointment!.reference);
      assert.equal(await store.requestReference("k1", "cancel"), null);
      const wrong = await store.cancel(first.appointment!.reference, { requestKey: "k1" });
      assert.equal(wrong.status, "request_key_reused");
    });

    test("two bookings of one technician may not overlap, cleanup time counts, touching is fine, and another technician is not affected", async () => {
      assert.equal((await store.book(booking())).status, "booked"); // 10:00-11:00, cleanup to 11:15
      for (const minutes of [630, 660, 540 + 15]) {
        const clash = await store.book(booking({ clientName: `Other ${minutes}`, phone: "+12125550001", startUtc: T(1, minutes) }));
        assert.equal(clash.status, "unavailable", String(minutes));
        assert.equal(clash.reason, "slot_taken");
      }
      assert.equal((await store.book(booking({ clientName: "Touching", phone: "+12125550002", startUtc: T(1, 675) }))).status, "booked", "11:15 touches the end of the cleanup");
      assert.equal((await store.book(booking({ clientName: "Elsewhere", phone: "+12125550003", staffId: "910002" }))).status, "booked", "the same time with someone else");
    });

    test("a client is never in two overlapping services, whoever performs them", async () => {
      await store.book(booking());
      const clash = await store.book(booking({ staffId: "910002", startUtc: T(1, 630) }));
      assert.equal(clash.status, "unavailable");
      assert.equal(clash.reason, "client_already_booked");
      assert.ok(clash.appointment, "the clashing appointment is returned");
      assert.equal((await store.book(booking({ staffId: "910002", startUtc: T(1, 660) }))).status, "booked", "back to back is allowed");
    });

    test("a refused booking leaves nothing behind: no client, no event, no alert, no request key", async () => {
      await store.book(booking());
      const refused = await store.book(booking({ clientName: "Newcomer", phone: "+12125550999", startUtc: T(1, 620), requestKey: "nk" }), { outbox: alert });
      assert.equal(refused.status, "unavailable");
      assert.equal((await store.clientsWithPhone("+12125550999")).length, 0);
      assert.equal(await store.requestReference("nk", "book"), null);
      assert.deepEqual(await store.counts(), { appointments: { confirmed: 1 }, clients: 1, outbox: {} });
      const veto = await store.book(booking({ clientName: "Vetoed", phone: "+12125550998", staffId: "910005" }), { validate: async () => { throw new Refused("unavailable", "not_free"); } });
      assert.equal(veto.status, "unavailable");
      assert.equal(veto.reason, "not_free");
      assert.equal((await store.clientsWithPhone("+12125550998")).length, 0);
    });

    test("the validator is asked inside the write with a reader of the day's confirmed bookings, and may refuse", async () => {
      await store.book(booking());
      let seen = -1;
      const out = await store.book(booking({ clientName: "Seen", phone: "+12125550997", staffId: "910006" }), {
        validate: async (rowsBetween, placement) => { seen = (await rowsBetween(placement.startUtc - HOUR, placement.startUtc + 2 * HOUR, ["confirmed"])).length; },
      });
      assert.equal(out.status, "booked");
      assert.equal(seen, 1);
    });

    test("cancel: stale versions, a repeat, a started appointment, and the slot is free again", async () => {
      const made = (await store.book(booking())).appointment!;
      const stale = await store.cancel(made.reference, { expectedVersion: 7 });
      assert.equal(stale.status, "stale");
      assert.equal(stale.reason, "changed_since_confirmation");
      assert.equal((await store.get(made.reference))!.status, "confirmed");
      const done = await store.cancel(made.reference, { expectedVersion: 1, reason: "  busy   that   day\t", requestKey: "c1", outbox: (s) => ({ kind: "cancelled", body: `cancelled ${s.reference}` }) });
      assert.equal(done.status, "cancelled");
      assert.equal(done.appointment!.version, 2);
      assert.equal(done.appointment!.cancelReason, "busy that day");
      assert.equal(done.previous!.status, "confirmed");
      assert.equal((await store.cancel(made.reference, { expectedVersion: 1 })).status, "already_cancelled", "before the version check: a repeat carries the old version");
      assert.equal((await store.cancel(made.reference, { requestKey: "c1" })).status, "replayed");
      assert.equal((await store.cancel("ZZZZZZ")).status, "not_found");
      assert.deepEqual((await store.outboxRows()).map((r) => r.dedupeKey), [`cancelled:${made.reference}:2`]);
      assert.equal((await store.book(booking({ clientName: "Takes It", phone: "+12125550004" }))).status, "booked", "a cancelled slot is free again");
      const soon = (await store.book(booking({ clientName: "Soon", phone: "+12125550005", staffId: "910007", startUtc: NOW - 600 }))).appointment!;
      const started = await store.cancel(soon.reference);
      assert.equal(started.status, "not_cancellable");
      assert.equal(started.reason, "already_started");
    });

    test("reschedule: it moves, it may overlap its OWN old time, it may not take someone else's, and a repeat changes nothing", async () => {
      const mine = (await store.book(booking())).appointment!;
      const other = (await store.book(booking({ clientName: "Neighbour", phone: "+12125550006", startUtc: T(1, 720) }))).appointment!; // 12:00
      const nudge = await store.reschedule(mine.reference, { startUtc: T(1, 630), expectedVersion: 1, requestKey: "r1", outbox: (s, p) => ({ kind: "rescheduled", body: `moved ${s.reference} from ${p!.startUtc}` }) });
      assert.equal(nudge.status, "rescheduled");
      assert.equal(nudge.appointment!.startUtc, T(1, 630));
      assert.equal(nudge.appointment!.version, 2);
      assert.equal(nudge.previous!.startUtc, T(1, 600));
      assert.equal(nudge.appointment!.reference, mine.reference, "a move keeps the reference");
      assert.equal((await store.reschedule(mine.reference, { startUtc: T(1, 630), requestKey: "r1" })).status, "replayed");
      assert.equal((await store.reschedule(mine.reference, { startUtc: T(1, 630) })).status, "unchanged");
      const taken = await store.reschedule(mine.reference, { startUtc: T(1, 700) });
      assert.equal(taken.status, "unavailable");
      assert.equal(taken.reason, "slot_taken");
      assert.equal((await store.get(mine.reference))!.startUtc, T(1, 630), "a refused move leaves the original untouched");
      const away = await store.reschedule(mine.reference, { startUtc: T(1, 630), staffId: "910002", expectedVersion: 2 });
      assert.equal(away.status, "rescheduled");
      assert.equal(away.appointment!.staffId, "910002");
      assert.equal((await store.reschedule(mine.reference, { startUtc: T(2, 600), expectedVersion: 1 })).status, "stale");
      assert.equal((await store.reschedule(other.reference, { startUtc: T(1, 630), staffId: "910002" })).status, "unavailable");
      assert.equal((await store.reschedule("ZZZZZZ", { startUtc: T(2, 600) })).status, "not_found");
      assert.equal((await store.events(mine.reference)).filter((e) => e.kind === "rescheduled").length, 2);
      assert.equal((await store.outboxRows()).length, 1, "only the move that asked for an alert wrote one");
    });

    test("a move into the same client's other service is refused as the client's own overlap", async () => {
      const a = (await store.book(booking())).appointment!;
      await store.book(booking({ staffId: "910002", startUtc: T(1, 720) }));
      const clash = await store.reschedule(a.reference, { startUtc: T(1, 700), staffId: "910003" });
      assert.equal(clash.status, "unavailable");
      assert.equal(clash.reason, "client_already_booked");
    });

    test("seeds are stable, settled once, and never notify; history rows are never validated", async () => {
      const seed = booking({ clientName: "Cedric Lowe", phone: "+12125550177", origin: "seed", seedKey: "upcoming:cedric:x:2026-10-22", startUtc: T(1, 840), staffId: "910004" });
      const one = await store.book(seed, { outbox: alert });
      const two = await store.book(seed, { outbox: alert });
      assert.equal(one.appointment!.reference, two.appointment!.reference);
      assert.equal(two.status, "replayed");
      assert.equal((await store.outboxRows()).length, 0, "creating a seed never notifies");
      assert.equal(await store.seededAfter("upcoming:cedric:", NOW), true);
      assert.equal(await store.seededAfter("upcoming:nobody:", NOW), false);
      const past = await store.addHistory(booking({ clientName: "Cedric Lowe", phone: "+12125550177", origin: "seed", seedKey: "history:cedric:y", startUtc: T(-20, 660), staffId: "910004" }), "completed");
      assert.equal(past.appointment!.status, "completed");
      assert.equal((await store.addHistory(booking({ origin: "seed", seedKey: "history:cedric:y" }), "completed")).status, "replayed");
      assert.deepEqual((await store.forClient(one.appointment!.clientId, ["completed"])).map((r) => r.reference), [past.appointment!.reference]);
      await assert.rejects(store.addHistory(booking(), "confirmed"));
      const old = await store.addHistory(booking({ clientName: "Old", phone: "+12125550010", origin: "seed", seedKey: "upcoming:old:z", startUtc: NOW - 2 * DAY, staffId: "910008" }), "completed");
      assert.equal(old.status, "booked");
      assert.equal(await store.settlePastSeeds(), 0);
    });

    test("reads: between, occupiedBetween, forClient and the change stamp follow the data", async () => {
      const a = (await store.book(booking())).appointment!;
      const stamp = await store.changeStamp();
      assert.deepEqual((await store.between(T(1, 0), T(2, 0))).map((r) => r.reference), [a.reference]);
      assert.deepEqual((await store.between(T(1, 660), T(2, 0))).map((r) => r.reference), [], "service time only: 11:00 is the end");
      assert.deepEqual((await store.occupiedBetween(T(1, 660), T(1, 700), { staffId: "910001" })).map((r) => r.reference), [a.reference], "but its cleanup is occupied time");
      assert.deepEqual((await store.between(T(1, 0), T(2, 0), { staffId: "910002" })).map((r) => r.reference), []);
      assert.deepEqual((await store.forClient(a.clientId)).map((r) => r.reference), [a.reference]);
      await store.cancel(a.reference);
      assert.notEqual(await store.changeStamp(), stamp);
      assert.deepEqual(await store.between(T(1, 0), T(2, 0)), [], "a cancelled appointment is not in the confirmed view");
      assert.equal((await store.between(T(1, 0), T(2, 0), { statuses: ["cancelled"] })).length, 1);
    });

    test("racing for one slot: exactly one wins whoever asks, and the losers are told it is taken", async () => {
      const racers = Array.from({ length: 8 }, (_, i) => store.book(booking({ clientName: `Racer ${i}`, phone: `+1212555${String(2000 + i)}`, startUtc: T(1, 600 + (i % 4) * 15) })));
      const results = await Promise.all(racers);
      assert.equal(results.filter((r) => r.status === "booked").length, 1, JSON.stringify(results.map((r) => r.status)));
      assert.equal(results.filter((r) => r.status === "unavailable" && r.reason === "slot_taken").length, 7);
      assert.deepEqual(await store.counts(), { appointments: { confirmed: 1 }, clients: 1, outbox: {} }, "the losers' clients were not kept");
    });

    test("racing with one request key: one booking, the rest are the same answer", async () => {
      const results = await Promise.all(Array.from({ length: 6 }, () => store.book(booking({ requestKey: "same" }), { outbox: alert })));
      assert.equal(results.filter((r) => r.status === "booked").length, 1);
      assert.equal(results.filter((r) => r.status === "replayed").length, 5);
      assert.equal(new Set(results.map((r) => r.appointment!.reference)).size, 1);
      assert.equal((await store.outboxRows()).length, 1);
    });

    test("racing for one client's two overlapping services: one wins", async () => {
      const results = await Promise.all([store.book(booking({ staffId: "910001" })), store.book(booking({ staffId: "910002", startUtc: T(1, 630) }))]);
      assert.deepEqual(results.map((r) => r.status).sort(), ["booked", "unavailable"]);
    });

    test("racing two moves onto one slot: one wins and the other stays where it was", async () => {
      const a = (await store.book(booking({ clientName: "A", phone: "+12125550021", startUtc: T(1, 540) }))).appointment!;
      const b = (await store.book(booking({ clientName: "B", phone: "+12125550022", startUtc: T(1, 780) }))).appointment!;
      const results = await Promise.all([store.reschedule(a.reference, { startUtc: T(1, 900) }), store.reschedule(b.reference, { startUtc: T(1, 900) })]);
      assert.deepEqual(results.map((r) => r.status).sort(), ["rescheduled", "unavailable"]);
      const [ra, rb] = await Promise.all([store.get(a.reference), store.get(b.reference)]);
      assert.equal([ra!.startUtc, rb!.startUtc].filter((t) => t === T(1, 900)).length, 1);
    });

    test("preferences: set, repeat, change, unchanged, remove, and a missing client is refused", async () => {
      const client = (await store.book(booking())).appointment!.clientId;
      const first = await store.preferences.set(client, "massage-swedish-massage", "910001", { requestKey: "p1" });
      assert.deepEqual([first.status, first.preference!.version], ["saved", 1]);
      assert.equal((await store.preferences.set(client, "massage-swedish-massage", "910001", { requestKey: "p1" })).status, "replayed");
      assert.equal((await store.preferences.set(client, "massage-swedish-massage", "910001")).status, "unchanged");
      const changed = await store.preferences.set(client, "massage-swedish-massage", "910006");
      assert.deepEqual([changed.status, changed.preference!.version, changed.previous], ["saved", 2, "910001"]);
      assert.deepEqual((await store.preferences.forClient(client)).map((p) => [p.familyId, p.staffId]), [["massage-swedish-massage", "910006"]]);
      const held = { staffId: "910006", version: 2 };
      assert.equal((await store.preferences.remove(client, "massage-swedish-massage", { requestKey: "p2", expected: held })).status, "removed");
      assert.equal((await store.preferences.remove(client, "massage-swedish-massage", { requestKey: "p2", expected: held })).status, "replayed");
      assert.equal((await store.preferences.remove(client, "massage-swedish-massage", { expected: null })).status, "nothing_to_remove");
      await assert.rejects(store.preferences.remove(client, "massage-swedish-massage", {} as any), StoreError); // a removal must always say what it removes
      assert.equal(await store.preferences.get(client, "massage-swedish-massage"), null);
      await assert.rejects(store.preferences.set(99999, "x", "910001"), StoreError);
    });

    test("preferences: a change applies only to the state the caller confirmed (a stale one changes nothing)", async () => {
      const client = (await store.book(booking())).appointment!.clientId;
      const family = "massage-swedish-massage";
      assert.equal((await store.preferences.set(client, family, "910001", { expected: { staffId: "910009", version: 1 } })).status, "stale"); // nothing saved, something else expected
      assert.equal(await store.preferences.get(client, family), null);
      assert.equal((await store.preferences.set(client, family, "910001", { expected: null })).status, "saved");
      assert.equal((await store.preferences.set(client, family, "910006", { expected: null })).status, "stale"); // expected none, but one is saved
      const moved = await store.preferences.set(client, family, "910006", { requestKey: "m1", expected: { staffId: "910001", version: 1 } });
      assert.deepEqual([moved.status, moved.preference!.staffId, moved.preference!.version], ["saved", "910006", 2]);
      const late = await store.preferences.set(client, family, "910008", { requestKey: "m2", expected: { staffId: "910001", version: 1 } }); // confirmed against the old state
      assert.deepEqual([late.status, late.preference!.staffId], ["stale", "910006"]);
      assert.equal((await store.preferences.set(client, family, "910008", { requestKey: "m2", expected: { staffId: "910006", version: 2 } })).status, "replayed"); // a stale answer still used its key
      assert.equal((await store.preferences.get(client, family))!.staffId, "910006");
      // an old value restored later is a NEW state: the version moved on, so a confirmation of the first one no longer applies
      await store.preferences.set(client, family, "910001", { expected: { staffId: "910006", version: 2 } });
      const gone = await store.preferences.remove(client, family, { requestKey: "r1", expected: { staffId: "910001", version: 1 } });
      assert.deepEqual([gone.status, gone.preference!.staffId, gone.preference!.version], ["stale", "910001", 3]);
      assert.equal((await store.preferences.remove(client, family, { expected: { staffId: "910001", version: 3 } })).status, "removed");
    });

    test("preferences: a repeated key never reapplies its change, even after the state moved on or was recreated", async () => {
      const client = (await store.book(booking())).appointment!.clientId;
      const family = "massage-swedish-massage";
      assert.equal((await store.preferences.set(client, family, "910001", { requestKey: "k" })).status, "saved");
      await store.preferences.set(client, family, "910002");
      assert.equal((await store.preferences.set(client, family, "910001", { requestKey: "k" })).status, "replayed"); // not an unconditional overwrite
      assert.equal((await store.preferences.get(client, family))!.staffId, "910002");
      // a removal, then the SAME technician saved again at the same version: the old removal's retry must replay, not remove it
      assert.equal((await store.preferences.remove(client, family, { requestKey: "r", expected: { staffId: "910002", version: 2 } })).status, "removed");
      await store.preferences.set(client, family, "910002");
      assert.deepEqual([(await store.preferences.get(client, family))!.staffId, (await store.preferences.get(client, family))!.version], ["910002", 1]);
      assert.equal((await store.preferences.remove(client, family, { requestKey: "r", expected: { staffId: "910002", version: 1 } })).status, "replayed");
      assert.equal((await store.preferences.get(client, family))!.staffId, "910002");
    });

    test("preferences: of six racing retries of one confirmation exactly one has an effect", async () => {
      const client = (await store.book(booking())).appointment!.clientId;
      const family = "massage-deep-tissue-massage";
      const results = await Promise.all(Array.from({ length: 6 }, () => store.preferences.set(client, family, "910001", { requestKey: "same", expected: null })));
      assert.deepEqual(results.map((r) => r.status).sort(), ["replayed", "replayed", "replayed", "replayed", "replayed", "saved"]);
      assert.equal((await store.preferences.get(client, family))!.version, 1);
      const removals = await Promise.all(Array.from({ length: 6 }, () => store.preferences.remove(client, family, { requestKey: "same-remove", expected: { staffId: "910001", version: 1 } })));
      assert.deepEqual(removals.map((r) => r.status).sort(), ["removed", "replayed", "replayed", "replayed", "replayed", "replayed"]);
    });
  });
}
