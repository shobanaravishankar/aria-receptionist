/* The appointment store in Postgres (Netlify Database). The same contract as MemoryStore and the Python SQLite store, held to the same tests (test/contract).
 *
 * Every writer is ONE SQL statement (a chain of data-modifying CTEs), so it is atomic whatever the connector does about interactive transactions: either the booking, its client, its event,
 * its idempotency key and its alert row all exist, or none does. The application judges first (the placement judge, for the reasons and wording the caller hears); the database is the last
 * arbiter of overlap, through the exclusion constraints in the migration. A repeated request is recognised by its key row, also written in that one statement. */

import { CANCELLED, COMPLETED, CONFIRMED, nameKey, NO_SHOW, ORIGIN_SEED, ORIGIN_TEST, outcome, placementOf, Refused, STATUSES } from "./appointments.ts";
import type { ClientRecord, NewBooking, Outcome, Placement, StoredAppointment } from "./appointments.ts";
import { cleanReason, makeReference, NOTHING, REMOVED, REPLAYED, SAVED, STALE, StoreError, UNCHANGED } from "./store.ts";
import type { EventRow, Expected, OutboxBuilder, OutboxRow, Preference, PreferenceOutcome, PreferenceStore, Store, Validator } from "./store.ts";
import type { CallDoc, CallStore, Pending } from "./callState.ts";
import { CallConflict, emptyCall } from "./callState.ts";
import type { Instant } from "./time.ts";

/** The one thing the store needs from a connection: pg.Pool, the Netlify serverless pool and a pg.Client all have it. */
export interface Queryable {
  query(text: string, params?: unknown[]): Promise<{ rows: any[]; rowCount?: number | null }>;
}

const COLUMNS =
  "a.appt_id, a.reference, a.client_id, c.name as client_name, a.service_id, a.service_name, a.category, a.staff_id, a.start_utc, a.duration_minutes, a.buffer_before_minutes, a.buffer_after_minutes, " +
  "a.status, a.version, a.origin, a.created_at, a.updated_at, a.cancelled_at, a.cancel_reason, a.seed_key";
const num = (v: unknown): number | null => (v === null || v === undefined ? null : Number(v));

function toAppointment(r: any): StoredAppointment {
  return {
    apptId: Number(r.appt_id), reference: r.reference, clientId: Number(r.client_id), clientName: r.client_name, serviceId: r.service_id, serviceName: r.service_name, category: r.category,
    staffId: r.staff_id, startUtc: Number(r.start_utc), durationMinutes: Number(r.duration_minutes), bufferBeforeMinutes: Number(r.buffer_before_minutes),
    bufferAfterMinutes: Number(r.buffer_after_minutes), status: r.status, version: Number(r.version), origin: r.origin, createdAt: Number(r.created_at), updatedAt: Number(r.updated_at),
    cancelledAt: num(r.cancelled_at), cancelReason: r.cancel_reason ?? null, seedKey: r.seed_key ?? null,
  };
}
const toClient = (r: any): ClientRecord => ({ clientId: Number(r.client_id), name: r.name, phone: r.phone });
const code = (error: unknown): string | undefined => (error as { code?: string }).code;
const constraint = (error: unknown): string | undefined => (error as { constraint?: string }).constraint;
const normalName = (name: string) => name.split(/\s+/).filter(Boolean).join(" ");

export class PgStore implements Store {
  readonly preferences: PgPreferences;
  private readonly db: Queryable;
  private readonly clock: () => number;
  private readonly randomReference: (() => number) | undefined;

  /** `clock` returns epoch SECONDS (the store's own time). */
  constructor(db: Queryable, opts: { clock: () => number; randomReference?: () => number }) {
    this.db = db;
    this.clock = opts.clock;
    this.randomReference = opts.randomReference;
    this.preferences = new PgPreferences(db, opts.clock);
  }

  private async rows(sql: string, params: unknown[] = []): Promise<any[]> {
    try {
      return (await this.db.query(sql, params)).rows;
    } catch (error) {
      if (code(error) === undefined || /^(08|53|57|58|XX)/.test(code(error)!)) throw new StoreError(`the appointment records are not reachable (${(error as Error).message.slice(0, 80)})`);
      throw error;
    }
  }
  private select(where: string, params: unknown[], order = "order by a.start_utc, a.appt_id"): Promise<StoredAppointment[]> {
    return this.rows(`select ${COLUMNS} from appointments a join clients c on c.client_id = a.client_id ${where} ${order}`, params).then((rs) => rs.map(toAppointment));
  }

