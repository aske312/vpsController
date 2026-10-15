import assert from "node:assert/strict";
import test from "node:test";
import { submitSystemOperation, SystemOperationUnknown, systemActionSnapshot, systemAdmissionSnapshot, restoreSystemAdmission, readSystemAdmission, persistSystemAdmission, createSystemActionCompletionTracker, protocolOperationOutcome, systemActionNeedsReload, systemActionNeedsPolling, systemOperationNotification, systemOperationId } from "../../src/control-panel/system-operation.ts";

test("an admitted worker cannot change identity or release its guard through an unrecognized outcome", () => {
  const id = "a".repeat(32);
  const admitted = { id, unit: "worker.service", action: "update", state: "running" };
  for (const change of [{ unit: "foreign.service" }, { action: "foreign" }, { state: "unexpected" }]) assert.throws(() => systemAdmissionSnapshot({ ...admitted, ...change }, id, admitted), SystemOperationUnknown);
  assert.equal(systemAdmissionSnapshot({ ...admitted, state: "succeeded", result: "unknown" }, id, admitted).state, "unknown");
});

test("system admission restores only validated identity with an unknown result", () => {
  const id = "a".repeat(32);
  const restored = restoreSystemAdmission({ id, state: "succeeded", message: {}, body: "secret", token: "secret" });
  assert.equal(restored.id, id);
  assert.equal(restored.state, "unknown");
  assert.equal(typeof restored.message, "string");
  assert.equal(restored.body, undefined);
  assert.equal(restored.token, undefined);
  assert.equal(restoreSystemAdmission({ id, kind: "cancel" }).cancel_requested, true);
  for (const invalid of [null, [], {}, { id: "invalid" }, { id: 1 }, { id, kind: "other" }]) assert.equal(restoreSystemAdmission(invalid), null);
});

test("system admission storage keeps only ID and cannot be cleared by a late different command", () => {
  const originalWindow = globalThis.window;
  const values = new Map();
  globalThis.window = { sessionStorage: { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) } };
  try {
    const id = "a".repeat(32);
    persistSystemAdmission({ id, state: "running", message: "Discard", action: "Discard" });
    assert.deepEqual(JSON.parse([...values.values()][0]), { id });
    persistSystemAdmission(null, "b".repeat(32));
    assert.equal(readSystemAdmission().id, id);
    persistSystemAdmission(null, id);
    assert.equal(readSystemAdmission(), null);
    persistSystemAdmission({ id, state: "unknown", cancel_requested: true, message: "Discard" });
    assert.deepEqual(JSON.parse([...values.values()][0]), { id, kind: "cancel" });
    assert.equal(readSystemAdmission().cancel_requested, true);
  } finally { if (originalWindow === undefined) delete globalThis.window; else globalThis.window = originalWindow; }
});

test("operation snapshots reject malformed notification fields while preserving known failures", () => {
  const failed={id:"worker",unit:"worker.service",state:"failed",result:"exit-code",message:"Failed",progress:50};
  assert.equal(systemActionSnapshot(failed),failed);
  assert.equal(systemOperationNotification(systemActionSnapshot(failed),"Worker",false).message,"Failed");
  assert.equal(systemActionSnapshot({progress:null}).progress,undefined);
  for(const value of [null,[],{message:{}},{unit:{}},{result:{}},{action:{}},{state:true},
    {progress:"50"},{progress:-1},{progress:101},{progress:Infinity},{cancel_requested:"true"}]) assert.throws(()=>systemActionSnapshot(value));
});

test("an unconfirmed polled operation stays unknown while polling continues",()=>{
  const action={id:"worker",state:"unknown",progress:50};
  assert.equal(systemActionNeedsPolling(action),true);
  const notification=systemOperationNotification(action,"Worker",true);
  assert.equal(notification.state,"unknown");assert.equal(notification.progress,undefined);
});

test("unknown system cards offer readback and withhold an unconfirmed rollback action", async () => {
  let checks = 0;
  const onCancel = () => {};
  const onRecheck = () => { checks++; };
  const notification = systemOperationNotification({ id: "worker", action: "update", state: "unknown" }, "Update", true, onCancel, onRecheck);
  assert.equal(notification.state, "unknown");
  assert.equal(notification.onCancel, undefined);
  assert.equal(notification.action.label, "Проверить результат");
  await notification.action.run();
  assert.equal(checks, 1);
  const running = systemOperationNotification({ id: "a".repeat(32), unit: "vps-control-action-123.service", action: "update", state: "running" }, "Update", true, onCancel, onRecheck);
  assert.equal(running.onCancel, onCancel);
  assert.equal(running.action, undefined);
});

