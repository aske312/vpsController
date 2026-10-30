import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createTestLoader } from "./vite-test-loader.mjs";
import react from '@vitejs/plugin-react';

const loader = await createTestLoader(import.meta.url, [react()]);
after(() => loader.close());
const { SubscriptionLimitsFields, unlimitedSubscription, subscriptionLimitChange, subscriptionTraffic } = await loader.ssrLoadModule('/src/features/mihomo/subscription-limits.tsx');
const { ProfileGeneral } = await loader.ssrLoadModule('/src/features/mihomo/profile-general.tsx');
const { profileSnapshots } = await loader.ssrLoadModule('/src/features/mihomo/workspace-refresh.ts');
const render = props => renderToStaticMarkup(createElement(SubscriptionLimitsFields, { value: unlimitedSubscription, disabled: false, onChange() {}, ...props }));

test('general settings isolate subscription editing from device identity and preserve shared limit warnings', () => {
  const status = { state: 'exhausted', enforced: true, used_bytes: 1000000000, started_at: null, expires_at: null, checked_at: 1000 };
  for (const scope of ['common', 'hwid', 'manual']) {
    const changed = [];
    const props = {
      device: { id: 'device', scope, name: 'Laptop', client_name: 'Koala Clash', client_version: '1.0', os: 'windows', os_version: '11', hwid_hash: 'private-hwid-hash', user_agent: 'private-user-agent' },
      profileName: 'Family', limits: { speed_mbps: 50, traffic_gb: 100, days: 30 }, status, disabled: false,
      onProfileNameChange: value => changed.push(['subscription', value]), onDeviceNameChange: value => changed.push(['device', value]), onLimitsChange() {}, onOpenSubscription() {},
    };
    const html = renderToStaticMarkup(createElement(ProfileGeneral, props));
    assert.match(html, /Объём трафика исчерпан/);
    assert.doesNotMatch(html, /private-hwid-hash|private-user-agent/);
    if (scope === 'common') {
      assert.equal((html.match(/type="number"/g) || []).length, 3);
      assert.match(html, /value="Family"/);
    } else {
      assert.doesNotMatch(html, /<select\b|type="number"/);
      assert.match(html, /value="Laptop"/);
      assert.match(html, /Koala Clash 1.0/);
      assert.match(html, /50 Мбит\/с/);
      assert.match(html, scope === 'manual' ? /Без HWID/ : />HWID</);
    }
    const findNameInput = node => {
      if (!node || typeof node !== 'object') return undefined;
      if (node.type === 'input' && node.props.type !== 'number') return node;
      for (const child of [node.props?.children].flat(Infinity)) {
        const match = findNameInput(child);
        if (match) return match;
      }
    };
    findNameInput(ProfileGeneral(props)).props.onChange({ target: { value: 'Renamed' } });
    assert.deepEqual(changed, [[scope === 'common' ? 'subscription' : 'device', 'Renamed']]);
  }
});

test('zero is displayed for unlimited limits and maps to the existing null wire contract', () => {
  const html = render({});
  assert.equal((html.match(/<input[^>]*type="number"[^>]*value="0"/g) || []).length, 3);
  const original = { speed_mbps: 50, traffic_gb: 100, days: 30 };
  for (const key of Object.keys(original)) {
    assert.deepEqual(subscriptionLimitChange(original, key, 0), { ...original, [key]: null });
    assert.deepEqual(subscriptionLimitChange(original, key, 17), { ...original, [key]: 17 });
  }
  assert.deepEqual(original, { speed_mbps: 50, traffic_gb: 100, days: 30 });
});

test('finite limits show units and preserve empty/invalid input validation', () => {
  const html = render({ value: { speed_mbps: 50, traffic_gb: 100, days: 30 }, disabled: true });
  assert.equal((html.match(/type="number"/g) || []).length, 3);
  for (const [value, max] of [[50, 100000], [100, 1000000], [30, 36500]]) {
    assert.match(html, new RegExp(`<input[^>]*min="0"[^>]*max="${max}"[^>]*step="1"[^>]*required=""[^>]*value="${value}"`));
  }
  assert.match(html, /required="" disabled=""/);
});

