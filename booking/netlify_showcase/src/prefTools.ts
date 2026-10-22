/* The voice tools for a returning client's PREFERRED TECHNICIAN per service family (demo only; an addition to the simulated lifecycle). Port of aria_booking/appointments/preference_tools.py.
 * `PreferenceTools` is the lifecycle tool set plus three routes: get_preferences, save_preference and remove_preference. Nothing else changes: booking, cancelling and moving do not read or
 * write a preference, so a one-off choice of technician, a past visit or an ordinary booking can never create or change one.
 *  - Who is asking: the same demo match as the other tools. Two clients who share a household number never share a preference.
 *  - Nothing is saved or removed without a read-back the caller confirms; a confirmed request whose details differ gets a fresh read-back; a repeated confirmed request changes nothing.
 *  - A preference is only WHO. It never guarantees a time. */

import { isEligible, staffKey } from "./bookable.ts";
import type { CallDoc } from "./callState.ts";
import type { CatalogItem } from "./catalog.ts";
import { joinNames } from "./speech.ts";
import { NOTHING, REMOVED, REPLAYED, SAVED, STALE, StoreError, UNCHANGED } from "./store.ts";
import type { Expected, Preference } from "./store.ts";
import { LIFECYCLE_OK, LIFECYCLE_ROUTES, LifecycleTools, MAX_CHANGES_PER_CALL } from "./tools.ts";
import type { ToolOptions } from "./tools.ts";
import type { DemoStaff } from "./dayplan.ts";

type Args = Record<string, any>;
type Reply = Record<string, any>;

export const PREFERENCE_ROUTES: Readonly<Record<string, string>> = {
  ...LIFECYCLE_ROUTES,
  "/tools/get_preferences": "get_preferences", "/tools/save_preference": "save_preference", "/tools/remove_preference": "remove_preference",
};
const PREFERENCE_OK: ReadonlySet<string> = new Set([...LIFECYCLE_OK, "preferences_listed", "no_preferences", "preference_for_service", "preference_saved", "preference_unchanged", "preference_removed", "nothing_to_remove", "preference_current"]);
const GUARANTEE = "It is only who you would like; it never guarantees a time, so I still check that person's hours and appointments for the full length.";
const SPOKEN_TEXT_MAX = 40;

const slug = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
const reply = (status: string, speak: string, data: Reply = {}): Reply => ({ status, ok: PREFERENCE_OK.has(status), speak, ...data });
/** A caller's own words, safe to say back: one short line. */
const echo = (text: unknown) => String(text).split(/\s+/).filter(Boolean).join(" ").slice(0, SPOKEN_TEXT_MAX);

interface Family {
  id: string;
  name: string;
  service: NonNullable<ReturnType<LifecycleTools["dataset"]["registry"]["get"]>>;
}

export class PreferenceTools extends LifecycleTools {
  private readonly families = new Map<string, [string, CatalogItem]>();

  constructor(opts: ToolOptions) {
    super(opts);
    for (const item of this.catalog.items) {
      const id = `${item.category}-${slug(item.family)}`;
      if (!this.families.has(id)) this.families.set(id, [item.family, item]);
    }
  }