test("rollback control needs a supported action, exact ID and no previous stop intent", () => {
  const cancel = () => {};
  for (const action of ["update", "test-rollback", "safe-update", "kernel-update"]) {
    const running = { id: "a".repeat(32), unit: "vps-control-action-123.service", action, state: "running" };
    assert.equal(systemOperationNotification(running, "Worker", true, cancel).onCancel, cancel);
    assert.equal(systemOperationNotification({ ...running, cancel_requested: true }, "Worker", true, cancel).onCancel, undefined);
    assert.equal(systemOperationNotification({ ...running, id: undefined }, "Worker", true, cancel).onCancel, undefined);
    assert.equal(systemOperationNotification({ ...running, unit: "foreign.service" }, "Worker", true, cancel).onCancel, undefined);
  }
  assert.equal(systemOperationNotification({ id: "a".repeat(32), action: "test-update", state: "running" }, "Worker", true, cancel).onCancel, undefined);
});

test("cancelled commands are terminal without success, progress or another action", () => {
  const action = { id: "worker", unit: "worker.service", action: "update", state: "cancelled", result: "interrupted", progress: 30 };
  const notification = systemOperationNotification(action, "Update", true, () => {}, () => {});
  assert.equal(notification.state, "cancelled");
  assert.equal(notification.progress, undefined);
  assert.equal(notification.action, undefined);
  assert.equal(notification.onCancel, undefined);
  assert.equal(systemActionNeedsPolling(action), false);
  assert.equal(protocolOperationOutcome(action, action, true), "failed");
  const track = createSystemActionCompletionTracker();
  track({ ...action, state: "running", result: undefined });
  assert.equal(track(action), undefined);
  assert.equal(track({ ...action, state: "running", result: undefined }), undefined);
  assert.equal(track({ ...action, state: "succeeded", result: "success" }), undefined);
});

test("succeeded with an unknown result keeps read-only polling and its unknown card", () => {
  const action = { id: "worker", state: "succeeded", result: "unknown", progress: 100 };
  assert.equal(systemActionNeedsPolling(action), true);
  const notification = systemOperationNotification(action, "Worker", true, undefined, () => {});
  assert.equal(notification.state, "unknown");
  assert.equal(notification.progress, undefined);
  assert.equal(notification.action.label, "Проверить результат");
});

test("unknown admission and confirmed worker share one notification identity", () => {
  const id = "a".repeat(32);
  assert.equal(systemOperationId({ id }), systemOperationId({ id, unit: "worker.service", action: "mihomo-profile-update" }));
  assert.notEqual(systemOperationId({ id }), systemOperationId({ id: "b".repeat(32) }));
  assert.equal(systemOperationId({ unit: "legacy.service" }), "operation:system:legacy.service");
});

test("lost mutation response is reconciled by identity without a second POST", async () => {
  const calls = [];
  let id;
  const result = await submitSystemOperation(async (path, options) => {
    calls.push([path, options?.method || "GET"]);
    if (options?.method === "POST") {
      id = options.headers.get("X-Operation-ID");
      throw new Error("Connection lost");
    }
    assert.equal(path, `/application/operations/${id}`);
    return {id, state: "running"};
  }, "/application/action", {method: "POST"});
  assert.equal(result.id, id);
  assert.match(id, /^[0-9a-f]{32}$/);
  assert.deepEqual(calls.map((call) => call[1]), ["POST", "GET"]);
});

test("definite authorization failure is not retried or presented as a running operation", async () => {
  let calls = 0;
  await assert.rejects(submitSystemOperation(async () => {
    calls += 1;
    throw Object.assign(new Error("Unauthorized"), {status: 401});
  }, "/application/action", {method: "POST"}), /Unauthorized/);
  assert.equal(calls, 1);
});

test("malformed or foreign admission is reconciled only by fresh exact identity readback", async () => {
  const identity = "a".repeat(32);
  for (const invalid of [{ id: identity, state: "running", message: {} }, { id: "b".repeat(32), state: "succeeded" }, { id: identity }, { id: identity, state: " " }]) {
    const calls = [];
    const controller = new AbortController();
    const confirmed = { id: identity, state: "failed", message: "Worker failed", result: "exit-code" };
    const result = await submitSystemOperation(async (path, options) => {
      calls.push([path, options]);
      if (options.method === "POST") return invalid;
      assert.equal(path, `/application/operations/${identity}`);
      assert.equal(options.cache, "no-store");
      assert.equal(options.signal, controller.signal);
      return confirmed;
    }, "/application/action", { method: "POST", signal: controller.signal }, identity);
    assert.equal(result, confirmed);
    assert.equal(calls.length, 2);
    assert.deepEqual(calls.map(([, options]) => options.method || "GET"), ["POST", "GET"]);
  }
});

