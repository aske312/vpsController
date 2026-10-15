import type { NotificationInput } from "../shared/notifications/store";

import type { SystemAction } from "../shared/types/control-plane";
export type { SystemAction } from "../shared/types/control-plane";
const rollbackableActions = new Set(["update", "test-rollback", "safe-update", "kernel-update"]);

/** Validate fields consumed by notifications before publishing an API operation. */
export function systemActionSnapshot(value: unknown): SystemAction {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Некорректный ответ операции");
  const action = value as Record<string, unknown>;
  for (const key of ["id", "unit", "action", "state", "result", "started_at", "updated_at", "message"]) {
    if (action[key] !== undefined && typeof action[key] !== "string") throw new Error("Некорректные данные операции");
  }
  if (action.cancel_requested !== undefined && typeof action.cancel_requested !== "boolean") throw new Error("Некорректное состояние остановки операции");
  if (action.progress != null && (typeof action.progress !== "number" || !Number.isFinite(action.progress) || action.progress < 0 || action.progress > 100)) {
    throw new Error("Некорректный прогресс операции");
  }
  return action.progress === null ? { ...action, progress: undefined } as SystemAction : action as SystemAction;
}

export class SystemOperationUnknown extends Error {
  readonly id: string;
  constructor(id: string) {
    super(`Результат команды пока неизвестен. Проверьте центр операций перед следующим действием. ID: ${id}`);
    this.id = id;
  }
}

export function systemAdmissionSnapshot(value: unknown, id: string, previous?: SystemAction): SystemAction {
  const action = systemActionSnapshot(value);
  if (action.id !== id || !["queued", "active", "activating", "running", "unknown", "rebooting", "powering-off", "succeeded", "finished", "failed", "cancelled"].includes(action.state || "")
    || (previous?.unit && action.unit !== previous.unit) || (previous?.action && action.action !== previous.action)) throw new SystemOperationUnknown(id);
  return action.state === "succeeded" && action.result === "unknown" ? { ...action, state: "unknown" } : action;
}

const PENDING_ADMISSION_KEY = "system-pending-admission:v1";
export const SYSTEM_ADMISSION_CHANGED = "system-pending-admission-changed";

export function restoreSystemAdmission(value: unknown): SystemAction | null {
  if (!value || typeof value !== "object" || !("id" in value) || typeof value.id !== "string" || !/^[a-f0-9]{32}$/.test(value.id)) return null;
  if ("kind" in value && value.kind !== "cancel") return null;
  return { id: value.id, state: "unknown", ...("kind" in value ? { cancel_requested: true } : {}), message: "Приём команды пока не подтверждён. Проверяем прежний идентификатор без повторной отправки." };
}

export function readSystemAdmission(): SystemAction | null {
  if (typeof window === "undefined") return null;
  try { return restoreSystemAdmission(JSON.parse(window.sessionStorage.getItem(PENDING_ADMISSION_KEY) || "null")); }
  catch { return null; }
}

export function persistSystemAdmission(operation: SystemAction | null, completedId?: string) {
  if (typeof window === "undefined") return;
  const current = readSystemAdmission();
  if (!operation && current && current.id !== completedId) return;
  const descriptor = operation ? { id: operation.id, ...(operation.cancel_requested === true ? { kind: "cancel" } : {}) } : null;
  try {
    if (descriptor) window.sessionStorage.setItem(PENDING_ADMISSION_KEY, JSON.stringify(descriptor));
    else window.sessionStorage.removeItem(PENDING_ADMISSION_KEY);
  } catch { /* The active hook retains its guard without storage. */ }
  if (typeof window.dispatchEvent === "function") window.dispatchEvent(new window.CustomEvent(SYSTEM_ADMISSION_CHANGED, { detail: descriptor }));
}

