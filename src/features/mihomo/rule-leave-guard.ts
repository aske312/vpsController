export type SectionLeaveResult = { allowed: boolean; dirty: boolean };
export type SectionLeaveGuard = () => Promise<SectionLeaveResult>;
export type RuleLeaveState = { open: boolean; saving: boolean; error: string };

export function createRuleLeaveGuard(actions: {
  dirty: () => boolean;
  pending: () => boolean;
  canSave: () => boolean;
  save: () => Promise<boolean>;
  discard: () => void;
}) {
  let state: RuleLeaveState = { open: false, saving: false, error: "" };
  let resolve: ((allowed: boolean) => void) | undefined;
  const listeners = new Set<() => void>();
  function update(next: RuleLeaveState) {
    state = next;
    for (const listener of listeners) listener();
  }
  function finish(allowed: boolean) {
    const continuation = resolve;
    resolve = undefined;
    update({ open: false, saving: false, error: "" });
    continuation?.(allowed);
  }
  return {
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    getSnapshot: () => state,
    request(): Promise<boolean> {
      // Only the first attempted destination owns the open dialog.
      if (resolve) return Promise.resolve(false);
      if (!actions.dirty() && !actions.pending()) return Promise.resolve(true);
      return new Promise((continuation) => {
        resolve = continuation;
        update({ open: true, saving: false, error: "" });
      });
    },
    async choose(choice: "stay" | "discard" | "save") {
      if (!resolve || state.saving) return;
      if (choice === "stay") { finish(false); return; }
      if (actions.pending()) {
        update({ ...state, error: "Результат сохранения ещё не подтверждён. Останьтесь в разделе и проверьте результат." });
        return;
      }
      if (!actions.dirty()) { finish(true); return; }
      if (choice === "discard") {
        try { actions.discard(); } catch {
          update({ ...state, error: "Не удалось восстановить сохранённые настройки. Правки оставлены в редакторе." });
          return;
        }
        if (actions.dirty()) {
          update({ ...state, error: "Не удалось отменить изменения. Останьтесь в редакторе и загрузите сохранённые настройки." });
          return;
        }
        finish(true);
        return;
      }
      if (!actions.canSave()) {
        update({ ...state, error: "Сохранение сейчас недоступно. Останьтесь в разделе и проверьте состояние настроек." });
        return;
      }
      update({ ...state, saving: true, error: "" });
      const continuation = resolve;
      try {
        const saved = await actions.save();
        if (resolve !== continuation) return;
        if (saved && !actions.dirty() && !actions.pending()) { finish(true); return; }
        update({ open: true, saving: false, error: "Сохранение не подтверждено. Изменения оставлены в редакторе." });
      } catch {
        if (resolve !== continuation) return;
        update({ open: true, saving: false, error: "Не удалось сохранить изменения. Правки оставлены в редакторе." });
      }
    },
    reconcile() {
      if (resolve && !state.saving && !actions.dirty() && !actions.pending()) finish(true);
    },
    dispose() { if (resolve) finish(false); },
  };
}
