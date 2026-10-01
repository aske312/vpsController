import assert from "node:assert/strict";
import test from "node:test";
import { componentPresentation } from "../../src/features/overview/component-state.ts";

const image = { installed: true, installable: true, update_available: true, management: {state: "managed"},
  component_state: { installation: {state: "installed"}, runtime: {state: "stopped"}, health: {state: "unchecked"}, operation: null } };

test("installation, runtime, health and ongoing action remain independent", () => {
  const state = componentPresentation({...image, component_state: {...image.component_state, operation: {kind: "updating"}}});
  assert.equal(state.installation, "Installed");
  assert.equal(state.runtime, "Stopped");
  assert.equal(state.health, "Unchecked");
  assert.equal(state.operation, "Updating");
  assert.equal(state.canUpdate, false);
  const unknown = componentPresentation({...image, component_state: undefined});
  assert.equal(unknown.installation, "Unknown");
  assert.equal(unknown.runtime, "Unknown");
  assert.equal(unknown.canInstall, false);
});

test("legacy installs require adoption and retained configuration can only be reinstalled", () => {
  const legacy = componentPresentation({...image, management: {state: "unmanaged"}});
  assert.equal(legacy.canAdopt, true);
  assert.equal(legacy.canUpdate, false);
  assert.equal(legacy.readOnly, true);
  const retained = {...image, installed: false, management: {state: "managed", retained: true},
    component_state: {...image.component_state, installation: {state: "not_installed"}}};
  assert.equal(componentPresentation(retained).canInstall, true);
  assert.equal(componentPresentation(retained).canUpdate, false);
  assert.equal(componentPresentation({...retained, management: {state: "unknown"}}).canInstall, false);
});
