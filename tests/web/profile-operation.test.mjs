import assert from "node:assert/strict";
import test from "node:test";
import { checkProfileMutation, profileTransitionMessage, ProfileResultUnknown, ProfileMutationFailed, ProfileMutationSuperseded, submitProfileMutation } from "../../src/features/mihomo/profile-operation.ts";
import { profileSnapshots } from "../../src/features/mihomo/workspace-refresh.ts";
import { systemActionSnapshot } from "../../src/control-panel/system-operation.ts";

const mutation = { profileId: "profile-1", payload: { operation_id: "operation-1", name: "Test" } };
const confirmed = { id: "profile-1", last_operation_id: "operation-1" };
const interrupted = () => Object.assign(new Error("connection lost"), { kind: "network" });
const options = { timeoutMs: 150, pollDelayMs: 0 };

test("YAML delivery stays informational and keeps the original grace deadline", () => {
  const previous_valid_until = Date.now() / 1000 + 900;
  const profile = { protection_status: {
    common: { previous_valid_until },
    device: { previous_valid_until, yaml_served_at: Date.now() / 1000 },
  } };
  assert.match(profileTransitionMessage(profile, "device"), /рабочее подключение ещё не подтверждено/);
  assert.match(profileTransitionMessage(profile, "device"), /доступна до/);
  assert.match(profileTransitionMessage(profile, "common"), /доступна до/);
  assert.match(profileTransitionMessage(profile), /доступна до/);
  assert.doesNotMatch(profileTransitionMessage(profile), /рабочее подключение ещё не подтверждено/);
  assert.equal(profileTransitionMessage({ protection_status: { device: {} } }, "device"), "");
});

test("successful profile save needs no recovery requests", async () => {
  let calls = 0;
  const result = await submitProfileMutation(async (_path, init) => {
    calls++;
    assert.equal(init.method, "PATCH");
    assert.deepEqual(JSON.parse(init.body), mutation.payload);
    return confirmed;
  }, mutation, () => {}, options);
  assert.equal(result, confirmed);
  assert.equal(calls, 1);
});

test("dropped save response is confirmed by committed operation ID without replaying PATCH", async () => {
  const calls = [];
  const reports = [];
  let reads = 0;
  const result = await submitProfileMutation(async (path, init) => {
    calls.push([path, init?.method || "GET"]);
    if (init?.method === "PATCH") throw interrupted();
    if (++reads === 1) throw interrupted();
    return { items: reads === 2 ? [{ ...confirmed, last_operation_id: "previous-operation" }] : [confirmed] };
  }, mutation, (message) => reports.push(message), options);
  assert.equal(result, confirmed);
  assert.equal(calls.filter(([, method]) => method === "PATCH").length, 1);
  assert.ok(calls.slice(1).every(([path, method]) => path === "/mihomo/profiles" && method === "GET"));
  assert.equal(reports.length, 1);
});

test("a lost create response finds the created profile without another POST", async () => {
  const created = { id: "new-profile", create_operation_id: "operation-1" };
  let writes = 0;
  const result = await submitProfileMutation(async (_path, init) => {
    if (init?.method === "POST") { writes++; throw interrupted(); }
    return { items: [created] };
  }, { payload: mutation.payload }, () => {}, options);
  assert.equal(result, created);
  assert.equal(writes, 1);
});

test("matching settings or another profile's operation never prove this save succeeded", async () => {
  const profiles = [
    { ...confirmed, last_operation_id: "previous-operation", name: "Test" },
    { ...confirmed, id: "other-profile" },
  ];
  await assert.rejects(checkProfileMutation(async () => ({ items: profiles }), mutation, () => {}, { ...options, timeoutMs: 12 }), ProfileResultUnknown);
  await assert.rejects(checkProfileMutation(async () => ({ items: [{ id: "profile-1" }] }), { ...mutation, payload: { operation_id: "" } }, () => {}, options), ProfileResultUnknown);
});

test("validation and authentication errors are not converted into successful or pending saves", async () => {
  for (const status of [401, 403, 409, 422]) {
    const error = Object.assign(new Error("rejected"), { kind: "http", status });
    let calls = 0;
    await assert.rejects(submitProfileMutation(async () => { calls++; throw error; }, mutation, () => {}, options), (cause) => cause === error);
    assert.equal(calls, 1);
  }
});

