import assert from 'node:assert/strict';
import test from 'node:test';
import { connectionState } from '../src/connection-state.ts';

test('issued proxy credentials are not mistaken for disconnected clients', () => {
  for (const protocol of ['hysteria2','tuic','xray']) {
    assert.equal(connectionState({protocol,quality:'offline'}),'issued');
    assert.equal(connectionState({protocol,quality:'stable'}),'issued');
    assert.equal(connectionState({protocol,update_state:'needs-profile'}),'attention');
  }
  assert.equal(connectionState({protocol:'awg',quality:'offline'}),'offline');
  assert.equal(connectionState({protocol:'awg',quality:'stable'}),'stable');
  assert.equal(connectionState({protocol:'awg',quality:'warning'}),'attention');
});
