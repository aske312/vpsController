import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { createServer } from 'vite';
const loader = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false }, appType: 'custom', optimizeDeps: { noDiscovery: true } });
after(() => loader.close());
const { relayConnection, relaySnapshot, relayClientResult } = await loader.ssrLoadModule('/src/features/network/relay-state.ts');

const connection = { api_version: 1, agent_url: 'https://85.209.155.229:9443', token: 's'.repeat(48), certificate_sha256: 'a'.repeat(64) };
const host = { id: 'b'.repeat(32), name: 'Relay', agent_url: connection.agent_url, certificate_sha256: connection.certificate_sha256, api_version: 1, token_configured: true, state: 'connected', version: '1.0.0', agent_revision: 0, foreign_routes: 0, routes: [] };
const status = { revision: 'c'.repeat(32), hosts: [host], pending: false, origin_ip: '195.14.118.112', origin_hosts: ['195.14.118.112'], targets: [{ transport: 'tcp', port: 443 }] };

test('optional Agent update status stays compatible with old agents and rejects unknown state', () => {
  assert.equal(relaySnapshot(status).hosts[0].updates, undefined);
  const updates = { enabled: true, interval_hours: 24, state: 'updated', version: '1.2.0' };
  assert.equal(relaySnapshot({ ...status, hosts: [{ ...host, updates }] }).hosts[0].updates.state, 'updated');
  assert.throws(() => relaySnapshot({ ...status, hosts: [{ ...host, updates: { ...updates, state: 'raw command output' } }] }));
  assert.throws(() => relaySnapshot({ ...status, hosts: [{ ...host, updates: { ...updates, enabled: 'yes' } }] }));
});
test('connection import requires a complete explicit API v1 identity', () => {
  assert.deepEqual(relayConnection(JSON.stringify(connection)), connection);
  for (const changed of [{ api_version: 2 }, { token: '' }, { certificate_sha256: '' }, { agent_url: 'http://85.209.155.229:9443' }]) assert.throws(() => relayConnection(JSON.stringify({ ...connection, ...changed })));
});
test('relay state rejects secrets, malformed counters and false readiness', () => {
  assert.deepEqual(relaySnapshot(status), status);
  for (const changed of [{ token: connection.token }, { agent_revision: null }, { state: 'running' }, { foreign_routes: -1 }]) assert.throws(() => relaySnapshot({ ...status, hosts: [{ ...host, ...changed }] }));
  const route = { id: 'pro-'+'d'.repeat(32), host_id: host.id, transport: 'udp', listen_port: 20001, target_ip: status.origin_ip, target_port: 8444, state: 'listening', target_verified: false };
  assert.equal(relaySnapshot({ ...status, hosts: [{ ...host, routes: [route] }] }).hosts[0].routes[0].target_verified, false);
  for (const changed of [{ target_verified: true }, { sent_bytes: -1 }, { listen_port: 443 }]) assert.throws(() => relaySnapshot({ ...status, hosts: [{ ...host, routes: [{ ...route, ...changed }] }] }));
  assert.throws(() => relaySnapshot({ ...status, hosts: [{ ...host, state: 'unknown', routes: [route] }] }));
});
test('automatic client delivery requires a complete native result and valid variants', () => {
  const result = { config: 'native config', filename: 'phone.conf' };
  assert.equal(relayClientResult(result), result);
  const variant = { id: 'direct', name: 'VLESS', filename: 'phone.txt', config: 'vless://native' };
  assert.equal(relayClientResult({ ...result, profiles: [variant] }).profiles[0], variant);
  for (const value of [null, {}, { config: '' }, { ...result, filename: '../secret' }, { ...result, profiles: {} },
    { ...result, profiles: [variant, variant] }, { ...result, profiles: [{ ...variant, config: {} }] }]) assert.throws(() => relayClientResult(value));
  for (const client_address of [null, {}, '', 'a'.repeat(254)]) assert.throws(() => relaySnapshot({ ...status, hosts: [{ ...host, client_address }] }));
  assert.equal(relaySnapshot({ ...status, hosts: [{ ...host, client_address: 'relay.example' }] }).hosts[0].client_address, 'relay.example');
});

test('recovery metadata names the exact pending Relay without exposing credentials', () => {
  const recovery = { operation_id:'e'.repeat(32),host_id:host.id,action:'put',transport:'udp',listen_port:20001,target_ip:status.origin_ip,target_port:51831,error:'response_unknown' };
  assert.equal(relaySnapshot({...status,pending:true,recovery}).recovery.host_id,host.id);
  for (const changed of [{operation_id:''},{action:'replay'},{listen_port:22},{error:'raw secret'}]) assert.throws(()=>relaySnapshot({...status,pending:true,recovery:{...recovery,...changed}}));
  assert.throws(()=>relaySnapshot({...status,pending:false,recovery}));
});
