import assert from "node:assert/strict";
import test from "node:test";
import { read, readApiSources } from "./support.mjs";

test("network owns shared endpoints while protected channels keep protocol identities", async () => {
  const [api, networkApi, types] = await Promise.all([readApiSources(), read("src/features/network/network-api.ts"), read("src/shared/types/control-plane.ts")]);
  for (const field of ["NetworkEndpointSettings", "channel_mode", "tls_relay_domain", "udp_relay_domain", "cdn_domains", "tls_relay_domains", "udp_relay_domains", "delete_network_endpoint", "MIHOMO_VLESS_CDN_ROUTES"]) assert.ok(api.includes(field), field);
  assert.match(api, /"verification": "dns_only"/);
  assert.match(api, /check_id = uuid\.uuid4\(\)\.hex/);
  assert.match(types, /failed_checks\?: number/);
  assert.match(types, /transport_endpoints/);
  assert.match(types, /NetworkEndpointSettings/);
  assert.match(networkApi, /saveNetworkEndpoints/);
  // Admission, binding identity and readiness are verified by API and state tests.
  // Presentation is free to change without retaining table markup or copy.
});

test("network DNS selection is limited to installed independently configurable protocols", async () => {
  const [dns, css] = await Promise.all([
    read("src/features/network/network-dns.tsx"),
    read("src/features/network/network.css"),
  ]);
  assert.match(dns, /installedDnsComponents/);
  assert.match(dns, /item\.key !== "apply_system"/);
  assert.match(dns, /DNS:.*undefined/);
  assert.doesNotMatch(dns, /Безопасное применение|Границы применения|networkImpact|networkPolicyNote/);
  assert.doesNotMatch(dns, /Исключения по компонентам/);
  assert.match(css, /networkResolverColumn \.networkProviderGrid[\s\S]+max-height: 300px/);
  assert.match(css, /networkSaveBar[\s\S]+position: sticky/);
});
