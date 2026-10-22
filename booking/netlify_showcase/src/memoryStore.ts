/* A model of the Python appointment store (SQLite, BEGIN IMMEDIATE) held in memory: the reference the contract tests and the Python-oracle scenario replay run against. Every writer runs
 * alone (a promise chain stands in for BEGIN IMMEDIATE), judges against its own state, checks the store's invariants (a technician's OCCUPIED time is never shared, a client is never in two
 * overlapping services) and answers with an Outcome. This is a MODEL, not the production store: it proves the logic and the contract, and says nothing about Postgres. */

import {
  blockedEndUtc, blockedStartUtc, CANCELLED, COMPLETED, CONFIRMED, endUtc, nameKey, NO_SHOW, ORIGIN_SEED, ORIGIN_TEST, outcome, placementOf, Refused, STATUSES,
} from "./appointments.ts";
import type { ClientRecord, NewBooking, Outcome, Placement, StoredAppointment } from "./appointments.ts";
import { cleanReason, makeReference, NOTHING, REMOVED, REPLAYED, SAVED, STALE, StoreError, UNCHANGED } from "./store.ts";
import type { EventRow, Expected, OutboxBuilder, OutboxRow, Preference, PreferenceOutcome, PreferenceStore, Store, Validator } from "./store.ts";

interface ClientRow extends ClientRecord {
  nameKey: string;
  createdAt: number;
}
interface RequestRow {
  kind: string;
  reference: string;
}

export class MemoryStore implements Store {
  readonly preferences: MemoryPreferences;
  private readonly appointments: StoredAppointment[] = [];
  private readonly clients: ClientRow[] = [];
  private readonly requests = new Map<string, RequestRow>();
  private readonly eventLog: Array<EventRow & { eventId: number; reference: string }> = [];
  private readonly outbox: OutboxRow[] = [];
  private nextAppt = 1;
  private nextClient = 1;
  private tail: Promise<unknown> = Promise.resolve();
  private readonly clock: () => number;
  private readonly randomReference: (() => number) | undefined;

  /** `clock` returns epoch SECONDS (the store's own time). `randomReference` draws an index into the reference alphabet (tests make it deterministic). */
  constructor(opts: { clock: () => number; randomReference?: () => number }) {
    this.clock = opts.clock;
    this.randomReference = opts.randomReference;
    this.preferences = new MemoryPreferences(this, this.clock);
  }

  // ---- one writer at a time, like BEGIN IMMEDIATE
  async serialised<T>(work: () => Promise<T>): Promise<T> {
    const run = this.tail.then(work, work);
    this.tail = run.catch(() => undefined);
    return run;
  }

  // ---- reads
  async get(reference: string): Promise<StoredAppointment | null> {
    return this.row(reference);
  }
  private row(reference: string): StoredAppointment | null {
    const found = this.appointments.find((a) => a.reference === reference);
    return found ? { ...found } : null;
  }
  private named(row: StoredAppointment): StoredAppointment {
    const client = this.clients.find((c) => c.clientId === row.clientId);
    return { ...row, clientName: client ? client.name : row.clientName };
  }
  private rows(filter: (a: StoredAppointment) => boolean): StoredAppointment[] {
    return this.appointments.filter(filter).sort((a, b) => a.startUtc - b.startUtc || a.apptId - b.apptId).map((a) => this.named(a));
  }
  async between(startUtc: number, endUtc_: number, opts: { staffId?: string | null; statuses?: readonly string[] } = {}): Promise<StoredAppointment[]> {
    const statuses = opts.statuses ?? [CONFIRMED];
    return this.rows((a) => statuses.includes(a.status) && a.startUtc < endUtc_ && endUtc(a) > startUtc && (opts.staffId == null || a.staffId === opts.staffId));
  }
  async occupiedBetween(startUtc: number, endUtc_: number, opts: { staffId: string }): Promise<StoredAppointment[]> {
    return this.rows((a) => a.status === CONFIRMED && a.staffId === opts.staffId && blockedStartUtc(a) < endUtc_ && blockedEndUtc(a) > startUtc);
  }
  async forClient(clientId: number, statuses: readonly string[] = STATUSES): Promise<StoredAppointment[]> {
    return this.rows((a) => a.clientId === clientId && statuses.includes(a.status));
  }
  async clientsWithPhone(phone: string): Promise<ClientRecord[]> {
    return this.clients.filter((c) => c.phone === phone).sort((a, b) => a.clientId - b.clientId).map(({ clientId, name, phone: p }) => ({ clientId, name, phone: p }));
  }
  async client(clientId: number): Promise<ClientRecord | null> {
    const c = this.clients.find((x) => x.clientId === clientId);
    return c ? { clientId: c.clientId, name: c.name, phone: c.phone } : null;
  }
  async requestReference(requestKey: string, kind: string): Promise<string | null> {
    const r = this.requests.get(requestKey);
    return r && r.kind === kind ? r.reference : null;
  }
  async seededAfter(prefix: string, afterUtc: number): Promise<boolean> {
    return this.appointments.some((a) => a.seedKey !== null && a.seedKey.startsWith(prefix) && a.startUtc > afterUtc);
  }
  async counts() {
    const appointments: Record<string, number> = {};
    for (const a of this.appointments) appointments[a.status] = (appointments[a.status] ?? 0) + 1;
    const outbox: Record<string, number> = {};
    for (const o of this.outbox) outbox[o.status] = (outbox[o.status] ?? 0) + 1;
    return { appointments, clients: this.clients.length, outbox };
  }
  async changeStamp(): Promise<string> {
    const versions = this.appointments.reduce((n, a) => n + a.version, 0);
    const updated = this.appointments.reduce((n, a) => Math.max(n, a.updatedAt), 0);
    const event = this.eventLog.reduce((n, e) => Math.max(n, e.eventId), 0);
    return `${this.appointments.length}.${versions}.${updated}.${event}`;
  }
  async outboxRows(status: string | null = null): Promise<OutboxRow[]> {
    return this.outbox.filter((o) => status === null || o.status === status).map((o) => ({ ...o }));
  }
  async events(reference: string): Promise<EventRow[]> {
    return this.eventLog.filter((e) => e.reference === reference).map(({ kind, detail, at }) => ({ kind, detail, at }));
  }

