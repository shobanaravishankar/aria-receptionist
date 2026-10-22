// FALLBACK ONLY: apply netlify/database/migrations to a Postgres you point it at, if the host's own deploy does not apply them. Netlify's documented route is that migrations in
// netlify/database/migrations are applied on deploys; use this only if a draft deploy shows they were not. It records what it applied in netlify.migrations (the table @netlify/database-dev
// uses), one transaction per migration, and does nothing for a migration already recorded.
//
//   NETLIFY_DB_URL=postgres://...  node scripts/apply_migrations.mjs
//
// It prints no connection string. It never drops or alters an existing table.
import { readdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import pg from "pg";

const url = process.env.NETLIFY_DB_URL;
if (!url) { console.error("set NETLIFY_DB_URL to the database's connection string"); process.exit(2); }
const dir = fileURLToPath(new URL("../netlify/database/migrations", import.meta.url));
const client = new pg.Client({ connectionString: url, ssl: /sslmode=disable|localhost|127\.0\.0\.1/.test(url) ? undefined : { rejectUnauthorized: true } });
await client.connect();
await client.query("create schema if not exists netlify; create table if not exists netlify.migrations (name text primary key, applied_at timestamptz not null default now())");
const applied = new Set((await client.query("select name from netlify.migrations")).rows.map((r) => r.name));
const names = (await readdir(dir, { withFileTypes: true })).filter((e) => e.isDirectory() && /^\d+_.+$/.test(e.name)).map((e) => e.name).sort();
for (const name of names) {
  if (applied.has(name)) { console.log(`already applied: ${name}`); continue; }
  const sql = await readFile(`${dir}/${name}/migration.sql`, "utf8");
  await client.query("begin");
  try {
    await client.query(sql);
    await client.query("insert into netlify.migrations (name) values ($1)", [name]);
    await client.query("commit");
    console.log(`applied: ${name}`);
  } catch (error) {
    await client.query("rollback");
    console.error(`FAILED ${name}: ${error.message}`);
    process.exitCode = 1;
    break;
  }
}
await client.end();
