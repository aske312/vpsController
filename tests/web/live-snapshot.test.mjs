import assert from "node:assert/strict";
import test from "node:test";
import { clientSnapshot, clientsUnavailable, liveStatusSnapshot, overviewSnapshot, overviewUnavailable, resourceSnapshot, securityLiveObservation } from "../../src/control-panel/live-snapshot.ts";
import { directProtocolSnapshot, directProtocolUnavailable, tunnelLiveObservation } from "../../src/control-panel/protocol-snapshot.ts";

const resources = { load1: 0, cpu_percent: null, cpu_count: 2, memory_total: 100, memory_available: 50,
  disk_total: 200, disk_available: 100, network_rx: 1000, network_tx: 2000, uptime_s: 10 };
const client = { id: "phone", protocol: "wg", name: "Phone", public_key: "fixture-key", address: "10.0.0.2",
  rx_bytes: 10, tx_bytes: 20, handshake_age_s: 2, quality: "stable", latency_ms: 10, rx_bps: 5, tx_bps: 6, active_connections: 1,
  route_state: { state: "current", reason: "Observed", addresses: ["relay.example"], bindings: ["fixture"] } };
const tunnel = { active: true, peers: 1, online_peers: 1, interface_rx_bytes: 100, interface_tx_bytes: 200 };
const live = { resources, protocols: { wg: tunnel, awg: tunnel }, clients: [client],
  security: { firewall_active: true, fail2ban_active: false, ssh_listening: true }, unavailable: [] };
const full = { protocol: "wg", interface: "wg0", ...tunnel, service_active: true, service_enabled: true,
  active_since: "now", address: "10.0.0.1", listen_port: 51820, mtu: 1420, endpoints: 1, peer_rx_bytes: 10, peer_tx_bytes: 20,
  rx_errors: 0, tx_errors: 0, rx_dropped: 0, tx_dropped: 0,
  health: { state: "WORKS", base_state: "READY", checking: false, traffic_now: true, clients: 1, reason: "Observed" } };

test("valid live samples accept zero, unknown resource measurements and all supported client protocols", () => {
  assert.equal(liveStatusSnapshot(live).protocols.wg.active, true);
  assert.equal(resourceSnapshot(resources).cpu_percent, null);
  assert.equal(resourceSnapshot(resources).load1, 0);
  const clients = ["wg", "awg", "shadowsocks", "vless-reality-xhttp", "hysteria2", "tuic", "trojan", "openvpn", "ikev2"]
    .map((protocol) => ({ ...client, protocol, endpoint: null, handshake_age_s: null, latency_ms: null }));
  assert.equal(clientSnapshot(clients).length, 9);
  assert.deepEqual(clientSnapshot([]), []);
});

test("stale and explicitly unavailable resource groups cannot produce live measurements", () => {
  const stale = resourceSnapshot({ ...resources, stale: true });
  for (const key of Object.keys(resources)) assert.equal(stale[key], null);
  const partial = resourceSnapshot({ ...resources, observations: { network: { available: false, checked_at: "now", reason: "read failed" } } });
  assert.equal(partial.network_rx, null);
  assert.equal(partial.memory_available, 50);
  for (const bad of [{ ...resources, network_rx: "1" }, { ...resources, cpu_percent: Infinity }, { ...resources, observations: { cpu: [] } }, { ...resources, stale: "false" }]) {
    assert.throws(() => resourceSnapshot(bad));
  }
});

test("a damaged live source becomes unknown without hiding independent valid sources", () => {
  for (const value of [null, { ...tunnel, active: "true" }, { ...tunnel, peers: 0 }, { ...tunnel, interface_rx_bytes: -1 }]) {
    const next = liveStatusSnapshot({ ...live, protocols: { ...live.protocols, wg: value } });
    assert.equal(next.protocols.wg, null);
    assert.deepEqual(next.protocols.awg, tunnel);
    assert.equal(next.clients[0].id, "phone");
    assert.deepEqual(next.unavailable, ["wg"]);
  }
  assert.equal(liveStatusSnapshot({ ...live, unavailable: ["wg"] }).protocols.wg, null);
  assert.equal(liveStatusSnapshot({ ...live, security: { ...live.security, firewall_active: "false" } }).security, null);
  for (const bad of [null, [], { ...live, protocols: [] }, { ...live, unavailable: {} }, { ...live, resources: {} }]) assert.throws(() => liveStatusSnapshot(bad));
});

