import assert from "node:assert/strict";
import test from "node:test";
import { read, readApiSources } from "./support.mjs";

test("network owns shared CDN and relay endpoints while protected channels keep protocol identities", async () => {
  const [api, page, networkApi, types] = await Promise.all([
    readApiSources(),
    read("src/features/network/network-view.tsx"),
    read("src/features/network/network-api.ts"),
    read("src/shared/types/control-plane.ts"),
  ]);
  assert.match(api, /NetworkEndpointSettings/);
  assert.match(api, /@app\.put\("\/api\/network\/endpoints"\)/);
  assert.match(api, /channel_mode/);
  assert.match(api, /tls_relay_domain/);
  assert.match(api, /udp_relay_domain/);
  assert.match(page, /NetworkEndpoints/);
  assert.match(page, /expandedDomains/);
  assert.match(page, /networkRouteDetailRow/);
  assert.match(page, /networkRouteCascade/);
  assert.match(page, /NetworkRouteTags/);
  assert.match(page, /networkServerIpGrid/);
  assert.match(page, /PUBLIC IPv4/);
  assert.match(page, /PUBLIC IPv6/);
  assert.ok(page.indexOf("networkV2State") < page.indexOf("networkV2RouteBlock"));
  assert.match(networkApi, /saveNetworkEndpoints/);
  assert.match(types, /transport_endpoints/);
  assert.match(types, /NetworkEndpointSettings/);
});
