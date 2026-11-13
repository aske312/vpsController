import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { createTestLoader } from './vite-test-loader.mjs';
const loader = await createTestLoader(import.meta.url);
after(() => loader.close());
const { historyEntries, historyMatches, historyState } = await loader.ssrLoadModule('/src/control-panel/components/operation-history-state.ts');

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
