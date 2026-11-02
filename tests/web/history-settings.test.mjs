import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import * as stateHelpers from '../../src/features/services/history-settings-state.ts';

// Execute the real hook with explicit renders/commits and controllable I/O.
// No DOM is needed to exercise its network ordering and draft ownership.
function mount(request) {
  const slots = [], effects = [];
  let cursor = 0;
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ||= { current: initial }; },
    useCallback(fn, deps) {
      const index = cursor++;
      if (!same(slots[index]?.deps, deps)) slots[index] = { deps, fn };
      return slots[index].fn;
    },
    useEffect(fn, deps) {
      const index = cursor++;
      if (!same(slots[index]?.deps, deps)) {
        effects.push(() => { slots[index]?.cleanup?.(); slots[index] = { deps, cleanup: fn() }; });
      }
    },
  };
  const source = readFileSync(new URL('../../src/features/services/operation-history-settings.tsx', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  const exported = {}, actualRequire = createRequire(import.meta.url);
  const require = name => name === 'react' ? hooks : name === './history-settings-state' ? stateHelpers : actualRequire(name);
  new Function('require', 'exports', outputText)(require, exported);
  return () => {
    cursor = 0;
    const result = exported.useOperationHistorySettings(request, true);
    for (const effect of effects.splice(0)) effect();
    return result;
  };
}
const policy = (revision, days = 30) => ({ revision, retention_days: days, disk_limit_mb: 64, log_retention_days: 30, log_disk_limit_mb: 128, sources: {} });
const flush = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

test('refresh updates a clean history form and preserves explicit edits with their original revision', async () => {
  let next = policy('first');
  const render = mount(async () => next);
  render(); await flush();
  let view = render();
  next = policy('second', 60);
  await view.load(); view = render();
  assert.equal(view.draft.revision, 'second'); assert.equal(view.draft.retention_days, 60);
  view.change({ retention_days: 45 }); view = render();
  next = policy('third', 90);
  await view.load(); view = render();
  assert.equal(view.saved.revision, 'third'); assert.equal(view.draft.revision, 'second');
  assert.equal(view.draft.retention_days, 45);
  let confirmed = false;
  await view.save(async () => { confirmed = true; return true; });
  assert.equal(confirmed, false);
});

test('an older refresh cannot overwrite saving state or the confirmed result', async () => {
  const oldRead = deferred(), write = deferred(); let reads = 0, writes = 0;
  const render = mount(async (_path, init) => {
    if (init?.method) { writes++; return write.promise; }
    return ++reads === 1 ? policy('first') : oldRead.promise;
  });
  render(); await flush(); let view = render();
  view.change({ retention_days: 60 }); view = render();
  const reading = view.load();
  const saving = view.save(async () => true);
  oldRead.resolve(policy('old-read', 20)); await reading;
  view = render(); assert.equal(view.saving, true); assert.equal(view.saved.revision, 'first');
  await view.save(async () => true); assert.equal(writes, 1);
  write.resolve(policy('committed', 60)); await saving;
  view = render(); assert.equal(view.saving, false); assert.equal(view.draft.revision, 'committed');
});

test('confirmation reserves one save and cancellation releases the form without a write', async () => {
  const confirmation = deferred(); let confirmations = 0, writes = 0;
  const render = mount(async (_path, init) => { if (init?.method) writes++; return policy('first'); });
  render(); await flush(); let view = render();
  view.change({ retention_days: 10 }); view = render();
  const confirm = async () => { confirmations++; return confirmation.promise; };
  const saving = view.save(confirm);
  await view.save(confirm);
  assert.equal(confirmations, 1); assert.equal(render().saving, true);
  confirmation.resolve(false); await saving;
  view = render(); assert.equal(view.saving, false); assert.equal(writes, 0); assert.equal(view.draft.retention_days, 10);
});

test('a lost response reads back the actual policy without replaying PUT or dropping the draft', async () => {
  let reads = 0, writes = 0;
  const render = mount(async (_path, init) => {
    if (init?.method) { writes++; throw new Error('connection lost'); }
    return policy(++reads === 1 ? 'first' : 'committed', reads === 1 ? 30 : 60);
  });
  render(); await flush(); let view = render(); view.change({ retention_days: 60 }); view = render();
  await view.save(async () => true); view = render();
  assert.equal(writes, 1); assert.equal(reads, 2); assert.equal(view.saved.revision, 'committed');
  assert.equal(view.draft.retention_days, 60); assert.equal(view.draft.revision, 'first');
  assert.match(view.error, /connection lost/); assert.equal(view.saving, false);
});