  // ---- reads
  async get(reference: string): Promise<StoredAppointment | null> {
    return (await this.select("where a.reference = $1", [reference]))[0] ?? null;
  }
  async between(startUtc: number, endUtc_: number, opts: { staffId?: string | null; statuses?: readonly string[] } = {}): Promise<StoredAppointment[]> {
    const statuses = [...(opts.statuses ?? [CONFIRMED])];
    return this.select(`where a.status = any($1::text[]) and a.start_utc < $2 and a.start_utc + a.duration_minutes * 60 > $3 and ($4::text is null or a.staff_id = $4::text)`, [statuses, endUtc_, startUtc, opts.staffId ?? null]);
  }
  async occupiedBetween(startUtc: number, endUtc_: number, opts: { staffId: string }): Promise<StoredAppointment[]> {
    return this.select(
      `where a.status = 'confirmed' and a.staff_id = $1 and a.start_utc - a.buffer_before_minutes * 60 < $2 and a.start_utc + (a.duration_minutes + a.buffer_after_minutes) * 60 > $3`,
      [opts.staffId, endUtc_, startUtc],
    );
  }
  async forClient(clientId: number, statuses: readonly string[] = STATUSES): Promise<StoredAppointment[]> {
    return this.select("where a.client_id = $1 and a.status = any($2::text[])", [clientId, [...statuses]]);
  }
  async clientsWithPhone(phone: string): Promise<ClientRecord[]> {
    return (await this.rows("select client_id, name, phone from clients where phone = $1 order by client_id", [phone])).map(toClient);
  }
  async client(clientId: number): Promise<ClientRecord | null> {
    const found = await this.rows("select client_id, name, phone from clients where client_id = $1", [clientId]);
    return found.length ? toClient(found[0]) : null;
  }
  async requestReference(requestKey: string, kind: string): Promise<string | null> {
    const found = await this.rows("select reference from requests where request_key = $1 and kind = $2", [requestKey, kind]);
    return found.length ? found[0].reference : null;
  }
  async seededAfter(prefix: string, afterUtc: number): Promise<boolean> {
    const found = await this.rows("select 1 from appointments where left(seed_key, $1::int) = $2::text and start_utc > $3 limit 1", [prefix.length, prefix, afterUtc]);
    return found.length > 0;
  }
  async counts() {
    const appointments: Record<string, number> = {};
    for (const r of await this.rows("select status, count(*) as n from appointments group by status")) appointments[r.status] = Number(r.n);
    const outbox: Record<string, number> = {};
    for (const r of await this.rows("select status, count(*) as n from outbox group by status")) outbox[r.status] = Number(r.n);
    const clients = Number((await this.rows("select count(*) as n from clients"))[0].n);
    return { appointments, clients, outbox };
  }
  async changeStamp(): Promise<string> {
    const a = (await this.rows("select count(*) as n, coalesce(sum(version), 0) as v, coalesce(max(updated_at), 0) as u from appointments"))[0];
    const e = (await this.rows("select coalesce(max(event_id), 0) as e from appointment_events"))[0];
    return `${Number(a.n)}.${Number(a.v)}.${Number(a.u)}.${Number(e.e)}`;
  }
  async outboxRows(status: string | null = null): Promise<OutboxRow[]> {
    const found = await this.rows("select * from outbox where ($1::text is null or status = $1::text) order by outbox_id", [status]);
    return found.map((r) => ({ outboxId: Number(r.outbox_id), dedupeKey: r.dedupe_key, kind: r.kind, reference: r.reference, body: r.body, status: r.status, attempts: Number(r.attempts), lastError: r.last_error ?? null, createdAt: Number(r.created_at) }));
  }
  async events(reference: string): Promise<EventRow[]> {
    return (await this.rows("select kind, detail, at from appointment_events where reference = $1 order by event_id", [reference])).map((r) => ({ kind: r.kind, detail: r.detail, at: Number(r.at) }));
  }

