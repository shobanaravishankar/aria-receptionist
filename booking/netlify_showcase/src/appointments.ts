/* Plain data types for the simulated appointment store. No I/O. Port of aria_booking/appointments/models.py.
 *
 * Stored times are epoch SECONDS (`startUtc`, `createdAt`, ...) exactly as in the Python store and its SQLite file, so a snapshot exported from the local demo can be imported unchanged.
 * Everywhere else in this port an instant is epoch milliseconds (see time.ts); `instantOf` and `epochOf` convert at the edge. */

import type { Instant } from "./time.ts";

export const CONFIRMED = "confirmed", CANCELLED = "cancelled", COMPLETED = "completed", NO_SHOW = "no_show";
export const STATUSES = [CONFIRMED, CANCELLED, COMPLETED, NO_SHOW] as const;
export type Status = (typeof STATUSES)[number];

// Where a record came from. Only "seed" rows exist before anyone books anything; "voice" rows are made by a confirmed tool call.
export const ORIGIN_SEED = "seed", ORIGIN_VOICE = "voice", ORIGIN_TEST = "test";

export const instantOf = (epochSeconds: number): Instant => epochSeconds * 1000;
export const epochOf = (t: Instant): number => Math.trunc(t / 1000);

/** Python's str.casefold() for the characters that matter in names (full Unicode case folding is not needed for the demo's names). */
export function casefold(text: string): string {
  return text.toLowerCase().replace(/ß|ẞ/g, "ss").replace(/ς/g, "σ").replace(/ſ/g, "s");
}

/** How two spellings of one name compare: case and runs of whitespace do not matter, nothing else is guessed. */
export function nameKey(name: string | null | undefined): string {
  return casefold(name ?? "").split(/\s+/).filter(Boolean).join(" ");
}

export interface ClientRecord {
  clientId: number;
  name: string;
  phone: string; // normalised "+<digits>": identifies a DEMO record, not a person (the demo's policy, not production authentication)
}

export interface StoredAppointment {
  apptId: number;
  reference: string; // the stable, spoken-friendly handle; it never changes, not even when the appointment is moved
  clientId: number;
  clientName: string;
  serviceId: string;
  serviceName: string;
  category: string;
  staffId: string;
  startUtc: number;
  durationMinutes: number;
  bufferBeforeMinutes: number;
  bufferAfterMinutes: number;
  status: string;
  version: number; // +1 on every change; a confirmation made against an older version is stale
  origin: string;
  createdAt: number;
  updatedAt: number;
  cancelledAt: number | null;
  cancelReason: string | null;
  seedKey: string | null;
}

export const endUtc = (a: { startUtc: number; durationMinutes: number }): number => a.startUtc + a.durationMinutes * 60;
export const blockedStartUtc = (a: { startUtc: number; bufferBeforeMinutes: number }): number => a.startUtc - a.bufferBeforeMinutes * 60;
export const blockedEndUtc = (a: { startUtc: number; durationMinutes: number; bufferAfterMinutes: number }): number => endUtc(a) + a.bufferAfterMinutes * 60;

/** What a confirmed booking saves. The service facts come from the demo's registry, never from the caller. */
export interface NewBooking {
  clientName: string;
  phone: string; // already normalised
  serviceId: string;
  serviceName: string;
  category: string;
  staffId: string;
  startUtc: number;
  durationMinutes: number;
  bufferBeforeMinutes?: number;
  bufferAfterMinutes?: number;
  origin?: string;
  seedKey?: string | null;
  requestKey?: string | null; // a repeat of the same request returns the first result and saves nothing more
}

/** One owner notification, written with the change it reports and (if ever) sent only after that commits. This showcase never sends one: the rows are kept for the owner's view. */
export interface OutboxMessage {
  kind: string; // booked / cancelled / rescheduled
  body: string;
}

export interface Outcome {
  status: string;
  appointment: StoredAppointment | null;
  previous: StoredAppointment | null; // a reschedule: how it was before
  reason: string;
  detail: Record<string, unknown>;
}
export const outcome = (status: string, appointment: StoredAppointment | null = null, extra: Partial<Outcome> = {}): Outcome => ({
  status, appointment, previous: null, reason: "", detail: {}, ...extra,
});

// Outcomes that mean "the stored state is now what was asked for" (including a repeat that changed nothing).
export const SUCCESS: ReadonlySet<string> = new Set(["booked", "replayed", "cancelled", "already_cancelled", "rescheduled", "unchanged"]);
export const isOk = (o: Outcome): boolean => SUCCESS.has(o.status);

/** Thrown inside a transaction to abandon it. Nothing the transaction did is kept; the caller gets `status` and `reason`. */
export class Refused extends Error {
  readonly status: string;
  readonly reason: string;
  readonly appointment: StoredAppointment | null;
  readonly detail: Record<string, any>;
  constructor(status: string, reason = "", appointment: StoredAppointment | null = null, detail: Record<string, any> = {}) {
    super(`${status}: ${reason}`);
    this.status = status;
    this.reason = reason;
    this.appointment = appointment;
    this.detail = detail;
  }
}

/** Where an appointment is being put: what a validator is asked to judge. */
export interface Placement {
  staffId: string;
  startUtc: number;
  durationMinutes: number;
  bufferBeforeMinutes: number;
  bufferAfterMinutes: number;
  serviceId: string;
  category: string;
  ignoreReference: string | null; // the appointment being moved: its own old place is not a conflict
}
export function placementOf(booking: NewBooking): Placement {
  return {
    staffId: booking.staffId, startUtc: booking.startUtc, durationMinutes: booking.durationMinutes, bufferBeforeMinutes: booking.bufferBeforeMinutes ?? 0,
    bufferAfterMinutes: booking.bufferAfterMinutes ?? 0, serviceId: booking.serviceId, category: booking.category, ignoreReference: null,
  };
}
