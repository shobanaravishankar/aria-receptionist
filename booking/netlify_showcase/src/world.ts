/* Open the simulated appointment world: the store, the calendar that draws it, the judge that guards it, the seeded fictional clients and the tools. Port of aria_booking/appointments/
 * lifecycle.py + launch.py. The clock decides 'now' for everything here (the store's past/started checks, the judge's minimum notice, the seeds' dates), so one clock is one consistent world.
 * Nothing here talks to a calendar service, a browser or the network. */

import { epochOf } from "./appointments.ts";
import { MemoryCallStore } from "./callState.ts";
import type { CallStore } from "./callState.ts";
import { DemoDataset } from "./dataset.ts";
import { PlacementJudge } from "./judge.ts";
import { MemoryStore } from "./memoryStore.ts";
import { PreferenceTools, PREFERENCE_ROUTES } from "./prefTools.ts";
import { seedDemo } from "./seed.ts";
import type { SeedReport } from "./seed.ts";
import type { Store } from "./store.ts";
import { dateOf } from "./time.ts";
import type { Instant } from "./time.ts";

export interface World {
  store: Store;
  calls: CallStore;
  dataset: DemoDataset;
  judge: PlacementJudge;
  tools: PreferenceTools;
  clock: () => Instant;
  seeded: SeedReport | null;
  /** Daily upkeep for a server that stays up for days: fictional visits whose time has passed are settled, and the fictional upcoming ones are topped up. A client's own booking is never touched. */
  maintain(): Promise<SeedReport>;
  /** Run one tool by its route ("/tools/check_slot") or its name. Unknown routes are not tools. */
  call(route: string, callId: string, args: Record<string, unknown>): Promise<Record<string, any>>;
}

export interface WorldOptions {
  clock: () => Instant;
  store?: Store;
  calls?: CallStore;
  contact?: string | null;
  newToken?: () => string;
  randomReference?: () => number;
  seed?: boolean;
  diagnosticUnknownHours?: Iterable<string>;
  dataset?: DemoDataset; // share one (the Function keeps a database-free one for validating requests)
  minLeadMinutes?: number;
  gridMinutes?: number;
}

export async function openWorld(opts: WorldOptions): Promise<World> {
  const clock = opts.clock;
  const store = opts.store ?? new MemoryStore({ clock: () => epochOf(clock()), randomReference: opts.randomReference });
  const calls = opts.calls ?? new MemoryCallStore();
  if (opts.seed !== false) await store.settlePastSeeds(); // the fictional fixtures only: a caller's appointment is never marked completed for them (hosted: the guarded daily upkeep does this)
  // with no explicit demo date the calendar's 'today' follows the real date in the spa's time zone, however long the server stays up
  const dataset = opts.dataset ?? new DemoDataset({ today: () => dateOf(clock()), diagnosticUnknownHours: opts.diagnosticUnknownHours });
  const judge = new PlacementJudge(dataset, { clock, minLeadMinutes: opts.minLeadMinutes, gridMinutes: opts.gridMinutes });
  const tools = new PreferenceTools({ dataset, judge, store, calls, clock, contact: opts.contact, newToken: opts.newToken, gridMinutes: opts.gridMinutes, minLeadMinutes: opts.minLeadMinutes });
  const seeded = opts.seed === false ? null : await seedDemo(store, judge, clock);
  const world: World = {
    store, calls, dataset, judge, tools, clock, seeded,
    async maintain() {
      await store.settlePastSeeds();
      world.seeded = await seedDemo(store, judge, clock);
      return world.seeded;
    },
    async call(route, callId, args) {
      const name = PREFERENCE_ROUTES[route] ?? (Object.values(PREFERENCE_ROUTES).includes(route) ? route : undefined);
      if (name === undefined) throw new Error(`not a tool: ${route}`);
      return (tools as any)[name](callId, args);
    },
  };
  return world;
}
