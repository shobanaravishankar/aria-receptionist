/* Match a customer's words to the menu. Pure; no calendar, no network. Port of aria_booking/catalog/{models,lookup}.py and the helpers of website_data.py.
 *
 * The point is to NEVER guess: if the words fit several different services, or one service with several lengths or named variants (for example 2-week and 3-week refills), the
 * answer says so (`ambiguous` / `variants`) and the caller must be asked. A length the customer names that no variant has is reported (`hintUnmatched`), not silently replaced by
 * a different length. A spoken length is read by LONGEST match ("one and a half hours" is 90, never the "half hour" inside it), and a length phrase the code does not understand
 * yields no length at all, never a shorter one.
 *
 * Known difference from the Python source: digits are the ASCII 0-9 only (Python's \d also accepts other scripts' digits); a spoken-transcript path never produces those.
 */

import showcaseItems from "./data/showcase_items.json" with { type: "json" };

export interface CatalogItem {
  itemId: string; // stable canonical id, e.g. "massage-swedish-60"; the ONLY way a service is referred to by tools
  family: string; // the service as a customer names it, e.g. "Swedish Massage"
  category: string;
  name: string; // display name including the variant, e.g. "Swedish Massage, 1 hour"
  description: string;
  durationMinutes: number | null;
  priceUsd: number | null;
  sourceUrl: string;
  retrievedOn: string;
  originalPriceUsd: number | null;
  notes: string;
}

export type ResolutionKind = "exact" | "variants" | "ambiguous" | "unknown";
export interface Resolution {
  kind: ResolutionKind;
  items: CatalogItem[];
  hintUnmatched: boolean;
}
const resolution = (kind: ResolutionKind, items: CatalogItem[] = [], hintUnmatched = false): Resolution => ({ kind, items, hintUnmatched });

/** The variant part of the name ('2-week refill', '1 hour'), or '' for an item that has none. */
export function variantLabel(item: CatalogItem): string {
  const prefix = item.family + ", ";
  return item.name.startsWith(prefix) ? item.name.slice(prefix.length) : "";
}

/** What a tool may return about this item. Informational facts only; the keys are the tool contract's (snake_case). */
export function toPublic(item: CatalogItem): Record<string, unknown> {
  const data: Record<string, unknown> = {
    service_id: item.itemId, name: item.name, category: item.category, duration_minutes: item.durationMinutes, price_usd: item.priceUsd, description: item.description,
    source_url: item.sourceUrl, retrieved_on: item.retrievedOn,
  };
  if (item.originalPriceUsd !== null) data.original_price_usd = item.originalPriceUsd;
  if (item.notes) data.notes = item.notes;
  return data;
}

export function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

export function durationLabel(minutes: number | null): string {
  if (minutes === null) return "duration not published";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const parts: string[] = [];
  if (hours) parts.push(hours === 1 ? "1 hour" : `${hours} hours`);
  if (rest) parts.push(`${rest} minutes`);
  return parts.join(" ");
}

export const FILLER: ReadonlySet<string> = new Set(
  ("a an the of for with and or to please i id i'd would like want wish book booking appointment appointments schedule do you does have offer offering how much is are what whats " +
    "your service services treatment treatments session sessions price cost prices costs about tell me can could get need some any it there this that one").split(" "),
);

// Every way a length can be said, as (pattern, minutes); null means computed from the digits. Matches are reconciled by longest span, so a phrase never loses to a piece of itself.
const LENGTH_SOURCES: Array<[string, number | null]> = [
  ["two\\s+and\\s+a\\s+half\\s+hours?", 150], ["2\\s+and\\s+a\\s+half\\s+hours?", 150],
  ["(?:one|1)\\s+and\\s+a\\s+half\\s+hours?", 90], ["(?:an?\\s+)?hours?\\s+and\\s+a\\s+half", 90],
  ["half\\s+an\\s+hour", 30], ["(?<!and a )(?<!and )(?:a\\s+)?half\\s+hours?", 30], // never the tail of "<number> and a half hours"
  ["(?:one|an?)\\s+hours?", 60], ["two\\s+hours?", 120], ["three\\s+hours?", 180],
  ["(\\d+(?:\\.\\d+)?)\\s*-?\\s*(?:hr|hrs|hours?)", null], // digits + hours: computed
  ["(\\d+)\\s*-?\\s*(?:min|mins|minutes?)", null], // digits + minutes: computed
];
const LENGTHS = LENGTH_SOURCES.map(([p, minutes]) => ({ pattern: new RegExp(`\\b${p}\\b`, "g"), minutes }));
const HOURS_INDEX = LENGTHS.length - 2;
// Minutes said in words ("sixty minute massage"): a transcript spells a number out as often as it writes digits, and a length that is not understood must never be left behind as if it were
// part of a service's name.
const SPOKEN_MINUTES: Record<string, number> = {
  ten: 10, fifteen: 15, twenty: 20, "twenty five": 25, thirty: 30, "thirty five": 35, forty: 40, "forty five": 45, fifty: 50, "fifty five": 55,
  sixty: 60, "sixty five": 65, seventy: 70, "seventy five": 75, eighty: 80, ninety: 90,
};
const SPOKEN_WORD = "(?:ten|fifteen|(?:twenty|thirty|forty|fifty|sixty|seventy)(?:[\\s-]+five)?|eighty|ninety)";
const SPOKEN_RE = new RegExp(`\\b(${SPOKEN_WORD})\\s*-?\\s*(?:min|mins|minutes?)\\b`, "g");
const SPOKEN_ANY = new RegExp(`\\b${SPOKEN_WORD}\\b`, "g");
const WEEKS = /\b(\d+)\s*-?\s*weeks?\b/g;

