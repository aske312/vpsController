import test from "node:test";
import assert from "node:assert/strict";
import { readFile, access } from "node:fs/promises";

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

test("Light v2 uses a modular entry point instead of the legacy page monolith", async () => {
  const page = await read("app/page.tsx");
  assert.match(page, /LightApplication/);
  assert.ok(page.split("\n").length < 12);
  await Promise.all(["src/light/light-application.tsx", "src/light/contracts.ts", "src/light/api.ts", "src/light/format.ts", "src/light/light.css"].map((path) => access(new URL(`../${path}`, import.meta.url))));
});

test("Light exposes only overview, connections, WireGuard and AmneziaWG", async () => {
  const contracts = await read("src/light/contracts.ts");
  assert.match(contracts, /views: \["overview", "connections", "wg", "awg"\]/);
  assert.match(contracts, /protocols: \["wg", "awg"\]/);
  for (const feature of ["mihomo", "network", "security", "services", "application"]) assert.doesNotMatch(contracts, new RegExp(`"${feature}"`));
});

test("Light UI has no PRO navigation or API calls", async () => {
  const app = await read("src/light/light-application.tsx");
  for (const feature of ["Mihomo", "Безопасность", "Службы", "Приложение", "/network", "/dns", "/security", "/services", "/application"]) assert.doesNotMatch(app, new RegExp(feature));
  assert.match(app, /\/overview/);
  assert.match(app, /\/clients/);
  assert.match(app, /\/protocols\/\$\{protocol\}/);
});

test("Light filters server responses to the supported edition capabilities", async () => {
  const app = await read("src/light/light-application.tsx");
  assert.match(app, /item\.protocol === "wg" \|\| item\.protocol === "awg"/);
  assert.match(app, /item\.id === "wg" \|\| item\.id === "awg"/);
});

test("Light uses the canonical PRO palette", async () => {
  const css = await read("src/light/light.css");
  for (const token of ["--bg:#071018", "--panel:#0c1821", "--blue:#70b7e8", "--cyan:#61cfd8", "--violet:#9b8bd8", "--green:#69c8aa"]) assert.match(css, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
});

test("authentication remains browser-session scoped", async () => {
  const app = await read("src/light/light-application.tsx");
  const api = await read("src/light/api.ts");
  assert.match(app, /sessionStorage\.setItem\("312-light-session"/);
  assert.match(app, /sessionStorage\.removeItem\("312-light-session"/);
  assert.match(api, /Authorization: `Basic \$\{this\.credentials\}`/);
  assert.doesNotMatch(app, /localStorage/);
});

test("WG and AWG remain independent installable modules", async () => {
  for (const protocol of ["wireguard", "amneziawg"]) {
    await access(new URL(`../protocol-images/${protocol}/manifest.json`, import.meta.url));
    await access(new URL(`../protocol-images/${protocol}/install.sh`, import.meta.url));
    await access(new URL(`../protocol-images/${protocol}/uninstall.sh`, import.meta.url));
  }
  const main = await read("api/main.py");
  assert.match(main, /Literal\["wg", "awg"\]/);
});

test("the release package remains self-contained and excludes local state", async () => {
  const build = await read("scripts/build-release.sh");
  assert.match(build, /"\$\{ROOT_DIR\}\/" "\$\{STAGE\}\/"/);
  assert.match(build, /\.runtime/);
  assert.match(build, /\.servers/);
  assert.match(build, /docs\/audit/);
  assert.match(build, /docs\/backlog/);
});

test("legal and connection documents remain part of Light", async () => {
  await Promise.all(["LICENSE", "docs/PRIVACY_POLICY.md", "docs/TERMS_OF_USE.md", "docs/CONNECTION_GUIDE.md", "public/connection-guide-wg-awg.pdf"].map((path) => access(new URL(`../${path}`, import.meta.url))));
});
