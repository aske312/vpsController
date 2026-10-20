import assert from "node:assert/strict";
import test from "node:test";
import { appendNetworkEndpoints, emptyEndpointSettings } from "../../src/features/network/endpoint-additions.ts";

test("adding addresses retains saved primary routes, other types and the concurrency revision", () => {
  const existing = { revision: "saved-version", cdn_domain: "old.example", cdn_domains: ["old.example", "edge.example"],
    tls_relay_domain: "tls.example", udp_relay_domain: "195.14.118.112" };
  const before = structuredClone(existing);
  const additions = { ...emptyEndpointSettings(), cdn_domain: " NEW.example. ", cdn_domains: ["second.example"] };
  const addedBefore = structuredClone(additions);
  assert.deepEqual(appendNetworkEndpoints(existing, additions), { ...existing,
    cdn_domains: ["old.example", "edge.example", "new.example", "second.example"] });
  assert.deepEqual(existing, before);
  assert.deepEqual(additions, addedBefore);
});

test("duplicate addresses are ignored per type and one domain can serve both TLS and UDP", () => {
  const existing = { ...emptyEndpointSettings(), cdn_domain: "cdn.example", cdn_domains: ["cdn.example"] };
  assert.deepEqual(appendNetworkEndpoints(existing, { ...emptyEndpointSettings(), cdn_domain: "CDN.EXAMPLE." }), existing);
  const result = appendNetworkEndpoints(existing, { ...emptyEndpointSettings(), tls_relay_domain: "direct.example",
    udp_relay_domain: "direct.example", tls_relay_domains: ["DIRECT.EXAMPLE."] });
  assert.equal(result.tls_relay_domain, "direct.example");
  assert.deepEqual(result.tls_relay_domains, ["direct.example"]);
  assert.deepEqual(result.udp_relay_domains, ["direct.example"]);
  assert.equal(result.cdn_domain, "cdn.example");
});