  // ---- helpers for the writers
  private rowsBetween = async (low: number, high: number, statuses: readonly string[]) => this.between(low, high, { statuses });

  private async replayed(requestKey: string | null | undefined, kind: string): Promise<Outcome | null> {
    if (!requestKey) return null;
    const found = await this.rows("select kind, reference from requests where request_key = $1", [requestKey]);
    if (!found.length) return null;
    if (found[0].kind !== kind) throw new Refused("request_key_reused", "that request key belongs to a different kind of request");
    return outcome("replayed", await this.get(found[0].reference));
  }

  /** Why a refused insert or update was refused, in the order the Python store checks: a technician's occupied time first, then the client's own overlapping service. */
  private async overlapReason(placement: Placement, clientId: number): Promise<Refused | null> {
    const blockedStart = placement.startUtc - placement.bufferBeforeMinutes * 60;
    const mine = placement.startUtc + placement.durationMinutes * 60;
    const blockedEnd = mine + placement.bufferAfterMinutes * 60;
    for (const other of await this.occupiedBetween(blockedStart, blockedEnd, { staffId: placement.staffId })) {
      if (other.reference !== placement.ignoreReference) return new Refused("unavailable", "slot_taken");
    }
    for (const other of await this.between(placement.startUtc, mine)) {
      if (other.clientId === clientId && other.reference !== placement.ignoreReference) return new Refused("unavailable", "client_already_booked", other);
    }
    return null;
  }
  /** The overlap, if any, for this client (by number and name) and this placement: checked BEFORE a reference is drawn, as the Python store does, so a refusal costs nothing. */
  private async conflictFor(booking: NewBooking, placement: Placement): Promise<Refused | null> {
    const existing = (await this.clientsWithPhone(booking.phone)).find((c) => nameKey(c.name) === nameKey(booking.clientName));
    return this.overlapReason(placement, existing ? existing.clientId : -1);
  }