test("malformed or foreign readback remains unknown under the submitted ID", async () => {
  const identity = "a".repeat(32);
  for (const invalid of [{ id: identity, state: "succeeded", progress: 101 }, { id: "b".repeat(32), state: "succeeded" }, { id: identity }]) {
    let writes = 0;
    let reads = 0;
    await assert.rejects(submitSystemOperation(async (_path, options) => {
      if (options.method === "POST") { writes++; throw new Error("Lost reply"); }
      reads++; return invalid;
    }, "/application/action", { method: "POST" }, identity), error => error instanceof SystemOperationUnknown && error.id === identity);
    assert.equal(writes, 1);
    assert.equal(reads, 1);
  }
});

test("module presence and version changes wait for the same command to finish", () => {
  const started = {unit: "install-1.service", action: "protocol-install:mihomo"};
  for (const state of ["active", "activating", "running", "unknown"]) {
    assert.equal(protocolOperationOutcome(started, {...started, state, result:"success"}, true), "pending");
  }
  assert.equal(protocolOperationOutcome(started, {unit:"old.service",state:"succeeded",result:"success"}, true), "pending");
  assert.equal(protocolOperationOutcome(started, {...started,state:"failed",result:"exit-code"}, true), "failed");
  assert.equal(protocolOperationOutcome(started, {...started,state:"succeeded",result:"success"}, false), "pending");
  assert.equal(protocolOperationOutcome(started, {...started,state:"succeeded",result:"success"}, true), "success");
});

test("unknown final results cannot emit successful notifications or reload", () => {
  const action = {unit:"update-2.service",action:"update"};
  const track = createSystemActionCompletionTracker();
  track({...action,state:"running"});
  assert.equal(track({...action,state:"finished",result:"unknown"}),undefined);
  assert.equal(systemOperationNotification({...action,state:"finished",result:"unknown"},"Update",false).state,"unknown");
  assert.equal(systemOperationNotification({...action,state:"finished",result:"exit-code"},"Update",false).state,"error");
  assert.ok(track({...action,state:"succeeded",result:"success"}));
});

test("server commands refresh once after completion, never from historical or late polls", () => {
  const track = createSystemActionCompletionTracker();
  const action = { unit: "install-123.service", action: "protocol-install:mihomo" };
  assert.equal(track({ ...action, state: "queued", started_at: "api-time" }), undefined);
  assert.equal(track({ ...action, state: "running", started_at: "shell-time" }), undefined);
  assert.equal(track({ ...action, state: "unknown" }), undefined);
  const completed = track({ ...action, state: "succeeded" });
  assert.equal(systemActionNeedsReload(completed), true);
  assert.equal(track({ ...action, state: "succeeded" }), undefined);
  assert.equal(track({ ...action, state: "active" }), undefined);
  assert.equal(track({ ...action, state: "finished" }), undefined);

  const afterReload = createSystemActionCompletionTracker();
  assert.equal(afterReload({ ...action, state: "succeeded" }), undefined);
  assert.equal(afterReload({ ...action, state: "active" }), undefined);
  assert.equal(afterReload({ ...action, state: "finished" }), undefined);
  const next = { ...action, unit: "install-456.service", state: "active" };
  afterReload(next);
  assert.ok(afterReload({ ...next, state: "finished" }));
});

test("failures and read-only diagnostics do not trigger a page reload", () => {
  for (const outcome of [{ state: "failed" }, { state: "finished", result: "failed" }]) {
    const track = createSystemActionCompletionTracker();
    const action = { unit: "update-123.service", action: "update" };
    track({ ...action, state: "running" });
    assert.equal(track({ ...action, ...outcome }), undefined);
  }
  for (const action of ["protocol-install:mihomo", "protocol-remove:wireguard", "protocol-update:mihomo", "restart", "update", "test-update", "test-rollback", "safe-update", "kernel-update", "reboot"]) {
    assert.equal(systemActionNeedsReload({ action }), true, action);
  }
  for (const action of ["network-check", "integrity-check", "poweroff", "logging-config", "logs-clear", "service-action:wg:restart", ""]) {
    assert.equal(systemActionNeedsReload({ action }), false, action);
  }
});


test("unknown operations continue read-only reconciliation until confirmed terminal state", () => {
  for (const state of ["queued", "running", "unknown", "rebooting"])
    assert.equal(systemActionNeedsPolling({ state }), true);
  assert.equal(systemActionNeedsPolling({ state: "finished", result: "unknown" }), true);
  for (const state of ["succeeded", "failed", "cancelled"])
    assert.equal(systemActionNeedsPolling({ state }), false);
  assert.equal(systemActionNeedsPolling(null), false);
});
