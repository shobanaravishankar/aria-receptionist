/* The one Netlify Function of the Serenity showcase: the eleven Retell custom functions, the demo calendar's read-only answers and the staff calendar page, all on the website's own address
 * (so there is no cross-origin setup). It is a thin adapter: everything it answers is src/http.ts, tested without Netlify. Settings come from environment variables set on the site:
 *
 *   ARIA_RETELL_API_KEY       the Retell key that owns the agent (verifies the signature on every tool call)           [secret]
 *   ARIA_RETELL_TOOL_TOKEN    the bearer token the agent's tools send (and /readyz needs)                              [secret]
 *   ARIA_CONTACT              optional: how the agent says to reach the salon, e.g. "contact Serenity Spa at ..."
 *   ARIA_SITE_ORIGINS         optional: other https origins allowed to read the calendar or frame the staff page (space separated)
 *
 * A request is authenticated before any database is opened; the database is opened lazily, and the daily upkeep (settle past fictional visits, top up fictional upcoming ones) runs at most
 * once per spa day, on the first request that needs data. Nothing here polls, keeps anything warm or sends any message. */

import { getDatabase } from "@netlify/database";
import type { Config } from "@netlify/functions";

import { epochOf } from "../../src/appointments.ts";
import { PgCallStore, PgStore } from "../../src/pgStore.ts";
import { handle } from "../../src/http.ts";
import { dateOf, isoDate } from "../../src/time.ts";
import { openWorld } from "../../src/world.ts";
import type { World } from "../../src/world.ts";

let worldPromise: Promise<World> | null = null;
let pool: any = null;
let upkeepFor = "";

function env(name: string): string | null {
  const value = process.env[name];
  return value && value.trim() ? value.trim() : null;
}

/** One pool per warm instance (getDatabase makes a new one each call). An idle connection that the server drops must not take the function down. */
function database(): any {
  if (pool === null) {
    pool = getDatabase().pool;
    pool.on?.("error", (error: Error) => console.log(`database connection error: ${error.message.slice(0, 80)}`));
  }
  return pool;
}

async function world(): Promise<World> {
  worldPromise ??= (async () => {
    const db = database();
    const clock = () => Date.now();
    return openWorld({
      clock,
      store: new PgStore(db, { clock: () => epochOf(clock()) }),
      calls: new PgCallStore(db, clock),
      contact: env("ARIA_CONTACT"),
      seed: false, // upkeep is guarded below, once per spa day
    });
  })();
  const w = await worldPromise;
  const today = isoDate(dateOf(Date.now()));
  if (upkeepFor !== today) {
    const claimed = await database().query("insert into meta (key, value) values ($1::text, 'done') on conflict (key) do nothing returning key", [`upkeep:${today}`]);
    if (claimed.rows.length) await w.maintain();
    upkeepFor = today;
  }
  return w;
}

export default async (request: Request): Promise<Response> => {
  const url = new URL(request.url);
  const headers: Record<string, string> = {};
  request.headers.forEach((value, key) => { headers[key.toLowerCase()] = value; });
  const body = request.method === "GET" || request.method === "HEAD" ? new Uint8Array() : new Uint8Array(await request.arrayBuffer());
  const reply = await handle(
    { method: request.method, path: url.pathname, query: url.searchParams, headers, body },
    {
      world, apiKey: env("ARIA_RETELL_API_KEY"), bearer: env("ARIA_RETELL_TOOL_TOKEN"), now: () => Date.now(),
      siteOrigins: (env("ARIA_SITE_ORIGINS") ?? "").split(/\s+/).filter(Boolean), log: (message) => console.log(message),
    },
  );
  return new Response(reply.status === 204 || request.method === "HEAD" ? null : reply.body, { status: reply.status, headers: reply.headers });
};

export const config: Config = { path: ["/tools/*", "/demo/*", "/pro/*", "/healthz", "/readyz"] };
