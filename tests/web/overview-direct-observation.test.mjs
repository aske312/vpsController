import assert from "node:assert/strict";
import test from "node:test";
import { directChannelObservation } from "../../src/features/overview/direct-observation.ts";

const status = { interface: "wg0", online_peers: 2, service_active: true, health: { state: "WORKS" } };
const clients = [{ rx_bps: 20, tx_bps: 30, active_connections: 1, latency_ms: 10 }, { rx_bps: 50, tx_bps: 60, active_connections: 1, latency_ms: 20 }];

test("overview aggregates observed client activity without turning missing directions or failed clients into zero traffic", () => {
  const observed = directChannelObservation(status, clients, { rx: 100, tx: 200 }, false);
  assert.equal(observed.rx, 70);
  assert.equal(observed.tx, 90);
  assert.equal(observed.sessions, 2);
  assert.equal(observed.avgLatency, 15);
  const partial = directChannelObservation(status, [{ tx_bps: 0 }], { rx: null, tx: 200 }, false);
  assert.equal(partial.rx, null);
  assert.equal(partial.tx, 0);
  const failedClients = directChannelObservation(status, clients.map((client) => ({ ...client, observation_failed: true })), { rx: null, tx: null }, false);
  assert.equal(failedClients.rx, null);
  assert.equal(failedClients.tx, null);
  assert.equal(failedClients.avgLatency, null);
});

test("an unconfirmed protocol snapshot cannot contribute WORKS, sessions, rate or RTT", () => {
  for (const [value, failed] of [[status, true], [{ ...status, observation_failed: true }, false], [undefined, false]]) {
    const result = directChannelObservation(value, clients, { rx: 100, tx: 200 }, failed);
    assert.equal(result.inUse, false);
    for (const key of ["sessions", "rx", "tx", "avgLatency", "serviceActive", "configured"]) assert.equal(result[key], null);
  }
});
