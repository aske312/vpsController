import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import react from '@vitejs/plugin-react';
import { createTestLoader } from './vite-test-loader.mjs';
import { connectionAddressModes, snellCarriers } from '../../src/features/mihomo/connection-address.ts';

const loader = await createTestLoader(import.meta.url, [react()]);
after(() => loader.close());
const { MihomoConnectionAddress } = await loader.ssrLoadModule('/src/features/mihomo/connection-address-select.tsx');
const { profileConnectionRoute, ConnectionRouteMap } = await loader.ssrLoadModule('/src/features/mihomo/connection-route-map.tsx');
const snell = { id: 'snell', name: 'Phone', device_id: 'phone', component: 'transport-snell', settings: {},
  credential: {server_address:'8.8.8.8', port:33000} };
const carrier = { id: 'carrier', name: 'My CDN', device_id: 'phone', component: 'transport-reality',
  settings: {route_mode:'cdn', cdn_domain:'cdn.example'}, credential: {server_address:'8.8.8.8',port:443} };
const inventory = { status: { origin_ip:'8.8.8.8', hosts:[] }, failed:false };

test('Snell offers TCP Relay and only device-local Xray TLS/CDN carriers', () => {
  assert.deepEqual(connectionAddressModes('transport-snell'), {tls:false,cdn:false,udp:false,relay:true});
  const foreign = {...carrier, id:'foreign',device_id:'laptop'};
  const direct = {...carrier,id:'direct',settings:{route_mode:'direct'}};
  assert.deepEqual(snellCarriers(snell,[carrier,foreign,direct,snell]),[carrier]);
  const html=renderToStaticMarkup(createElement(MihomoConnectionAddress, { connection:snell, connections:[carrier,foreign],
    inventory,tlsDomains:[],udpDomains:[],cdnDomains:[],webTransports:[],supported:true,disabled:false,onChange(){} }));
  assert.match(html,/value="carrier"/);
  assert.doesNotMatch(html,/value="foreign"/);
  assert.match(html,/1\.19\.34/);
});

test('Snell chain map shows the selected CDN and its own listener without a direct fallback', () => {
  const selected={...snell,settings:{carrier_connection_id:'carrier'}};
  const route=profileConnectionRoute(selected,inventory,[snell,carrier]);
  assert.equal(route.mode,'cdn');
  assert.equal(route.domain,'cdn.example');
  assert.equal(route.inner.port,33000);
  const html=renderToStaticMarkup(createElement(ConnectionRouteMap,{route}));
  assert.match(html,/cdn\.example/);
  assert.match(html,/33000/);
  const missing=profileConnectionRoute(selected,inventory,[]);
  assert.equal(missing.unavailable,true);
  assert.doesNotMatch(renderToStaticMarkup(createElement(ConnectionRouteMap,{route:missing})),/data-hop="vps"/);
});

test('Snell Relay map uses the TCP binding even when UDP forwarding is enabled', () => {
  const host={id:'relay',name:'Relay',agent_url:'https://relay.example',routes:[
    {transport:'tcp',target_ip:'8.8.8.8',target_port:33000,listen_port:20020},
    {transport:'udp',target_ip:'8.8.8.8',target_port:33000,listen_port:20030}]};
  const route=profileConnectionRoute({...snell,settings:{relay_host_id:'relay',udp:true}},
    {status:{origin_ip:'8.8.8.8',hosts:[host]}});
  assert.equal(route.relay.port,20020);
});
