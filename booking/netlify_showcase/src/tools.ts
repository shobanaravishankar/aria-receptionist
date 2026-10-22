/* The functions a voice agent may call on the simulated lifecycle line: look a service up, check a slot, find alternatives, find appointments, the last completed visit, book, cancel and
 * reschedule. Port of aria_booking/voice/tools.py (the read tools) and aria_booking/appointments/tools.py (the lifecycle tools), INCLUDING the rehearsal corrections R-1 and R-2.
 * The three preference tools are in prefTools.ts.
 *
 * Rules enforced here, not left to the prompt:
 *  - Nothing is saved, cancelled or moved without a read-back that the caller confirms; the read-back issues a token bound to this call and to exactly what was read. The save re-checks
 *    everything inside the store's atomic write.
 *  - A retried or repeated confirmed request is answered from the first result and changes nothing (idempotency key = call + token).
 *  - `ok` is true only for a result that was re-read from the store and the calendar after the save.
 *  - A phone number identifies a DEMO record; it is not production authentication. It is never spoken back, logged or returned.
 *  - Only ONE appointment is ever touched: cancellation and moves go by the appointment's own reference AND the client's complete number.
 *  - No cancellation fee, penalty or policy is ever stated: the tools hold none, so none is spoken.
 * Everything a call remembers (tokens, numbers tried, failed matches, changes, who the call settled on, the time last asked for) lives in the CallStore, not in this object. */

import { createHash, randomBytes } from "node:crypto";

import { casefold, CANCELLED, CONFIRMED, epochOf, instantOf, nameKey, Refused } from "./appointments.ts";
import type { NewBooking, Outcome, Placement, StoredAppointment } from "./appointments.ts";
import { findSlots, validateSlot } from "./availability.ts";
import { isEligible, staffKey } from "./bookable.ts";
import type { BookableService } from "./bookable.ts";
import { CallConflict } from "./callState.ts";
import type { CallDoc, CallStore, Pending } from "./callState.ts";
import { descriptiveLabel, durationHint, SHOWCASE_CATALOG, toPublic, treatmentWords } from "./catalog.ts";
import type { Catalog, CatalogItem } from "./catalog.ts";
import { NotCovered } from "./dataset.ts";
import type { DemoDataset, DemoStaff } from "./dataset.ts";
import { dayBounds, snapshotFromPlan } from "./dayplan.ts";
import { Classifier, CONTACT, chooseClient, HistoryLookup, HistoryUnavailable, H_CANCELLED, H_COMPLETED, H_NO_SHOW, H_UNKNOWN, H_UPCOMING, isReply } from "./history.ts";
import type { ClientHistory, ClientRef, HistorySource, HistoryVisit } from "./history.ts";
import { makeGroupAnswer } from "./discovery.ts";
import type { PlacementJudge } from "./judge.ts";
import { forStaff } from "./models.ts";
import type { DaySnapshot, ServiceSpec, Slot } from "./models.ts";
import { outboxBuilder } from "./notify.ts";
import { normalizePhone } from "./phone.ts";
import { joinChoices, joinNames, spokenDay, spokenDuration, spokenPrice, spokenSlot, spokenTime } from "./speech.ts";
import { StoreError } from "./store.ts";
import type { Store } from "./store.ts";
import { addDays, addMinutes, compareDates, dateOf, fromLocal, hhmm, isoDate, isoformat, localParts, parseIsoDate } from "./time.ts";
import type { DateParts, Instant } from "./time.ts";

type Args = Record<string, any>;
type Reply = Record<string, any>;

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const TIME_RE = /^\d{1,2}:\d{2}$/;
const SUCCESS_STATUSES = new Set(["booked_verified", "already_booked"]);
export const LIFECYCLE_OK: ReadonlySet<string> = new Set(["booked_verified", "already_booked", "cancelled", "already_cancelled", "rescheduled", "already_rescheduled", "unchanged", "found"]);
export const MAX_NUMBERS_PER_CALL = 3; // a caller cannot try number after number to find who is a client
export const MAX_FAILED_MATCHES_PER_CALL = 5;
const MAX_CALL_REDOS = 12; // how many times a step may be redone because another invocation of the same call saved first
export const MAX_CHANGES_PER_CALL = 3;
export const NAME_MAX = 80;
export const SHOWN_UPCOMING = 3;
const CATEGORY_WORDS: Record<string, string> = { waxing: "waxing", lash: "lash", "body-scrub": "body scrub", couples: "couples" }; // added to a service name only when the name does not say it
export const GENERIC_CONTACT = "contact the salon directly"; // what the reviewed tools say; replaced by the configured name and number when there is one
const REASON_RANK: Record<string, number> = { not_working: 0, outside_hours: 1, on_break: 2, ends_after_closing: 3, too_soon: 4, occupied: 5, conflicts_next: 6 };
// Same-day start-time bounds a caller can ask for: the argument and how it is said. A bound is a RULE, not a hint.
export const WINDOW_ARGS: Record<string, [side: "lo" | "hi", shift: number, wording: (t: string) => string]> = {
  starting_at_or_after: ["lo", 0, (t) => `at ${t} or later`], starting_after: ["lo", 1, (t) => `later than ${t}`],
  starting_at_or_before: ["hi", 0, (t) => `at ${t} or earlier`], starting_before: ["hi", -1, (t) => `earlier than ${t}`],
};

class DriverError extends Error {}

const response = (status: string, speak: string, data: Reply = {}): Reply => ({ status, ok: SUCCESS_STATUSES.has(status), speak, ...data });
const reply = (status: string, speak: string, data: Reply = {}): Reply => ({ status, ok: LIFECYCLE_OK.has(status), speak, ...data });

function editDistance(a: string, b: string): number {
  let previous = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const current = [i];
    for (let j = 1; j <= b.length; j++) current.push(Math.min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a[i - 1] !== b[j - 1] ? 1 : 0)));
    previous = current;
  }
  return previous[b.length];
}
/** Team names a mis-heard name could be (one letter off for short names, two for longer ones), nearest first, at most two. Used only to ASK 'did you mean ...?'. */
export function closeNames(heard: string, team: string[]): string[] {
  const wanted = staffKey(heard);
  const scored: Array<[number, number, string]> = [];
  for (const name of team) {
    const key = staffKey(name);
    const distance = editDistance(wanted, key);
    if (distance <= (Math.min(wanted.length, key.length) <= 5 ? 1 : 2)) scored.push([distance, scored.length, name]);
  }
  return scored.sort((a, b) => a[0] - b[0] || a[1] - b[1]).slice(0, 2).map((s) => s[2]);
}

export interface ToolOptions {
  dataset: DemoDataset;
  judge: PlacementJudge;
  store: Store;
  calls: CallStore;
  clock: () => Instant;
  catalog?: Catalog;
  contact?: string | null;
  newToken?: () => string;
  gridMinutes?: number;
  minLeadMinutes?: number;
  confirmTtlSeconds?: number;
}

class StoreHistorySource implements HistorySource {
  private readonly store: Store;
  private readonly clock: () => Instant;
  private readonly staffName: (id: string) => string;
  constructor(store: Store, clock: () => Instant, staffName: (id: string) => string) {
    this.store = store;
    this.clock = clock;
    this.staffName = staffName;
  }
  async findClients(phone: string): Promise<ClientRef[]> {
    try {
      return (await this.store.clientsWithPhone(phone)).map((c) => ({ ref: String(c.clientId), name: c.name }));
    } catch (error) {
      if (error instanceof StoreError) throw new HistoryUnavailable();
      throw error;
    }
  }
  async readHistory(client: ClientRef): Promise<ClientHistory> {
    let rows: StoredAppointment[];
    try {
      rows = await this.store.forClient(Number(client.ref));
    } catch (error) {
      if (error instanceof StoreError) throw new HistoryUnavailable();
      throw error;
    }
    const now = epochOf(this.clock());
    const statusOf: Record<string, string> = { completed: H_COMPLETED, cancelled: H_CANCELLED, no_show: H_NO_SHOW };
    const visits: HistoryVisit[] = rows.map((row) => ({
      start: instantOf(row.startUtc), services: [row.serviceName], technician: this.staffName(row.staffId),
      status: row.status === CONFIRMED ? (row.startUtc > now ? H_UPCOMING : H_UNKNOWN) : (statusOf[row.status] ?? H_UNKNOWN), // in the past, never marked done: unknown, which blocks an older answer
    }));
    return { visits, completionKnown: true, complete: true, newestFirst: false };
  }
}

export class LifecycleTools {
  readonly dataset: DemoDataset;
  protected readonly judge: PlacementJudge;
  protected readonly store: Store;
  protected readonly calls: CallStore;
  protected readonly clock: () => Instant;
  readonly catalog: Catalog;
  protected readonly contact: string | null;
  protected readonly newToken: () => string;
  protected readonly grid: number;
  protected readonly minLead: number;
  protected readonly confirmTtl: number; // ms
  protected readonly searchDays = 4;
  protected readonly checkDays = 1;
  protected readonly maxOptions = 3;
  protected readonly horizonDays = 60;
  protected readonly groupAnswer: (query: string) => Reply | null;
  protected readonly classifier: Classifier;
  protected readonly history: HistoryLookup;
  protected readonly outbox;
  static readonly SOURCE = "demo_calendar";

  constructor(opts: ToolOptions) {
    this.dataset = opts.dataset;
    this.judge = opts.judge;
    this.store = opts.store;
    this.calls = opts.calls;
    this.clock = opts.clock;
    this.catalog = opts.catalog ?? SHOWCASE_CATALOG;
    this.contact = opts.contact ?? null; // e.g. "contact Serenity Spa at 2 1 2, ...": null until the owner supplies a number
    this.newToken = opts.newToken ?? (() => randomBytes(6).toString("base64url"));
    this.grid = opts.gridMinutes ?? 15;
    this.minLead = opts.minLeadMinutes ?? 60;
    this.confirmTtl = (opts.confirmTtlSeconds ?? 300) * 1000;
    this.groupAnswer = makeGroupAnswer(this.catalog);
    this.classifier = new Classifier(this.catalog);
    this.history = new HistoryLookup(new StoreHistorySource(this.store, this.clock, (id) => this.staffName(id)), this.clock, this.calls, this.classifier);
    this.outbox = outboxBuilder((id) => this.staffName(id));
  }

  // ================================================================ plumbing
  private now(): Instant {
    return this.clock();
  }
  protected spec(service: BookableService): ServiceSpec {
    return { name: service.booksyName, durationMinutes: service.durationMinutes, bufferBeforeMinutes: service.bufferBeforeMinutes, bufferAfterMinutes: service.bufferAfterMinutes };
  }
  protected staffName(staffId: string): string {
    return this.dataset.roster.find((s) => s.staffId === staffId)?.name ?? "a technician";
  }
  protected price(service: BookableService): number | null {
    if (service.priceUsd !== null) return service.priceUsd;
    const item = service.catalogItem ? this.catalog.get(service.catalogItem) : null;
    return item ? item.priceUsd : null;
  }

