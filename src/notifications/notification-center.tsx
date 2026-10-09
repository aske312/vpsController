"use client";

import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { createNotificationStore, isPending, type Notification, type NotificationStore } from "./store";
import { operationStage } from "./operation-stage";

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

function useNotificationList() {
  const store = useNotifications();
  return useSyncExternalStore(store.subscribe, store.getSnapshot, serverSnapshot);
}

export function useOperationCount() {
  return useNotificationList().filter((item) => item.kind === "operation" && isPending(item)).length;
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
      <header className="gateNotificationHeader"><strong>{items.some((item) => item.kind === "operation") ? "Процессы" : "Уведомления"} <span>{items.length}</span></strong><div><button type="button" aria-expanded={expanded} aria-controls="gate-notification-list" onClick={() => setExpanded((value) => !value)}>{expanded ? "Свернуть" : "Все"}</button>{items.some((item) => !isPending(item)) && <button type="button" onClick={store.clearCompleted}>Очистить</button>}</div></header>
      <div id="gate-notification-list" className="gateNotificationList" tabIndex={0} aria-label="Список уведомлений">
        {[...items].reverse().sort((a, b) => Number(isPending(b)) - Number(isPending(a))).map((item) => <NotificationCard key={item.id} item={item} />)}
      </div>
    </aside>,
    document.body,
  );
}

function NotificationCard({ item }: { item: Notification }) {
  const store = useNotifications();
  const [hidden, setHidden] = useState(false);
  const pending = isPending(item);
  const progress = item.state !== "unknown" && Number.isFinite(item.progress) ? Math.max(0, Math.min(100, item.progress!)) : undefined;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [pending]);
  const elapsed = Math.max(0, Math.floor((now - (item.startedAt ?? item.createdAt)) / 1000));
  const message = item.message.replace(/ветки test-light/g, "тестовой версии").replace(/ветки light/g, "стабильной версии");
  const stage = item.kind === "operation" ? operationStage(message, item.state) : message;
  if (hidden && pending) return <section className={`gateOperationCard collapsed ${item.state}`} aria-label={item.title}>
    <div className="gateOperationContent"><div className="gateOperationText"><strong>{item.title}</strong><small>{stage}</small><span className="gateOperationPercent">{progress === undefined ? "Ожидание" : `${progress}%`}</span></div><button type="button" className="gateOperationButton" onClick={() => setHidden(false)}>Показать</button></div>
  </section>;
  return <section className={`gateOperationCard ${item.state}`} role={item.state === "error" ? "alert" : "status"} aria-atomic="true" aria-label={item.title}>
    <div className="gateOperationContent">
      <span className="gateOperationIcon" aria-hidden="true">{item.state === "error" ? "!" : item.state === "success" ? "✓" : pending ? "…" : "i"}</span>
      <div className="gateOperationText"><strong>{item.title}{item.count > 1 ? ` ×${item.count}` : ""}</strong><small>{stage}</small>{pending && item.kind === "operation" && <span aria-live="off">{elapsed < 60 ? `${elapsed} с` : `${Math.floor(elapsed / 60)} мин ${elapsed % 60} с`}</span>}{item.kind === "operation" && stage !== message && item.state !== "error" && <details className="gateOperationDetails"><summary>Подробности</summary><p>{message}</p></details>}</div>
      <div className="gateOperationActions">
        {pending && <b className="gateOperationPercent" title="Общий прогресс операции">{progress === undefined ? "Ожидание" : `${progress}%`}</b>}
        {pending && <button type="button" className="gateOperationButton" onClick={() => setHidden(true)}>Скрыть</button>}
        {!pending && <button type="button" className="gateNotificationClose" onClick={() => store.dismiss(item.id)} aria-label={`Закрыть: ${item.title}`}>×</button>}
      </div>
    </div>
    {item.kind === "operation" && <div className={`gateOperationTrack ${pending && progress === undefined ? "indeterminate" : ""}`} role="progressbar" aria-label={`Общий прогресс: ${item.title}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pending ? progress : item.state === "success" ? 100 : progress} aria-valuetext={message}><i style={{ width: `${pending ? progress ?? 34 : item.state === "error" ? progress ?? 0 : 100}%` }} /></div>}
  </section>;
}
