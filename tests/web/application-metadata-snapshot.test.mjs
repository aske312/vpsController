import assert from "node:assert/strict";
import test from "node:test";
import { applicationMetadataSnapshot, applicationMetadataUnavailable } from "../../src/control-panel/application-metadata-snapshot.ts";
import { applicationVersionState } from "../../src/features/application/version-state.ts";
import { metadata } from "./application-metadata-fixtures.mjs";

test("current metadata supports nullable refresh timestamps and missing legacy dependency inventory", () => {
  assert.deepEqual(applicationMetadataSnapshot(metadata), metadata);
  const { dependencies, ...legacy } = metadata;
  assert.equal(dependencies.length, 2);
  assert.deepEqual(applicationMetadataSnapshot(legacy), legacy);
  assert.doesNotThrow(() => applicationMetadataSnapshot({ application_version: {}, updates: { available: null } }));
  assert.doesNotThrow(() => applicationMetadataSnapshot({ application_version: {}, updates: { available: null, security: null, kernel_available: null, error: "Unavailable" } }));
});

test("malformed version identities, flags, counters and dependencies cannot reach the view", () => {
  for (const change of [value => value.application_version.current_commit = {}, value => value.application_version.outdated = "false",
    value => value.application_version.error = [], value => value.application_version.refreshing = "false",
    value => value.updates.available = "0", value => value.updates.security = -1, value => value.updates.checked_at = {},
    value => value.dependencies = {}, value => value.dependencies.push(value.dependencies[0]),
    value => value.dependencies[0].version = {}, value => value.dependencies[0].name = {}]) {
    const value = structuredClone(metadata); change(value); assert.throws(() => applicationMetadataSnapshot(value));
  }
});

test("unavailable metadata preserves dependency identity without current version or update claims", () => {
  const value = applicationMetadataUnavailable(metadata);
  assert.deepEqual(value.dependencies, metadata.dependencies.map(({ id, name }) => ({ id, name })));
  assert.equal(value.application_version.current_commit, undefined);
  assert.equal(value.updates.available, undefined);
  assert.equal(applicationVersionState(value.application_version), "unknown");
  assert.equal(applicationMetadataUnavailable(null), null);
});

test("a pending or failed cached comparison cannot confirm current", () => {
  const version = metadata.application_version;
  assert.equal(applicationVersionState(version), "current");
  assert.equal(applicationVersionState({ ...version, current_commit: version.current_commit.slice(0, 7) }), "current");
  assert.equal(applicationVersionState({ ...version, latest_commit: version.latest_commit.slice(0, 12) }), "current");
  assert.equal(applicationVersionState({ ...version, current_commit: version.current_commit.slice(0, 7), outdated: true }), "unknown");
  assert.equal(applicationVersionState({ ...version, current_commit: "unknown", latest_commit: "unknown" }), "unknown");
  assert.equal(applicationVersionState({ ...version, current_commit: "manual", latest_commit: "manual" }), "unknown");
  assert.equal(applicationVersionState({ ...version, refreshing: true }), "checking");
  assert.equal(applicationVersionState({ ...version, error: "Unavailable" }), "unknown");
  assert.equal(applicationVersionState({ ...version, latest_commit: "", outdated: false }), "unknown");
  assert.equal(applicationVersionState({ ...version, latest_commit: "b".repeat(40), outdated: false }), "unknown");
  assert.equal(applicationVersionState({ ...version, latest_commit: "b".repeat(40), outdated: true }), "outdated");
  assert.equal(applicationVersionState({ ...version, outdated: true }), "unknown");
  assert.equal(applicationVersionState({ ...version, outdated: null }), "unknown");
  assert.equal(applicationVersionState(), "unknown");
});
