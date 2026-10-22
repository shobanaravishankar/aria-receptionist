/* How Aria answers 'what kinds of massage / facials / waxing... do you have': a FEW featured choices, ONE simple question about what the caller wants, a wait, and then ONE recommendation.
 * Port of aria_booking/showcase/discovery.py. Nothing here ranks anything; the featured examples are editorial, not popularity. A length does not hide a group: "a sixty minute massage" is
 * still the group 'massage', and the length is kept apart, not chosen for. */

import { Catalog, durationHint, withoutLengths } from "./catalog.ts";

export const FEATURED_LIMIT = 3;

// category -> the ONE goal question asked after the featured choices (every category the menu has needs one: a test checks)
export const QUESTIONS: Record<string, string> = {
  massage: "Are you looking to relax, ease everyday muscle tension, or focus on something else?",
  facial: "Are you thinking about hydration, brightness, or the appearance of fine lines?",
  lash: "Are you thinking about your lashes or your brows, and what kind of look do you want?",
  waxing: "Which area would you like waxed?",
  "head-spa": "Are you after a calming scalp massage, or a deeper cleanse for your scalp?",
  "body-scrub": "Is this for your whole body or just your back, and do you want a polish or something more relaxing?",
  package: "Is there one treatment you'd like to start with, and how much time do you have?",
  couples: "Would the two of you like massages, facials, or a mix?",
  membership: "How often do you expect to visit?",
};

// category -> the family names to feature, when the first three on the menu are not the best examples to give. Editorial, NOT popularity data.
export const FEATURED: Record<string, string[]> = {
  massage: ["Swedish Massage", "Deep Tissue Massage", "Hot Stone Massage"],
  facial: ["Express Facial", "Signature Facial", "Deep Hydration Facial"],
};

// How a caller names a whole group (never a specific treatment: "Swedish massage" is not a group word, "massage" is).
export const GROUP_WORDS: Record<string, string[]> = {
  massage: ["massage", "massages"],
  facial: ["facial", "facials"],
  lash: ["lash", "lashes", "eyelash", "eyelashes", "lash and brow", "lashes and brows", "eyelashes and brows"],
  waxing: ["wax", "waxing", "waxes"],
  "head-spa": ["head spa", "head spas", "scalp", "scalp treatment", "scalp treatments"],
  "body-scrub": ["body scrub", "body scrubs", "scrub", "scrubs", "body polish", "body polishes"],
  package: ["combo", "combos", "package", "packages", "spa package", "spa packages"],
  couples: ["couples", "couple", "couples treatment", "couples treatments", "couples package", "couples packages"],
  membership: ["membership", "memberships", "bundle", "bundles", "visit bundle", "visit bundles", "visit package", "visit packages"],
};
// how a group reads after "We have 11 kinds of ..."
export const LABELS: Record<string, string> = {
  massage: "massage", facial: "facials", lash: "lash and brow services", waxing: "waxing", "head-spa": "head spa treatments", "body-scrub": "body scrubs",
  package: "combos", couples: "couples treatments", membership: "visit bundles",
};
const FILLER: ReadonlySet<string> = new Set(
  ("what which kinds kind types type sorts sort of do you have offer a an the any options option are there is available services service treatments treatment your can i get tell me about show list " +
    "all different variety varieties choices choice and or for please d ll m ve to like want would love looking book booking appointment appointments schedule need interested").split(" "),
);

/** The words that name a group: lower case, no punctuation, no filler ('what kinds of ... do you have'). */
function core(text: string): string {
  return (text || "").toLowerCase().replace(/[^a-z0-9 ]+/g, " ").split(/\s+/).filter((w) => w && !FILLER.has(w)).join(" ");
}

/** The ONE group a caller is naming as a whole, or null. A specific treatment ('swedish massage') is not a group. */
export function groupOf(query: unknown): string | null {
  const rest = core(withoutLengths(query)); // "a sixty minute massage" is still the group 'massage'
  if (!rest) return null;
  const hits = Object.entries(GROUP_WORDS).filter(([, names]) => names.some((n) => core(n) === rest)).map(([category]) => category);
  return hits.length === 1 ? hits[0] : null;
}

/** The menu's family names for one category, in menu order. */
export function families(catalog: Catalog, category: string): string[] {
  return [...new Set(catalog.items.filter((i) => i.category === category).map((i) => i.family))];
}

/** At most three family names for this category: the configured ones (each must be on the menu), otherwise the first three on the menu. */
export function featured(catalog: Catalog, category: string, configured: Record<string, string[]> = FEATURED): string[] {
  const onMenu = families(catalog, category);
  if (category in configured) {
    const chosen = configured[category];
    const unknown = chosen.filter((name) => !onMenu.includes(name));
    if (unknown.length) throw new Error(`featured ${category} choices are not on the menu: ` + unknown.join(", "));
    if (chosen.length < 1 || chosen.length > FEATURED_LIMIT) throw new Error(`a featured list holds one to ${FEATURED_LIMIT} choices`);
    return [...chosen];
  }
  return onMenu.slice(0, FEATURED_LIMIT);
}

/** The `lookup_service` hook of the demo: a bare group question gets at most three featured examples and the one question, whatever the model does with its own prompt. */
export function makeGroupAnswer(catalog: Catalog, configured?: Record<string, string[]>): (query: string) => Record<string, any> | null {
  return (query) => {
    const category = groupOf(query);
    if (category === null || !families(catalog, category).length || !(category in QUESTIONS)) return null;
    const names = featured(catalog, category, configured);
    const total = families(catalog, category).length;
    const examples = names.length === 1 ? names[0] : names.slice(0, -1).join(", ") + " or " + names[names.length - 1];
    const answer: Record<string, any> = {
      status: "service_group", category, featured: names, question: QUESTIONS[category], total_kinds: total,
      speak: `We have ${total} kinds of ${LABELS[category]}. For example, ${examples}. ${QUESTIONS[category]}`,
      instruction: "Say this sentence, then STOP and wait for the caller's answer. Do not list more kinds unless the caller asks for them; then recommend ONE treatment from the menu.",
    };
    const minutes = durationHint(query);
    if (minutes !== null) { // the caller already gave a length: keep it, but a length does not choose a treatment
      answer.requested_minutes = minutes;
      answer.instruction += ` The caller asked for ${minutes} minutes: remember that length, but it does NOT choose a treatment. Do not pick one yourself and do not check the calendar until the caller has chosen which treatment they want.`;
    }
    return answer;
  };
}
