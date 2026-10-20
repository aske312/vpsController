import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import reactPlugin from "@vitejs/plugin-react";

const loader = await createServer({ configFile: false, plugins: [reactPlugin()], server: { middlewareMode: true, hmr: false }, appType: "custom", optimizeDeps: { noDiscovery: true } });
after(() => loader.close());
const { RelayEndpointSelect } = await loader.ssrLoadModule("/src/features/network/relay-endpoint-select.tsx");
const { NetworkRelayRows, relayMatches } = await loader.ssrLoadModule("/src/features/network/network-relays.tsx");
const { connectionAddressModes } = await loader.ssrLoadModule("/src/features/mihomo/connection-address.ts");
const { MihomoConnectionAddress } = await loader.ssrLoadModule("/src/features/mihomo/connection-address-select.tsx");
const host = { id: "b".repeat(32), name: "Amsterdam", client_address: "relay.example", agent_url: "https://85.209.155.229:9443", state: "unknown", routes: [] };

const mihomoAddress = (component, settings = {}, props = {}) => renderToStaticMarkup(createElement(MihomoConnectionAddress, {
  connection: { id: 'fixture', name: 'fixture', device_id: 'phone', component, settings },
  inventory: { status: { hosts: [{ ...host, addresses: { ipv4: ['85.209.155.229'], ipv6: [], domains: ['relay.example', 'cdn-relay.example'], cdn_domains: ['cdn-relay.example'] } }] }, failed: false },
  tlsDomains: ['origin-tls.example'], udpDomains: ['origin-udp.example'], cdnDomains: ['origin-cdn.example'], webTransports: ['websocket'], supported: true, disabled: false, onChange: () => {}, ...props,
}));

test('Mihomo separates protocol mode from Relay endpoint and keeps TLS identity visible', () => {
  const settings = { route_mode: 'tls', tls_domain: 'origin-tls.example', relay_host_id: host.id, relay_address: 'relay.example' };
  const html = mihomoAddress('transport-reality', settings);
  assert.match(html, /<button[^>]*aria-pressed="true"[^>]*>TLS<\/button>/);
  assert.doesNotMatch(html, /<button[^>]*>Relay<\/button>|<button[^>]*>UDP<\/button>/);
  assert.doesNotMatch(html, /<optgroup/);
  assert.match(html, />relay.example · через Relay · Amsterdam<\/option>/);
  assert.match(html, /relay.example.*selected=""/);
  assert.match(html, /aria-label="TLS-сервис основного VPS"/);
  assert.match(html, /origin-tls.example.*selected=""/);
  assert.doesNotMatch(html, /cdn-relay.example/);
  assert.match(mihomoAddress('transport-reality', {route_mode:'direct'}), />relay.example · через Relay · Amsterdam<\/option>/);
});

test('Mihomo exposes Relay domains in UDP for every supported UDP protocol', () => {
  for (const component of ['transport-wg', 'transport-awg', 'transport-hysteria2', 'transport-tuic', 'transport-shadowsocks']) {
    const html = mihomoAddress(component, { relay_host_id: host.id, relay_address: 'relay.example' });
    assert.doesNotMatch(html, /<optgroup/);
    assert.match(html, />relay.example · через Relay · Amsterdam<\/option>/);
    assert.match(html, /origin-udp.example/);
    assert.match(html, /relay.example.*selected=""/);
    assert.doesNotMatch(html, /<button[^>]*>(TLS|CDN|REALITY|Relay)<\/button>|cdn-relay.example/);
  }
});

test('Mihomo offers Relay before CDN and retains saved legacy choices', () => {
  const html = mihomoAddress('transport-reality', {route_mode:'cdn',cdn_domain:'origin-cdn.example'});
  assert.match(html, /Вход в CDN/);
  assert.match(html, /relay.example/);
  const saved = mihomoAddress('transport-reality', {route_mode:'cdn',cdn_domain:'origin-cdn.example',relay_host_id:host.id,relay_address:'saved.example'});
  assert.match(saved, /saved.example.*selected=""/);
  assert.match(saved, /сохранённый адрес/);
});

