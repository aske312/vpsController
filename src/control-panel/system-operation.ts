import type { NotificationInput } from "../shared/notifications/store";

import type { SystemAction } from "../shared/types/control-plane";
export type { SystemAction } from "../shared/types/control-plane";
const rollbackableActions = new Set(["update", "test-update", "test-rollback", "safe-update", "kernel-update"]);

export async function submitSystemOperation(request: (path: string, options?: RequestInit) => Promise<SystemAction>, path: string, options: RequestInit) {
  const id = crypto.randomUUID().replaceAll("-", "");
  const headers = new Headers(options.headers);
  headers.set("X-Operation-ID", id);
  try {
    return await request(path, {...options, headers});
  } catch (cause) {
    if (cause && typeof cause === "object" && "status" in cause && typeof cause.status === "number" && cause.status >= 400 && cause.status < 500) throw cause;
    // A lost response is reconciled by identity, never by repeating a mutation.
    try { return await request(`/application/operations/${id}`); }
    catch { throw new Error(`Результат команды пока неизвестен. Проверьте центр операций перед следующим действием. ID: ${id}`); }
  }
}

export function systemActionSucceeded(action: SystemAction) {
  return ["succeeded", "finished"].includes(action.state || "") && (!action.result || action.result === "success");
}

export function protocolOperationOutcome(started: SystemAction, current: SystemAction | undefined, moduleReady: boolean) {
  if (!started.unit || current?.unit !== started.unit) return "pending";
  if (current.state === "failed" || (current.result && !["success", "unknown"].includes(current.result))) return "failed";
  return systemActionSucceeded(current) && moduleReady ? "success" : "pending";
}

export function systemOperationId(action: SystemAction) {
  // API and shell record different start timestamps for the same systemd unit.
  return `operation:system:${action.unit || `${action.action}:${action.started_at || ""}`}`;
}

export function systemOperationNotification(action: SystemAction, title: string, active: boolean, onCancel?: () => void | Promise<void>): NotificationInput {
  const failed = action.state === "failed" || Boolean(action.result && !["success", "unknown"].includes(action.result));
  const done = systemActionSucceeded(action);
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
    const terminal = systemActionSucceeded(action) || action.state === "failed";
    const wasActive = tracked.get(id) === false;
    if (terminal || (active && !tracked.has(id))) tracked.set(id, terminal);
    if (tracked.size > 200) tracked.delete(tracked.keys().next().value!);
    if (terminal && wasActive && systemActionSucceeded(action)) return action;
  };
}

export function systemActionNeedsReload(action: SystemAction) {
  return Boolean(action.action) && !["mihomo-profile-module-settings", "mihomo-profile-create", "mihomo-profile-update", "mihomo-profile-delete", "mihomo-profile-device-delete", "mihomo-profile-reconcile", "mihomo-module-recover", "mihomo-module-install", "mihomo-module-update", "mihomo-module-remove", "network-check", "integrity-check", "poweroff", "logging-config", "logs-clear", "service-action", "automation-config", "automation-recover", "dns-settings", "dns-recover", "network-settings", "network-delete", "ssh-key-add", "ssh-key-reset", "ssh-key-delete", "ssh-access-begin", "ssh-access-confirm", "ssh-access-rollback", "ssh-access-disable"].includes(action.action!.split(":")[0]);
}

export function systemActionNeedsPolling(action?: SystemAction | null) {
  return Boolean(action && (["queued", "active", "activating", "running", "unknown", "rebooting", "powering-off"].includes(action.state || "")
    || action.state === "finished" && action.result === "unknown"));
}
