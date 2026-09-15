import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const panel = readFileSync("src/control-panel/control-panel.tsx", "utf8");
const workspace = readFileSync("src/control-panel/components/app-workspace.tsx", "utf8");
const styles = readFileSync("src/shared/styles/control-center.css", "utf8");

test("manual refresh uses one delayed, lightweight loading veil", () => {
  assert.match(panel, /setTimeout\(\(\) => \{/);
  assert.match(panel, /\}, 220\);/);
  assert.match(panel, /refreshCurrent\(false, false\)/);
  assert.match(workspace, /className="contentLoadingVeil"/);
  assert.match(styles, /@keyframes contentLoadingSweep/);
  assert.match(styles, /prefers-reduced-motion/);
  const veilRule = styles.match(/\.contentLoadingVeil \{[\s\S]*?\n\}/)?.[0] || "";
  assert.doesNotMatch(veilRule, /backdrop-filter/);
});
