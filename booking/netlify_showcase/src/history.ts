/* "When was my last <kind> appointment?": the date of the most recent COMPLETED visit in the requested category, the service, and the technician only if asked. Port of aria_booking/history/
 * {models,classify,lookup}.py. It never says a phone number, a name, a record id, how many records matched, anything about another client, notes, medical or payment data, and it never guesses:
 * a missing, ambiguous, unreadable, incomplete or uncertain case becomes "I can't confirm" (or a request for the one thing that would settle it). */

import { Catalog, slug } from "./catalog.ts";
import { casefold } from "./appointments.ts";
import { normalizePhone } from "./phone.ts";
import { spokenDay } from "./speech.ts";
import { dateOf, isoDate } from "./time.ts";
import type { Instant } from "./time.ts";
import type { CallStore } from "./callState.ts";

// What a visit's state can be. Only COMPLETED counts as a visit that happened. A past date alone proves nothing.
export const H_COMPLETED = "completed", H_CANCELLED = "cancelled", H_NO_SHOW = "no_show", H_UPCOMING = "upcoming", H_UNKNOWN = "unknown";

export interface HistoryVisit {
  start: Instant | null; // null = the date could not be read
  services: string[]; // service names as the source shows them
  status: string; // anything the source cannot classify must be UNKNOWN, never COMPLETED
  technician: string | null;
}
export interface ClientRef {
  ref: string; // opaque handle for the source
  name: string | null; // many records have none; used ONLY to tell apart records that share a number
}
export interface ClientHistory {
  visits: HistoryVisit[];
  completionKnown: boolean;
  complete: boolean;
  newestFirst: boolean;
}
export interface HistorySource {
  findClients(normalizedPhone: string): Promise<ClientRef[]>;
  readHistory(client: ClientRef): Promise<ClientHistory>;
}
export class HistoryUnavailable extends Error {}

// Words that name a category in speech or in a service title. Deliberately explicit; anything not covered is "not understood", never guessed.
const ALIASES: Record<string, string[]> = {
  lash: ["lash", "lashes", "eyelash", "eyelashes"],
  massage: ["massage", "massages"],
  facial: ["facial", "facials"],
  waxing: ["wax", "waxing"],
  "permanent-makeup": ["microblading", "permanent makeup", "lip blush", "ombre powder"],
  "head-spa": ["head spa", "scalp"],
  "body-scrub": ["scrub", "body polish"],
  couples: ["couples", "couple"],
};
const norm = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, " ").split(/\s+/).filter(Boolean).join(" ");
const has = (phrase: string, text: string) => ` ${text} `.includes(` ${phrase} `);

export class Classifier {
  private readonly families: Array<[string, string]>;
  private readonly categories: Set<string>;
  constructor(catalog: Catalog) {
    this.families = catalog.items.map((i): [string, string] => [norm(i.family), i.category]).sort((a, b) => b[0].length - a[0].length); // stable: ties keep menu order
    this.categories = new Set(catalog.items.map((i) => i.category));
  }
  /** Every category a service title belongs to. Empty = not understood (the caller must treat that as uncertain). */
  categoriesOf(serviceText: string | null | undefined): Set<string> {
    const text = norm(serviceText ?? "");
    const found = new Set<string>();
    if (!text) return found;
    for (const [family, category] of this.families) if (has(family, text)) found.add(category);
    for (const [category, words] of Object.entries(ALIASES)) if (words.some((w) => has(norm(w), text))) found.add(category);
    return found;
  }
  /** The ONE category the caller asked about, or null when it is missing, ambiguous or not on the menu. */
  categoryOfRequest(words: string | null | undefined): string | null {
    const text = norm(words ?? "");
    if (!text) return null;
    const hits = Object.entries(ALIASES).filter(([, aliases]) => aliases.some((a) => has(norm(a), text))).map(([c]) => c);
    if (hits.length > 1) return null;
    if (hits.length) return hits[0];
    const direct = [...this.categories].filter((c) => norm(c) === text);
    if (direct.length === 1) return direct[0];
    const named = this.categoriesOf(words);
    return named.size === 1 ? [...named][0] : null;
  }
}

export const MAX_ATTEMPTS_PER_CALL = 3; // a caller cannot try number after number to discover who is a client
export const CONTACT = "Please contact the salon directly.";

const reply = (status: string, speak: string, data: Record<string, unknown> = {}) => ({ status, ok: status === "found", speak, ...data });
const unable = (reason: string, speak: string) => reply("unable_to_confirm", speak, { reason });
const normName = (value: string | null | undefined) => casefold(value ?? "").split(/\s+/).filter(Boolean).join(" ");

/** Exactly one record, or a response that asks for the one thing that would settle it. Never lists or counts records. */
export function chooseClient(clients: ClientRef[], name: string | null): ClientRef | Record<string, any> {
  if (!clients.length) return reply("not_found", "I can't find an appointment history for that number. " + CONTACT, { reason: "no_match" });
  if (clients.length === 1) return clients[0];
  const wanted = normName(name);
  if (!wanted) return reply("needs_clarification", "I can't settle on a single record from the number alone. Could you tell me the full name on the booking?", { reason: "name_needed" });
  const named = clients.filter((c) => normName(c.name) === wanted); // a record with no name can never match
  if (named.length === 1) return named[0];
  return unable("record_ambiguous", "I can't confirm a single record from that. " + CONTACT);
}
export const isReply = (value: unknown): value is Record<string, any> => typeof value === "object" && value !== null && "status" in value;

