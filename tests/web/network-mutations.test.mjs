import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { createTestLoader } from './vite-test-loader.mjs';
const loader = await createTestLoader(import.meta.url);
after(() => loader.close());
const { SystemOperationUnknown } = await loader.ssrLoadModule('/src/control-panel/system-operation.ts');
const { saveNetworkDns, saveNetworkEndpoints, deleteNetworkEndpoint, recoverNetworkDns } = await loader.ssrLoadModule('/src/features/network/network-api.ts');

const cases = [
  [admit => saveNetworkDns(admit, { revision: 'dns-revision', apply_system: true }), '/dns/settings', 'PUT'],
  [admit => saveNetworkEndpoints(admit, { revision: 'endpoint-revision', cdn_domain: 'cdn.example' }), '/network/endpoints', 'PUT'],
  [admit => deleteNetworkEndpoint(admit, 'cdn', 'cdn.example', 'endpoint-revision'), '/network/endpoints/cdn/cdn.example', 'DELETE'],
  [admit => recoverNetworkDns(admit), '/dns/recover', 'POST'],
];

test('lost network admission preserves its exact pending worker without repeating a mutation', async () => {
  for (const [invoke, path, method] of cases) {
    let calls = 0, identity;
    const operation = await invoke(async (actualPath, init) => {
      calls++; assert.equal(actualPath, path); assert.equal(init.method, method);
      identity = new Headers(init.headers).get('X-Operation-ID');
      assert.match(identity, /^[a-f0-9]{32}$/);
      if (method === 'DELETE') assert.equal(new Headers(init.headers).get('If-Match'), 'endpoint-revision');
      throw new SystemOperationUnknown(identity);
    });
    assert.equal(calls, 1); assert.equal(operation.id, identity); assert.equal(operation.state, 'unknown');
  }
});

test('an unrelated pending worker or explicit rejection cannot become this form\'s operation', async () => {
  for (const [invoke] of cases) {
    for (const error of [new SystemOperationUnknown('a'.repeat(32)), Object.assign(new Error('conflict'), { status: 409 })]) {
      await assert.rejects(invoke(async () => { throw error; }), cause => cause === error);
    }
    const saved = { id: 'worker', state: 'queued' };
    assert.equal(await invoke(async () => saved), saved);
  }
});
