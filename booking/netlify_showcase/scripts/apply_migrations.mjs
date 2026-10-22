// FALLBACK ONLY: apply netlify/database/migrations to a Postgres you point it at, if the host's own deploy did not apply them. Netlify's documented route is that migrations in
// netlify/database/migrations are applied by its own build; use this only if a draft deploy shows they were not.
//
//   NETLIFY_DB_URL=postgres://...  node scripts/apply_migrations.mjs            apply what is missing, in order
//   NETLIFY_DB_URL=postgres://...  node scripts/apply_migrations.mjs --check    only say what would be applied
//   NETLIFY_DB_URL=postgres://...  node scripts/apply_migrations.mjs --record-in-platform-table    ...and also add each applied name to netlify.migrations (INSERT only, best effort)
//
// It needs NO privilege beyond creating tables in the database's own (public) schema: it never creates a schema and never writes to the platform's protected `netlify` schema (a hosted
// owner role may not: a first version that tried got "permission denied for database"). It records what it applied in its own table, public.showcase_migrations, and also treats a migration
// as applied if the platform's own bookkeeping (netlify.migrations, READ only by default, if it exists) already names it, so it never runs one twice. A later Netlify build that applies
// migrations itself would not know these were applied and might try to run them again (and fail on "already exists"); --record-in-platform-table adds the names there with a plain INSERT, which
// is what the platform's own build does, and says plainly if the role may not. One transaction per migration; it never drops or
// alters an existing table of its own accord; it prints no connection string.
import { readdir, readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

const dir = fileURLToPath(new URL("../netlify/database/migrations", import.meta.url));
const TABLE = "public.showcase_migrations";

/** The migration folder names, in order. */
export async function migrationNames(from = dir) {
  return (await readdir(from, { withFileTypes: true })).filter((e) => e.isDirectory() && /^\d+_.+$/.test(e.name)).map((e) => e.name).sort();
}

/** Which migrations the platform's own bookkeeping already names (read only; nothing if it does not exist or cannot be read). */
async function platformApplied(db, names) {
  try {
    const exists = (await db.query("select to_regclass('netlify.migrations') as t")).rows[0].t;
    if (!exists) return new Set();
    const text = (await db.query("select row_to_json(m)::text as r from netlify.migrations m")).rows.map((r) => r.r).join("\n");
    return new Set(names.filter((n) => text.includes(`"${n}"`)));
  } catch {
    return new Set();
  }
}

/** Apply every migration that is not yet applied. Returns { applied, skipped, failed }. */
export async function applyMigrations(db, { check = false, from = dir, mirror = false } = {}) {
  const names = await migrationNames(from);
  const done = new Set();
  const out = { applied: [], skipped: [], failed: null, mirrored: [], mirrorFailed: [] };
  try {
    for (const row of (await db.query(`select name from ${TABLE}`)).rows) done.add(row.name);
  } catch {
    // our own table does not exist yet: nothing of ours has run
  }
  const byPlatform = await platformApplied(db, names);
  if (!check) await db.query(`create table if not exists ${TABLE} (name text primary key, applied_at timestamptz not null default now(), how text not null default 'fallback script')`);
  for (const name of names) {
    if (done.has(name)) { out.skipped.push(`${name} (applied by this script before)`); continue; }
    if (byPlatform.has(name)) {
      out.skipped.push(`${name} (the platform's own bookkeeping names it)`);
      continue;
    }
    if (check) { out.applied.push(`${name} (would apply)`); continue; }
    const sql = await readFile(`${from}/${name}/migration.sql`, "utf8");
    await db.query("begin");
    try {
      await db.query(sql);
      await db.query(`insert into ${TABLE} (name) values ($1)`, [name]);
      await db.query("commit");
      out.applied.push(name);
      if (mirror) {
        try {
          await db.query("insert into netlify.migrations (name) values ($1)", [name]);
          out.mirrored.push(name);
        } catch {
          out.mirrorFailed.push(name);
        }
      }
    } catch (error) {
      await db.query("rollback").catch(() => undefined);
      out.failed = { name, message: error.message, code: error.code };
      break;
    }
  }
  return out;
}

async function main() {
  const url = process.env.NETLIFY_DB_URL;
  if (!url) { console.error("set NETLIFY_DB_URL to the database's connection string"); process.exit(2); }
  const pg = (await import("pg")).default;
  const client = new pg.Client({ connectionString: url, ssl: /sslmode=disable|localhost|127\.0\.0\.1/.test(url) ? undefined : { rejectUnauthorized: true } });
  await client.connect();
  try {
    const result = await applyMigrations(client, { check: process.argv.includes("--check"), mirror: process.argv.includes("--record-in-platform-table") });
    for (const name of result.applied) console.log(`applied: ${name}`);
    for (const name of result.skipped) console.log(`skipped: ${name}`);
    for (const name of result.mirrored) console.log(`also recorded in the platform's table: ${name}`);
    if (result.mirrorFailed.length) console.log(`NOTE: could not record ${result.mirrorFailed.join(", ")} in the platform's table (this role may not): a later Netlify build that applies migrations itself might try to run them again`);
    if (result.applied.length && !result.mirrored.length && !result.mirrorFailed.length && !process.argv.includes("--check")) console.log("NOTE: the platform's own bookkeeping does not know these were applied; see --record-in-platform-table");
    if (result.failed) {
      console.error(`FAILED ${result.failed.name}: ${result.failed.message}${result.failed.code ? ` (${result.failed.code})` : ""}`);
      process.exitCode = 1;
    }
  } finally {
    await client.end();
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
