"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createApiClient, mutationFailureState } from "../../shared/lib/api-request";
import { systemActionSnapshot, systemAdmissionSnapshot, systemActionNeedsPolling, systemOperationNotification, type SystemAction } from "../../control-panel/system-operation";
import { useNotifications, useNotifier } from "../../shared/notifications/notification-center";

export type PendingAction = { actor?: string; actor_name?: string; service: string; action: string; code: string; expires: number; operation_id?: string; changes?: Record<string, unknown> };
type Client = ReturnType<typeof createApiClient>;
export const confirmationKey = (action: PendingAction) => `${action.actor || "panel"}:${action.code}`;
type Queue = { actions: PendingAction[]; working: string[]; results: Record<string, string>;
  decide: (action: PendingAction, decision: "confirm" | "reject") => Promise<string | undefined>;
  ingest: (actor: string, actions: PendingAction[]) => void };
const Context = createContext<Queue | null>(null);
export function useAssistantConfirmations() {
  const value = useContext(Context);
  if (!value) throw new Error("AssistantConfirmations is required");
  return value;
}

export function AssistantConfirmations({ request, onOperation, children }: { request: Client; onOperation: (operation: SystemAction) => void; children: ReactNode }) {
  const notifications = useNotifications();
  const notify = useNotifier("nodera", "Nodera · Подтверждение");
  const [actions, setActions] = useState<PendingAction[]>([]);
  const [working, setWorking] = useState<string[]>([]);
  const [results, setResults] = useState<Record<string, string>>({});
  const writing = useRef(new Set<string>());
  const settled = useRef(new Set<string>());
  const generation = useRef(0);
  const activeRequest = useRef<Client | null>(request);
  const registered = useRef(new Set<string>());
  const tracked = useRef(new Map<string, { snapshot: SystemAction; title: string; reading: boolean; checked: number; terminal: boolean }>());
  const operation = useRef(onOperation);
  useEffect(() => { operation.current = onOperation; }, [onOperation]);
  const readOperation = useCallback(async function readOperation(id: string) {
    const item = tracked.current.get(id);
    if (!item || item.reading || activeRequest.current !== request) return;
    item.reading = true; item.checked = Date.now();
    try {
      const observed = await request(`/application/operations/${id}`, { cache: "no-store" });
      if (activeRequest.current !== request) return;
      item.snapshot = systemAdmissionSnapshot(observed, id, item.snapshot);
      item.terminal = !systemActionNeedsPolling(item.snapshot);
    } catch {
      if (activeRequest.current !== request) return;
      item.snapshot = { ...item.snapshot, state: "unknown", progress: undefined, message: "Результат команды пока не подтверждён. Повторно команду не отправляем." };
    } finally { item.reading = false; }
    if (activeRequest.current === request) notifications.upsert(systemOperationNotification(item.snapshot, item.title, true, undefined, () => readOperation(id)));
  }, [request, notifications]);
  const trackOperation = useCallback((snapshot: SystemAction, title: string) => {
    if (!snapshot.id || !/^[a-f0-9]{32}$/.test(snapshot.id) || tracked.current.has(snapshot.id)) return;
    tracked.current.set(snapshot.id, { snapshot, title, reading: false, checked: 0, terminal: !systemActionNeedsPolling(snapshot) });
    notifications.upsert(systemOperationNotification(snapshot, title, true, undefined, () => readOperation(snapshot.id!)));
    if (systemActionNeedsPolling(snapshot)) void readOperation(snapshot.id);
  }, [notifications, readOperation]);
  const ingest = useCallback((actor: string, incoming: PendingAction[]) => {
    generation.current++;
    setActions(current => [...current.filter(item => (item.actor || "panel") !== actor),
      ...incoming.map(item => ({ ...item, actor })).filter(item => !settled.current.has(confirmationKey(item)) && item.expires * 1000 > Date.now())]);
  }, []);
  const decide = useCallback(async (action: PendingAction, decision: "confirm" | "reject") => {
    const key = confirmationKey(action);
    if (activeRequest.current !== request || writing.current.has(key) || settled.current.has(key) || action.expires * 1000 <= Date.now()) return;
    writing.current.add(key); generation.current++;
    setWorking([...writing.current]);
    try {
      const result = await request<{ text: string; operation?: SystemAction | null }>("/assistant/service-confirm", {
        method: "POST", body: JSON.stringify({ code: action.code, decision, telegram_id: action.actor && action.actor !== "panel" ? Number(action.actor) : null }),
      });
      if (activeRequest.current !== request) return;
      settled.current.add(key);
      setActions(current => current.filter(item => confirmationKey(item) !== key));
      setResults(current => ({ ...current, [key]: result.text }));
      if (result.operation) {
        const snapshot = systemActionSnapshot(result.operation);
        trackOperation(snapshot, `Nodera · ${action.action} · ${action.service}`);
        operation.current(snapshot);
      }
      else notify.info(result.text);
      return result.text;
    } catch (cause) {
      if (activeRequest.current !== request) return;
      if (mutationFailureState(cause) === "unknown") {
        settled.current.add(key);
        setActions(current => current.filter(item => confirmationKey(item) !== key));
        const text = "Результат решения не подтверждён. Проверяем состояние без повторной отправки.";
        setResults(current => ({ ...current, [key]: text }));
        if (decision === "confirm" && action.operation_id) {
          const snapshot = { id: action.operation_id, state: "unknown", message: text };
          trackOperation(snapshot, `Nodera · ${action.action} · ${action.service}`);
          operation.current(snapshot);
        }
        else notify.error(text);
      } else notify.error(cause instanceof Error ? cause.message : "Действие не выполнено");
    } finally {
      if (activeRequest.current === request) { writing.current.delete(key); setWorking([...writing.current]); }
    }
  }, [request, notify, trackOperation]);
  useEffect(() => {
    activeRequest.current = request; generation.current++;
    const sessionOperations = tracked.current;
    writing.current.clear(); settled.current.clear(); tracked.current.clear();
    queueMicrotask(() => { if (activeRequest.current === request) { setActions([]); setWorking([]); setResults({}); } });
    let stopped = false, reading = false;
    const poll = async () => {
      if (reading || writing.current.size) return;
      reading = true;
      const version = generation.current;
      try {
        const response = await request<{ pending_actions?: PendingAction[]; pending_action?: PendingAction | null; operations?: { id: string; action: string; service: string }[] }>("/assistant/pending-action?all_actors=true", { cache: "no-store" });
        if (stopped || version !== generation.current || writing.current.size) return;
        const incoming = response.pending_actions ?? (response.pending_action ? [response.pending_action] : []);
        setActions(incoming.filter(item => item.expires * 1000 > Date.now() && !settled.current.has(confirmationKey(item))));
        for (const item of response.operations ?? []) trackOperation({ id: item.id, state: "unknown" }, `Nodera · ${item.action} · ${item.service}`);
      } catch { /* A temporary read failure does not discard pending decisions. */ }
      finally { reading = false; }
    };
    void poll();
    const timer = window.setInterval(() => {
      setActions(current => current.filter(item => item.expires * 1000 > Date.now()));
      void poll();
      for (const [id] of [...tracked.current].filter(([, item]) => !item.terminal && !item.reading).sort((a, b) => a[1].checked - b[1].checked).slice(0, 4)) void readOperation(id);
    }, 2000);
    return () => {
      stopped = true; window.clearInterval(timer); activeRequest.current = null;
      for (const id of sessionOperations.keys()) notifications.resolve(`operation:system:${id}`);
    };
  }, [request, trackOperation, readOperation, notifications]);
  useEffect(() => {
    const ids = new Set<string>();
    for (const action of actions) {
      const id = `nodera-confirm:${confirmationKey(action)}`;
      ids.add(id);
      notifications.upsert({ id, source: "nodera", title: "Nodera · Подтверждение", state: "info", kind: "confirmation",
        message: `${action.actor_name || "Администратор"}: ${action.action} · ${action.service}. До ${new Date(action.expires * 1000).toLocaleTimeString("ru-RU")}.`,
        details: action.changes ? JSON.stringify(action.changes, null, 2) : undefined,
        action: { label: "Подтвердить", run: async () => { await decide(action, "confirm"); } },
        secondaryAction: { label: "Отказать", run: async () => { await decide(action, "reject"); } } });
    }
    for (const id of registered.current) if (!ids.has(id)) notifications.resolve(id);
    registered.current = ids;
  }, [actions, notifications, decide]);
  useEffect(() => () => { for (const id of registered.current) notifications.resolve(id); }, [notifications, request]);
  const value = useMemo(() => ({ actions, working, results, decide, ingest }), [actions, working, results, decide, ingest]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function ConfirmationCards({ actor }: { actor: string }) {
  const queue = useAssistantConfirmations();
  const actions = queue.actions.filter(item => (item.actor || "panel") === (actor || "panel"));
  if (!actions.length) return null;
  return <section className="mcpConfirmations" role="region" aria-label="Подтверждение действий">
    <header><strong>Ожидают подтверждения · {actions.length}</strong><small>Каждое действие подтверждается отдельно</small></header>
    {actions.map(action => <article key={confirmationKey(action)} className="mcpConfirmationCard" aria-label={`${action.action} · ${action.service}`}>
      <div><strong>{action.action}</strong><span>{action.service}</span><small>До {new Date(action.expires * 1000).toLocaleTimeString("ru-RU")}</small></div>
      {action.changes && <details><summary>Изменения</summary><pre>{JSON.stringify(action.changes, null, 2)}</pre></details>}
      <footer><button type="button" className="primaryButton" disabled={queue.working.includes(confirmationKey(action))} onClick={() => void queue.decide(action, "confirm")}>Подтвердить</button>
        <button type="button" className="ghostButton" disabled={queue.working.includes(confirmationKey(action))} onClick={() => void queue.decide(action, "reject")}>Отказать</button></footer>
    </article>)}
  </section>;
}
