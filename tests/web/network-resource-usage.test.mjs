import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createTestLoader } from "./vite-test-loader.mjs";

const loader = await createTestLoader(import.meta.url);
after(() => loader.close());
const { resourceBindingsSnapshot, resourceConsumers, resourceUsageLabel, resourceProvider, ipProvider } = await loader.ssrLoadModule("/src/features/network/resource-usage.ts");
const entry = { kind: "domain", address: "edge.example", domain: { role: "CDN endpoint", resolved: ["203.0.113.1"] } };
const consumer = { id: "mihomo:p1:c1", label: "Family", owner: "mihomo" };
const binding = { address: entry.address, kind: "cdn", state: "active", consumers: [consumer] };

test("a failed usage read never becomes zero usage or proof of DNS readiness", () => {
  assert.equal(resourceConsumers(entry, null, null), null);
  assert.equal(resourceUsageLabel(entry, null), "Данные не получены");
  assert.equal(resourceUsageLabel(entry, []), "Нет связанных подключений");
  assert.equal(resourceUsageLabel({ ...entry, domain: { role: "panel" } }, []), "Панель управления");
  for (const value of [{}, { schema: 1, bindings: {} }, { schema: 1, bindings: [{ ...binding, consumers: null }] }, { schema: 1, bindings: [{ ...binding, kind: "unknown" }] }]) assert.throws(() => resourceBindingsSnapshot(value));
});

test("shared domains deduplicate connections and distinguish archived bindings from current use", () => {
  const bindings = resourceBindingsSnapshot({ schema: 1, bindings: [binding, { ...binding, kind: "tls_relay", state: "retired", consumers: [consumer, { ...consumer, id: "mihomo:p1:c2" }] }, { ...binding, address: "other.example" }] });
  const consumers = resourceConsumers(entry, bindings, null);
  assert.equal(consumers.length, 2);
  assert.equal(consumers.filter(item => !item.retained).length, 1);
  assert.equal(resourceUsageLabel(entry, consumers), "1 подключение · Архив привязок: 1");
  assert.equal(resourceUsageLabel({ ...entry, domain: { role: "panel, CDN" } }, consumers), "Панель · 1 подключение · Архив привязок: 1");
});

test("Relay usage counts configured connections rather than forwarded TCP and UDP ports", () => {
  const relay = { profiles: [{ id: "p1", name: "Family", channels: [{ id: "c1", host_id: "h1", route_ids: ["tcp", "udp"] }, { id: "c2", host_id: "h2" }] }] };
  const host = { kind: "relay", host: { id: "h1" } };
  assert.equal(resourceConsumers(host, [], relay).length, 1);
  assert.equal(resourceConsumers(host, [], { profiles: [] }).length, 0);
  assert.equal(resourceConsumers(host, [], {}), null);
});

test("DNS operator, uncertain CDN detection and hosting provider remain separate", () => {
  const domain = { ...entry, domain: { ...entry.domain, dns: { provider: "DNS operator" }, edge: { provider: "Guessed CDN", confidence: "unknown" }, ip_info: [{ hoster: "Hosting company", provider: "IP owner" }] } };
  assert.equal(resourceProvider(domain), "Hosting company");
  assert.equal(ipProvider({ hoster: "Unknown", provider: "IP owner" }), "IP owner");
  assert.equal(resourceProvider({ ...domain, domain: { ...domain.domain, edge: { provider: "Cloudflare", confidence: "high" } } }), "Cloudflare");
  const safe = resourceBindingsSnapshot({ schema: 1, bindings: [{ ...binding, consumers: [{ ...consumer, password: "not-for-ui" }] }] });
  assert.equal("password" in safe[0].consumers[0], false);
});