  /** Runs one tool call: the call id is checked, what the call remembers is loaded first and saved after, and the answer is labelled with its source. */
  protected async run(callId: string, work: (doc: CallDoc) => Promise<Reply>): Promise<Reply> {
    if (typeof callId !== "string" || !callId.trim()) return this.labelled(response("needs_clarification", "I could not identify this call, so I can't check the calendar.", { reason: "no_call_id" }));
    // What the call remembers is saved by compare-and-set. If another invocation of THIS call saved first (parallel tool calls, on any instance), nothing is lost: this one loads the newer
    // memory and redoes its step against it. Redoing is safe because every write a step makes carries a request key, so it replays instead of repeating.
    for (let attempt = 1; ; attempt++) {
      const doc = await this.calls.load(callId);
      let result: Reply | undefined;
      let failure: unknown;
      let failed = false;
      try {
        result = this.branded(this.labelled(await work(doc)));
      } catch (error) {
        failed = true;
        failure = error;
      }
      try {
        await this.calls.save(callId, doc);
      } catch (error) {
        if (!(error instanceof CallConflict)) throw error;
        if (attempt >= MAX_CALL_REDOS) throw new StoreError("this call is busy; try again");
        await new Promise((resolve) => setTimeout(resolve, Math.floor(Math.random() * 15 * attempt)));
        continue;
      }
      if (failed) throw failure;
      return result!;
    }
  }
  private labelled(r: Reply): Reply {
    r.source = LifecycleTools.SOURCE;
    return r;
  }
  private branded(r: Reply): Reply {
    if (this.contact && typeof r.speak === "string") r.speak = r.speak.replaceAll(GENERIC_CONTACT, this.contact);
    return r;
  }
  protected contacting(text: string): string {
    return `${text} Please ${this.contact ?? GENERIC_CONTACT}.`;
  }

  /** The calendar as the page draws it for one day: the generated schedule with the stored appointments drawn in, as a snapshot, and the whole team. */
  private async readDay(day: DateParts): Promise<DaySnapshot> {
    try {
      const [low, high] = dayBounds(day);
      const rows = await this.store.between(low, high, { statuses: [CONFIRMED] });
      return snapshotFromPlan(this.dataset.dayPlan(day, rows), this.now());
    } catch (error) {
      if (error instanceof NotCovered) throw new DriverError(`no demo data for that date (${error.message})`);
      throw error;
    }
  }

  // ================================================================ choosing the service and technician
  protected select(args: Args): { service: BookableService; named: string | null } | { problem: Reply } {
    const raw = args.service_id;
    let service: BookableService | null;
    if (raw === null || raw === undefined || raw === "") {
      return { problem: response("needs_clarification", "Which service would you like?", { reason: "service_required" }) }; // the model must always name the service; it can never fall back to a default
    }
    service = this.dataset.registry.get(raw);
    if (service === null) {
      const item = this.catalog.get(raw);
      if (item !== null) return { problem: this.notBookable(item.name, item.itemId) };
      const resolution = typeof raw === "string" ? this.catalog.resolve(raw) : null;
      const candidates = (resolution ? resolution.items : []).slice(0, 5).map((i) => this.lite(i));
      return { problem: response("invalid_service", "I'm not sure which service you mean. Could you tell me the service you'd like?", { reason: "unknown_service_id", candidates }) };
    }
    if (!service.verified) return { problem: this.notBookable(service.booksyName, service.serviceId) };
    const words = this.checkServiceWords(service, args.service_words);
    if (words !== null) return { problem: words };
    const staff = args.staff;
    if (staff !== null && staff !== undefined && staff !== "" && typeof staff !== "string") return { problem: response("needs_clarification", "Which technician did you have in mind?", { reason: "bad_staff" }) };
    return { service, named: typeof staff === "string" ? staff.trim() || null : null };
  }

  /** The caller's OWN words for the treatment (`service_words`), when the agent passes them, must name the treatment it chose. A group, a length alone, several services that fit, or words that
   * name another treatment or length than the id are not a choice: the caller is asked, and nothing is checked or booked until they choose. Words that match nothing are not second-guessed. */
  private checkServiceWords(service: BookableService, words: unknown): Reply | null {
    if (typeof words !== "string" || !words.trim()) return null;
    const group = this.groupAnswer(words);
    if (group !== null) {
      const { status: _s, speak, ...data } = group;
      return response("needs_clarification", speak, { reason: "service_not_chosen", service_chosen: false, ...data });
    }
    const hint = durationHint(words);
    if (!treatmentWords(words).length) { // nothing but a length (or nothing at all): no treatment has been named, and the id must not stand in for one
      const ask = hint !== null ? `Which treatment would you like for ${spokenDuration(hint)}?` : "Which treatment would you like?";
      return response("needs_clarification", ask, {
        reason: "service_not_chosen", service_chosen: false, ...(hint !== null ? { requested_minutes: hint } : {}),
        instruction: "The caller has not named a treatment. Ask which one they want and wait; do not pick for them and do not check the calendar yet.",
      });
    }
    const resolution = this.catalog.resolve(words);
    const families = [...new Set(resolution.items.map((i) => i.family))];
    if (resolution.kind === "ambiguous") {
      return response("needs_clarification", `Several services fit that: ${joinChoices(families.slice(0, 8))}. Which one did you mean?`, {
        reason: "service_not_chosen", service_chosen: false, families: families.slice(0, 8),
        instruction: "The caller has not chosen one treatment. Ask which one they want and wait; do not pick for them and do not check the calendar yet.",
      });
    }
    const chosen = service.catalogItem ? this.catalog.get(service.catalogItem) : null;
    if ((resolution.kind === "exact" || resolution.kind === "variants") && chosen !== null) {
      const ids = new Set(resolution.items.map((i) => i.itemId));
      if (ids.has(chosen.itemId) && !(resolution.kind === "variants" && resolution.hintUnmatched)) return null; // the words name this very service
      if (resolution.hintUnmatched && families.includes(chosen.family)) { // the caller named a length this treatment does not come in
        const lengths = resolution.items.filter((i) => i.durationMinutes).map((i) => spokenDuration(i.durationMinutes!));
        return response("needs_clarification", `I don't have ${chosen.family} at ${spokenDuration(hint!)}. It comes as ${joinChoices(lengths)}. Which length would you like?`, {
          reason: "length_not_offered", service_chosen: false, lengths, requested_minutes: hint,
          instruction: "The length the caller asked for is not one this treatment comes in. Say so, ask which of the lengths they want, and wait; never use a different length without their answer.",
        });
      }
      const shown = resolution.kind === "variants" ? families.slice(0, 3) : resolution.items.slice(0, 3).map((i) => i.name);
      const label = resolution.kind === "variants" ? chosen.family : chosen.name;
      return response("needs_clarification", `Just to be sure I have the right treatment and length: did you mean ${joinChoices([...shown, label])}?`, {
        reason: "service_mismatch", service_chosen: false, families: families.slice(0, 3),
        instruction: "The service you chose is not the one (or not the length) the caller's words name. Ask which they want and wait; use only the one they choose.",
      });
    }
    return null;
  }

  private notBookable(name: string, serviceId: string): Reply {
    return response("not_bookable", `I can tell you about ${name}, but I can't book it online yet. Please contact the salon directly.`, { reason: "no_verified_booking_mapping", service_id: serviceId });
  }
  private lite(item: CatalogItem): Reply {
    const mapped = this.dataset.registry.forCatalogItem(item.itemId);
    const bookable = Boolean(mapped && mapped.verified);
    return {
      service_id: item.itemId, name: item.name, duration_minutes: item.durationMinutes, price_usd: item.priceUsd, ...(descriptiveLabel(item) ? { variant: descriptiveLabel(item) } : {}),
      bookable, ...(bookable ? { bookable_service_id: mapped!.serviceId } : {}),
    };
  }

  /** A name that matches nobody on the whole team: ask about it against the REAL roster. `team` is (name, staff id) for everyone. A sound-alike is only offered as a question, never used. */
  protected unknownStaffReply(named: string, service: BookableService, team: Array<[string, string]>): Reply {
    const whoDoes = team.filter(([name, id]) => isEligible(service, name, id)).map(([name]) => name);
    const close = closeNames(named, team.map(([name]) => name));
    const speak = close.length
      ? `I don't have a technician called ${named}. Did you mean ${joinChoices(close)}?`
      : `I don't have a technician called ${named}.${whoDoes.length ? ` For ${service.booksyName} it's ${joinNames(whoDoes)}.` : ""} Which did you mean?`;
    return response("needs_clarification", speak, {
      reason: "unknown_staff", candidates: whoDoes, closest: close, times_checked: false,
      instruction: "No technician has that name. Ask the caller which of the real technicians they meant. A name in `closest` may only be offered as a question ('Did you mean ...?'); use it " +
        "only after the caller says yes. Never invent a technician, never assume a name, and never say that anyone is free or available: no time has been checked. After the " +
        "caller chooses, call this tool again with that name.",
    });
  }
  /** Who works that day and does the service: CANDIDATES. No time has been checked for any of them, and the result says so in a field and in words. */
  private onSchedule(names: string[]): Reply {
    return {
      technicians_on_schedule: [...names], times_checked: false,
      instruction: "These technicians work that day and do this service, but NO time has been checked for any of them. Never say that any of them is free or available at the requested " +
        "time. Ask which one the caller would like, then call check_slot with that name (or call find_alternatives for real open times).",
    };
  }

  /** Technicians who can do this service on this day's snapshot, or a response explaining why none can be used. */
  private eligibleStaff(snapshot: DaySnapshot, service: BookableService, named: string | null): { staffs: string[]; problem: Reply | null } {
    const days = snapshot.staffDays;
    const keys = days.map((sd) => staffKey(sd.staff));
    if (new Set(keys).size !== keys.length) { // two columns with one display name cannot be attributed; never guess
      return { staffs: [], problem: response("unknown", "I couldn't tell the technicians' schedules apart clearly enough to check, so I won't guess.", { reason: "ambiguous_roster" }) };
    }
    const eligible = days.filter((sd) => isEligible(service, sd.staff, sd.staffId)).map((sd) => sd.staff);
    if (named) {
      const wanted = staffKey(named);
      const words = new Set(wanted.split(" ").filter(Boolean));
      const subset = (key: string) => words.size > 0 && [...words].every((w) => key.split(" ").includes(w));
      // The WHOLE team decides whether a name is ambiguous, not only who happens to be on today's schedule.
      const team = this.dataset.roster.map((s): [string, string] => [s.name, s.staffId]);
      let everyone = team.map(([n]) => n).filter((n) => staffKey(n) === wanted);
      if (!everyone.length) everyone = team.map(([n]) => n).filter((n) => subset(staffKey(n)));
      if (everyone.length > 1) {
        return { staffs: [], problem: response("needs_clarification", `I know more than one ${named}: ${joinChoices(everyone)}. Which did you mean?`, { reason: "ambiguous_staff", candidates: everyone }) };
      }
      if (!everyone.length) return { staffs: [], problem: this.unknownStaffReply(named, service, team) }; // nobody on the whole team goes by that name: ask, never treat the day's schedule as the answer
      let present = days.filter((sd) => staffKey(sd.staff) === wanted).map((sd) => sd.staff);
      if (!present.length) present = days.filter((sd) => subset(staffKey(sd.staff))).map((sd) => sd.staff);
      if (present.length > 1) {
        return { staffs: [], problem: response("needs_clarification", `I see more than one ${named} on the schedule: ${joinChoices(present)}. Which did you mean?`, { reason: "ambiguous_staff", candidates: present }) };
      }
      if (!present.length) {
        return { staffs: [], problem: response("staff_unavailable", `I don't see ${named} on the schedule for that day.`, { reason: "staff_not_on_schedule", ...this.onSchedule(eligible) }) };
      }
      const person = days.find((sd) => sd.staff === present[0])!;
      if (!isEligible(service, present[0], person.staffId)) {
        return { staffs: [], problem: response("staff_unavailable", `${present[0]} doesn't do ${service.booksyName}.`, { reason: "staff_not_eligible", ...this.onSchedule(eligible) }) };
      }
      return { staffs: present, problem: null };
    }
    if (!eligible.length) return { staffs: [], problem: response("staff_unavailable", `I can't find anyone on the schedule who does ${service.booksyName}.`, { reason: "no_eligible_staff" }) };
    return { staffs: eligible, problem: null };
  }

