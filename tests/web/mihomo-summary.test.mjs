import assert from "node:assert/strict";
import test from "node:test";
import { createMihomoSummaryStore } from "../../src/features/overview/mihomo-summary.ts";

const profile = { id: "profile-1", name: "Test", channels: ["transport-reality"], connections: [{ id: "connection-1", component: "transport-reality" }] };
const status = { active: true, profiles: 1, credentials: 1, channels_in_use: ["transport-reality"] };
const responses = {
  "/mihomo/status": status,
  "/mihomo/modules": { items: [{ id: "transport-reality", installed: true, active: true }] },
  "/mihomo/profiles": { items: [profile] },
  "/mihomo/stats": { items: [{ id: profile.id, summary: { rx_bytes: 120, tx_bytes: 30 } }] },
};
const tick = () => new Promise((resolve) => setImmediate(resolve));

test("profiles render while statistics are pending and survive a network failure", async () => {
  let rejectStats;
  const stats = new Promise((_, reject) => { rejectStats = reject; });
  const store = createMihomoSummaryStore(async (path) => path.endsWith("/stats") ? stats : responses[path]);
  const published = [];
  const unsubscribe = store.subscribe(() => published.push(store.getSnapshot()));
  const refresh = store.refresh();
  await tick();
  assert.deepEqual(store.getSnapshot().profiles, [profile]);
  assert.equal(store.getSnapshot().status.active, true);
  assert.equal(store.getSnapshot().profileStats, null);
  assert.ok(published.some((snapshot) => snapshot.profiles?.length === 1));
  rejectStats(new TypeError("Failed to fetch"));
  await refresh;
  assert.deepEqual(store.getSnapshot().profiles, [profile]);
  assert.deepEqual(Object.keys(store.getSnapshot().errors), ["profileStats"]);
  unsubscribe();
});

test("each failing endpoint leaves the other sections available", async () => {
  const sections = { status: "status", modules: "modules", profiles: "profiles", stats: "profileStats" };
  for (const [endpoint, section] of Object.entries(sections)) {
    const store = createMihomoSummaryStore(async (path) => {
      if (path.endsWith(`/${endpoint}`)) throw new Error("unavailable");
      return responses[path];
    });
    await store.refresh();
    const snapshot = store.getSnapshot();
    assert.equal(snapshot[section], null);
    assert.deepEqual(Object.keys(snapshot.errors), [section]);
    for (const other of Object.values(sections).filter((key) => key !== section)) assert.notEqual(snapshot[other], null);
  }
});

test("failed refresh preserves previous data, then recovers and accepts an empty profile list", async () => {
  let phase = "success";
  const store = createMihomoSummaryStore(async (path) => {
    if (phase === "failure") throw new Error("offline");
    if (phase === "empty" && path.endsWith("/profiles")) return { items: [] };
    return responses[path];
  });
  await store.refresh();
  phase = "failure";
  await store.refresh(true);
  assert.deepEqual(store.getSnapshot().profiles, [profile]);
  assert.deepEqual(store.getSnapshot().profileStats[profile.id], { rx_bytes: 120, tx_bytes: 30 });
  assert.equal(Object.keys(store.getSnapshot().errors).length, 4);
  phase = "empty";
  await store.refresh(true);
  assert.deepEqual(store.getSnapshot().profiles, []);
  assert.deepEqual(store.getSnapshot().errors, {});
});

test("invalid profile response is unavailable, never a successful empty list", async () => {
  const store = createMihomoSummaryStore(async (path) => path.endsWith("/profiles") ? {} : responses[path]);
  await store.refresh();
  assert.equal(store.getSnapshot().profiles, null);
  assert.ok(store.getSnapshot().errors.profiles);
});

test("concurrent reads share a refresh, fresh data is cached, and stores remain isolated", async () => {
  let calls = 0;
  const store = createMihomoSummaryStore(async (path) => { calls++; return responses[path]; });
  const refresh = store.refresh();
  assert.equal(store.refresh(true), refresh);
  await refresh;
  await store.refresh();
  assert.equal(calls, 4);
  await store.refresh(true);
  assert.equal(calls, 8);
  const other = createMihomoSummaryStore(async (path) => responses[path]);
  assert.equal(other.getSnapshot().profiles, null);
});
