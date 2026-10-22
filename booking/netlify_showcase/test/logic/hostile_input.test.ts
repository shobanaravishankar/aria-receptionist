/* Hostile and malformed tool arguments: whatever a caller, a model or an attacker sends, a tool ANSWERS (a domain reply with a status and words) and never throws, never hangs and never lets a
 * non-finite or absurd value reach the calendar maths. The in-memory store here is the same contract the Postgres store satisfies; the HTTP layer separately refuses what is not JSON. */

import assert from "node:assert/strict";
import { test } from "node:test";

import { MemoryStore } from "../../src/memoryStore.ts";
import { PREFERENCE_ROUTES } from "../../src/prefTools.ts";
import { openWorld } from "../../src/world.ts";
import { PyRandom } from "../../src/pyrandom.ts";

const NOW = Date.parse("2026-10-22T16:00:00Z");
const SERVICES = ["massage-swedish-massage-60", "massage-deep-tissue-massage-90", "lashes-classic-full-set-2-week-refill", "not-a-service", "", "__proto__", "constructor"];
const WEIRD: unknown[] = [
  null, undefined, true, false, 0, -1, 1e308, -1e308, NaN, Infinity, -Infinity, 2 ** 53, -(2 ** 53), 1e-9, "", " ", "   ", "\u0000", "‮", "x".repeat(10_000), "💥", "😀".repeat(500), "<script>alert(1)</script>",
  "'; drop table appointments; --", "../../etc/passwd", "__proto__", "constructor", "toString", [], [1, 2, 3], [[]], {}, { a: 1 }, { toString: 1 }, { __proto__: { x: 1 } }, "2026-02-30", "2026-13-01", "9999-12-31",
  "0000-01-01", "24:00", "23:59", "00:00", "12:60", "7pm", "noon", "tomorrow", "+1 (212) 555-0142", "212555", "0".repeat(40), "1e3", "0x10", "٣٤٥", "Ａ１", "A".repeat(300), "Willa  Hartwell", "willa hartwell", "Hartwell, Willa",
];
const KEYS = ["service_id", "date", "time", "staff", "service_words", "client_name", "name", "phone", "reference", "query", "service", "service_category", "confirmed", "confirmation_token", "name_confirmed", "reason",
  "starting_at_or_after", "starting_after", "starting_at_or_before", "starting_before", "limit", "count", "__proto__", "constructor"];
const routes = Object.keys(PREFERENCE_ROUTES).map((r) => r.replace("/tools/", ""));

test("1,500 hostile calls over all eleven tools: every one answers, none throws", async () => {
  const world = await openWorld({ clock: () => NOW, store: new MemoryStore({ clock: () => Math.floor(NOW / 1000) }), seed: true });
  const rng = new PyRandom(20261022n);
  const pick = <T>(items: readonly T[]): T => items[rng.randbelow(items.length)];
  const thrown = new Map<string, number>();
  const statuses = new Map<string, number>();
  let answered = 0;
  for (let i = 0; i < 1500; i++) {
    const route = pick(routes);
    const args: Record<string, unknown> = {};
    for (let k = 0, n = rng.randbelow(8); k < n; k++) args[pick(KEYS)] = rng.randbelow(4) === 0 ? pick(SERVICES) : pick(WEIRD);
    if (rng.randbelow(3) === 0) args.service_id = pick(SERVICES);
    if (rng.randbelow(3) === 0) { args.date = "2026-10-23"; args.time = pick(["10:00", "16:30", "25:00", "09:07"]); }
    const started = Date.now();
    try {
      const reply = await world.call(route, `hostile-${i % 7}`, args);
      answered += 1;
      assert.equal(typeof reply, "object");
      assert.equal(typeof reply.status, "string", `${route} ${JSON.stringify(args).slice(0, 120)}`);
      assert.equal(typeof reply.ok, "boolean");
      assert.ok(typeof reply.speak === "string", `${route}: words`);
      statuses.set(`${route}/${reply.status}`, (statuses.get(`${route}/${reply.status}`) ?? 0) + 1);
    } catch (error) {
      const key = `${route}: ${(error as Error).constructor.name}: ${(error as Error).message.slice(0, 80)}`;
      thrown.set(key, (thrown.get(key) ?? 0) + 1);
    }
    assert.ok(Date.now() - started < 2000, `${route} was slow on ${JSON.stringify(args).slice(0, 100)}`);
  }
  assert.deepEqual([...thrown.entries()], [], "no tool threw");
  assert.equal(answered, 1500);
  assert.ok(statuses.size >= 15, `a spread of answers, not one refusal: ${statuses.size} kinds`);
});

test("the calendar maths never sees a non-finite or absurd time or date", async () => {
  const world = await openWorld({ clock: () => NOW, store: new MemoryStore({ clock: () => Math.floor(NOW / 1000) }), seed: false });
  for (const time of [NaN, Infinity, -1, 1e308, "99:99", "-5:00", "1e3", {}, [], "10:00:00:00"]) {
    for (const date of ["2026-10-23", "0000-00-00", "9999-99-99", 20261023, null, "2026-10-23T10:00", {}]) {
      const reply = await world.call("check_slot", "maths", { service_id: "massage-swedish-massage-60", date, time, staff: "Sienna", service_words: "swedish massage" });
      assert.ok(typeof reply.status === "string" && typeof reply.speak === "string" && reply.ok !== undefined, `${String(date)} ${String(time)}`);
      assert.notEqual(reply.status, "available", `an invalid date or time is never 'available': ${String(date)} ${String(time)}`);
    }
  }
});
