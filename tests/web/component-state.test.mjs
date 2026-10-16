import assert from "node:assert/strict";
import test from "node:test";
import { componentPresentation } from "../../src/features/overview/component-state.ts";
import { componentInstalledVersion } from "../../src/shared/lib/format-version.ts";

test("installed component version never falls back to the available release", () => {
  const candidate = {id: "wg", installed: true, available_version: "9.9.9"};
  assert.equal(componentInstalledVersion(candidate), "Версия неизвестна");
  assert.equal(componentInstalledVersion({...candidate, installed: false}), "Не установлен");
  assert.equal(componentInstalledVersion({...candidate, installed_version: "1.2.3"}), "v1.2.3");
  assert.equal(componentInstalledVersion({...candidate, id: "mihomo", installed_version: "1.2.3"}, "2.3.4"), "v2.3.4");
});

const image = { installed: true, installable: true, update_available: true, management: {state: "managed"},
  component_state: { installation: {state: "installed"}, runtime: {state: "stopped"}, health: {state: "unchecked"}, operation: null } };

test("one component status prioritizes health, unknown results and active operations", () => {
  for (const [runtime,health,label] of [
    ["running","healthy","ACTIVE"], ["running","unchecked","ACTIVE"],
    ["running","possible_issues","WARN"], ["running","error","ERROR"],
    ["running","unknown","UNKNOWN"], ["stopped","healthy","STOPPED"],
    ["error","healthy","ERROR"], ["unknown","healthy","UNKNOWN"],
  ]) assert.equal(componentPresentation({...image,component_state:{...image.component_state,runtime:{state:runtime},health:{state:health}}}).badge.label,label);
  const changing={...image,component_state:{...image.component_state,operation:{kind:"updating",state:"running"}}};
  assert.equal(componentPresentation(changing).badge.label,"UPDATING");
  assert.equal(componentPresentation({...changing,component_state:{...changing.component_state,operation:{kind:"updating",state:"unknown"}}}).badge.label,"UNKNOWN");
  assert.equal(componentPresentation({...image,component_state:undefined}).badge.label,"UNKNOWN");
});

test("removal requires a removable managed installation and a confirmed idle state", () => {
  const removable = {...image, removable: true};
  assert.equal(componentPresentation(removable).canRemove, true);
  for (const patch of [
    {removable: false}, {installed: false}, {management: {state: "unmanaged"}},
    {management: {state: "unknown"}}, {management: {state: "managed", retained: true}},
    {component_state: undefined},
    {component_state: {...image.component_state, installation: {state: "unknown"}}},
    {component_state: {...image.component_state, operation: {kind: "removing", state: "running"}}},
    {component_state: {...image.component_state, operation: {kind: "removing", state: "unknown"}}},
  ]) assert.equal(componentPresentation({...removable, ...patch}).canRemove, false);
});

test("installation, runtime, health and ongoing action remain independent", () => {
  const state = componentPresentation({...image, component_state: {...image.component_state, operation: {kind: "updating"}}});
  assert.equal(state.installation, "Установлен");
  assert.equal(state.runtime, "Остановлена");
  assert.equal(state.health, "Не выполнялась");
  assert.equal(state.operation, "Обновление");
  assert.equal(state.summary.tone, "progress");
  assert.equal(state.canUpdate, false);
  const unknown = componentPresentation({...image, component_state: undefined});
  assert.equal(unknown.installation, "Нет данных");
  assert.equal(unknown.runtime, "Нет данных");
  assert.equal(unknown.summary.tone, "unknown");
  assert.equal(unknown.canInstall, false);
  const uncertain = componentPresentation({ ...image, component_state: { ...image.component_state, operation: { kind: null, state: "unknown" } } });
  assert.equal(uncertain.summary.tone, "unknown");
  assert.equal(uncertain.canUpdate, false);
  assert.equal(uncertain.canInstall, false);
});

test("a running service is distinct from verified health and adverse observations take priority", () => {
  const cases = [
    ["installed", "running", "unchecked", "running"],
    ["installed", "running", "unknown", "running"],
    ["installed", "running", "healthy", "online"],
    ["installed", "running", "possible_issues", "warning"],
    ["installed", "running", "error", "error"],
    ["installed", "stopped", "healthy", "warning"],
    ["installed", "error", "healthy", "error"],
    ["installed", "unknown", "healthy", "unknown"],
    ["incomplete", "running", "healthy", "error"],
    [null, "running", "healthy", "unknown"],
    ["not_installed", "running", "healthy", "available"],
  ];
  for (const [installation, runtime, health, tone] of cases) {
    const value = { ...image, component_state: { installation: { state: installation }, runtime: { state: runtime }, health: { state: health, reason: "Причина проверки" } } };
    const presentation = componentPresentation(value);
    assert.equal(presentation.summary.tone, tone, `${installation}/${runtime}/${health}`);
    assert.ok(presentation.summary.label);
    assert.ok(presentation.summary.description);
    if (health === "possible_issues" && runtime === "running") assert.equal(presentation.summary.description, "Причина проверки");
  }
  const failing = { ...image, component_state: { ...image.component_state, runtime: { state: "running" }, health: { state: "possible_issues" } } };
  assert.equal(componentPresentation(failing, "Нет успешных проверок внешней сети").summary.description, "Нет успешных проверок внешней сети");
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
