import assert from "node:assert/strict";
import test from "node:test";
import { applicationActionState, bytes } from "../app/lib/control-plane-ui.ts";

test("small traffic rates and missing telemetry never display undefined units", () => {
  assert.equal(bytes(0.5), "1 B");
  for (const value of [0, -1, NaN, Infinity]) assert.equal(bytes(value), "0 B");
  assert.equal(bytes(1024), "1 KB");
});

test("only confirmed terminal operations display DONE", () => {
  for (const state of ["queued", "active", "activating", "running", "rebooting", "powering-off"]) {
    assert.equal(applicationActionState({ state, result: "success" }), "RUNNING");
  }
  assert.equal(applicationActionState({ state: "failed", result: "exit-code" }), "FAILED");
  assert.equal(applicationActionState({ state: "finished", result: "interrupted" }), "FAILED");
  assert.equal(applicationActionState({ state: "succeeded", result: "success" }), "DONE");
  assert.equal(applicationActionState({}), "UNKNOWN");
});
