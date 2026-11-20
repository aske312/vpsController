import assert from 'node:assert/strict';
import test from 'node:test';
import { isRelayOriginTls, relayOriginTlsSettings, xrayProtocolSettings } from '../../src/features/mihomo/connection-address.ts';


test('origin TLS Relay selection keeps protocol options', () => {
  const base = {xray_protocol:'vmess',route_mode:'cdn',cdn_transport:'websocket',cdn_xhttp_mode:'packet-up',relay_cdn:true};
  const settings = relayOriginTlsSettings(base,'cert.example','relay-id','relay.example');
  assert.equal(isRelayOriginTls(settings),true);
  assert.equal(settings.cdn_domain,'cert.example');
  assert.equal(settings.relay_host_id,'relay-id');
  assert.equal(settings.relay_address,'relay.example');
  assert.equal(settings.cdn_transport,base.cdn_transport);
  assert.equal(settings.cdn_xhttp_mode,base.cdn_xhttp_mode);
  assert.equal(base.relay_cdn,true);
  assert.equal(isRelayOriginTls({...settings,relay_cdn:true}),false);

});

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
