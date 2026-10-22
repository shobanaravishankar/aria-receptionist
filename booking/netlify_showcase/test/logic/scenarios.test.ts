/* The tool layer against the Python tools: 40 seeded random walks of mixed good, wrong, misspelled, off-grid, repeated, confirmed and stale calls over several callers and calls (with the
 * follow-up confirms, moves and cancels that real calls make, and the daily upkeep), each replayed on the TypeScript tools with the same clock and the same token and reference counters.
 * Every response is compared whole, and so is the final state of the store: rows, owner alerts, preferences. */

import assert from "node:assert/strict";
import { test } from "node:test";

import { epochOf } from "../../src/appointments.ts";
import { MemoryCallStore } from "../../src/callState.ts";
import { MemoryStore } from "../../src/memoryStore.ts";
import { replay } from "./replay.ts";
import { vectors } from "./support.ts";

const { scenarios } = vectors<{ scenarios: any[] }>("scenarios");

test("every request in the random walks gets the answer Python gave, and the store ends in the same state", { timeout: 600_000 }, async () => {
  const seen = new Set<string>();
  let steps = 0;
  for (const scenario of scenarios) {
    steps += await replay(scenario, (clock, randomReference) => ({ store: new MemoryStore({ clock: () => epochOf(clock()), randomReference }), calls: new MemoryCallStore() }), seen);
  }
  assert.ok(steps >= 4000 && seen.size >= 60, `${steps} steps, ${seen.size} distinct route/status pairs`);
  for (const needed of ["book_appointment/booked_verified", "book_appointment/already_booked", "cancel_appointment/cancelled", "cancel_appointment/already_cancelled", "reschedule_appointment/confirmation_required",
    "reschedule_appointment/rescheduled", "save_preference/preference_saved", "remove_preference/preference_removed", "find_appointments/found", "last_completed_visit/found", "check_slot/available",
    "lookup_service/service_group"]) {
    assert.ok(seen.has(needed), `the walks never produced ${needed}`);
  }
});
