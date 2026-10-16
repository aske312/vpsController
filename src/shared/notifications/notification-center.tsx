"use client";

import { createContext, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { createNotificationStore, isPending, type Notification, type NotificationAction, type NotificationFailure, type NotificationState, type NotificationStore } from "./store";
import { notificationDuration, notificationPresentation, orderedNotifications } from "./operation-presentation";

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
  const [now, setNow] = useState(() => Date.now());
  const pending = items.some(isPending);
  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [pending]);
  useEffect(() => { if (hovered || focused) store.pause(); else store.resume(); }, [store, hovered, focused]);
  useEffect(() => () => store.resume(), [store]);
  return createPortal(
    <aside className={`gateOperationDock gateNotificationDock${expanded ? " is-expanded" : ""}`} aria-label="Уведомления и операции" onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)} onFocus={() => setFocused(true)} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false); }}>
      {items.length > 1 && <header className="gateNotificationHeader"><strong>{items.some(item => item.kind === "operation") ? "Процессы и уведомления" : "Уведомления"} <span>{items.length}</span>{pending && <small>В работе: {items.filter(item => item.state === "running").length} · результат уточняется: {items.filter(item => item.state === "unknown").length}</small>}</strong><div><button type="button" aria-expanded={expanded} aria-controls="gate-notification-list" onClick={() => setExpanded((value) => !value)}>{expanded ? "Свернуть" : `Все (${items.length})`}</button>{items.some((item) => !isPending(item)) && <button type="button" onClick={store.clearCompleted} aria-label="Закрыть завершённые уведомления">Очистить</button>}</div></header>}
      <div id="gate-notification-list" className="gateNotificationList" tabIndex={0} aria-label="Список уведомлений">
        {orderedNotifications(items).map((item) => <NotificationCard key={item.id} item={item} now={now} />)}
      </div>
    </aside>, document.body,
  );
}

function NotificationCard({ item, now }: { item: Notification; now: number }) {
  const store = useNotifications();
  const [checking, setChecking] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const pending = isPending(item);
  const presentation = notificationPresentation(item);
  const { progress } = presentation;
  async function runAction() {
    if (checking || !item.action) return;
    setChecking(true);
    try { await item.action.run(); }
    catch (cause) {
      const current = store.getSnapshot().find(entry => entry.id === item.id);
      if (current && isPending(current)) store.upsert({ ...current, state: "unknown", progress: undefined, onCancel: undefined,
        message: `Не удалось подтвердить результат. ${cause instanceof Error ? cause.message : "Повторите чтение состояния."}` });
      else if (current?.kind !== "operation" && current?.state === item.state) store.upsert({ ...current, state: "error", message: cause instanceof Error ? cause.message : "Не удалось выполнить действие" });
    }
    finally { setChecking(false); }
  }
  async function cancelOperation() {
    if (canceling || !item.onCancel) return;
    setCanceling(true);
    try { await item.onCancel(); }
    catch (cause) { store.notify(item.source, "Остановка операции", "error", cause instanceof Error ? cause.message : "Не удалось подтвердить остановку команды; проверьте её результат"); }
    finally { setCanceling(false); }
  }
  if (hidden && pending) return <section className={`gateOperationCard collapsed ${item.state}`} aria-label={item.title}>
    <div className="gateOperationContent"><div className="gateOperationText"><strong>{item.title}</strong><small>{presentation.label} · {presentation.summary}</small></div><button type="button" className="gateOperationButton" onClick={() => setHidden(false)}>Показать</button></div>
  </section>;
  return <section className={`gateOperationCard ${item.state}`} role={item.state === "error" ? "alert" : "status"} aria-atomic="true" aria-label={item.title}>
    <div className="gateOperationContent">
      <span className="gateOperationIcon" title={presentation.label} aria-hidden="true">{item.state === "running" ? <i className="gateOperationSpinner" /> : item.state === "unknown" ? "?" : item.state === "error" ? "!" : item.state === "success" ? "✓" : item.state === "cancelled" ? "−" : "i"}</span>
      <div className="gateOperationText"><strong>{item.title}{item.count > 1 ? ` ×${item.count}` : ""}</strong><small title={presentation.summary}>{presentation.summary}</small></div>
      {item.kind === "operation" && item.state === "running" && <b className="gateOperationPercent" title={progress === undefined ? "Сервер не передал процент" : "Прогресс выполнения"}>{progress === undefined ? "—" : `${progress}%`}</b>}
      {item.state === "unknown" && item.action && <button type="button" className="gateNotificationMore" disabled={checking} aria-label={item.action.label} title={item.action.label} onClick={() => void runAction()}>↻</button>}
      <button type="button" className="gateNotificationMore" aria-expanded={detailsOpen} aria-label={`Подробности: ${item.title}`} title="Подробности и действия" onClick={() => setDetailsOpen(value => !value)}>···</button>
      {!pending && <button type="button" className="gateNotificationClose" onClick={() => store.dismiss(item.id)} aria-label={`Закрыть: ${item.title}`}>×</button>}
    </div>
    {detailsOpen && <div className="gateOperationBody">
      <p className="gateOperationStage">{presentation.details || presentation.summary}</p>
      {item.kind === "operation" && <div className="gateOperationMeta"><span>{presentation.label}</span><span aria-live="off">{pending ? "Прошло" : "Длительность"}: {notificationDuration(item, now)}</span></div>}
      {item.state === "unknown" && item.kind === "operation" && <small className="gateOperationUnknown">Проверяем прежнюю команду без повторного запуска.</small>}
    </div>}
    {item.kind === "operation" && item.state === "running" && <div className={`gateOperationTrack${progress === undefined ? " indeterminate" : ""}`} role="progressbar" aria-label={`Прогресс: ${item.title}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress} aria-valuetext={presentation.summary}><i style={{ width: `${progress ?? 34}%` }} /></div>}
    {detailsOpen && (pending || item.action) && <div className="gateOperationActions">
      {item.action && item.state !== "unknown" && <button type="button" className="gateOperationButton" disabled={checking || item.state === "running"} onClick={() => void runAction()}>{checking ? "Проверяем…" : item.action.label}</button>}
      {item.state === "running" && item.onCancel && <button type="button" className="gateNotificationCancel" onClick={() => void cancelOperation()} disabled={canceling} aria-label={`Остановить и откатить: ${item.title}`}>{canceling ? "Останавливаем…" : "Остановить / откатить"}</button>}
      {pending && <button type="button" className="gateOperationButton" onClick={() => setHidden(true)} aria-label={`Скрыть карточку без остановки: ${item.title}`}>Скрыть</button>}
    </div>}
  </section>;
}