  // ---- the writers
  async book(booking: NewBooking, opts: { validate?: Validator | null; outbox?: OutboxBuilder | null } = {}): Promise<Outcome> {
    return this.serialised(async () => {
      try {
        const replay = this.replayed(booking.requestKey, "book");
        if (replay) return replay;
        if (booking.seedKey) {
          const seeded = this.appointments.find((a) => a.seedKey === booking.seedKey);
          if (seeded) return outcome("replayed", this.named({ ...seeded }));
        }
        const placement = placementOf(booking);
        if (opts.validate) await opts.validate(async (low, high, statuses) => this.between(low, high, { statuses }), placement);
        const snapshot = this.snapshotState();
        try {
          const client = this.clientFor(booking.clientName, booking.phone);
          this.guard(placement, client.clientId);
          const reference = this.newReference(booking.seedKey ?? null);
          const now = this.clock();
          const row: StoredAppointment = {
            apptId: this.nextAppt++, reference, clientId: client.clientId, clientName: client.name, serviceId: booking.serviceId, serviceName: booking.serviceName, category: booking.category,
            staffId: booking.staffId, startUtc: booking.startUtc, durationMinutes: booking.durationMinutes, bufferBeforeMinutes: booking.bufferBeforeMinutes ?? 0,
            bufferAfterMinutes: booking.bufferAfterMinutes ?? 0, status: CONFIRMED, version: 1, origin: booking.origin ?? "voice", createdAt: now, updatedAt: now, cancelledAt: null,
            cancelReason: null, seedKey: booking.seedKey ?? null,
          };
          this.appointments.push(row);
          const saved = this.named(row);
          this.event(reference, "booked", { origin: saved.origin, service: saved.serviceId, staff: saved.staffId, start: saved.startUtc });
          this.remember(booking.requestKey, "book", reference);
          this.notify("booked", saved, null, opts.outbox ?? null);
          return outcome("booked", saved);
        } catch (error) {
          this.restore(snapshot);
          throw error;
        }
      } catch (error) {
        return this.refusal(error);
      }
    });
  }

