import assert from 'node:assert/strict';
import test from 'node:test';
import { ProviderMonitor } from '../../src/features/assistant/provider-connection.ts';

function setup(fetch, canPoll = () => true) {
  const timers = new Map(), observed = []; let next = 0;
  const monitor = new ProviderMonitor({ fetch, publish: value => observed.push(value), automatic: true, canPoll,
    now: () => 100000, schedule: (fn, delay) => { const id = ++next; timers.set(id, { fn, delay }); return id; },
    cancel: id => timers.delete(id) });
  return { monitor, timers, observed, async tick(delay) {
    const entry = [...timers].find(([, value]) => value.delay === delay);
    assert.ok(entry, `No scheduled ${delay}ms check`); timers.delete(entry[0]); entry[1].fn();
    await new Promise(setImmediate);
  } };
}

test('an initial outage recovers automatically, then checks continue every minute', async () => {
  let requests = 0;
  const state = setup(async () => { if (++requests === 1) throw new Error('temporary outage'); return {models: ['model']}; });
  await state.monitor.refresh();
  assert.equal(state.monitor.snapshot.state, 'error');
  await state.tick(15000);
  assert.equal(state.monitor.snapshot.state, 'available');
  assert.deepEqual(state.monitor.snapshot.models, ['model']);
  await state.tick(60000);
  assert.equal(requests, 3);
  state.monitor.stop(); assert.equal(state.timers.size, 0);
});

test('a failed refresh retains the last valid catalog and concurrent checks share one request', async () => {
  let resolve, requests = 0;
  const state = setup(async () => { ++requests; if (requests === 1) return {models: ['saved']}; return new Promise(done => resolve = done); });
  await state.monitor.refresh();
  const first = state.monitor.refresh(), second = state.monitor.refresh();
  await Promise.resolve();
  assert.equal(first, second); assert.equal(requests, 2);
  resolve({models: []}); await first;
  assert.equal(state.monitor.snapshot.state, 'error');
  assert.deepEqual(state.monitor.snapshot.models, ['saved']);
  state.monitor.stop();
});

test('timeout aborts discovery, exposes an error and schedules recovery', async () => {
  let signal;
  const state = setup(value => { signal = value; return new Promise(() => {}); });
  const pending = state.monitor.refresh(); await state.tick(20000); await pending;
  assert.equal(signal.aborted, true); assert.equal(state.monitor.snapshot.state, 'error');
  assert.ok([...state.timers.values()].some(timer => timer.delay === 15000));
  state.monitor.stop();
});

test('stopping a provider cancels timers and ignores late responses from old credentials', async () => {
  let resolve, signal;
  const state = setup(value => { signal = value; return new Promise(done => resolve = done); });
  const pending = state.monitor.refresh(); state.monitor.stop();
  await Promise.resolve();
  resolve({models: ['foreign-model']}); await pending;
  assert.equal(signal.aborted, true); assert.equal(state.timers.size, 0);
  assert.deepEqual(state.monitor.snapshot.models, []);
  assert.equal(state.observed.some(value => value.state === 'available'), false);
});

test('synchronous transport failures cannot leave discovery permanently stuck', async () => {
  let calls = 0;
  const state = setup(() => { if (++calls === 1) throw new Error('transport failed'); return Promise.resolve({models:['recovered']}); });
  await state.monitor.refresh(); await state.tick(15000);
  assert.equal(state.monitor.snapshot.state, 'available');
  state.monitor.stop();
});

test('hidden or busy views postpone checks without claiming the provider is down', async () => {
  let active = false, requests = 0;
  const state = setup(async () => { requests++; return {models: ['model']}; }, () => active);
  await state.monitor.refresh(); assert.equal(requests, 0); assert.equal(state.monitor.snapshot.state, 'unchecked');
  active = true; await state.tick(15000); assert.equal(requests, 1);
  state.monitor.stop();
});
