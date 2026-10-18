import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createServer } from "vite";

const loader = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false }, appType: "custom", optimizeDeps: { noDiscovery: true } });
after(() => loader.close());
const { domainRouteSummary, relayRouteSummary, serverAddressSummary } = await loader.ssrLoadModule("/src/features/network/route-summary.ts");

const domain = { value: "edge.example.com", role: "CDN endpoint", source: "settings", endpoint_kind: "cdn", resolved: ["203.0.113.2"], matches_origin: false, route: "proxy_or_cdn" };
const check = { kind: "cdn", domain: domain.value, status: "ready", ready: true, verification: "end_to_end", message: "ok", binding_id: "binding", check_id: "nonce", desired_revision: 2, checked_revision: 2, checked_at: "2026-10-18T00:00:00Z" };
const host = { state: "connected", routes: [{ transport: "tcp", state: "listening" }] };

test("DNS and agent contact never imply a verified connection", () => {
  assert.equal(domainRouteSummary({ ...domain, role: "panel", endpoint_kind: undefined }, []).state, "dns");
  assert.equal(domainRouteSummary({ ...domain, resolved: [], route: "unresolved" }, []).state, "error");
  assert.equal(domainRouteSummary(domain, []).state, "unchecked");
  assert.equal(domainRouteSummary(domain, [{ ...check, verification: "dns_only" }]).state, "unchecked");
  assert.equal(domainRouteSummary(domain, [{ ...check, ready: false }]).state, "unchecked");
  assert.equal(domainRouteSummary(domain, [{ ...check, checked_revision: 1 }]).state, "unchecked");
  assert.equal(domainRouteSummary(domain, [{ ...check, check_id: null }]).state, "unchecked");
  assert.equal(domainRouteSummary(domain, [check]).state, "verified");
  assert.equal(relayRouteSummary({ ...host, routes: [] }).state, "connected");
  assert.equal(relayRouteSummary(host).state, "listening");
});

test("retirement, failed reads and partial checks override earlier success", () => {
  assert.equal(domainRouteSummary({ ...domain, status: "stale" }, [check]).state, "stale");
  assert.equal(domainRouteSummary(domain, [check], true).state, "unknown");
  assert.equal(domainRouteSummary(domain, [{ ...check, domain: "other.example.com" }]).state, "unchecked");
  const combined = { ...domain, role: "CDN endpoint, TLS relay" };
  assert.equal(domainRouteSummary(combined, [check]).state, "unchecked");
  assert.equal(domainRouteSummary(combined, [check, { ...check, kind: "tls_relay" }]).state, "verified");
  assert.equal(domainRouteSummary(combined, [check, { ...check, kind: "tls_relay", status: "error" }]).state, "error");
});

test("an external DNS address does not establish a proxy or a transport protocol", () => {
  const external = domainRouteSummary(domain, []);
  assert.doesNotMatch(external.connection, /PROXY|TCP|UDP|Cloudflare/i);
  const cloudflare = domainRouteSummary({ ...domain, edge: { provider: "Cloudflare", confidence: "high" } }, []);
  assert.match(cloudflare.connection, /Cloudflare/);
  assert.notEqual(domainRouteSummary({ ...domain, matches_origin: true, resolved: ["203.0.113.1", "203.0.113.2"] }, [], false, ["203.0.113.1"]).connection, external.connection);
});

test("IP inventory failures and pending changes cannot display a confirmed assignment", () => {
  const ip = { address: "203.0.113.1", family: "ipv4", interfaces: ["eth0"], source: "interface", presence: "present" };
  assert.equal(serverAddressSummary(ip).state, "present");
  assert.equal(serverAddressSummary(ip, true).state, "unknown");
  assert.equal(serverAddressSummary(ip, false, true).state, "pending");
  assert.equal(serverAddressSummary({ ...ip, presence: "missing" }).state, "error");
});

test("Relay summary includes forwarding faults and never reuses stale liveness", () => {
  const offline = relayRouteSummary({ ...host, name: "Amsterdam", state: "unknown", error: "Agent connection timed out" });
  assert.equal(offline.state, "error");
  assert.match(offline.detail, /Amsterdam.*timed out/);
  for (const state of ["failed", "missing", "drift"]) assert.equal(relayRouteSummary({ ...host, routes: [{ transport: "tcp", state }] }).state, "error");
  assert.equal(relayRouteSummary({ ...host, routes: [{ transport: "tcp", state: "listening", check: { state: "failed" } }] }).state, "error");
  assert.equal(relayRouteSummary({ ...host, routes: [{ transport: "udp", state: "unknown" }] }).state, "unknown");
  assert.equal(relayRouteSummary(host, true).state, "unknown");
  assert.equal(relayRouteSummary(host, false, true).state, "pending");
  assert.equal(relayRouteSummary({ ...host, state: "unknown" }).state, "unknown");
  assert.equal(relayRouteSummary({ ...host, state: "unknown" }, false, false, true).state, "pending");
  assert.notEqual(relayRouteSummary({ ...host, routes: [], state: "unknown" }).connection, relayRouteSummary({ ...host, routes: [] }).connection);
});
