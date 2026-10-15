import assert from "node:assert/strict";
import { test, afterEach } from "node:test";
import { createApiClient } from "../../src/shared/lib/api-request.ts";
import { submitCdnSecurity, followCdnSecurity, cdnSecuritySnapshot, CdnResultUnknown, restorePendingCdnOperation, readPendingCdnOperation, persistPendingCdnOperation, CDN_PENDING_CHANGED } from "../../src/shared/lib/cdn-security-operation.ts";

const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
afterEach(() => { globalThis.fetch = originalFetch; if (originalWindow === undefined) delete globalThis.window; else globalThis.window = originalWindow; });
const request = () => createApiClient("test", { retryDelaysMs: [] });
const initial = { id: "a".repeat(32), enabled: true, state: "queued", progress: 0, message: "Starting" };
const result = (state, enabled = true) => ({ authenticated_origin_pulls: enabled, operation: { ...initial, state, progress: state === "succeeded" ? 100 : 45 } });

test("restored CF/ECH descriptors contain only identity and remain unconfirmed", () => {
  const restored = restorePendingCdnOperation({ ...initial, state: "succeeded", message: { unsafe: true }, result: { secret: "discard" } });
  assert.equal(restored.state, "unknown");
  assert.equal(typeof restored.message, "string");
  assert.equal(restored.result, undefined);
  assert.equal(restorePendingCdnOperation({ ...initial, kind: "ech", domain: "Edge.Example." }).domain, "Edge.Example.");
  for (const invalid of [null, [], { ...initial, id: "invalid" }, { ...initial, enabled: "true" }, { ...initial, kind: "other" }, { ...initial, domain: "unexpected" }, { ...initial, kind: "ech" }, { ...initial, kind: "ech", domain: " " }]) assert.equal(restorePendingCdnOperation(invalid), null);
});

test("pending storage omits replies and a late completion cannot clear another command", () => {
  const data = new Map();
  const events = [];
  globalThis.window = { sessionStorage: { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) }, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } }, dispatchEvent: event => events.push(event) };
  persistPendingCdnOperation({ ...initial, kind: "ech", domain: "edge.example", result: { secret: "discard" } });
  assert.deepEqual(JSON.parse([...data.values()][0]), { id: initial.id, enabled: true, kind: "ech", domain: "edge.example" });
  assert.equal(readPendingCdnOperation().state, "unknown");
  const otherId = "b".repeat(32);
  persistPendingCdnOperation({ ...initial, id: otherId });
  persistPendingCdnOperation(null, initial.id);
  assert.equal(readPendingCdnOperation().id, otherId);
  assert.equal(events.length, 2);
  persistPendingCdnOperation(null, otherId);
  assert.equal(readPendingCdnOperation(), null);
  assert.equal(events.at(-1).type, CDN_PENDING_CHANGED);
  assert.equal(events.at(-1).detail, null);
});

test("unavailable or malformed session storage does not break the current page", () => {
  globalThis.window = { sessionStorage: { getItem: () => "invalid JSON", setItem: () => { throw new Error("denied"); }, removeItem: () => { throw new Error("denied"); } } };
  assert.equal(readPendingCdnOperation(), null);
  assert.doesNotThrow(() => persistPendingCdnOperation(initial));
  assert.doesNotThrow(() => persistPendingCdnOperation(null, initial.id));
});

test("lost acknowledgement is reconciled by ID without repeating the command", async () => {
  const calls = [];
  globalThis.fetch = async (path, init) => {
    calls.push(init.method);
    if (init.method === "PUT") {
      assert.equal(JSON.parse(init.body).operation_id, initial.id);
      throw new TypeError("disconnected during reload");
    }
    assert.match(path, new RegExp(`operation_id=${initial.id}$`));
    if (calls.length === 2) throw new TypeError("reconnecting");
    return Response.json(result(calls.length === 3 ? "running" : "succeeded"));
  };
  const progress = [];
  const final = await submitCdnSecurity(request(), initial, (value) => progress.push(value), { pollDelayMs: 0 });
  assert.equal(final.operation.state, "succeeded");
  assert.deepEqual(calls, ["PUT", "GET", "GET", "GET"]);
  assert.ok(progress.some((value) => /связ/i.test(value.message || "")));
  assert.ok(progress.some((value) => value.state === "unknown"));
  assert.ok(progress.every((value) => value.state !== "failed"));
});

test("an unchanged setting is not treated as successful until this command finishes", async () => {
  let calls = 0;
  globalThis.fetch = async () => Response.json(result(++calls < 3 ? "running" : "failed"));
  const final = await submitCdnSecurity(request(), initial, () => {}, { pollDelayMs: 0 });
  assert.equal(calls, 3);
  assert.equal(final.operation.state, "failed");
});

test("rejected commands report their error without polling or replaying", async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls++; return Response.json({ detail: "Missing certificate" }, { status: 409 }); };
  await assert.rejects(submitCdnSecurity(request(), initial, () => {}), { status: 409, message: "Missing certificate" });
  assert.equal(calls, 1);
});

