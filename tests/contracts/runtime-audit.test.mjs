import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(new URL("../../scripts/audit-runtime.mjs", import.meta.url));

function runReport(value) {
  const directory = mkdtempSync(join(tmpdir(), "runtime-audit-"));
  const report = join(directory, "report.json");
  writeFileSync(report, JSON.stringify(value));
  return spawnSync(process.execPath, [script, "--input", report], { encoding: "utf8" });
}

function runAudit(vulnerabilities) {
  return runReport({ vulnerabilities });
}

test("runtime audit rejects registry errors and incomplete reports", () => {
  for (const report of [{ error: { code: "E503" } }, {}, null, [], { vulnerabilities: [] }, { vulnerabilities: null }]) {
    const result = runReport(report);
    assert.equal(result.status, 1, JSON.stringify(report));
    assert.match(result.stderr, /invalid or unavailable report/);
  }
});

test("runtime audit accepts a valid report without vulnerabilities", () => {
  const result = runAudit({});
  assert.equal(result.status, 0, result.stderr);
});

test("runtime audit allows only the unpatched braces advisory and its dependency chain", () => {
  const result = runAudit({
    braces: { severity: "high", via: [{ url: "https://github.com/advisories/GHSA-vfj7-8cjw-p6xm" }] },
    micromatch: { severity: "high", via: ["braces"] },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /Allowed runtime advisory/);
});

test("runtime audit still blocks every other high severity advisory", () => {
  const result = runAudit({
    dependency: { severity: "high", via: [{ url: "https://github.com/advisories/GHSA-block-this" }] },
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Blocking runtime vulnerabilities: dependency \(high\)/);
});
