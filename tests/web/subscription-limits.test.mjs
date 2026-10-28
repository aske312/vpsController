import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createTestLoader } from "./vite-test-loader.mjs";
import react from '@vitejs/plugin-react';

const loader = await createTestLoader(import.meta.url, [react()]);
after(() => loader.close());
const { SubscriptionLimitsFields, unlimitedSubscription } = await loader.ssrLoadModule('/src/features/mihomo/subscription-limits.tsx');
const { profileSnapshots } = await loader.ssrLoadModule('/src/features/mihomo/workspace-refresh.ts');
const render = props => renderToStaticMarkup(createElement(SubscriptionLimitsFields, { value: unlimitedSubscription, disabled: false, onChange() {}, ...props }));

test('each subscription control starts with an explicit Unlimit choice', () => {
  const html = render({});
  assert.equal((html.match(/value="unlimit" selected=""/g) || []).length, 3);
  assert.doesNotMatch(html, /type="number"/);
  assert.match(html, /всех протоколов и устройств/);
});

test('finite limits show units and preserve empty/invalid input validation', () => {
  const html = render({ value: { speed_mbps: 50, traffic_gb: 100, days: 30 }, disabled: true });
  assert.equal((html.match(/type="number"/g) || []).length, 3);
  assert.equal((html.match(/value="limited" selected=""/g) || []).length, 3);
  assert.match(html, /Скорость, Мбит\/с/);
  assert.match(html, /Общий трафик, GB/);
  assert.match(html, /Дни использования, дней/);
  assert.match(html, /required="" disabled=""/);
});

test('exhaustion and unknown enforcement are visible instead of appearing active', () => {
  const status = { state: 'exhausted', enforced: true, used_bytes: 1000000000, started_at: null, expires_at: null, checked_at: 1000 };
  assert.match(render({ status }), /Объём трафика исчерпан/);
  assert.match(render({ status: { ...status, state: 'unknown', enforced: false } }), /не подтверждена/);
  assert.match(render({ status: { ...status, used_bytes: 0, state: 'active', enforced: false } }), /Учёт начнётся/);
});

test('profile snapshots reject malformed policy values and usage observations', () => {
  const profile = { id: 'one', name: 'Profile', connections: [], limits: unlimitedSubscription };
  assert.deepEqual(profileSnapshots({ items: [profile] }), [profile]);
  for (const limits of [{ speed_mbps: 0, traffic_gb: null, days: null }, { ...unlimitedSubscription, days: true }, { ...unlimitedSubscription, speed_mbps: '50' }]) {
    assert.throws(() => profileSnapshots({ items: [{ ...profile, limits }] }));
  }
  assert.throws(() => profileSnapshots({ items: [{ ...profile, limits_status: { state: 'active', enforced: true, used_bytes: -1 } }] }));
});
