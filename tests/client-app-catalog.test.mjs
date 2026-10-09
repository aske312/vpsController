import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";

test("every protocol and supported OS offers at least two distinct client choices with official HTTPS sources", () => {
  const source = readFileSync(new URL("../app/client-apps.tsx", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const compiledModule = { exports: {} };
  new Function("require", "module", "exports", compiled)(createRequire(import.meta.url), compiledModule, compiledModule.exports);
  const { clientApps } = compiledModule.exports;
  for (const protocol of ["awg", "hysteria2", "tuic", "xray"]) {
    for (const os of ["windows", "ios", "android"]) {
      const apps = clientApps[protocol][os];
      assert.ok(new Set(apps.map(app => app.name)).size >= 2, `${protocol}/${os}`);
      for (const app of apps) {
        assert.ok(app.formats && app.sources.length);
        for (const source of app.sources) assert.equal(new URL(source.href).protocol, "https:");
      }
    }
  }
  for (const protocol of ["hysteria2", "tuic"]) {
    const ios = clientApps[protocol].ios;
    assert.ok(ios.some(app => app.name === "Karing"));
    assert.ok(ios.some(app => app.name === "sing-box MT"));
    assert.ok(!ios.some(app => app.name === "Hiddify"));
  }
});
