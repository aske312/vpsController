"use client";

import { useEffect, useRef } from "react";
import type { CdnOperation } from "../../shared/lib/cdn-security-operation";
import { useNotifications } from "../../shared/notifications/notification-center";

type SystemAction = { unit?: string; action?: string; state?: string; result?: string; started_at?: string; progress?: number; message?: string };
type Props = { action?: SystemAction | null; label?: string; active: boolean; command?: CdnOperation | null; onRecheck?: () => void; onDismiss?: () => void };

/** Adapt polled server state to the shared center; this component has no UI. */
export function OperationNotifications({ action, label, active, command, onRecheck, onDismiss }: Props) {
  const store = useNotifications();
  const tracked = useRef(new Set<string>());
  useEffect(() => {
    if (!action) return;
    const id = `operation:system:${action.unit || action.action}:${action.started_at || ""}`;
    if (active) tracked.current.add(id);
    if (!tracked.current.has(id)) return;
    const failed = action.state === "failed" || action.result === "failed";
    const done = ["succeeded", "finished"].includes(action.state || "");
    const state = failed ? "error" : done ? "success" : active ? "running" : "unknown";
    const title = label || action.action || "Системная операция";
    store.upsert({ id, source: "system", title, state, kind: "operation", progress: active ? action.progress : undefined,
      message: failed ? `${title}: выполнение завершилось с ошибкой` : done ? `${title}: успешно завершено` : active ? "Команда выполняется. Итог появится после завершения." : "Результат команды пока не подтверждён." });
  }, [store, action, label, active]);
  useEffect(() => {
    if (!command) return;
    store.upsert({ id: `operation:cdn:${command.id}`, source: "cdn", title: "Проверка сертификата Cloudflare", message: command.message,
      state: command.state === "succeeded" ? "success" : command.state === "failed" ? "error" : command.state === "unknown" ? "unknown" : "running",
      kind: "operation", progress: command.state === "queued" ? undefined : command.progress,
      action: command.state === "unknown" && onRecheck ? { label: "Проверить результат", run: onRecheck } : undefined, onDismiss });
  }, [store, command, onRecheck, onDismiss]);
  return null;
}
