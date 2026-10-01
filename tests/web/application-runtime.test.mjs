import assert from "node:assert/strict";
import test from "node:test";
import { applicationRuntime } from "../../src/features/application/runtime-state.ts";

test("missing application observations stay unknown rather than stopped", () => {
  assert.equal(applicationRuntime().state, "unknown");
  assert.equal(applicationRuntime({ active: null }).state, "unknown");
  assert.equal(applicationRuntime({ active: true, runtime: { state: "unknown", reason: "Unavailable", checked_at: null } }).state, "unknown");
  assert.equal(applicationRuntime({ State: "running" }).label, "Running");
  assert.equal(applicationRuntime({ State: "error" }).label, "Error");
  assert.equal(applicationRuntime({ active: false }).label, "Stopped");
});
