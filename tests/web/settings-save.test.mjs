import assert from "node:assert/strict";
import test from "node:test";
import { createSettingsSaveQueue } from "../../src/features/mihomo/settings-save.ts";
import { ApiRequestError, mutationFailureState } from "../../src/shared/lib/api-request.ts";
import { refreshWorkspaceSections } from "../../src/features/mihomo/workspace-refresh.ts";

test("manual save waits for older autosave and the newest edit stays on the server", async () => {
  const calls = [];
  let release;
  let stored;
  const first = new Promise((resolve) => { release = resolve; });
  const save = createSettingsSaveQueue(async (value) => {
    calls.push(value);
    if (value === "old") await first;
    stored = value;
    return value;
  });
  const old = save("old");
  const latest = save("latest");
  await Promise.resolve();
  assert.deepEqual(calls, ["old"]);
  release();
  assert.deepEqual(await Promise.all([old, latest]), ["old", "latest"]);
  assert.equal(stored, "latest");
});

test("failed save is not replayed and does not lose a later explicit edit", async () => {
  const calls = [];
  const save = createSettingsSaveQueue(async (value) => {
    calls.push(value);
    if (value === "old") throw new Error("connection lost");
    return value;
  });
  const results = await Promise.allSettled([save("old"), save("latest")]);
  assert.equal(results[0].status, "rejected");
  assert.equal(results[1].value, "latest");
  assert.deepEqual(calls, ["old", "latest"]);
});

test("interrupted mutations stay unknown while validation and authorization failures are errors", () => {
  for (const kind of ["network", "response"]) {
    assert.equal(mutationFailureState(new ApiRequestError("interrupted", kind)), "unknown");
  }
  for (const status of [401, 409, 422, 500]) {
    assert.equal(mutationFailureState(new ApiRequestError("rejected", "http", status)), "error");
  }
  assert.equal(mutationFailureState(new Error("validation")), "error");
});

test("workspace publishes available sections without waiting for slow reads and preserves failed data", async () => {
  const displayed = { status: "old", modules: "old", profiles: "old" };
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const pending = refreshWorkspaceSections(async (path) => {
    if (path === "modules") throw new Error("unavailable");
    if (path === "profiles") return slow;
    return "new";
  }, Object.fromEntries(Object.keys(displayed).map((key) => [key, {
    path: key, accept: (value) => { displayed[key] = value; },
  }])));
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(displayed, { status: "new", modules: "old", profiles: "old" });
  release("new");
  const failures = await pending;
  assert.deepEqual(Object.keys(failures), ["modules"]);
  assert.deepEqual(displayed, { status: "new", modules: "old", profiles: "new" });
});
