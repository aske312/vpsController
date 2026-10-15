import assert from "node:assert/strict";
import test from "node:test";
import { servicesSnapshot, servicesUnavailable, metricsSettingsSnapshot, observedServicesRequest } from "../../src/control-panel/services-snapshot.ts";
import { servicesSummary, runtimeState } from "../../src/features/services/service-state.ts";
import { createApiClient } from "../../src/shared/lib/api-request.ts";
import { services, metrics } from "./services-fixtures.mjs";

test("current and legacy service observations preserve policy and runtime", () => {
  assert.deepEqual(servicesSnapshot(services), services);
  const legacy = structuredClone(services); delete legacy.items[0].runtime; delete legacy.items[0].unit_present;
  assert.equal(runtimeState(servicesSnapshot(legacy).items[0]), "running");
  assert.deepEqual(metricsSettingsSnapshot(metrics), metrics);
});

test("malformed service identity, counters, schedules and policy cannot reach renderers", () => {
  for (const change of [value => value.items = {}, value => value.items.push(value.items[0]),
    value => value.items[0].controls = {}, value => value.items[0].name = {},
    value => value.items[0].restarts = "3", value => value.items[0].runtime.state = ["running"],
    value => value.items[0].unit_present = "true", value => value.failed_units = NaN,
    value => value.automation.cleanup.hour = 24, value => value.automation.update.minute = -1,
    value => value.automation.reboot.weekday = {}, value => value.timers.reboot.next_run = {},
    value => value.timers.reboot.runtime = { state: "unknown", reason: {}, checked_at: "" },
    value => value.panel_access.vpn_urls = [null], value => value.logging.persistent = "false",
    value => value.service_mode.active = "false", value => value.automation_recovery_required = "false"]) {
    const value = structuredClone(services); change(value); assert.throws(() => servicesSnapshot(value));
  }
});

test("unavailable service reads preserve identities and policy while clearing runtime and timer claims", () => {
  const unavailable = servicesUnavailable({ ...services, reboot_required: true });
  assert.equal(unavailable.items[0].id, services.items[0].id);
  assert.equal(unavailable.automation, services.automation); assert.equal(unavailable.logging, services.logging);
  assert.equal(runtimeState(unavailable.items[0]), "unknown"); assert.equal(unavailable.items[0].restarts, null);
  assert.equal(unavailable.failed_units, null); assert.equal(unavailable.timers.reboot.next_run, null);
  const summary = servicesSummary(unavailable);
  assert.equal(summary.title, "Unknown"); assert.equal(summary.active, "—"); assert.equal(summary.enabled, "—");
  assert.equal(summary.restarts, "—"); assert.equal(summary.failed, "—");
  assert.equal(servicesUnavailable(null), null);
  assert.doesNotThrow(() => servicesSnapshot(unavailable));
});

test("invalid metric settings cannot replace the stored revision or be reported as saved", () => {
  for (const patch of [{ enabled: "true" }, { revision: null }, { revision: "" }, { used_bytes: "1000" },
    { raw_hours: Infinity }, { minute_days: -1 }, { trimmed_at: {} }, { error: {} }]) {
    assert.throws(() => metricsSettingsSnapshot({ ...metrics, ...patch }));
  }
});

test("service read-back and metric acknowledgement use validated snapshots and uncached reads", async () => {
  const previous = globalThis.fetch; const calls = []; let invalid = false;
  try {
    globalThis.fetch = async (path, options) => {
      calls.push([path, options]);
      return Response.json(path.endsWith("/metrics") ? { ...metrics, ...(invalid ? { revision: {} } : {}) } : services);
    };
    const request = observedServicesRequest(createApiClient("service-fixture", { retryDelaysMs: [] }));
    assert.deepEqual(await request("/services"), services);
    assert.deepEqual(await request("/services/metrics"), metrics);
    assert.ok(calls.every(([, options]) => options.cache === "no-store"));
    invalid = true;
    await assert.rejects(request("/services/metrics", { method: "PUT", body: "{}" }));
    assert.equal(calls.length, 3); assert.equal(calls[2][1].method, "PUT");
  } finally { globalThis.fetch = previous; }
});