  async addHistory(booking: NewBooking, status: string): Promise<Outcome> {
    if (![COMPLETED, NO_SHOW, CANCELLED].includes(status) || ![ORIGIN_SEED, ORIGIN_TEST].includes(booking.origin ?? "voice")) {
      throw new Error("history rows are for seeding and tests only, with a final status");
    }
    return this.serialised(async () => {
      if (booking.seedKey) {
        const existing = this.appointments.find((a) => a.seedKey === booking.seedKey);
        if (existing) return outcome("replayed", this.named({ ...existing }));
      }
      const client = this.clientFor(booking.clientName, booking.phone);
      const reference = this.newReference(booking.seedKey ?? null);
      const now = this.clock();
      const row: StoredAppointment = {
        apptId: this.nextAppt++, reference, clientId: client.clientId, clientName: client.name, serviceId: booking.serviceId, serviceName: booking.serviceName, category: booking.category,
        staffId: booking.staffId, startUtc: booking.startUtc, durationMinutes: booking.durationMinutes, bufferBeforeMinutes: booking.bufferBeforeMinutes ?? 0,
        bufferAfterMinutes: booking.bufferAfterMinutes ?? 0, status, version: 1, origin: booking.origin ?? ORIGIN_SEED, createdAt: now, updatedAt: now,
        cancelledAt: status === CANCELLED ? now : null, cancelReason: null, seedKey: booking.seedKey ?? null,
      };
      this.appointments.push(row);
      this.event(reference, "history", { status, origin: row.origin });
      return outcome("booked", this.named(row));
    });
  }

  async cancel(reference: string, opts: { expectedVersion?: number | null; reason?: string | null; requestKey?: string | null; outbox?: OutboxBuilder | null } = {}): Promise<Outcome> {
    return this.serialised(async () => {
      try {
        const replay = this.replayed(opts.requestKey, "cancel");
        if (replay) return replay;
        const row = this.row(reference);
        if (row === null) throw new Refused("not_found", "no_such_reference");
        if (row.status === CANCELLED) return outcome("already_cancelled", this.named(row)); // before the version check: a repeat carries the version from BEFORE the first cancel
        if (row.status !== CONFIRMED) throw new Refused("not_cancellable", row.status, this.named(row));
        if (opts.expectedVersion != null && row.version !== opts.expectedVersion) throw new Refused("stale", "changed_since_confirmation", this.named(row));
        if (row.startUtc <= this.clock()) throw new Refused("not_cancellable", "already_started", this.named(row));
        const now = this.clock();
        const cleaned = cleanReason(opts.reason);
        const stored = this.appointments.find((a) => a.reference === reference)!;
        Object.assign(stored, { status: CANCELLED, version: stored.version + 1, updatedAt: now, cancelledAt: now, cancelReason: cleaned });
        const saved = this.named(stored);
        this.event(reference, "cancelled", { reason_given: cleaned !== null, staff: row.staffId, start: row.startUtc });
        this.remember(opts.requestKey, "cancel", reference);
        this.notify("cancelled", saved, this.named(row), opts.outbox ?? null);
        return outcome("cancelled", saved, { previous: this.named(row) });
      } catch (error) {
        return this.refusal(error);
      }
    });
  }

  async reschedule(
    reference: string,
    opts: { startUtc: number; staffId?: string | null; expectedVersion?: number | null; validate?: Validator | null; requestKey?: string | null; outbox?: OutboxBuilder | null },
  ): Promise<Outcome> {
    return this.serialised(async () => {
      try {
        const replay = this.replayed(opts.requestKey, "reschedule");
        if (replay) return replay;
        const row = this.row(reference);
        if (row === null) throw new Refused("not_found", "no_such_reference");
        if (row.status !== CONFIRMED) throw new Refused("not_reschedulable", row.status, this.named(row));
        if (opts.expectedVersion != null && row.version !== opts.expectedVersion) throw new Refused("stale", "changed_since_confirmation", this.named(row));
        if (row.startUtc <= this.clock()) throw new Refused("not_reschedulable", "already_started", this.named(row));
        const targetStaff = opts.staffId || row.staffId;
        if (opts.startUtc === row.startUtc && targetStaff === row.staffId) return outcome("unchanged", this.named(row));
        const placement: Placement = {
          staffId: targetStaff, startUtc: opts.startUtc, durationMinutes: row.durationMinutes, bufferBeforeMinutes: row.bufferBeforeMinutes, bufferAfterMinutes: row.bufferAfterMinutes,
          serviceId: row.serviceId, category: row.category, ignoreReference: reference,
        };
        if (opts.validate) await opts.validate(async (low, high, statuses) => this.between(low, high, { statuses }), placement);
        this.guard(placement, row.clientId);
        const now = this.clock();
        const stored = this.appointments.find((a) => a.reference === reference)!;
        Object.assign(stored, { startUtc: opts.startUtc, staffId: targetStaff, version: stored.version + 1, updatedAt: now });
        const saved = this.named(stored);
        this.event(reference, "rescheduled", { from_start: row.startUtc, to_start: saved.startUtc, from_staff: row.staffId, to_staff: saved.staffId });
        this.remember(opts.requestKey, "reschedule", reference);
        this.notify("rescheduled", saved, this.named(row), opts.outbox ?? null);
        return outcome("rescheduled", saved, { previous: this.named(row) });
      } catch (error) {
        return this.refusal(error);
      }
    });
  }

