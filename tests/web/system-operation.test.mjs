import assert from "node:assert/strict";
import test from "node:test";
import { submitSystemOperation, createSystemActionCompletionTracker, protocolOperationOutcome, systemActionNeedsReload, systemActionNeedsPolling, systemOperationNotification } from "../../src/control-panel/system-operation.ts";

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
