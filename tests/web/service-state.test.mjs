import assert from "node:assert/strict";
import test from "node:test";
import { runtimeState, servicesSummary } from "../../src/features/services/service-state.ts";

const item = {id: "api", installed: true, active: true, state: "active", substate: "running", enabled: false, unit_file_state: "disabled", restarts: 0};
const status = (items = [item], failed_units = 0) => ({items, failed_units, reboot_required: false});

test("unknown observations and initial loading are not stopped or healthy zeroes", () => {
  const unknown = {...item, active: false, state: "unknown", unit_present: null, unit_file_state: "unknown", restarts: null};
  assert.equal(runtimeState(unknown), "unknown");
  for (const value of [null, status([unknown], null)]) {
    const summary = servicesSummary(value);
    assert.equal(summary.tone, "attention");
    for (const metric of ["active", "failed", "enabled", "restarts"]) assert.equal(summary[metric], "—");
  }
});

test("runtime is independent of autostart and a failed source does not hide other metrics", () => {
  assert.equal(runtimeState(item), "running");
  const summary = servicesSummary(status([item], null));
  assert.equal(summary.active, "1/1");
  assert.equal(summary.enabled, "0/1");
  assert.equal(summary.failed, "—");
  assert.equal(summary.restarts, "0");
  assert.equal(summary.tone, "attention");
  assert.equal(servicesSummary(status([{...item, active: false, state: "inactive"}])).active, "0/1");
});

test("canonical runtime wins over legacy booleans; older responses remain readable", () => {
  assert.equal(runtimeState({...item, runtime: {state: "unknown"}}), "unknown");
  for (const [state, expected] of [["inactive", "stopped"], ["failed", "error"], ["activating", "unknown"], ["deactivating", "unknown"], ["", "unknown"]]) {
    assert.equal(runtimeState({...item, active: false, state}), expected);
  }
  assert.equal(servicesSummary(status([{...item, runtime: {state: "error"}}])).tone, "attention");
});
