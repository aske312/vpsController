import assert from 'node:assert/strict';
import test from 'node:test';
import { latestObservation, quotaFacts, quotaText, queueProviderCatalog } from '../../src/features/assistant/provider-details.ts';

test('unavailable fields stay unknown while confirmed zeros and unlimited values retain their meaning', () => {
  assert.equal(quotaText(undefined), '—');
  assert.equal(quotaText({ used: 5 }), '—');
  assert.equal(quotaText({ remaining: 0 }), '0');
  assert.equal(quotaText({ unlimited: true, remaining: -71698 }), 'Без лимита');
  assert.deepEqual(quotaFacts({ quota: { used: 0 } }), [{ label: 'Использовано', value: '0' }]);
  assert.equal(quotaFacts({ quota: { unlimited: true, granted: 0 } }).length, 0);
});

test('expiry and model limits are displayed only when the provider supplied them', () => {
  assert.deepEqual(quotaFacts({ quota: {} }), []);
  const facts = quotaFacts({ quota: { expires_at: 0, model_limits_enabled: true, allowed_models_count: 2, allowed_models: ['one', 'two'] } });
  assert.equal(facts.find(row => row.label === 'Ключ').value, 'Без срока');
  assert.equal(facts.find(row => row.label === 'Лимит моделей').title, 'one, two');
  assert.match(quotaFacts({ quota: { expires_at: 1700000000 } }, 1800000000000)[0].value, /^Истёк /);
});

test('a fresh catalog does not make an old quota fresh; explicit unavailability supersedes old quota', () => {
  const old = { quota: { remaining: 50 }, quota_checked_at: 10, provider_info: { checked_at: 10 } };
  const catalog = { provider_info: { checked_at: 20 } };
  assert.equal(latestObservation(old, catalog).quota_checked_at, 10);
  assert.equal(latestObservation(old, catalog).provider_info.checked_at, 20);
  assert.equal(latestObservation(old, { quota: null, quota_checked_at: 30 }).quota, null);
});

test('connection discovery shares identical reads and serializes different providers', async () => {
  let finish; const calls = [];
  const first = queueProviderCatalog('queue-one', () => { calls.push('one'); return new Promise(resolve => finish = resolve); });
  const shared = queueProviderCatalog('queue-one', () => { throw new Error('duplicate request'); });
  const second = queueProviderCatalog('queue-two', async () => { calls.push('two'); return { models: ['two'] }; });
  assert.equal(first, shared);
  await Promise.resolve();
  assert.deepEqual(calls, ['one']);
  finish({ models: ['one'] });
  await Promise.all([first, second]);
  assert.deepEqual(calls, ['one', 'two']);
});

test('aborting an obsolete request releases the discovery queue even if transport ignores abort', async () => {
  const controller = new AbortController();
  const first = queueProviderCatalog('aborted', () => new Promise(() => {}), controller.signal);
  const rejected = assert.rejects(first);
  await Promise.resolve(); controller.abort();
  await rejected;
  const next = await queueProviderCatalog('after-abort', async () => ({ models: ['working'] }));
  assert.deepEqual(next.models, ['working']);
});
