/* The typed-chat relay against a FAKE Retell server on this machine (no network, no paid call, no real key): sessions, validation, isolation of five (and twenty-five) simultaneous visitors, every
 * provider failure, rate limits and the guarantee that no secret or provider text reaches a visitor. The page itself is tested separately in a headless browser. */

import assert from "node:assert/strict";
import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { after, before, beforeEach, describe, test } from "node:test";

import { agentWords, handle, MAX_MESSAGES, MAX_TEXT, resetLimits, sign, TOKEN_TTL_MS, verify } from "../src/chat.ts";
import type { ChatRequest, Env } from "../src/chat.ts";

const KEY = "key_test_not_a_real_secret_0123456789";
const AGENT = "agent_chat_test";
const NOW = 1_800_000_000_000;
let server: ReturnType<typeof createServer>;
let base = "";
let mode: Record<string, any> = {};
let calls: Array<{ path: string; auth: string; body: any }> = [];
let counter = 0;
const chats = new Map<string, string[]>(); // chat id -> what it was asked

const env = (): Env => ({ RETELL_API_KEY: KEY, GLAMOUR_CHAT_AGENT_ID: AGENT, RETELL_API_BASE: base });
const deps = (now = NOW, timeoutMs?: number) => ({ fetch, now: () => now, log: (m: string) => logs.push(m), timeoutMs });
let logs: string[] = [];

