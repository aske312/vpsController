import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

test("AWG domain catalog has distinct valid choices and a listed default", async () => {
  const source = await readFile(new URL("../app/awg-domain-presets.ts", import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } });
  const { awgDomainPresets, awgDefaultDomain, awgDomainGroups } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
  assert.equal(awgDomainPresets.length, 32);
  assert.equal(new Set(awgDomainPresets.map((item) => item.domain)).size, awgDomainPresets.length);
  assert.ok(awgDomainPresets.some((item) => item.domain === awgDefaultDomain));
  assert.equal(awgDomainGroups.length, 3);
  for (const item of awgDomainPresets) {
    assert.ok(item.label);
    assert.ok(awgDomainGroups.includes(item.group));
    assert.match(item.domain, /^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$/);
    assert.ok(item.domain.split(".").every((label) => label.length <= 63));
  }
});
