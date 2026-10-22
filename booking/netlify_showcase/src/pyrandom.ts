/* CPython's `random.Random`, as far as the demo schedule needs it: seeding from an integer, `random()` and `choice()`. The generated demo calendar is a function of these draws, so
 * the port must make exactly the same ones (Mersenne Twister MT19937, `init_by_array` seeding from the integer's 32-bit words, 53-bit floats, and `choice` by rejection sampling
 * of `getrandbits(k)`). Verified against Python's own output in test/logic/pyrandom.test.ts. Synchronous; no dependency. */

import { createHash } from "node:crypto";

const N = 624;
const M = 397;

export class PyRandom {
  private readonly mt = new Uint32Array(N);
  private index = N;

  /** `random.Random(seed)` for a non-negative integer seed. */
  constructor(seed: bigint) {
    if (seed < 0n) throw new Error("negative seeds are not used by the demo");
    const key: number[] = [];
    for (let n = seed; n > 0n; n >>= 32n) key.push(Number(n & 0xffffffffn));
    if (!key.length) key.push(0);
    this.initByArray(key);
  }

  private initGenrand(seed: number): void {
    const mt = this.mt;
    mt[0] = seed >>> 0;
    for (let i = 1; i < N; i++) mt[i] = (Math.imul(1812433253, mt[i - 1] ^ (mt[i - 1] >>> 30)) + i) >>> 0;
    this.index = N;
  }

  private initByArray(key: number[]): void {
    const mt = this.mt;
    this.initGenrand(19650218);
    let i = 1;
    let j = 0;
    for (let k = Math.max(N, key.length); k > 0; k--) {
      mt[i] = ((mt[i] ^ Math.imul(mt[i - 1] ^ (mt[i - 1] >>> 30), 1664525)) + key[j] + j) >>> 0;
      i++;
      j++;
      if (i >= N) { mt[0] = mt[N - 1]; i = 1; }
      if (j >= key.length) j = 0;
    }
    for (let k = N - 1; k > 0; k--) {
      mt[i] = ((mt[i] ^ Math.imul(mt[i - 1] ^ (mt[i - 1] >>> 30), 1566083941)) - i) >>> 0;
      i++;
      if (i >= N) { mt[0] = mt[N - 1]; i = 1; }
    }
    mt[0] = 0x80000000;
    this.index = N;
  }

  private twist(): void {
    const mt = this.mt;
    const mix = (u: number, v: number) => ((u & 0x80000000) | (v & 0x7fffffff)) >>> 0;
    const apply = (u: number, v: number) => { const y = mix(u, v); return (y >>> 1) ^ ((v & 1) ? 0x9908b0df : 0); };
    let k = 0;
    for (; k < N - M; k++) mt[k] = (mt[k + M] ^ apply(mt[k], mt[k + 1])) >>> 0;
    for (; k < N - 1; k++) mt[k] = (mt[k + M - N] ^ apply(mt[k], mt[k + 1])) >>> 0;
    mt[N - 1] = (mt[M - 1] ^ apply(mt[N - 1], mt[0])) >>> 0;
    this.index = 0;
  }

  private nextUint32(): number {
    if (this.index >= N) this.twist();
    let y = this.mt[this.index++];
    y ^= y >>> 11;
    y ^= (y << 7) & 0x9d2c5680;
    y ^= (y << 15) & 0xefc60000;
    y ^= y >>> 18;
    return y >>> 0;
  }

  /** A float in [0, 1) from 53 random bits, as Python's `random()`. */
  random(): number {
    const a = this.nextUint32() >>> 5;
    const b = this.nextUint32() >>> 6;
    return (a * 67108864 + b) * (1 / 9007199254740992);
  }

  /** `getrandbits(k)` for 1 <= k <= 32. */
  private getrandbits(k: number): number {
    return this.nextUint32() >>> (32 - k);
  }

  /** An integer in [0, n) by Python's rejection sampling (`_randbelow_with_getrandbits`). */
  randbelow(n: number): number {
    if (n <= 0) return 0;
    const k = n.toString(2).length;
    let r = this.getrandbits(k);
    while (r >= n) r = this.getrandbits(k);
    return r;
  }

  choice<T>(seq: readonly T[]): T {
    if (!seq.length) throw new Error("cannot choose from an empty sequence");
    return seq[this.randbelow(seq.length)];
  }
}

/** The demo's per-(date, staff) generator: `random.Random(int.from_bytes(sha256("|".join((SEED, *parts))).digest()[:8], "big"))`. */
export function seededRandom(seed: string, ...parts: Array<string | number>): PyRandom {
  const digest = createHash("sha256").update([seed, ...parts].map(String).join("|")).digest();
  return new PyRandom(digest.readBigUInt64BE(0));
}