  // ---- which service, which technician
  /** (family, null) for exactly one service family the calendar can check, else (null, a reply that asks for the one thing that would settle it). */
  private family(query: unknown): { family: Family | null; problem: Reply | null } {
    if (typeof query !== "string" || !query.trim()) return { family: null, problem: reply("needs_clarification", "Which service is that for?", { reason: "service_required" }) };
    const group = this.groupAnswer(query);
    if (group !== null) {
      return { family: null, problem: reply("needs_clarification", `Which one is that for? For example ${joinNames(group.featured)}.`, { reason: "service_not_specific", examples: group.featured }) };
    }
    const resolution = this.catalog.resolve(query);
    if (resolution.kind === "unknown") return { family: null, problem: reply("needs_clarification", "I don't have that service on the menu. Which service did you mean?", { reason: "unknown_service" }) };
    if (resolution.kind === "ambiguous") {
      const names = [...new Set(resolution.items.map((i) => i.family))].slice(0, 8);
      return { family: null, problem: reply("needs_clarification", `Several services fit that: ${joinNames(names)}. Which one did you mean?`, { reason: "service_ambiguous", families: names }) };
    }
    const item = resolution.items[0];
    const service = this.dataset.registry.forCatalogItem(item.itemId);
    if (service === null || !service.verified) {
      return { family: null, problem: reply("needs_clarification", `A preferred technician is only kept for services that are booked with a technician, and ${item.family} isn't one I can book.`, { reason: "not_bookable" }) };
    }
    return { family: { id: `${item.category}-${slug(item.family)}`, name: item.family, service }, problem: null };
  }
  private eligibleFor(service: Family["service"]): DemoStaff[] {
    return this.dataset.roster.filter((s) => isEligible(service, s.name, s.staffId));
  }
  private technician(family: Family, text: unknown): { person: DemoStaff | null; problem: Reply | null } {
    const eligible = this.eligibleFor(family.service);
    const names = joinNames(eligible.map((s) => s.name));
    const list = eligible.map((s) => s.name);
    if (typeof text !== "string" || !text.trim()) {
      return { person: null, problem: reply("needs_clarification", `Which technician would you like saved? For ${family.name} it's ${names}.`, { reason: "staff_required", technicians: list }) };
    }
    const match = this.dataset.roster.find((s) => staffKey(s.name) === staffKey(text));
    if (match === undefined) {
      return { person: null, problem: reply("needs_clarification", `I don't know a technician called ${echo(text)}. For ${family.name} it's ${names}. Which one would you like?`, { reason: "unknown_staff", technicians: list }) };
    }
    if (!isEligible(family.service, match.name, match.staffId)) {
      return { person: null, problem: reply("needs_clarification", `${match.name} doesn't do ${family.name}. For ${family.name} it's ${names}. Which one would you like?`, { reason: "staff_not_eligible", technicians: list }) };
    }
    return { person: match, problem: null };
  }
  private async record(doc: CallDoc, args: Args): Promise<{ client: { clientId: number; name: string } | null; problem: Reply | null }> {
    const { record, problem } = await this.client(doc, args);
    if (problem !== null) {
      if (problem.reason === "no_match") {
        return { client: null, problem: reply("not_found", this.contacting("I can't find an account for that number, so I can't keep a preferred technician for it."), { reason: "no_match" }) };
      }
      return { client: null, problem };
    }
    return { client: record, problem: null };
  }
  private show(staffId: string): string | null {
    return this.dataset.roster.find((s) => s.staffId === staffId)?.name ?? null;
  }

  // ---- READ
  async get_preferences(callId: string, args: Args): Promise<Reply> {
    return this.run(callId, (doc) => this.get(args ?? {}, doc));
  }
  private async get(args: Args, doc: CallDoc): Promise<Reply> {
    const { client, problem } = await this.record(doc, args);
    if (problem) return problem;
    const serviceText = args.service;
    let family: Family | null = null;
    if (typeof serviceText === "string" && serviceText.trim()) {
      const found = this.family(serviceText);
      if (found.problem) return found.problem;
      family = found.family;
    }
    let held: Preference[];
    try {
      held = await this.store.preferences.forClient(client!.clientId);
    } catch (error) {
      if (error instanceof StoreError) return this.unavailable();
      throw error;
    }
    if (family !== null) {
      const mine = held.find((p) => p.familyId === family!.id);
      if (!mine) {
        return reply("no_preferences", `I don't have a preferred technician saved for ${family.name}.`, {
          service: family.name, preferred: null,
          instruction: `Nothing is saved for ${family.name}. Offer to check availability as usual; save one only if the caller asks. Never save one on your own.`,
        });
      }
      const person = this.show(mine.staffId);
      const still = Boolean(person && isEligible(family.service, person, mine.staffId));
      if (!person || !still) {
        return reply("preference_for_service", `Your saved choice for ${family.name} is a technician who isn't available to book for it any more, so I'd suggest someone else for now.`, {
          service: family.name, preferred: person, eligible: false,
          instruction: "Do NOT change or remove the saved preference. Offer alternatives from check_slot or find_alternatives; the caller may ask to change it.",
        });
      }
      return reply("preference_for_service", `Your preferred technician for ${family.name} is ${person}.`, {
        service: family.name, preferred: person, eligible: true,
        instruction: `If they are booking ${family.name}, offer ${person} first ('${person}, your usual?') and call check_slot with staff=${person}. ${GUARANTEE} If ${person} isn't free or doesn't ` +
          "fit, say why from the tool and offer alternatives; do NOT change or remove the saved preference. If they ask for someone else for this appointment, book that person " +
          "and leave the saved preference alone.",
      });
    }
    if (!held.length) {
      return reply("no_preferences", "I don't have any preferred technicians saved for you.", {
        preferences: [], instruction: "Nothing is saved. Save one only if the caller asks, in their own words. Never save one on your own.",
      });
    }
    const listed = held.map((p) => ({ service: (this.families.get(p.familyId) ?? [p.familyId])[0], technician: this.show(p.staffId) ?? "a technician who is no longer on the team" }));
    const spoken = listed.map((entry) => `${entry.technician} for ${entry.service}`).join("; ");
    return reply("preferences_listed", `Your preferred technicians: ${spoken}.`, { preferences: listed, instruction: `Read these as they are. ${GUARANTEE} Do not add or change any unless the caller asks.` });
  }

