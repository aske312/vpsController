import assert from 'node:assert/strict';
import test from 'node:test';
import { applyConnectionAddress, connectionAddressModes } from '../../src/features/mihomo/connection-address.ts';

const connection = (id, component, settings = {}, device_id = 'phone') => ({ id, component, device_id, name: id, settings });
test('bulk address selection preserves protocol parameters and isolates devices', () => {
  const vless = connection('vless', 'transport-reality', { route_mode: 'cdn', cdn_domain: 'cdn.example', cdn_enabled: true, port: 443, uuid: 'secret' });
  const udp = connection('udp', 'transport-tuic', { port: 8443, password: 'secret' });
  const other = connection('other', 'transport-tuic', {}, 'laptop');
  const source = { ...udp, settings: { ...udp.settings, relay_host_id: 'a'.repeat(32), relay_address: '2606:4700::1111' } };
  const result = applyConnectionAddress([vless, udp, other], source);
  assert.equal(result[0].settings.route_mode, 'direct');
  assert.equal(result[0].settings.cdn_enabled, false);
  assert.equal(result[0].settings.port, 443);
  assert.equal(result[0].settings.uuid, 'secret');
  assert.equal(result[1].settings.port, 8443);
  assert.equal(result[1].settings.password, 'secret');
  for (const item of result.slice(0, 2)) assert.equal(item.settings.relay_address, '2606:4700::1111');
  assert.equal(result[2], other);
  assert.equal(vless.settings.route_mode, 'cdn');
  const cdn = applyConnectionAddress(result, vless);
  assert.equal(cdn[0].settings.cdn_domain, 'cdn.example');
  assert.equal(cdn[0].settings.relay_host_id, '');
  assert.equal(cdn[1], result[1]);
  const direct = applyConnectionAddress(cdn, udp);
  assert.equal(direct[0].settings.route_mode, 'direct');
  for (const item of direct.slice(0, 2)) assert.equal(item.settings.relay_host_id, '');
});

test('TLS and UDP bulk selection is limited to compatible protocols and clears Relay', () => {
  const vless = connection('vless', 'transport-reality', { route_mode: 'direct', port: 9443 });
  const udp = connection('udp', 'transport-tuic', { relay_host_id: 'a'.repeat(32), sni: 'cert.example', port: 8443 });
  const other = connection('other', 'transport-wg', {}, 'laptop');
  const result = applyConnectionAddress([vless, udp, other], { ...udp, settings: { udp_domain: 'direct.example' } });
  assert.equal(result[0], vless);
  assert.equal(result[1].settings.udp_domain, 'direct.example');
  assert.equal(result[1].settings.relay_host_id, '');
  assert.equal(result[1].settings.sni, 'cert.example');
  assert.equal(result[1].settings.port, 8443);
  assert.equal(result[2], other);
  const tls = applyConnectionAddress(result, { ...vless, settings: { route_mode: 'tls', tls_domain: 'direct.example' } });
  assert.equal(tls[0].settings.route_mode, 'tls');
  assert.equal(tls[0].settings.tls_domain, 'direct.example');
  assert.equal(tls[1], result[1]);
  for (const component of ['transport-wg', 'transport-awg', 'transport-hysteria2', 'transport-tuic', 'transport-shadowsocks']) {
    assert.deepEqual(connectionAddressModes(component), { tls: false, cdn: false, udp: true, relay: true });
  }
  assert.deepEqual(connectionAddressModes('transport-reality'), { tls: true, cdn: true, udp: false, relay: true });
  assert.deepEqual(connectionAddressModes('unknown'), { tls: false, cdn: false, udp: false, relay: false });
});
