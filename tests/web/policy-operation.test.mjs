import assert from "node:assert/strict";
import test from "node:test";
import { checkPolicySave, persistPendingPolicySave, PolicyResultUnknown, readPendingPolicySave, submitPolicySave } from "../../src/features/mihomo/policy-operation.ts";
import { submitSystemOperation, systemActionSnapshot } from "../../src/control-panel/system-operation.ts";
import { policySnapshot } from "../../src/features/mihomo/workspace-refresh.ts";

const id = "a".repeat(32);
const ruleId = "personal-" + "b".repeat(12);
const options = { attempts: 3, delayMs: 0, admit: submitSystemOperation };
const interrupted = () => Object.assign(new Error("connection lost"), { kind: "network" });

test("durable admission with corrupt notification fields requires a valid operation read",async()=>{
  const save={id,kind:"operation",action:"mihomo-profile-module-settings"};
  const operation={id,unit:"worker.service",action:save.action,state:"queued"};
  let writes=0,reads=0;
  const result=await submitPolicySave(async(_path,init)=>{
    if(init?.method){writes++;return {...operation,progress:{}};}
    reads++;return operation;
  },save,"/mihomo/modules/transport-wg/settings",{},"PATCH",{...options,validate:systemActionSnapshot});
  assert.equal(result,operation);assert.equal(writes,1);assert.equal(reads,1);
  await assert.rejects(checkPolicySave(async()=>({...operation,state:"failed",message:{}}),save,
    {...options,validate:systemActionSnapshot}),PolicyResultUnknown);
});

test("successful policy response needs no recovery reads", async () => {
  const calls = [];
  const result = { revision: "saved" };
  assert.equal(await submitPolicySave(async (path, init) => {
    calls.push([path, init]);
    assert.deepEqual(JSON.parse(init.body), { values: { mode: "rule" }, operation_id: id });
    return result;
  }, { id, kind: "routing" }, "/mihomo/routing/settings", { values: { mode: "rule" } }), result);
  assert.equal(calls.length, 1);
});

test("a malformed success response is reconciled by receipt before any success callback",async()=>{
  let writes=0,reads=0;
  const confirmed={schema:[],values:{mode:"fake-ip"},last_operation_id:id};
  const result=await submitPolicySave(async(_path,init)=>{
    if(init){writes++;return {last_operation_id:id,values:{}};}
    reads++;return confirmed;
  },{id,kind:"dns"},"/write",{},"PATCH",{...options,validate:value=>policySnapshot(value,"dns")});
  assert.equal(result,confirmed);assert.equal(writes,1);assert.equal(reads,1);
  writes=0;reads=0;
  await assert.rejects(submitPolicySave(async(_path,init)=>{
    if(init)writes++;else reads++;
    return {last_operation_id:id,schema:[],values:[]};
  },{id,kind:"dns"},"/write",{},"PATCH",{...options,validate:value=>policySnapshot(value,"dns")}),PolicyResultUnknown);
  assert.equal(writes,1);assert.equal(reads,3);
});

test("lost replies for every policy form use exact receipts and never replay a write", async () => {
  for (const kind of ["dns", "routing", "presets", "personal"]) {
    const calls = [];
    const rule = { id: ruleId, last_operation_id: id, revision: "rule" };
    const policy = { last_operation_id: id, presets_last_operation_id: id, presets: [{ id: "preset" }], presets_revision: "preset", personal_rules: [rule] };
    let reads = 0;
    const result = await submitPolicySave(async (path, init) => {
      calls.push([path, init?.method || "GET"]);
      if (init) throw interrupted();
      if (++reads === 1) throw interrupted();
      return policy;
    }, { id, kind }, "/write", {}, kind === "personal" ? "POST" : "PATCH", options);
    assert.equal(calls.filter(([, method]) => method !== "GET").length, 1);
    assert.ok(calls.slice(1).every(([path, method]) => method === "GET" && path === (kind === "dns" ? "/mihomo/dns/settings" : "/mihomo/routing/schema")));
    assert.deepEqual(result, kind === "personal" ? rule : kind === "presets" ? { presets: policy.presets, revision: "preset", last_operation_id: id } : policy);
  }
});

test("matching values, previous receipts and receipts on another rule cannot prove success", async () => {
  for (const kind of ["dns", "routing", "presets", "personal"]) {
    await assert.rejects(checkPolicySave(async () => ({
      last_operation_id: "previous", presets_last_operation_id: "previous", values: { mode: "rule" },
      personal_rules: [{ id: "personal-" + "c".repeat(12), last_operation_id: id }, { id: ruleId, last_operation_id: "previous" }],
    }), { id, kind, ...(kind === "personal" ? { ruleId } : {}) }, options), PolicyResultUnknown);
  }
});

