import assert from "node:assert/strict";
import test from "node:test";
import { createSystemActionCompletionTracker, systemActionNeedsReload } from "../../src/control-panel/system-operation.ts";

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
  for (const action of ["network-check", "integrity-check", "poweroff", ""]) {
    assert.equal(systemActionNeedsReload({ action }), false, action);
  }
});
