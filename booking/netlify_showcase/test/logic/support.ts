/* Shared helpers for the logic tests: load a golden-vector file produced by the Python oracle (oracle/gen_*.py). */

import { readFileSync } from "node:fs";

export function vectors<T = any>(name: string): T {
  return JSON.parse(readFileSync(new URL(`../vectors/${name}.json`, import.meta.url), "utf8")) as T;
}
