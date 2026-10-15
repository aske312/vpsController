import assert from "node:assert/strict";
import test from "node:test";
import { managerStatusSnapshot, networkRoutesSnapshot } from "../../src/features/mihomo/workspace-refresh.ts";
import { confirmedRouteAddresses } from "../../src/shared/lib/network-readiness.ts";

test("manager observations retain explicit running, stopped and unknown states", () => {
  for (const active of [true, false, null]) {
    const value = { active, health: { state: "unknown" }, profiles: 0 };
    assert.equal(managerStatusSnapshot(value), value);
  }
});

test("malformed manager observations cannot publish activity or renderable counters", () => {
  for (const value of [null, {}, { active: "true" }, { active: true, runtime: [] },
    { active: true, health: { state: "ready" } }, { active: true, profiles: -1 },
    { active: true, channels_in_use: "wg" }, { active: true, endpoint: {} },
    { active: true, recovery_required: "false" }, { active: true, runtime: { state: "running", reason: {} } },
    { active: true, runtime: { state: "unknown" } }, { active: false, runtime: { state: "running" } }]) {
    assert.throws(() => managerStatusSnapshot(value));
  }
});

const check = { kind: "cdn", domain: "Edge.Example", resolved: ["203.0.113.1"], status: "ready", ready: true,
  verification: "end_to_end", binding_id: "binding", check_id: "check", checked_at: "now", desired_revision: 2, checked_revision: 2, selectable: true };

test("validated route evidence retains eligibility while legacy reads claim none", () => {
  assert.deepEqual(confirmedRouteAddresses(networkRoutesSnapshot({}), "cdn"), []);
  const value = { transport_endpoint_checks: { cdn: check } };
  assert.equal(networkRoutesSnapshot(value), value);
  assert.deepEqual(confirmedRouteAddresses(value, "cdn"), ["edge.example"]);
  assert.deepEqual(confirmedRouteAddresses(value, "cdn", true), ["edge.example"]);
});

test("malformed route records cannot crash selectors or grant false eligibility", () => {
  for (const key of ["transport_endpoint_checks", "transport_endpoint_checks_by_domain"]) {
    for (const value of [null, [], { cdn: null }, { cdn: { ...check, domain: {} } },
      { cdn: { ...check, resolved: null } }, { cdn: { ...check, selectable: "true" } },
      { cdn: { ...check, ready: "false" } }, { cdn: { ...check, checked_revision: true } },
      { cdn: { ...check, binding_id: {} } }, { cdn: { ...check, verification: "maybe" } }]) {
      assert.throws(() => networkRoutesSnapshot({ [key]: value }));
    }
  }
});
