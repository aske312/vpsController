import assert from 'node:assert/strict';
import test from 'node:test';
import { authorizedRequest, apiErrorMessage, SessionExpiredError, UnknownMutationError } from '../src/api-request.ts';

test('401 expires the current session and blocks subsequent commands', async () => {
  let current = 'current'; let calls = 0;
  const session = { isCurrent: token => token === current, expire: () => { current = ''; } };
  const transport = async () => { calls++; return new Response('', { status: 401 }); };
  await assert.rejects(authorizedRequest('/clients', 'current', undefined, session, transport), SessionExpiredError);
  await assert.rejects(authorizedRequest('/clients', 'current', { method: 'POST' }, session, transport), SessionExpiredError);
  assert.equal(calls, 1);
});

test('an old response cannot expire a new session or return stale data', async () => {
  for (const status of [200, 401]) {
    let current = 'old'; let expirations = 0;
    const session = { isCurrent: token => token === current, expire: () => { expirations++; } };
    const transport = async () => { current = 'new'; return new Response('{}', { status }); };
    await assert.rejects(authorizedRequest('/clients', 'old', undefined, session, transport), SessionExpiredError);
    assert.equal(expirations, 0);
  }
});

test('validation errors stay readable without echoing rejected secrets', () => {
  const raw = JSON.stringify({ detail: [{ loc: ['body','settings','local_password'], msg: 'Too short', input: 'SECRET' }] });
  assert.equal(apiErrorMessage(raw, 422), 'settings.local_password: Too short');
  assert.equal(apiErrorMessage('<html>gateway unavailable</html>', 502), 'Ошибка сервера (502)');
});

test('network failures never retry mutations or expire authentication', async () => {
  let calls = 0;
  const session = { isCurrent: () => true, expire: () => assert.fail('Must not expire') };
  await assert.rejects(authorizedRequest('/clients','token',{method:'POST'},session, async () => { calls++; throw new TypeError('Failed to fetch'); }), UnknownMutationError);
  assert.equal(calls,1);
});

test('a lost successful response is reported as unknown, not as a failed command', async () => {
  const session = { isCurrent: () => true, expire: () => assert.fail('Must not expire') };
  await assert.rejects(authorizedRequest('/clients','token',{method:'POST'},session,async () => new Response('{truncated')), UnknownMutationError);
});
