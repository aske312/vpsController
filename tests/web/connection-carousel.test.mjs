import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import react from "@vitejs/plugin-react";
import { createTestLoader } from "./vite-test-loader.mjs";

const loader = await createTestLoader(import.meta.url, [react()]);
after(() => loader.close());
const { ConnectionOrderNav } = await loader.ssrLoadModule("/src/features/mihomo/connection-order-nav.tsx");
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
