import assert from "node:assert/strict";
import test from "node:test";
import { changedRoutingDraftKeys, rebaseRoutingDraft, routingControlDefaults } from "../../src/features/mihomo/routing-draft.ts";

test("restoring the displayed Mihomo settings leaves no unsaved edits", () => {
  const saved = { mode: "", strategy: "fallback", interval: "030", tolerance: 12 };
  assert.deepEqual(changedRoutingDraftKeys(saved, { ...routingControlDefaults, tolerance: 12 }), []);
  assert.deepEqual(changedRoutingDraftKeys(saved, { ...saved, strategy: "url-test", tolerance: 0 }), ["strategy", "tolerance"]);
  assert.deepEqual(changedRoutingDraftKeys(saved, { ...saved, interval: "" }), ["interval"]);
});

test("a save adopts server normalization and keeps edits made after submission", () => {
  const submitted = { strategy: "load-balance", interval: 45, tolerance: 20, rules: " example " };
  const saved = { ...submitted, load_balance_strategy: "consistent-hashing", rules: "example" };
  assert.strictEqual(rebaseRoutingDraft(saved, submitted, { ...submitted }), saved);
  assert.deepEqual(rebaseRoutingDraft(saved, submitted, { ...submitted, strategy: "fallback", interval: 30 }), {
    ...saved, strategy: "fallback", interval: 30,
  });
});
