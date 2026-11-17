import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// GHSA-fc5q-39xc-vjpf has no upstream release. Reject repeating extglobs before
// either parser path can build a backtracking RegExp. Ordinary globs and
// non-repeating extglobs remain available; callers cannot disable this guard.
const originalHashes = new Map([
  ["2.3.2", "3a91656f440069aa8f8e15c9d695b9f6b2209df1eab2be8127c72cbcca45ff5f"],
  ["4.0.4", "ce57c04e04994b83e2ca566170fa192e6fe19d710bea6da00ce2f76d01c0dea4"],
  ["4.0.7", "4e1e05447668aa1e94366c5516a2bcd8f5fff800cc92b1d0cd7d77703b8618c4"],
]);
const originalExport = "module.exports = parse;";
const guardedExport = `// vpsController: reject repeating extglobs (GHSA-fc5q-39xc-vjpf).
const guardRepeatedExtglobs = input => {
  if (typeof input === 'string' && (input.includes('*(') || input.includes('+('))) {
    throw new SyntaxError('Repeating extglobs are disabled by the picomatch security guard');
  }
};
const guardedParse = (input, ...args) => {
  guardRepeatedExtglobs(input);
  return parse(input, ...args);
};
Object.assign(guardedParse, parse);
guardedParse.fastpaths = (input, ...args) => {
  guardRepeatedExtglobs(input);
  return parse.fastpaths(input, ...args);
};
module.exports = guardedParse;`;
const hash = source => createHash("sha256").update(source).digest("hex");

export function picomatchDirectories(root) {
  const found = [];
  function modules(directory) {
    if (!existsSync(directory)) return;
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (!entry.isDirectory() || entry.name.startsWith(".")) continue;
      const path = join(directory, entry.name);
      if (entry.name.startsWith("@")) {
        for (const child of readdirSync(path, { withFileTypes: true })) if (child.isDirectory()) pkg(join(path, child.name));
      } else pkg(path);
    }
  }
  function pkg(directory) {
    const manifest = join(directory, "package.json");
    if (existsSync(manifest) && JSON.parse(readFileSync(manifest, "utf8")).name === "picomatch") found.push(directory);
    modules(join(directory, "node_modules"));
  }
  modules(join(root, "node_modules"));
  return found;
}

export function hardenPicomatch(root, check = false, expectedNodes = []) {
  root = resolve(root);
  const directories = picomatchDirectories(root);
  if (!directories.length) throw new Error("No installed picomatch copies found");
  const installed = new Set(directories.map(directory => relative(root, directory).replaceAll("\\", "/")));
  if (expectedNodes.some(path => typeof path !== "string" || !installed.has(path))) throw new Error("Audit references an unverified picomatch installation");
  // Validate every file before changing any copy, including restored caches.
  const patches = directories.map(directory => {
    const version = JSON.parse(readFileSync(join(directory, "package.json"), "utf8")).version;
    const expected = originalHashes.get(version);
    if (!expected) throw new Error(`Unsupported picomatch version: ${version}`);
    const path = join(directory, "lib", "parse.js");
    const source = readFileSync(path, "utf8");
    const original = source.replace(guardedExport, originalExport);
    if (hash(original) !== expected) throw new Error(`Unexpected picomatch parser content: ${version}`);
    const patched = original.replace(originalExport, guardedExport);
    if (check && source !== patched) throw new Error(`Missing picomatch security guard: ${version}`);
    return { path, source, patched };
  });
  if (!check) for (const { path, source, patched } of patches) if (source !== patched) writeFileSync(path, patched);
  return directories.length;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const count = hardenPicomatch(process.cwd(), process.argv.includes("--check"));
    console.log(`Verified picomatch security guard in ${count} installed copies`);
  } catch (cause) {
    console.error(cause.message);
    process.exitCode = 1;
  }
}
