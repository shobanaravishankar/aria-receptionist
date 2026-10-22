// Load a snapshot made by scripts/export_store.py (the Python demo's SQLite store: clients, appointments, events, idempotency keys, alert rows, preferred technicians) into the showcase's Postgres,
// idempotently, and PROVE it arrived intact.
//
//   NETLIFY_DB_URL=postgres://... node scripts/import_store.mjs <snapshot.json>                       dry run: does everything in a transaction, verifies, then ROLLS BACK
//   NETLIFY_DB_URL=postgres://... node scripts/import_store.mjs <snapshot.json> --apply               the same, and COMMITS if (and only if) every check passed
//   NETLIFY_DB_URL=postgres://... node scripts/import_store.mjs <snapshot.json> --apply --replace-existing   first empties the showcase's tables (it is a fictional demo database)
//   NETLIFY_DB_URL=postgres://... node scripts/import_store.mjs <snapshot.json> --undo               removes the imported data again, only if nothing has been added since
//
// What it guarantees:
//   - the snapshot file is checked against its own digests before the database is touched;
//   - all of it happens in ONE transaction: any refused row (an overlap the database's exclusion constraints refuse, a bad value) or any count/digest mismatch rolls EVERYTHING back, and the
//     problem rows are reported by table and reference/key only, never by name or number;
//   - the original ids are kept (so references, events and keys still line up) and the id sequences are moved past them;
//   - after loading, each table is read back from Postgres and its row count and digest are compared with the snapshot's manifest (the digest of the canonical rows, computed the same way in
//     Python and here): "equal" means every column of every row is what was exported;
//   - running it again on the same snapshot changes nothing ("already imported", re-verified); it refuses a database that already holds other data unless --replace-existing is given;
//   - it prints table names, counts and digests only. It reads nothing but the snapshot and the database, sends nothing anywhere and starts no call.
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

/** target table -> columns in canonical order, the order the digest is taken in, which columns are integers, which are JSON, which are generated serials. */
export const TABLES = {
  clients: { cols: ["client_id", "name", "name_key", "phone", "created_at"], order: "client_id", ints: ["client_id", "created_at"], json: [], serial: "client_id" },
  appointments: {
    cols: ["appt_id", "reference", "client_id", "service_id", "service_name", "category", "staff_id", "start_utc", "duration_minutes", "buffer_before_minutes", "buffer_after_minutes", "status", "version", "origin", "seed_key", "created_at", "updated_at", "cancelled_at", "cancel_reason"],
    order: "appt_id", ints: ["appt_id", "client_id", "start_utc", "duration_minutes", "buffer_before_minutes", "buffer_after_minutes", "version", "created_at", "updated_at", "cancelled_at"], json: [], serial: "appt_id", key: "reference",
  },
  appointment_events: { cols: ["event_id", "reference", "kind", "detail", "at"], order: "event_id", ints: ["event_id", "at"], json: ["detail"], serial: "event_id" },
  requests: { cols: ["request_key", "kind", "reference", "created_at"], order: "request_key", ints: ["created_at"], json: [], serial: null },
  outbox: { cols: ["outbox_id", "dedupe_key", "kind", "reference", "body", "status", "attempts", "last_error", "created_at", "claimed_at", "sent_at"], order: "outbox_id", ints: ["outbox_id", "attempts", "created_at", "claimed_at", "sent_at"], json: [], serial: "outbox_id" },
  client_preferences: { cols: ["client_id", "family_id", "staff_id", "version", "created_at", "updated_at"], order: "client_id, family_id", ints: ["client_id", "version", "created_at", "updated_at"], json: [], serial: null },
  preference_requests: { cols: ["request_key", "kind", "client_id", "family_id", "created_at"], order: "request_key", ints: ["client_id", "created_at"], json: [], serial: null },
  preference_events: { cols: ["event_id", "client_id", "family_id", "kind", "detail", "at"], order: "event_id", ints: ["event_id", "client_id", "at"], json: ["detail"], serial: "event_id" },
};
const ORDER = Object.keys(TABLES); // parents before children
const EMPTYING = [...ORDER].reverse();
const MARKER = "import:snapshot";

/** JSON with sorted keys and no spaces: the text Python's json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False) makes. */
export function canonicalJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${canonicalJson(value[k])}`).join(",")}}`;
}
export const digestRows = (rows) => createHash("sha256").update(JSON.stringify(rows), "utf8").digest("hex");
const canonicalRow = (table, row) => row.map((value, i) => (TABLES[table].json.includes(TABLES[table].cols[i]) && value !== null ? canonicalJson(typeof value === "string" ? JSON.parse(value) : value) : value));

