import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";

function mount() {
  const slots = [], effects = [], calls = [];
  let cursor = 0, refreshKey = 0;
  const same = (a, b) => a && b && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));
  const hooks = {
    useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = initial; return [slots[index], value => { slots[index] = typeof value === "function" ? value(slots[index]) : value; }]; },
    useRef(initial) { const index = cursor++; return slots[index] ||= { current: initial }; },
    useCallback(fn, deps) { const index = cursor++; if (!same(slots[index]?.deps, deps)) slots[index] = { fn, deps }; return slots[index].fn; },
    useEffect(fn, deps) { const index = cursor++; if (!same(slots[index]?.deps, deps)) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = fn(); }); } },
  };
  const actual = createRequire(import.meta.url), exported = {};
  const source = readFileSync(new URL("../../src/features/assistant/telegram-alerts.tsx", import.meta.url), "utf8");
  const output = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  new Function("require", "exports", output)(name => name === "react" ? hooks : actual(name), exported);
  const request = (path, options) => new Promise(resolve => calls.push({ path, options, resolve }));
  return { calls, refresh() { refreshKey++; }, render() { cursor = 0; const tree = exported.TelegramAlerts({ request, refreshKey }); for (const effect of effects.splice(0)) effect(); return tree; } };
}
function nodes(tree, predicate) {
  if (Array.isArray(tree)) return tree.flatMap(node => nodes(node, predicate));
  if (!tree?.props) return [];
  return [...(predicate(tree) ? [tree] : []), ...nodes(tree.props.children, predicate)];
}
const tick = async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); };
const status = revision => ({ settings: { enabled: false, owner_id: 123, failures: true, recoveries: true, boots: true }, revision: revision.repeat(64), owners: [{ telegram_id: 123, name: "Owner" }], token_configured: true, pending: 0, last_delivery: null });
test("background refresh preserves an alert draft and its original optimistic revision", async () => {
  const view = mount(); view.render(); view.calls[0].resolve(status("a")); await tick();
  let tree = view.render();
  nodes(tree, node => node.props.role === "switch")[0].props.onChange({ target: { checked: true } });
  view.refresh(); view.render(); view.calls[1].resolve(status("b")); await tick(); tree = view.render();
  assert.equal(nodes(tree, node => node.props.role === "switch")[0].props.checked, true);
  nodes(tree, node => node.type === "button" && node.props.children === "Сохранить алерты")[0].props.onClick();
  const write = view.calls[2];
  assert.equal(JSON.parse(write.options.body).expected_revision, "a".repeat(64));
  assert.equal(JSON.parse(write.options.body).enabled, true);
  write.resolve({ ...status("c"), settings: { ...status("c").settings, enabled: true } }); await tick();
  assert.equal(nodes(view.render(), node => node.props.role === "switch")[0].props.checked, true);
});
test("a read started before a save cannot replace the successfully saved alert settings", async () => {
  const view = mount(); view.render(); view.calls[0].resolve(status("a")); await tick();
  view.refresh(); let tree = view.render();
  nodes(tree, node => node.props.role === "switch")[0].props.onChange({ target: { checked: true } }); tree = view.render();
  nodes(tree, node => node.type === "button" && node.props.children === "Сохранить алерты")[0].props.onClick();
  view.calls[2].resolve({ ...status("b"), settings: { ...status("b").settings, enabled: true } }); await tick();
  view.calls[1].resolve(status("a")); await tick();
  assert.equal(nodes(view.render(), node => node.props.role === "switch")[0].props.checked, true);
});
