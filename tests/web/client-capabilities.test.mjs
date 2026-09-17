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
  assert.equal(changed.tunnel_ech, true);
  assert.equal(changed.tunnel_fragment, true);
  assert.equal(changed.direct_games_enabled, true);
  assert.equal(source.tunnel_privacy, true);
  assert.equal(clientCapabilities("mihomo").features.includes("tunnel_privacy"), true);
  assert.equal(clientConnectionSupported({ component: "transport-reality", settings: { transport: "xhttp", route_mode: "direct" } }, "singbox"), false);
  assert.equal(clientConnectionSupported({ component: "transport-reality", settings: { transport: "raw", route_mode: "direct" } }, "singbox"), true);
});

test("desktop process presets are absent on mobile and remain available on desktop", () => {
  for (const os of ["ios", "android", "macos", "linux", "unknown"]) {
    assert.equal(clientCapabilities("mihomo", os).rules.includes("windows_geolocation"), false);
    assert.equal(compatibleClientRouting({ windows_geolocation: true }, "mihomo", os).windows_geolocation, false);
  }
  assert.equal(clientCapabilities("mihomo", "windows").rules.includes("windows_geolocation"), true);
  assert.equal(clientCapabilities("mihomo", "ios").rules.includes("direct_games_enabled"), false);
  assert.equal(clientCapabilities("mihomo", "windows").rules.includes("direct_games_enabled"), true);
  assert.equal(clientCapabilities("mihomo").rules.includes("direct_games_enabled"), true);
  assert.equal(clientCapabilities("singbox", "ios").rules.includes("direct_games_udp_enabled"), true);
});

test("sing-box restores WG, ECH, selector and complete rule presets", () => {
  const caps = clientCapabilities("singbox", "windows");
  assert.equal(caps.components.includes("transport-wg"), true);
  assert.equal(caps.features.includes("tunnel_ech"), true);
  assert.equal(caps.strategies.includes("select"), true);
  for (const key of ["block_ads", "direct_ru_sites", "direct_games_enabled", "direct_games_udp_enabled", "direct_p2p_enabled"]) assert.equal(caps.rules.includes(key), true);
});

test("Launcher extensions do not alter ordinary sing-box or shared profiles", () => {
  assert.equal(clientCapabilities(undefined).label, "Mihomo YAML");
  const launcher = clientCapabilities("singbox", "windows", "Sing-Box Launcher");
  assert.equal(launcher.components.includes("transport-awg"), true);
  assert.equal(launcher.transports.includes("xhttp"), true);
  assert.equal(launcher.features.includes("tunnel_privacy"), true);
  assert.equal(clientCapabilities("singbox").transports.includes("xhttp"), false);
  assert.equal(compatibleClientRouting({ tunnel_privacy: true }, "singbox", "windows", "Sing-Box Launcher").tunnel_privacy, true);
});
