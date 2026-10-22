/* A local Postgres for the tests: PGlite (PostgreSQL compiled to WebAssembly) behind a wire server, through @netlify/database-dev, with the project's own migrations applied.
 * It is an emulator of a single session at a time: it can show the SQL is right and that overlapping writes are refused; it cannot show hosted cold start, connector behaviour or true concurrency. */

import { fileURLToPath } from "node:url";

export const TABLES = "clients, appointments, appointment_events, requests, outbox, client_preferences, preference_requests, preference_events, call_state, pending_confirmations, meta";

export async function startPg() {
  const { NetlifyDB } = await import("@netlify/database-dev");
  const pg = (await import("pg")).default;
  const db = new NetlifyDB({ logger: () => undefined });
  const url = await db.start();
  await db.applyMigrations(fileURLToPath(new URL("../../netlify/database/migrations", import.meta.url)));
  const pool = new pg.Pool({ connectionString: url, max: 6 });
  return {
    pool,
    url,
    reset: async () => void (await pool.query(`truncate ${TABLES} restart identity cascade`)),
    close: async () => {
      await pool.end();
      await db.stop();
    },
  };
}