test('legacy CDN Relay stays explicit and can be moved before CDN', () => {
  const html = mihomoAddress('transport-reality', {route_mode:'cdn',cdn_domain:'origin-cdn.example',relay_host_id:host.id,relay_cdn:true});
  assert.match(html, /origin-cdn.example.*selected=""/);
  assert.match(html, /aria-label="Вход в CDN"/);
  assert.match(html, /Origin Rules/);
  assert.match(html, /Клиент → CDN \(origin-cdn.example:443\) → Relay \(relay.example[^)]*\) → VPS/);
  assert.doesNotMatch(html, /сохранённый адрес|<optgroup/);
  for (const transport of ['websocket', 'xhttp', 'grpc', 'httpupgrade']) {
    assert.match(mihomoAddress('transport-reality', {route_mode:'cdn',cdn_domain:'origin-cdn.example',cdn_transport:transport}, {webTransports:[transport]}), /aria-label="Вход в CDN"/);
  }
});

test('first hop CDN Relay shows the preserved CDN domain and a Relay entry address', () => {
  const html = mihomoAddress('transport-reality', {route_mode:'cdn',cdn_domain:'origin-cdn.example',relay_host_id:host.id,relay_cdn:true,relay_cdn_mode:'before_cdn'});
  assert.match(html, /origin-cdn.example.*selected=""/);
  assert.match(html, /Клиент → Relay \(relay.example[^)]*\) → CDN \(origin-cdn.example:443\) → VPS/);
  assert.doesNotMatch(html, /<details|Настройки Cloudflare|SNI, Host|Origin Rules:|Перенести Relay/);
});

test('Mihomo route maps use protocol ports, matching Relay sockets and bracketed IPv6', () => {
  const origin = '203.0.113.10';
  for (const component of ['transport-reality','transport-wg','transport-awg','transport-shadowsocks','transport-hysteria2','transport-tuic']) {
    const transports = component === 'transport-reality' ? ['tcp'] : component === 'transport-shadowsocks' ? ['tcp','udp'] : ['udp'];
    const routes = transports.map(transport => ({transport,target_ip:origin,target_port:31000,listen_port:20001}));
    const props = {connection:{id:'node',component,settings:{route_mode:'direct',relay_host_id:host.id,relay_address:'2001:db8::1'},credential:{server_address:origin,port:31000}},
      inventory:{status:{origin_ip:origin,hosts:[{...host,routes}]},failed:false}};
    const html = mihomoAddress(component, {}, props);
    assert.match(html, /Клиент → Relay \(\[2001:db8::1\]:20001\) → VPS \(203.0.113.10:31000\)/, component);
    // A draft port without a matching prepared rule cannot borrow another port.
    props.connection.settings.port = 31001;
    assert.match(mihomoAddress(component, {}, props), /Relay \(\[2001:db8::1\] · порт после сохранения\) → VPS \(203.0.113.10:31001\)/);
  }
});

test('Mihomo CDN and direct maps use their own addresses and ignore unrelated Relay destinations', () => {
  const origin = '203.0.113.10';
  const inventory = {status:{origin_ip:origin,hosts:[{...host,routes:[
    {transport:'tcp',target_ip:origin,target_port:443,listen_port:20001},
    {transport:'tcp',target_ip:'104.16.1.2',target_port:443,listen_port:20002,cdn_domain:'origin-cdn.example'},
  ]}]},failed:false};
  const base = {route_mode:'cdn',cdn_domain:'origin-cdn.example',relay_host_id:host.id,relay_cdn:true};
  assert.match(mihomoAddress('transport-reality',{...base,relay_cdn_mode:'before_cdn'},{inventory}), /Relay \(relay.example:20002\) → CDN \(origin-cdn.example:443\) → VPS \(203.0.113.10:443\)/);
  assert.match(mihomoAddress('transport-reality',base,{inventory}), /CDN \(origin-cdn.example:443\) → Relay \(relay.example:20001\) → VPS \(203.0.113.10:443\)/);
  assert.match(mihomoAddress('transport-reality',{route_mode:'tls',tls_domain:'origin-tls.example'},{inventory}), /Клиент → VPS \(origin-tls.example:443\)/);
  assert.match(mihomoAddress('transport-reality',{route_mode:'cdn',cdn_domain:'other.example',relay_host_id:host.id,relay_cdn:true,relay_cdn_mode:'before_cdn'},{inventory}), /Relay \(relay.example · порт после сохранения\) → CDN \(other.example:443\)/);
});

