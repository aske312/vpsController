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

export function useOperationCount() {
  return useNotificationList().filter((item) => item.kind === "operation" && isPending(item)).length;
}

export function OperationNavigation({ onOpenJournal }: { onOpenJournal: () => void }) {
  const items = useNotificationList().filter((item) => item.kind === "operation");
  const [expanded, setExpanded] = useState(true);
  const pending = items.filter(isPending);
  if (!items.length) return null;
  const latest = pending[pending.length - 1] || items[items.length - 1];
  return <section className="operationNavigation" aria-label="Процессы сервера">
    <header>
      <div><strong>Процессы сервера</strong><span>{pending.length ? `В работе: ${pending.length}` : latest.state === "error" ? "Требует внимания" : "Завершено"}</span></div>
      <div><button type="button" className="miniButton" onClick={onOpenJournal}>Открыть журнал</button><button type="button" className="miniButton" aria-expanded={expanded} aria-controls="server-operation-list" onClick={() => setExpanded((value) => !value)}>{expanded ? "Свернуть" : "Подробнее"}</button></div>
    </header>
    {expanded ? <div id="server-operation-list" className="operationNavigationList">{[...items].reverse().map((item) => <NotificationCard key={item.id} item={item} inline />)}</div>
      : <div id="server-operation-list" className="operationSummary"><strong>{latest.title}</strong><span>{latest.message}</span>{pending.length > 0 && <small>Операция продолжается на сервере</small>}</div>}
  </section>;
}

function NotificationCenter() {
  const items = useNotificationList().filter((item) => item.kind !== "operation");
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

function NotificationCard({ item, inline = false }: { item: Notification; inline?: boolean }) {
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
  if (hidden && pending) return <section className={`gateOperationCard collapsed ${item.state}`} aria-label={item.title}>
    <div className="gateOperationContent"><div className="gateOperationText"><strong>{item.title}</strong><small>Операция продолжается</small></div><button type="button" className="gateOperationButton" onClick={() => setHidden(false)}>Показать</button></div>
  </section>;
  return <section className={`gateOperationCard ${item.state}`} role={item.state === "error" ? "alert" : "status"} aria-atomic="true" aria-label={item.title}>
    <div className="gateOperationContent">
      <span className="gateOperationIcon" aria-hidden="true">{item.state === "error" ? "!" : item.state === "success" ? "✓" : pending ? "…" : "i"}</span>
      <div className="gateOperationText"><span>{item.kind === "operation" ? item.state === "error" ? "ОШИБКА" : item.state === "success" ? "УСПЕШНО ЗАВЕРШЕНО" : item.state === "unknown" ? "ПРОВЕРЯЕМ РЕЗУЛЬТАТ" : "ВЫПОЛНЯЕТСЯ НА СЕРВЕРЕ" : "УВЕДОМЛЕНИЕ"}{item.count > 1 ? ` · ×${item.count}` : ""}</span><strong>{item.title}</strong><small>{message}</small>{pending && inline && <span>Прошло {elapsed < 60 ? `${elapsed} с` : `${Math.floor(elapsed / 60)} мин ${elapsed % 60} с`} · можно переходить между разделами</span>}</div>
      <div className="gateOperationActions">
        {pending && <b className="gateOperationPercent" title="Общий прогресс операции">{progress === undefined ? "Ожидание" : `${progress}%`}</b>}
        {pending && !inline && <button type="button" className="gateOperationButton" onClick={() => setHidden(true)}>Скрыть</button>}
        {!pending && <button type="button" className="gateNotificationClose" onClick={() => store.dismiss(item.id)} aria-label={`Закрыть: ${item.title}`}>×</button>}
      </div>
    </div>
    {item.kind === "operation" && <div className={`gateOperationTrack ${pending && progress === undefined ? "indeterminate" : ""}`} role="progressbar" aria-label={`Общий прогресс: ${item.title}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pending ? progress : item.state === "success" ? 100 : progress} aria-valuetext={message}><i style={{ width: `${pending ? progress ?? 34 : item.state === "error" ? progress ?? 0 : 100}%` }} /></div>}
  </section>;
}
