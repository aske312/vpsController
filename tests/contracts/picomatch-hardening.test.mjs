import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { hardenPicomatch, picomatchDirectories } from "../../scripts/harden-picomatch.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const audit = fileURLToPath(new URL("../../scripts/audit-runtime.mjs", import.meta.url));
const advisory = "https://github.com/advisories/GHSA-fc5q-39xc-vjpf";

function installation(fn) {
  const temporaryRoot = resolve(tmpdir());
  const directory = mkdtempSync(join(temporaryRoot, "picomatch-guard-"));
  try {
    const nodes = picomatchDirectories(root).map((source, index) => {
      const target = join(directory, "node_modules", `parent-${index}`, "node_modules", "picomatch");
      cpSync(source, target, { recursive: true });
      const path = join(target, "lib", "parse.js");
      const content = readFileSync(path, "utf8");
      const marker = content.indexOf("// vpsController: reject repeating extglobs");
      if (marker !== -1) writeFileSync(path, content.slice(0, marker) + "module.exports = parse;\n");
      return relative(directory, target).replaceAll("\\", "/");
    });
    assert(nodes.length > 0);
    fn(directory, nodes);
  } finally {
    assert.equal(dirname(resolve(directory)), temporaryRoot);
    rmSync(directory, { recursive: true, force: true });
  }
}

test("all installed parser versions reject repeating extglobs before regex compilation and preserve ordinary globs", () => installation((directory, nodes) => {
  assert.throws(() => hardenPicomatch(directory, true), /Missing/);
  assert.equal(hardenPicomatch(directory), nodes.length);
  assert.equal(hardenPicomatch(directory), nodes.length);
  assert.equal(hardenPicomatch(directory, true, nodes), nodes.length);
  for (const node of nodes) {
    const result = spawnSync(process.execPath, ["-e", `
      const assert = require('node:assert/strict');
      const pm = require(process.argv[1]);
      const parser = require(process.argv[1] + '/lib/parse.js');
      for (const pattern of ['+(*)', '+(*a)', '*(a*)', '**/+(*)', '@(ok|+(a*))']) {
        for (const options of [{}, {fastpaths:false}, {maxExtglobRecursion:false}]) {
          assert.throws(() => pm.makeRe(pattern, options), /security guard/);
          assert.throws(() => pm.parse(pattern, options), /security guard/);
          assert.throws(() => parser.fastpaths(pattern, options), /security guard/);
        }
      }
      assert.equal(pm.isMatch('src/sub/file.ts', 'src/**/*.ts'), true);
      assert.equal(pm.isMatch('src/file.css', 'src/**/*.ts'), false);
      assert.equal(pm.isMatch('file+a.js', 'file+a.*'), true);
      assert.equal(pm.isMatch('file.ts', 'file.@(js|ts)'), true);
    `, join(directory, node)], { encoding: "utf8", timeout: 5000 });
    assert.equal(result.status, 0, result.stderr);
  }
}));

test("runtime audit accepts only the exact advisory with every installed copy hardened", () => installation((directory, nodes) => {
  const report = join(directory, "audit.json");
  const values = { picomatch: { severity: "high", via: [{ url: advisory }], nodes },
    vite: { severity: "high", via: ["picomatch"] },
    moderate: { severity: "moderate", via: [{ severity: "moderate", url: "https://github.com/advisories/GHSA-moderate" }] },
    vinext: { severity: "high", via: ["vite", "moderate"] } };
  const run = () => { writeFileSync(report, JSON.stringify({ vulnerabilities: values })); return spawnSync(process.execPath, [audit, "--input", report], { cwd: directory, encoding: "utf8" }); };
  assert.equal(run().status, 1);
  hardenPicomatch(directory);
  assert.equal(run().status, 0);
  values.picomatch.via.push({ url: "https://github.com/advisories/GHSA-another-issue" });
  assert.equal(run().status, 1);
  values.picomatch.via.pop();
  values.picomatch.nodes.push("node_modules/missing/picomatch");
  assert.equal(run().status, 1);
  values.picomatch.nodes.pop();
  const path = join(directory, nodes[0], "lib", "parse.js");
  writeFileSync(path, readFileSync(path, "utf8") + "\n// changed parser\n");
  assert.throws(() => hardenPicomatch(directory), /Unexpected/);
  assert.equal(run().status, 1);
}));
