// Local spike (PGlite via @netlify/database-dev): can the engine enforce "no two confirmed bookings of one technician overlap" with an exclusion constraint on a range column, and does an UPDATE
// that overlaps the row's OWN old range work in one statement? This says nothing about hosted Netlify Database; it only shows the SQL is valid Postgres that this engine accepts.
import { NetlifyDB } from "@netlify/database-dev";
import pg from "pg";

const db = new NetlifyDB({ logger: () => {} });
const url = await db.start();
const client = new pg.Client({ connectionString: url });
await client.connect();
const run = async (sql, params) => {
  try {
    return { ok: true, rows: (await client.query(sql, params)).rows };
  } catch (error) {
    return { ok: false, code: error.code, message: error.message.slice(0, 160), constraint: error.constraint };
  }
};

console.log("version", (await run("select version() as v")).rows?.[0]?.v);
console.log("create", await run(`
  create table appt (
    id serial primary key, staff bigint not null, start_min bigint not null, end_min bigint not null, status text not null default 'confirmed',
    rng int8range generated always as (int8range(staff * 100000000 + start_min, staff * 100000000 + end_min)) stored,
    constraint no_overlap exclude using gist (rng with &&) where (status = 'confirmed')
  )`));
console.log("insert A", await run("insert into appt (staff, start_min, end_min) values (910001, 1000, 1075) returning id"));
console.log("insert overlapping B (expect 23P01)", await run("insert into appt (staff, start_min, end_min) values (910001, 1060, 1120)"));
console.log("insert touching C (allowed)", await run("insert into appt (staff, start_min, end_min) values (910001, 1075, 1100) returning id"));
console.log("insert other staff D (allowed)", await run("insert into appt (staff, start_min, end_min) values (910002, 1000, 1075) returning id"));
console.log("self-overlapping move of D (alone for its technician) by 15 minutes (allowed)", await run("update appt set start_min = start_min + 15, end_min = end_min + 15 where id = 4 returning id, start_min"));
console.log("move of A onto C (expect 23P01)", await run("update appt set start_min = 1080, end_min = 1150 where id = 1"));
console.log("another technician: book, cancel, rebook the very same time (allowed)",
  (await run("insert into appt (staff, start_min, end_min) values (910004, 500, 560) returning id")).ok,
  (await run("update appt set status = 'cancelled' where staff = 910004")).ok,
  (await run("insert into appt (staff, start_min, end_min) values (910004, 500, 560) returning id")).ok);
console.log("a cancelled row is not restored over a live one (expect 23P01)", await run("update appt set status = 'confirmed' where staff = 910004 and status = 'cancelled'"));
// two concurrent sessions racing for one slot
const a = new pg.Client({ connectionString: url });
const b = new pg.Client({ connectionString: url });
await a.connect();
await b.connect();
const race = await Promise.allSettled([
  a.query("insert into appt (staff, start_min, end_min) values (910003, 2000, 2060)"),
  b.query("insert into appt (staff, start_min, end_min) values (910003, 2030, 2090)"),
]);
console.log("race", race.map((r) => (r.status === "fulfilled" ? "ok" : r.reason.code)));
await a.end();
await b.end();
await client.end();
await db.stop();
