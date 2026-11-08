import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createTestLoader } from "./vite-test-loader.mjs";
import react from '@vitejs/plugin-react';

const loader = await createTestLoader(import.meta.url, [react()]);
after(() => loader.close());
const { SubscriptionLimitsFields, unlimitedSubscription, subscriptionLimitChange, subscriptionTraffic, periodTraffic, aggregateUsage, usageBytes, usagePeriodLabel } = await loader.ssrLoadModule('/src/features/mihomo/subscription-limits.tsx');
const { ProfileGeneral } = await loader.ssrLoadModule('/src/features/mihomo/profile-general.tsx');
const { profileSnapshots, profileStatsSnapshot } = await loader.ssrLoadModule('/src/features/mihomo/workspace-refresh.ts');
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
      assert.doesNotMatch(html, /type="number"/);
      assert.match(html, /aria-label="Период трафика"/);
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

test('general uses persisted device usage and subscription sum, never transient core or quota totals', () => {
  const usage = used_bytes => ({ used_bytes, stats_available: true, stats_partial: true });
  const stats = { connections: { one: { rx_bytes: 999999999, tx_bytes: 999999999 } }, usage: {
    summary: usage(13312), devices: { phone: usage(4096), laptop: usage(8192), common: usage(1024) }
  } };
  assert.equal(subscriptionTraffic(stats, 'phone').used_bytes, 4096);
  assert.equal(subscriptionTraffic(stats, 'laptop').used_bytes, 8192);
  assert.equal(subscriptionTraffic(stats).used_bytes, Object.values(stats.usage.devices).reduce((sum, row) => sum + row.used_bytes, 0));
  assert.equal(subscriptionTraffic(stats, 'missing'), undefined);
  assert.equal(subscriptionTraffic(undefined), undefined);
  assert.equal(subscriptionTraffic({ connections: stats.connections }), undefined);
  assert.match(render({ traffic: subscriptionTraffic(stats, 'phone') }), /≥ 4 KB/);
  assert.match(render({ traffic: subscriptionTraffic(stats) }), /≥ 13 KB/);
  assert.match(render({ status: { state: 'active', used_bytes: 999999999, expires_at: null } }), />—</);
});

test('all-time traffic remains visible when core observations reset or disappear', () => {
  const amount = used_bytes => ({ used_bytes, stats_available: true, stats_partial: true });
  const usage = { summary: amount(8 * 1024 ** 3), devices: { phone: amount(3 * 1024 ** 3) } };
  for (const observation of [{ rx_bytes: 1000, tx_bytes: 100 }, { rx_bytes: 0, tx_bytes: 0 }, { stats_available: false }]) {
    const stats = { usage, summary: observation, connections: { one: observation } };
    assert.equal(usageBytes(subscriptionTraffic(stats)), '≥ 8.0 GB');
    assert.equal(usageBytes(subscriptionTraffic(stats, 'phone')), '≥ 3.0 GB');
    assert.equal(aggregateUsage([subscriptionTraffic(stats)]).used_bytes, 8 * 1024 ** 3);
  }
  assert.equal(usageBytes(subscriptionTraffic({ summary: { rx_bytes: 999999, tx_bytes: 999999 } })), '—');
});

test('usage aggregation preserves partial and missing measurements without inventing zero', () => {
  const amount = used_bytes => ({ used_bytes, stats_available: true, stats_partial: false });
  assert.deepEqual(aggregateUsage([amount(100), amount(200)]), amount(300));
  assert.deepEqual(aggregateUsage([amount(100), undefined]), { ...amount(100), stats_partial: true });
  assert.equal(usageBytes(aggregateUsage([undefined, { used_bytes: null, stats_available: false, stats_partial: false }])), '—');
  assert.equal(usageBytes(aggregateUsage([amount(0)])), '0 B');
  assert.equal(usageBytes(aggregateUsage([])), '0 B');
});

test('lifetime period identifies the server accounting start in UTC',()=>{
  assert.match(usagePeriodLabel({history_started_at:Date.UTC(2026,10,2,23)/1000}),/02\.11\.2026 UTC/);
  assert.doesNotMatch(usagePeriodLabel(undefined),/Invalid Date|1970/);
});

test('traffic periods select their own totals and never substitute all-time or another device', () => {
  const amount = used_bytes => ({ used_bytes, stats_available: true, stats_partial: true });
  const window = used_bytes => ({ ...amount(used_bytes), started_at: 0, ended_at: 100 });
  const device = { ...amount(1000), history_started_at: 50, periods: { day: window(10), week: window(20), month: window(30) } };
  const stats = { usage: { summary: amount(9000), devices: { phone: device } } };
  for (const [period, bytes] of [['day', 10], ['week', 20], ['month', 30], ['all', 1000]]) {
    assert.equal(periodTraffic(subscriptionTraffic(stats, 'phone'), period).used_bytes, bytes);
  }
  assert.equal(periodTraffic(amount(9000), 'week'), undefined);
  assert.equal(periodTraffic(subscriptionTraffic(stats, 'missing'), 'day'), undefined);
  const html = render({ traffic: device });
  assert.match(html, /aria-label="Период трафика"/);
  for (const label of ['За день', 'За 7 дней', 'За 30 дней', 'За всё время']) assert.ok(html.includes(label));
});

test('traffic history snapshots reject malformed counters and reversed or missing windows', () => {
  const amount = { used_bytes: 1000, stats_available: true, stats_partial: true };
  const window = { ...amount, started_at: 0, ended_at: 100 };
  const row = { ...amount, history_started_at: 50, periods: { day: window, week: window, month: window } };
  const snapshot = usage => ({ summary: { configured: 0, active: 0 }, connections: {}, usage: { summary: usage, devices: { phone: usage } } });
  assert.deepEqual(profileStatsSnapshot(snapshot(row)), snapshot(row));
  assert.deepEqual(profileStatsSnapshot(snapshot(amount)), snapshot(amount));
  for (const invalid of [
    { ...row, history_started_at: -1 },
    { ...row, history_started_at: 'yesterday' },
    { ...row, periods: { day: window, week: window } },
    { ...row, periods: { ...row.periods, day: { ...window, started_at: 101 } } },
    { ...row, periods: { ...row.periods, week: { ...window, used_bytes: -1 } } },
    { ...row, periods: { ...row.periods, month: { ...window, used_bytes: null } } },
  ]) assert.throws(() => profileStatsSnapshot(snapshot(invalid)));
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
