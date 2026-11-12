import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import * as helpers from '../../src/features/assistant/assistant-state.ts';
import * as chatFiles from '../../src/features/assistant/chat-files.ts';
import * as answerFormat from '../../src/features/assistant/answer-format.ts';
import * as providerConnection from '../../src/features/assistant/provider-connection.ts';
import * as providerDetails from '../../src/features/assistant/provider-details.ts';

// Render the real form with controlled hooks and I/O, including disabled ancestors.
function mount(status, catalog = [], overrides = {}) {
  const slots = [], effects = [], writes = [], notifications = [];
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
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  const settings = {};
  const require = name => name === 'react' ? hooks : name === './assistant-state' ? helpers : name === './nodera-settings' ? settings : name === './chat-files' ? chatFiles : name === './answer-format' ? answerFormat : name === './assistant-answer' ? { AssistantAnswer() {} } : name === './chat-icon' ? { ChatIcon() {} }
    : name === './provider-connection' ? providerConnection : name === './provider-details' ? providerDetails : name.endsWith('/notification-center') ? { useNotifier: () => ({ error: message => notifications.push({state:'error',message}), success: message => notifications.push({state:'success',message}), info: message => notifications.push({state:'info',message}) }) } : name.endsWith('/system-operation') ? { systemActionSnapshot: value => value } : name.endsWith('/api-request') ? { mutationFailureState: () => 'error' } : name.endsWith('/protocol-icon') ? { ProtocolIcon() {} } : requireActual(name);
  const settingsSource = readFileSync(new URL('../../src/features/assistant/nodera-settings.tsx', import.meta.url), 'utf8');
  const compiledSettings = ts.transpileModule(settingsSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  new Function('require', 'exports', compiledSettings.outputText)(require, settings);
  new Function('require', 'exports', outputText)(require, exported);
  const request = async (path, options) => {
    if (path === '/assistant/mihomo-profiles') return { items: [{id: 'own', name: 'Моя подписка'}] };
    if (path === '/assistant/bot-users') return { items: [] };
    if (path === '/assistant/models') return overrides.models ? overrides.models(options) : { models: catalog };
    if (path.startsWith('/assistant/queue?')) return overrides.queue ? overrides.queue(path) : {state:'running', position:0, waiting:0};
    if (options?.method === 'PUT' || path === '/assistant/chat') writes.push(JSON.parse(options.body));
    if (path === '/assistant/chat') return overrides.chat ? overrides.chat(options) : { result: { answer: 'OK', tools_used: [] } };
    return status;
  };
  const props = { request, busy: false, refreshKey: 0, onDirtyChange() {}, confirmAction: async () => false, admitOperation() {}, onOperation() {} };
  return { writes, notifications, render() {
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

test('saved connection data is visible in AI settings before a manual check and unavailable fields are omitted', async () => {
  const status = fixture(), identity = 'b'.repeat(32);
  status.settings.providers = [{ id: identity, name: 'Fixture API', provider_url: 'https://provider.example/v1', model: '', model_mode: 'catalog', api_key_configured: true }];
  status.settings.active_provider = identity;
  status.provider_observations = { [identity]: { quota: { unlimited: true, remaining: -50, used: 0, expires_at: 0 }, quota_checked_at: 1700000000,
    provider_info: { checked_at: 1700000000, catalog_latency_ms: 123, models_count: 43 } } };
  const view = mount(status);
  view.render(); await Promise.resolve();
  let tree = view.render();
  nodes(tree, n => n.type === 'button' && n.props.children?.[1] === 'Настройки AI')[0].node.props.onClick();
  tree = view.render();
  const text = JSON.stringify(tree);
  assert.match(text, /Без лимита/);
  assert.match(text, /Без срока/);
  assert.match(text, /Использовано/);
  assert.match(text, /моделей 43/);
  assert.doesNotMatch(text, /Аккаунт ·/);
});

test('a pending chat shows queue position and submits the question only once', async () => {
  const status = fixture();
  Object.assign(status.settings, {model:'fixture-model', modules:{'ai-agent':true,'nodera-bot':true}});
  Object.assign(status.modules[0], {enabled:true, configured:true});
  let resolve;
  const view = mount(status, [], {chat: () => new Promise(done => resolve = done), queue: () => ({state:'queued', position:2, waiting:2})});
  view.render(); await Promise.resolve();
  let tree = view.render();
  nodes(tree, n => n.type === 'textarea')[0].node.props.onChange({target:{value:'hello',style:{},scrollHeight:48}});
  tree = view.render();
  nodes(tree, n => n.props['aria-label'] === 'Отправить сообщение')[0].node.props.onClick();
  view.render(); await Promise.resolve(); tree = view.render();
  assert.equal(nodes(tree, n => n.props.role === 'status' && n.props.children === 'Запрос в очереди AI · позиция 2').length, 1);
  assert.match(view.writes[0].request_id, /^[0-9a-f]{32}$/);
  resolve({result:{answer:'finished', tools_used:[]}});
  await new Promise(setImmediate); tree = view.render();
  assert.equal(view.writes.length, 1);
  assert.equal(nodes(tree, n => n.props.role === 'status' && n.props.children === 'Запрос в очереди AI · позиция 2').length, 0);
});

test('all MCP cards show their actual tool counts', async () => {
  const status = fixture();
  status.mcp_modules = [{id:'telegram-mcp', name:'Telegram MCP'}, {id:'vps-controller-mcp', name:'VPS Controller'}, {id:'mihomo-mcp', name:'Mihomo'}];
  status.tools = {'telegram-mcp': Array.from({length:54}, (_,i) => `tg_${i}`), 'vps-controller-mcp': Array.from({length:17}, (_,i) => `vps_${i}`), 'mihomo-mcp': ['a','b','c','d','e']};
  const view = mount(status); view.render(); await Promise.resolve();
  nodes(view.render(), n => n.type === 'button' && n.props.children?.[1] === 'Настройки AI')[0].node.props.onClick();
  const tree = view.render();
  for (const [name, count] of [['Telegram MCP',54],['VPS Controller',17],['Mihomo',5]]) {
    const item = nodes(tree, n => n.props['aria-label'] === `Количество инструментов ${name}`)[0].node;
    assert.equal(item.props.children.includes(count), true);
  }
});

test('catalog failure is visible and keeps chat available with the saved model; checking restores status', async () => {
  const status = fixture();
  Object.assign(status.settings, {model_mode:'catalog', model:'saved-model', api_key_configured:true,
    active_provider:'a'.repeat(32), provider_url:'https://provider.example/v1', modules:{'ai-agent':true,'nodera-bot':true}});
  Object.assign(status.modules[0], {enabled:true, configured:true});
  let requests = 0;
  const view = mount(status, [], {models: async options => {
    assert.equal(JSON.parse(options.body).provider_id, status.settings.active_provider);
    if (++requests === 1) throw new Error('temporary outage');
    return {models:['saved-model','new-model']};
  }});
  view.render(); await Promise.resolve(); view.render(); await new Promise(setImmediate);
  let tree = view.render();
  assert.equal(nodes(tree, n => n.props.role === 'alert' && n.props.children === 'temporary outage').length, 1);
  nodes(tree, n => n.type === 'textarea')[0].node.props.onChange({target:{value:'hello', style:{}, scrollHeight:48}});
  tree = view.render();
  assert.equal(nodes(tree, n => n.props['aria-label'] === 'Отправить сообщение')[0].disabled, false);
  nodes(tree, n => n.type === 'button' && n.props.children === 'Проверить API сейчас')[0].node.props.onClick();
  await new Promise(setImmediate); tree = view.render();
  assert.equal(nodes(tree, n => n.type === 'span' && n.props.children === 'Каталог API доступен').length, 1);
  assert.equal(nodes(tree, n => n.props.role === 'alert' && n.props.children === 'temporary outage').length, 0);
});

test('Telegram exposes all 54 switches per user and saves individual tools with allowed targets', async () => {
  const status = fixture();
  status.settings.telegram_tool_catalog = JSON.parse(readFileSync(new URL('../../protocol-images/ai-agent/telegram_catalog.json', import.meta.url), 'utf8'));
  status.settings.accounts = [{ telegram_id: 11, name: 'Fixture', bot_access: false, bot_chat_access: true,
    telegram_access: true, vps_access: false, session_configured: true, excluded_dialogs: [99] }];
  const view = mount(status);
  view.render(); await Promise.resolve();
  nodes(view.render(), n => n.type === 'button' && n.props.children?.[1] === 'Доступ')[0].node.props.onClick();
  let tree = view.render();
  nodes(tree, n => n.props['aria-label'] === 'Настроить пользователя Fixture')[0].node.props.onClick();
  tree = view.render();
  const tools = nodes(tree, n => n.type === 'label' && n.props.title?.startsWith('tg_'));
  assert.equal(tools.length, 54);
  assert.equal(tools.filter(({node}) => nodes(node, n => n.type === 'input' && n.props.checked).length).length, 5);
  const send = tools.find(({node}) => node.props.title === 'tg_send_message').node;
  nodes(send, n => n.type === 'input')[0].node.props.onChange({target: {checked: true}});
  tree = view.render();
  assert.equal(nodes(nodes(tree, n => n.props.title === 'tg_send_message')[0].node, n => n.type === 'input')[0].node.props.checked, true);
  nodes(tree, n => n.type === 'input' && n.props.placeholder?.startsWith('ID, username'))[0].node.props.onChange({target: {value: '42, @example'}});
  tree = view.render();
  nodes(tree, n => typeof n.type === 'function' && n.props.title === 'Пользователь')[0].node.props.submit();
  tree = view.render();
  nodes(tree, n => n.type === 'button' && n.props.children === 'Сохранить')[0].node.props.onClick();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(view.writes[0].accounts[0].telegram_tools.includes('tg_send_message'), true);
  assert.equal(view.writes[0].accounts[0].telegram_tools.includes('tg_delete_messages'), false);
  assert.deepEqual(view.writes[0].accounts[0].telegram_action_targets, ['42', '@example']);
  assert.deepEqual(view.writes[0].accounts[0].excluded_dialogs, [99]);
});

test('Mihomo and VPS have separate settings cards and saving one scope leaves the other disabled', async () => {
  const status = fixture();
  status.settings.mcp = { 'telegram-mcp': false, 'vps-controller-mcp': false, 'mihomo-mcp': false };
  status.mcp_modules = [
    { id: 'telegram-mcp', name: 'Telegram MCP', enabled: false, configured: true },
    { id: 'vps-controller-mcp', name: 'VPS Controller', enabled: false, configured: true },
    { id: 'mihomo-mcp', name: 'Mihomo', enabled: false, configured: true },
  ];
  const view = mount(status);
  view.render(); await Promise.resolve();
  let tree = view.render();
  nodes(tree, n => n.type === 'button' && n.props.children?.[1] === 'Настройки AI')[0].node.props.onClick();
  tree = view.render();
  nodes(tree, n => n.props['aria-label'] === 'Mihomo · Выключен')[0].node.props.onClick();
  tree = view.render();
  assert.equal(nodes(tree, n => n.props['aria-label'] === 'VPS Controller · Выключен').length, 1);
  assert.equal(nodes(tree, n => n.props['aria-label'] === 'Mihomo · Включён').length, 1);
  nodes(tree, n => n.type === 'button' && n.props.children === 'Сохранить')[0].node.props.onClick();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(view.writes[0].mcp['mihomo-mcp'], true);
  assert.equal(view.writes[0].mcp['vps-controller-mcp'], false);
});

test('AI settings can be edited and submitted while NoderaBot is running', async () => {
  const view = mount(fixture());
  view.render(); await Promise.resolve();
  let tree = view.render();
  assert.equal(nodes(tree, n => n.type === 'h1').length, 0);
  nodes(tree, n => n.type === 'button' && n.props.children?.[1] === 'Настройки AI')[0].node.props.onClick();
  tree = view.render();
  const toggle = nodes(tree, n => n.props.role === 'switch' && n.props['aria-label'] === 'AI')[0];
  assert.equal(toggle.disabled, false);
  toggle.node.props.onClick();
  tree = view.render();
  const save = nodes(tree, n => n.type === 'button' && n.props.children === 'Сохранить')[0];
  assert.equal(save.disabled, false);
  save.node.props.onClick();
  await Promise.resolve();
  assert.equal(view.writes.length, 1);
  assert.equal(view.writes[0].component, 'nodera');
  assert.equal(view.writes[0].modules['ai-agent'], true);
  assert.equal(view.writes[0].bot_token, null);
  assert.ok(view.notifications.some(item => item.state === 'success' && item.message === 'Настройки сохранены'));
});

test('running Telegram bot locks its token and shows the stop action', async () => {
  const view = mount(fixture());
  view.render(); await Promise.resolve();
  nodes(view.render(), n => n.type === 'button' && n.props.children?.[1] === 'Доступ')[0].node.props.onClick();
  const tree = view.render();
  const toggles = nodes(tree, n => n.type === 'input' && n.props.type === 'checkbox');
  assert.equal(toggles.length, 0);
  assert.equal(nodes(tree, n => n.type === 'fieldset' && n.props.className === 'noderaBotToken')[0].disabled, true);
  const botSwitch = nodes(tree, n => n.props.role === 'switch' && n.props['aria-label'] === 'Telegram bot')[0];
  assert.equal(botSwitch.disabled, false);
  assert.equal(botSwitch.node.props['aria-checked'], true);
  assert.equal(nodes(tree, n => n.props.role === 'switch' && n.props['aria-label'] === 'AI').length, 0);
});

test('chat scope lives in settings and subscription binding is saved with address preferences', async () => {
  const status = fixture();
  status.settings.accounts = [{ telegram_id: 11, name: 'User', bot_chat_access: true, bot_access: false,
    telegram_access: false, vps_access: false, excluded_dialogs: [], session_configured: false }];
  const view = mount(status);
  view.render(); await Promise.resolve();
  let tree = view.render();
  assert.equal(nodes(tree, n => n.type === 'select' && n.props['aria-label'] === 'Контур').length, 0);
  const gear = nodes(tree, n => n.props['aria-label'] === 'Настройки')[0].node;
  assert.equal(gear.props.children.includes('Настройки'), false);
  nodes(tree, n => n.type === 'button' && n.props.children?.[1] === 'Настройки чата')[0].node.props.onClick();
  tree = view.render();
  assert.equal(nodes(tree, n => n.type === 'select' && n.props['aria-label'] === 'Контур').length, 1);
  nodes(tree, n => n.type === 'button' && n.props.children?.[1] === 'Доступ')[0].node.props.onClick();
  view.render(); await Promise.resolve();
  tree = view.render();
  nodes(tree, n => n.props['aria-label'] === 'Настроить пользователя User')[0].node.props.onClick();
  tree = view.render();
  nodes(tree, n => n.type === 'select' && n.props['aria-label'] === 'Подписка Mihomo')[0].node.props.onChange({ target: { value: 'own' } });
  tree = view.render();
  nodes(tree, n => n.type === 'select' && n.props['aria-label'] === 'Форма обращения')[0].node.props.onChange({ target: { value: 'male' } });
  tree = view.render();
  nodes(tree, n => n.props.title === 'Пользователь' && typeof n.props.submit === 'function')[0].node.props.submit();
  tree = view.render();
  nodes(tree, n => n.type === 'button' && n.props.children === 'Сохранить')[0].node.props.onClick();
  await Promise.resolve();
  assert.equal(view.writes[0].accounts[0].mihomo_profile_id, 'own');
  assert.equal(view.writes[0].accounts[0].address_gender, 'male');
});

test('catalog mode loads any number of models and sends the chat selection without changing settings', async () => {
  const status = fixture();
  Object.assign(status.settings, { model_mode: 'catalog', model: '', api_key_configured: true,
    provider_url: 'https://api.nyxos.shop/v1', modules: { 'ai-agent': true, 'nodera-bot': true } });
  Object.assign(status.modules[0], { enabled: true, configured: true });
  const models = Array.from({ length: 250 }, (_, i) => `model-${i}`);
  const view = mount(status, models);
  view.render(); await Promise.resolve(); view.render(); await new Promise(setImmediate);
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

test('tools use switches and gears; model and tool count are separate header facts', async () => {
  const status = fixture();
  status.web_tools = true; status.context_tools = true;
  const view = mount(status);
  view.render(); await Promise.resolve();
  nodes(view.render(), n => n.type === 'button' && n.props.children?.[1] === 'Настройки AI')[0].node.props.onClick();
  let tree = view.render();
  assert.equal(nodes(tree, n => n.props.role === 'switch' && n.props['aria-label'] === 'Интернет').length, 1);
  assert.equal(nodes(tree, n => n.props.role === 'switch' && n.props['aria-label'] === 'Персонализация').length, 1);
  assert.equal(nodes(tree, n => n.type === 'dt' && n.props.children === 'Модель по умолчанию').length, 1);
  assert.equal(nodes(tree, n => n.type === 'dt' && n.props.children === 'Инструменты').length, 1);
  nodes(tree, n => n.props['aria-label'] === 'Настроить Интернет')[0].node.props.onClick();
  tree = view.render();
  assert.equal(nodes(tree, n => n.props.title === 'Интернет' && typeof n.type === 'function').length, 1);
});

test('access exposes all five roles, owner permissions remain fixed, and personalisation is wide and grouped', async () => {
  const status = fixture(); status.context_tools = true;
  const view = mount(status);
  view.render(); await Promise.resolve();
  nodes(view.render(), n => n.type === 'button' && n.props.children?.[1] === 'Доступ')[0].node.props.onClick();
  let tree = view.render();
  assert.equal(nodes(tree, n => n.props['aria-label']?.startsWith('Настроить роль ')).length, 0);
  nodes(tree, n => n.type === 'button' && n.props.children?.[1] === 'Роли и права')[0].node.props.onClick();
  tree = view.render();
  nodes(tree, n => n.props['aria-label'] === 'Редактируемая роль')[0].node.props.onChange({target:{value:'owner'}});
  tree = view.render();
  const owner = nodes(tree, n => typeof n.type === 'function' && n.props.title === 'Ролевая модель')[0].node;
  assert.equal(owner.props.wide, true);
  assert.equal(nodes(owner, n => n.type === 'input' && n.props.type === 'checkbox').filter(item => !item.disabled).length, 0);
  nodes(tree, n => n.type === 'button' && n.props.children?.[1] === 'Настройки AI')[0].node.props.onClick();
  tree = view.render();
  nodes(tree, n => n.props['aria-label'] === 'Настроить Персонализация')[0].node.props.onClick();
  tree = view.render();
  const personal = nodes(tree, n => typeof n.type === 'function' && n.props.title === 'Персонализация')[0].node;
  assert.equal(personal.props.wide, true);
  assert.equal(nodes(personal, n => n.type === 'nav' && n.props['aria-label'] === 'Разделы персонализации').length, 1);
  assert.equal(nodes(personal, n => n.type === 'h3' && n.props.children === 'Личная память собеседника').length, 0);
});
