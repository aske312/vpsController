import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import react from "@vitejs/plugin-react";
import { createTestLoader } from "./vite-test-loader.mjs";
import { profileConnectionMode, profileModeChange, profileRuleVisible } from "../../src/features/mihomo/profile-modes.ts";

const loader = await createTestLoader(import.meta.url, [react()]);
after(() => loader.close());
const { ProfileProtection } = await loader.ssrLoadModule("/src/features/mihomo/profile-protection.tsx");
const { ProfileRoutingMode } = await loader.ssrLoadModule("/src/features/mihomo/profile-routing-mode.tsx");
const render = (Component, routing) => renderToStaticMarkup(createElement(Component, { routing, onChange() {} }));

test("explicit Proxy wins over legacy forced TUN and switching back restores TUN", () => {
  const source = { tun_enabled: true, tun_force: true, vpn_services: "youtube", dns_hijack_force: true };
  assert.equal(profileConnectionMode(source), "tun");
  const proxy = { ...source, ...profileModeChange("connection_mode", "proxy") };
  assert.equal(profileConnectionMode(proxy), "proxy");
  assert.equal(proxy.tun_enabled, false);
  assert.equal(proxy.tun_force, false);
  const tun = { ...proxy, ...profileModeChange("connection_mode", "tun") };
  assert.equal(profileConnectionMode(tun), "tun");
  assert.equal(tun.vpn_services, "youtube");
  assert.equal(tun.dns_hijack_force, true);
  assert.equal(source.tun_force, true);
  assert.deepEqual(profileModeChange("routing_scope", "selected"), { routing_scope: "selected", mode: "rule" });
});

test("capture mode has one active choice and Proxy hides TUN-only controls", () => {
  const proxy = render(ProfileProtection, { connection_mode: "proxy", tun_force: true });
  assert.match(proxy, /aria-pressed="false"><b>TUN режим/);
  assert.match(proxy, /aria-pressed="true"><b>Proxy/);
  assert.doesNotMatch(proxy, /DNS Hijack|Strict Route|Fake-IP DNS/);
  const tun = render(ProfileProtection, { connection_mode: "tun" });
  assert.match(tun, /aria-pressed="true"><b>TUN режим/);
  assert.match(tun, /aria-pressed="false"><b>Proxy/);
  assert.match(tun, /DNS Hijack/);
  assert.match(tun, /Strict Route/);
});

test("selective routing exposes services and describes the Proxy boundary", () => {
  const selected = render(ProfileRoutingMode, { routing_scope: "selected", vpn_services: "telegram", connection_mode: "proxy" });
  assert.match(selected, /aria-pressed="true"><b>Telegram/);
  assert.match(selected, /aria-pressed="false"><b>YouTube/);
  assert.match(selected, /не использующие прокси/);
  assert.doesNotMatch(render(ProfileRoutingMode, { routing_scope: "all" }), /Сервисы через VPN/);
});

test("routing choices exclude inactive presets for Proxy and selected-only scope", () => {
  assert.equal(profileRuleVisible("direct_games_enabled", { connection_mode: "proxy" }), false);
  assert.equal(profileRuleVisible("direct_p2p_enabled", { connection_mode: "proxy" }), false);
  assert.equal(profileRuleVisible("direct_ru_sites", { routing_scope: "selected" }), false);
  assert.equal(profileRuleVisible("block_malware", { routing_scope: "selected" }), true);
  assert.equal(profileRuleVisible("direct_local_network", { routing_scope: "selected" }), true);
  assert.equal(profileRuleVisible("direct_games_enabled", { connection_mode: "tun" }), true);
});