test("recovery has a deadline and a later manual check only reads the saved result", async () => {
  await assert.rejects(checkProfileMutation(async (_path, init) => new Promise((_, reject) => {
    init.signal.addEventListener("abort", () => reject(interrupted()), { once: true });
  }), mutation, () => {}, { ...options, timeoutMs: 12 }), ProfileResultUnknown);
  assert.equal(await checkProfileMutation(async (path, init) => {
    assert.equal(path, "/mihomo/profiles");
    assert.equal(init.method, undefined);
    return { items: [confirmed] };
  }, mutation, () => {}, options), confirmed);
});


test("accepted profile mutation waits for worker success before returning persisted profile", async () => {
  const id = "a".repeat(32);
  const currentMutation = { profileId: "profile-1", payload: { operation_id: id } };
  let writes = 0, polls = 0, profileReads = 0;
  const result = await submitProfileMutation(async (path, init) => {
    if (init?.method === "PATCH") {
      writes++;
      assert.equal(init.headers["X-Operation-ID"], id);
      return { id, action: "mihomo-profile-update", unit: "worker.service", state: "queued" };
    }
    if (path.startsWith("/application/operations/")) {
      return { id, action: "mihomo-profile-update", unit: "worker.service", state: ++polls === 1 ? "running" : "succeeded" };
    }
    profileReads++;
    assert.equal(polls, 2);
    return { items: [{ id: "profile-1", last_operation_id: id }] };
  }, currentMutation, () => {}, options);
  assert.equal(result.last_operation_id, id);
  assert.equal(writes, 1);
  assert.equal(profileReads, 1);
});

test("failed durable worker cannot be mistaken for saved profile", async () => {
  const id = "b".repeat(32);
  await assert.rejects(submitProfileMutation(async (path, init) => {
    if (init?.method === "PATCH") return { id, action: "mihomo-profile-update", unit: "worker.service", state: "queued" };
    assert.ok(path.startsWith("/application/operations/"));
    return { id, action: "mihomo-profile-update", unit: "worker.service", state: "failed", message: "Rollback required" };
  }, { profileId: "profile-1", payload: { operation_id: id } }, () => {}, options), /Rollback required/);
});

test("temporary operation lookup failures are retried without repeating the mutation", async () => {
  const id = "c".repeat(32);
  let writes = 0, polls = 0;
  const updates = [];
  const result = await submitProfileMutation(async (path, init) => {
    if (init?.method === "PATCH") { writes++; throw interrupted(); }
    if (path.startsWith("/application/operations/")) {
      if (++polls <= 2) throw Object.assign(new Error("temporarily unavailable"), { kind: "http", status: polls === 1 ? 404 : 503 });
      return { id, action: "mihomo-profile-update", unit: "worker.service", state: "succeeded" };
    }
    return { items: [{ id: "profile-1", last_operation_id: id }] };
  }, { profileId: "profile-1", payload: { operation_id: id } }, () => {}, { ...options, onOperation: (value) => updates.push(value) });
  assert.equal(result.last_operation_id, id);
  assert.equal(writes, 1);
  assert.equal(polls, 3);
  assert.equal(updates.length, 1);
});

test("manual readback distinguishes terminal failure from an unavailable result", async () => {
  const pending = { profileId: "profile-1", payload: { operation_id: "d".repeat(32) } };
  for (const state of ["failed", "cancelled"]) {
    await assert.rejects(checkProfileMutation(async (_path, init) => {
      assert.equal(init.method, undefined);
      return { id: pending.payload.operation_id, action: "mihomo-profile-update", unit: "worker.service", state, message: "Operation stopped" };
    }, pending, () => {}, options), ProfileMutationFailed);
  }
  const unavailable = Object.assign(new Error("Session expired"), { kind: "http", status: 401 });
  await assert.rejects(checkProfileMutation(async () => { throw unavailable; }, pending, () => {}, options), ProfileResultUnknown);
});

test("server errors after admission reconcile the same operation without another write", async () => {
  const id = "e".repeat(32);
  let writes = 0;
  const result = await submitProfileMutation(async (path, init) => {
    if (init?.method === "PATCH") { writes++; throw Object.assign(new Error("response failed"), { kind: "http", status: 503 }); }
    if (path.startsWith("/application/operations/")) return { id, action: "mihomo-profile-update", unit: "worker.service", state: "succeeded" };
    assert.equal(init.cache, "no-store");
    return { items: [{ id: "profile-1", last_operation_id: id }] };
  }, { profileId: "profile-1", payload: { operation_id: id } }, () => {}, options);
  assert.equal(result.last_operation_id, id);
  assert.equal(writes, 1);
});

