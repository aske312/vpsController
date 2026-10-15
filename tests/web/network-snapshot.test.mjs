import assert from "node:assert/strict";
import test from "node:test";
import { dnsChecksSnapshot, dnsStatusSnapshot, networkPageSnapshot } from "../../src/features/network/network-snapshot.ts";
import { networkRoutesSnapshot } from "../../src/features/mihomo/workspace-refresh.ts";
import { dns, network } from "./network-fixtures.mjs";

const snapshot = (value) => networkPageSnapshot(networkRoutesSnapshot(value));
test("page snapshots retain valid network evidence and DNS settings including legacy optional fields", () => {
  assert.equal(snapshot(network), network);
  assert.equal(dnsStatusSnapshot(dns), dns);
  const legacy = { ...network }; delete legacy.transport_endpoint_checks_by_domain; delete legacy.capabilities;
  assert.deepEqual(snapshot(legacy), legacy);
});

test("malformed page observations cannot replace renderable network data or endpoint revisions", () => {
  for (const change of [
    (row) => { row.server.public_ip = {}; }, (row) => { row.domains[0].resolved = {}; },
    (row) => { row.domains[0].dns.records.aaaa = null; }, (row) => { row.domains[0].edge.confidence = "ready"; },
    (row) => { row.listeners[0].port = 65536; }, (row) => { row.resolvers = [true]; },
    (row) => { row.transport_endpoints.revision = {}; }, (row) => { row.transport_endpoints.udp_relay_domains = "relay.example"; },
    (row) => { row.transport_endpoint_checks_by_domain["udp_relay:relay.example"].ready = "true"; },
    (row) => { row.transport_endpoint_checks_by_domain["udp_relay:relay.example"].consumers[0].label = {}; },
    (row) => { row.transport_endpoint_checks_by_domain["udp_relay:relay.example"].verified_connection.protocol = {}; },
    (row) => { row.capabilities.checks = [null]; },
  ]) { const value = structuredClone(network); change(value); assert.throws(() => snapshot(value)); }
  for (const value of [null, {}, { ...network, route: null }, { ...network, access: [] }]) assert.throws(() => snapshot(value));
});

test("malformed DNS snapshots cannot overwrite editable settings or publish false effects", () => {
  for (const change of [
    (row) => { row.settings.selected_id = {}; }, (row) => { row.settings.apply_wg = "false"; },
    (row) => { row.settings.revision = {}; }, (row) => { row.settings.custom = { name: "Custom", addresses: null, doh_url: "" }; },
    (row) => { row.settings.profiles = { wg: true }; }, (row) => { row.providers[0].addresses = "1.1.1.1"; },
    (row) => { row.protocol_effect.wg = {}; }, (row) => { row.protocol_effect_details.wg.matches_selected = "true"; },
    (row) => { row.system.managed = "false"; }, (row) => { row.recovery_required = "false"; },
    (row) => { row.providers.push(row.providers[0]); },
  ]) { const value = structuredClone(dns); change(value); assert.throws(() => dnsStatusSnapshot(value)); }
  for (const value of [null, {}, { ...dns, settings: null }, { ...dns, providers: {} }]) assert.throws(() => dnsStatusSnapshot(value));
});

test("DNS probe evidence requires booleans, finite timings and unambiguous provider identities", () => {
  const check = { id: "cloudflare", available: true, udp_ok: true, tcp_ok: false, doh_ok: true, latency_ms: 0.5 };
  assert.deepEqual(dnsChecksSnapshot({ items: [check] }), [check]);
  assert.deepEqual(dnsChecksSnapshot({ items: [] }), []);
  for (const value of [null, {}, { items: {} }, { items: [check, check] }, { items: [{ ...check, available: "true" }] },
    { items: [{ ...check, latency_ms: -1 }] }, { items: [{ ...check, udp_ms: Infinity }] }, { items: [{ ...check, id: "" }] }]) assert.throws(() => dnsChecksSnapshot(value));
});