/** Parse a snapshot and check it against its own manifest. Throws a plain Error with a reason; never prints row content. */
export function checkSnapshot(snapshot) {
  if (snapshot?.format !== 1 || snapshot.schema_version !== "1" || typeof snapshot.tables !== "object" || typeof snapshot.manifest !== "object") throw new Error("not a snapshot made by export_store.py (format 1, store schema 1)");
  const overall = [];
  for (const table of ORDER) {
    const entry = snapshot.tables[table];
    const wanted = snapshot.manifest[table];
    if (!entry || !wanted) throw new Error(`the snapshot has no table ${table}`);
    if (JSON.stringify(entry.columns) !== JSON.stringify(TABLES[table].cols)) throw new Error(`${table}: the snapshot's columns are not the ones this importer knows`);
    if (entry.rows.length !== wanted.count) throw new Error(`${table}: ${entry.rows.length} rows, but the manifest says ${wanted.count}`);
    if (digestRows(entry.rows.map((row) => canonicalRow(table, row))) !== wanted.digest) throw new Error(`${table}: the rows do not match the manifest digest (the file was changed or damaged)`);
    overall.push([table, wanted.count, wanted.digest]);
  }
  return createHash("sha256").update(JSON.stringify(overall)).digest("hex");
}

const placeholders = (rows, width, cast) => rows.map((_, r) => `(${Array.from({ length: width }, (__, c) => `$${r * width + c + 1}${cast[c]}`).join(",")})`).join(",");
function valuesFor(table, rows) {
  const { cols, json } = TABLES[table];
  return rows.flatMap((row) => row.map((value, i) => (json.includes(cols[i]) && value !== null ? JSON.stringify(value) : value)));
}
const castsFor = (table) => TABLES[table].cols.map((c) => (TABLES[table].json.includes(c) ? "::jsonb" : ""));

async function insertRows(db, table, rows, report) {
  const { cols } = TABLES[table];
  const casts = castsFor(table);
  const labelOf = (row) => String(row[cols.indexOf(TABLES[table].key ?? TABLES[table].order.split(",")[0])]); // a reference, an id or an opaque key: never a name or a number
  const insert = async (slice) => {
    await db.query("savepoint row_insert");
    try {
      const done = await db.query(`insert into ${table} (${cols.join(", ")}) values ${placeholders(slice, cols.length, casts)}`, valuesFor(table, slice)); // no 'on conflict': a clash is a finding, not something to skip
      report.inserted[table] = (report.inserted[table] ?? 0) + done.rowCount;
      await db.query("release savepoint row_insert");
      return null;
    } catch (error) {
      await db.query("rollback to savepoint row_insert");
      return error;
    }
  };
  for (let i = 0; i < rows.length; i += 100) {
    const slice = rows.slice(i, i + 100);
    if ((await insert(slice)) === null) continue;
    for (const row of slice) { // the batch was refused: find the row(s) one at a time, so the report names them
      const error = await insert([row]);
      if (error !== null && report.problems.length < 20) report.problems.push({ table, row: labelOf(row), reason: `${error.constraint ?? error.code ?? "refused"}` });
    }
  }
}

async function readBack(db, table) {
  const { cols, order, ints, json } = TABLES[table];
  const rows = (await db.query(`select ${cols.join(", ")} from ${table} order by ${order}`)).rows;
  return rows.map((r) => cols.map((c) => {
    const value = r[c];
    if (value === null || value === undefined) return null;
    if (ints.includes(c)) return Number(value);
    if (json.includes(c)) return canonicalJson(value);
    return value;
  }));
}

async function counts(db) {
  const out = {};
  for (const table of ORDER) out[table] = Number((await db.query(`select count(*) as n from ${table}`)).rows[0].n);
  return out;
}