  // ================================================================ parsing the day and time
  protected parseWhen(args: Args, needTime: boolean): { day: DateParts; start: Instant | null } | { problem: Reply } {
    const rawDay = args.date;
    const rawTime = args.time;
    const missing = ([["date", rawDay], ["time", rawTime]] as const).filter(([name, value]) => !value && (name === "date" || needTime)).map(([name]) => name);
    if (missing.length) {
      const ask = missing.map((m) => (m === "date" ? "which day" : "what time")).join(" and ");
      return { problem: response("needs_clarification", `I need to know ${ask} you would like.`, { reason: "missing", missing }) };
    }
    if (typeof rawDay !== "string" || !DATE_RE.test(rawDay)) return { problem: response("needs_clarification", "Which date did you mean? Please give me the day.", { reason: "bad_date" }) };
    let day: DateParts;
    try {
      day = parseIsoDate(rawDay);
    } catch {
      return { problem: response("needs_clarification", "That date doesn't exist. Which date did you mean?", { reason: "bad_date" }) };
    }
    const today = dateOf(this.now());
    if (compareDates(day, today) < 0) return { problem: response("needs_clarification", "That date has already passed. Which day would you like instead?", { reason: "past_date" }) };
    if (compareDates(day, addDays(today, this.horizonDays)) > 0) return { problem: response("needs_clarification", "That's further ahead than I can check. Could you pick a nearer date?", { reason: "too_far" }) };
    let start: Instant | null = null;
    if (rawTime || needTime) {
      if (typeof rawTime !== "string" || !TIME_RE.test(rawTime)) return { problem: response("needs_clarification", "What time did you mean? For example, 2:30 in the afternoon.", { reason: "bad_time" }) };
      const [hour, minute] = rawTime.split(":").map(Number);
      if (hour > 23 || minute > 59) return { problem: response("needs_clarification", "That time doesn't look right. What time did you mean?", { reason: "bad_time" }) };
      if (minute % this.grid) {
        return {
          problem: response("needs_clarification",
            `We book on the quarter hour. Would a time such as ${spokenTime(fromLocal(day.year, day.month, day.day, hour, 0))} or a quarter past, half past or quarter to work?`, { reason: "off_grid" }),
        };
      }
      start = fromLocal(day.year, day.month, day.day, hour, minute);
    }
    return { day, start };
  }

  /** ((lowest, highest) start in minutes since midnight, either may be null; inclusive), the words for it, or a clarification. Several bounds on one side: the stricter wins. */
  private parseWindow(args: Args, day: DateParts): { window: [number | null, number | null] | null; said: string; problem: Reply | null } {
    let lo: number | null = null;
    let hi: number | null = null;
    const phrases: Record<string, string> = {};
    for (const [arg, [side, shift, wording]] of Object.entries(WINDOW_ARGS)) {
      const raw = args[arg];
      if (raw === null || raw === undefined || raw === "") continue;
      if (typeof raw !== "string" || !TIME_RE.test(raw)) return { window: null, said: "", problem: response("needs_clarification", "What time did you mean? For example, 2:30 in the afternoon.", { reason: "bad_time" }) };
      const [hour, minute] = raw.split(":").map(Number);
      if (hour > 23 || minute > 59) return { window: null, said: "", problem: response("needs_clarification", "That time doesn't look right. What time did you mean?", { reason: "bad_time" }) };
      const value = hour * 60 + minute + shift;
      const said = wording(spokenTime(fromLocal(day.year, day.month, day.day, hour, minute)));
      if (side === "lo" && (lo === null || value > lo)) { lo = value; phrases.lo = said; }
      if (side === "hi" && (hi === null || value < hi)) { hi = value; phrases.hi = said; }
    }
    if (lo === null && hi === null) return { window: null, said: "", problem: null };
    return { window: [lo, hi], said: ["lo", "hi"].filter((k) => k in phrases).map((k) => phrases[k]).join(" and "), problem: null };
  }

  // ================================================================ explaining a refusal
  private baseWhyNotOne(snapshot: DaySnapshot, start: Instant, service: BookableService, staff: string): [string, string, Reply] {
    const sd = forStaff(snapshot, staff)!;
    const spec = this.spec(service);
    const now = this.now();
    const length = spokenDuration(spec.durationMinutes);
    const windows = [...(sd.working ?? [])].sort((a, b) => a.start - b.start);
    if (!windows.length) return ["not_working", `We aren't taking appointments on ${spokenDay(start)}.`, {}];
    const firstOpen = windows[0].start;
    const lastClose = windows[windows.length - 1].end;
    const containing = windows.filter((w) => w.start <= start && start < w.end);
    if (!containing.length && firstOpen <= start && start < lastClose) {
      // inside the person's day but between two stretches of it: a break or time away, not "outside hours"
      const before = windows.filter((w) => w.end <= start);
      const after = windows.filter((w) => w.start > start);
      if (before.length && after.length) {
        const gapFrom = before[before.length - 1].end;
        const gapTo = after[0].start;
        return ["on_break", `${staff} is off between ${spokenTime(gapFrom)} and ${spokenTime(gapTo)} on ${spokenDay(start)}, so ${spokenTime(start)} isn't available.`, { off_from: hhmm(gapFrom), off_until: hhmm(gapTo) }];
      }
    }
    if (!containing.length) {
      return ["outside_hours", `${spokenTime(start)} is outside our hours on ${spokenDay(start)}. We're open from ${spokenTime(firstOpen)} to ${spokenTime(lastClose)}.`, { opens: hhmm(firstOpen), closes: hhmm(lastClose) }];
    }
    const window = containing[0];
    const neededEnd = addMinutes(start, spec.durationMinutes + spec.bufferAfterMinutes);
    if (neededEnd > window.end && window !== windows[windows.length - 1]) {
      // the stretch ends before the day does: the service would run into a break, not the closing time
      return ["conflicts_next", `The ${length} service starting at ${spokenTime(start)} would run into ${staff}'s break at ${spokenTime(window.end)}.`, { next_stop: hhmm(window.end) }];
    }
    if (neededEnd > window.end) {
      const raw = addMinutes(window.end, -(spec.durationMinutes + spec.bufferAfterMinutes));
      const p = localParts(raw);
      const latest = fromLocal(p.year, p.month, p.day, p.hour, p.minute - (p.minute % this.grid), 0);
      const close = window.end;
      if (latest >= window.start) {
        return ["ends_after_closing", `The ${length} service starting at ${spokenTime(start)} would run past our ${spokenTime(close)} closing. The latest start that day is ${spokenTime(latest)}.`,
          { latest_start: hhmm(latest), closes: hhmm(close) }];
      }
      return ["ends_after_closing", `The ${length} service doesn't fit within our hours that day.`, { closes: hhmm(close) }];
    }
    if (addMinutes(now, this.minLead) > start) return ["too_soon", `That's too soon. We need at least ${this.minLead} minutes' notice.`, {}];
    const booked = (sd.appointments ?? []).filter((a) => a.blocksTime).sort((a, b) => a.interval.start - b.interval.start);
    if (!booked.some((a) => a.interval.start <= start && start < a.interval.end)) {
      const serviceEnd = addMinutes(start, spec.durationMinutes);
      const blockedStart = addMinutes(start, -spec.bufferBeforeMinutes);
      const blockedEnd = neededEnd;
      const clash = booked.find((a) => a.interval.start < blockedEnd && blockedStart < a.interval.end);
      if (clash !== undefined) { // the start itself is free, but the whole visit (and its cleanup time) is not
        const at = clash.interval.start;
        const text = clash.interval.start < serviceEnd
          ? `The ${length} service starting at ${spokenTime(start)} would run into ${staff}'s next appointment at ${spokenTime(at)}.`
          : `The ${length} service would fit before ${staff}'s ${spokenTime(at)} appointment, but there wouldn't be time for the ${spec.bufferAfterMinutes} minutes of cleanup after it.`;
        return ["conflicts_next", text, { next_appointment: hhmm(at) }];
      }
    }
    return ["occupied", `${spokenTime(start)} on ${spokenDay(start)} is already taken.`, {}];
  }

  /** The reviewed explanation, except when a STORED appointment is the cause. Its cleanup time is part of the time it occupies (the calendar draws it as a shaded block), so the reviewed
   * text would call it a break or the technician 'off'. Here it is said as what it is: already taken, getting ready, or running into the next one. */
  private async whyNotOne(snapshot: DaySnapshot, start: Instant, service: BookableService, staff: string): Promise<[string, string, Reply]> {
    const [reason, text, extra] = this.baseWhyNotOne(snapshot, start, service, staff);
    const person = this.dataset.roster.find((s) => s.name === staff);
    if (!["on_break", "conflicts_next", "occupied"].includes(reason) || person === undefined) return [reason, text, extra];
    const spec = this.spec(service);
    const moment = epochOf(start);
    let rows: StoredAppointment[];
    try {
      rows = await this.store.occupiedBetween(moment - spec.bufferBeforeMinutes * 60, moment + (spec.durationMinutes + spec.bufferAfterMinutes) * 60, { staffId: person.staffId });
    } catch (error) {
      if (error instanceof StoreError) return [reason, text, extra];
      throw error;
    }
    if (!rows.length) return [reason, text, extra];
    const endOf = (r: StoredAppointment) => r.startUtc + r.durationMinutes * 60;
    if (rows.some((r) => r.startUtc <= moment && moment < endOf(r))) return ["occupied", `${spokenTime(start)} on ${spokenDay(start)} is already taken.`, {}];
    const before = rows.filter((r) => r.startUtc < moment);
    if (before.length) {
      const last = before.reduce((a, b) => (endOf(b) > endOf(a) ? b : a));
      const ready = addMinutes(instantOf(last.startUtc), last.durationMinutes + last.bufferAfterMinutes);
      return ["on_break", `${staff} needs until ${spokenTime(ready)} to get ready after the ${spokenTime(instantOf(last.startUtc))} appointment, so ${spokenTime(start)} isn't available.`, { ready_at: hhmm(ready) }];
    }
    const following = rows.reduce((a, b) => (b.startUtc < a.startUtc ? b : a));
    const at = instantOf(following.startUtc);
    const length = spokenDuration(service.durationMinutes);
    if (following.startUtc < moment + spec.durationMinutes * 60) {
      return ["conflicts_next", `The ${length} service starting at ${spokenTime(start)} would run into ${staff}'s next appointment at ${spokenTime(at)}.`, { next_appointment: hhmm(at) }];
    }
    return ["conflicts_next", `The ${length} service would fit before ${staff}'s ${spokenTime(at)} appointment, but there wouldn't be time for the ${spec.bufferAfterMinutes} minutes of cleanup after it.`, { next_appointment: hhmm(at) }];
  }

