import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const panel = readFileSync("src/control-panel/control-panel.tsx", "utf8");
const view = readFileSync("src/features/network/network-view.tsx", "utf8");
const cssFiles = [
  "src/features/network/network.css",
  "src/features/network/system-dns-control.css",
  "src/features/network/dns-matrix.css",
].map((file) => readFileSync(file, "utf8"));

test("network feature owns its state and styles stay inside the network page", () => {
  assert.match(panel, /<NetworkView request=\{request\} refreshKey=\{networkRefreshKey\} \/>/);
  assert.doesNotMatch(panel, /const \[dnsDraft|const \[network, setNetwork|readNetworkControl|saveNetworkDns|probeNetworkDns/);
  assert.match(view, /readNetworkControl\(request\)/);
  assert.match(view, /data-network-page="true"/);
  for (const css of cssFiles) {
    assert.match(css, /\[data-network-page\]/);
    assert.doesNotMatch(css, /(?:^|\})\s*\.network[A-Za-z]/m);
  }
});
