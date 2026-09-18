import assert from "node:assert/strict";
import test from "node:test";
import { aggregateTraffic, connectionOnline, trafficBytes } from "../../src/shared/lib/control-plane-ui.ts";

test("unavailable channels do not hide measured device and profile traffic", () => {
  const device = aggregateTraffic([{rx_bytes: 1024, tx_bytes: 512}, {stats_available: false, rx_bytes: null, tx_bytes: null}]);
  assert.equal(trafficBytes(device, "rx_bytes"), "≥ 1 KB");
  assert.equal(device.stats_partial, true);
  const profile = aggregateTraffic([device, {rx_bytes: 1024, tx_bytes: 0}]);
  assert.equal(profile.rx_bytes, 2048);
  assert.equal(profile.stats_partial, true);
  assert.equal(trafficBytes(aggregateTraffic([undefined]), "rx_bytes"), "—");
  assert.equal(aggregateTraffic([{rx_bytes: 0, tx_bytes: 0}]).stats_partial, false);
});

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