test("explicit HTTP rejection is final; server failures and invalid replies require readback", async () => {
  for (const status of [401, 404, 409, 422]) {
    let reads = 0;
    const failure = Object.assign(new Error("rejected"), { kind: "http", status });
    await assert.rejects(submitPolicySave(async (_path, init) => {
      if (init) throw failure;
      reads++;
    }, { id, kind: "dns" }, "/write", {}, "PATCH", options), (cause) => cause === failure);
    assert.equal(reads, 0);
  }
  for (const failure of [Object.assign(new Error("server"), { kind: "http", status: 500 }), Object.assign(new Error("invalid reply"), { kind: "response" })]) {
    let writes = 0;
    assert.equal((await submitPolicySave(async (_path, init) => {
      if (init) { writes++; throw failure; }
      return { last_operation_id: id };
    }, { id, kind: "dns" }, "/write", {}, "PATCH", options)).last_operation_id, id);
    assert.equal(writes, 1);
  }
});

test("unknown result can later be confirmed with only GET requests", async () => {
  const save = { id, kind: "personal" };
  await assert.rejects(submitPolicySave(async (_path, init) => {
    if (init) throw interrupted();
    return { personal_rules: [] };
  }, save, "/write", {}, "POST", options), PolicyResultUnknown);
  const rule = { id: ruleId, last_operation_id: id };
  assert.equal(await checkPolicySave(async (path, init) => {
    assert.equal(path, "/mihomo/routing/schema");
    assert.equal(init, undefined);
    return { personal_rules: [rule] };
  }, save, options), rule);
});

test("durable admission keeps its ID across lost replies and preserves pending or failed worker state", async () => {
  const save = { id, kind: "operation", action: "mihomo-profile-personal-delete" };
  for (const state of ["queued", "running", "failed", "succeeded"]) {
    let writes = 0;
    const action = { id, unit: "worker.service", action: save.action, state };
    const result = await submitPolicySave(async (path, init) => {
      if (init?.method === "DELETE") {
        writes++;
        assert.equal(init.method, "DELETE");
        assert.equal(init.headers.get("X-Operation-ID"), id);
        throw interrupted();
      }
      assert.equal(path, `/application/operations/${id}`);
      assert.equal(init.cache, "no-store");
      return action;
    }, save, `/mihomo/routing/personal-rules/${ruleId}`, {}, "DELETE", options);
    assert.equal(result, action);
    assert.equal(writes, 1);
    assert.equal(result.state, state);
  }
});

test("unknown durable admission requires the same action, ID and worker on a manual read", async () => {
  const save = { id, kind: "operation", action: "mihomo-profile-module-settings" };
  let writes = 0;
  await assert.rejects(submitPolicySave(async (_path, init) => {
    if (init?.method === "PATCH") writes++;
    throw interrupted();
  }, save, "/write", {}, "PATCH", options), PolicyResultUnknown);
  assert.equal(writes, 1);
  for (const action of [{ id, action: save.action, state: "succeeded" },
                        { id: "other", action: save.action, unit: "worker.service", state: "succeeded" },
                        { id, action: "mihomo-profile-delete", unit: "worker.service", state: "succeeded" }]) {
    await assert.rejects(checkPolicySave(async (_path, init) => { assert.equal(init, undefined); return action; }, save, options), PolicyResultUnknown);
  }
  const action = { id, action: save.action, unit: "worker.service", state: "queued" };
  assert.equal(await checkPolicySave(async () => action, save, options), action);
});

test("reload receipt persists only identifiers and validates restored paths", () => {
  const previous = globalThis.window;
  const storage = new Map();
  globalThis.window = { sessionStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) } };
  try {
    const save = { id, kind: "personal", ruleId };
    persistPendingPolicySave({ ...save, payload: { password: "must not persist", entries: ["private.example"] }, token: "must not persist" });
    assert.deepEqual(readPendingPolicySave(), save);
    assert.deepEqual(JSON.parse([...storage.values()][0]), save);
    for (const invalid of [{ ...save, id: "invalid" }, { ...save, id: 11111111111111111111111111111111 }, { ...save, ruleId: "../../outside" }, { ...save, kind: "invalid" }, { ...save, kind: "dns" }]) {
      storage.set([...storage.keys()][0], JSON.stringify(invalid));
      assert.equal(readPendingPolicySave(), null);
    }
    persistPendingPolicySave(null);
    assert.equal(readPendingPolicySave(), null);
    const operation = { id, kind: "operation", action: "mihomo-profile-personal-delete" };
    persistPendingPolicySave({ ...operation, payload: { rule: "private" }, token: "private" });
    assert.deepEqual(readPendingPolicySave(), operation);
    assert.deepEqual(JSON.parse([...storage.values()][0]), operation);
    for (const action of ["application-update", "../../outside", "mihomo-profile-delete\nprivate"]) {
      storage.set([...storage.keys()][0], JSON.stringify({ ...operation, action }));
      assert.equal(readPendingPolicySave(), null);
    }
  } finally { if (previous === undefined) delete globalThis.window; else globalThis.window = previous; }
});