test('Mihomo retains excluded or unavailable saved Relay endpoints and allows direct recovery', () => {
  const html = mihomoAddress('transport-tuic', {relay_host_id:host.id,relay_address:'saved.example'}, {inventory:{status:null,failed:true}});
  assert.match(html, /saved.example.*selected=""/);
  assert.doesNotMatch(html, /<select[^>]*disabled=/);
  assert.match(html, /origin-udp.example/);
  assert.doesNotMatch(mihomoAddress('transport-reality', {}, {webTransports:[]}), /<button[^>]*>(TLS|CDN)<\/button>/);
  assert.doesNotMatch(mihomoAddress('unknown'), /<button[^>]*>|через Relay/);
  const excluded = mihomoAddress('transport-wg', {}, {inventory:{status:{hosts:[{...host,excluded_addresses:['relay.example']}]},failed:false}});
  assert.doesNotMatch(excluded, /relay.example/);
});

test('protocol-incompatible modes are absent rather than disabled', () => {
  for (const component of ['transport-reality', 'transport-wg', 'transport-tuic', 'transport-hysteria2', 'transport-awg', 'transport-shadowsocks', 'unknown']) {
    const modes = connectionAddressModes(component);
    const choice = kind => ({ domains: ['direct.example'], value: '', disabled: !modes[kind], onChange: () => {} });
    const html = renderToStaticMarkup(createElement(RelayEndpointSelect, {
      modeControls: true, relaySupported: modes.relay, request: async () => ({}),
      inventory: {status: {hosts: [host]}, failed: false}, value: '', onChange: () => {},
      tls: choice('tls'), udp: choice('udp'), cdn: choice('cdn'),
    }));
    for (const [kind, label] of [['tls', 'TLS'], ['udp', 'UDP'], ['cdn', 'CDN'], ['relay', 'Relay']]) {
      assert.equal(new RegExp(`<button[^>]*>${label}</button>`).test(html), modes[kind], `${component} ${kind}`);
    }
  }
});

test('local TLS and UDP remain usable without Relay inventory while native Relay stays locked', () => {
  const props = { request: async () => ({}), inventory: { status: null, failed: true }, value: '', onChange: () => {} };
  for (const kind of ['tls', 'udp']) {
    const html = renderToStaticMarkup(createElement(RelayEndpointSelect, { ...props, modeControls: true, [kind]: { domains: ['direct.example'], value: 'direct.example', onChange: () => {} } }));
    assert.match(html, /direct.example[^>]*selected=""/);
    assert.doesNotMatch(html, /<select[^>]*disabled=/);
  }
  const native = renderToStaticMarkup(createElement(RelayEndpointSelect, props));
  assert.match(native, /<select[^>]*disabled=/);
});

test("saved Relay addresses remain selectable regardless of liveness and pending diagnostics", () => {
  for (const state of ["unknown", "connected"]) for (const pending of [false, true]) {
    const html = renderToStaticMarkup(createElement(RelayEndpointSelect, {
      request: async () => { throw Error("Selection must use the screen inventory"); },
      inventory: { status: { hosts: [{ ...host, state }], pending }, failed: false },
      value: host.id, onChange: () => {},
    }));
    assert.doesNotMatch(html, /disabled=/);
    assert.match(html, /value="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" selected=""/);
    assert.match(html, /relay\.example/);
  }
});

test("an explicit editing lock still disables Relay selection", () => {
  const html = renderToStaticMarkup(createElement(RelayEndpointSelect, {
    request: async () => ({}), inventory: { status: { hosts: [host] }, failed: false },
    value: host.id, onChange: () => {}, disabled: true,
  }));
  assert.match(html, /<select[^>]*disabled=""/);
});