  /** (reason, spoken explanation, extra data) for a start time nobody eligible can take. Known facts only. When several technicians are considered, the reason closest to bookable is the most useful. */
  private async whyNot(snapshot: DaySnapshot, start: Instant, service: BookableService, staffs: string[], named: string | null): Promise<[string, string, Reply]> {
    const reasons: Array<[string, string, Reply, string]> = [];
    for (const name of staffs) {
      const [r, t, e] = await this.whyNotOne(snapshot, start, service, name);
      reasons.push([r, t, e, name]);
    }
    let best = reasons[0];
    for (const r of reasons) if (REASON_RANK[r[0]] > REASON_RANK[best[0]]) best = r;
    let [reason, text, extra, who] = best;
    if (named) { // a technician was asked for by name: speak about THEIR day, not the salon's
      const p = localParts(start);
      const at = (t: string) => { const [h, m] = t.split(":").map(Number); return spokenTime(fromLocal(p.year, p.month, p.day, h, m)); };
      if (reason === "occupied") text = `${who} is already booked at ${spokenTime(start)} on ${spokenDay(start)}.`;
      else if (reason === "not_working") text = `${who} isn't working on ${spokenDay(start)}.`;
      else if (reason === "outside_hours") text = `${who} works from ${at(extra.opens)} to ${at(extra.closes)} on ${spokenDay(start)}, so ${spokenTime(start)} is outside those hours.`;
      else if (reason === "ends_after_closing" && "latest_start" in extra) {
        const length = spokenDuration(service.durationMinutes);
        text = `The ${length} service starting at ${spokenTime(start)} would run past the end of ${who}'s day at ${at(extra.closes)}. The latest start that day is ${at(extra.latest_start)}.`;
      }
    }
    return [reason, text, extra];
  }

  // ================================================================ finding alternatives
  private async alternatives(
    firstDay: DateParts, preferredMinutes: number, firstSnapshot: DaySnapshot | null, service: BookableService, named: string | null, doc: CallDoc,
    opts: { days?: number | null; window?: [number | null, number | null] | null } = {},
  ): Promise<{ slots: Slot[]; unreadable: number; firstError: Reply | null }> {
    const chosen: Slot[] = [];
    let unreadable = 0;
    let firstError: Reply | null = null;
    const spec = this.spec(service);
    const totalDays = opts.days ?? this.searchDays;
    for (let offset = 0; offset < totalDays; offset++) {
      const day = addDays(firstDay, offset);
      let snapshot: DaySnapshot;
      if (offset === 0 && firstSnapshot !== null) snapshot = firstSnapshot;
      else {
        try {
          snapshot = await this.readDay(day);
        } catch (error) {
          if (error instanceof DriverError) { unreadable += 1; continue; }
          throw error;
        }
      }
      const { staffs, problem } = this.eligibleStaff(snapshot, service, named);
      if (problem !== null) {
        if (problem.status === "unknown") unreadable += 1;
        if (offset === 0) firstError = problem;
        continue;
      }
      const byStart = new Map<number, Slot>();
      let dayUnknown = false;
      for (const name of staffs) {
        const found = findSlots(snapshot, spec, name, { now: this.now(), gridMinutes: this.grid, minLeadMinutes: this.minLead });
        if (found.unknown.length) { dayUnknown = true; continue; }
        for (const slot of found.slots) if (!byStart.has(slot.service.start)) byStart.set(slot.service.start, slot); // the first eligible technician keeps a shared start
      }
      const slots = [...byStart.values()].sort((a, b) => a.service.start - b.service.start);
      if (dayUnknown && !slots.length) { unreadable += 1; continue; }
      if (!slots.length) continue;
      const minutes = (slot: Slot) => { const p = localParts(slot.service.start); return p.hour * 60 + p.minute; };
      if (opts.window) { // an explicit bound is a rule: only starts inside it are offered, and only from the day asked about
        const [lo, hi] = opts.window;
        const inside = slots.filter((s) => (lo === null || minutes(s) >= lo) && (hi === null || minutes(s) <= hi));
        if (!inside.length) continue;
        chosen.push(...(hi !== null && lo === null ? inside.slice(-this.maxOptions) : inside.slice(0, this.maxOptions))); // nearest the bound that was given
      } else if (offset === 0) {
        const after = slots.filter((s) => minutes(s) >= preferredMinutes);
        const before = slots.filter((s) => minutes(s) < preferredMinutes);
        if (after.length) chosen.push(after[0]);
        if (before.length) chosen.push(before[before.length - 1]);
      } else {
        let best = slots[0];
        for (const s of slots) if (Math.abs(minutes(s) - preferredMinutes) < Math.abs(minutes(best) - preferredMinutes)) best = s;
        chosen.push(best);
      }
      if (chosen.length >= this.maxOptions) break;
    }
    void doc;
    return { slots: chosen.slice(0, this.maxOptions), unreadable, firstError };
  }

  private issue(start: Instant, service: BookableService, staff: string): Reply {
    return {
      label: `${spokenSlot(start)} with ${staff}`, start: isoformat(start), service_id: service.serviceId, service: service.booksyName, duration_minutes: service.durationMinutes,
      price_usd: this.price(service), technician: staff,
    };
  }

  private offer(slots: Slot[], unreadable: number, service: BookableService, named: string | null, lead: string, daysSearched: number | null = null): Reply {
    if (!slots.length) {
      if (unreadable) {
        return response("unknown", `${lead}I couldn't read the calendar clearly enough to offer other times. We can try again in a moment, or you can contact the salon directly.`, { options: [] });
      }
      const who = named ? ` for ${named}` : "";
      const searched = daysSearched ?? this.searchDays;
      const scope = searched === 1 ? "that day" : "in the next few days";
      const more = named ? " Would you like me to check other technicians?" : searched === 1 ? " Would you like me to look at other days?" : " Would you like me to look further ahead?";
      return response("no_alternatives", `${lead}I don't see any other openings${who} ${scope}.${more}`, { options: [], days_searched: searched });
    }
    const options = slots.map((s) => this.issue(s.service.start, service, s.staff));
    return response("alternatives", `${lead}I do have ${joinChoices(options.map((o) => o.label))}. Which would you prefer?`, { options });
  }

  // ================================================================ local information (never the calendar)
  /** LOCAL ONLY: what the menu says about a service. It never touches the calendar, needs no call id, and carries no source label (as in the Python tools). */
  async lookup_service(_callId: string, args: Args): Promise<Reply> {
    return this.branded(this.lookup(args ?? {}));
  }
  private lookup(args: Args): Reply {
    const query = args.query;
    if (typeof query !== "string" || !query.trim()) return response("needs_clarification", "Which service would you like to know about?", { reason: "no_query" });
    const group = this.groupAnswer(query);
    if (group !== null) {
      const { status, speak, ...data } = group;
      return response(status, speak, data);
    }
    const length = durationHint(query);
    if (length !== null && !treatmentWords(query).length) { // a length and no treatment ("sixty minutes"): ask which treatment
      return response("needs_clarification", `Which treatment would you like for ${spokenDuration(length)}?`, { reason: "service_required", requested_minutes: length });
    }
    const resolution = this.catalog.resolve(query);
    if (resolution.kind === "unknown") {
      return response("unknown_service", "I don't have that on the services I can see. I don't want to guess, so please contact the salon directly about it.", { items: [] });
    }
    if (resolution.kind === "ambiguous") {
      const families = [...new Set(resolution.items.map((i) => i.family))].slice(0, 8);
      return response("choose_service", `Several services fit that: ${joinChoices(families)}. Which one did you mean?`, { families, items: resolution.items.map((i) => this.lite(i)).slice(0, 8) });
    }
    if (resolution.kind === "variants") {
      const parts = resolution.items.map((i) => (descriptiveLabel(i) ? `${descriptiveLabel(i)}: ` : "") + `${i.durationMinutes ? spokenDuration(i.durationMinutes) : "no listed length"} for ${spokenPrice(i.priceUsd)}`);
      const family = resolution.items[0].family;
      const prefix = resolution.hintUnmatched ? `I don't see that length for ${family}. ` : "";
      return response("choose_variant", `${prefix}${family} comes as ${joinChoices(parts)}. Which length would you like?`, { family, items: resolution.items.map((i) => this.lite(i)) });
    }
    const item = resolution.items[0];
    const mapped = this.dataset.registry.forCatalogItem(item.itemId);
    const canBook = Boolean(mapped && mapped.verified);
    const len = item.durationMinutes ? spokenDuration(item.durationMinutes) : "no listed length";
    const note = item.notes ? ` Note: ${item.notes}` : "";
    const booking = canBook ? "I can check availability for it." : "I can tell you about it, but I can't book it online yet.";
    const technicians = canBook ? this.dataset.providers(mapped!) : [];
    const who = technicians.length ? ` ${joinNames(technicians)} ${technicians.length === 1 ? "does" : "do"} this service.` : "";
    return response("service_info", `${item.name}: ${len}, ${spokenPrice(item.priceUsd)} ${this.catalog.listedAs(item)}. ${item.description} ${booking}${who}${note}`, {
      item: toPublic(item), bookable: canBook, ...(canBook ? { bookable_service_id: mapped!.serviceId } : {}),
      ...(technicians.length ? { technicians, technicians_source: LifecycleTools.SOURCE } : {}),
    });
  }

