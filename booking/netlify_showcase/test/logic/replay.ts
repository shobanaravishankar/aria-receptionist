/* Replay a recorded scenario (see oracle/gen_scenarios.py) on the TypeScript tools: the same requests with the same clock, the same token and reference counters; every response and the final
 * state of the store (rows, owner alerts, preferences) are compared whole to what the Python tools produced. */

import assert from "node:assert/strict";

import { STATUSES } from "../../src/appointments.ts";
import type { CallStore } from "../../src/callState.ts";
import type { Store } from "../../src/store.ts";
import { openWorld } from "../../src/world.ts";

export interface Backend {
  store: Store;
  calls: CallStore;
}
const byText = (rows: unknown[]) => [...rows].sort((a, b) => (JSON.stringify(a) < JSON.stringify(b) ? -1 : JSON.stringify(a) > JSON.stringify(b) ? 1 : 0));

export async function replay(scenario: any, make: (clock: () => number, randomReference: () => number) => Backend, seen: Set<string>): Promise<number> {
  let now = scenario.start;
  let tokens = 0;
  let chars = 0;
  const backend = make(() => now, () => chars++ % 31);
  const world = await openWorld({ clock: () => now, store: backend.store, calls: backend.calls, newToken: () => `tok${String(++tokens).padStart(5, "0")}` });
  let steps = 0;
  for (const [index, step] of scenario.steps.entries()) {
    now = step.now;
    if (step.op === "maintain") {
      await world.maintain();
      continue;
    }
    let got: any;
    try {
      got = JSON.parse(JSON.stringify(await world.call(step.route, step.call, step.args)));
    } catch (error) {
      got = { exception: (error as Error).constructor.name };
    }
    assert.deepEqual(got, step.response, `scenario ${scenario.seed} step ${index}: ${step.route} ${JSON.stringify(step.args)}`);
    seen.add(`${step.route}/${got.status ?? got.exception}`);
    steps += 1;
  }
  const store = world.store;
  const rows = await store.between(0, 4_000_000_000, { statuses: STATUSES });
  const preferences: unknown[] = [];
  for (let id = 1; id < 40; id++) {
    const client = await store.client(id);
    if (client) for (const p of await store.preferences.forClient(id)) preferences.push([client.name, p.familyId, p.staffId, p.version]);
  }
  const got = {
    outbox: byText((await store.outboxRows()).map((r) => [r.dedupeKey, r.kind, r.body, r.status])),
    counts: await store.counts(),
    appointments: byText(rows.map((a) => [a.reference, a.status, a.version, a.staffId, a.startUtc, a.clientName, a.origin, a.serviceId, a.cancelReason])),
    preferences: byText(preferences),
  };
  const want = { ...scenario.final, outbox: byText(scenario.final.outbox), appointments: byText(scenario.final.appointments), preferences: byText(scenario.final.preferences) };
  assert.deepEqual(got, want, `scenario ${scenario.seed}: the final state of the store`);
  return steps;
}