test("foreign or malformed admission never changes the operation being checked", async () => {
  const id = "f".repeat(32);
  for (const change of [{ id: "other" }, { action: "mihomo-profile-delete" }, { unit: undefined }, { state: "invented" }]) {
    let writes = 0;
    const updates = [];
    await assert.rejects(submitProfileMutation(async (path, init) => {
      if (init?.method === "PATCH") writes++;
      else assert.equal(path, `/application/operations/${id}`);
      return { id, action: "mihomo-profile-update", unit: "worker.service", state: "succeeded", ...change };
    }, { profileId: "profile-1", payload: { operation_id: id } }, () => {}, { ...options, timeoutMs: 12, onOperation: value => updates.push(value) }), ProfileResultUnknown);
    assert.equal(writes, 1);
    assert.equal(updates.length, 0);
  }
});

test("worker unit cannot change after confirmed admission", async () => {
  const id = "1".repeat(32);
  await assert.rejects(submitProfileMutation(async (_path, init) => ({ id, action: "mihomo-profile-update", unit: init?.method ? "first.service" : "other.service", state: init?.method ? "queued" : "succeeded" }),
    { profileId: "profile-1", payload: { operation_id: id } }, () => {}, { ...options, timeoutMs: 12 }), ProfileResultUnknown);
});

test("known successful worker with a superseded or deleted profile does not stay pending forever", async () => {
  const id = "2".repeat(32);
  for (const items of [[], [{id:"profile-1",last_operation_id:"later-writer"}]]) {
    let writes = 0;
    await assert.rejects(submitProfileMutation(async (path, init) => {
      if (init?.method === "PATCH") { writes++; throw interrupted(); }
      if (path.startsWith("/application/operations/")) return { id, action:"mihomo-profile-update", unit:"worker.service", state:"succeeded" };
      assert.equal(init.cache,"no-store");
      return { items };
    }, { profileId:"profile-1", payload:{operation_id:id} }, () => {}, options), ProfileMutationSuperseded);
    assert.equal(writes,1);
  }
});

test("a matching receipt in a malformed profile reply requires a valid readback without replay", async () => {
  const valid = { ...confirmed, name: "Test", connections: [], devices: [] };
  let writes = 0, reads = 0;
  const result = await submitProfileMutation(async (_path, init) => {
    if (init?.method) { writes++; return { ...valid, devices: [{ id: "device", name: {} }] }; }
    reads++; return { items: [valid] };
  }, mutation, () => {}, { ...options, validateProfiles: profileSnapshots });
  assert.equal(result, valid);
  assert.equal(writes, 1);
  assert.equal(reads, 1);
});

test("successful worker with a malformed collection stays unknown rather than superseded", async () => {
  const id = "3".repeat(32);
  let writes = 0;
  await assert.rejects(submitProfileMutation(async (path, init) => {
    if (init?.method) { writes++; throw interrupted(); }
    if (path.startsWith("/application/operations/")) return { id, action: "mihomo-profile-update", unit: "worker.service", state: "succeeded" };
    return { items: [{ id: "profile-1", name: "Test", last_operation_id: id, connections: null }] };
  }, { profileId: "profile-1", payload: { operation_id: id } }, () => {}, { ...options, validateProfiles: profileSnapshots }), ProfileResultUnknown);
  assert.equal(writes, 1);
});

test("malformed operation messages are withheld from UI and valid admission is confirmed by GET", async () => {
  const id="4".repeat(32),updates=[];
  const operation={id,unit:"worker.service",action:"mihomo-profile-update",state:"succeeded"};
  const profile={id:"profile-1",name:"Test",connections:[],last_operation_id:id};
  let writes=0;
  const result=await submitProfileMutation(async(path,init)=>{
    if(init?.method){writes++;return {...operation,message:{}};}
    return path.startsWith("/application/operations/")?operation:{items:[profile]};
  },{profileId:"profile-1",payload:{operation_id:id}},()=>{},
  {...options,validateOperation:systemActionSnapshot,validateProfiles:profileSnapshots,onOperation:value=>updates.push(value)});
  assert.equal(result,profile);assert.equal(writes,1);assert.deepEqual(updates,[operation]);
});

test("malformed operation read retains unknown state without publishing an invalid failure", async () => {
  const id="5".repeat(32),updates=[];
  await assert.rejects(checkProfileMutation(async()=>({id,unit:"worker.service",action:"mihomo-profile-update",state:"failed",message:{}}),
    {profileId:"profile-1",payload:{operation_id:id}},()=>{},
    {...options,validateOperation:systemActionSnapshot,onOperation:value=>updates.push(value)}),ProfileResultUnknown);
  assert.equal(updates.length,0);
});
