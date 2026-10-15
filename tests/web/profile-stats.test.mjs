import assert from "node:assert/strict";
import test from "node:test";
import { profileStatsSnapshot } from "../../src/features/mihomo/workspace-refresh.ts";

const snapshot = { summary: { configured: 2, active: 1, rx_bytes: 1000, tx_bytes: 2000 }, connections: { one: { active: true, rx_bytes: 1000, tx_bytes: 2000 } } };

test("valid and unavailable telemetry preserves its values without inventing counters", () => {
  assert.deepEqual(profileStatsSnapshot(snapshot), snapshot);
  const unknown = { summary: { configured: 2, active: 0, stats_available: false, activity_available: false, rx_bytes: 0, tx_bytes: 0 }, connections: { one: { active: null, rx_bytes: null, tx_bytes: null, stats_available: false, activity_available: false } } };
  assert.deepEqual(profileStatsSnapshot(unknown), unknown);
  assert.deepEqual(profileStatsSnapshot({summary:{configured:1,active:0}}), {summary:{configured:1,active:0},connections:{}});
});

test("malformed stats and invalid activity cannot become an empty or active snapshot", () => {
  for (const value of [null, [], {}, {summary:null}, {summary:[]},
    {...snapshot,summary:{...snapshot.summary,active:"1"}}, {...snapshot,summary:{...snapshot.summary,active:3}},
    {...snapshot,summary:{...snapshot.summary,configured:-1}}, {...snapshot,summary:{...snapshot.summary,active:0.5}},
    {...snapshot,summary:{...snapshot.summary,activity_available:"false"}}, {...snapshot,connections:null},
    {...snapshot,connections:[]}, {...snapshot,connections:{one:[]}}, {...snapshot,connections:{one:{active:"false"}}}]) {
    assert.throws(()=>profileStatsSnapshot(value), Error);
  }
});

test("corrupt traffic and timing do not get coerced to plausible display values", () => {
  for (const field of ["rx_bytes","tx_bytes","latency_ms","handshake_age_s","last_handshake_age_s"]) {
    for (const value of [-1, Infinity, NaN, "1000"]) {
      assert.throws(()=>profileStatsSnapshot({...snapshot,summary:{...snapshot.summary,[field]:value}}), Error);
      assert.throws(()=>profileStatsSnapshot({...snapshot,connections:{one:{[field]:value}}}), Error);
    }
  }
});