export class HistoryLookup {
  private readonly source: HistorySource;
  private readonly clock: () => Instant;
  private readonly classifier: Classifier;
  private readonly calls: CallStore;
  private readonly maxAttempts: number;

  constructor(source: HistorySource, clock: () => Instant, calls: CallStore, classifier: Classifier, maxAttemptsPerCall = MAX_ATTEMPTS_PER_CALL) {
    this.source = source;
    this.clock = clock;
    this.calls = calls;
    this.classifier = classifier;
    this.maxAttempts = maxAttemptsPerCall;
  }

  /** `identify(phone, args)` -> (args to use, a reply to give instead) runs AFTER the attempt is counted, so no identity question is ever answered past the limit. */
  async lastCompletedVisit(callId: string, args: Record<string, any>, identify?: (phone: string, args: Record<string, any>) => Promise<[Record<string, any>, Record<string, any> | null]>): Promise<Record<string, any>> {
    args = args ?? {};
    if (typeof callId !== "string" || !callId.trim()) return reply("needs_clarification", "I could not identify this call, so I can't look anything up.", { reason: "no_call_id" });
    const category = this.classifier.categoryOfRequest(typeof args.service_category === "string" ? args.service_category : null);
    if (category === null) return reply("needs_clarification", "Which kind of appointment do you mean, for example lashes, a massage or a facial?", { reason: "service_category" });
    const rawPhone = args.phone;
    if (rawPhone === null || rawPhone === undefined || (typeof rawPhone === "string" && !rawPhone.trim())) {
      return reply("needs_clarification", "Could you give me the complete phone number on your account, including the area code?", { reason: "phone_required" });
    }
    const phone = normalizePhone(rawPhone);
    if (phone === null) {
      return reply("invalid_phone", "That doesn't sound like a complete phone number. Could you give me the whole number, including the area code?", { reason: "phone_incomplete" });
    }
    const doc = await this.calls.load(callId);
    if (doc.attempts >= this.maxAttempts) return reply("refused", `I can't look up any more numbers on this call. ${CONTACT}`, { reason: "too_many_attempts" });
    doc.attempts += 1;
    await this.calls.save(callId, doc);
    if (identify) {
      const [used, instead] = await identify(phone, args);
      if (instead !== null) return instead;
      args = used;
    }
    let clients: ClientRef[];
    try {
      clients = await this.source.findClients(phone);
    } catch (error) {
      if (error instanceof HistoryUnavailable) return unable("source_unavailable", "I can't reach the appointment records right now, so I can't confirm that. " + CONTACT);
      return unable("source_error", "I couldn't read the appointment records, so I can't confirm that. " + CONTACT);
    }
    const client = chooseClient(clients, typeof args.name === "string" ? args.name : null);
    if (isReply(client)) return client;
    let history: ClientHistory;
    try {
      history = await this.source.readHistory(client);
    } catch (error) {
      if (error instanceof HistoryUnavailable) return unable("source_unavailable", "I can't reach the appointment records right now, so I can't confirm that. " + CONTACT);
      return unable("history_unreadable", "I couldn't read that appointment history clearly, so I can't confirm it. " + CONTACT);
    }
    return this.answer(history, category, args.include_technician === true);
  }

  private answer(history: ClientHistory, category: string, wantsTechnician: boolean): Record<string, any> {
    if (!history.completionKnown) {
      return unable("completion_unavailable", "I can see past appointments but not whether they were completed, so I can't confirm your last one. " + CONTACT);
    }
    const now = this.clock();
    let best: [HistoryVisit, string] | null = null;
    let newestUncertain: Instant | null = null;
    let uncertainWithoutDate = false;
    for (const visit of history.visits) {
      if ([H_CANCELLED, H_NO_SHOW, H_UPCOMING].includes(visit.status)) continue; // these did not happen (yet), whatever the date says
      const kinds = visit.services.map((s) => this.classifier.categoriesOf(s));
      const matching = visit.services.filter((_s, i) => kinds[i].has(category));
      const unplaced = !visit.services.length || kinds.some((k) => k.size === 0);
      if (!matching.length && !unplaced) continue; // a different kind of service
      const happened = visit.status === H_COMPLETED && visit.start !== null && visit.start < now;
      if (happened && matching.length) {
        if (best === null || visit.start! > best[0].start!) best = [visit, matching[0]];
        continue;
      }
      // Relevant, but we cannot be sure whether it counts: it blocks any older answer.
      if (visit.start === null) uncertainWithoutDate = true;
      else if (newestUncertain === null || visit.start > newestUncertain) newestUncertain = visit.start;
    }
    if (uncertainWithoutDate || (newestUncertain !== null && (best === null || newestUncertain > best[0].start!))) {
      return unable("status_uncertain", "I can't tell for sure which of your appointments was the last one, so I can't confirm it. " + CONTACT);
    }
    if (!history.complete && !(best !== null && history.newestFirst)) return unable("history_incomplete", "I couldn't see all of your appointment history, so I can't confirm your last one. " + CONTACT);
    const label = category.replaceAll("-", " ");
    if (best === null) return reply("no_completed_visit", `I don't see a completed ${label} appointment in your history.`, { reason: "none_in_history" });
    const [visit, service] = best;
    const who = wantsTechnician && visit.technician ? ` with ${visit.technician}` : "";
    const data: Record<string, unknown> = { date: isoDate(dateOf(visit.start!)), service };
    if (wantsTechnician && visit.technician) data.technician = visit.technician;
    return reply("found", `Your last completed ${label} appointment was ${service} on ${spokenDay(visit.start!)}${who}.`, data);
  }
}

export { slug };
