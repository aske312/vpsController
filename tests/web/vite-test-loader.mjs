import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

export function createTestLoader(owner, plugins = []) {
  // Parallel Node test processes must never rename the same optimizer cache.
  const cacheDir = join(fileURLToPath(new URL("../../.runtime/vite-tests/", import.meta.url)), basename(fileURLToPath(owner)));
  return createServer({
    configFile: false,
    cacheDir,
    plugins,
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
  });
}