  async settlePastSeeds(): Promise<number> {
    return this.serialised(async () => {
      const now = this.clock();
      const due = this.appointments.filter((a) => a.status === CONFIRMED && a.origin === ORIGIN_SEED && endUtc(a) <= now);
      for (const a of due) {
        Object.assign(a, { status: COMPLETED, version: a.version + 1, updatedAt: now });
        this.event(a.reference, "settled", { start: a.startUtc, as: "completed", fixture: true });
      }
      return due.length;
    });
  }

  // ---- inside a write
  private snapshotState() {
    return { appointments: this.appointments.map((a) => ({ ...a })), clients: this.clients.map((c) => ({ ...c })), requests: new Map(this.requests), events: this.eventLog.length, outbox: this.outbox.length, nextAppt: this.nextAppt, nextClient: this.nextClient };
  }
  private restore(s: ReturnType<MemoryStore["snapshotState"]>): void {
    this.appointments.splice(0, this.appointments.length, ...s.appointments);
    this.clients.splice(0, this.clients.length, ...s.clients);
    this.requests.clear();
    for (const [k, v] of s.requests) this.requests.set(k, v);
    this.eventLog.length = s.events;
    this.outbox.length = s.outbox;
    this.nextAppt = s.nextAppt;
    this.nextClient = s.nextClient;
  }
  private refusal(error: unknown): Outcome {
    if (error instanceof Refused) return outcome(error.status, error.appointment, { reason: error.reason, detail: error.detail });
    throw error;
  }
  private clientFor(name: string, phone: string): ClientRow {
    const key = nameKey(name);
    const found = this.clients.find((c) => c.phone === phone && c.nameKey === key);
    if (found) return found;
    const row: ClientRow = { clientId: this.nextClient++, name: name.split(/\s+/).filter(Boolean).join(" "), phone, nameKey: key, createdAt: this.clock() };
    this.clients.push(row);
    return row;
  }
  /** The store's own invariants, whatever the caller's validator did. A technician's OCCUPIED time is never shared with another appointment's; a client is never in two overlapping services. */
  private guard(placement: Placement, clientId: number): void {
    const mine = placement.startUtc + placement.durationMinutes * 60;
    const blockedStart = placement.startUtc - placement.bufferBeforeMinutes * 60;
    const blockedEnd = mine + placement.bufferAfterMinutes * 60;
    for (const other of this.appointments) {
      if (other.status === CONFIRMED && other.staffId === placement.staffId && blockedStartUtc(other) < blockedEnd && blockedEndUtc(other) > blockedStart && other.reference !== placement.ignoreReference) {
        throw new Refused("unavailable", "slot_taken");
      }
    }
    for (const other of this.appointments) {
      if (other.status === CONFIRMED && other.startUtc < mine && endUtc(other) > placement.startUtc && other.clientId === clientId && other.reference !== placement.ignoreReference) {
        throw new Refused("unavailable", "client_already_booked", this.named(other));
      }
    }
  }
  private newReference(seedKey: string | null): string {
    for (let attempt = 0; attempt < 50; attempt++) {
      const candidate = makeReference(seedKey, attempt, this.randomReference);
      if (!this.appointments.some((a) => a.reference === candidate)) return candidate;
    }
    throw new StoreError("could not make a unique reference");
  }
  private event(reference: string, kind: string, detail: Record<string, unknown>): void {
    this.eventLog.push({ eventId: this.eventLog.length + 1, reference, kind, detail, at: this.clock() });
  }
  private remember(requestKey: string | null | undefined, kind: string, reference: string): void {
    if (requestKey) this.requests.set(requestKey, { kind, reference });
  }
  /** The earlier result of this very request, if there is one. A key reused for a different kind of request is refused, never reinterpreted. */
  private replayed(requestKey: string | null | undefined, kind: string): Outcome | null {
    if (!requestKey) return null;
    const r = this.requests.get(requestKey);
    if (!r) return null;
    if (r.kind !== kind) throw new Refused("request_key_reused", "that request key belongs to a different kind of request");
    return outcome("replayed", this.row(r.reference) ? this.named(this.row(r.reference)!) : null);
  }
  /** The owner notification, in THIS write. Creating a seed never notifies, but cancelling or moving a seeded appointment does, like any other. The dedupe key makes a second row impossible. */
  private notify(kind: string, saved: StoredAppointment, previous: StoredAppointment | null, builder: OutboxBuilder | null): void {
    if (builder === null || (kind === "booked" && saved.origin === ORIGIN_SEED)) return;
    const message = builder(saved, previous);
    if (message === null) return;
    const dedupeKey = `${message.kind}:${saved.reference}:${saved.version}`;
    if (this.outbox.some((o) => o.dedupeKey === dedupeKey)) return;
    this.outbox.push({ outboxId: this.outbox.length + 1, dedupeKey, kind: message.kind, reference: saved.reference, body: message.body, status: "pending", attempts: 0, lastError: null, createdAt: this.clock() });
  }

