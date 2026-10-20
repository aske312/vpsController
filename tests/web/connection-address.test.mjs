import assert from 'node:assert/strict';
import test from 'node:test';
import { applyConnectionAddress, connectionAddressModes, xrayProtocolSettings } from '../../src/features/mihomo/connection-address.ts';

const connection = (id, component, settings = {}, device_id = 'phone') => ({ id, component, device_id, name: id, settings });

test('Xray protocol selection retains transport options and uses compatible modes', () => {
  const settings = {route_mode:'direct', port:12000, target:'sni.example:443', transport:'xhttp', xpadding:'100-200',
    tls_transport:'grpc', cdn_transport:'httpupgrade', tls_xhttp_mode:'packet-up', relay_host_id:'b'.repeat(32)};
  for (const protocol of ['vmess', 'trojan']) {
    const selected = xrayProtocolSettings(settings, protocol, ['tls.example'], ['cdn.example']);
    assert.equal(selected.route_mode, 'tls');
    assert.equal(selected.xray_protocol, protocol);
    for (const key of Object.keys(settings).filter(key => key !== 'route_mode')) assert.equal(selected[key], settings[key]);
    assert.equal(selected.tls_domain, 'tls.example');
    assert.equal(xrayProtocolSettings(settings, protocol, [], ['cdn.example']).route_mode, 'cdn');
    const cdn = {...settings, route_mode:'cdn', relay_cdn:true, cdn_domain:'cdn.example'};
    assert.deepEqual(xrayProtocolSettings(cdn, protocol, [], []), {...cdn, xray_protocol:protocol});
    const restored = xrayProtocolSettings(selected, 'vless', [], []);
    assert.equal(restored.transport, settings.transport);
    assert.equal(restored.target, settings.target);
  }
});

test('bulk REALITY address does not turn VMess or Trojan into an invalid connection', () => {
  const vmess = connection('vmess', 'transport-reality', {xray_protocol:'vmess', route_mode:'tls', tls_domain:'tls.example'});
  const source = connection('vless', 'transport-reality', {route_mode:'direct', relay_host_id:'b'.repeat(32)});
  assert.equal(applyConnectionAddress([vmess], source)[0], vmess);
});

test('bulk CDN through Relay stays on VLESS and clears the flag when leaving CDN', () => {
  const vless = connection('vless', 'transport-reality', { route_mode: 'tls', tls_domain: 'origin.example', cdn_transport:'xhttp', cdn_xhttp_mode:'packet-up' });
  const udp = connection('udp', 'transport-tuic', { sni: 'cert.example' });
  const source = connection('cdn', 'transport-reality', { route_mode:'cdn', cdn_domain:'cdn.example', relay_cdn:true, relay_host_id:'b'.repeat(32) });
  const result = applyConnectionAddress([vless, udp], source);
  assert.equal(result[0].settings.route_mode, 'cdn');
  assert.equal(result[0].settings.relay_cdn, true);
  assert.equal(result[0].settings.cdn_transport, 'xhttp');
  assert.equal(result[0].settings.cdn_xhttp_mode, 'packet-up');
  assert.equal(result[1], udp);
  const cleared = applyConnectionAddress(result, {...source, settings:{route_mode:'direct'}});
  assert.equal(cleared[0].settings.relay_cdn, false);
});
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
