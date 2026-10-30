import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createElement, Children, isValidElement } from "react";
import fs from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import react from "@vitejs/plugin-react";
import { createTestLoader } from "./vite-test-loader.mjs";
import { profileConnectionMode, profileModeChange, profileRuleVisible, profileFunctionKeys, profileRoutingSettings } from "../../src/features/mihomo/profile-modes.ts";

const loader = await createTestLoader(import.meta.url, [react()]);
after(() => loader.close());
const { ProfileProtection } = await loader.ssrLoadModule("/src/features/mihomo/profile-protection.tsx");
const { ProfileRoutingMode } = await loader.ssrLoadModule("/src/features/mihomo/profile-routing-mode.tsx");
const { ProfileModeToggle } = await loader.ssrLoadModule("/src/features/mihomo/profile-mode-toggle.tsx");
const catalog = JSON.parse(fs.readFileSync(new URL("../../protocol-images/mihomo/vpn-services.json", import.meta.url), "utf8"));
const render = (Component, routing) => renderToStaticMarkup(createElement(Component, { routing, onChange() {} }));

function findElement(element, predicate) {
  if (!isValidElement(element)) return undefined;
  if (predicate(element)) return element;
  for (const child of Children.toArray(element.props.children)) {
    const found = findElement(child, predicate);
    if (found) return found;
  }
}

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

test("Protection reflects profile-only traffic functions while inheriting connection defaults", () => {
  const defaults = { block_malware: true, block_privacy: true, quic_compatibility: true, tun_enabled: true, dns_secure: true };
  const profile = { block_privacy: true, dns_secure: false };
  const routing = profileRoutingSettings(defaults, profile);
  assert.equal(routing.block_malware, false);
  assert.equal(routing.block_privacy, true);
  assert.equal(routing.quic_compatibility, false);
  assert.equal(routing.dns_secure, false);
  assert.equal(profileConnectionMode(routing), "tun");
  assert.equal(defaults.block_malware, true);
  assert.deepEqual(profile, { block_privacy: true, dns_secure: false });
});

test("one capture button shows the current mode without guidance", () => {
  const proxy = render(ProfileProtection, { connection_mode: "proxy", tun_force: true });
  assert.equal((proxy.match(/class="mihomoModeToggle"/g) || []).length, 1);
  assert.match(proxy, /aria-label="Режим подключения: Proxy"/);
  assert.doesNotMatch(proxy, /DNS Hijack|Strict Route|Fake-IP DNS/);
  assert.doesNotMatch(proxy, /mihomoHint|После сохранения|переопределяет TUN/);
  const tun = render(ProfileProtection, { connection_mode: "tun" });
  assert.match(tun, /aria-label="Режим подключения: TUN"/);
  assert.match(tun, /DNS Hijack/);
  assert.match(tun, /Strict Route/);
});

test("one routing button exposes local brand icons only in selected mode without guidance", () => {
  const selected = render(ProfileRoutingMode, { routing_scope: "selected", vpn_services: "telegram", connection_mode: "proxy" });
  assert.equal((selected.match(/class="mihomoModeToggle"/g) || []).length, 1);
  assert.match(selected, /aria-label="Маршрутизация: Только выбранное"/);
  assert.equal((selected.match(/class="mihomoVpnService"/g) || []).length, catalog.services.length);
  assert.doesNotMatch(selected, /<small|mihomoHint|локальный прокси/);
  for (const service of catalog.services) {
    assert.ok(selected.includes(`src="${service.icon}"`), service.id);
    assert.match(service.icon, /^\/icons\/apps\/[a-z]+\.svg$/);
    const svg = fs.readFileSync(new URL(`../../public${service.icon}`, import.meta.url), "utf8");
    assert.match(svg, /^<svg.*<path /);
    assert.doesNotMatch(svg, /<script|href=/);
  }
  assert.doesNotMatch(render(ProfileRoutingMode, { routing_scope: "all" }), /Сервисы через VPN/);
  assert.match(render(ProfileRoutingMode, { routing_scope: "all" }), /aria-label="Маршрутизация: По правилам"/);
  assert.doesNotMatch(render(ProfileRoutingMode, { routing_scope: "all" }), /Всё через VPN/);
});

test("pressing each single button cycles both ways and preserves service selections", () => {
  let routing = { connection_mode: "tun", routing_scope: "all", vpn_services: "telegram" };
  const onChange = (key, value) => { routing = { ...routing, ...profileModeChange(key, value) }; };
  const press = Component => {
    const tree = Component({ routing, onChange });
    const mode = findElement(tree, node => node.type === ProfileModeToggle);
    assert.ok(mode);
    ProfileModeToggle(mode.props).props.onClick();
  };
  press(ProfileProtection);
  assert.equal(routing.connection_mode, "proxy");
  press(ProfileProtection);
  assert.equal(routing.connection_mode, "tun");
  press(ProfileRoutingMode);
  assert.equal(routing.routing_scope, "selected");
  const service = findElement(ProfileRoutingMode({ routing, onChange }), node => node.type === "button" && node.key?.endsWith("discord"));
  assert.ok(service);
  service.props.onClick();
  assert.equal(routing.vpn_services, "telegram,discord");
  press(ProfileRoutingMode);
  assert.equal(routing.routing_scope, "all");
  press(ProfileRoutingMode);
  assert.equal(routing.routing_scope, "selected");
  assert.equal(routing.vpn_services, "telegram,discord");
});

test("routing choices exclude inactive presets for Proxy and selected-only scope", () => {
  assert.equal(profileRuleVisible("direct_games_enabled", { connection_mode: "proxy" }), false);
  assert.equal(profileRuleVisible("direct_p2p_enabled", { connection_mode: "proxy" }), false);
  assert.equal(profileRuleVisible("direct_ru_sites", { routing_scope: "selected" }), false);
  for (const key of profileFunctionKeys) {
    assert.equal(profileRuleVisible(key, { routing_scope: "all" }), false);
    assert.equal(profileRuleVisible(key, { routing_scope: "selected" }), false);
  }
  assert.equal(profileRuleVisible("direct_local_network", { routing_scope: "selected" }), true);
  assert.equal(profileRuleVisible("direct_games_enabled", { connection_mode: "tun" }), true);
});

test("traffic functions stay in Protection for both routing scopes and keep their saved keys", () => {
  const titles = { block_malware: "Фишинг и вредоносные сайты", block_privacy: "Приватность", quic_compatibility: "Совместимость QUIC", route_dns: "DNS по маршруту" };
  for (const scope of ["all", "selected"]) {
    let routing = { connection_mode: "tun", routing_scope: scope, block_malware: true, route_dns: true };
    const onChange = (key, value) => { routing = { ...routing, [key]: value }; };
    const html = render(ProfileProtection, routing);
    assert.match(html, /Функции трафика/);
    for (const [key, title] of Object.entries(titles)) {
      assert.ok(html.includes(title));
      assert.equal(render(ProfileRoutingMode, routing).includes(title), false);
      const button = findElement(ProfileProtection({ routing, onChange }), node => node.type === "button" && node.props.className?.includes("mihomoProfileRuleButton") && renderToStaticMarkup(node).includes(title));
      assert.ok(button, key);
      const previous = Boolean(routing[key]);
      button.props.onClick();
      assert.equal(routing[key], !previous);
      assert.equal(routing.routing_scope, scope);
    }
  }
});
