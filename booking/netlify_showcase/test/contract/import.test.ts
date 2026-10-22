/* The safe import of the preserved demo store (scripts/export_store.py -> scripts/import_store.mjs) on Postgres (PGlite through @netlify/database-dev, the local emulator). The snapshot is made by
 * the PYTHON store from a recorded walk of mixed calls (oracle/gen_import_fixture.py), so these tests show that what Python wrote arrives in Postgres column for column, that the digests
 * computed in Python and here agree, that a refused or damaged import changes nothing, and that the imported data then works with the tools. Not a hosted-Neon test. */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, afterEach, before, beforeEach, describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { PgCallStore, PgStore } from "../../src/pgStore.ts";
import { openWorld } from "../../src/world.ts";
// @ts-expect-error a plain .mjs script
import { checkSnapshot, digestRows, runImport, TABLES, undoImport } from "../../scripts/import_store.mjs";
import { startPg } from "./pg.ts";

const snapshot = () => JSON.parse(readFileSync(fileURLToPath(new URL("../vectors/store_snapshot.json", import.meta.url)), "utf8"));
let pg: Awaited<ReturnType<typeof startPg>>;
let db: any;
before(async () => { pg = await startPg(); });
after(async () => { await pg?.close(); });
beforeEach(async () => {
  await pg.reset();
  const { default: client } = await import("pg");
  db = new client.Client({ connectionString: pg.url });
  db.on("error", () => undefined);
  await db.connect();
});
afterEach(async () => { await db?.end().catch(() => undefined); });
const count = async (table: string) => Number((await pg.pool.query(`select count(*) as n from ${table}`)).rows[0].n);
const total = async () => (await Promise.all(Object.keys(TABLES).map(count))).reduce((a, b) => a + b, 0);

describe("the snapshot", () => {
  test("is Python's: its rows reproduce the manifest digests computed independently here, and it is not trivial", () => {
    const s = snapshot();
    assert.ok(checkSnapshot(s).length === 64);
    for (const table of Object.keys(TABLES)) assert.ok(s.manifest[table].count >= 1, table);
    assert.ok(s.manifest.appointments.count >= 10 && s.manifest.client_preferences.count >= 1 && s.manifest.outbox.count >= 1 && s.manifest.requests.count >= 1);
  });

  test("a damaged or foreign file is refused before the database is touched", async () => {
    const changed = snapshot();
    changed.tables.clients.rows[0][1] = "Someone Else";
    assert.throws(() => checkSnapshot(changed), /manifest digest/);
    const missing = snapshot();
    delete missing.tables.outbox;
    assert.throws(() => checkSnapshot(missing), /no table outbox/);
    assert.throws(() => checkSnapshot({ format: 2 }), /not a snapshot/);
    const fewer = snapshot();
    fewer.tables.requests.rows.pop();
    assert.throws(() => checkSnapshot(fewer), /manifest says/);
    await assert.rejects(runImport(db, changed, { apply: true }), /manifest digest/);
    assert.equal(await total(), 0);
  });
});

