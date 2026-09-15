import assert from "node:assert/strict";
import test from "node:test";
import { read, readApiSources } from "./support.mjs";

test("network owns shared CDN and relay endpoints while protected channels keep protocol identities", async () => {
  const [api, page, networkApi, types, endpoint, css] = await Promise.all([
    readApiSources(),
    read("src/features/network/network-view.tsx"),
    read("src/features/network/network-api.ts"),
    read("src/shared/types/control-plane.ts"),
    read("src/features/network/network-endpoints.tsx"),
    read("src/features/network/network.css"),
  ]);
  assert.match(api, /NetworkEndpointSettings/);
  assert.match(api, /@app\.put\("\/api\/network\/endpoints"\)/);
  assert.match(api, /channel_mode/);
  assert.match(api, /tls_relay_domain/);
  assert.match(api, /udp_relay_domain/);
  assert.match(page, /NetworkEndpoints/);
  assert.match(endpoint, /networkAddRouteButton/);
  assert.match(endpoint, /Настройка внешних адресов/);
  assert.match(endpoint, />ROUTES<\/span>/);
  assert.doesNotMatch(endpoint, /networkRoutesLauncher/);
  assert.match(css, /networkAddRouteButton/);
  assert.match(page, /expandedDomains/);
  assert.match(page, /networkRouteDetailRow/);
  assert.match(page, /networkRouteCascade/);
  assert.match(page, /NetworkRouteTags/);
  assert.match(page, /networkStateStrip/);
  assert.match(page, /SERVER IPv4/);
  assert.match(page, /SERVER IPv6/);
  assert.match(page, /serverRoutes/);
  assert.ok(page.indexOf("networkStateStrip") < page.indexOf("networkV2RouteBlock"));
  assert.match(networkApi, /saveNetworkEndpoints/);
  assert.match(types, /transport_endpoints/);
  assert.match(types, /NetworkEndpointSettings/);
});

test("network DNS selection is limited to installed independently configurable protocols", async () => {
  const [dns, css] = await Promise.all([
    read("src/features/network/network-dns.tsx"),
    read("src/features/network/network.css"),
  ]);
  assert.match(dns, /installedDnsComponents/);
  assert.match(dns, /item\.key !== "apply_system"/);
  assert.doesNotMatch(dns, /Исключения по компонентам/);
  assert.match(css, /networkResolverColumn \.networkProviderGrid[\s\S]+max-height: 300px/);
  assert.match(css, /networkSaveBar[\s\S]+position: sticky/);
  assert.match(css, /networkModal[\s\S]+width: min\(1180px/);
});