test("malformed client collections cannot become empty or deleted observations", () => {
  for (const value of [{}, [null], [{ ...client, protocol: "foreign" }], [{ ...client, rx_bytes: "10" }], [{ ...client, active_sources: {} }],
    [{ ...client, route_state: { ...client.route_state, bindings: {} } }], [client, client]]) {
    assert.throws(() => clientSnapshot(value));
    const next = liveStatusSnapshot({ ...live, clients: value });
    assert.equal(next.clients, null);
    assert.ok(next.unavailable.includes("clients"));
    assert.equal(next.protocols.wg.active, true);
  }
});

test("failed client observations retain identities and cumulative counters while clearing live activity and route assurance", () => {
  const next = clientsUnavailable(clientSnapshot([client]));
  assert.equal(next[0].name, "Phone");
  assert.equal(next[0].rx_bytes, 10);
  assert.equal(next[0].quality, "unknown");
  assert.equal(next[0].observation_failed, true);
  assert.equal(next[0].route_state.state, "unknown");
  for (const key of ["handshake_age_s", "latency_ms", "rx_bps", "tx_bps", "active_connections"]) assert.equal(next[0][key], undefined);
  const fresh = clientSnapshot([client])[0];
  assert.equal(fresh.quality, "stable");
  assert.equal(fresh.observation_failed, false);
});

test("cheap WG observations do not borrow a previous ICMP quality assessment", () => {
  const cheap = { ...client };
  delete cheap.quality;
  delete cheap.latency_ms;
  const next = clientSnapshot([cheap])[0];
  assert.equal(next.quality, "unknown");
  assert.equal(next.latency_ms, undefined);
  assert.equal(next.handshake_age_s, 2);
  assert.equal(next.observation_failed, false);
});

test("partial interface telemetry cannot restore service readiness or sticky WORKS after a failed full observation", () => {
  const known = directProtocolSnapshot(full, "wg");
  const running = tunnelLiveObservation(known, tunnel);
  assert.equal(running.health.state, "READY");
  assert.equal(running.health.traffic_now, false);
  const unknown = tunnelLiveObservation(directProtocolUnavailable(known), tunnel);
  assert.equal(unknown.observation_failed, true);
  assert.equal(unknown.health.state, "WARN");
  assert.equal(tunnelLiveObservation(known, null).observation_failed, true);
  assert.equal(tunnelLiveObservation(known, { ...tunnel, active: false }).observation_failed, true);
  assert.equal(tunnelLiveObservation(undefined, tunnel), undefined);
  assert.equal(directProtocolSnapshot(full, "wg").observation_failed, false);
});

test("overview failures preserve server identity but remove current protocol and resource assurance", () => {
  const value = { server: { name: "Fixture", public_ip: "203.0.113.1", city: "City", country: "Country", country_code: "XX", uptime_s: 10 },
    resources, protocols: { wg: { interface: "wg0", port: 51820, active: true }, awg: { interface: "awg0", port: 51821, active: false } } };
  const known = overviewSnapshot(value);
  const unknown = overviewUnavailable(known);
  assert.equal(unknown.server.name, "Fixture");
  assert.equal(unknown.protocols.wg.active, null);
  assert.equal(unknown.resources.network_rx, null);
  assert.equal(overviewSnapshot(value).protocols.wg.active, true);
  assert.throws(() => overviewSnapshot({ ...value, protocols: { wg: {} } }));
});

test("missing security telemetry is unknown rather than active or stopped, and independent policy data survives", () => {
  const previous = { firewall: { active: true, rules: ["fixture"] }, ssh: { active: false, password_authentication: "no" }, fail2ban: { active: true } };
  const unknown = securityLiveObservation(previous, null);
  assert.equal(unknown.live_observation_failed, true);
  for (const key of ["firewall", "ssh", "fail2ban"]) assert.equal(unknown[key].active, null);
  assert.deepEqual(unknown.firewall.rules, ["fixture"]);
  const observed = securityLiveObservation(unknown, live.security);
  assert.equal(observed.live_observation_failed, false);
  assert.equal(observed.firewall.active, true);
  assert.equal(observed.fail2ban.active, false);
  assert.equal(observed.ssh.active, true);
  assert.equal(securityLiveObservation(null, live.security), null);
});
