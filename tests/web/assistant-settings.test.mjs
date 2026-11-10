import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import * as helpers from '../../src/features/assistant/assistant-state.ts';

// Render the real form with controlled hooks and I/O, including disabled ancestors.
function mount(mode, status) {
  const slots = [], effects = [], writes = [];
  let cursor = 0;
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ||= { current: initial }; },
    useId() { return `fixture-${cursor++}`; },
    useCallback(fn, deps) {
      const index = cursor++;
      if (!same(slots[index]?.deps, deps)) slots[index] = { deps, fn };
      return slots[index].fn;
    },
    useEffect(fn, deps) {
      const index = cursor++;
      if (!same(slots[index]?.deps, deps)) { slots[index] = { deps }; effects.push(fn); }
    },
  };
  const requireActual = createRequire(import.meta.url), exported = {};
  const source = readFileSync(new URL('../../src/features/assistant/assistant-view.tsx', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  const require = name => name === 'react' ? hooks : name === './assistant-state' ? helpers
    : name.endsWith('/protocol-icon') ? { ProtocolIcon() {} } : requireActual(name);
  new Function('require', 'exports', outputText)(require, exported);
  const request = async (_path, options) => {
    if (options?.method === 'PUT') writes.push(JSON.parse(options.body));
    return status;
  };
  const props = { mode, request, busy: false, refreshKey: 0, onDirtyChange() {}, confirmAction: async () => false, admitOperation() {} };
  return { writes, render() {
    cursor = 0;
    const tree = exported.AssistantView(props);
    for (const effect of effects.splice(0)) effect();
    return tree;
  } };
}
function nodes(tree, predicate, disabled = false) {
  if (Array.isArray(tree)) return tree.flatMap(child => nodes(child, predicate, disabled));
  if (!tree || typeof tree !== 'object') return [];
  const blocked = disabled || tree.props?.disabled === true;
  return [...(predicate(tree) ? [{ node: tree, disabled: blocked }] : []), ...nodes(tree.props?.children, predicate, blocked)];
}
function fixture() {
  return { settings: { revision: 'a'.repeat(32), modules: { 'ai-agent': false, 'nodera-bot': true },
    mcp: { 'telegram-mcp': false, 'vps-controller-mcp': false }, provider_url: '', model: '', telegram_api_id: null,
    accounts: [], api_key_configured: false, bot_token_configured: true, telegram_api_hash_configured: false },
    modules: ['ai-agent', 'nodera-bot'].map(id => ({ id, installed: true, configured: id === 'nodera-bot', management: { state: 'managed' } })),
    mcp_modules: [], tools: {}, bot: { active: true }, legacy_bot: {} };
}

test('AI settings can be edited and submitted while NoderaBot is running', async () => {
  const view = mount('agent', fixture());
  view.render(); await Promise.resolve();
  let tree = view.render();
  const url = nodes(tree, n => n.type === 'input' && n.props.type === 'url')[0];
  assert.equal(url.disabled, false);
  url.node.props.onChange({ target: { value: 'https://provider.example/v1' } });
  tree = view.render();
  const toggle = nodes(tree, n => n.type === 'input' && n.props.type === 'checkbox')[0];
  assert.equal(toggle.disabled, false);
  toggle.node.props.onChange({ target: { checked: true } });
  tree = view.render();
  assert.equal(nodes(tree, n => n.type === 'button' && n.props.type === 'submit')[0].disabled, false);
  await nodes(tree, n => n.type === 'form')[0].node.props.onSubmit({ preventDefault() {} });
  assert.equal(view.writes.length, 1);
  assert.equal(view.writes[0].component, 'ai-agent');
  assert.equal(view.writes[0].provider_url, 'https://provider.example/v1');
  assert.equal(view.writes[0].modules['ai-agent'], true);
  assert.equal(view.writes[0].bot_token, null);
});

test('running NoderaBot still locks its own token and enable switch', async () => {
  const view = mount('bot', fixture());
  view.render(); await Promise.resolve();
  const tree = view.render();
  assert.equal(nodes(tree, n => n.type === 'input' && n.props.type === 'checkbox')[0].disabled, true);
  assert.equal(nodes(tree, n => n.type === 'fieldset')[0].disabled, true);
});
