import assert from "node:assert/strict";
import test from "node:test";
import { createNotificationStore } from "../../src/shared/notifications/store.ts";

const operation = (id, state = "running", message = id) => ({ id, state, message, source: "test", title: id, kind: "operation" });

test("parallel commands and form errors retain independent cards", () => {
  const store = createNotificationStore();
  store.upsert(operation("a"));
  store.upsert(operation("b"));
  store.notify("modal", "Form", "error", "Validation failed");
  store.upsert(operation("a", "success"));
  assert.deepEqual(store.getSnapshot().map(({ state }) => state), ["success", "running", "error"]);
  store.clearCompleted();
  assert.deepEqual(store.getSnapshot().map(({ id }) => id), ["b"]);
});

test("repeated errors are counted without dropping different errors", () => {
  const store = createNotificationStore();
  store.notify("screen", "Form", "error", "A");
  store.notify("screen", "Form", "error", "B");
  store.notify("screen", "Form", "error", "A");
  store.notify("screen", "Form", "error", "");
  assert.deepEqual(store.getSnapshot().map(({ message, count }) => [message, count]), [["A", 2], ["B", 1]]);
});

test("a dismissed result stays dismissed during polling but a new run appears", () => {
  const store = createNotificationStore();
  store.upsert(operation("a", "error"));
  store.dismiss("a");
  store.upsert(operation("a", "error"));
  assert.equal(store.getSnapshot().length, 0);
  store.upsert(operation("a"));
  assert.equal(store.getSnapshot().length, 1);
});

test("old expiry cannot remove a restarted command; unknown outcomes persist", () => {
  let now = 0;
  const store = createNotificationStore(() => now);
  store.upsert(operation("a", "success"));
  now = 7999;
  store.upsert(operation("a"));
  store.upsert(operation("b", "unknown"));
  store.notify("screen", "Error", "error", "Failed");
  now = 100000;
  store.tick();
  store.dismiss("a");
  store.dismiss("b");
  assert.equal(store.getSnapshot().length, 3);
});

test("background network failures share one delayed card and clear on recovery", () => {
  let now = 0;
  const store = createNotificationStore(() => now);
  const failure = { message: "offline", network: true };
  store.setFailures("panel", "Panel", { overview: failure, services: failure });
  store.setFailures("mihomo", "Mihomo", { profiles: failure });
  now = 14999; store.tick();
  assert.equal(store.getSnapshot().length, 0);
  now = 15000; store.tick();
  assert.equal(store.getSnapshot().length, 1);
  store.dismiss("refresh:network");
  store.tick();
  assert.equal(store.getSnapshot().length, 0);
  store.clearFailures("panel");
  store.clearFailures("mihomo");
  store.setFailures("panel", "Panel", { overview: failure });
  now += 15000; store.tick();
  assert.equal(store.getSnapshot().length, 1);
  store.clearFailures("panel");
  assert.equal(store.getSnapshot().length, 0);
});

test("expected reconnects use the command card and persistent HTTP failures remain visible", () => {
  let now = 0;
  const store = createNotificationStore(() => now);
  store.upsert(operation("command"));
  store.setFailures("panel", "Panel", { connection: { message: "offline", network: true }, version: { message: "unauthorized" } });
  now = 30000; store.tick();
  assert.equal(store.getSnapshot().length, 2);
  store.upsert(operation("command", "success"));
  store.tick();
  assert.equal(store.getSnapshot().length, 3);
});

test("polling does not multiply a failed section and a repaired section can fail again", () => {
  const store = createNotificationStore();
  for (let i = 0; i < 4; i++) store.setFailures("panel", "Panel", { version: { message: "invalid response" } });
  assert.equal(store.getSnapshot()[0].count, 1);
  store.dismiss("refresh:panel:version");
  store.setFailures("panel", "Panel", { version: { message: "invalid response" } });
  assert.equal(store.getSnapshot().length, 0);
  store.setFailures("panel", "Panel", {});
  store.setFailures("panel", "Panel", { version: { message: "invalid response" } });
  assert.equal(store.getSnapshot().length, 1);
});

test("reading pauses expiration; provider instances and logout isolate messages", () => {
  let now = 0;
  const store = createNotificationStore(() => now);
  store.upsert(operation("a", "success"));
  now = 1000; store.pause();
  now = 20000; store.tick();
  assert.equal(store.getSnapshot().length, 1);
  store.resume();
  now = 26999; store.tick();
  assert.equal(store.getSnapshot().length, 1);
  now = 27000; store.tick();
  assert.equal(store.getSnapshot().length, 0);
  store.notify("screen", "Error", "error", "Failed");
  assert.equal(createNotificationStore().getSnapshot().length, 0);
  store.reset();
  assert.equal(store.getSnapshot().length, 0);
});

test("commands finishing while the center is focused get a full reading interval", () => {
  let now = 0;
  const store = createNotificationStore(() => now);
  store.upsert(operation("a"));
  now = 1000; store.pause();
  now = 20000; store.upsert(operation("a", "success"));
  now = 25000; store.resume();
  now = 32999; store.tick();
  assert.equal(store.getSnapshot().length, 1);
  now = 33000; store.tick();
  assert.equal(store.getSnapshot().length, 0);
});

test("adding a recovery action updates the snapshot and uses the current callback", () => {
  const store = createNotificationStore();
  store.upsert(operation("a", "unknown"));
  const before = store.getSnapshot();
  let calls = 0;
  store.upsert({ ...operation("a", "unknown"), action: { label: "Check", run: () => calls++ } });
  assert.notEqual(store.getSnapshot(), before);
  store.getSnapshot()[0].action.run();
  assert.equal(calls, 1);
});
