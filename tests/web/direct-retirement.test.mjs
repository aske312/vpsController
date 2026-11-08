import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import react from '@vitejs/plugin-react';
import { createTestLoader } from './vite-test-loader.mjs';

const loader = await createTestLoader(import.meta.url, [react()]);
after(() => loader.close());
const { GateNavigation } = await loader.ssrLoadModule('/src/control-panel/components/gate-navigation.tsx');

test('legacy installed Direct modules do not expose navigation; Mihomo stays available', () => {
  const legacy = ['wg', 'awg', 'shadowsocks', 'vless-reality-xhttp', 'hysteria2', 'tuic', 'trojan', 'openvpn', 'ikev2'];
  for (const installed of [true, false]) {
    const html = renderToStaticMarkup(createElement(GateNavigation, {
      activeTab: 'overview', nodeState: 'green', nodeStateLabel: 'Ready', onNavigate() {},
      protocolImages: [...legacy.map(id => ({ id, name: id, installed: true })), { id: 'mihomo', installed }],
    }));
    assert.equal(html.includes('aria-label="Mihomo"'), installed);
    assert.ok(!html.includes('aria-label="Tunnels"'));
    assert.ok(!html.includes('aria-label="Подключения"'));
    for (const label of ['Обзор', 'Сеть', 'Безопасность', 'Приложение', 'Службы']) {
      assert.ok(html.includes(`aria-label="${label}"`));
    }
  }
});