test('limit suggestions include zero and allow an arbitrary custom number', () => {
  const html = render({ value: { speed_mbps: 137, traffic_gb: 423, days: 19 } });
  const lists = [...html.matchAll(/<datalist id="([^"]+)">([\s\S]*?)<\/datalist>/g)];
  assert.equal(lists.length, 3);
  for (const [, id, options] of lists) {
    assert.match(options, /value="0"/);
    assert.ok((options.match(/<option/g) || []).length > 3);
    assert.ok(html.includes(`list="${id}"`));
  }
  for (const number of [137, 423, 19]) assert.ok(html.includes(`value="${number}"`));
});

test('usage sums each saved connection once and keeps device ownership and missing counters separate', () => {
  const connections = [{ id: 'one', device_id: 'phone' }, { id: 'two', device_id: 'laptop' }, { id: 'three', device_id: 'phone' }, { id: 'pool', device_id: 'common' }];
  const stats = { connections: { one: { rx_bytes: 1024, tx_bytes: 2048 }, two: { rx_bytes: 8192, tx_bytes: 0 }, three: { rx_bytes: 1024, tx_bytes: 0 }, pool: { rx_bytes: 1024, tx_bytes: 0 } } };
  const phone = subscriptionTraffic(stats, connections, 'phone');
  const laptop = subscriptionTraffic(stats, connections, 'laptop');
  const common = subscriptionTraffic(stats, connections, 'common');
  const all = subscriptionTraffic(stats, connections);
  assert.equal(phone.rx_bytes + phone.tx_bytes, 4096);
  assert.equal(laptop.rx_bytes + laptop.tx_bytes, 8192);
  for (const direction of ['rx_bytes', 'tx_bytes']) assert.equal(all[direction], phone[direction] + laptop[direction] + common[direction]);
  assert.equal(subscriptionTraffic(undefined, connections), undefined);
  const partial = subscriptionTraffic({ connections: { one: stats.connections.one } }, connections, 'phone');
  assert.equal(partial.stats_partial, true);
  assert.match(render({ traffic: partial }), /≥ 3 KB/);
  assert.match(render({ traffic: all }), />13 KB</);
  assert.match(render({ traffic: phone }), />4 KB</);
  const unknown = subscriptionTraffic({ connections: {} }, connections, 'phone');
  assert.equal(unknown.stats_available, false);
  assert.match(render({ traffic: unknown, status: { state: 'active', used_bytes: 999999999, expires_at: null } }), />—</);
});

test('exhaustion and unknown enforcement are visible instead of appearing active', () => {
  const status = { state: 'exhausted', enforced: true, used_bytes: 1000000000, started_at: null, expires_at: null, checked_at: 1000 };
  assert.match(render({ status }), /Объём трафика исчерпан/);
  assert.match(render({ status: { ...status, state: 'unknown', enforced: false } }), /не подтверждена/);
  assert.match(render({ status: { ...status, used_bytes: 0, state: 'active', enforced: false } }), />—</);
});

test('profile snapshots reject malformed policy values and usage observations', () => {
  const profile = { id: 'one', name: 'Profile', connections: [], limits: unlimitedSubscription };
  assert.deepEqual(profileSnapshots({ items: [profile] }), [profile]);
  for (const limits of [{ speed_mbps: 0, traffic_gb: null, days: null }, { ...unlimitedSubscription, days: true }, { ...unlimitedSubscription, speed_mbps: '50' }]) {
    assert.throws(() => profileSnapshots({ items: [{ ...profile, limits }] }));
  }
  assert.throws(() => profileSnapshots({ items: [{ ...profile, limits_status: { state: 'active', enforced: true, used_bytes: -1 } }] }));
});
