import { epochOf } from "../src/appointments.ts";
import { MemoryStore } from "../src/memoryStore.ts";
import { Refused } from "../src/appointments.ts";
import { openWorld } from "../src/world.ts";

const now = 1797954000000;
const world = await openWorld({ clock: () => now, store: new MemoryStore({ clock: () => epochOf(now), randomReference: (() => { let n = 0; return () => n++ % 31; })() }), seed: false });
const service = world.dataset.registry.get("waxing-full-leg-40")!;
for (const staff of ["910004", "910007", "910008", "910009"]) {
  try {
    const start = Date.UTC(2026, 11, 22, 20, 0) / 1000; // 15:00 New York (EST, UTC-5)
    await world.judge.require({ staffId: staff, startUtc: start, durationMinutes: service.durationMinutes, bufferBeforeMinutes: 0, bufferAfterMinutes: service.bufferAfterMinutes, serviceId: service.serviceId, category: "waxing", ignoreReference: null }, async () => []);
    console.log(staff, "ok");
  } catch (e) {
    console.log(staff, e instanceof Refused ? [e.status, e.reason, JSON.stringify(e.detail.reasons ?? e.detail.unknown ?? "")] : e);
  }
}
const report = await (await import("../src/seed.ts")).seedDemo(world.store, world.judge, () => now);
console.log(JSON.stringify(report));
import { STATUSES } from "../src/appointments.ts";
for (const a of await world.store.between(0, 4_000_000_000, { statuses: STATUSES })) console.log("TS", a.reference, a.status, a.staffId, new Date(a.startUtc * 1000).toISOString(), a.serviceId, a.seedKey);