/** Python's round(): halves go to the even neighbour. */
function pyRound(x: number): number {
  const floor = Math.floor(x);
  const diff = x - floor;
  if (diff < 0.5) return floor;
  if (diff > 0.5) return floor + 1;
  return floor % 2 === 0 ? floor : floor + 1;
}

export function words(text: string): string[] {
  return text.toLowerCase().replaceAll("'", "").match(/[a-z0-9]+/g) ?? [];
}

/** Lowercase, single-spaced, with 'N week(s)' turned into the single token 'Nw' so refills ('2-week refill') can be matched. */
function norm(text: string): string {
  return text.toLowerCase().replace(WEEKS, (_m, n) => `${n}w`).split(/\s+/).filter(Boolean).join(" ");
}

type LengthMatch = [start: number, end: number, minutes: number];

/** (start, end, minutes) for every spoken length, with any match inside a longer one dropped. */
function lengthMatches(low: string): LengthMatch[] {
  const found: LengthMatch[] = [];
  LENGTHS.forEach(({ pattern, minutes }, index) => {
    for (const m of low.matchAll(pattern)) {
      let here: number;
      if (minutes === null) {
        const value = Number(m[1]);
        here = index === HOURS_INDEX ? pyRound(value * 60) : Math.trunc(value);
      } else here = minutes;
      found.push([m.index!, m.index! + m[0].length, here]);
    }
  });
  for (const m of low.matchAll(SPOKEN_RE)) found.push([m.index!, m.index! + m[0].length, SPOKEN_MINUTES[m[1].replace(/[\s-]+/g, " ")]]);
  return found.filter((a) => !found.some((b) => b !== a && b[0] <= a[0] && a[1] <= b[1] && b[1] - b[0] > a[1] - a[0]));
}

const blankWeeks = (low: string) => low.replace(/\b\d+w\b/g, (m) => " ".repeat(m.length));

/** A length the customer mentioned, in minutes, or null. Null also when the words are unclear or name several lengths: only an unambiguous, understood length counts, and a bare number never does. */
export function durationHint(query: unknown): number | null {
  if (typeof query !== "string") return null;
  const lowNoWeeks = norm(query).replace(/\b\d+w\b/g, " "); // "2w" is a refill's name, not a length
  if ((lowNoWeeks.match(/\d+(?:\.\d+)?/g) ?? []).length > 1) return null; // two numbers ("30 or 60 minutes"): the customer has not chosen, so do not pick for them
  const matches = lengthMatches(lowNoWeeks);
  const uncoveredHalf = [...lowNoWeeks.matchAll(/\bhalf\b/g)].filter((m) => !matches.some(([s, e]) => s <= m.index! && m.index! + m[0].length <= e));
  if (uncoveredHalf.length) return null; // "half" in a phrase we do not understand: never guess a shorter length
  if ([...lowNoWeeks.matchAll(SPOKEN_ANY)].some((m) => !matches.some(([s, e]) => s <= m.index! && m.index! + m[0].length <= e))) return null; // "thirty or sixty minutes": a second number said in words
  const distinct = new Set(matches.map(([, , minutes]) => minutes));
  return distinct.size === 1 && matches.length === 1 ? [...distinct][0] : null;
}

// Length-like phrases we do NOT turn into minutes (durationHint returns null for them) must still not be mistaken for part of a service name.
const NUMBER = "(?:\\d+(?:\\.\\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|an?)";
const UNKNOWN_LENGTHS = [
  new RegExp(`\\b${NUMBER}\\s+and\\s+a\\s+half\\s+(?:hours?|hrs?)\\b`, "g"),
  new RegExp(`\\b${NUMBER}\\s+(?:hours?|hrs?|minutes?|mins?)\\b`, "g"),
  /\bhalf\s+(?:a|an|of\s+an?)\s+(?:hours?|day)\b/g,
];