test("excluded addresses disappear independently from ROUTES and new connection choices", () => {
  const item = { ...host, addresses: { ipv4: ['85.209.155.229', '8.8.4.4'], ipv6: [], domains: ['relay.example'] }, excluded_addresses: ['relay.example', '85.209.155.229'] };
  const html = renderToStaticMarkup(createElement(RelayEndpointSelect, {
    request: async () => ({}), inventory: { status: { hosts: [item] }, failed: false }, value: '', onChange: () => {},
  }));
  assert.ok(html.includes('8.8.4.4'));
  assert.ok(!html.includes('relay.example'));
  assert.ok(!html.includes('85.209.155.229'));
  assert.equal(relayMatches(item, 'relay.example'), false);
  assert.equal(relayMatches(item, '8.8.4.4'), true);
  const saved = renderToStaticMarkup(createElement(RelayEndpointSelect, {
    request: async () => ({}), inventory: { status: { hosts: [item] }, failed: false }, value: host.id, address: 'relay.example', onChange: () => {},
  }));
  assert.match(saved, /relay.example.*selected=""/);
});

test("one Relay exposes every IPv4, IPv6 and domain and retains the selected address", () => {
  const addresses={ipv4:['85.209.155.229','8.8.4.4'],ipv6:['2606:4700::1111'],domains:['relay.example','alternate.example']};
  const html=renderToStaticMarkup(createElement(RelayEndpointSelect,{
    request:async()=>({}),inventory:{status:{hosts:[{...host,addresses}]},failed:false},
    value:host.id,address:'2606:4700::1111',onChange:()=>{},
  }));
  for(const address of [...addresses.ipv4,...addresses.ipv6,...addresses.domains]) assert.ok(html.includes(address));
  assert.match(html,/2606:4700::1111[^<]*selected=""/);
  assert.doesNotMatch(html,/disabled=/);
});

test("ROUTES lists and searches every Relay address with a legacy fallback", () => {
  const addresses = { ipv4: ['85.209.155.229', '8.8.4.4'], ipv6: ['2606:4700::1111'], domains: ['relay.example', 'alternate.example'] };
  const item = { ...host, addresses };
  const render = (query = '', selectedHost = item, expanded = []) => renderToStaticMarkup(createElement('table', null, createElement('tbody', null, createElement(NetworkRelayRows, {
    relay: { status: { hosts: [selectedHost], pending: false }, failed: false, reading: false, locked: false },
    query, expanded, onToggle: () => {},
  }))));
  const all = render();
  assert.equal((all.match(/<tr\b/g) || []).length, 5);
  for (const address of [...addresses.ipv4, ...addresses.ipv6, ...addresses.domains]) {
    assert.ok(all.includes(`<strong>${address}</strong>`));
    assert.ok(relayMatches(item, address));
  }
  for (const address of ['8.8.4.4', '2606:4700::1111', 'alternate.example']) {
    const filtered = render(address);
    assert.equal((filtered.match(/<tr\b/g) || []).length, 1);
    assert.ok(filtered.includes(`<strong>${address}</strong>`));
  }
  assert.equal((render('Amsterdam').match(/<tr\b/g) || []).length, 5);
  assert.equal((render('', host).match(/<tr\b/g) || []).length, 1);
  assert.ok(render('', host).includes('<strong>relay.example</strong>'));
  assert.equal((render('', item, [`relay-${host.id}-2606:4700::1111`]).match(/aria-expanded="true"/g) || []).length, 1);
  assert.equal(render('unmatched.example'), '<table><tbody></tbody></table>');
});

test("connection addresses group local and remote CDN domains separately from Relay", () => {
  const html = renderToStaticMarkup(createElement(RelayEndpointSelect, {
    request: async () => ({}), inventory: {status: {hosts: [{...host, addresses: {ipv4:['85.209.155.229'],ipv6:[],domains:['relay.example','remote-cdn.example'],cdn_domains:['remote-cdn.example']}}]},failed:false},
    value:'', onChange:()=>{}, cdn:{domains:['local-cdn.example'],value:'local-cdn.example',onChange:()=>{}},
  }));
  const cdn = html.match(/<optgroup label="CDN">(.*?)<\/optgroup>/s)?.[1];
  const relays = html.match(/<optgroup label="Relay">(.*?)<\/optgroup>/s)?.[1];
  assert.match(cdn, /local-cdn\.example[^>]*selected=""/);
  assert.match(cdn, /remote-cdn\.example/);
  assert.match(relays, /relay\.example/);
  assert.doesNotMatch(relays, /remote-cdn\.example|local-cdn\.example/);
  assert.match(cdn, /remote-cdn\.example[^>]*disabled=""/);
});
