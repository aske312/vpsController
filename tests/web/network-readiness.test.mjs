import assert from "node:assert/strict";
import test from "node:test";
import { configuredCdnDomains, confirmedRouteAddresses, directRouteAddresses, endpointCheckReady, endpointSelectable, endpointCheckForConsumer } from "../../src/shared/lib/network-readiness.ts";

test("configured CDN inventory is independent of live checks and excludes other domains", () => {
  assert.deepEqual(configuredCdnDomains(null), []);
  assert.deepEqual(configuredCdnDomains({}), []);
  assert.deepEqual(configuredCdnDomains({transport_endpoints:{cdn_domain:{}},domains:[null,{}, {endpoint_kind:'cdn',value:{}}]}), []);
  assert.deepEqual(configuredCdnDomains({transport_endpoints: {cdn_domain:'EDGE.Example',cdn_domains:['edge.example','second.example']},domains:[{value:'discovered.example',role:'CDN endpoint'},{value:'panel.example',role:'panel'}]}), ['edge.example','second.example','discovered.example']);
});

function ready(kind, domain = "same.example") {
  return { kind, domain, status: "ready", ready: true, verification: "end_to_end",
    binding_id: `${kind}-binding`, check_id: `${kind}-check`, checked_at: "2026-10-11T12:00:00Z",
    desired_revision: 2, checked_revision: 2, resolved: ["203.0.113.10"], matches_origin: true, route: "direct", message: "" };
}

test("Mihomo route selection rejects DNS-only, incomplete and stale evidence", () => {
  const check = ready("cdn");
  assert.equal(endpointCheckReady(check), true);
  for (const fields of [{ verification: "dns_only" }, { status: "unchecked" }, { ready: false },
    { desired_revision: undefined }, { checked_revision: 1 }, { checked_revision: true },
    { binding_id: "" }, { check_id: "" }, { checked_at: null }]) {
    assert.equal(endpointCheckReady({ ...check, ...fields }), false);
  }
  const dnsOnly = { domains: [{ value: "same.example", role: "CDN TLS UDP", resolved: ["203.0.113.10"], route: "proxy_or_cdn" }] };
  for (const kind of ["cdn", "tls", "udp"]) assert.deepEqual(confirmedRouteAddresses(dnsOnly, kind), []);
});

test("same hostname has independent bindings and origin privacy does not deny proven readiness", () => {
  const cdn = ready("cdn");
  const tls = { ...ready("tls_relay"), checked_revision: 1 };
  const udp = { ...ready("udp_relay"), status: "unchecked", ready: false };
  const status = { transport_endpoint_checks_by_domain: { "cdn:same.example": cdn, "tls_relay:same.example": tls, "udp_relay:same.example": udp } };
  assert.deepEqual(confirmedRouteAddresses(status, "cdn"), ["same.example"]);
  assert.deepEqual(confirmedRouteAddresses(status, "tls"), []);
  assert.deepEqual(confirmedRouteAddresses(status, "udp"), []);
});

test("valid primary evidence is deduplicated and old responses without revision stay unavailable", () => {
  const check = ready("tls_relay", "TLS.Example");
  const status = { transport_endpoint_checks: { tls_relay: check }, transport_endpoint_checks_by_domain: { "tls_relay:tls.example": check } };
  assert.deepEqual(confirmedRouteAddresses(status, "tls"), ["tls.example"]);
  assert.deepEqual(confirmedRouteAddresses({ transport_endpoint_checks: { tls_relay: { ...check, desired_revision: undefined } } }, "tls"), []);
  assert.deepEqual(confirmedRouteAddresses(null, "cdn"), []);
});

test("first connection can select an active unchecked route without claiming READY", () => {
  const check = { ...ready("tls_relay"), status: "unchecked", ready: false, verification: "dns_only", selectable: true };
  const status = { transport_endpoint_checks: { tls_relay: check } };
  assert.equal(endpointSelectable(check), true);
  assert.equal(endpointCheckReady(check), false);
  assert.deepEqual(confirmedRouteAddresses(status, "tls", true), ["same.example"]);
  assert.deepEqual(confirmedRouteAddresses(status, "tls"), []);
  for (const fields of [{ selectable: false }, { binding_id: "" }, { resolved: [] }]) {
    assert.equal(endpointSelectable({ ...check, ...fields }), false);
  }
});

test('direct TLS and UDP choices require origin DNS and an active role, without requiring traffic proof', () => {
  for (const [kind, routeKind] of [['tls', 'tls_relay'], ['udp', 'udp_relay']]) {
    const check = { ...ready(routeKind), status: 'warning', ready: false, matches_origin: true, selectable: true };
    const status = { transport_endpoint_checks: { [routeKind]: check }, transport_endpoint_checks_by_domain: { [`${routeKind}:same.example`]: check } };
    assert.deepEqual(directRouteAddresses(status, kind), ['same.example']);
    for (const changes of [{matches_origin:false},{selectable:false},{resolved:[]}]) {
      assert.deepEqual(directRouteAddresses({transport_endpoint_checks:{[routeKind]:{...check,...changes}}},kind),[]);
    }
  }
  assert.deepEqual(directRouteAddresses(null,'udp'),[]);
});

test("consumer selection cannot reuse another connection's result or hide route removal", () => {
  const check = { ...ready("cdn"), consumer_id: "direct:phone", consumers: [{ id: "direct:tablet", label: "Tablet" }], failed_checks: 4 };
  assert.equal(endpointCheckForConsumer(check, "direct:phone"), check);
  assert.equal(endpointCheckForConsumer(check, ""), check);
  const selected = endpointCheckForConsumer(check, "direct:tablet");
  assert.equal(endpointCheckReady(selected), false);
  assert.equal(selected.status, "unchecked");
  assert.equal(selected.checked_at, null);
  assert.equal(selected.failed_checks, 0);
  assert.equal(selected.consumers, check.consumers);
  assert.equal(check.status, "ready");
  const saved = { ...check, consumer_id: undefined, verified_connection: { consumer_id: "direct:phone" } };
  assert.equal(endpointCheckForConsumer(saved, "direct:phone"), saved);
  for (const fields of [{ status: "unresolved" }, { status: "stale", binding_id: undefined }]) {
    const unavailable = { ...check, ...fields, ready: false };
    assert.equal(endpointCheckForConsumer(unavailable, "direct:tablet"), unavailable);
  }
  assert.equal(endpointCheckForConsumer(undefined, "direct:phone"), undefined);
});
