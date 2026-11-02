import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import react from "@vitejs/plugin-react";
import { createTestLoader } from "./vite-test-loader.mjs";

const loader = await createTestLoader(import.meta.url, [react()]);
after(() => loader.close());
const { ConnectionOrderNav } = await loader.ssrLoadModule("/src/features/mihomo/connection-order-nav.tsx");
const { connectionAppearance } = await loader.ssrLoadModule("/src/features/mihomo/connection-status.ts");
const { ProfileConnectionCard } = await loader.ssrLoadModule("/src/features/mihomo/profile-connection-card.tsx");
const connections = Array.from({ length: 40 }, (_, index) => ({
  id: `connection-${index}`, device_id: "phone", name: `Канал ${index + 1}`,
  component: "transport-reality", settings: { route_mode: index % 2 ? "cdn" : "direct", transport_path: `/unsaved-${index}` },
}));
const render = (selectedId, disabled = false, items = connections) => renderToStaticMarkup(createElement(ConnectionOrderNav, {
  connections: items, modules: [{ id: "transport-reality", name: "Xray" }], selectedId, disabled,
  onSelect() {}, onReorder() {},
}));
const arrows = html => [...html.matchAll(/<button[^>]*class="mihomoConnectionArrow"[^>]*>/g)].map(match => match[0]);

test("40 connections retain their identity, order and draft settings in the carousel", () => {
  const before = structuredClone(connections);
  const html = render("connection-25");
  assert.equal((html.match(/data-connection-id=/g) || []).length, 40);
  assert.equal((html.match(/aria-pressed="true"/g) || []).length, 1);
  assert.match(html, /title="Канал 26"/);
  assert.match(html, /26 \/ 40/);
  assert.match(html, /VLESS · CDN/);
  assert.match(html, /VLESS · REALITY/);
  assert.match(html, /Изменить приоритет: Канал 40/);
  assert.ok(html.indexOf('data-connection-id="connection-0"') < html.indexOf('data-connection-id="connection-39"'));
  assert.deepEqual(connections, before);
});

test("navigation arrows stop at the first and last connection and lock while saving", () => {
  const first = arrows(render("connection-0"));
  assert.match(first[0], /Предыдущее подключение/);
  assert.match(first[0], /disabled/);
  assert.doesNotMatch(first[1], /disabled/);
  const last = arrows(render("connection-39"));
  assert.doesNotMatch(last[0], /disabled/);
  assert.match(last[1], /Следующее подключение/);
  assert.match(last[1], /disabled/);
  assert.ok(arrows(render("connection-20", true)).every(button => button.includes("disabled")));
});

test("a single connection cannot be moved past either boundary", () => {
  const html = render("connection-0", false, connections.slice(0, 1));
  assert.match(html, /1 \/ 1/);
  assert.ok(arrows(html).every(button => button.includes("disabled")));
  assert.doesNotMatch(html, /class="mihomoConnectionDrag"/);
});

test("tiles distinguish the actual Xray protocol, transport, Relay and legacy combined routes", () => {
  const items = [
    { ...connections[0], settings: { xray_protocol: "trojan", route_mode: "tls", tls_transport: "grpc", relay_host_id: "relay" } },
    { ...connections[1], settings: { route_mode: "both", transport: "xhttp" } },
  ];
  const html = render(items[0].id, false, items);
  assert.match(html, /Trojan · TLS · GRPC · Relay/);
  assert.match(html, /VLESS · REALITY \+ CDN · XHTTP/);
});

const checkedConnection = { id: 'one', component: 'transport-reality', name: 'CDN', device_id: 'phone', settings: { route_mode: 'cdn', cdn_domain: 'cdn.example' } };
const checkedProfile = { id: 'profile', updated_at: '2026-11-01T12:00:00Z', connections: [checkedConnection] };
const checkedModule = { id: 'transport-reality', name: 'Xray', installed: true, active: true };
const readyCheck = { kind: 'cdn', domain: 'cdn.example', status: 'ready', ready: true, verification: 'end_to_end',
  binding_id: 'binding', desired_revision: 2, checked_revision: 2, check_id: 'check', checked_at: '2026-11-02T12:00:00Z', consumer_id: 'mihomo:profile:one' };
const context = check => ({ profile: checkedProfile, modules: [checkedModule], network: { transport_endpoint_checks_by_domain: { cdn: check } } });

test('connection colors follow explicit evidence and preserve warnings above ordinary availability', () => {
  assert.equal(connectionAppearance(checkedConnection).state, 'unchecked');
  assert.equal(connectionAppearance(checkedConnection, context(readyCheck)).state, 'verified');
  assert.equal(connectionAppearance({ ...checkedConnection, route_state: { state: 'stale', reason: 'Relay отключён', addresses: [], bindings: [] } }, context(readyCheck)).state, 'warning');
  assert.equal(connectionAppearance(checkedConnection, { ...context(readyCheck), modules: [{ ...checkedModule, active: false }] }).state, 'error');
  assert.equal(connectionAppearance(checkedConnection, context({ ...readyCheck, status: 'error', ready: false, verification: 'dns_only', message: 'Handshake failed' })).state, 'error');
  assert.equal(connectionAppearance(checkedConnection, context({ ...readyCheck, status: 'warning', ready: false })).state, 'warning');
});

test('another consumer, old revision, DNS-only checks and unsaved edits never turn a connection green', () => {
  for (const patch of [{ consumer_id: 'mihomo:profile:neighbor' }, { checked_revision: 1 }, { verification: 'dns_only' }, { checked_at: '2026-10-01T12:00:00Z' }]) {
    assert.equal(connectionAppearance(checkedConnection, context({ ...readyCheck, ...patch })).state, 'unchecked');
  }
  const edited = { ...checkedConnection, settings: { ...checkedConnection.settings, cdn_transport: 'grpc' } };
  assert.equal(connectionAppearance(edited, { ...context(readyCheck), draft: true }).state, 'unchecked');
  assert.equal(connectionAppearance({ ...checkedConnection, settings: { ...checkedConnection.settings, route_mode: 'both' } }, context(readyCheck)).state, 'unchecked');
});

test('carousel and profile cards expose readable status labels without changing saved connections', () => {
  const items = ['unchecked', 'verified', 'warning', 'error'].map((state, index) => ({ ...checkedConnection, id: 'color-'+index }));
  const before = structuredClone(items);
  const states = Object.fromEntries(items.map((item, index) => [item.id, { state: ['unchecked', 'verified', 'warning', 'error'][index], label: ['Не проверено', 'Проверено', 'Требует внимания', 'Ошибка'][index], reason: 'Полная причина состояния подключения' }]));
  const html = renderToStaticMarkup(createElement(ConnectionOrderNav, { connections: items, modules: [], states, selectedId: items[2].id, disabled: false, onSelect() {}, onReorder() {} }));
  for (const state of ['unchecked', 'verified', 'warning', 'error']) assert.ok(html.includes(`data-connection-status="${state}"`));
  assert.match(html, /mihomoConnectionStatusNote/);
  assert.match(html, /Полная причина состояния подключения/);
  const card = renderToStaticMarkup(createElement(ProfileConnectionCard, { connection: items[2], status: states[items[2].id] }));
  assert.match(card, /data-connection-status="warning"/);
  assert.match(card, /Требует внимания/);
  assert.deepEqual(items, before);
});