  // ---- SAVE or CHANGE
  async save_preference(callId: string, args: Args): Promise<Reply> {
    return this.run(callId, (doc) => this.save(callId, args ?? {}, doc));
  }
  private async save(callId: string, args: Args, doc: CallDoc): Promise<Reply> {
    const { client, problem } = await this.record(doc, args);
    if (problem) return problem;
    const f = this.family(args.service);
    if (f.problem) return f.problem;
    const family = f.family!;
    const t = this.technician(family, args.staff);
    if (t.problem) return t.problem;
    const person = t.person!;
    let current: Preference | null;
    try {
      current = await this.store.preferences.get(client!.clientId, family.id);
    } catch (error) {
      if (error instanceof StoreError) return this.unavailable();
      throw error;
    }
    if (current !== null && current.staffId === person.staffId) {
      return reply("preference_unchanged", `${person.name} is already saved as your preferred technician for ${family.name}. Nothing changed.`, { service: family.name, preferred: person.name });
    }
    if (doc.changes >= MAX_CHANGES_PER_CALL) return reply("refused", this.contacting("I can't change any more on this call."), { reason: "too_many_changes" });
    const wanted = { client: client!.clientId, family: family.id, staff: person.staffId, current: current ? current.staffId : null };
    const pending = args.confirmed === true ? await this.takePending("pref_set", callId, args.confirmation_token) : null;
    if (pending !== null && (["client", "family", "staff"] as const).every((k) => pending.payload[k] === wanted[k])) {
      if (pending.payload.current !== wanted.current) return this.setReadback(callId, wanted, family, person, current, true); // it changed after the read-back: read it back again
      return this.commitSet(callId, args.confirmation_token, client!, family, person, doc, basisOf(pending.payload));
    }
    return this.setReadback(callId, wanted, family, person, current, false);
  }
  private async setReadback(callId: string, wanted: Record<string, any>, family: Family, person: DemoStaff, current: Preference | null, stale: boolean): Promise<Reply> {
    const token = await this.newPending("pref_set", callId, { ...wanted, version: current ? current.version : null }); // the version it was read back at: the write applies to exactly that state
    const lead = stale ? "That has changed since I read it out. " : "";
    const was = current ? this.show(current.staffId) : null;
    const what = current ? `change your preferred technician for ${family.name} from ${was} to ${person.name}` : `save ${person.name} as your preferred technician for ${family.name}`;
    return reply("confirmation_required", `${lead}To confirm: ${what}. ${GUARANTEE} Shall I go ahead?`, {
      confirmation_token: token, service: family.name, preferred: person.name, previous: was,
      instruction: "Read this back and wait for a clear yes. If they say no, say that nothing was saved and stop. Then call save_preference again with the same number, service and " +
        "technician, the same confirmation_token and confirmed=true. Only say it is saved after the tool says so.",
    });
  }
  private async commitSet(callId: string, token: string, client: { clientId: number }, family: Family, person: DemoStaff, doc: CallDoc, basis: Expected | null): Promise<Reply> {
    let outcome;
    let now: Preference | null;
    try {
      outcome = await this.store.preferences.set(client.clientId, family.id, person.staffId, { requestKey: this.tokenKey("pref_set", callId, token), expected: basis });
      now = await this.store.preferences.get(client.clientId, family.id);
    } catch (error) {
      if (error instanceof StoreError) return this.unavailable();
      return reply("needs_review", this.contacting("Something went wrong and I can't confirm whether that was saved. Please don't rely on it."), { reason: "exception" });
    }
    if (outcome.status === STALE) return this.setReadback(callId, { client: client.clientId, family: family.id, staff: person.staffId, current: now ? now.staffId : null }, family, person, now, true); // changed between the check and the write: read it back again
    if (outcome.status === REPLAYED && (now === null || now.staffId !== person.staffId)) { // an earlier confirmation of this very request, since overtaken by a newer one
      const kept = now ? this.show(now.staffId) : null;
      return reply("preference_current", kept ? `Your preferred technician for ${family.name} is now ${kept}.` : `You have no preferred technician saved for ${family.name} now.`, { service: family.name, preferred: kept });
    }
    if (![SAVED, UNCHANGED, REPLAYED].includes(outcome.status) || now === null || now.staffId !== person.staffId) {
      return reply("needs_review", this.contacting("I can't confirm that was saved. Please don't rely on it."), { reason: "not_verified" });
    }
    if (outcome.status === SAVED) this.countChange(doc);
    return reply(
      outcome.status !== UNCHANGED ? "preference_saved" : "preference_unchanged",
      outcome.status !== UNCHANGED ? `Done: ${person.name} is saved as your preferred technician for ${family.name}. Your appointments haven't changed.`
        : `${person.name} was already saved as your preferred technician for ${family.name}. Nothing changed.`,
      { service: family.name, preferred: person.name },
    );
  }

