import assert from "node:assert/strict";
import test from "node:test";
import { directProtocolSnapshot, directProtocolUnavailable } from "../../src/control-panel/protocol-snapshot.ts";

const value = { protocol: "vless-reality-xhttp", interface: "Xray", active: true, service_active: true, service_enabled: true,
  active_since: "now", address: "", listen_port: 443, mtu: 0, peers: 1, online_peers: 0, endpoints: 0,
  peer_rx_bytes: 20, peer_tx_bytes: 30, interface_rx_bytes: 20, interface_tx_bytes: 30, rx_errors: 0, tx_errors: 0, rx_dropped: 0, tx_dropped: 0,
  routes: { direct: { enabled: true }, cdn: { enabled: true, available_domains: ["edge.example"], confirmed_domains: ["edge.example"] } } };
test("valid stream observations keep routes and existing defaults for optional monitoring fields", () => {
  const result = directProtocolSnapshot(value, value.protocol);
  assert.equal(result.observation_failed, false);
  assert.deepEqual(result.routes, value.routes);
  assert.deepEqual(result.resources.items, []);
  assert.deepEqual(result.history.events, []);
  assert.equal(result.history.received_bytes, 20);
  assert.equal(result.diagnostics.status, "pending");
});

test("Direct diagnostic endpoint metadata coexists with numeric tunnel observations", () => {
  const live = { endpoint: "203.0.113.10", resolved: ["203.0.113.10"], ports: [8443], transport: "HYSTERIA2" };
  const source = { ...value, diagnostics: { status: "healthy", live, checks: [], findings: [] } };
  assert.deepEqual(directProtocolSnapshot(source, value.protocol).diagnostics.live, live);
  assert.deepEqual(directProtocolSnapshot({ ...source, diagnostics: { ...source.diagnostics, live: { ...live, ports: [] } } }, value.protocol).diagnostics.live.ports, []);
  for (const malformed of [{ endpoint: {} }, { transport: [] }, { resolved: [null] }, { resolved: {} }, { ports: ["443"] }, { ports: [0] }, { ports: [65536] }, { ports: [1.5] }, { ports: null, latency_ms: "0" }]) {
    assert.throws(() => directProtocolSnapshot({ ...source, diagnostics: { ...source.diagnostics, live: { ...live, ...malformed } } }, value.protocol));
  }
});

test("malformed or foreign protocol observations cannot publish activity, routes or monitoring rows", () => {
  for (const change of [
    (row) => { row.protocol = "wg"; }, (row) => { row.active = "true"; }, (row) => { row.listen_port = 65536; },
    (row) => { row.interface_rx_bytes = {}; }, (row) => { row.routes.cdn.enabled = "true"; },
    (row) => { row.routes.cdn.available_domains = "edge.example"; }, (row) => { row.routes.cdn.endpoint = {}; },
    (row) => { row.resources = { items: {} }; }, (row) => { row.history = { latency_avg_ms: "0" }; },
    (row) => { row.history = { events: [null] }; }, (row) => { row.diagnostics = { checks: { ok: true } }; },
    (row) => { row.diagnostics = { live: { latency_ms: "0" } }; }, (row) => { row.health = { state: "READY", clients: 0 }; },
    (row) => { row.editable_settings = [{ key: "Port", label: "Port", type: "number", value: Infinity }]; },
  ]) { const candidate = structuredClone(value); change(candidate); assert.throws(() => directProtocolSnapshot(candidate, value.protocol)); }
  for (const candidate of [null, {}, [], { ...value, routes: null }]) assert.throws(() => directProtocolSnapshot(candidate, value.protocol));
});

test("failed observations retain counters, mark unknown and remove route eligibility until valid readback", () => {
  assert.equal(directProtocolUnavailable(undefined), undefined);
  const good = directProtocolSnapshot(value, value.protocol);
  const unknown = directProtocolUnavailable(good);
  assert.equal(unknown.observation_failed, true);
  assert.equal(unknown.interface_rx_bytes, 20);
  assert.equal(unknown.routes, undefined);
  assert.equal(unknown.health.state, "WARN");
  assert.equal(unknown.health.traffic_now, false);
  assert.equal(good.routes.cdn.enabled, true);
  assert.equal(directProtocolSnapshot(value, value.protocol).routes.cdn.enabled, true);
});