  // ================================================================ the calendar functions
  async check_slot(callId: string, args: Args): Promise<Reply> {
    return this.run(callId, (doc) => this.checkSlot(args ?? {}, doc));
  }
  protected async checkSlot(args: Args, doc: CallDoc): Promise<Reply> {
    const sel = this.select(args);
    if ("problem" in sel) return sel.problem;
    const when = this.parseWhen(args, true);
    if ("problem" in when) return when.problem;
    const { service, named } = sel;
    const { day } = when;
    const start = when.start!;
    let snapshot: DaySnapshot;
    try {
      snapshot = await this.readDay(day);
    } catch (error) {
      if (error instanceof DriverError) return response("unknown", "I couldn't read the calendar clearly, so I can't tell you whether that time is free. Let me not guess.");
      throw error;
    }
    const { staffs, problem } = this.eligibleStaff(snapshot, service, named);
    if (problem) return problem;
    const p = localParts(start);
    doc.preferred = p.hour * 60 + p.minute;
    const spec = this.spec(service);
    const free: string[] = [];
    const unreadableStaff: Array<[string, string[]]> = [];
    for (const name of staffs) {
      const verdict = validateSlot(snapshot, spec, name, start, { now: this.now(), minLeadMinutes: this.minLead });
      if (verdict.unknown.length) unreadableStaff.push([name, verdict.unknown]);
      else if (verdict.ok) free.push(name);
    }
    const unknown = unreadableStaff.length > 0;
    const length = spokenDuration(service.durationMinutes);
    if (free.length) {
      return response("available", `${spokenSlot(start)} is open for the full ${length} of ${service.booksyName}, with ${free[0]}. Would you like me to book it?`, { options: [this.issue(start, service, free[0])] });
    }
    const unreadableNames = new Set(unreadableStaff.map(([n]) => n));
    const known = staffs.filter((n) => !unreadableNames.has(n));
    if (unknown && (named || !known.length)) { // a named person, or nobody whose schedule can be read: fail closed
      return response("unknown", "I couldn't confirm the calendar for that day, so I can't tell you whether it's free. Let me not guess.");
    }
    if (unknown) return this.partlyUnknown(snapshot, start, day, service, named, known, unreadableStaff, doc);
    const [reason, explanation, extra] = await this.whyNot(snapshot, start, service, staffs, named);
    const everyone = staffs.length > 1 && !named ? `Everyone working that day who does this is booked or unavailable at ${spokenTime(start)} for the full ${spokenDuration(service.durationMinutes)}. ` : "";
    const alt = await this.alternatives(day, p.hour * 60 + p.minute, snapshot, service, named, doc, { days: this.checkDays });
    const offered = this.offer(alt.slots, alt.unreadable, service, named, everyone + explanation + " ", this.checkDays);
    Object.assign(offered, extra);
    offered.unavailable_reason = reason;
    offered.requested = isoformat(start);
    return offered;
  }

  /** No one whose schedule can be read is free at the asked time, and someone who could do it has a schedule that cannot be read. Unknown is still never free, so nothing is promised, but the
   * answer says what is actually the case: the readable schedules cannot take it (and why), the unreadable ones are not guessed at, and the genuinely open times are offered. */
  private async partlyUnknown(snapshot: DaySnapshot, start: Instant, day: DateParts, service: BookableService, named: string | null, known: string[], unreadableStaff: Array<[string, string[]]>, doc: CallDoc): Promise<Reply> {
    const length = spokenDuration(service.durationMinutes);
    const [reason, explanation, extra] = await this.whyNot(snapshot, start, service, known, null);
    const hoursMissing = unreadableStaff.filter(([, r]) => r.includes("working hours unknown")).map(([n]) => n);
    const other = unreadableStaff.map(([n]) => n).filter((n) => !hoursMissing.includes(n));
    const parts = [...hoursMissing.map((n) => `${n}'s working hours aren't shown on the calendar, so I can't say whether ${n} could take it`), ...other.map((n) => `${n}'s schedule couldn't be read, so I can't say whether ${n} could take it`)];
    const lead = `I can't confirm ${spokenSlot(start)} for the full ${length} of ${service.booksyName}. ` + (known.length > 1 ? "None of the technicians whose schedules I can read can take it. " : "") + `${explanation} ${parts.join("; ")}. `;
    const p = localParts(start);
    const alt = await this.alternatives(day, p.hour * 60 + p.minute, snapshot, service, named, doc, { days: this.checkDays });
    const offered = this.offer(alt.slots, alt.unreadable, service, named, lead, this.checkDays);
    offered.status = "unknown";
    offered.ok = false; // whatever else it found, the asked time could not be confirmed
    Object.assign(offered, extra);
    offered.reason = "partial_calendar";
    offered.incomplete_schedules = unreadableStaff.map(([n]) => n);
    offered.unavailable_reason = reason;
    offered.requested = isoformat(start);
    return offered;
  }

  async find_alternatives(callId: string, args: Args): Promise<Reply> {
    return this.run(callId, (doc) => this.findAlternatives(args ?? {}, doc));
  }
  private async findAlternatives(args: Args, doc: CallDoc): Promise<Reply> {
    const sel = this.select(args);
    if ("problem" in sel) return sel.problem;
    const when = this.parseWhen(args, false);
    if ("problem" in when) return when.problem;
    const { service, named } = sel;
    const { day, start } = when;
    const w = this.parseWindow(args, day);
    if (w.problem) return w.problem;
    let preferred: number;
    if (start !== null) { // the caller gave a time (a new one replaces the old)
      const p = localParts(start);
      preferred = p.hour * 60 + p.minute;
      doc.preferred = preferred;
    } else if (args.any_time === true) { // the caller said any time works: drop the earlier preference
      doc.preferred = null;
      preferred = 10 * 60;
    } else preferred = doc.preferred ?? 10 * 60; // no time given: keep the one asked for earlier in THIS call, if any
    const alt = await this.alternatives(day, preferred, null, service, named, doc, { days: w.window ? 1 : null, window: w.window });
    if (!alt.slots.length && alt.firstError !== null && ["staff_unavailable", "needs_clarification"].includes(alt.firstError.status)) return alt.firstError; // a name nobody has, or two people with it: ask
    if (w.window === null) return this.offer(alt.slots, alt.unreadable, service, named, "");
    // an explicit start-time bound: the answer says what was asked, offers nothing outside it, and does not wander to other days
    const dayText = spokenDay(fromLocal(day.year, day.month, day.day));
    const hm = (m: number | null) => (m === null ? null : `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`);
    const info = { date: isoDate(day), from: hm(w.window[0]), to: hm(w.window[1]), described_as: w.said };
    let r: Reply;
    if (!alt.slots.length && !alt.unreadable) {
      r = response("no_alternatives", `I don't see any openings${named ? ` with ${named}` : ""} starting ${w.said} on ${dayText}. Would you like me to look at other days?`, { options: [], days_searched: 1 });
    } else r = this.offer(alt.slots, alt.unreadable, service, named, `For a start ${w.said} on ${dayText}: `, 1);
    r.window = info;
    r.instruction = `Every time listed, if any, starts ${w.said} on ${dayText}; nothing outside that was searched or offered. Never call an earlier or later time something it is not, and do not ` +
      "offer a time from another day as if it met the request: if the caller wants other days, ask and search again.";
    return r;
  }

  // ================================================================ small helpers of the lifecycle line
  private local(row: StoredAppointment): Instant {
    return instantOf(row.startUtc);
  }
  protected label(row: StoredAppointment): string {
    const extra = CATEGORY_WORDS[row.category];
    return extra && !row.serviceName.toLowerCase().includes(extra) ? `${row.serviceName} ${extra}` : row.serviceName;
  }
  protected slot(row: StoredAppointment): string {
    return spokenSlot(this.local(row));
  }
  protected view(row: StoredAppointment): Reply {
    return {
      reference: row.reference, service_id: row.serviceId, service: row.serviceName, category: row.category, technician: this.staffName(row.staffId), start: isoformat(this.local(row)),
      duration_minutes: row.durationMinutes, version: row.version,
    };
  }
  private spokenReference(reference: string): string {
    return [...reference].join(" ");
  }
  /** The service and its length, saying the length once: 'Swedish Massage, 1 hour' already does, 'Full Leg' does not. */
  private described(name: string, minutes: number): string {
    const length = spokenDuration(minutes);
    return name.toLowerCase().includes(length.toLowerCase()) ? name : `${name}, ${length}`;
  }
  protected tokenKey(kind: string, callId: string, token: string): string {
    return createHash("sha256").update(`${kind}|${callId}|${token}`, "utf8").digest("hex").slice(0, 32);
  }
  protected async newPending(kind: string, callId: string, payload: Record<string, any>): Promise<string> {
    const token = this.newToken();
    const now = this.now();
    await this.calls.putPending(token, { kind, callId, payload, expires: now + this.confirmTtl }, now);
    return token;
  }
  protected async takePending(kind: string, callId: string, token: unknown): Promise<Pending | null> {
    const pending = typeof token === "string" ? await this.calls.getPending(token) : null;
    if (pending === null || pending.kind !== kind || pending.callId !== callId || pending.expires <= this.now()) return null;
    return pending;
  }
  protected committed(): void {}
  protected countChange(doc: CallDoc): void {
    doc.changes += 1;
  }
  protected unavailable(): Reply {
    return reply("system_unavailable", this.contacting("I can't reach the appointment records right now, so I can't confirm that."), { reason: "store_unavailable" });
  }

  // ---- who is asking about: the complete phone number (a DEMO record match, not authentication)
  private digest(phone: string): string {
    return createHash("sha256").update(phone, "utf8").digest("hex").slice(0, 16);
  }
  protected phone(doc: CallDoc, args: Args): { phone: string; problem: null } | { phone: null; problem: Reply } {
    const raw = args.phone;
    if (raw === null || raw === undefined || (typeof raw === "string" && !raw.trim())) {
      return { phone: null, problem: reply("needs_clarification", "Could you give me the complete phone number on your account, including the area code?", { reason: "phone_required" }) };
    }
    const phone = normalizePhone(raw);
    if (phone === null) {
      return { phone: null, problem: reply("invalid_phone", "That doesn't sound like a complete phone number. Could you give me the whole number, including the area code?", { reason: "phone_incomplete" }) };
    }
    const key = this.digest(phone);
    if (!doc.numbers.includes(key) && doc.numbers.length >= MAX_NUMBERS_PER_CALL) {
      return { phone: null, problem: reply("refused", this.contacting("I can't look up any more numbers on this call."), { reason: "too_many_numbers" }) };
    }
    if (!doc.numbers.includes(key)) doc.numbers.push(key);
    return { phone, problem: null };
  }

  protected spokenName(value: unknown): string | null {
    if (typeof value !== "string") return null;
    const cleaned = value.replace(/[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]/gu, "").split(/\s+/).filter(Boolean).join(" ");
    return cleaned ? cleaned.slice(0, NAME_MAX) : null;
  }
  protected rememberIdentity(doc: CallDoc, phone: string, spoken: string | null, clientId: number | null): void {
    if (spoken === null && doc.identity !== null && doc.identity.phone === phone) spoken = doc.identity.name; // a request that gave only the number does not make the call forget who it settled on
    doc.identity = { phone, name: spoken, key: spoken ? nameKey(spoken) : null, clientId };
  }
  private countFailed(doc: CallDoc): void {
    doc.failed += 1;
  }

