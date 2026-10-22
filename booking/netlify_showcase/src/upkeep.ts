/* The daily upkeep of the fictional fixtures (settle past visits, top up upcoming ones), claimed ONCE per spa day across every instance of the Function.
 *
 * The claim is a LEASE, and the day is only marked done AFTER the work succeeded:
 *   - one instance takes the lease (an expiring row in `meta`) and does the work; every other caller waits for it and never proceeds with a half-made day;
 *   - if the work fails, the lease is released at once and the next caller tries again (the work is idempotent: a seed that exists is never changed or duplicated);
 *   - if the instance dies, the lease simply expires and the next caller takes over;
 *   - once `upkeep:<day>` exists nothing is claimed or waited for again that day.
 * Lease times come from the database's clock, so instances with different clocks agree. Nothing here polls while idle: it runs inside a request that needs the data. */

import { randomUUID } from "node:crypto";

import type { Queryable } from "./pgStore.ts";

export const LEASE_MS = 90_000; // longer than the work takes; a dead holder's lease is taken over after this
export const WAIT_MS = 25_000; // how long a caller waits for another instance's upkeep before giving up (the request then fails, and a later one retries)
const POLL_MS = 250;

const DB_NOW_MS = "(extract(epoch from clock_timestamp()) * 1000)::bigint";

export async function ensureUpkeep(db: Queryable, day: string, work: () => Promise<unknown>, opts: { leaseMs?: number; waitMs?: number; pollMs?: number } = {}): Promise<void> {
  const leaseMs = opts.leaseMs ?? LEASE_MS;
  const pollMs = opts.pollMs ?? POLL_MS;
  const deadline = Date.now() + (opts.waitMs ?? WAIT_MS);
  const doneKey = `upkeep:${day}`;
  const leaseKey = `upkeep-lease:${day}`;
  const holder = randomUUID();
  for (;;) {
    if ((await db.query("select 1 from meta where key = $1::text", [doneKey])).rows.length) return;
    const claimed = (
      await db.query(
        `insert into meta (key, value) values ($1::text, $2::text || '|' || (${DB_NOW_MS} + $3::bigint)::text)
         on conflict (key) do update set value = excluded.value where split_part(meta.value, '|', 2)::bigint <= ${DB_NOW_MS}
         returning key`,
        [leaseKey, holder, leaseMs],
      )
    ).rows;
    if (claimed.length) {
      try {
        await work();
        await db.query("insert into meta (key, value) values ($1::text, 'done') on conflict (key) do nothing", [doneKey]);
      } finally {
        await db.query("delete from meta where key = $1::text and split_part(value, '|', 1) = $2::text", [leaseKey, holder]).catch(() => undefined); // only our own lease
      }
      return;
    }
    if (Date.now() >= deadline) throw new Error("the daily upkeep is still running elsewhere");
    await new Promise((resolve) => setTimeout(resolve, pollMs));
  }
}
