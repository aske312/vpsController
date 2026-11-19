import assert from 'node:assert/strict';
import test from 'node:test';
import { observedNodeState } from '../../src/control-panel/node-state.ts';

const healthy = () => ({ api: { active: true }, containers: [{ State: 'running', healthy: true }], action: {}, component_health: [{ status: 'healthy' }] });

test('a rolled back failed operation does not keep a recovered node in error', () => {
  const app = healthy();
  app.action = { state: 'failed', result: 'exit-code', action: 'cdn-security' };
  assert.equal(observedNodeState(app, null, false), 'green');
  app.api.active = false;
  assert.equal(observedNodeState(app, null, false), 'red');
  app.api.active = true;
  assert.equal(observedNodeState(app, null, false), 'green');
});

test('current faults and required recovery remain visible regardless of history', () => {
  for (const change of [{ api: { active: false } }, { recovery: { application_required: true } }, { component_health: [{ status: 'critical' }] }]) {
    assert.equal(observedNodeState({ ...healthy(), ...change }, null, false), 'red');
  }
  assert.equal(observedNodeState(healthy(), { items: [{ state: 'failed' }] }, false), 'red');
  assert.equal(observedNodeState(healthy(), { items: [], failed_units: 1 }, false), 'green');
});

test('active work, service mode and missing observations have distinct states', () => {
  assert.equal(observedNodeState(null, null, false), 'gray');
  assert.equal(observedNodeState(healthy(), null, true), 'yellow');
  assert.equal(observedNodeState({ ...healthy(), api: { active: null } }, null, false), 'yellow');
  assert.equal(observedNodeState({ ...healthy(), containers: [{ State: 'stopped' }] }, null, false), 'yellow');
  assert.equal(observedNodeState({ ...healthy(), service_mode: { active: true } }, null, false), 'blue');
  assert.equal(observedNodeState(healthy(), { items: [], observation_failed: true }, false), 'yellow');
});
