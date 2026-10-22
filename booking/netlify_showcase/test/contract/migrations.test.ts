/* The fallback migration helper (scripts/apply_migrations.mjs). A hosted owner role could not `create schema netlify` (permission denied), so the helper must need only the right to create tables
 * in the database's own schema, never write to the platform's `netlify` schema, never run a migration twice, and roll back a failed one. Local Postgres emulation (PGlite). */

import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { after, afterEach, beforeEach, describe, test } from "node:test";

// @ts-expect-error a plain .mjs script
import { applyMigrations, migrationNames } from "../../scripts/apply_migrations.mjs";

let emulator: any;
let db: any;
const queries: string[] = [];

beforeEach(async () => {
  const { NetlifyDB } = await import("@netlify/database-dev");
  const { default: pg } = await import("pg");
  emulator = new NetlifyDB({ logger: () => undefined });
  const url = await emulator.start(); // NO migrations applied: a database as the platform hands it over when its build applied none
  const client = new pg.Client({ connectionString: url });
  client.on("error", () => undefined);
  await client.connect();
  queries.length = 0;
  db = { query: (sql: string, values?: unknown[]) => { queries.push(sql.replace(/\s+/g, " ").trim()); return client.query(sql, values); }, end: () => client.end() };
});
afterEach(async () => {
  await db?.end().catch(() => undefined);
  await emulator?.stop();
});
after(() => undefined);

const tables = async () => (await db.query("select table_name from information_schema.tables where table_schema = 'public' order by 1")).rows.map((r: any) => r.table_name);

describe("apply_migrations", () => {
  test("applies every migration in order on an empty database, records them in its own table, and a second run does nothing", async () => {
    const names = await migrationNames();
    assert.equal(names.length >= 3, true);
    const first = await applyMigrations(db);
    assert.deepEqual([first.applied, first.failed], [names, null]);
    const present = await tables();
    for (const t of ["clients", "appointments", "call_state", "client_preferences", "showcase_migrations"]) assert.ok(present.includes(t), t);
    assert.equal((await db.query("select count(*)::int as n from information_schema.columns where table_name = 'call_state' and column_name = 'rev'")).rows[0].n, 1, "0002 ran");
    assert.equal((await db.query("select count(*)::int as n from information_schema.columns where table_name = 'client_preferences' and column_name = 'generation'")).rows[0].n, 1, "0003 ran");
    const again = await applyMigrations(db);
    assert.deepEqual([again.applied, again.skipped.length, again.failed], [[], names.length, null]);
  });

  test("it never creates a schema and never writes to the platform's `netlify` schema (a hosted owner role may not)", async () => {
    await applyMigrations(db);
    const writes = queries.filter((q) => /^(create schema|alter schema|drop schema)/i.test(q) || /\b(insert into|update|delete from|create table|alter table)\s+(if not exists\s+)?netlify\./i.test(q));
    assert.deepEqual(writes, []);
    // and it still works when the role is refused exactly what the platform refused
    const refusing = { query: (sql: string, values?: unknown[]) => { if (/create schema/i.test(sql)) throw Object.assign(new Error("permission denied for database netlifydb"), { code: "42501" }); return db.query(sql, values); } };
    const result = await applyMigrations(refusing, { check: true });
    assert.equal(result.failed, null);
  });

  test("a migration the platform's own bookkeeping already names is not run again", async () => {
    const names = await migrationNames();
    // (the platform's own bookkeeping table, netlify.migrations(name, applied_at), is already there in the emulator, as it is on the host)
    await db.query(`insert into netlify.migrations (name) values ('${names[0]}')`);
    await db.query(await readFile(new URL(`../../netlify/database/migrations/${names[0]}/migration.sql`, import.meta.url), "utf8")); // the platform applied the first one itself
    const result = await applyMigrations(db);
    assert.deepEqual(result.applied, names.slice(1));
    assert.ok(result.skipped.some((s: string) => s.startsWith(names[0]) && s.includes("platform")), result.skipped.join("; "));
    assert.equal(result.failed, null);
    assert.equal((await db.query("select count(*)::int as n from netlify.migrations")).rows[0].n, 1, "the platform's table was only read");
  });

  test("on request it also records the names in the platform's table (insert only), and says so plainly when the role may not", async () => {
    const names = await migrationNames();
    const done = await applyMigrations(db, { mirror: true });
    assert.deepEqual([done.mirrored, done.mirrorFailed, done.failed], [names, [], null]);
    assert.deepEqual((await db.query("select name from netlify.migrations order by 1")).rows.map((r: any) => r.name), names);
    await db.query("drop table public.showcase_migrations");
    await db.query("truncate netlify.migrations");
    const refusing = { query: (sql: string, values?: unknown[]) => { if (/insert into netlify\.migrations/i.test(sql)) throw Object.assign(new Error("permission denied for table migrations"), { code: "42501" }); return db.query(sql, values); } };
    await db.query("drop table clients, appointments, appointment_events, requests, outbox, client_preferences, preference_requests, preference_events, call_state, pending_confirmations, meta cascade");
    await db.query("drop sequence preference_generation");
    const denied = await applyMigrations(refusing, { mirror: true });
    assert.deepEqual([denied.applied, denied.mirrored, denied.mirrorFailed, denied.failed], [names, [], names, null], "the migrations applied; only the mirror was refused, and it is reported");
  });

  test("--check says what would be applied and changes nothing", async () => {
    const names = await migrationNames();
    const result = await applyMigrations(db, { check: true });
    assert.deepEqual(result.applied, names.map((n: string) => `${n} (would apply)`));
    assert.deepEqual(await tables(), []);
  });

  test("a failing migration is rolled back and named, the ones before it stay, and a fixed one continues from there", async () => {
    const dir = await mkdtemp(`${tmpdir()}/migrations-`);
    try {
      await mkdir(`${dir}/0001_good`);
      await writeFile(`${dir}/0001_good/migration.sql`, "create table thing (id int primary key);\n");
      await mkdir(`${dir}/0002_bad`);
      await writeFile(`${dir}/0002_bad/migration.sql`, "create table half_done (id int primary key);\nthis is not sql;\n");
      const failed = await applyMigrations(db, { from: dir });
      assert.deepEqual(failed.applied, ["0001_good"]);
      assert.equal(failed.failed?.name, "0002_bad");
      assert.ok(!(await tables()).includes("half_done"), "the failed migration left nothing behind");
      assert.deepEqual((await db.query("select name from public.showcase_migrations order by 1")).rows.map((r: any) => r.name), ["0001_good"]);
      await writeFile(`${dir}/0002_bad/migration.sql`, "create table half_done (id int primary key);\n");
      const fixed = await applyMigrations(db, { from: dir });
      assert.deepEqual([fixed.applied, fixed.failed], [["0002_bad"], null]);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
});
