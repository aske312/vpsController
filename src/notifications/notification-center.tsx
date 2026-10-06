"use client";

import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { createNotificationStore, isPending, type Notification, type NotificationStore } from "./store";

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
      {items.length > 1 && <header className="gateNotificationHeader"><strong>Уведомления <span>{items.length}</span>{items.some(isPending) && <small>В работе: {items.filter(isPending).length}</small>}</strong><div><button type="button" aria-expanded={expanded} aria-controls="gate-notification-list" onClick={() => setExpanded((value) => !value)}>{expanded ? "Свернуть" : `Все (${items.length})`}</button>{items.some((item) => !isPending(item)) && <button type="button" onClick={store.clearCompleted}>Очистить</button>}</div></header>}
      <div id="gate-notification-list" className="gateNotificationList" tabIndex={0} aria-label="Список уведомлений">
        {[...items].reverse().map((item) => <NotificationCard key={item.id} item={item} />)}
      </div>
    </aside>,
    document.body,
  );
}

function NotificationCard({ item }: { item: Notification }) {
  const store = useNotifications();
  const [hidden, setHidden] = useState(false);
  const pending = isPending(item);
  const progress = Number.isFinite(item.progress) ? Math.max(0, Math.min(100, item.progress!)) : undefined;
  if (hidden && pending) return <section className={`gateOperationCard ${item.state}`} aria-label={item.title}>
    <div className="gateOperationContent"><div className="gateOperationText"><strong>{item.title}</strong><small>Операция продолжается</small></div><button type="button" className="gateOperationButton" onClick={() => setHidden(false)}>Показать</button></div>
  </section>;
  return <section className={`gateOperationCard ${item.state}`} role={item.state === "error" ? "alert" : "status"} aria-atomic="true" aria-label={item.title}>
    <div className="gateOperationContent">
      <span className="gateOperationIcon" aria-hidden="true">{item.state === "error" ? "!" : item.state === "success" ? "✓" : pending ? "…" : "i"}</span>
      <div className="gateOperationText"><span>{item.kind === "operation" ? "ВЫПОЛНЕНИЕ КОМАНДЫ" : "УВЕДОМЛЕНИЕ"}{item.count > 1 ? ` · ×${item.count}` : ""}</span><strong>{item.title}</strong><small>{item.message}</small></div>
      <div className="gateOperationActions">
        {pending && <b className="gateOperationPercent">{progress === undefined || item.state === "unknown" ? "…" : `${progress}%`}</b>}
        {pending && <button type="button" className="gateOperationButton" onClick={() => setHidden(true)}>Скрыть</button>}
        {!pending && <button type="button" className="gateNotificationClose" onClick={() => store.dismiss(item.id)} aria-label={`Закрыть: ${item.title}`}>×</button>}
      </div>
    </div>
    {item.kind === "operation" && <div className={`gateOperationTrack ${pending && progress === undefined ? "indeterminate" : ""}`} aria-hidden="true"><i style={{ width: `${pending ? progress ?? 34 : 100}%` }} /></div>}
  </section>;
}
