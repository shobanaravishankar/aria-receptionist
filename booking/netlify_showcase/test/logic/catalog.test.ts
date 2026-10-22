/* The menu lookup against the Python catalogue: thousands of customer phrasings, every outcome (which service, which variants, whether the length was matched) compared exactly. */

import assert from "node:assert/strict";
import { test } from "node:test";

import { BookableRegistry, isEligible } from "../../src/bookable.ts";
import { Catalog, durationHint, SHOWCASE_CATALOG, SHOWCASE_ITEMS, toPublic } from "../../src/catalog.ts";
import { demoRegistry, demoServices, ROSTER } from "../../src/dataset.ts";
import { nameOf, NEAR_MISS_NAME, ROSTER_ROWS, roleOf, validateRoster } from "../../src/roster.ts";
import { vectors } from "./support.ts";

const v = vectors("catalog");

test("every phrasing resolves exactly as the Python catalogue resolves it", () => {
  assert.ok(v.resolutions.length > 3500);
  const kinds: Record<string, number> = {};
  for (const r of v.resolutions) {
    const got = SHOWCASE_CATALOG.resolve(r.q);
    assert.deepEqual({ kind: got.kind, ids: got.items.map((i) => i.itemId), hintUnmatched: got.hintUnmatched }, { kind: r.kind, ids: r.ids, hintUnmatched: r.hintUnmatched }, JSON.stringify(r.q));
    kinds[r.kind] = (kinds[r.kind] ?? 0) + 1;
  }
  // the corpus must exercise every outcome, or agreement would prove little
  for (const kind of ["exact", "variants", "ambiguous", "unknown"]) assert.ok((kinds[kind] ?? 0) > 50, `${kind}: ${JSON.stringify(kinds)}`);
  assert.ok(v.resolutions.filter((r: any) => r.hintUnmatched).length > 20);
});

test("spoken lengths are read as Python reads them (longest match, no guessing, banker's rounding of fractions)", () => {
  assert.ok(v.hints.filter((h: any) => h.minutes !== null).length > 500 && v.hints.filter((h: any) => h.minutes === null).length > 500);
  for (const h of v.hints) assert.equal(durationHint(h.q), h.minutes, JSON.stringify(h.q));
  assert.equal(durationHint("one and a half hours"), 90);
  assert.equal(durationHint("30 or 60 minutes"), null);
  assert.equal(durationHint("0.375 hours"), 22); // Python's round() sends a half to the even neighbour
  assert.equal(durationHint(5 as unknown), null);
});

test("the menu, the families and the public shape of an item", () => {
  assert.deepEqual(SHOWCASE_CATALOG.familyNames(), v.families);
  assert.equal(SHOWCASE_ITEMS.length, 89);
  const swedish = SHOWCASE_CATALOG.get("massage-swedish-massage-60")!;
  assert.deepEqual(Object.keys(toPublic(swedish)), ["service_id", "name", "category", "duration_minutes", "price_usd", "description", "source_url", "retrieved_on"]);
  const priced = { ...swedish, originalPriceUsd: 120, notes: "Introductory price." };
  assert.deepEqual(Object.keys(toPublic(priced)).slice(-2), ["original_price_usd", "notes"]);
  assert.equal(toPublic(priced).original_price_usd, 120);
  assert.equal(SHOWCASE_CATALOG.get("nope"), null);
  assert.equal(SHOWCASE_CATALOG.get(7 as unknown), null);
  assert.throws(() => new Catalog([SHOWCASE_ITEMS[0], SHOWCASE_ITEMS[0]]), /duplicate/);
});

test("the demo's bookable services (ids, lengths, buffers, who may perform them, prices) are the Python derivation", () => {
  const got = demoServices(ROSTER).map(([s, category]) => ({
    serviceId: s.serviceId, booksyName: s.booksyName, duration: s.durationMinutes, bufferBefore: s.bufferBeforeMinutes, bufferAfter: s.bufferAfterMinutes,
    eligibleIds: [...s.eligibleStaffIds!].sort(), catalogItem: s.catalogItem, priceUsd: s.priceUsd, category,
  }));
  assert.deepEqual(got, v.services);
  assert.equal(got.length, 35);
  const registry = demoRegistry();
  assert.equal(registry.ids().length, 35);
  const service = registry.get("massage-swedish-massage-60")!;
  assert.equal(isEligible(service, "Marisol", "910001"), true);
  assert.equal(isEligible(service, "Marisol", ""), false); // a person whose id is unknown is never eligible
  assert.equal(isEligible(service, "Noor", "910005"), false); // a facial/lash specialist does not do massage
  assert.equal(registry.get("not-a-service"), null);
  assert.equal(registry.forCatalogItem("massage-swedish-massage-60")?.serviceId, "massage-swedish-massage-60");
});

test("the registry refuses a duplicate, a non-positive length, an unknown menu item and a length that disagrees with the menu", () => {
  const [service] = demoServices()[0];
  assert.throws(() => new BookableRegistry([service, service]), /duplicate/);
  assert.throws(() => new BookableRegistry([{ ...service, serviceId: "x", durationMinutes: 0, catalogItem: null }]), /positive/);
  assert.throws(() => new BookableRegistry([{ ...service, serviceId: "y", catalogItem: "no-such-item" }]), /unknown catalogue item/);
  assert.throws(() => new BookableRegistry([{ ...service, serviceId: "z", durationMinutes: service.durationMinutes + 5 }]), /differs/);
});

test("the roster: ten fictional first names, ids are the identity, and a near-miss name is not on it", () => {
  assert.equal(ROSTER_ROWS.length, 10);
  assert.equal(nameOf("910001"), "Marisol");
  assert.throws(() => nameOf("999999"));
  assert.equal(roleOf(ROSTER_ROWS[0]), "Massage therapist");
  assert.ok(!ROSTER_ROWS.some((r) => r.name === NEAR_MISS_NAME));
  assert.throws(() => validateRoster([ROSTER_ROWS[0], ROSTER_ROWS[0]]));
  assert.throws(() => validateRoster([{ ...ROSTER_ROWS[0], name: "Imogen Lin" }]));
  assert.throws(() => validateRoster([{ ...ROSTER_ROWS[0], categories: ["nonsense"] }]));
});
