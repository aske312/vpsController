import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { isDeepStrictEqual } from "node:util";

export function runtimeManifest(source) {
  const names = [
    "next", "react", "react-dom", "vinext", "vite", "@cloudflare/vite-plugin",
    "@vitejs/plugin-react", "@vitejs/plugin-rsc", "react-server-dom-webpack",
  ];
  const all = { ...source.dependencies, ...source.devDependencies };
  const result = { ...source, dependencies: Object.fromEntries(names.map((name) => {
    if (!all[name]) throw new Error(`Missing runtime dependency: ${name}`);
    return [name, all[name]];
  })) };
  delete result.devDependencies;
  return result;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const source = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  const path = new URL("./runtime/package.json", import.meta.url);
  const expected = runtimeManifest(source);
  if (process.argv.includes("--write")) {
    writeFileSync(path, JSON.stringify(expected, null, 2) + "\n");
  } else if (!isDeepStrictEqual(expected, JSON.parse(readFileSync(path, "utf8")))) {
    throw new Error("Runtime manifest is stale. Run node scripts/runtime-manifest.mjs --write and regenerate its lockfile.");
  }
}
