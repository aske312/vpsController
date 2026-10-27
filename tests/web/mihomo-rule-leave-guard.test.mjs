import assert from "node:assert/strict";
import test from "node:test";
import { createRuleLeaveGuard } from "../../src/features/mihomo/rule-leave-guard.ts";

function fixture() {
  const values = { dirty: true, pending: false, canSave: true, saves: 0, discards: 0 };
  const guard = createRuleLeaveGuard({ dirty: () => values.dirty, pending: () => values.pending,
    canSave: () => values.canSave, save: async () => { values.saves++; values.dirty = false; return true; },
    discard: () => { values.discards++; values.dirty = false; } });
  return { values, guard };
}

test("clean navigation does not ask; staying retains the draft", async () => {
  const { values, guard } = fixture();
  values.dirty = false;
  assert.equal(await guard.request(), true);
  assert.equal(guard.getSnapshot().open, false);
  values.dirty = true;
  const navigation = guard.request();
  assert.equal(guard.getSnapshot().open, true);
  await guard.choose("stay");
  assert.equal(await navigation, false);
  assert.equal(values.dirty, true);
  assert.equal(values.saves + values.discards, 0);
});

test("discard resets the draft; save waits for confirmed success", async () => {
  for (const choice of ["discard", "save"]) {
    const { values, guard } = fixture();
    const navigation = guard.request();
    await guard.choose(choice);
    assert.equal(await navigation, true);
    assert.equal(values.dirty, false);
    assert.equal(values.saves, choice === "save" ? 1 : 0);
    assert.equal(values.discards, choice === "discard" ? 1 : 0);
    assert.equal(guard.getSnapshot().open, false);
  }
});

test("failed saves and newer unsaved edits keep navigation blocked", async () => {
  for (const save of [async () => false, async () => { throw Error("failed"); }, async () => true]) {
    const guard = createRuleLeaveGuard({ dirty: () => true, pending: () => false, canSave: () => true, save, discard() {} });
    const navigation = guard.request();
    await guard.choose("save");
    assert.equal(guard.getSnapshot().open, true);
    assert.equal(guard.getSnapshot().saving, false);
    assert.ok(guard.getSnapshot().error);
    await guard.choose("stay");
    assert.equal(await navigation, false);
  }
});

test("pending writes cannot be discarded or resent, including clean drafts", async () => {
  const { values, guard } = fixture();
  values.pending = true;
  values.dirty = false;
  const navigation = guard.request();
  await guard.choose("discard");
  await guard.choose("save");
  assert.equal(values.saves + values.discards, 0);
  assert.equal(guard.getSnapshot().open, true);
  values.pending = false;
  await guard.choose("discard");
  assert.equal(await navigation, true);
});

test("a late confirmed receipt permits navigation without another write", async () => {
  const { values, guard } = fixture();
  values.pending = true;
  const navigation = guard.request();
  guard.reconcile();
  assert.equal(guard.getSnapshot().open, true);
  values.pending = false;
  values.dirty = false;
  guard.reconcile();
  assert.equal(await navigation, true);
  assert.equal(values.saves, 0);
});

test("discard cannot navigate away when restoring the baseline fails", async () => {
  for (const discard of [() => {}, () => { throw Error("failed"); }]) {
    const guard = createRuleLeaveGuard({ dirty: () => true, pending: () => false, canSave: () => true,
      save: async () => false, discard });
    const navigation = guard.request();
    await guard.choose("discard");
    assert.equal(guard.getSnapshot().open, true);
    assert.ok(guard.getSnapshot().error);
    await guard.choose("stay");
    assert.equal(await navigation, false);
  }
});

test("unavailable saves retain the draft and the first destination owns the dialog", async () => {
  const { values, guard } = fixture();
  values.canSave = false;
  const first = guard.request();
  assert.equal(await guard.request(), false);
  await guard.choose("save");
  assert.equal(values.saves, 0);
  assert.equal(guard.getSnapshot().open, true);
  await guard.choose("discard");
  assert.equal(await first, true);
});

test("double clicks do not duplicate saves; unmount cancels pending navigation", async () => {
  let finishSave;
  let saves = 0;
  let dirty = true;
  const guard = createRuleLeaveGuard({ dirty: () => dirty, pending: () => false, canSave: () => true,
    save: () => { saves++; return new Promise((resolve) => { finishSave = resolve; }); }, discard() {} });
  const navigation = guard.request();
  const saving = guard.choose("save");
  await guard.choose("save");
  await guard.choose("discard");
  await guard.choose("stay");
  assert.equal(saves, 1);
  assert.equal(guard.getSnapshot().saving, true);
  guard.dispose();
  assert.equal(await navigation, false);
  dirty = false;
  finishSave(true);
  await saving;
  assert.equal(guard.getSnapshot().open, false);
});