  // ---- the writers
  async book(booking: NewBooking, opts: { validate?: Validator | null; outbox?: OutboxBuilder | null } = {}): Promise<Outcome> {
    try {
      const replay = await this.replayed(booking.requestKey, "book");
      if (replay) return replay;
      if (booking.seedKey) {
        const seeded = await this.select("where a.seed_key = $1", [booking.seedKey]);
        if (seeded.length) return outcome("replayed", seeded[0]);
      }
      const placement = placementOf(booking);
      if (opts.validate) await opts.validate(this.rowsBetween, placement);
      const early = await this.conflictFor(booking, placement);
      if (early !== null) throw early;
      const display = (await this.clientsWithPhone(booking.phone)).find((c) => nameKey(c.name) === nameKey(booking.clientName))?.name ?? normalName(booking.clientName);
      for (let attempt = 0; attempt < 50; attempt++) {
        const reference = makeReference(booking.seedKey ?? null, attempt, this.randomReference);
        const now = this.clock();
        const predicted: StoredAppointment = {
          apptId: 0, reference, clientId: 0, clientName: display, serviceId: booking.serviceId, serviceName: booking.serviceName, category: booking.category, staffId: booking.staffId,
          startUtc: booking.startUtc, durationMinutes: booking.durationMinutes, bufferBeforeMinutes: booking.bufferBeforeMinutes ?? 0, bufferAfterMinutes: booking.bufferAfterMinutes ?? 0,
          status: CONFIRMED, version: 1, origin: booking.origin ?? "voice", createdAt: now, updatedAt: now, cancelledAt: null, cancelReason: null, seedKey: booking.seedKey ?? null,
        };
        const message = predicted.origin === ORIGIN_SEED || !opts.outbox ? null : opts.outbox(predicted, null);
        try {
          const done = await this.rows(
            `with c as (
               insert into clients (name, name_key, phone, created_at) values ($1::text, $2::text, $3::text, $4::bigint)
               on conflict (phone, name_key) do update set phone = excluded.phone returning client_id, name
             ), a as (
               insert into appointments (reference, client_id, service_id, service_name, category, staff_id, start_utc, duration_minutes, buffer_before_minutes, buffer_after_minutes, status, version, origin, seed_key, created_at, updated_at)
               select $5::text, c.client_id, $6::text, $7::text, $8::text, $9::text, $10::bigint, $11::int, $12::int, $13::int, 'confirmed', 1, $14::text, $15::text, $4::bigint, $4::bigint from c
               returning *
             ), e as (
               insert into appointment_events (reference, kind, detail, at) select reference, 'booked', $16::jsonb, $4::bigint from a
             ), r as (
               insert into requests (request_key, kind, reference, created_at) select $17::text, 'book', a.reference, $4::bigint from a where $17::text is not null
             ), o as (
               insert into outbox (dedupe_key, kind, reference, body, status, created_at) select $18::text, $19::text, a.reference, $20::text, 'pending', $4::bigint from a where $18::text is not null
               on conflict (dedupe_key) do nothing
             )
             select ${COLUMNS} from a, c`.replace("from a, c", "from a join c on c.client_id = a.client_id"),
            [
              normalName(booking.clientName), nameKey(booking.clientName), booking.phone, now, reference, booking.serviceId, booking.serviceName, booking.category, booking.staffId, booking.startUtc,
              booking.durationMinutes, booking.bufferBeforeMinutes ?? 0, booking.bufferAfterMinutes ?? 0, predicted.origin, booking.seedKey ?? null,
              JSON.stringify({ origin: predicted.origin, service: booking.serviceId, staff: booking.staffId, start: booking.startUtc }), booking.requestKey ?? null,
              message ? `${message.kind}:${reference}:1` : null, message ? message.kind : null, message ? message.body : null,
            ],
          );
          return outcome("booked", toAppointment(done[0]));
        } catch (error) {
          const c = code(error);
          if (c === "23P01") { // an overlap: but if THIS request already got in (it raced itself), its first answer is the answer
            const again = await this.replayed(booking.requestKey, "book");
            if (again) return again;
            throw (await this.conflictFor(booking, placement)) ?? new Refused("unavailable", "slot_taken"); // lost a race for the slot
          }
          if (c === "23505" && constraint(error) === "appointments_reference_key") continue; // a reference already taken: draw another
          if (c === "23505") { // the same request, or the same seed, got there first: its answer is the answer
            const again = (await this.replayed(booking.requestKey, "book")) ?? (booking.seedKey ? outcome("replayed", (await this.select("where a.seed_key = $1", [booking.seedKey]))[0] ?? null) : null);
            if (again) return again;
          }
          throw error;
        }
      }
      throw new StoreError("could not make a unique reference");
    } catch (error) {
      if (error instanceof Refused) return outcome(error.status, error.appointment, { reason: error.reason, detail: error.detail });
      throw error;
    }
  }

  async addHistory(booking: NewBooking, status: string): Promise<Outcome> {
    if (![COMPLETED, NO_SHOW, CANCELLED].includes(status) || ![ORIGIN_SEED, ORIGIN_TEST].includes(booking.origin ?? "voice")) {
      throw new Error("history rows are for seeding and tests only, with a final status");
    }
    if (booking.seedKey) {
      const existing = await this.select("where a.seed_key = $1", [booking.seedKey]);
      if (existing.length) return outcome("replayed", existing[0]);
    }
    for (let attempt = 0; attempt < 50; attempt++) {
      const reference = makeReference(booking.seedKey ?? null, attempt, this.randomReference);
      const now = this.clock();
      try {
        const done = await this.rows(
          `with c as (
             insert into clients (name, name_key, phone, created_at) values ($1::text, $2::text, $3::text, $4::bigint)
             on conflict (phone, name_key) do update set phone = excluded.phone returning client_id, name
           ), a as (
             insert into appointments (reference, client_id, service_id, service_name, category, staff_id, start_utc, duration_minutes, buffer_before_minutes, buffer_after_minutes, status, version, origin, seed_key, created_at, updated_at, cancelled_at)
             select $5::text, c.client_id, $6::text, $7::text, $8::text, $9::text, $10::bigint, $11::int, $12::int, $13::int, $14::text, 1, $15::text, $16::text, $4::bigint, $4::bigint,
                    case when $14::text = 'cancelled' then $4::bigint else null end from c
             returning *
           ), e as (
             insert into appointment_events (reference, kind, detail, at) select reference, 'history', $17::jsonb, $4::bigint from a
           )
           select ${COLUMNS} from a join c on c.client_id = a.client_id`,
          [
            normalName(booking.clientName), nameKey(booking.clientName), booking.phone, now, reference, booking.serviceId, booking.serviceName, booking.category, booking.staffId, booking.startUtc,
            booking.durationMinutes, booking.bufferBeforeMinutes ?? 0, booking.bufferAfterMinutes ?? 0, status, booking.origin ?? ORIGIN_SEED, booking.seedKey ?? null,
            JSON.stringify({ status, origin: booking.origin ?? ORIGIN_SEED }),
          ],
        );
        return outcome("booked", toAppointment(done[0]));
      } catch (error) {
        if (code(error) === "23505" && constraint(error) === "appointments_reference_key") continue;
        if (code(error) === "23505" && booking.seedKey) {
          const existing = await this.select("where a.seed_key = $1", [booking.seedKey]);
          if (existing.length) return outcome("replayed", existing[0]);
        }
        throw error;
      }
    }
    throw new StoreError("could not make a unique reference");
  }

