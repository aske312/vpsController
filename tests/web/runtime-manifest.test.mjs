import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { runtimeManifest } from "../../scripts/runtime-manifest.mjs";

const json = async (path) => JSON.parse(await readFile(new URL(path, import.meta.url), "utf8"));

test("locked runtime preserves the selected application dependencies, overrides and startup contract", async () => {
  const [source, manifest, lock] = await Promise.all([
    json("../../package.json"), json("../../scripts/runtime/package.json"), json("../../scripts/runtime/package-lock.json"),
  ]);
  assert.deepEqual(runtimeManifest(source), manifest);
  assert.deepEqual(lock.packages[""].dependencies, manifest.dependencies);
  assert.equal(lock.lockfileVersion, 3);
  assert.equal(manifest.scripts.start, source.scripts.start);
  assert.equal(manifest.devDependencies, undefined);
  assert.equal(manifest.overrides.tslib, "2.8.1");
  for (const [name, version] of Object.entries(manifest.dependencies)) {
    assert.equal(lock.packages[`node_modules/${name}`].version, version);
  }
  const targets = Object.keys(lock.packages);
  assert.ok(targets.some((name) => name.includes("linux-x64")));
  assert.ok(targets.some((name) => name.includes("linux-arm64")));
});

test("runtime generation fails when a required dependency disappears", () => {
  assert.throws(() => runtimeManifest({ dependencies: {} }), /Missing runtime dependency/);
});
