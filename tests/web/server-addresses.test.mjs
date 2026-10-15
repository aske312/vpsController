import assert from "node:assert/strict";
import test from "node:test";
import { addressMatches, confirmsAddressChange, serverAddressesSnapshot } from "../../src/features/network/server-address-state.ts";

const row = (address, role = "additional") => ({ address, family: "ipv4", interfaces: ["ens3"], source: "interface", presence: "present", role });
const inventory = () => ({ revision: "a".repeat(32), detected_at: "2026-10-16T00:00:00Z", change: null, discovery: { state: "ready", message: "Assigned" }, items: [row("45.66.163.179", "primary"), row("45.66.163.181", "reserve"), row("45.66.163.183")] });
test("public IPv4/IPv6 inventory supports multiple addresses, missing saved IPs and searches by role/interface", () => {
  const value = inventory(); value.items.push({ ...row("2606:4700::1111", "primary"), family: "ipv6" });
  assert.equal(serverAddressesSnapshot(value), value);
  assert.equal(addressMatches(value.items[1], "резерв"), true);
  assert.equal(addressMatches(value.items[2], "ens3"), true);
  assert.equal(addressMatches(value.items[0], ".181"), false);
  value.items[1] = { ...value.items[1], source: "saved", presence: "missing", interfaces: [] };
  value.discovery.state = "error";
  assert.equal(serverAddressesSnapshot(value), value);
});
test("invalid observations cannot replace saved roles or permit conflicting primaries", () => {
  for (const edit of [v => v.items.push(v.items[0]), v => v.items[1].role = "primary", v => v.items[1].address = "999.1.1.1", v => v.items[1].address = "domain.example", v => v.items[1].family = "ipv6", v => v.items[1].interfaces = [], v => v.items[1].presence = "ready", v => v.revision = "old", v => v.discovery.state = "pending", v => v.change = {}, v => v.items = null]) {
    const value = inventory(); edit(value); assert.throws(() => serverAddressesSnapshot(value));
  }
});
test("lost write acknowledgment requires the exact change receipt, not merely a matching role", () => {
  const value = inventory();
  const pending = { id: "b".repeat(32), address: value.items[0].address, role: "primary", previous_revision: "0".repeat(32) };
  assert.equal(confirmsAddressChange(value, pending), false);
  value.change = { ...pending, revision: value.revision };
  assert.equal(serverAddressesSnapshot(value), value);
  assert.equal(confirmsAddressChange(value, pending), true);
  for (const edit of [{ id: "c".repeat(32) }, { address: "45.66.163.183" }, { role: "reserve" }, { previous_revision: "d".repeat(32) }]) assert.equal(confirmsAddressChange({ ...value, change: { ...value.change, ...edit } }, pending), false);
});
