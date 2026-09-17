import assert from "node:assert/strict";
import test from "node:test";
import { connectionOnline, trafficBytes } from "../../src/shared/lib/control-plane-ui.ts";

test("unknown counters stay unknown, measured zero is displayed as zero", () => {
  assert.equal(trafficBytes(undefined, "rx_bytes"), "—");
  assert.equal(trafficBytes({ rx_bytes: null }, "rx_bytes"), "—");
  assert.equal(trafficBytes({ rx_bytes: 0, stats_available: false }, "rx_bytes"), "—");
  assert.notEqual(trafficBytes({ rx_bytes: 0, stats_available: true }, "rx_bytes"), "—");
});

test("service state, previous endpoint and unavailable activity do not mean online", () => {
  assert.equal(connectionOnline({ service_active: true, endpoint: "192.0.2.1:1234" }), false);
  assert.equal(connectionOnline({ active: null }), false);
  assert.equal(connectionOnline({ active: false, endpoint: "192.0.2.1:1234" }), false);
  assert.equal(connectionOnline({ active: true }), true);
});
