import test from "node:test";
import assert from "node:assert/strict";
import { clientCapabilities, compatibleClientRouting, clientConnectionSupported } from "../../src/features/mihomo/client-capabilities.ts";

test("legacy format choices use YAML capabilities and discard retired switches", () => {
  for (const format of [undefined, "mihomo", "singbox", "xray", "uri"]) {
    assert.equal(clientCapabilities(format).label, "Mihomo YAML");
    assert.equal(clientCapabilities(format).rules.includes("block_ads"), false);
    assert.equal(clientConnectionSupported({ component: "transport-awg", settings: {} }, format), true);
  }
  const source = { client_config_format: "singbox", block_ads: true, block_ads_rules: "@default", tunnel_fragment: true, tunnel_privacy: true, strategy: "fallback" };
  const changed = compatibleClientRouting(source, "singbox", "windows");
  assert.equal(changed.tunnel_privacy, true);
  assert.equal(changed.strategy, "fallback");
  for (const key of ["client_config_format", "block_ads", "block_ads_rules", "tunnel_fragment"]) assert.equal(key in changed, false);
  assert.equal(source.block_ads, true);
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