  /** Which record is meant by this number and this name, in this call: `{record, problem, isNew}`.
   * A name that matches NOBODY on a number that already has a record is not a new person until the caller has spelled it: a recogniser's 'Villa' for 'Willa' must not become a second client.
   * The question never says whose record is on the number, and nothing is fuzzy-matched: only an exact name (case and spacing aside) selects a record. What the call has settled on stays
   * settled: a different name later in the same call is asked about, never silently used to read another household member's appointments. */
  protected async identify(doc: CallDoc, phone: string, spoken: string | null, confirmed: boolean, booking = false): Promise<{ record: { clientId: number; name: string } | null; problem: Reply | null; isNew: boolean }> {
    const found = await this.store.clientsWithPhone(phone);
    const earlier = doc.identity !== null && doc.identity.phone === phone ? doc.identity : null;
    if (earlier !== null && earlier.name) {
      if (spoken === null) { spoken = earlier.name; confirmed = true; } // the caller already told this call who they are
      else if (nameKey(spoken) === earlier.key) confirmed = true;
      else if (!confirmed) {
        return {
          record: null, isNew: false,
          problem: reply("needs_clarification", `Earlier in this call I had the name as ${earlier.name}. Which name should I use for this?`, {
            reason: "name_changed",
            instruction: "The name is different from the one used earlier in this call. Ask which name is right and have them spell it, then call again with that name and name_confirmed true.",
          }),
        };
      }
    }
    if (!found.length) return { record: null, problem: null, isNew: true }; // nobody has this number yet
    if (doc.failed >= MAX_FAILED_MATCHES_PER_CALL) { // guessing names against a number is bounded like guessing references
      return { record: null, isNew: false, problem: reply("refused", this.contacting("I can't look up any more appointments on this call."), { reason: "too_many_attempts" }) };
    }
    if (spoken === null) {
      const choice = chooseClient(found.map((c) => ({ ref: String(c.clientId), name: c.name })), null);
      if (isReply(choice)) return { record: null, problem: choice, isNew: false }; // the one question that settles it: the full name on the booking
      return { record: found.find((c) => String(c.clientId) === choice.ref)!, problem: null, isNew: false };
    }
    const same = found.filter((c) => nameKey(c.name) === nameKey(spoken));
    if (same.length) return { record: same[0], problem: null, isNew: false };
    if (!confirmed) {
      this.countFailed(doc);
      return {
        record: null, isNew: false,
        problem: reply("needs_clarification", "I couldn't match that name to a record on that number. To be sure I get it right, could you spell your first and last name for me, one letter at a time?", {
          reason: "name_unmatched",
          instruction: "Ask the caller to spell their full name, then call this tool again with the name exactly as spelled and name_confirmed true. Do not guess a spelling, never say " +
            "whose record is on the number, and never say how many records match.",
        }),
      };
    }
    if (booking) return { record: null, problem: null, isNew: true }; // spelled out and still nobody's: a new person on a number that is already used
    this.countFailed(doc);
    return { record: null, isNew: false, problem: reply("not_found", this.contacting("I can't find that name on that number."), { reason: "name_not_found" }) };
  }

  protected async client(doc: CallDoc, args: Args): Promise<{ record: { clientId: number; name: string } | null; problem: Reply | null }> {
    const ph = this.phone(doc, args);
    if (ph.problem) return { record: null, problem: ph.problem };
    const spoken = this.spokenName(args.name);
    let found;
    try {
      found = await this.identify(doc, ph.phone, spoken, args.name_confirmed === true);
    } catch (error) {
      if (error instanceof StoreError) return { record: null, problem: this.unavailable() };
      throw error;
    }
    if (found.problem) return { record: null, problem: found.problem };
    if (found.record === null) return { record: null, problem: chooseClient([], null) as Reply }; // no record on this number: the reviewed answer
    this.rememberIdentity(doc, ph.phone, spoken, found.record.clientId);
    return { record: found.record, problem: null };
  }

  private reference(value: unknown): string | null {
    if (typeof value !== "string") return null;
    const cleaned = value.replace(/[\s-]/g, "").toUpperCase();
    return cleaned.length === 6 && [...cleaned].every((c) => "23456789ABCDEFGHJKMNPQRSTUVWXYZ".includes(c)) ? cleaned : null;
  }

  /** The ONE appointment this reference names, if the complete number given is the client's. A wrong pair is just 'not found': it says nothing about why. */
  protected async owned(doc: CallDoc, callId: string, args: Args): Promise<{ row: StoredAppointment | null; problem: Reply | null }> {
    void callId;
    const ph = this.phone(doc, args);
    if (ph.problem) return { row: null, problem: ph.problem };
    const reference = this.reference(args.reference);
    if (reference === null) return { row: null, problem: reply("needs_clarification", "I need the appointment's reference. Let me look up your upcoming appointments first.", { reason: "reference_required" }) };
    if (doc.failed >= MAX_FAILED_MATCHES_PER_CALL) return { row: null, problem: reply("refused", this.contacting("I can't look up any more appointments on this call."), { reason: "too_many_attempts" }) };
    let row: StoredAppointment | null;
    let client;
    let sharing;
    try {
      row = await this.store.get(reference);
      client = row !== null ? await this.store.client(row.clientId) : null;
      if (row === null || client === null || client.phone !== ph.phone) {
        doc.failed += 1;
        return { row: null, problem: reply("not_found", this.contacting("I can't find that appointment for that number."), { reason: "no_match" }) };
      }
      sharing = await this.store.clientsWithPhone(ph.phone);
    } catch (error) {
      if (error instanceof StoreError) return { row: null, problem: this.unavailable() };
      throw error;
    }
    let spoken = this.spokenName(args.name);
    const earlier = doc.identity;
    if (earlier !== null && earlier.phone === ph.phone && earlier.name) {
      if (spoken === null) spoken = earlier.name; // the name this call already settled on
      else if (nameKey(spoken) !== earlier.key && args.name_confirmed !== true) {
        return {
          row: null,
          problem: reply("needs_clarification", `Earlier in this call I had the name as ${earlier.name}. Which name should I use for this?`, {
            reason: "name_changed",
            instruction: "The name is different from the one used earlier in this call. Ask which name is right and have them spell it, then call again with that name and name_confirmed true.",
          }),
        };
      }
    }
    if (sharing.length > 1 && nameKey(spoken ?? "") !== nameKey(client.name)) {
      return { row: null, problem: reply("needs_clarification", "That number is on more than one record. Could you tell me the full name on the booking?", { reason: "name_needed" }) };
    }
    this.rememberIdentity(doc, ph.phone, spoken, client.clientId);
    return { row, problem: null };
  }

  // ================================================================ FIND: upcoming appointments
  async find_appointments(callId: string, args: Args): Promise<Reply> {
    return this.run(callId, (doc) => this.find(args ?? {}, doc));
  }
  private async find(args: Args, doc: CallDoc): Promise<Reply> {
    const { record: client, problem } = await this.client(doc, args);
    if (problem) return problem;
    let rows: StoredAppointment[];
    try {
      rows = await this.store.forClient(client!.clientId, [CONFIRMED]);
    } catch (error) {
      if (error instanceof StoreError) return this.unavailable();
      throw error;
    }
    const now = epochOf(this.now());
    const upcoming = rows.filter((r) => r.startUtc > now).sort((a, b) => a.startUtc - b.startUtc || a.apptId - b.apptId);
    if (!upcoming.length) return reply("no_upcoming", "I don't see any upcoming appointments for that number.", { appointments: [], count: 0 });
    const wanted = args.service;
    const category = typeof wanted === "string" && wanted.trim() ? this.classifier.categoryOfRequest(wanted) : null;
    const focus = upcoming.filter((r) => category !== null && r.category === category);
    const rest = upcoming.filter((r) => !focus.includes(r));
    const when = (row: StoredAppointment) => `on ${this.slot(row)} with ${this.staffName(row.staffId)}`;
    const brief = (rs: StoredAppointment[]) => {
      const shown = rs.slice(0, SHOWN_UPCOMING).map((r) => `${this.label(r)} ${when(r)}`);
      const more = rs.length - shown.length;
      return joinNames(shown) + (more > 0 ? `, and ${more} more` : "");
    };
    let speak: string;
    if (category !== null && focus.length) {
      let lead = `Your ${this.label(focus[0])} is ${when(focus[0])}.`;
      if (focus.length > 1) lead = `Your next ${this.label(focus[0])} is ${when(focus[0])}, and you have ${focus.length - 1} more of that kind.`;
      speak = lead + (rest.length ? ` You also have ${brief(rest)}.` : "");
    } else if (category !== null) {
      speak = `I don't see an upcoming ${category.replaceAll("-", " ")} appointment. You do have ${brief(upcoming)}.`;
    } else {
      speak = `You have ${upcoming.length} upcoming ${upcoming.length === 1 ? "appointment" : "appointments"}: ${brief(upcoming)}.`;
      if (upcoming.length > SHOWN_UPCOMING) speak += " I can go through the rest if you like.";
    }
    return reply("found", speak, { appointments: upcoming.slice(0, 8).map((r) => this.view(r)), count: upcoming.length });
  }

  // ================================================================ HISTORY: the latest COMPLETED visit
  async last_completed_visit(callId: string, args: Args): Promise<Reply> {
    return this.run(callId, async (doc) => {
      // The reviewed history answer, after the same identity rules as every other tool, run INSIDE its bounded attempts.
      const identify = async (phone: string, given: Args): Promise<[Args, Reply | null]> => {
        const spoken = this.spokenName(given.name);
        let found;
        try {
          found = await this.identify(doc, phone, spoken, given.name_confirmed === true);
        } catch (error) {
          if (error instanceof StoreError) return [given, this.unavailable()];
          throw error;
        }
        if (found.problem !== null && ["name_unmatched", "name_changed", "name_not_found", "too_many_attempts"].includes(found.problem.reason)) return [given, found.problem];
        if (found.record !== null) {
          this.rememberIdentity(doc, phone, spoken, found.record.clientId);
          return [{ ...given, name: found.record.name }, null];
        }
        return [given, null];
      };
      // the history lookup counts its attempts in this run's own document
      return this.history.lastCompletedVisit(callId, args ?? {}, identify, doc);
    });
  }

