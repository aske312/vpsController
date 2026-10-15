import assert from "node:assert/strict";
import test from "node:test";
import { securitySnapshot, securityUnavailable } from "../../src/control-panel/security-snapshot.ts";
import { securityLiveObservation } from "../../src/control-panel/live-snapshot.ts";
import { security } from "./security-fixtures.mjs";

test("full security observations accept current nullable refresh timestamps and preserve policy", () => {
  const value = securitySnapshot(security);
  assert.equal(value.full_observation_failed, false);
  assert.equal(value.firewall, security.firewall);
  assert.equal(value.application_version.checked_at, null);
  const partial = structuredClone(security); partial.ssh.active = null;
  assert.equal(securitySnapshot(partial).live_observation_failed, true);
  assert.equal(securitySnapshot(partial).full_observation_failed, true);
  partial.ssh.active = true; partial.fail2ban.jail_active = null; partial.fail2ban.currently_banned = null;
  const unavailableJail = securitySnapshot(partial);
  assert.equal(unavailableJail.full_observation_failed, true);
  assert.equal(securityLiveObservation(unavailableJail, { firewall_active: true, fail2ban_active: true, ssh_listening: true }).full_observation_failed, true);
  const failedUpdates = structuredClone(security);
  failedUpdates.updates = { available: null, security: null, kernel_available: null, checked_at: null, refreshing: false, error: "Package observation unavailable" };
  assert.doesNotThrow(() => securitySnapshot(failedUpdates));
  for (const change of [value => value.system.auditd_active = null, value => value.updates.automatic = null,
    value => value.legacy_services["fixture.service"].active = null, value => value.legacy_services["fixture.service"].enabled = "unknown"]) {
    const partial = structuredClone(security); change(partial);
    const observed = securitySnapshot(partial);
    assert.equal(observed.full_observation_failed, true);
    assert.equal(securityLiveObservation(observed, { firewall_active: true, fail2ban_active: true, ssh_listening: true }).full_observation_failed, true);
  }
});

test("malformed security counters, nested policies and renderable lists are rejected", () => {
  for (const change of [value => value.failed_ssh_records_24h = {}, value => value.listeners = {},
    value => value.firewall.rules = {}, value => value.firewall.active = "true",
    value => value.firewall.panel_access.allowed_channels = [null], value => value.firewall.protocol_policies.wg.healthy = "false",
    value => value.fail2ban.currently_banned = -1, value => value.ssh.permit_root_login = {},
    value => value.system.apparmor.profiles = Infinity, value => value.system.sudo_users = {},
    value => value.updates.security = "0", value => value.application_version.outdated = "false",
    value => value.application_security.secrets_mode = {}, value => value.legacy_services["fixture.service"].enabled = {},
    value => delete value.system]) {
    const value = structuredClone(security); change(value); assert.throws(() => securitySnapshot(value));
  }
});

test("cheap security telemetry cannot confirm a failed full policy observation", () => {
  const failed = securityUnavailable(securitySnapshot(security));
  assert.equal(failed.firewall, security.firewall);
  const live = securityLiveObservation(failed, { firewall_active: true, fail2ban_active: true, ssh_listening: true });
  assert.equal(live.live_observation_failed, false);
  assert.equal(live.full_observation_failed, true);
  assert.equal(securitySnapshot(security).full_observation_failed, false);
  assert.equal(securityUnavailable(null), null);
});
