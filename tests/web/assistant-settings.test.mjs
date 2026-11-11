import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import * as helpers from '../../src/features/assistant/assistant-state.ts';
import * as chatFiles from '../../src/features/assistant/chat-files.ts';
import * as answerFormat from '../../src/features/assistant/answer-format.ts';

// Render the real form with controlled hooks and I/O, including disabled ancestors.
function mount(status, catalog = []) {
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
  const settings = {};
  const require = name => name === 'react' ? hooks : name === './assistant-state' ? helpers : name === './nodera-settings' ? settings : name === './chat-files' ? chatFiles : name === './answer-format' ? answerFormat : name === './assistant-answer' ? { AssistantAnswer() {} } : name === './chat-icon' ? { ChatIcon() {} }
    : name.endsWith('/protocol-icon') ? { ProtocolIcon() {} } : requireActual(name);
  const settingsSource = readFileSync(new URL('../../src/features/assistant/nodera-settings.tsx', import.meta.url), 'utf8');
  const compiledSettings = ts.transpileModule(settingsSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  new Function('require', 'exports', compiledSettings.outputText)(require, settings);
  new Function('require', 'exports', outputText)(require, exported);
  const request = async (path, options) => {
    if (path === '/assistant/models') return { models: catalog };
    if (options?.method === 'PUT' || path === '/assistant/chat') writes.push(JSON.parse(options.body));
    if (path === '/assistant/chat') return { result: { answer: 'OK', tools_used: [] } };
    return status;
  };
  const props = { request, busy: false, refreshKey: 0, onDirtyChange() {}, confirmAction: async () => false, admitOperation() {}, onOperation() {} };
  return { writes, render() {
    cursor = 0;
    const tree = exported.AssistantView(props);
    const expand = node => {
      if (Array.isArray(node)) return node.map(expand);
      if (!node?.props) return node;
      if (node.type === settings.NoderaSettings) return expand(node.type(node.props));
      return { ...node, props: { ...node.props, children: expand(node.props.children) } };
    };
    const expanded = expand(tree);
    for (const effect of effects.splice(0)) effect();
    return expanded;
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
  const view = mount(fixture());
  view.render(); await Promise.resolve();
  let tree = view.render();
  assert.equal(nodes(tree, n => n.type === 'h1').length, 0);
  nodes(tree, n => n.type === 'button' && n.props.children?.[1] === 'Настройки AI')[0].node.props.onClick();
  tree = view.render();
  const toggle = nodes(tree, n => n.type === 'input' && n.props.type === 'checkbox')[0];
  assert.equal(toggle.disabled, false);
  toggle.node.props.onChange({ target: { checked: true } });
  tree = view.render();
  const save = nodes(tree, n => n.type === 'button' && n.props.children === 'Сохранить')[0];
  assert.equal(save.disabled, false);
  save.node.props.onClick();
  await Promise.resolve();
  assert.equal(view.writes.length, 1);
  assert.equal(view.writes[0].component, 'nodera');
  assert.equal(view.writes[0].modules['ai-agent'], true);
  assert.equal(view.writes[0].bot_token, null);
});

test('running Telegram bot locks its token and shows the stop action', async () => {
  const view = mount(fixture());
  view.render(); await Promise.resolve();
  nodes(view.render(), n => n.type === 'button' && n.props.children?.[1] === 'Доступ')[0].node.props.onClick();
  const tree = view.render();
  const toggles = nodes(tree, n => n.type === 'input' && n.props.type === 'checkbox');
  assert.equal(toggles.length, 0);
  assert.equal(nodes(tree, n => n.type === 'fieldset' && n.props.className === 'noderaBotToken')[0].disabled, true);
  assert.equal(nodes(tree, n => n.type === 'button' && n.props.children === 'Остановить')[0].disabled, false);
});

test('catalog mode loads any number of models and sends the chat selection without changing settings', async () => {
  const status = fixture();
  Object.assign(status.settings, { model_mode: 'catalog', model: '', api_key_configured: true,
    provider_url: 'https://api.nyxos.shop/v1', modules: { 'ai-agent': true, 'nodera-bot': true } });
  Object.assign(status.modules[0], { enabled: true, configured: true });
  const models = Array.from({ length: 250 }, (_, i) => `model-${i}`);
  const view = mount(status, models);
  view.render(); await Promise.resolve(); view.render(); await Promise.resolve();
  let tree = view.render();
  const selector = nodes(tree, n => n.type === 'button' && n.props['aria-label'] === 'Модель для запроса')[0];
  assert.equal(selector.node.props['aria-expanded'], false);
  assert.equal(selector.disabled, false);
  nodes(tree, n => n.type === 'textarea')[0].node.props.onChange({ target: { value: 'hello', style: {}, scrollHeight: 48 } });
  tree = view.render();
  nodes(tree, n => n.type === 'textarea')[0].node.props.onKeyDown({ key: 'Enter', ctrlKey: true, preventDefault() {} });
  assert.equal(view.writes.length, 0);
  selector.node.props.onClick();
  tree = view.render();
  assert.equal(nodes(tree, n => n.type === 'input' && n.props['aria-label'] === 'Найти модель').length, 0);
  const options = nodes(tree, n => n.props.role === 'option');
  assert.equal(options.length, 250);
  options.at(-1).node.props.onClick();
  nodes(tree, n => n.type === 'textarea')[0].node.props.onChange({ target: { value: 'hello', style: {}, scrollHeight: 48 } });
  tree = view.render();
  nodes(tree, n => n.type === 'textarea')[0].node.props.onKeyDown({ key: 'Enter', ctrlKey: true, preventDefault() {} });
  await Promise.resolve();
  assert.equal(view.writes.length, 1);
  assert.equal(view.writes[0].model, 'model-249');
  assert.equal(status.settings.model, '');
});

test('new catalog chats use the saved default and copy actions sit outside the answer', async () => {
  const status = fixture();
  Object.assign(status.settings, { model_mode: 'catalog', model: 'default-model', api_key_configured: true,
    provider_url: 'https://provider.example/v1', modules: { 'ai-agent': true, 'nodera-bot': true } });
  Object.assign(status.modules[0], { enabled: true, configured: true });
  const view = mount(status, ['default-model', 'another-model']);
  view.render(); await Promise.resolve(); view.render(); await Promise.resolve();
  let tree = view.render();
  nodes(tree, n => n.type === 'textarea')[0].node.props.onChange({ target: { value: 'hello', style: {}, scrollHeight: 48 } });
  tree = view.render();
  nodes(tree, n => n.type === 'textarea')[0].node.props.onKeyDown({ key: 'Enter', preventDefault() {} });
  await Promise.resolve();
  assert.equal(view.writes[0].model, 'default-model');
  tree = view.render();
  const answer = nodes(tree, n => n.type === 'article' && n.props.className.startsWith('mcpAnswer'))[0].node;
  assert.equal(nodes(answer, n => n.props['aria-label'] === 'Скопировать ответ').length, 0);
  const actions = nodes(tree, n => n.props.className === 'mcpAnswerActions')[0].node;
  assert.equal(nodes(actions, n => n.props['aria-label'] === 'Скопировать ответ').length, 1);
  nodes(tree, n => n.props['aria-label'] === 'Модель для запроса')[0].node.props.onClick();
  tree = view.render();
  nodes(tree, n => n.props.role === 'option').at(-1).node.props.onClick();
  tree = view.render();
  nodes(tree, n => n.props['aria-label'] === 'Новый чат')[0].node.props.onClick();
  tree = view.render();
  const selection = nodes(tree, n => n.props['aria-label'] === 'Модель для запроса')[0].node;
  assert.equal(selection.props.children[0].props.children, 'default-model');
});
