import assert from 'node:assert/strict';
import test from 'node:test';
import { notificationFixture } from './notification-fixture.mjs';
import { notificationTarget, subscribeNotificationTarget } from '../../src/shared/notifications/notification-target.ts';

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
