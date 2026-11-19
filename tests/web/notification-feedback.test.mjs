import assert from 'node:assert/strict';
import test from 'node:test';
import { notificationFixture } from './notification-fixture.mjs';
import { notificationTarget, subscribeNotificationTarget } from '../../src/shared/notifications/notification-target.ts';
import { createNotificationStore } from '../../src/shared/notifications/store.ts';

test('screen and modal publishers share one recovery notice and retain it until all owners recover', () => {
  const store = createNotificationStore(), page = Symbol('page'), dialog = Symbol('dialog');
  const recovery = { id: 'notice:mihomo:recovery', source: 'mihomo', title: 'Восстановление Mihomo', message: 'Снимок сохранён', state: 'unknown', kind: 'message', action: { label: 'Восстановить Mihomo', run() {} } };
  store.setNotice(page, recovery);
  store.setNotice(dialog, recovery);
  assert.equal(store.getSnapshot().length, 1);
  store.dismiss(recovery.id);
  assert.equal(store.getSnapshot().length, 1, 'an unresolved recovery remains accessible');
  store.setNotice(dialog, null);
  assert.equal(store.getSnapshot().length, 1);
  store.setNotice(page, null);
  assert.equal(store.getSnapshot().length, 0);
});

test('notice action availability updates subscribers without reopening a dismissed warning', () => {
  const store = createNotificationStore(), owner = Symbol('profile');
  let updates = 0;
  store.subscribe(() => updates++);
  const notice = { id: 'notice:profile-conflict', source: 'mihomo', title: 'Профиль', message: 'Ваш ввод сохранён', state: 'error', kind: 'message', action: { label: 'Загрузить', disabled: true, run() {} } };
  store.setNotice(owner, notice);
  store.setNotice(owner, { ...notice, action: { ...notice.action, disabled: false } });
  assert.equal(updates, 2);
  assert.equal(store.getSnapshot()[0].action.disabled, false);
  store.dismiss(notice.id);
  store.setNotice(owner, notice);
  assert.equal(store.getSnapshot().length, 0);
  store.setNotice(owner, null);
  store.setNotice(owner, notice);
  assert.equal(store.getSnapshot().length, 1, 'a new conflict is visible after recovery');
});

test('declarative screen feedback renders no inline markup and cleans up on unmount', () => {
  const effects = [], slots = [];
  let cursor = 0;
  const hooks = {
    useRef(value) { const index = cursor++; return slots[index] ??= { current: value }; },
    useEffect(run) { effects.push(run); },
  };
  const { api, store } = notificationFixture(hooks);
  let recovered = 0;
  const markup = api.NotificationNotice({ id: 'mihomo:recovery', title: 'Восстановление Mihomo', state: 'unknown', message: 'Изменение прервано', action: { label: 'Восстановить Mihomo', run: () => recovered++ } });
  assert.equal(markup, null);
  const cleanups = effects.map(run => run());
  assert.equal(recovered, 0, 'publishing a warning never executes recovery');
  assert.equal(store.getSnapshot()[0].action.label, 'Восстановить Mihomo');
  store.getSnapshot()[0].action.run();
  assert.equal(recovered, 1);
  cleanups.forEach(cleanup => cleanup?.());
  assert.equal(store.getSnapshot().length, 0);
});

test('recovery notification renders both actions and preserves disabled guards', async () => {
  const { api } = notificationFixture({ useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}] });
  let recovered = 0, checked = 0;
  const item = { id: 'notice:relay:recovery', source: 'relay', title: 'Relay', message: 'Результат не подтверждён', state: 'unknown', kind: 'message', count: 1,
    action: { label: 'Проверить результат', disabled: true, run: () => checked++ },
    secondaryAction: { label: 'Восстановить пересылку', run: () => recovered++ } };
  const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : tree?.props ? [tree, ...nodes(tree.props.children)] : [];
  const tree = api.renderCard({ item, now: 0 });
  const buttons = nodes(tree).filter(node => node.type === 'button');
  const check = buttons.find(node => node.props.children === item.action.label);
  const recover = buttons.find(node => node.props.children === item.secondaryAction.label);
  assert.equal(check.props.disabled, true);
  check.props.onClick(); recover.props.onClick(); await Promise.resolve();
  assert.equal(checked, 0);
  assert.equal(recovered, 1);
  assert.ok(nodes(tree).some(node => node.props.className === 'gateOperationActions'), 'actions have a separate row instead of squeezing the notification text');
});

test('read errors retain the latest retry, recover independently and clean up on unmount', async () => {
  let cursor = 0;
  const slots = [], pending = [];
  const hooks = { useEffect(run, deps) {
    const index = cursor++;
    if (!slots[index] || deps.some((value, i) => value !== slots[index].deps[i])) {
      pending.push(() => { slots[index]?.cleanup?.(); slots[index] = { deps, cleanup: run() }; });
    }
  } };
  const { api, store } = notificationFixture(hooks);
  let retried = '';
  const render = (message, retry) => {
    cursor = 0;
    api.usePageError('network', 'Сеть', message, { label: 'Повторить', run: retry });
    api.usePageError('history', 'История', 'History unavailable');
    pending.splice(0).forEach(commit => commit());
  };
  render('Network unavailable', () => { retried = 'old'; });
  render('Network unavailable', () => { retried = 'current'; });
  assert.equal(store.getSnapshot().length, 2);
  assert.equal(store.getSnapshot()[0].count, 1);
  await store.getSnapshot()[0].action.run();
  assert.equal(retried, 'current');
  render('', () => {});
  assert.deepEqual(store.getSnapshot().map(item => item.source), ['history']);
  slots.forEach(slot => slot.cleanup?.());
  assert.equal(store.getSnapshot().length, 0);
});

test('notifications follow native modal dialogs and return to the page when closed', () => {
  const originalDocument = globalThis.document, originalObserver = globalThis.MutationObserver;
  const body = {}, modal = {};
  let dialogs = [], changed, disconnected = false, updates = 0;
  globalThis.document = { body, querySelectorAll(selector) {
    assert.equal(selector, 'dialog:modal');
    return { length: dialogs.length, item: index => dialogs[index] ?? null };
  } };
  globalThis.MutationObserver = class {
    constructor(callback) { changed = callback; }
    observe(target, options) { assert.equal(target, body); assert.ok(options.subtree && options.childList && options.attributeFilter.includes('open')); }
    disconnect() { disconnected = true; }
  };
  try {
    const unsubscribe = subscribeNotificationTarget(() => updates++);
    assert.equal(notificationTarget(), body);
    dialogs = [modal]; changed();
    assert.equal(notificationTarget(), modal);
    dialogs = []; changed();
    assert.equal(notificationTarget(), body);
    assert.equal(updates, 2);
    unsubscribe(); assert.equal(disconnected, true);
  } finally {
    if (originalDocument === undefined) delete globalThis.document; else globalThis.document = originalDocument;
    if (originalObserver === undefined) delete globalThis.MutationObserver; else globalThis.MutationObserver = originalObserver;
  }
});
