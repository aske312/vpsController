import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import reactPlugin from '@vitejs/plugin-react';

const loader = await createServer({ configFile: false, cacheDir: '.runtime/vite-relay-route-picker-tests', plugins: [reactPlugin()], server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
after(() => loader.close());
const { RelayRoutePicker, RelayConsumerPicker } = await loader.ssrLoadModule('/src/features/network/relay-route-picker.tsx');
const route = { id: 'tcp', transport: 'tcp', listen_port: 20000, target_port: 443, state: 'listening', sessions: 0 };
const host = { state: 'connected', routes: [route, { ...route, id: 'udp', transport: 'udp', sessions: 7, state: 'failed' }] };

test('route choices distinguish shared ports by transport and keep zero sessions visible', () => {
  const html = renderToStaticMarkup(createElement(RelayRoutePicker, { host, selected: 'tcp', onChange() {} }));
  assert.match(html, /aria-pressed="true"/);
  assert.match(html, /TCP · 20000/);
  assert.match(html, /UDP · 20000/);
  assert.match(html, /<strong>0<\/strong>/);
  assert.match(html, /<strong>7<\/strong>/);
  assert.match(html, /data-state="warning"/);
  assert.doesNotMatch(html, /<select/);
});

test('disconnected agents do not display stale counts or a listening status', () => {
  const html = renderToStaticMarkup(createElement(RelayRoutePicker, { host: { ...host, state: 'unknown' }, selected: 'tcp', onChange() {} }));
  assert.doesNotMatch(html, /<strong>[07]<\/strong>|data-state="listening"/);
  assert.match(html, /Нет связи с Agent/);
  assert.match(html, /<strong>—<\/strong>/);
});

test('unknown session counts stay unknown instead of becoming zero', () => {
  const html = renderToStaticMarkup(createElement(RelayRoutePicker, { host: { ...host, routes: [{ ...route, sessions: undefined }] }, selected: 'tcp', onChange() {} }));
  assert.match(html, /<strong>—<\/strong>/);
});

test('connection choices preserve disabled state, selected identity and large-list search', () => {
  const consumers = Array.from({ length: 7 }, (_, i) => ({ id: String(i), label: `Connection ${i}` }));
  const html = renderToStaticMarkup(createElement(RelayConsumerPicker, { consumers, selected: '3', disabled: true, onChange() {} }));
  assert.match(html, /type="search"/);
  assert.match(html, /aria-pressed="true" disabled=""><span>Connection 3/);
  const options = html.split('class="relayConsumerChoices"')[1].split('</div>')[0];
  assert.equal((options.match(/disabled=""/g) || []).length, 5);
  assert.match(html, /1–5 из 7/);
  assert.doesNotMatch(html, /<select/);
});

test('large route lists open on the selected page and render only five rows', () => {
  const routes = Array.from({ length: 80 }, (_, i) => ({ ...route, id: `route-${i}`, listen_port: 20000 + i }));
  const html = renderToStaticMarkup(createElement(RelayRoutePicker, { host: { ...host, routes }, selected: 'route-79', onChange() {} }));
  assert.equal((html.match(/class="relayRouteChoice"/g) || []).length, 5);
  assert.match(html, /76–80 из 80/);
  assert.match(html, /TCP · 20079/);
  assert.doesNotMatch(html, /TCP · 20000/);
  assert.match(html, /aria-label="Поиск маршрута Relay"/);
});

test('large connection lists retain the selected label without rendering every connection', () => {
  const consumers = Array.from({ length: 1000 }, (_, i) => ({ id: String(i), label: `Connection ${i}` }));
  const html = renderToStaticMarkup(createElement(RelayConsumerPicker, { consumers, selected: '999', disabled: false, onChange() {} }));
  const options = html.split('class="relayConsumerChoices"')[1].split('</div>')[0];
  assert.equal((options.match(/<button/g) || []).length, 5);
  assert.match(html, /996–1000 из 1000/);
  assert.match(html, /<strong>Connection 999<\/strong>/);
});
