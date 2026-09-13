import test from "node:test";
import assert from "node:assert/strict";
import { recommendDns } from "../../src/features/network/dns-recommendation.ts";

const providers = [
  { id: "first", filter: "none", doh_url: "https://first.example/dns-query" },
  { id: "second", filter: "none", doh_url: "https://second.example/dns-query" },
  { id: "plain", filter: "none" },
  { id: "filtered", filter: "family", doh_url: "https://family.example/dns-query" },
];
const checks = Object.fromEntries(providers.map((item, index) => [item.id, { udp_ok: true, doh_ok: true, udp_ms: 20 - index * 5, doh_ms: 30 - index * 5 }]));

test("DNS recommendation keeps filtering and encryption and does not mutate settings", () => {
  const settings = { selected_id: "first", fallback_enabled: true, profiles: { wg: "filtered" }, prefer_encrypted: true };
  const original = structuredClone(settings);
  const result = recommendDns(settings, providers, checks, { udp: true, doh: true });
  assert.equal(result.primary.id, "second");
  assert.equal(result.backup.id, "first");
  assert.deepEqual(settings, original);
});

test("DNS recommendation respects fixed binding and rejects incomplete measurements", () => {
  const settings = { selected_id: "first", fallback_enabled: false };
  assert.equal(recommendDns(settings, providers, checks, { udp: true, doh: false }).backup, null);
  assert.equal(recommendDns(settings, providers, {}, { udp: true, doh: true }), null);
  assert.equal(recommendDns(settings, providers, { first: { udp_ok: true, udp_ms: NaN } }, { udp: true, doh: false }), null);
});

test("DNS backup uses another provider and preserves an explicit backup filter", () => {
  const settings = { selected_id: "first", fallback_enabled: true, fallback_id: "filtered" };
  assert.equal(recommendDns(settings, providers, checks, { udp: true, doh: true }).backup.id, "filtered");
  const sameFamily = providers.slice(0, 2).map((item, index) => ({ ...item, id: `vendor-${index}` }));
  const readings = Object.fromEntries(sameFamily.map((item) => [item.id, checks.first]));
  assert.equal(recommendDns({ selected_id: "vendor-0", fallback_enabled: true }, sameFamily, readings, { udp: true, doh: true }), null);
});
