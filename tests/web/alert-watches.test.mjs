import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { notificationFixture } from './notification-fixture.mjs';

const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : tree?.props ? [tree, ...nodes(tree.props.children)] : [];
function mount(file, props) {
  let cursor = 0;
  const slots = [], pending = [];
  const hooks = {
    useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial; return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }]; },
    useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useEffect(run, deps) { const index = cursor++; if (!slots[index] || deps.some((value, i) => value !== slots[index].deps[i])) pending.push(() => { slots[index]?.cleanup?.(); slots[index] = { deps, cleanup: run() }; }); },
  };
  const { api, store } = notificationFixture(hooks), exported = {}, actual = createRequire(import.meta.url);
  const source = readFileSync(new URL(`../../src/features/assistant/${file}.tsx`, import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  new Function('require', 'exports', output)(name => name === 'react' ? hooks : name.endsWith('/notification-center') ? api : actual(name), exported);
  const Component = file === 'alert-watches' ? exported.AlertWatches : exported.AlertDiagnosticRules;
  return { store, render() { cursor = 0; const tree = Component(props); pending.splice(0).forEach(run => run()); return tree; } };
}

test('watch editor stages operation/service scope, events, pause and deletion without a request', () => {
  const props = { value: [], catalog: { services: [{ id: 'api', name: 'API', unit: 'vps-control-api.service' }], operation_actions: ['application-update'] }, now: 1800000000,
    disabled: false, onChange: value => { props.value = value; } };
  const view = mount('alert-watches', props);
  nodes(view.render()).find(node => node.type === 'button' && node.props.children === 'Добавить правило').props.onClick();
  assert.equal(props.value.length, 1);
  assert.match(props.value[0].id, /^[a-f0-9]{32}$/);
  let tree = view.render();
  nodes(tree).find(node => node.type === 'select' && node.props.value === 'operation').props.onChange({ target: { value: 'service' } });
  assert.deepEqual(props.value[0].events, ['failed', 'unknown', 'recovered']);
  tree = view.render();
  nodes(tree).find(node => node.type === 'select' && node.props.value === '').props.onChange({ target: { value: 'api' } });
  tree = view.render();
  nodes(tree).find(node => node.type === 'button' && node.props.children === 'Пауза на час').props.onClick();
  assert.equal(props.value[0].snoozed_until, 1800003600);
  tree = view.render();
  nodes(tree).find(node => node.type === 'button' && node.props.children === 'Возобновить правило').props.onClick();
  assert.equal(props.value[0].snoozed_until, 0);
  nodes(view.render()).find(node => node.type === 'button' && node.props.children === 'Удалить правило').props.onClick();
  assert.equal(props.value.length, 0);
});

test('diagnostic thresholds save the read revision only on explicit save', async () => {
  const rules = { revision: 'a'.repeat(32), service_error: true, local_health: true, disk_pressure: true, http_error_rate: true,
    disk_percent: 95, http_error_percent: 10, http_min_requests: 20, window_minutes: 5, trigger_samples: 2, recovery_samples: 2 };
  const calls = [];
  const request = async (path, options) => { calls.push({ path, options }); return options?.method ? { ...JSON.parse(options.body), revision: 'b'.repeat(32) } : rules; };
  const view = mount('alert-diagnostic-rules', { request, blocked: false });
  view.render(); await Promise.resolve();
  let tree = view.render();
  nodes(tree).find(node => node.type === 'input' && node.props.value === 95).props.onChange({ target: { value: '90' } });
  assert.equal(calls.length, 1);
  tree = view.render();
  nodes(tree).find(node => node.type === 'button' && node.props.children === 'Сохранить условия').props.onClick();
  await Promise.resolve(); view.render();
  assert.equal(JSON.parse(calls[1].options.body).revision, rules.revision);
  assert.equal(JSON.parse(calls[1].options.body).disk_percent, 90);
  assert.equal(view.store.getSnapshot()[0].state, 'success');
});