  /** Used by MemoryPreferences, inside the same one-writer-at-a-time chain. */
  hasClient(clientId: number): boolean {
    return this.clients.some((c) => c.clientId === clientId);
  }
}

class MemoryPreferences implements PreferenceStore {
  private readonly rows: Preference[] = [];
  private readonly requests = new Map<string, string>(); // `${kind}|${key}` -> kind
  private readonly store: MemoryStore;
  private readonly clock: () => number;
  constructor(store: MemoryStore, clock: () => number) {
    this.store = store;
    this.clock = clock;
  }
  async get(clientId: number, familyId: string): Promise<Preference | null> {
    const found = this.rows.find((p) => p.clientId === clientId && p.familyId === familyId);
    return found ? { ...found } : null;
  }
  async forClient(clientId: number): Promise<Preference[]> {
    return this.rows.filter((p) => p.clientId === clientId).sort((a, b) => (a.familyId < b.familyId ? -1 : a.familyId > b.familyId ? 1 : 0)).map((p) => ({ ...p }));
  }
  async set(clientId: number, familyId: string, staffId: string, opts: { requestKey?: string | null; expected?: Expected | null } = {}): Promise<PreferenceOutcome> {
    return this.store.serialised(async () => {
      if (!this.store.hasClient(clientId)) throw new StoreError("that client does not exist, so nothing was saved");
      const key = opts.requestKey ?? null;
      if (key && this.requests.has(`set|${key}`)) return { status: REPLAYED, preference: await this.get(clientId, familyId), previous: null };
      const current = this.rows.find((p) => p.clientId === clientId && p.familyId === familyId);
      const now = this.clock();
      let result: PreferenceOutcome;
      const matches = opts.expected === undefined || (opts.expected === null ? !current : Boolean(current) && current!.staffId === opts.expected.staffId && current!.version === opts.expected.version);
      if (current && current.staffId === staffId) result = { status: UNCHANGED, preference: { ...current }, previous: null };
      else if (!matches) result = { status: STALE, preference: current ? { ...current } : null, previous: null };
      else if (!current) {
        const row: Preference = { clientId, familyId, staffId, version: 1, createdAt: now, updatedAt: now };
        this.rows.push(row);
        result = { status: SAVED, preference: { ...row }, previous: null };
      } else {
        const before = current.staffId;
        Object.assign(current, { staffId, version: current.version + 1, updatedAt: now });
        result = { status: SAVED, preference: { ...current }, previous: before };
      }
      if (key) this.requests.set(`set|${key}`, "set");
      return result;
    });
  }
  async remove(clientId: number, familyId: string, opts: { requestKey?: string | null; expected: Expected | null }): Promise<PreferenceOutcome> {
    if (opts?.expected === undefined) throw new StoreError("a removal must name the preference it removes");
    return this.store.serialised(async () => {
      const key = opts.requestKey ?? null;
      if (key && this.requests.has(`remove|${key}`)) return { status: REPLAYED, preference: null, previous: null };
      const index = this.rows.findIndex((p) => p.clientId === clientId && p.familyId === familyId);
      let result: PreferenceOutcome;
      if (index < 0) result = { status: NOTHING, preference: null, previous: null };
      else if (opts.expected === null || this.rows[index].staffId !== opts.expected.staffId || this.rows[index].version !== opts.expected.version) result = { status: STALE, preference: { ...this.rows[index] }, previous: null };
      else {
        const [gone] = this.rows.splice(index, 1);
        result = { status: REMOVED, preference: null, previous: gone.staffId };
      }
      if (key) this.requests.set(`remove|${key}`, "remove");
      return result;
    });
  }
}