  // ================================================================ BOOK
  async book_appointment(callId: string, args: Args): Promise<Reply> {
    return this.run(callId, (doc) => this.book(callId, args ?? {}, doc));
  }
  private cleanName(value: unknown): string | null {
    if (typeof value !== "string") return null;
    const cleaned = value.replace(/[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]/gu, "").split(/\s+/).filter(Boolean).join(" ");
    return cleaned.length > 0 && cleaned.length <= NAME_MAX ? cleaned : null;
  }
  private spelled(name: string): string {
    return name.split(/\s+/).filter((word) => /\p{L}/u.test(word)).map((word) => [...word].filter((ch) => /\p{L}/u.test(ch)).map((ch) => ch.toUpperCase()).join(" ")).join(", ");
  }
  private async book(callId: string, args: Args, doc: CallDoc): Promise<Reply> {
    const sel = this.select(args);
    if ("problem" in sel) return sel.problem;
    const when = this.parseWhen(args, true);
    if ("problem" in when) return when.problem;
    const { service, named } = sel;
    const { day } = when;
    const start = when.start!;
    const name = this.cleanName(args.client_name);
    if (name === null) return reply("needs_clarification", "May I have your full name for the booking?", { reason: "name_required" });
    const ph = this.phone(doc, { phone: args.phone });
    if (ph.problem) return ph.problem;
    let found;
    let shared: boolean;
    try {
      found = await this.identify(doc, ph.phone, name, args.name_confirmed === true, true);
      shared = found.isNew && (await this.store.clientsWithPhone(ph.phone)).length > 0; // a number that already has someone on it, and this name is not one of them
    } catch (error) {
      if (error instanceof StoreError) return this.unavailable();
      throw error;
    }
    if (found.problem) return found.problem;
    this.rememberIdentity(doc, ph.phone, name, found.record !== null ? found.record.clientId : null);
    const spec = { service_id: service.serviceId, start_utc: epochOf(start), name, phone: ph.phone, named: named ? staffKey(named) : null, asked: named };
    const pending = args.confirmed === true ? await this.takePending("book", callId, args.confirmation_token) : null;
    if (pending !== null && this.same(pending.payload, spec)) return this.commitBook(callId, args.confirmation_token, pending, service, day, start, doc);
    if (doc.changes >= MAX_CHANGES_PER_CALL) return reply("refused", this.contacting("I've made as many changes as I can on one call."), { reason: "one_change_limit" });
    const checked: Args = {};
    for (const k of ["service_id", "date", "time", "staff"]) if (k in args) checked[k] = args[k];
    const check = await this.checkSlot(checked, doc);
    if (check.status !== "available") return check; // it already says why not, and offers real alternatives
    const technician = check.options[0].technician;
    const staff = this.dataset.roster.find((s) => s.name === technician)!;
    const full = { ...spec, staff_id: staff.staffId, staff_name: technician };
    const token = await this.newPending("book", callId, full);
    const price = this.price(service) !== null ? ` The listed price is ${spokenPrice(this.price(service))}.` : "";
    const who = shared ? `${name}, spelled ${this.spelled(name)}, as a new client on this number` : name; // a new person on a shared number hears the spelling they gave
    return reply("confirmation_required", `To confirm: ${this.described(service.booksyName, service.durationMinutes)}, with ${technician}, on ${spokenSlot(start)}, for ${who}.${price} Shall I book it?`, {
      confirmation_token: token, service_id: service.serviceId, service: service.booksyName, duration_minutes: service.durationMinutes, technician, start: isoformat(start), price_usd: this.price(service),
      instruction: "Read this back, wait for the caller's clear yes, then call book_appointment again with the same details, the same confirmation_token and confirmed=true.",
    });
  }
  /** Does what was read back match what is now being confirmed? (The technician is the one the read-back named, unless the caller named another.) */
  private same(payload: Record<string, any>, spec: Record<string, any>): boolean {
    return ["service_id", "start_utc", "name", "phone"].every((k) => payload[k] === spec[k]) && (spec.named === null || spec.named === staffKey(payload.staff_name ?? ""));
  }

  private async commitBook(callId: string, token: string, pending: Pending, service: BookableService, day: DateParts, start: Instant, doc: CallDoc): Promise<Reply> {
    const p = pending.payload;
    const booking: NewBooking = {
      clientName: p.name, phone: p.phone, serviceId: service.serviceId, serviceName: service.booksyName, category: this.dataset.categoryOf(service.serviceId), staffId: p.staff_id, startUtc: p.start_utc,
      durationMinutes: service.durationMinutes, bufferBeforeMinutes: service.bufferBeforeMinutes, bufferAfterMinutes: service.bufferAfterMinutes, requestKey: this.tokenKey("book", callId, token),
    };
    let outcome: Outcome;
    try {
      outcome = await this.store.book(booking, { validate: async (rowsBetween, placement) => void (await this.judge.require(placement, rowsBetween)), outbox: this.outbox });
    } catch (error) {
      if (error instanceof StoreError) return this.unavailable();
      return reply("needs_review", this.contacting("Something went wrong and I can't confirm whether the booking went through. Please don't rely on it."), { reason: "exception" });
    }
    const appt = outcome.appointment;
    if (outcome.status === "replayed" && appt && (appt.status !== CONFIRMED || appt.startUtc !== p.start_utc || appt.staffId !== p.staff_id)) {
      const now = appt.status === CONFIRMED ? `it is now on ${this.slot(appt)}` : `it is now ${appt.status.replaceAll("_", " ")}`;
      return reply("stale", `That booking was made earlier, but the appointment has changed since: ${now}. I haven't booked anything again.`, { reason: "changed_after_the_booking", appointment: this.view(appt) });
    }
    if (outcome.status === "booked" || outcome.status === "replayed") return this.bookedReply(outcome, outcome.status === "replayed", doc); // the token stays until it expires, so a retry is answered from the first result
    if (outcome.reason === "client_already_booked" && appt !== null) {
      return reply("unavailable", `You already have ${this.label(appt)} on ${this.slot(appt)} then, so I haven't booked another.`, { reason: "client_already_booked" });
    }
    if (outcome.status === "unavailable" || outcome.status === "unknown") {
      const again: Args = { service_id: service.serviceId, date: isoDate(day), time: hhmm(start) };
      if (p.asked) again.staff = p.asked; // the caller named someone: answer about that person, as they asked
      const r = await this.checkSlot(again, doc);
      r.speak = r.status !== "available" ? "I'm sorry, nothing was booked. " + r.speak : "I'm sorry, that time was just taken, so nothing was booked.";
      r.reason = "changed_before_save";
      if (r.status === "available") r.status = "unavailable";
      return r;
    }
    return reply("refused", this.contacting("I'm not able to book that one, so nothing was booked."), { reason: outcome.reason || outcome.status });
  }

  private async bookedReply(outcome: Outcome, repeat: boolean, doc: CallDoc): Promise<Reply> {
    const row = outcome.appointment!;
    const check = await this.verify(row, CONFIRMED, null);
    if (check !== null) return check;
    if (!repeat) { this.countChange(doc); this.committed(); }
    const what = `${this.described(row.serviceName, row.durationMinutes)}, with ${this.staffName(row.staffId)}, on ${this.slot(row)}`;
    if (repeat) return reply("already_booked", `That's already booked for you: ${what}. I won't book it twice. Your reference is ${this.spokenReference(row.reference)}.`, { appointment: this.view(row) });
    return reply("booked_verified", `You're booked: ${what}. I checked the calendar and it's there. Your reference is ${this.spokenReference(row.reference)}.`, { appointment: this.view(row) });
  }

  /** Re-read the store and the shared calendar after a save. null when both say what the save claims; otherwise a needs_review reply (never a success). */
  private async verify(row: StoredAppointment, expectStatus: string, movedFrom: StoredAppointment | null): Promise<Reply | null> {
    let saved: StoredAppointment | null = null;
    let here: number[] | null = null;
    let elsewhere: number[] | null = null;
    try {
      saved = await this.store.get(row.reference);
      here = await this.shown(dateOf(this.local(row)), row.reference);
      elsewhere = movedFrom !== null ? await this.shown(dateOf(this.local(movedFrom)), row.reference) : null;
    } catch {
      saved = here = elsewhere = null;
    }
    const p = localParts(this.local(row));
    const minutes = p.hour * 60 + p.minute;
    let good = saved !== null && saved.status === expectStatus && saved.serviceId === row.serviceId && saved.staffId === row.staffId && saved.startUtc === row.startUtc;
    good = good && here !== null && (expectStatus === CONFIRMED ? here.length === 1 && here[0] === minutes : here.length === 0);
    if (good && movedFrom !== null && compareDates(dateOf(this.local(movedFrom)), dateOf(this.local(row))) !== 0) good = elsewhere !== null && elsewhere.length === 0; // the old day no longer shows it
    if (good) return null;
    return reply("needs_review", this.contacting("I made the change but when I checked the calendar again I can't confirm it. Please don't rely on it."), { reason: "reverification_failed", reference: row.reference });
  }
  /** The start minutes at which the shared calendar draws this appointment on that day (the same plan the page and the voice read). */
  private async shown(day: DateParts, reference: string): Promise<number[]> {
    const [low, high] = dayBounds(day);
    const rows = await this.store.between(low, high, { statuses: [CONFIRMED] });
    return this.dataset.dayPlan(day, rows).plans.flatMap((p) => p.visits.filter((v) => v.reference === reference).map((v) => v.start));
  }

  // ================================================================ CANCEL
  async cancel_appointment(callId: string, args: Args): Promise<Reply> {
    return this.run(callId, (doc) => this.cancel(callId, args ?? {}, doc));
  }
  private async cancel(callId: string, args: Args, doc: CallDoc): Promise<Reply> {
    const { row, problem } = await this.owned(doc, callId, args);
    if (problem) return problem;
    const r = row!;
    if (r.status === CANCELLED) return reply("already_cancelled", `That ${this.label(r)} appointment on ${this.slot(r)} was already cancelled. Nothing else was changed.`, { appointment: this.view(r) });
    if (r.status !== CONFIRMED || r.startUtc <= epochOf(this.now())) return reply("not_cancellable", this.contacting("I can't cancel that one, because it has already taken place or begun."), { reason: "not_upcoming" });
    const pending = args.confirmed === true ? await this.takePending("cancel", callId, args.confirmation_token) : null;
    if (pending !== null && pending.payload.reference === r.reference) {
      if (pending.payload.version !== r.version) return this.cancelReadback(callId, r, true); // it changed after the read-back: read it back again
      return this.commitCancel(callId, args.confirmation_token, r, args.reason, doc);
    }
    return this.cancelReadback(callId, r, false);
  }
  private async cancelReadback(callId: string, row: StoredAppointment, stale: boolean): Promise<Reply> {
    const token = await this.newPending("cancel", callId, { reference: row.reference, version: row.version });
    const lead = stale ? "That appointment has changed since I read it out. " : "";
    return reply("confirmation_required", `${lead}To confirm: cancel your ${this.label(row)} appointment with ${this.staffName(row.staffId)} on ${this.slot(row)}. Shall I go ahead and cancel it?`, {
      confirmation_token: token, appointment: this.view(row),
      instruction: "Read this back and wait for a clear yes. You may ask why they are cancelling, but the reason is optional. If they are busy or unavailable, offer another day or time " +
        "(then use reschedule_appointment); they may still cancel without choosing one. Then call cancel_appointment again with the same reference and number, the same " +
        "confirmation_token, confirmed=true and the reason if they gave one. Never mention a fee or policy.",
    });
  }
  private async commitCancel(callId: string, token: string, row: StoredAppointment, reason: unknown, doc: CallDoc): Promise<Reply> {
    let outcome: Outcome;
    try {
      outcome = await this.store.cancel(row.reference, { expectedVersion: row.version, reason: typeof reason === "string" ? reason : null, requestKey: this.tokenKey("cancel", callId, token), outbox: this.outbox });
    } catch (error) {
      if (error instanceof StoreError) return this.unavailable();
      return reply("needs_review", this.contacting("Something went wrong and I can't confirm whether it was cancelled. Please don't rely on it."), { reason: "exception" });
    }
    if (["cancelled", "replayed", "already_cancelled"].includes(outcome.status)) {
      const done = outcome.appointment!;
      const check = await this.verify(done, CANCELLED, null);
      if (check !== null) return check;
      if (outcome.status === "already_cancelled") return reply("already_cancelled", "That appointment was already cancelled. Nothing else was changed.", { appointment: this.view(done) });
      if (outcome.status === "cancelled") { this.countChange(doc); this.committed(); }
      return reply("cancelled", `Your ${this.label(done)} appointment on ${this.slot(done)} is cancelled, and that time is free again.`, { appointment: this.view(done) });
    }
    if (outcome.status === "stale" && outcome.appointment !== null) return this.cancelReadback(callId, outcome.appointment, true);
    return reply("refused", this.contacting("I couldn't cancel that, so nothing was changed."), { reason: outcome.reason || outcome.status });
  }

