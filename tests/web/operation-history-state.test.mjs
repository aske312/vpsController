import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { createTestLoader } from './vite-test-loader.mjs';
const loader = await createTestLoader(import.meta.url);
after(() => loader.close());
const { historyEntries, historyErrorCount, historyMatches, historyState } = await loader.ssrLoadModule('/src/control-panel/components/operation-history-state.ts');

test('operation history keeps unconfirmed and failed worker results in attention', () => {
  for (const state of ['succeeded', 'finished', 'done']) {
    for (const result of ['unknown', 'exit-code', 'timeout']) {
      const entry = historyEntries({ items: [{ id: 'operation', state, result }] })[0];
      assert.equal(historyState(entry).tone, result === 'unknown' ? 'unknown' : 'error');
      assert.equal(historyMatches(entry, 'attention', ''), true);
      assert.equal(historyMatches(entry, 'completed', ''), false);
    }
    for (const result of [undefined, 'success', 'passed']) {
      const entry = { id: 'operation', state, result };
      assert.equal(historyState(entry).tone, 'success');
      assert.equal(historyMatches(entry, 'completed', ''), true);
    }
  }
  assert.equal(historyState({ state: 'cancelled', result: 'signal' }).tone, 'neutral');
  assert.equal(historyMatches({ state: 'running' }, 'active', ''), true);
  assert.equal(historyState({state:'running',result:'failed'}).tone,'active');
  assert.equal(historyState({state:'failed',result:'passed'}).tone,'error');
  assert.equal(historyState({state:'succeeded',result:'unexpected-new-code'}).tone,'unknown');
  assert.equal(historyState({state:'succeeded',result:'skipped'}).tone,'neutral');
  assert.equal(historyState({state:'unknown',result:'timeout'}).tone,'unknown');
});

test('invalid result payload cannot be rendered as successful history', () => {
  for (const result of [{}, [], 1, true]) assert.throws(() => historyEntries({ items: [{ id: 'operation', state: 'succeeded', result }] }));
});

test('error count excludes resolved, cancelled, unknown and active operations', () => {
  const items = historyEntries({ items: [
    { id: 'failed', state: 'failed' },
    { id: 'exit-code', state: 'finished', result: 'exit-code' },
    { id: 'resolved', state: 'failed', resolved_at: '2026-11-19T10:00:00Z' },
    { id: 'active', state: 'running', result: 'failed' },
    { id: 'unknown', state: 'unknown', result: 'timeout' },
    { id: 'cancelled', state: 'cancelled', result: 'signal' },
    { id: 'success', state: 'succeeded' },
  ] });
  assert.equal(historyErrorCount(items), 2);
  const resolved = items[2];
  assert.equal(historyState(resolved).label, 'Ошибка · решено');
  assert.equal(resolved.state, 'failed');
  assert.equal(historyMatches(resolved, 'attention', ''), false);
  assert.equal(historyMatches(resolved, 'resolved', ''), true);
  assert.equal(historyMatches(resolved, 'completed', ''), true);
  assert.equal(historyMatches(items[0], 'resolved', ''), false);
  assert.equal(historyErrorCount([{ ...resolved, resolved_at: null }]), 1);
  assert.throws(() => historyEntries({ items: [{ id: 'invalid', resolved_at: true }] }));
});
