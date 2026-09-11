"use client";

import { useEffect, useRef } from "react";
import type { CdnOperation } from "../../shared/lib/cdn-security-operation";
import { useNotifications } from "../../shared/notifications/notification-center";
import { systemOperationId, systemOperationNotification, type SystemAction } from "../system-operation";

type Props = { action?: SystemAction | null; label?: string; active: boolean; command?: CdnOperation | null; onRecheck?: () => void; onDismiss?: () => void; onCancel?: () => void | Promise<void> };

/** Adapt polled server state to the shared center; this component has no UI. */
export function OperationNotifications({ action, label, active, command, onRecheck, onDismiss, onCancel }: Props) {
  const store = useNotifications();
  const tracked = useRef(new Set<string>());
  useEffect(() => {
    if (!action) return;
    const id = systemOperationId(action);
    if (active) tracked.current.add(id);
    if (!tracked.current.has(id)) return;
    const title = label || action.action || "Системная операция";
    store.upsert(systemOperationNotification(action, title, active, onCancel));
  }, [store, action, label, active, onCancel]);
  useEffect(() => {
    if (!command) return;
    store.upsert({ id: `operation:cdn:${command.id}`, source: "cdn", title: "Проверка сертификата Cloudflare", message: command.message,
      state: command.state === "succeeded" ? "success" : command.state === "failed" ? "error" : command.state === "unknown" ? "unknown" : "running",
      kind: "operation", progress: command.state === "queued" ? undefined : command.progress,
      action: command.state === "unknown" && onRecheck ? { label: "Проверить результат", run: onRecheck } : undefined, onDismiss });
  }, [store, command, onRecheck, onDismiss]);
  return null;
}
