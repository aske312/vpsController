import test from "node:test";
import assert from "node:assert/strict";
import { clientCapabilities, compatibleClientRouting, clientConnectionSupported } from "../../src/features/mihomo/client-capabilities.ts";

test("Happ offers only Xray exporter features and compatible transports", () => {
  const caps = clientCapabilities("xray", "windows");
  assert.deepEqual(caps.components, ["transport-reality", "transport-shadowsocks"]);
  assert.deepEqual(caps.strategies, []);
  assert.equal(caps.features.includes("tunnel_ech"), false);
  assert.equal(clientConnectionSupported({ component: "transport-awg", settings: {} }, "xray"), false);
  assert.equal(clientConnectionSupported({ component: "transport-reality", settings: { route_mode: "cdn", cdn_transport: "xhttp" } }, "xray"), true);
});

test("format changes clear unavailable settings without changing shared catalog", () => {
  const source = { strategy: "fallback", tunnel_privacy: true, tunnel_ech: true, tunnel_fragment: true, direct_games_enabled: true };
  const changed = compatibleClientRouting(source, "singbox", "windows");
  assert.equal(changed.strategy, "url-test");
  assert.equal(changed.tunnel_privacy, false);
  assert.equal(changed.tunnel_ech, false);
  assert.equal(changed.tunnel_fragment, true);
  assert.equal(changed.direct_games_enabled, false);
  assert.equal(source.tunnel_privacy, true);
  assert.equal(clientCapabilities("mihomo").features.includes("tunnel_privacy"), true);
  assert.equal(clientConnectionSupported({ component: "transport-reality", settings: { transport: "xhttp", route_mode: "direct" } }, "singbox"), false);
  assert.equal(clientConnectionSupported({ component: "transport-reality", settings: { transport: "raw", route_mode: "direct" } }, "singbox"), true);
});

test("desktop process presets are absent on mobile and remain available on desktop", () => {
  assert.equal(clientCapabilities("mihomo", "ios").rules.includes("direct_games_enabled"), false);
  assert.equal(clientCapabilities("mihomo", "windows").rules.includes("direct_games_enabled"), true);
  assert.equal(clientCapabilities("mihomo").rules.includes("direct_games_enabled"), true);
});