before(async () => {
  server = createServer(async (req: IncomingMessage, res: ServerResponse) => {
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(chunk as Buffer);
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
    calls.push({ path: req.url ?? "", auth: String(req.headers.authorization ?? ""), body });
    const send = (status: number, data: unknown) => { res.writeHead(status, { "content-type": "application/json" }); res.end(typeof data === "string" ? data : JSON.stringify(data)); };
    if (req.headers.authorization !== `Bearer ${KEY}`) return send(401, { status: "error", message: "bad key" });
    if (mode.fail) return send(mode.fail, { status: "error", message: "provider internal detail that must never reach a visitor" });
    if (mode.garbage) return send(200, "<html>not json</html>");
    if (mode.hang) return; // never answers
    if (req.url === "/create-chat") {
      assert.equal(body.agent_id, AGENT);
      const id = `chat_${++counter}_${Math.random().toString(36).slice(2, 8)}`;
      chats.set(id, []);
      return send(201, { chat_id: id, agent_id: AGENT, chat_status: "ongoing" });
    }
    if (req.url === "/create-chat-completion") {
      if (!chats.has(body.chat_id)) return send(422, { status: "error", message: "no such chat" });
      chats.get(body.chat_id)!.push(body.content);
      await new Promise((r) => setTimeout(r, Math.floor(Math.random() * 40))); // answers finish in a different order than they began
      const messages = mode.noAgentWords
        ? [{ role: "tool_call_invocation", name: "x", arguments: "{}" }]
        : [{ role: "tool_call_invocation", name: "end_call", arguments: "{}", message_id: "t" }, { role: "agent", content: `echo[${body.chat_id}]: ${body.content}`, message_id: "m" }];
      return send(201, { messages });
    }
    return send(404, {});
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
after(() => server.close());
beforeEach(() => { mode = {}; calls = []; logs = []; chats.clear(); resetLimits(); });

let ipCounter = 0;
const post = (path: string, body: unknown, over: Partial<ChatRequest> = {}, ip = `203.0.113.${++ipCounter % 250}`): ChatRequest => ({
  method: "POST", path, headers: { "content-type": "application/json", ...(over.headers ?? {}) }, body: typeof body === "string" ? body : JSON.stringify(body), ip, url: "https://glamour.example/api/chat", ...over,
});
const start = async (ip?: string, e = env(), now = NOW) => handle(post("/api/chat/start", {}, {}, ip), e, deps(now));
const say = async (token: string, text: string, ip?: string, now = NOW) => handle(post("/api/chat/message", { token, text }, {}, ip), env(), deps(now));
const json = (r: { body: string }) => JSON.parse(r.body);

describe("a typed chat", () => {
  test("starts a chat on the customer's chat agent and answers with only the agent's words", async () => {
    const s = await start();
    assert.equal(s.status, 201);
    const token = json(s).token as string;
    assert.ok(token.length < 400);
    const r = await say(token, "What time do you open?");
    assert.equal(r.status, 200);
    const out = json(r);
    assert.match(out.reply, /^echo\[chat_\d+_\w+\]: What time do you open\?$/);
    assert.notEqual(out.token, token, "each reply carries the next session token");
    assert.equal(out.remaining, MAX_MESSAGES - 1);
    assert.ok(!out.reply.includes("end_call"), "tool activity is dropped");
    assert.deepEqual(calls.map((c) => c.path), ["/create-chat", "/create-chat-completion"]);
    assert.ok(calls.every((c) => c.auth === `Bearer ${KEY}`), "the secret key is used server-side only");
    for (const response of [s, r]) {
      assert.equal(response.headers["cache-control"], "no-store");
      assert.ok(!response.body.includes(KEY) && !JSON.stringify(response.headers).includes(KEY), "the key never reaches the visitor");
    }
  });

  test("the cooperative flow limit: a visitor who follows the tokens in order is asked to start a new chat after 20 messages", async () => {
    let token = json(await start()).token as string;
    for (let i = 0; i < MAX_MESSAGES; i++) {
      const r = await say(token, `q${i}`, "198.51.100.7");
      assert.equal(r.status, 200, `message ${i}`);
      token = json(r).token;
      resetLimits();
    }
    const full = await say(token, "one more");
    assert.equal(full.status, 429);
    assert.equal(json(full).error, "chat_full");
    assert.match(json(full).message, /new chat|\(908\) 668-8828/);
  });
});

describe("what the 20-message counter is NOT (a documented limitation, tested so it cannot be mistaken for a cap)", () => {
  test("an OLD session token can be replayed within its lifetime: the counter is a cooperative flow limit, not a cap on messages or spend", async () => {
    const first = json(await start()).token as string;   // n = 0
    let accepted = 0;
    for (let i = 0; i < MAX_MESSAGES + 1; i++) {
      const r = await say(first, `replay ${i}`, "198.51.100.77");
      if (r.status === 200) accepted += 1;
      resetLimits();   // (the per-visitor limit is a separate, best-effort control)
    }
    assert.equal(accepted, MAX_MESSAGES + 1, "the same n=0 token was accepted past the 20-message mark: the relay keeps no state, so it cannot refuse a replay");
  });

  test("two simultaneous requests carrying the same count both reach the provider", async () => {
    let token = json(await start()).token as string;
    for (let i = 0; i < MAX_MESSAGES - 1; i++) { token = json(await say(token, `q${i}`, "198.51.100.78")).token; resetLimits(); }   // n = 19
    const before = calls.filter((c) => c.path === "/create-chat-completion").length;
    const both = await Promise.all([say(token, "last one A", "198.51.100.79"), say(token, "last one B", "198.51.100.80")]);
    assert.deepEqual(both.map((r) => r.status), [200, 200]);
    assert.equal(calls.filter((c) => c.path === "/create-chat-completion").length, before + 2);
  });

  test("what IS enforced on every request: the length of a message, the expiry of the token, and the origin", async () => {
    const token = json(await start()).token as string;
    assert.equal((await say(token, "x".repeat(MAX_TEXT + 1))).status, 413);
    assert.equal((await say(token, "hello", undefined, NOW + TOKEN_TTL_MS + 1)).status, 401);
    assert.equal((await handle(post("/api/chat/message", { token, text: "hi" }, { headers: { origin: "https://evil.example", "content-type": "application/json" } }), env(), deps())).status, 403);
  });
});

describe("validation", () => {
  test("only POST to the two routes; JSON only; small bodies; own-origin only", async () => {
    assert.equal((await handle({ ...post("/api/chat/start", {}), method: "GET" }, env(), deps())).status, 405);
    assert.equal((await handle(post("/api/chat/other", {}), env(), deps())).status, 404);
    assert.equal((await handle(post("/api/chat/start", "x", { headers: { "content-type": "text/plain" } }), env(), deps())).status, 415);
    assert.equal((await handle(post("/api/chat/start", "{not json"), env(), deps())).status, 400);
    assert.equal((await handle(post("/api/chat/start", "[1,2]"), env(), deps())).status, 400);
    assert.equal((await handle(post("/api/chat/start", { pad: "x".repeat(5000) }), env(), deps())).status, 413);
    assert.equal((await handle(post("/api/chat/start", {}, { headers: { origin: "https://evil.example", "content-type": "application/json" } }), env(), deps())).status, 403);
    assert.equal((await handle(post("/api/chat/start", {}, { headers: { origin: "https://glamour.example", "content-type": "application/json" } }), env(), deps())).status, 201);
    const allowed = { ...env(), ALLOWED_ORIGINS: "https://www.warrenglamourdayspanj.com" };
    assert.equal((await handle(post("/api/chat/start", {}, { headers: { origin: "https://www.warrenglamourdayspanj.com", "content-type": "application/json" } }), allowed, deps())).status, 201);
    assert.equal(calls.filter((c) => c.path === "/create-chat").length, 2, "refused requests never reached the provider");
  });

  test("messages need a genuine, unexpired session token and acceptable text", async () => {
    const token = json(await start()).token as string;
    const refused = async (t: unknown, text: unknown, status: number, now = NOW) => assert.equal((await handle(post("/api/chat/message", { token: t, text }), env(), deps(now))).status, status, JSON.stringify([t, text]).slice(0, 80));
    await refused(undefined, "hi", 401);
    await refused("garbage", "hi", 401);
    await refused(token.slice(0, -2) + "xx", "hi", 401);
    await refused(token.replace(/^[^.]+/, sign(env(), { c: "chat_x", e: NOW + 1000, n: 0 }).split(".")[0]), "hi", 401); // someone else's payload, this signature
    await refused(sign({ ...env(), RETELL_API_KEY: "another-key" }, { c: "chat_1", e: NOW + 1000, n: 0 }), "hi", 401);
    await refused(sign(env(), { c: "../../etc", e: NOW + 1000, n: 0 }), "hi", 401);
    await refused(sign(env(), { c: "chat_1", e: NOW + 1000, n: -1 }), "hi", 401);
    await refused(token, "hi", 401, NOW + TOKEN_TTL_MS + 1);
    await refused(token, 42, 400);
    await refused(token, "   \u0000\u202e  ", 400);
    await refused(token, "x".repeat(MAX_TEXT + 1), 413);
    assert.equal(calls.filter((c) => c.path === "/create-chat-completion").length, 0, "no refused message reached the provider");
    assert.equal(verify(env(), token, NOW)?.n, 0);
  });

  test("control and direction-override characters are removed from what is sent on", async () => {
    const token = json(await start()).token as string;
    await say(token, "hello\u0000 \u202eworld\u2028!");
    assert.equal(calls.at(-1)!.body.content, "hello world!");
  });

  test("nothing works without the server's configuration, and it says so without detail", async () => {
    const r = await handle(post("/api/chat/start", {}), { RETELL_API_BASE: base }, deps());
    assert.equal(r.status, 503);
    assert.ok(!r.body.includes("RETELL") && /call the salon/i.test(json(r).message));
    assert.equal(calls.length, 0);
  });
});

describe("provider failures become short, truthful answers", () => {
  for (const [name, setup, status] of [
    ["rate limited (429)", { fail: 429 }, 503], ["payment required (402)", { fail: 402 }, 502], ["unauthorised (401)", { fail: 401 }, 502], ["server error (500)", { fail: 500 }, 502],
    ["not JSON", { garbage: true }, 502],
  ] as const) {
    test(name, async () => {
      mode = setup;
      const r = await start();
      assert.equal(r.status, status);
      assert.equal(json(r).ok, false);
      assert.match(json(r).message, /\(908\) 668-8828/, "points to the phone");
      assert.ok(!/internal detail|bad key|Bearer|stack|api\.retellai/i.test(r.body), "no provider text reaches the visitor");
      assert.ok(logs.every((l) => !l.includes(KEY)), "the log holds a status code only");
      if (name.startsWith("rate")) assert.ok(r.headers["retry-after"]);
    });
  }

  test("a provider that never answers times out instead of hanging the visitor", async () => {
    mode = { hang: true };
    const t = Date.now();
    const r = await handle(post("/api/chat/start", {}), env(), deps(NOW, 300));
    assert.equal(r.status, 504);
    assert.ok(Date.now() - t < 3000, "it gave up at its own limit instead of hanging");
    assert.match(json(r).message, /\(908\) 668-8828/);
  });

  test("a reply with no agent words is treated as a failure, not shown as an empty answer", async () => {
    const token = json(await start()).token as string;
    mode = { noAgentWords: true };
    const r = await say(token, "hi");
    assert.equal(r.status, 502);
    assert.equal(agentWords({ messages: [{ role: "user", content: "x" }, { role: "tool_call_result", content: "y" }] }), "");
  });
});

describe("limits", () => {
  test("one visitor cannot open chats or send messages without bound; another visitor is unaffected", async () => {
    for (let i = 0; i < 6; i++) assert.equal((await start("192.0.2.1")).status, 201);
    const slow = await start("192.0.2.1");
    assert.equal(slow.status, 429);
    assert.ok(slow.headers["retry-after"]);
    assert.equal((await start("192.0.2.2")).status, 201, "a different visitor is not slowed");
    assert.equal((await start("192.0.2.1", env(), NOW + 11 * 60_000)).status, 201, "and the window passes");
  });
});

describe("simultaneous visitors", () => {
  for (const visitors of [5, 25]) {
    test(`${visitors} visitors at once, each with several messages: every answer goes to its own visitor, in order, and nothing leaks across chats`, async () => {
      const sessions = await Promise.all(Array.from({ length: visitors }, (_, i) => start(`10.0.0.${i + 1}`)));
      assert.ok(sessions.every((s) => s.status === 201));
      const tokens = sessions.map((s) => json(s).token as string);
      const ids = tokens.map((t) => verify(env(), t, NOW)!.c);
      assert.equal(new Set(ids).size, visitors, "every visitor has a chat of their own");
      const transcripts: string[][] = tokens.map(() => []);
      await Promise.all(tokens.map(async (first, v) => {
        let token = first;
        for (let m = 0; m < 4; m++) {
          const question = `visitor ${v} question ${m} ${"é中ñ"[m % 3]}`;
          const r = await say(token, question, `10.0.0.${v + 1}`);
          assert.equal(r.status, 200);
          const out = json(r);
          assert.equal(out.reply, `echo[${ids[v]}]: ${question}`, "the answer is to THIS visitor's question in THIS visitor's chat");
          transcripts[v].push(out.reply);
          token = out.token;
        }
      }));
      for (let v = 0; v < visitors; v++) assert.deepEqual(chats.get(ids[v])!.map((q) => q.split(" ")[1]), ["" + v, "" + v, "" + v, "" + v], "only this visitor's words ever reached this chat");
      assert.equal(calls.filter((c) => c.path === "/create-chat-completion").length, visitors * 4);
      assert.ok(transcripts.flat().every((t) => !t.includes(KEY)));
    });
  }

  test("a flood of one visitor does not block or mix up the others", async () => {
    const token = json(await start("172.16.0.9")).token as string;
    const others = await Promise.all(Array.from({ length: 5 }, (_, i) => start(`172.16.1.${i}`)));
    const flood = await Promise.all(Array.from({ length: 60 }, () => say(token, "spam", "172.16.0.9")));
    assert.ok(flood.filter((r) => r.status === 429).length >= 20, "the flood is cut off by the per-visitor limit");
    for (const o of others) assert.equal((await say(json(o).token, "hello", o ? "172.16.1.1" : undefined)).status, 200);
  });
});