test("resuming an operation only reads its result", async () => {
  globalThis.fetch = async (_path, init) => { assert.equal(init.method, "GET"); return Response.json(result("succeeded")); };
  assert.equal((await followCdnSecurity(request(), initial, () => {})).operation.state, "succeeded");
});

test("ECH preparation uses its endpoint and recovers a lost acknowledgement without replay", async () => {
  const ech = { ...initial, kind: "ech", domain: "cdn.example.com" };
  let writes = 0;
  globalThis.fetch = async (path, init) => {
    if (init.method === "PUT") {
      writes++;
      assert.equal(path, "/api/application/ech");
      assert.deepEqual(JSON.parse(init.body), { domain: ech.domain, operation_id: ech.id });
      throw new TypeError("connection lost");
    }
    assert.match(path, /cdn-security\?operation_id=/);
    return Response.json({ authenticated_origin_pulls: false, operation: { ...ech, state: "succeeded", result: { domain: ech.domain, public_name: ech.domain, type: "HTTPS", priority: 1, target: ".", ttl: 300, parameters: "ech=fixture", content: "1 . ech=fixture" } } });
  };
  const final = await submitCdnSecurity(request(), ech, () => {}, { pollDelayMs: 0 });
  assert.equal(writes, 1);
  assert.equal(final.operation.result.domain, ech.domain);
});

test("another operation cannot accidentally confirm this command", async () => {
  globalThis.fetch = async () => Response.json({ ...result("succeeded"), operation: { ...initial, id: "b".repeat(32), state: "succeeded" } });
  await assert.rejects(followCdnSecurity(request(), initial, () => {}), CdnResultUnknown);
});

test("a missing record immediately after launch is allowed to become visible", async () => {
  let calls = 0;
  globalThis.fetch = async () => ++calls === 1 ? Response.json({ detail: "Not found" }, { status: 404 }) : Response.json(result("succeeded"));
  assert.equal((await followCdnSecurity(request(), initial, () => {}, { pollDelayMs: 0 })).operation.state, "succeeded");
});

test("a prolonged outage stays unconfirmed rather than reporting success or replaying", async () => {
  globalThis.fetch = async () => { throw new TypeError("offline"); };
  await assert.rejects(followCdnSecurity(request(), initial, () => {}, { pollDelayMs: 1, timeoutMs: 5 }), CdnResultUnknown);
});

test("logout cancels polling", async () => {
  const controller = new AbortController();
  globalThis.fetch = async () => Response.json(result("running"));
  const pending = followCdnSecurity(request(), initial, () => controller.abort(), { signal: controller.signal, pollDelayMs: 1000 });
  await assert.rejects(pending, { name: "AbortError" });
});

test("malformed CF/ECH records cannot publish progress, success or renderable DNS fields",()=>{
  for(const value of [{authenticated_origin_pulls:"true"},{...result("running"),operation:{...initial,message:{}}},
    {...result("running"),operation:{...initial,progress:101}}, {...result("running"),operation:{...initial,enabled:"false"}},
    {...result("succeeded"),operation:{...initial,kind:"ech",domain:"edge.example",state:"succeeded",result:{domain:"edge.example"}}}]) assert.throws(()=>cdnSecuritySnapshot(value),CdnResultUnknown);
});

test("corrupt acknowledgements and server errors reconcile one PUT by exact GET",async()=>{
  for(const mode of ["json","shape","server"]){
    let writes=0,reads=0;const updates=[];
    globalThis.fetch=async(_path,init)=>{
      if(init.method==="PUT"){writes++;return mode==="json"?new Response("invalid JSON",{status:202}):mode==="server"?Response.json({detail:"worker reply lost"},{status:502}):Response.json({...result("running"),operation:{...initial,message:{}}});}
      reads++;return Response.json(result("succeeded"));
    };
    assert.equal((await submitCdnSecurity(request(),initial,value=>updates.push(value),{pollDelayMs:0})).operation.state,"succeeded");
    assert.equal(writes,1);assert.equal(reads,1);assert.ok(updates.every(value=>typeof value.message==="string"));
  }
});

test("matching operation ID with malformed or incompatible data stays unknown without replay",async()=>{
  for(const change of [{message:{}},{enabled:false},{kind:"ech",domain:"edge.example"}]){
    let writes=0;const updates=[];
    globalThis.fetch=async(_path,init)=>{if(init.method==="PUT"){writes++;throw new TypeError("lost reply");}return Response.json({...result("running"),operation:{...initial,...change}});};
    await assert.rejects(submitCdnSecurity(request(),initial,value=>updates.push(value),{pollDelayMs:0}),CdnResultUnknown);
    assert.equal(writes,1);assert.ok(updates.every(value=>typeof value.message==="string"));
  }
});

test("a known completed command with a subsequently different setting reports current state without staying pending",async()=>{
  globalThis.fetch=async()=>Response.json(result("succeeded",false));
  const final=await followCdnSecurity(request(),initial,()=>{});
  assert.equal(final.operation.state,"succeeded");assert.equal(final.authenticated_origin_pulls,false);
  assert.match(final.operation.message,/уже отличается/);
});