  async cancel(reference: string, opts: { expectedVersion?: number | null; reason?: string | null; requestKey?: string | null; outbox?: OutboxBuilder | null } = {}): Promise<Outcome> {
    try {
      for (let tries = 0; tries < 3; tries++) {
        const replay = await this.replayed(opts.requestKey, "cancel");
        if (replay) return replay;
        const row = await this.get(reference);
        if (row === null) throw new Refused("not_found", "no_such_reference");
        if (row.status === CANCELLED) return outcome("already_cancelled", row); // before the version check: a repeat carries the version from BEFORE the first cancel
        if (row.status !== CONFIRMED) throw new Refused("not_cancellable", row.status, row);
        if (opts.expectedVersion != null && row.version !== opts.expectedVersion) throw new Refused("stale", "changed_since_confirmation", row);
        if (row.startUtc <= this.clock()) throw new Refused("not_cancellable", "already_started", row);
        const now = this.clock();
        const cleaned = cleanReason(opts.reason);
        const predicted: StoredAppointment = { ...row, status: CANCELLED, version: row.version + 1, updatedAt: now, cancelledAt: now, cancelReason: cleaned };
        const message = opts.outbox ? opts.outbox(predicted, row) : null;
        let done: any[];
        try {
          done = await this.rows(
            `with u as (
               update appointments set status = 'cancelled', version = version + 1, updated_at = $2::bigint, cancelled_at = $2::bigint, cancel_reason = $3::text
               where reference = $1::text and status = 'confirmed' and version = $4::int returning *
             ), e as (
               insert into appointment_events (reference, kind, detail, at) select reference, 'cancelled', $5::jsonb, $2::bigint from u
             ), r as (
               insert into requests (request_key, kind, reference, created_at) select $6::text, 'cancel', u.reference, $2::bigint from u where $6::text is not null
             ), o as (
               insert into outbox (dedupe_key, kind, reference, body, status, created_at) select $7::text, $8::text, u.reference, $9::text, 'pending', $2::bigint from u where $7::text is not null
               on conflict (dedupe_key) do nothing
             )
             select ${COLUMNS.replaceAll("a.", "u.")} from u join clients c on c.client_id = u.client_id`,
            [reference, now, cleaned, row.version, JSON.stringify({ reason_given: cleaned !== null, staff: row.staffId, start: row.startUtc }), opts.requestKey ?? null,
              message ? `${message.kind}:${reference}:${predicted.version}` : null, message ? message.kind : null, message ? message.body : null],
          );
        } catch (error) {
          if (code(error) === "23505") { const again = await this.replayed(opts.requestKey, "cancel"); if (again) return again; }
          throw error;
        }
        if (done.length) return outcome("cancelled", toAppointment(done[0]), { previous: row });
        // it changed between the read and the write: look again
      }
      throw new Refused("stale", "changed_since_confirmation", await this.get(reference));
    } catch (error) {
      if (error instanceof Refused) return outcome(error.status, error.appointment, { reason: error.reason, detail: error.detail });
      throw error;
    }
  }

