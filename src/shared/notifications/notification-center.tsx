"use client";

import { createContext, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { createNotificationStore, isPending, type Notification, type NotificationAction, type NotificationFailure, type NotificationState, type NotificationStore } from "./store";

const Context = createContext<NotificationStore | null>(null);
const EMPTY: Notification[] = [];
const serverSnapshot = () => EMPTY;

export function NotificationProvider({ children }: { children: ReactNode }) {
  const [store] = useState(() => createNotificationStore());
  useEffect(() => {
    const timer = window.setInterval(store.tick, 1000);
    return () => window.clearInterval(timer);
  }, [store]);
  return <Context.Provider value={store}>{children}<NotificationCenter /></Context.Provider>;
}

export function useNotifications() {
  const store = useContext(Context);
  if (!store) throw new Error("NotificationProvider is required");
  return store;
}

export function useNotificationList() {
  const store = useNotifications();
  return useSyncExternalStore(store.subscribe, store.getSnapshot, serverSnapshot);
}

export function useNotifier(source: string, title: string) {
  const store = useNotifications();
  return useMemo(() => ({
    error: (message: string) => { store.notify(source, title, "error", message); },
    success: (message: string) => { store.notify(source, title, "success", message); },
    info: (message: string) => { store.notify(source, title, "info", message); },
    operation: (id: string, label: string, state: NotificationState, message = "", onRecheck?: () => void) => {
      store.upsert({ id: `operation:${source}:${id}`, source, title: label, message, state, kind: "operation", action: onRecheck && state === "unknown" ? { label: "Проверить результат", run: onRecheck } : undefined });
    },
  }), [source, store, title]);
}

export function useFailureNotifications(source: string, title: string, failures: Record<string, NotificationFailure>, reconnecting = false, action?: NotificationAction) {
  const store = useNotifications();
  useEffect(() => { store.setFailures(source, title, failures, reconnecting, action); }, [store, source, title, failures, reconnecting, action]);
  useEffect(() => () => store.clearFailures(source), [store, source]);
}

function NotificationCenter() {
  const items = useNotificationList();
  const mounted = useSyncExternalStore(() => () => {}, () => true, () => false);
  if (!mounted || !items.length) return null;
  return <NotificationViewport items={items} />;
}

function NotificationViewport({ items }: { items: Notification[] }) {
  const store = useNotifications();
  const [expanded, setExpanded] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  useEffect(() => { if (hovered || focused) store.pause(); else store.resume(); }, [store, hovered, focused]);
  useEffect(() => () => store.resume(), [store]);
  return createPortal(
    <aside className={`gateOperationDock gateNotificationDock${expanded ? " is-expanded" : ""}`} aria-label="Уведомления и операции" onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)} onFocus={() => setFocused(true)} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false); }}>
      {items.length > 1 && <header className="gateNotificationHeader"><strong>Уведомления <span>{items.length}</span>{items.some(isPending) && <small>В работе: {items.filter(isPending).length}</small>}</strong><div><button type="button" aria-expanded={expanded} aria-controls="gate-notification-list" onClick={() => setExpanded((value) => !value)}>{expanded ? "Свернуть" : `Все (${items.length})`}</button>{items.some((item) => !isPending(item)) && <button type="button" onClick={store.clearCompleted} aria-label="Закрыть завершённые уведомления">Очистить</button>}</div></header>}
      <div id="gate-notification-list" className="gateNotificationList" tabIndex={0} aria-label="Список уведомлений">
        {[...items].reverse().map((item) => <NotificationCard key={item.id} item={item} />)}
      </div>
    </aside>, document.body,
  );
}

function NotificationCard({ item }: { item: Notification }) {
  const store = useNotifications();
  const [checking, setChecking] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const pending = isPending(item);
  const progress = Number.isFinite(item.progress) ? Math.max(0, Math.min(100, item.progress!)) : undefined;
  async function runAction() {
    if (checking || !item.action) return;
    setChecking(true);
    try { await item.action.run(); }
    catch (cause) { store.upsert({ ...item, state: "error", message: cause instanceof Error ? cause.message : "Не удалось выполнить действие" }); }
    finally { setChecking(false); }
  }
  async function cancelOperation() {
    if (canceling || !item.onCancel) return;
    setCanceling(true);
    try { await item.onCancel(); }
    catch (cause) { store.upsert({ ...item, state: "error", message: cause instanceof Error ? cause.message : "Не удалось остановить команду" }); }
    finally { setCanceling(false); }
  }
  return <section className={`gateOperationCard ${item.state}`} role={item.state === "error" ? "alert" : "status"} aria-atomic="true" aria-label={item.title}>
    <div className="gateOperationContent">
      {pending && item.onCancel && <button type="button" className="gateNotificationCancel" onClick={() => void cancelOperation()} disabled={canceling} aria-label={`Остановить и откатить: ${item.title}`}>{canceling ? "…" : "×"}</button>}
      <span className="gateOperationIcon" aria-hidden="true">{item.state === "error" ? "!" : item.state === "success" ? "✓" : pending ? "…" : "i"}</span>
      <div className="gateOperationText"><span>{item.kind === "operation" ? "ВЫПОЛНЕНИЕ КОМАНДЫ" : "УВЕДОМЛЕНИЕ"}{item.count > 1 ? ` · ×${item.count}` : ""}</span><strong>{item.title}</strong><small>{item.message}</small></div>
      {!pending && <button type="button" className="gateNotificationClose" onClick={() => store.dismiss(item.id)} aria-label={`Закрыть: ${item.title}`}>×</button>}
      {pending && <b className="gateOperationPercent">{progress === undefined || item.state === "unknown" ? "…" : `${progress}%`}</b>}
    </div>
    {item.kind === "operation" && <div className={`gateOperationTrack ${pending && (progress === undefined || item.state === "unknown") ? "indeterminate" : ""}`} aria-hidden="true"><i style={{ width: `${pending ? progress ?? 34 : 100}%` }} /></div>}
    {item.action && <button type="button" className="gateOperationButton" disabled={checking || item.state === "running"} onClick={() => void runAction()}>{checking ? "Проверяем…" : item.action.label}</button>}
  </section>;
}
