/* PyRandom against CPython's random.Random: the same seeds, the same draws, bit for bit. */

import assert from "node:assert/strict";
import { test } from "node:test";

import { PyRandom, seededRandom } from "../../src/pyrandom.ts";
import { vectors } from "./support.ts";

const v = vectors("rng");

function draws(rng: PyRandom, ops: string[]): number[] {
  const out: number[] = [];
  for (let i = 0; i < 75; i++) {
    const op = ops[i % ops.length];
    out.push(op === "r" ? rng.random() : rng.choice(Array.from({ length: Number(op.slice(1)) }, (_, k) => k)));
  }
  return out;
}

test("integer seeds (including 32-bit and 64-bit edges) give Python's draws", () => {
  const edge = v.cases.filter((c: any) => !c.parts);
  assert.ok(edge.length >= 11);
  for (const c of edge) assert.deepEqual(draws(new PyRandom(BigInt(c.seed)), v.ops), c.draws, `seed ${c.seed}`);
});

test("the demo's per-(date, staff) seeds, derived from SHA-256 as in the Python dataset, give Python's draws", () => {
  const derived = v.cases.filter((c: any) => c.parts);
  assert.ok(derived.length > 500);
  for (const c of derived) {
    assert.deepEqual(draws(seededRandom(v.seedText, ...c.parts), v.ops), c.draws, `parts ${c.parts}`);
    assert.equal(seededRandom(v.seedText, ...c.parts) instanceof PyRandom, true);
  }
});

test("choice never leaves its range and floats stay in [0, 1)", () => {
  const rng = seededRandom("range-check", 1);
  for (let i = 0; i < 5000; i++) {
    const x = rng.random();
    assert.ok(x >= 0 && x < 1);
    assert.ok(rng.randbelow(7) < 7);
  }
  assert.equal(rng.randbelow(1), 0);
  assert.throws(() => rng.choice([]));
});