  // ================================================================ RESCHEDULE
  async reschedule_appointment(callId: string, args: Args): Promise<Reply> {
    return this.run(callId, (doc) => this.reschedule(callId, args ?? {}, doc));
  }
  /** The same confirmed move arriving again (a retry, a repeated yes). If THIS request already moved the appointment, the answer is what is true now: already moved, or moved and then changed since. */
  private async repeatOfAMove(callId: string, args: Args, row: StoredAppointment): Promise<Reply | null> {
    const pending = args.confirmed === true ? await this.takePending("reschedule", callId, args.confirmation_token) : null;
    if (pending === null || pending.payload.reference !== row.reference) return null;
    if ((await this.store.requestReference(this.tokenKey("reschedule", callId, args.confirmation_token), "reschedule")) !== row.reference) return null;
    const intended = [pending.payload.start_utc, pending.payload.staff_id];
    if (row.status === CONFIRMED && row.startUtc === intended[0] && row.staffId === intended[1]) {
      const check = await this.verify(row, CONFIRMED, null);
      if (check !== null) return check;
      return reply("already_rescheduled", `That move was already made: your ${this.label(row)} appointment is on ${this.slot(row)}, with ${this.staffName(row.staffId)}. I haven't changed anything again.`, { appointment: this.view(row) });
    }
    const now = row.status === CONFIRMED ? `it is now on ${this.slot(row)} with ${this.staffName(row.staffId)}` : `it is now ${row.status.replaceAll("_", " ")}`;
    return reply("stale", `That move was made earlier, but the appointment has changed since: ${now}. I haven't changed anything more.`, { reason: "changed_after_the_move", appointment: this.view(row) });
  }
  private async reschedule(callId: string, args: Args, doc: CallDoc): Promise<Reply> {
    const { row, problem } = await this.owned(doc, callId, args);
    if (problem) return problem;
    const r = row!;
    const replay = await this.repeatOfAMove(callId, args, r);
    if (replay !== null) return replay;
    if (r.status !== CONFIRMED || r.startUtc <= epochOf(this.now())) return reply("not_reschedulable", this.contacting("I can't move that one, because it isn't an upcoming appointment."), { reason: "not_upcoming" });
    const service = this.dataset.registry.get(r.serviceId);
    if (service === null) return reply("not_reschedulable", this.contacting("I can't move that one, because that service isn't offered now."), { reason: "service_not_offered" });
    const when = this.parseWhen(args, true);
    if ("problem" in when) return when.problem;
    const { day } = when;
    const start = when.start!;
    const asked = args.staff;
    const named = typeof asked === "string" && asked.trim() ? asked.trim() : null;
    const placed = await this.placeMove(r, service, start, named);
    const pending = args.confirmed === true ? await this.takePending("reschedule", callId, args.confirmation_token) : null;
    if (placed.target === null) return this.cannotMove(r, service, day, start, named, placed.refusal, doc);
    const target = placed.target;
    if (target === r.staffId && epochOf(start) === r.startUtc) { // already there: nothing to confirm and nothing to change
      return reply("unchanged", `Your ${this.label(r)} appointment is already on ${this.slot(r)} with ${this.staffName(r.staffId)}. Nothing was changed.`, { appointment: this.view(r) });
    }
    const want = { reference: r.reference, version: r.version, start_utc: epochOf(start) };
    if (pending !== null && Object.entries(want).every(([k, v]) => pending.payload[k] === v) && (named === null || pending.payload.staff_id === target)) {
      return this.commitMove(callId, args.confirmation_token, r, pending.payload.staff_id, epochOf(start), doc);
    }
    if (doc.changes >= MAX_CHANGES_PER_CALL) return reply("refused", this.contacting("I've made as many changes as I can on one call."), { reason: "one_change_limit" });
    const token = await this.newPending("reschedule", callId, { ...want, staff_id: target });
    const who = this.staffName(target);
    return reply("confirmation_required", `To confirm: move your ${this.label(r)} appointment from ${this.slot(r)} to ${spokenSlot(start)}, with ${who}. Shall I make that change?`, {
      confirmation_token: token, appointment: this.view(r), new_start: isoformat(start), technician: who,
      instruction: "Read this back and wait for a clear yes, then call reschedule_appointment again with the same reference, number and new date and time, the same confirmation_token and confirmed=true.",
    });
  }

  /** (staff id, null) for the technician who can take the new time, preferring the same one; or (null, the first refusal). */
  private async placeMove(row: StoredAppointment, service: BookableService, start: Instant, named: string | null): Promise<{ target: string | null; refusal: Refused | null }> {
    const roster = this.dataset.roster;
    let candidates: string[];
    if (named) {
      const wanted = staffKey(named);
      const words = new Set(wanted.split(" ").filter(Boolean));
      let people = roster.filter((s) => staffKey(s.name) === wanted);
      if (!people.length) people = roster.filter((s) => words.size > 0 && [...words].every((w) => staffKey(s.name).split(" ").includes(w)));
      candidates = people.length === 1 ? [people[0].staffId] : [];
      if (!candidates.length) return { target: null, refusal: new Refused("staff_unavailable", "unknown_staff", null, { asked: named }) };
    } else {
      const others = roster.filter((s) => s.staffId !== row.staffId && isEligible(service, s.name, s.staffId) && s.hoursKnown).map((s) => s.staffId);
      candidates = [row.staffId, ...others];
    }
    let first: Refused | null = null;
    for (const staffId of candidates) {
      const placement: Placement = {
        staffId, startUtc: epochOf(start), durationMinutes: service.durationMinutes, bufferBeforeMinutes: service.bufferBeforeMinutes, bufferAfterMinutes: service.bufferAfterMinutes,
        serviceId: service.serviceId, category: row.category, ignoreReference: row.reference,
      };
      try {
        await this.judge.require(placement, async (low, high, statuses) => this.store.between(low, high, { statuses }));
        return { target: staffId, refusal: null };
      } catch (error) {
        if (!(error instanceof Refused)) throw error;
        first = first ?? error;
      }
    }
    return { target: null, refusal: first };
  }

  private async cannotMove(row: StoredAppointment, service: BookableService, day: DateParts, start: Instant, named: string | null, refusal: Refused | null, doc: CallDoc): Promise<Reply> {
    const lead = `Your ${this.label(row)} appointment stays on ${this.slot(row)}. `;
    const reason = refusal !== null ? refusal.reason : "unknown";
    if (reason === "unknown_staff" && named) { // nobody on the team has that name: ask against the real roster, and say the appointment stays where it is
      const asked = this.unknownStaffReply(named, service, this.dataset.roster.map((s): [string, string] => [s.name, s.staffId]));
      asked.speak = `${asked.speak} ${lead}`.trimEnd();
      return asked;
    }
    if (reason === "unknown_staff" || reason === "staff_not_eligible") return reply("staff_unavailable", `I can't do that with ${named ?? "that technician"}. ${lead}`, { reason });
    const snapshot = refusal ? (refusal.detail.snapshot as DaySnapshot | undefined) : undefined;
    const who = refusal ? (refusal.detail.staff as string | undefined) : undefined;
    let text: string;
    if (reason === "not_working" && who) text = `${who} isn't working on ${spokenDay(start)}.`;
    else if (reason === "not_free" && snapshot) text = (await this.whyNot(snapshot, start, service, [who!], named))[1];
    else if (reason === "calendar_unknown" || reason === "not_covered") {
      return reply("unknown", this.contacting(`I couldn't confirm that time on the calendar, so I haven't changed anything. ${lead}`.trimEnd()), { reason });
    } else return reply("refused", this.contacting(`I can't move it to then. ${lead}`), { reason });
    const p = localParts(start);
    const alt = await this.alternatives(day, p.hour * 60 + p.minute, null, service, named, doc);
    const offered = this.offer(alt.slots, alt.unreadable, service, named, `${text} ${lead}`);
    offered.reason = "new_time_unavailable";
    return offered;
  }

  private async commitMove(callId: string, token: string, row: StoredAppointment, staffId: string, startUtc: number, doc: CallDoc): Promise<Reply> {
    let outcome: Outcome;
    try {
      outcome = await this.store.reschedule(row.reference, {
        startUtc, staffId, expectedVersion: row.version, validate: async (rowsBetween, placement) => void (await this.judge.require(placement, rowsBetween)),
        requestKey: this.tokenKey("reschedule", callId, token), outbox: this.outbox,
      });
    } catch (error) {
      if (error instanceof StoreError) return this.unavailable();
      return reply("needs_review", this.contacting("Something went wrong and I can't confirm whether it was moved. Please don't rely on it."), { reason: "exception" });
    }
    if (["rescheduled", "replayed", "unchanged"].includes(outcome.status)) {
      const done = outcome.appointment!;
      const check = await this.verify(done, CONFIRMED, outcome.previous);
      if (check !== null) return check;
      if (outcome.status === "unchanged") return reply("unchanged", `Your ${this.label(done)} appointment is already on ${this.slot(done)} with ${this.staffName(done.staffId)}. Nothing was changed.`, { appointment: this.view(done) });
      if (outcome.status === "rescheduled") { this.countChange(doc); this.committed(); }
      return reply("rescheduled", `Done. Your ${this.label(done)} appointment is now on ${this.slot(done)}, with ${this.staffName(done.staffId)}. I checked the calendar and it's there.`, { appointment: this.view(done) });
    }
    if (outcome.status === "stale" && outcome.appointment !== null) {
      return reply("stale", `That appointment changed while we were talking, so I haven't moved anything. It is now ${this.label(outcome.appointment)} on ${this.slot(outcome.appointment)}.`, { appointment: this.view(outcome.appointment), reason: "changed_since_read_back" });
    }
    if (outcome.status === "unavailable" || outcome.status === "unknown") {
      const current = await this.store.get(row.reference);
      const where = current !== null && current.status === CONFIRMED ? ` Your ${this.label(current)} appointment is still on ${this.slot(current)}.` : "";
      return reply("unavailable", `I'm sorry, that new time was just taken, so I haven't changed anything.${where} Shall I look for other times?`, { reason: "changed_before_save" });
    }
    return reply("refused", this.contacting("I couldn't move that, so nothing was changed."), { reason: outcome.reason || outcome.status });
  }

  /** book_slot is the Booksy writer of the live line. It is not a route here and is refused outright if anything ever calls it. */
  async book_slot(callId: string, _args: Args): Promise<Reply> {
    void callId;
    return reply("refused", this.contacting("Booking isn't available that way on this line."), { reason: "no_booksy_writer" });
  }
}

export const LIFECYCLE_ROUTES: Readonly<Record<string, string>> = {
  "/tools/lookup_service": "lookup_service", "/tools/check_slot": "check_slot", "/tools/find_alternatives": "find_alternatives", "/tools/find_appointments": "find_appointments",
  "/tools/last_completed_visit": "last_completed_visit", "/tools/book_appointment": "book_appointment", "/tools/cancel_appointment": "cancel_appointment",
  "/tools/reschedule_appointment": "reschedule_appointment",
};

export type { DemoStaff };
export { casefold, joinChoices, joinNames, H_NO_SHOW };
