import assert from "node:assert/strict";
import test from "node:test";
import { checkProfileMutation, ProfileResultUnknown, submitProfileMutation } from "../../src/features/mihomo/profile-operation.ts";

const mutation = { profileId: "profile-1", payload: { operation_id: "operation-1", name: "Test" } };
const confirmed = { id: "profile-1", last_operation_id: "operation-1" };
const interrupted = () => Object.assign(new Error("connection lost"), { kind: "network" });
const options = { timeoutMs: 150, pollDelayMs: 0 };

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
  for (const status of [401, 403, 409, 422, 500]) {
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