function stripLengths(query: string): string {
  let low = norm(query);
  const sorted = lengthMatches(blankWeeks(low)).sort((a, b) => b[0] - a[0] || b[1] - a[1] || b[2] - a[2]);
  for (const [start, end] of sorted) low = low.slice(0, start) + " " + low.slice(end);
  for (const pattern of UNKNOWN_LENGTHS) low = low.replace(pattern, " ");
  return low.replace(SPOKEN_ANY, " "); // a number said in words that no length phrase took is not part of a service's name either
}

/** The customer's words with every length they said removed (lower case, single-spaced): what is left names the service. */
export function withoutLengths(query: unknown): string {
  return typeof query === "string" ? stripLengths(query) : "";
}

/** The words that name a treatment once every length, filler word and bare number is taken out. Empty when the customer has named a length (or nothing) but no treatment. */
export function treatmentWords(query: unknown): string[] {
  return typeof query === "string" ? words(stripLengths(query)).filter((w) => !FILLER.has(w) && !/^\d+$/.test(w)) : [];
}

/** A variant name that says more than the length ('2-week refill', 'initial set'); empty when it is just the length. */
export function descriptiveLabel(item: CatalogItem): string {
  const label = variantLabel(item);
  if (!label || (item.durationMinutes && label === durationLabel(item.durationMinutes))) return "";
  return label;
}

const isSubset = (small: Set<string>, ...bigs: Array<ReadonlySet<string>>) => [...small].every((w) => bigs.some((b) => b.has(w)));

export class Catalog {
  readonly items: readonly CatalogItem[];
  readonly listedAs: (item: CatalogItem) => string;
  private readonly byId = new Map<string, CatalogItem>();
  private readonly families = new Map<string, CatalogItem[]>();
  private readonly familyWords = new Map<string, Set<string>>();
  private readonly labelWords = new Map<CatalogItem, Set<string>>();
  private readonly variantWords = new Map<string, Set<string>>();

  constructor(items: readonly CatalogItem[], listedAs?: (item: CatalogItem) => string) {
    this.items = [...items];
    this.listedAs = listedAs ?? ((item) => `as listed on our website (${item.retrievedOn})`);
    for (const item of this.items) {
      this.byId.set(item.itemId, item);
      if (!this.families.has(item.family)) this.families.set(item.family, []);
      this.families.get(item.family)!.push(item);
    }
    if (this.byId.size !== this.items.length) throw new Error("duplicate catalogue ids");
    for (const name of this.families.keys()) this.familyWords.set(name, new Set(words(norm(name))));
    for (const item of this.items) this.labelWords.set(item, new Set(words(norm(descriptiveLabel(item)))));
    for (const [name, members] of this.families) this.variantWords.set(name, new Set(members.flatMap((i) => [...this.labelWords.get(i)!])));
  }

  get(itemId: unknown): CatalogItem | null {
    return typeof itemId === "string" ? (this.byId.get(itemId) ?? null) : null;
  }

  familyNames(): string[] {
    return [...this.families.keys()];
  }

  familyItems(name: string): CatalogItem[] {
    return this.families.get(name) ?? [];
  }

  resolve(query: unknown): Resolution {
    if (typeof query !== "string" || !query.trim()) return resolution("unknown");
    const direct = this.get(query.trim());
    if (direct !== null) return resolution("exact", [direct]);
    const hint = durationHint(query);
    const wanted = treatmentWords(query);
    if (!wanted.length) return resolution("unknown");
    const wantedSet = new Set(wanted);
    let matching = [...this.families.keys()].filter((name) => isSubset(wantedSet, this.familyWords.get(name)!, this.variantWords.get(name)!));
    if (matching.length > 1) { // "Lymphatic Drainage Massage" also fits "... with Wood Therapy": an exact name wins
      const exact = matching.filter((name) => slug(name) === slug(wanted.join(" ")));
      if (exact.length === 1) matching = exact;
    }
    if (!matching.length) return resolution("unknown");
    if (matching.length > 1) return resolution("ambiguous", matching.map((name) => this.families.get(name)![0]));
    const family = matching[0];
    let variants = [...this.families.get(family)!];
    const named = [...wantedSet].filter((w) => this.variantWords.get(family)!.has(w) && !this.familyWords.get(family)!.has(w)); // words that pick a variant, e.g. "refill", "2w"
    if (named.length) {
      const narrowed = variants.filter((v) => named.every((w) => this.labelWords.get(v)!.has(w)));
      if (narrowed.length) variants = narrowed;
    }
    if (hint !== null) {
      const chosen = variants.filter((v) => v.durationMinutes === hint);
      if (chosen.length === 1) return resolution("exact", [chosen[0]]);
      if (chosen.length) return resolution("variants", chosen);
      return resolution("variants", variants, true);
    }
    return variants.length === 1 ? resolution("exact", [variants[0]]) : resolution("variants", variants);
  }
}

export const SHOWCASE_ITEMS: readonly CatalogItem[] = showcaseItems as CatalogItem[];
export const SHOWCASE_CATALOG = new Catalog(SHOWCASE_ITEMS, () => "as listed on our menu");
