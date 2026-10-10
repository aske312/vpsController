import assert from "node:assert/strict";
import test from "node:test";
import { lightReleaseVersion } from "../scripts/release-version.mjs";

test("Light version series starts at the package version and advances only on production releases", () => {
  for (const [base, branch, tag, commit, expected] of [
    ["1.1.5", "test-light", null, null, "v1.1.5"],
    ["1.1.5", "light", null, null, "v1.1.5"],
    ["1.1.5", "test-light", "light-v1.1.4", "old", "v1.1.5"],
    ["1.1.5", "light", "light-v1.1.4", "old", "v1.1.5"],
    ["1.1.5", "test-light", "light-v1.1.5", "old", "v1.1.5"],
    ["1.1.5", "light", "light-v1.1.5", "old", "v1.1.6"],
    ["1.1.5", "light", "light-v1.1.5", "head", "v1.1.5"],
    ["1.0.0", "test-light", "light-v1.0.22", "old", "v1.0.22"],
    ["1.0.0", "light", "light-v1.0.22", "old", "v1.0.23"],
  ]) {
    assert.equal(lightReleaseVersion(base, branch, "head", tag ? { tag, commit } : null), expected);
  }
  assert.throws(() => lightReleaseVersion("invalid", "light", "head", null));
  assert.throws(() => lightReleaseVersion("1.1.5", "pro", "head", null));
  assert.throws(() => lightReleaseVersion("1.1.5", "light", "head", { tag: "light-v1.0.22", commit: "old" }));
});
