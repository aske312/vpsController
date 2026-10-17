import assert from "node:assert/strict";
import { test } from "node:test";
import { refreshResource } from "../../src/shared/lib/refresh-resource.ts";

test("a fast section publishes while another is pending, and failure does not erase it", async () => {
  const values = {};
  const failures = {};
  let rejectSlow;
  const slow = refreshResource(() => new Promise((_, reject) => { rejectSlow = reject; }),
    value => { values.slow = value; }, cause => { failures.slow = cause; });
  await refreshResource(async () => "fresh", value => { values.fast = value; }, assert.fail);
  assert.deepEqual(values, {fast: "fresh"});
  assert.deepEqual(failures, {});
  const error = new Error("unavailable");
  rejectSlow(error);
  await slow;
  assert.equal(failures.slow, error);
  assert.deepEqual(values, {fast: "fresh"});
});

test("cached preview is replaced in the same consumer, without publishing obsolete updates", async () => {
  let callbacks;
  let current = true;
  const values = [];
  const failures = [];
  await refreshResource(async updates => { callbacks = updates; return "cached"; },
    value => values.push(value), cause => failures.push(cause), () => current);
  callbacks.onRevalidated("fresh");
  assert.deepEqual(values, ["cached", "fresh"]);
  callbacks.onRevalidationError("offline");
  assert.deepEqual(failures, ["offline"]);
  current = false;
  callbacks.onRevalidated("obsolete");
  callbacks.onRevalidationError("obsolete failure");
  assert.deepEqual(values, ["cached", "fresh"]);
  assert.deepEqual(failures, ["offline"]);
});

test("a superseded read cannot publish success or failure", async () => {
  for (const outcome of ["success", "failure"]) {
    let finish;
    let current = true;
    const read = refreshResource(() => new Promise((resolve, reject) => {
      finish = () => outcome === "success" ? resolve("late") : reject(new Error("late"));
    }), assert.fail, assert.fail, () => current);
    current = false;
    finish();
    await read;
  }
});
