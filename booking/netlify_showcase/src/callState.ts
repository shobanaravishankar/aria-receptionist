/* What the voice tools remember WITHIN one call, and the confirmation tokens they hand out. In the Python backend this lives in process memory (`_tokens`, `_numbers`, `_failed`, `_changes`, the
 * identity a call settled on, the start time the caller last asked for, the history attempts); a Netlify Function has no memory between invocations, so it is a small store of its own
 * (CallStore): in memory for tests, in Postgres in production. Nothing here outlives its expiry and nothing carries from one call to another. */

import type { Instant } from "./time.ts";

export interface Identity {
  phone: string;
  name: string | null;
  key: string | null;
  clientId: number | null;
}
/** Everything one call has used so far. Numbers tried are kept as digests, never as the numbers. */
export interface CallDoc {
  numbers: string[];
  failed: number; // failed references and failed name matches
  changes: number; // changes made on this call
  attempts: number; // last-visit lookups made on this call
  preferred: number | null; // the start time (minutes since midnight) the caller last asked for in THIS call
  identity: Identity | null;
  /** The revision this document was loaded at (0: the call has no row yet). A save only succeeds against the revision it was loaded at; see CallConflict. */
  rev: number;
}
export const emptyCall = (): CallDoc => ({ numbers: [], failed: 0, changes: 0, attempts: 0, preferred: null, identity: null, rev: 0 });

/** Another invocation of the same call saved first. Nothing was written; the caller loads again and redoes its step against the newer document. */
export class CallConflict extends Error {}

export interface Pending {
  kind: string; // book / cancel / reschedule / pref_set / pref_remove
  callId: string;
  payload: Record<string, any>;
  expires: Instant;
}

export interface CallStore {
  load(callId: string): Promise<CallDoc>;
  /** Compare-and-set: succeeds only if nobody saved this call since `doc` was loaded, then moves `doc.rev` on; otherwise throws CallConflict and writes nothing. */
  save(callId: string, doc: CallDoc): Promise<void>;
  /** A new read-back replaces this call's earlier one of the same kind; expired ones are dropped. */
  putPending(token: string, pending: Pending, now: Instant): Promise<void>;
  getPending(token: string): Promise<Pending | null>;
}

export class MemoryCallStore implements CallStore {
  private readonly calls = new Map<string, CallDoc>();
  private readonly tokens = new Map<string, Pending>();

  async load(callId: string): Promise<CallDoc> {
    return structuredClone(this.calls.get(callId) ?? emptyCall());
  }
  async save(callId: string, doc: CallDoc): Promise<void> {
    if ((this.calls.get(callId)?.rev ?? 0) !== doc.rev) throw new CallConflict(callId);
    doc.rev += 1;
    this.calls.set(callId, structuredClone(doc));
  }
  async putPending(token: string, pending: Pending, now: Instant): Promise<void> {
    for (const [key, p] of this.tokens) if (p.expires <= now || (p.callId === pending.callId && p.kind === pending.kind)) this.tokens.delete(key);
    this.tokens.set(token, structuredClone(pending));
  }
  async getPending(token: string): Promise<Pending | null> {
    const p = this.tokens.get(token);
    return p ? structuredClone(p) : null;
  }
}
