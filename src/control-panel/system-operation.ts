import type { NotificationInput } from "../shared/notifications/store";

export type SystemAction = { unit?: string; action?: string; state?: string; result?: string; started_at?: string; progress?: number; message?: string };

export function systemOperationId(action: SystemAction) {
  // API and shell record different start timestamps for the same systemd unit.
  return `operation:system:${action.unit || `${action.action}:${action.started_at || ""}`}`;
}

export function systemOperationNotification(action: SystemAction, title: string, active: boolean): NotificationInput {
  const failed = action.state === "failed" || action.result === "failed";
  const done = ["succeeded", "finished"].includes(action.state || "");
  return {
    id: systemOperationId(action), source: "system", title, kind: "operation",
    state: failed ? "error" : done ? "success" : active ? "running" : "unknown",
    progress: active ? action.progress : undefined,
    message: failed ? action.message?.trim() || "Команда завершилась с ошибкой." : "",
  };
}