/** Load, verify, and (only with apply) commit. Returns a report; `ok` is true only if nothing was refused and every table verified. */
export async function runImport(db, snapshot, { apply = false, replace = false } = {}) {
  const overall = checkSnapshot(snapshot);
  const report = { ok: false, applied: false, already: false, overall, inserted: {}, problems: [], verified: {}, notes: [] };
  await db.query("begin");
  try {
    const marker = (await db.query("select value from meta where key = $1::text", [MARKER])).rows[0];
    if (marker && marker.value.split("|")[0] === overall && !replace) {
      report.already = true;
    } else {
      const present = await counts(db);
      const occupied = Object.entries(present).filter(([, n]) => n > 0).map(([t]) => t);
      if (marker && !replace) throw Object.assign(new Error("a DIFFERENT snapshot was imported here before; use --replace-existing to replace it"), { plain: true });
      if (occupied.length && !replace) throw Object.assign(new Error(`the database already holds data (${occupied.join(", ")}); import into an empty one, or pass --replace-existing`), { plain: true });
      if (replace) {
        await db.query(`truncate ${EMPTYING.join(", ")}, call_state, pending_confirmations restart identity cascade`);
        await db.query("delete from meta where key = $1::text", [MARKER]);
        report.notes.push("emptied the showcase tables first (--replace-existing)");
      }
      for (const table of ORDER) await insertRows(db, table, snapshot.tables[table].rows, report);
      if (!report.problems.length) {
        for (const table of ORDER) {
          const serial = TABLES[table].serial;
          if (serial) await db.query(`select setval(pg_get_serial_sequence('${table}', '${serial}'), greatest((select coalesce(max(${serial}), 0) from ${table}), 1), (select count(*) > 0 from ${table}))`);
        }
      }
    }
    if (!report.problems.length) {
      let allEqual = true;
      for (const table of ORDER) {
        const rows = await readBack(db, table);
        const got = { count: rows.length, digest: digestRows(rows) };
        const want = snapshot.manifest[table];
        const equal = got.count === want.count && got.digest === want.digest;
        report.verified[table] = { count: got.count, digest: got.digest.slice(0, 16), equal };
        allEqual &&= equal;
      }
      report.ok = allEqual;
      if (allEqual && !report.already && apply) {
        await db.query("insert into meta (key, value) values ($1::text, $2::text) on conflict (key) do update set value = excluded.value", [MARKER, `${overall}|${new Date().toISOString()}`]);
      }
    }
    if (report.ok && apply) {
      await db.query("commit");
      report.applied = !report.already;
    } else {
      await db.query("rollback");
      if (report.ok && !apply) report.notes.push("dry run: verified, then rolled back; pass --apply to keep it");
    }
  } catch (error) {
    await db.query("rollback").catch(() => undefined);
    if (error.plain) report.problems.push({ table: "-", row: "-", reason: error.message });
    else throw error;
  }
  return report;
}

/** Remove the imported data again, but only if nothing has been added or changed since (the tables still hold exactly the snapshot). */
export async function undoImport(db, snapshot) {
  const overall = checkSnapshot(snapshot);
  await db.query("begin");
  try {
    const marker = (await db.query("select value from meta where key = $1::text", [MARKER])).rows[0];
    if (!marker || marker.value.split("|")[0] !== overall) throw Object.assign(new Error("this snapshot was not imported here, so there is nothing to undo"), { plain: true });
    for (const table of ORDER) {
      const rows = await readBack(db, table);
      const want = snapshot.manifest[table];
      if (rows.length !== want.count || digestRows(rows) !== want.digest) throw Object.assign(new Error(`${table} has changed since the import, so undoing would lose newer data; restore the database from a backup instead`), { plain: true });
    }
    await db.query(`truncate ${EMPTYING.join(", ")}, call_state, pending_confirmations restart identity cascade`);
    await db.query("delete from meta where key = $1::text", [MARKER]);
    await db.query("commit");
    return { ok: true, problems: [] };
  } catch (error) {
    await db.query("rollback").catch(() => undefined);
    if (error.plain) return { ok: false, problems: [{ table: "-", row: "-", reason: error.message }] };
    throw error;
  }
}

function print(report) {
  for (const [table, v] of Object.entries(report.verified)) console.log(`${v.equal ? "PASS" : "FAIL"}  ${table.padEnd(20)} rows=${String(v.count).padStart(5)} digest=${v.digest}`);
  for (const [table, n] of Object.entries(report.inserted)) if (!report.already) console.log(`      ${table.padEnd(20)} inserted ${n}`);
  for (const p of report.problems) console.log(`REFUSED  ${p.table}  ${p.row}  (${p.reason})`);
  for (const note of report.notes) console.log(`note: ${note}`);
  if (report.already) console.log("already imported: nothing changed; re-verified only");
  console.log(report.ok ? (report.applied ? "IMPORTED AND VERIFIED" : report.already ? "ALREADY IMPORTED, VERIFIED" : "DRY RUN OK") : "NOT IMPORTED (nothing was changed)");
}

async function main() {
  const [file, ...flags] = process.argv.slice(2);
  const url = process.env.NETLIFY_DB_URL;
  if (!file || !url) {
    console.error("usage: NETLIFY_DB_URL=postgres://... node scripts/import_store.mjs <snapshot.json> [--apply] [--replace-existing] [--undo]");
    process.exit(2);
  }
  const snapshot = JSON.parse(await readFile(file, "utf8"));
  const pg = (await import("pg")).default;
  const db = new pg.Client({ connectionString: url });
  await db.connect();
  try {
    if (flags.includes("--undo")) {
      const result = await undoImport(db, snapshot);
      for (const p of result.problems) console.log(`REFUSED  ${p.reason}`);
      console.log(result.ok ? "UNDONE: the imported data was removed" : "NOT UNDONE (nothing was changed)");
      process.exit(result.ok ? 0 : 1);
    }
    const report = await runImport(db, snapshot, { apply: flags.includes("--apply"), replace: flags.includes("--replace-existing") });
    print(report);
    process.exit(report.ok ? 0 : 1);
  } finally {
    await db.end();
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