  async reschedule(
    reference: string,
    opts: { startUtc: number; staffId?: string | null; expectedVersion?: number | null; validate?: Validator | null; requestKey?: string | null; outbox?: OutboxBuilder | null },
  ): Promise<Outcome> {
    try {
      for (let tries = 0; tries < 3; tries++) {
        const replay = await this.replayed(opts.requestKey, "reschedule");
        if (replay) return replay;
        const row = await this.get(reference);
        if (row === null) throw new Refused("not_found", "no_such_reference");
        if (row.status !== CONFIRMED) throw new Refused("not_reschedulable", row.status, row);
        if (opts.expectedVersion != null && row.version !== opts.expectedVersion) throw new Refused("stale", "changed_since_confirmation", row);
        if (row.startUtc <= this.clock()) throw new Refused("not_reschedulable", "already_started", row);
        const targetStaff = opts.staffId || row.staffId;
        if (opts.startUtc === row.startUtc && targetStaff === row.staffId) return outcome("unchanged", row);
        const placement: Placement = {
          staffId: targetStaff, startUtc: opts.startUtc, durationMinutes: row.durationMinutes, bufferBeforeMinutes: row.bufferBeforeMinutes, bufferAfterMinutes: row.bufferAfterMinutes,
          serviceId: row.serviceId, category: row.category, ignoreReference: reference,
        };
        if (opts.validate) await opts.validate(this.rowsBetween, placement);
        const now = this.clock();
        const predicted: StoredAppointment = { ...row, startUtc: opts.startUtc, staffId: targetStaff, version: row.version + 1, updatedAt: now };
        const message = opts.outbox ? opts.outbox(predicted, row) : null;
        let done: any[];
        try {
          done = await this.rows(
            `with u as (
               update appointments set start_utc = $2::bigint, staff_id = $3::text, version = version + 1, updated_at = $4::bigint
               where reference = $1::text and status = 'confirmed' and version = $5::int returning *
             ), e as (
               insert into appointment_events (reference, kind, detail, at) select reference, 'rescheduled', $6::jsonb, $4::bigint from u
             ), r as (
               insert into requests (request_key, kind, reference, created_at) select $7::text, 'reschedule', u.reference, $4::bigint from u where $7::text is not null
             ), o as (
               insert into outbox (dedupe_key, kind, reference, body, status, created_at) select $8::text, $9::text, u.reference, $10::text, 'pending', $4::bigint from u where $8::text is not null
               on conflict (dedupe_key) do nothing
             )
             select ${COLUMNS.replaceAll("a.", "u.")} from u join clients c on c.client_id = u.client_id`,
            [reference, opts.startUtc, targetStaff, now, row.version, JSON.stringify({ from_start: row.startUtc, to_start: opts.startUtc, from_staff: row.staffId, to_staff: targetStaff }),
              opts.requestKey ?? null, message ? `${message.kind}:${reference}:${predicted.version}` : null, message ? message.kind : null, message ? message.body : null],
          );
        } catch (error) {
          if (code(error) === "23P01") {
            const again = await this.replayed(opts.requestKey, "reschedule");
            if (again) return again;
            throw (await this.overlapReason(placement, row.clientId)) ?? new Refused("unavailable", "slot_taken");
          }
          if (code(error) === "23505") { const again = await this.replayed(opts.requestKey, "reschedule"); if (again) return again; }
          throw error;
        }
        if (done.length) return outcome("rescheduled", toAppointment(done[0]), { previous: row });
      }
      throw new Refused("stale", "changed_since_confirmation", await this.get(reference));
    } catch (error) {
      if (error instanceof Refused) return outcome(error.status, error.appointment, { reason: error.reason, detail: error.detail });
      throw error;
    }
  }

  async settlePastSeeds(): Promise<number> {
    const now = this.clock();
    const done = await this.rows(
      `with u as (
         update appointments set status = 'completed', version = version + 1, updated_at = $1::bigint
         where status = 'confirmed' and origin = 'seed' and start_utc + duration_minutes * 60 <= $1::bigint returning reference, start_utc
       ), e as (
         insert into appointment_events (reference, kind, detail, at) select reference, 'settled', jsonb_build_object('start', start_utc, 'as', 'completed', 'fixture', true), $1::bigint from u
       )
       select count(*) as n from u`,
      [now],
    );
    return Number(done[0].n);
  }

