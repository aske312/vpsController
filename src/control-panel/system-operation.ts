import type { NotificationInput } from "../shared/notifications/store";

export type SystemAction = { unit?: string; action?: string; state?: string; result?: string; started_at?: string; progress?: number; message?: string };
const rollbackableActions = new Set(["update", "test-update", "test-rollback", "safe-update", "kernel-update"]);

export function systemOperationId(action: SystemAction) {
  // API and shell record different start timestamps for the same systemd unit.
  return `operation:system:${action.unit || `${action.action}:${action.started_at || ""}`}`;
}

export function systemOperationNotification(action: SystemAction, title: string, active: boolean, onCancel?: () => void | Promise<void>): NotificationInput {
  const failed = action.state === "failed" || action.result === "failed";
  const done = ["succeeded", "finished"].includes(action.state || "");
  return {
    id: systemOperationId(action), source: "system", title, kind: "operation",
    state: failed ? "error" : done ? "success" : active ? "running" : "unknown",
    progress: active ? action.progress : undefined,
    onCancel: active && rollbackableActions.has(action.action || "") ? onCancel : undefined,
    message: failed ? action.message?.trim() || "Команда завершилась с ошибкой." : "",
  };
}

/** Ignore historical results and repeated polls, including late active snapshots. */
export function createSystemActionCompletionTracker() {
  const tracked = new Map<string, boolean>();
  return (action?: SystemAction | null) => {
    if (!action?.unit) return;
    const id = systemOperationId(action);
    const active = ["queued", "active", "activating", "running", "rebooting", "powering-off"].includes(action.state || "");
    const terminal = ["succeeded", "finished", "failed"].includes(action.state || "");
    const wasActive = tracked.get(id) === false;
    if (terminal || (active && !tracked.has(id))) tracked.set(id, terminal);
    if (tracked.size > 200) tracked.delete(tracked.keys().next().value!);
    if (terminal && wasActive && action.state !== "failed" && action.result !== "failed") return action;
  };
}

export function systemActionNeedsReload(action: SystemAction) {
  return Boolean(action.action) && !["network-check", "integrity-check", "poweroff"].includes(action.action!.split(":")[0]);
}
