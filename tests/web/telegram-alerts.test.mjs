import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import { notificationFixture } from './notification-fixture.mjs';

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
  const { api, store } = notificationFixture(hooks);
  const source = readFileSync(new URL("../../src/features/assistant/telegram-alerts.tsx", import.meta.url), "utf8");
  const output = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  new Function("require", "exports", output)(name => name === "react" ? hooks : name === "./alert-watches" ? { AlertWatches() {} } : name === "./alert-diagnostic-rules" ? { AlertDiagnosticRules() {} } : name.endsWith('/notification-center') ? api : actual(name), exported);
  const request = (path, options) => path === "/assistant/alerts/catalog" ? Promise.resolve({ services: [], operation_actions: [] }) : new Promise((resolve, reject) => calls.push({ path, options, resolve, reject }));
  return { calls, store, refresh() { refreshKey++; }, render() { cursor = 0; const tree = exported.TelegramAlerts({ request, refreshKey }); for (const effect of effects.splice(0)) effect(); return tree; } };
}
function nodes(tree, predicate) {
  if (Array.isArray(tree)) return tree.flatMap(node => nodes(node, predicate));
  if (!tree?.props) return [];
  return [...(predicate(tree) ? [tree] : []), ...nodes(tree.props.children, predicate)];
}
const tick = async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); };
const status = revision => ({ settings: { enabled: false, owner_id: 123, failures: true, recoveries: true, boots: true }, revision: revision.repeat(64), owners: [{ telegram_id: 123, name: "Owner" }], token_configured: true, pending: 0, last_delivery: null });

test('background failures use one notification, respect dismissal and clear after recovery', async () => {
  const view = mount(); view.render(); view.calls[0].reject(new Error('Alerts unavailable')); await tick();
  let tree = view.render();
  assert.equal(nodes(tree, node => ['alert', 'status'].includes(node.props.role)).length, 0);
  assert.equal(view.store.getSnapshot().length, 1);
  view.refresh(); view.render(); view.calls[1].reject(new Error('Alerts unavailable')); await tick(); view.render();
  assert.equal(view.store.getSnapshot()[0].count, 1);
  view.store.dismiss(view.store.getSnapshot()[0].id);
  view.refresh(); view.render(); view.calls[2].reject(new Error('Alerts unavailable')); await tick(); view.render();
  assert.equal(view.store.getSnapshot().length, 0);
  view.refresh(); view.render(); view.calls[3].resolve(status('a')); await tick(); view.render();
  view.refresh(); view.render(); view.calls[4].reject(new Error('Alerts unavailable')); await tick(); view.render();
  assert.equal(view.store.getSnapshot().length, 1);
});

test('saving alerts publishes its result in notifications, not the page', async () => {
  const view = mount(); view.render(); view.calls[0].resolve(status('a')); await tick();
  let tree = view.render();
  nodes(tree, node => node.props.role === 'switch')[0].props.onChange({ target: { checked: true } });
  tree = view.render();
  nodes(tree, node => node.type === 'button' && node.props.children === 'Сохранить алерты')[0].props.onClick();
  view.calls[1].resolve(status('b')); await tick(); tree = view.render();
  assert.equal(view.store.getSnapshot()[0].state, 'success');
  assert.equal(nodes(tree, node => ['alert', 'status'].includes(node.props.role)).length, 0);
});
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

test('watch changes and delivery pause are staged and sent together with the current revision', async () => {
  const view = mount(); view.render(); view.calls[0].resolve(status('a')); await tick();
  let tree = view.render();
  const rule = { id: 'b'.repeat(32), name: 'API', enabled: true, source: 'service', target: 'api', operation_id: null, events: ['failed', 'recovered'], after_seconds: 600, snoozed_until: 0 };
  nodes(tree, node => typeof node.type === 'function' && node.type.name === 'AlertWatches')[0].props.onChange([rule]);
  tree = view.render();
  nodes(tree, node => node.type === 'button' && node.props.children === 'Пауза на час')[0].props.onClick();
  assert.equal(view.calls.length, 1);
  tree = view.render();
  nodes(tree, node => node.type === 'button' && node.props.children === 'Сохранить алерты')[0].props.onClick();
  const body = JSON.parse(view.calls[1].options.body);
  assert.deepEqual(body.watches, [rule]);
  assert.equal(body.expected_revision, 'a'.repeat(64));
  assert.ok(body.snoozed_until > Date.now() / 1000);
  view.calls[1].resolve(status('b')); await tick();
});
