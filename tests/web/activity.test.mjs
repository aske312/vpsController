import test from "node:test";
import assert from "node:assert/strict";
import { activityCount, activityDescription, connectionActivity } from "../../src/features/mihomo/activity.ts";

test("partial activity preserves confirmed connections and identifies unknown channels", () => {
  const partial = connectionActivity([{active:true}, {active:false}, {active:null, activity_available:false}, undefined]);
  assert.deepEqual(partial, {configured:4, active:1, activity_available:false, activity_known:2, activity_unknown:2});
  assert.equal(activityCount(partial), "≥ 1");
  assert.match(activityDescription(partial), /2/);
  assert.equal(activityCount({...partial, active:0}), "≥ 0");
});

test("idle and absent telemetry remain distinct and an unavailable stale true is not counted", () => {
  assert.equal(activityCount(connectionActivity([{active:false}])), "0");
  assert.equal(activityCount(connectionActivity([{active:null}, undefined, {active:true, activity_available:false}])), "—");
  assert.equal(activityCount(undefined), "—");
  assert.equal(activityCount(connectionActivity([])), "0");
  assert.equal(activityCount({configured:4, active:2, activity_available:false}), "≥ 2");
});