export async function submitSystemOperation(request: (path: string, options?: RequestInit) => Promise<SystemAction>, path: string, options: RequestInit, identity?: string) {
  const id = identity ?? crypto.randomUUID().replaceAll("-", "");
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error("Некорректный идентификатор операции");
  const headers = new Headers(options.headers);
  headers.set("X-Operation-ID", id);
  const confirmed = (value: unknown) => systemAdmissionSnapshot(value, id);
  try {
    return confirmed(await request(path, {...options, headers}));
  } catch (cause) {
    if (cause && typeof cause === "object" && "status" in cause && typeof cause.status === "number" && cause.status >= 400 && cause.status < 500) throw cause;
    // A lost response is reconciled by identity, never by repeating a mutation.
    try { return confirmed(await request(`/application/operations/${id}`, { cache: "no-store", signal: options.signal })); }
    catch { throw new SystemOperationUnknown(id); }
  }
}

export function systemActionSucceeded(action: SystemAction) {
  return ["succeeded", "finished"].includes(action.state || "") && (!action.result || action.result === "success");
}

export function protocolOperationOutcome(started: SystemAction, current: SystemAction | undefined, moduleReady: boolean) {
  if (!started.unit || current?.unit !== started.unit) return "pending";
  if (["failed", "cancelled"].includes(current.state || "") || (current.result && !["success", "unknown"].includes(current.result))) return "failed";
  return systemActionSucceeded(current) && moduleReady ? "success" : "pending";
}

export function systemOperationId(action: SystemAction) {
  // API and shell record different start timestamps for the same systemd unit.
  return `operation:system:${action.id || action.unit || `${action.action}:${action.started_at || ""}`}`;
}

export function systemOperationNotification(action: SystemAction, title: string, active: boolean, onCancel?: () => void | Promise<void>, onRecheck?: () => void | Promise<void>): NotificationInput {
  const failed = action.state === "failed" || Boolean(action.result && !["success", "unknown"].includes(action.result));
  const done = systemActionSucceeded(action);
  const unconfirmed = action.state === "unknown" || ["succeeded", "finished"].includes(action.state || "") && action.result === "unknown";
  const cancelled = action.state === "cancelled";
  return {
    id: systemOperationId(action), source: "system", title, kind: "operation",
    state: cancelled ? "cancelled" : failed ? "error" : done ? "success" : unconfirmed ? "unknown" : active ? "running" : "unknown",
    progress: active && !unconfirmed && !cancelled ? action.progress : undefined,
    onCancel: active && !unconfirmed && !cancelled && action.cancel_requested !== true && /^[a-f0-9]{32}$/.test(action.id || "") && /^vps-control-action-[0-9]+\.service$/.test(action.unit || "") && rollbackableActions.has(action.action || "") ? onCancel : undefined,
    action: unconfirmed && onRecheck ? { label: "Проверить результат", run: onRecheck } : undefined,
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
    const terminal = systemActionSucceeded(action) || ["failed", "cancelled"].includes(action.state || "");
    const wasActive = tracked.get(id) === false;
    if (terminal || (active && !tracked.has(id))) tracked.set(id, terminal);
    if (tracked.size > 200) tracked.delete(tracked.keys().next().value!);
    if (terminal && wasActive && systemActionSucceeded(action)) return action;
  };
}

export function systemActionNeedsReload(action: SystemAction) {
  if (action.action === "mihomo-profile-personal-delete") return false;
  return Boolean(action.action) && !["mihomo-profile-module-settings", "mihomo-profile-create", "mihomo-profile-update", "mihomo-profile-delete", "mihomo-profile-device-delete", "mihomo-profile-reconcile", "mihomo-module-recover", "mihomo-module-install", "mihomo-module-update", "mihomo-module-remove", "network-check", "integrity-check", "poweroff", "logging-config", "logs-clear", "service-action", "automation-config", "automation-recover", "dns-settings", "dns-recover", "network-settings", "network-delete", "ssh-key-add", "ssh-key-reset", "ssh-key-delete", "ssh-access-begin", "ssh-access-confirm", "ssh-access-rollback", "ssh-access-disable"].includes(action.action!.split(":")[0]);
}

export function systemActionNeedsPolling(action?: SystemAction | null) {
  return Boolean(action && (["queued", "active", "activating", "running", "unknown", "rebooting", "powering-off"].includes(action.state || "")
    || ["succeeded", "finished"].includes(action.state || "") && action.result === "unknown"));
}