  // ---- REMOVE
  async remove_preference(callId: string, args: Args): Promise<Reply> {
    return this.run(callId, (doc) => this.remove(callId, args ?? {}, doc));
  }
  private async remove(callId: string, args: Args, doc: CallDoc): Promise<Reply> {
    const { client, problem } = await this.record(doc, args);
    if (problem) return problem;
    const f = this.family(args.service);
    if (f.problem) return f.problem;
    const family = f.family!;
    let current: Preference | null;
    try {
      current = await this.store.preferences.get(client!.clientId, family.id);
    } catch (error) {
      if (error instanceof StoreError) return this.unavailable();
      throw error;
    }
    if (current === null) return reply("nothing_to_remove", `You don't have a preferred technician saved for ${family.name}, so there is nothing to remove.`, { service: family.name });
    if (doc.changes >= MAX_CHANGES_PER_CALL) return reply("refused", this.contacting("I can't change any more on this call."), { reason: "too_many_changes" });
    const wanted = { client: client!.clientId, family: family.id, current: current.staffId };
    const pending = args.confirmed === true ? await this.takePending("pref_remove", callId, args.confirmation_token) : null;
    if (pending !== null && (["client", "family", "current"] as const).every((k) => pending.payload[k] === wanted[k])) return this.commitRemove(callId, args.confirmation_token, client!, family, doc, basisOf(pending.payload));
    if (pending !== null) return this.removeReadback(callId, wanted, family, current, true); // confirmed, but what is saved is not what was read out
    return this.removeReadback(callId, wanted, family, current, false);
  }
  private async removeReadback(callId: string, wanted: Record<string, any>, family: Family, current: Preference, stale: boolean): Promise<Reply> {
    const token = await this.newPending("pref_remove", callId, { ...wanted, version: current.version }); // the version it was read back at: the removal applies to exactly that state
    const lead = stale ? "That has changed since I read it out. " : "";
    return reply("confirmation_required", `${lead}To confirm: remove ${this.show(current.staffId) ?? "your saved technician"} as your preferred technician for ${family.name}. Your appointments won't change. Shall I go ahead?`, {
      confirmation_token: token, service: family.name,
      instruction: "Read this back and wait for a clear yes. If they say no, say that nothing was removed and stop. Then call remove_preference again with the same number and service, " +
        "the same confirmation_token and confirmed=true. Only say it is removed after the tool says so.",
    });
  }
  private async commitRemove(callId: string, token: string, client: { clientId: number }, family: Family, doc: CallDoc, basis: Expected | null): Promise<Reply> {
    let outcome;
    let gone: boolean;
    try {
      outcome = await this.store.preferences.remove(client.clientId, family.id, { requestKey: this.tokenKey("pref_remove", callId, token), expected: basis });
      gone = (await this.store.preferences.get(client.clientId, family.id)) === null;
    } catch (error) {
      if (error instanceof StoreError) return this.unavailable();
      return reply("needs_review", this.contacting("Something went wrong and I can't confirm whether that was removed. Please don't rely on it."), { reason: "exception" });
    }
    if (outcome.status === STALE && outcome.preference) return this.removeReadback(callId, { client: client.clientId, family: family.id, current: outcome.preference.staffId }, family, outcome.preference, true); // changed between the check and the write: read it back again
    if (![REMOVED, NOTHING, REPLAYED].includes(outcome.status)) return reply("needs_review", this.contacting("I can't confirm that was removed. Please don't rely on it."), { reason: "not_verified" });
    if (outcome.status === REPLAYED && !gone) return reply("preference_current", `You have a preferred technician saved for ${family.name} now, so I haven't removed it.`, { service: family.name }); // an earlier removal, since overtaken by a newer save
    if (!gone) return reply("needs_review", this.contacting("I can't confirm that was removed. Please don't rely on it."), { reason: "not_verified" });
    if (outcome.status === REMOVED) this.countChange(doc);
    return reply("preference_removed", `Done: your preferred technician for ${family.name} is removed. Your appointments haven't changed.`, { service: family.name });
  }
}

/** The saved preference a read-back was made against (its technician and version), or null when none was saved then. */
function basisOf(payload: Record<string, any>): Expected | null {
  return typeof payload.current === "string" && typeof payload.version === "number" ? { staffId: payload.current, version: payload.version } : null;
}

function sortKeys(o: Record<string, any>): Record<string, any> {
  return Object.fromEntries(Object.entries(o).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}