  /** Everything the demo holds, as plain rows, for the synthetic export. */
  async exportRows(): Promise<Record<string, any[]>> {
    const out: Record<string, any[]> = {};
    for (const table of ["clients", "appointments", "appointment_events", "requests", "outbox", "client_preferences", "preference_requests", "preference_events"]) {
      const cols = table === "appointments" ? "appt_id, reference, client_id, service_id, service_name, category, staff_id, start_utc, duration_minutes, buffer_before_minutes, buffer_after_minutes, status, version, origin, seed_key, created_at, updated_at, cancelled_at, cancel_reason" : "*";
      out[table] = (await this.rows(`select ${cols} from ${table} order by 1`)).map((r) => JSON.parse(JSON.stringify(r)));
    }
    return out;
  }
}

class PgPreferences implements PreferenceStore {
  private readonly db: Queryable;
  private readonly clock: () => number;
  constructor(db: Queryable, clock: () => number) {
    this.db = db;
    this.clock = clock;
  }
  private toPreference = (r: any): Preference => ({ clientId: Number(r.client_id), familyId: r.family_id, staffId: r.staff_id, version: Number(r.version), createdAt: Number(r.created_at), updatedAt: Number(r.updated_at) });
  async get(clientId: number, familyId: string): Promise<Preference | null> {
    const found = (await this.db.query("select * from client_preferences where client_id = $1 and family_id = $2", [clientId, familyId])).rows;
    return found.length ? this.toPreference(found[0]) : null;
  }
  async forClient(clientId: number): Promise<Preference[]> {
    return (await this.db.query("select * from client_preferences where client_id = $1 order by family_id", [clientId])).rows.map(this.toPreference);
  }
  /* Both writers are ONE statement. The request key is claimed first (insert ... on conflict do nothing), and the preference change and its event only happen if THIS statement claimed it, so
   * the key and the effect commit together or not at all, and of any number of requests racing on one key exactly one has an effect (the others wait on the key's unique index, then replay).
   * `expected` is the state the caller confirmed (null: none saved); the change only applies to exactly that state, checked on the row the statement locks, not on an earlier read. */
  async set(clientId: number, familyId: string, staffId: string, opts: { requestKey?: string | null; expected?: Expected | null } = {}): Promise<PreferenceOutcome> {
    const key = opts.requestKey ?? null;
    const checked = opts.expected !== undefined;
    const before = await this.get(clientId, familyId);
    let row: any;
    try {
      row = (
        await this.db.query(
          `with k as (
             insert into preference_requests (request_key, kind, client_id, family_id, created_at) select $5::text, 'set', $1::bigint, $2::text, $4::bigint where $5::text is not null on conflict do nothing returning request_key
           ), go as (
             select 1 as one where $5::text is null union all select 1 from k
           ), i as (
             insert into client_preferences (client_id, family_id, staff_id, version, created_at, updated_at)
               select $1::bigint, $2::text, $3::text, 1, $4::bigint, $4::bigint from go where not $6::boolean or $7::text is null
             on conflict (client_id, family_id) do update set staff_id = excluded.staff_id, version = client_preferences.version + 1, updated_at = excluded.updated_at
               where not $6::boolean and client_preferences.staff_id is distinct from excluded.staff_id
             returning *
           ), u as (
             update client_preferences set staff_id = $3::text, version = version + 1, updated_at = $4::bigint
               where client_id = $1::bigint and family_id = $2::text and $6::boolean and $7::text is not null and exists (select 1 from go)
                 and staff_id = $7::text and version = $8::integer and staff_id <> $3::text
             returning *
           ), p as (
             select * from i union all select * from u
           ), e as (
             insert into preference_events (client_id, family_id, kind, detail, at) select client_id, family_id, 'set', jsonb_build_object('staff', staff_id, 'version', version), $4::bigint from p
           )
           select (select count(*) from go)::integer as went, (select to_jsonb(p) from p limit 1) as saved`,
          [clientId, familyId, staffId, this.clock(), key, checked, checked && opts.expected ? opts.expected.staffId : null, checked && opts.expected ? opts.expected.version : null],
        )
      ).rows[0];
    } catch (error) {
      if (code(error) === "23503") throw new StoreError("that client does not exist, so nothing was saved");
      throw error;
    }
    if (Number(row.went) === 0) return { status: REPLAYED, preference: await this.get(clientId, familyId), previous: null };
    if (row.saved) return { status: SAVED, preference: this.toPreference(row.saved), previous: before ? before.staffId : null };
    const after = await this.get(clientId, familyId);
    return after !== null && after.staffId === staffId ? { status: UNCHANGED, preference: after, previous: null } : { status: STALE, preference: after, previous: null };
  }
  async remove(clientId: number, familyId: string, opts: { requestKey?: string | null; expected: Expected | null }): Promise<PreferenceOutcome> {
    if (opts?.expected === undefined) throw new StoreError("a removal must name the preference it removes");
    const key = opts.requestKey ?? null;
    const row = (
      await this.db.query(
        `with k as (
           insert into preference_requests (request_key, kind, client_id, family_id, created_at) select $4::text, 'remove', $1::bigint, $2::text, $3::bigint where $4::text is not null on conflict do nothing returning request_key
         ), go as (
           select 1 as one where $4::text is null union all select 1 from k
         ), d as (
           delete from client_preferences where client_id = $1::bigint and family_id = $2::text and exists (select 1 from go) and staff_id = $5::text and version = $6::integer returning *
         ), e as (
           insert into preference_events (client_id, family_id, kind, detail, at) select client_id, family_id, 'remove', jsonb_build_object('staff', staff_id, 'version', version), $3::bigint from d
         )
         select (select count(*) from go)::integer as went, (select staff_id from d limit 1) as removed`,
        [clientId, familyId, this.clock(), key, opts.expected ? opts.expected.staffId : null, opts.expected ? opts.expected.version : null],
      )
    ).rows[0];
    if (Number(row.went) === 0) return { status: REPLAYED, preference: null, previous: null };
    if (row.removed) return { status: REMOVED, preference: null, previous: row.removed };
    const after = await this.get(clientId, familyId);
    return after === null ? { status: NOTHING, preference: null, previous: null } : { status: STALE, preference: after, previous: null };
  }
}

