/* The appointment store the tools are written against. Port of the AppointmentStore / PreferenceStore surface of aria_booking/appointments/store.py and preferences.py.
 *
 * Two implementations satisfy it and are held to the SAME contract tests (test/contract): MemoryStore (this folder; a model of the Python store, serialised like its BEGIN IMMEDIATE) and
 * PgStore (Postgres; conflicts are refused by exclusion constraints, so the database, not the application, is the last arbiter). Every writer is one atomic step: it either does all it says
 * or nothing, and answers with an Outcome; a refusal is an Outcome, never a half-written state. */

import { createHash, randomInt } from "node:crypto";
import type { ClientRecord, NewBooking, Outcome, OutboxMessage, Placement, StoredAppointment } from "./appointments.ts";
import type { RowsBetween } from "./judge.ts";

export const REFERENCE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"; // no 0 / 1 / I / L / O: a reference read aloud or copied by hand is not misheard
export const REFERENCE_LENGTH = 6;
export const MAX_REASON_LENGTH = 200;

/** The store could not do what was asked (unreachable, unreadable). Nothing was changed. */
export class StoreError extends Error {}

/** Runs inside the write step; throws `Refused` to abandon it. It reads through the step's own reader, so "free" is decided against exactly the state the write will join. */
export type Validator = (rowsBetween: RowsBetween, placement: Placement) => Promise<void>;
export type OutboxBuilder = (saved: StoredAppointment, previous: StoredAppointment | null) => OutboxMessage | null;

export interface Preference {
  clientId: number;
  familyId: string;
  staffId: string;
  version: number; // counts the changes to this preference; starts again at 1 if it is recreated
  generation: number; // unique to every write, never reused, so a confirmation can tell 'the same technician, saved again later' from the state it was read back at
  createdAt: number;
  updatedAt: number;
}
export const SAVED = "saved", UNCHANGED = "unchanged", REMOVED = "removed", NOTHING = "nothing_to_remove", REPLAYED = "replayed", STALE = "stale";
/** The saved preference a caller confirmed (its technician and generation), or null when none was saved. A change only applies to exactly that state; otherwise the outcome is STALE and nothing changes. */
export type Expected = { staffId: string; generation: number };
export interface PreferenceOutcome {
  status: string; // saved / unchanged / removed / nothing_to_remove / replayed / stale (what is saved is no longer what the caller confirmed: nothing changed)
  preference: Preference | null; // the state AFTER the call (null once removed)
  previous: string | null; // the staff id it held before, for a change
}

export interface PreferenceStore {
  get(clientId: number, familyId: string): Promise<Preference | null>;
  forClient(clientId: number): Promise<Preference[]>;
  /** One atomic step: the request key and the change commit together, so of any number of requests with one key exactly one has an effect and the rest replay. With `expected` the change
   * only applies to that exact saved state (null: nothing saved), checked at the write, not on an earlier read; otherwise it answers STALE and changes nothing. */
  set(clientId: number, familyId: string, staffId: string, opts?: { requestKey?: string | null; expected?: Expected | null }): Promise<PreferenceOutcome>;
  /** As set, but a removal must always say WHICH saved preference it removes (`expected`), so a retry can never remove a newer choice. */
  remove(clientId: number, familyId: string, opts: { requestKey?: string | null; expected: Expected | null }): Promise<PreferenceOutcome>;
}

export interface EventRow {
  kind: string;
  detail: Record<string, unknown>;
  at: number;
}
export interface OutboxRow {
  outboxId: number;
  dedupeKey: string;
  kind: string;
  reference: string;
  body: string;
  status: string;
  attempts: number;
  lastError: string | null;
  createdAt: number;
}

export interface Store {
  readonly preferences: PreferenceStore;
  // ---- reads
  get(reference: string): Promise<StoredAppointment | null>;
  /** Appointments (in the given statuses) whose SERVICE time overlaps [start, end), oldest first. */
  between(startUtc: number, endUtc: number, opts?: { staffId?: string | null; statuses?: readonly string[] }): Promise<StoredAppointment[]>;
  /** Confirmed appointments of one technician whose OCCUPIED time (the service plus its cleanup before and after) overlaps [start, end), oldest first. */
  occupiedBetween(startUtc: number, endUtc: number, opts: { staffId: string }): Promise<StoredAppointment[]>;
  forClient(clientId: number, statuses?: readonly string[]): Promise<StoredAppointment[]>;
  clientsWithPhone(phone: string): Promise<ClientRecord[]>;
  client(clientId: number): Promise<ClientRecord | null>;
  /** The reference a successful request of this kind was saved against, or null: whether this very request has already happened. */
  requestReference(requestKey: string, kind: string): Promise<string | null>;
  seededAfter(prefix: string, afterUtc: number): Promise<boolean>;
  /** Totals only: safe to log. */
  counts(): Promise<{ appointments: Record<string, number>; clients: number; outbox: Record<string, number> }>;
  /** Differs whenever a booking, cancellation, move or seed has changed the data. Counts and ids only: no names, no phone numbers. */
  changeStamp(): Promise<string>;
  outboxRows(status?: string | null): Promise<OutboxRow[]>;
  events(reference: string): Promise<EventRow[]>;
  // ---- the writers
  book(booking: NewBooking, opts?: { validate?: Validator | null; outbox?: OutboxBuilder | null }): Promise<Outcome>;
  /** Seed or test only: a PAST visit with a final status. It is never validated against availability and never notifies anyone. */
  addHistory(booking: NewBooking, status: string): Promise<Outcome>;
  cancel(reference: string, opts?: { expectedVersion?: number | null; reason?: string | null; requestKey?: string | null; outbox?: OutboxBuilder | null }): Promise<Outcome>;
  reschedule(
    reference: string,
    opts: { startUtc: number; staffId?: string | null; expectedVersion?: number | null; validate?: Validator | null; requestKey?: string | null; outbox?: OutboxBuilder | null },
  ): Promise<Outcome>;
  /** For the FICTIONAL SEED FIXTURES only: an upcoming seed whose time has passed becomes a completed seed visit. A caller's appointment is never settled. Never notifies. */
  settlePastSeeds(): Promise<number>;
}

/** The stable reference derived from a seed key (the same seed, the same reference), or a random one. `attempt` re-draws after a collision. */
export function makeReference(seedKey: string | null | undefined, attempt: number, random: () => number = () => randomInt(REFERENCE_ALPHABET.length)): string {
  if (seedKey) {
    const digest = createHash("sha256").update(`${seedKey}|${attempt}`, "utf8").digest();
    return Array.from(digest.subarray(0, REFERENCE_LENGTH), (b) => REFERENCE_ALPHABET[b % REFERENCE_ALPHABET.length]).join("");
  }
  return Array.from({ length: REFERENCE_LENGTH }, () => REFERENCE_ALPHABET[random()]).join("");
}

/** A caller's reason for cancelling, as one printable line of at most 200 characters, or null. */
export function cleanReason(reason: unknown): string | null {
  if (typeof reason !== "string") return null;
  const printable = Array.from(reason).filter((ch) => !/[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]/u.test(ch) && !(/\p{Z}/u.test(ch) && ch !== " ")).join("");
  const cleaned = printable.split(/\s+/).filter(Boolean).join(" ").slice(0, MAX_REASON_LENGTH);
  return cleaned || null;
}
