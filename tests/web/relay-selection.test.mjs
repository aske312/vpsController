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
const host = { id: "b".repeat(32), name: "Amsterdam", client_address: "relay.example", agent_url: "https://85.209.155.229:9443", state: "unknown", routes: [] };

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