/** What the voice tools remember within a call, in Postgres. */
export class PgCallStore implements CallStore {
  private readonly db: Queryable;
  private readonly clock: () => Instant;
  constructor(db: Queryable, clock: () => Instant = () => Date.now()) {
    this.db = db;
    this.clock = clock;
  }
  async load(callId: string): Promise<CallDoc> {
    const found = (await this.db.query("select doc, rev from call_state where call_id = $1", [callId])).rows;
    return found.length ? { ...emptyCall(), ...found[0].doc, rev: Number(found[0].rev) } : emptyCall();
  }
  async save(callId: string, doc: CallDoc): Promise<void> {
    const { rev, ...rest } = doc;
    const saved = (
      await this.db.query(
        `insert into call_state (call_id, doc, rev, updated_at) values ($1::text, $2::jsonb, $4::integer + 1, $3::bigint)
         on conflict (call_id) do update set doc = excluded.doc, rev = excluded.rev, updated_at = excluded.updated_at where call_state.rev = $4::integer
         returning rev`,
        [callId, JSON.stringify(rest), this.clock(), rev],
      )
    ).rows;
    if (!saved.length) throw new CallConflict(callId);
    doc.rev = Number(saved[0].rev);
  }
  async putPending(token: string, pending: Pending, now: Instant): Promise<void> {
    await this.db.query(
      `with d as (delete from pending_confirmations where expires_at <= $1::bigint or (call_id = $2::text and kind = $3::text))
       insert into pending_confirmations (token, kind, call_id, payload, expires_at) values ($4::text, $3::text, $2::text, $5::jsonb, $6::bigint)`,
      [now, pending.callId, pending.kind, token, JSON.stringify(pending.payload), pending.expires],
    );
  }
  async getPending(token: string): Promise<Pending | null> {
    const found = (await this.db.query("select kind, call_id, payload, expires_at from pending_confirmations where token = $1", [token])).rows;
    return found.length ? { kind: found[0].kind, callId: found[0].call_id, payload: found[0].payload, expires: Number(found[0].expires_at) } : null;
  }
}

