/* The tool layer on POSTGRES against the Python tools: the same recorded requests as test/logic/scenarios.test.ts, replayed on the Postgres store and the Postgres call-state store (PGlite),
 * every response and the final state of the database compared whole. Together with the in-memory replay this ties the Postgres implementation, not just the model, to the Python behaviour. */

import assert from "node:assert/strict";
import { after, before, test } from "node:test";

import { epochOf } from "../../src/appointments.ts";
import { PgCallStore, PgStore } from "../../src/pgStore.ts";
import { replay } from "../logic/replay.ts";
import { vectors } from "../logic/support.ts";
import { startPg } from "./pg.ts";

const { scenarios } = vectors<{ scenarios: any[] }>("scenarios");
let pg: Awaited<ReturnType<typeof startPg>>;
before(async () => {
  pg = await startPg();
});
after(async () => pg.close());

test("every request in the random walks gets the answer Python gave, on Postgres, and the database ends in the same state", { timeout: 1_700_000 }, async () => {
  const seen = new Set<string>();
  let steps = 0;
  for (const scenario of scenarios) {
    await pg.reset();
    steps += await replay(scenario, (clock, randomReference) => ({ store: new PgStore(pg.pool, { clock: () => epochOf(clock()), randomReference }), calls: new PgCallStore(pg.pool, clock) }), seen);
  }
  assert.ok(steps >= 4000 && seen.size >= 60, `${steps} steps, ${seen.size} distinct route/status pairs`);
});