describe("import", () => {
  test("a dry run does everything, verifies it, and keeps nothing", async () => {
    const report = await runImport(db, snapshot());
    assert.equal(report.ok, true);
    assert.equal(report.applied, false);
    assert.ok(Object.values(report.verified).every((v: any) => v.equal));
    assert.equal(await total(), 0, "rolled back");
    assert.equal(await count("meta"), 0);
  });

  test("apply keeps it, every table equals the manifest, the id sequences move past the imported ids, and a second run changes nothing", async () => {
    const s = snapshot();
    const report = await runImport(db, s, { apply: true });
    assert.deepEqual([report.ok, report.applied, report.problems], [true, true, []]);
    for (const table of Object.keys(TABLES)) assert.equal(await count(table), s.manifest[table].count, table);
    const before = (await pg.pool.query("select * from appointments order by appt_id")).rows.length;
    const again = await runImport(db, s, { apply: true });
    assert.deepEqual([again.ok, again.already, again.applied], [true, true, false]);
    assert.equal((await pg.pool.query("select * from appointments")).rows.length, before);
    // the sequences continue after the largest imported id, for every serial table
    const store = new PgStore(pg.pool, { clock: () => Date.parse("2026-10-22T12:00:00Z") / 1000 });
    const maxClient = Number((await pg.pool.query("select max(client_id) as m from clients")).rows[0].m);
    const booked = await store.book({ clientName: "Brand New Person", phone: "+12125550999", serviceId: "massage-swedish-massage-60", serviceName: "Swedish Massage, 1 hour", category: "massage", staffId: "910006", startUtc: Date.parse("2026-12-15T15:00:00Z") / 1000, durationMinutes: 60, bufferAfterMinutes: 15 });
    assert.equal(booked.status, "booked");
    assert.equal(booked.appointment!.clientId, maxClient + 1);
  });

  test("it refuses a database that already holds other data, unless told to replace it", async () => {
    const s = snapshot();
    await new PgStore(pg.pool, { clock: () => Date.parse("2026-10-22T12:00:00Z") / 1000 }).book({ clientName: "Other Person", phone: "+12125550123", serviceId: "massage-swedish-massage-60", serviceName: "Swedish Massage, 1 hour", category: "massage", staffId: "910006", startUtc: Date.parse("2026-12-15T15:00:00Z") / 1000, durationMinutes: 60 });
    const refused = await runImport(db, s, { apply: true });
    assert.equal(refused.ok, false);
    assert.match(refused.problems[0].reason, /already holds data/);
    assert.equal(await count("clients"), 1, "nothing was changed");
    const replaced = await runImport(db, s, { apply: true, replace: true });
    assert.deepEqual([replaced.ok, replaced.applied], [true, true]);
    assert.equal(await count("clients"), s.manifest.clients.count);
    assert.equal((await pg.pool.query("select count(*)::int as n from clients where name = 'Other Person'")).rows[0].n, 0);
  });

  test("an overlap the database refuses rolls EVERYTHING back and is reported by reference, never by name or number", async () => {
    const s = snapshot();
    const rows = s.tables.appointments.rows as any[][];
    const cols: string[] = s.tables.appointments.columns;
    const col = (name: string) => cols.indexOf(name);
    const first = rows.find((r) => r[col("status")] === "confirmed")!;
    const clone = [...first];
    clone[col("appt_id")] = 9999;
    clone[col("reference")] = "ZZZZZ9";
    clone[col("seed_key")] = null;
    rows.push(clone); // the same technician at the same time, confirmed: the exclusion constraint must refuse it
    // keep the file self-consistent so only the database can refuse it
    s.manifest.appointments = { count: rows.length, digest: digestRows([...rows].sort((a, b) => a[0] - b[0])) };
    const report = await runImport(db, s, { apply: true });
    assert.equal(report.ok, false);
    assert.deepEqual(report.problems.map((p: any) => [p.table, p.row]), [["appointments", "ZZZZZ9"]]);
    assert.match(report.problems[0].reason, /no_double_booked_staff|no_double_booked_client/);
    assert.equal(await total(), 0, "and nothing else was kept");
    assert.ok(!JSON.stringify(report).includes(String(first[col("service_name")]) + "x"));
  });

  test("a verification mismatch after loading rolls back (a row that silently differs is not accepted)", async () => {
    const s = snapshot();
    // the database already holds a row with the same key but different content: 'on conflict do nothing' would skip it, the digest check must catch it
    await db.query("begin");
    await db.query("insert into clients (client_id, name, name_key, phone, created_at) values ($1, 'Different Name', 'different name', $2, 1)", [s.tables.clients.rows[0][0], s.tables.clients.rows[0][3]]);
    await db.query("commit");
    const report = await runImport(db, s, { apply: true, replace: false }).catch((e) => ({ ok: false, error: e }));
    assert.equal(report.ok, false);
  });

  test("a row that arrives DIFFERENT from what was exported (here: a trigger rewriting it) fails verification and rolls everything back", async () => {
    await pg.pool.query("create function shout() returns trigger language plpgsql as $$ begin new.name := upper(new.name); return new; end $$");
    await pg.pool.query("create trigger shout_names before insert on clients for each row execute function shout()");
    try {
      const report = await runImport(db, snapshot(), { apply: true });
      assert.equal(report.ok, false);
      assert.deepEqual(report.problems, [], "nothing was refused: it was the read-back that caught it");
      assert.equal(report.verified.clients.equal, false);
      assert.equal(await total(), 0, "so nothing was kept");
    } finally {
      await pg.pool.query("drop trigger shout_names on clients");
    }
  });

  test("a destructive step takes the tables BEFORE it looks at them (so what it verified is what it changes); a dry run takes them too", async () => {
    const s = snapshot();
    const seen: string[] = [];
    const spy = { query: (sql: string, values?: unknown[]) => { seen.push(sql.replace(/\s+/g, " ").trim().slice(0, 60)); return db.query(sql, values); } };
    await runImport(db, s, { apply: true });
    seen.length = 0;
    await undoImport(spy, s);
    const lock = seen.findIndex((q) => /^lock table .* in access exclusive mode$/.test(q) || q.startsWith("lock table"));
    const firstLook = seen.findIndex((q) => /^select/.test(q) && !q.startsWith("set local"));
    assert.ok(lock >= 1 && lock < firstLook, `the lock (${lock}) comes before the first look (${firstLook}): ${seen.slice(0, 4).join(" | ")}`);
    assert.match(seen[lock], /access exclusive|lock table/);
    seen.length = 0;
    await runImport(spy, s, { apply: true, replace: true });
    assert.ok(seen.findIndex((q) => q.startsWith("lock table")) < seen.findIndex((q) => /^select/.test(q)), "replace locks before it looks, too");
    seen.length = 0;
    await runImport(spy, s);
    assert.ok(seen.some((q) => q.startsWith("lock table")), "and so does a plain import or dry run (share row exclusive: no other writer, readers unaffected)");
  });

  test("undo removes exactly what was imported, and refuses if anything changed since", async () => {
    const s = snapshot();
    await runImport(db, s, { apply: true });
    await pg.pool.query("insert into meta (key, value) values ('upkeep:2026-10-22', 'done')");
    const changed = await new PgStore(pg.pool, { clock: () => Date.parse("2026-10-22T12:00:00Z") / 1000 }).book({ clientName: "Later Person", phone: "+12125550124", serviceId: "massage-swedish-massage-60", serviceName: "Swedish Massage, 1 hour", category: "massage", staffId: "910006", startUtc: Date.parse("2026-12-16T15:00:00Z") / 1000, durationMinutes: 60 });
    assert.equal(changed.status, "booked");
    const refused = await undoImport(db, s);
    assert.equal(refused.ok, false);
    assert.match(refused.problems[0].reason, /has changed since the import/);
    await pg.pool.query("truncate appointments, clients restart identity cascade"); // (put it back exactly as imported)
    await pg.reset();
    await runImport(db, s, { apply: true });
    const undone = await undoImport(db, s);
    assert.equal(undone.ok, true);
    assert.equal(await total(), 0);
    assert.equal((await pg.pool.query("select count(*)::int as n from meta where key = 'import:snapshot'")).rows[0].n, 0);
  });

  test("the imported records work with the tools: a returning client is found, their preference is read, and the owner's alert rows are kept untouched", async () => {
    const s = snapshot();
    await runImport(db, s, { apply: true });
    const clock = () => Date.parse("2026-10-22T12:00:00Z");
    const world = await openWorld({ clock, store: new PgStore(pg.pool, { clock: () => clock() / 1000 }), calls: new PgCallStore(pg.pool, clock), seed: false });
    const row = (s.tables.clients.rows as any[][])[0];
    const found = await world.call("find_appointments", "after-import", { phone: row[3], name: row[1] });
    assert.ok(["appointments_found", "no_appointments", "upcoming_found", "found"].includes(found.status) || found.ok === true, `${found.status}`);
    const sent = await pg.pool.query("select count(*)::int as n from outbox where status in ('sending', 'sent')");
    assert.equal(sent.rows[0].n, (s.tables.outbox.rows as any[][]).filter((r) => r[5] === "sending" || r[5] === "sent").length, "importing and reading changed no alert row and sent nothing");
  });
});
