"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createApiClient, mutationFailureState } from "../../shared/lib/api-request";
import { systemActionSnapshot, systemAdmissionSnapshot, systemActionNeedsPolling, systemOperationNotification, type SystemAction } from "../../control-panel/system-operation";
import { useNotifications, useNotifier } from "../../shared/notifications/notification-center";

export type PendingAction = { actor?: string; actor_name?: string; service: string; action: string; code: string; expires: number; operation_id?: string; changes?: Record<string, unknown>; confirmation_request?: { title: string; reason: string; request_id?: string; operation_ids?: string[] } };
type Client = ReturnType<typeof createApiClient>;
export const confirmationKey = (action: PendingAction) => `${action.actor || "panel"}:${action.code}`;
export type ConfirmationGroup = { key: string; actions: PendingAction[]; presentation?: PendingAction["confirmation_request"] };
export function confirmationGroups(actions: PendingAction[]): ConfirmationGroup[] {
  const groups = new Map<string, ConfirmationGroup>();
  for (const action of actions) {
    const presentation = action.confirmation_request;
    const grouped = presentation?.request_id && /^[a-f0-9]{32}$/.test(presentation.request_id) && action.operation_id && presentation.operation_ids?.includes(action.operation_id);
    const key = grouped ? `plan:${action.actor || "panel"}:${presentation.request_id}` : confirmationKey(action);
    const group = groups.get(key) ?? { key, actions: [], presentation: grouped ? presentation : undefined };
    group.actions.push(action); groups.set(key, group);
  }
  for (const group of groups.values()) if (group.presentation) group.actions.sort((a, b) => group.presentation!.operation_ids!.indexOf(a.operation_id!) - group.presentation!.operation_ids!.indexOf(b.operation_id!));
  return [...groups.values()];
}

type DecisionContext = { key: string; actor: string; question: string; answer: string };
type Queue = { context: DecisionContext[]; actions: PendingAction[]; working: string[]; results: Record<string, string>; errors: Record<string, string>;
  decide: (action: PendingAction, decision: "confirm" | "reject") => Promise<string | undefined>;
  decideGroup: (group: ConfirmationGroup, decision: "confirm" | "reject") => Promise<void>;
  ingest: (actor: string, actions: PendingAction[]) => void };
const Context = createContext<Queue | null>(null);
export function useAssistantConfirmations() {
  const value = useContext(Context);
  if (!value) throw new Error("AssistantConfirmations is required");
  return value;
}

export function AssistantConfirmations({ request, onOperation, onChanged, children }: { request: Client; onOperation: (operation: SystemAction) => void; onChanged?: () => void; children: ReactNode }) {
  const notifications = useNotifications();
  const notify = useNotifier("nodera", "Nodera · Подтверждение");
  const [context, setContext] = useState<DecisionContext[]>([]);
  const [actions, setActions] = useState<PendingAction[]>([]);
  const [working, setWorking] = useState<string[]>([]);
  const [results, setResults] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const writing = useRef(new Set<string>());
  const batching = useRef(new Set<string>());
  const currentActions = useRef(actions);
  currentActions.current = actions;
  const settled = useRef(new Set<string>());
  const generation = useRef(0);
  const activeRequest = useRef<Client | null>(request);
  const registered = useRef(new Set<string>());
  const tracked = useRef(new Map<string, { snapshot: SystemAction; title: string; reading: boolean; checked: number; terminal: boolean }>());
  const operation = useRef(onOperation);
  const changed = useRef(onChanged);
  useEffect(() => { changed.current = onChanged; }, [onChanged]);
  useEffect(() => { operation.current = onOperation; }, [onOperation]);
  const readOperation = useCallback(async function readOperation(id: string) {
    const item = tracked.current.get(id);
    if (!item || item.reading || activeRequest.current !== request) return;
    item.reading = true; item.checked = Date.now();
    try {
      const observed = await request(`/application/operations/${id}`, { cache: "no-store" });
      if (activeRequest.current !== request) return;
      const previousState = item.snapshot.state;
      item.snapshot = systemAdmissionSnapshot(observed, id, item.snapshot);
      item.terminal = !systemActionNeedsPolling(item.snapshot);
      if (item.snapshot.state === "succeeded" && previousState !== "succeeded") changed.current?.();
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
  const decideOne = useCallback(async (action: PendingAction, decision: "confirm" | "reject", batchKey?: string) => {
    const key = confirmationKey(action);
    const planKey = confirmationGroups([action])[0].key;
    if (batching.current.has(planKey) && batchKey !== planKey) return;
    if (activeRequest.current !== request || writing.current.has(key) || settled.current.has(key) || action.expires * 1000 <= Date.now()) return;
    writing.current.add(key); generation.current++;
    setErrors(current => { const next = { ...current }; delete next[key]; return next; });
    setWorking([...writing.current, ...batching.current]);
    try {
      const result = await request<{ text: string; display_text?: string; operation?: SystemAction | null }>("/assistant/service-confirm", {
        method: "POST", body: JSON.stringify({ code: action.code, decision, telegram_id: action.actor && action.actor !== "panel" ? Number(action.actor) : null }),
      });
      if (activeRequest.current !== request) return;
      setContext(current => [...current, { key, actor: action.actor || "panel", question: `${decision === "confirm" ? "Подтверждено" : "Отклонено"}: ${action.confirmation_request?.title ? action.confirmation_request.title + ": " : ""}${action.action} · ${action.service}. Операция ${action.operation_id || ""}`, answer: result.display_text || result.text }].slice(-32));
      settled.current.add(key);
      setActions(current => current.filter(item => confirmationKey(item) !== key));
      setResults(current => ({ ...current, [key]: result.display_text || result.text }));
      if (result.operation) {
        const snapshot = systemActionSnapshot(result.operation);
        trackOperation(snapshot, `Nodera · ${action.action} · ${action.service}`);
        operation.current(snapshot);
        if (decision === "confirm" && snapshot.state === "succeeded") changed.current?.();
      }
      else {
        if (decision === "confirm") changed.current?.();
        if (!batchKey) notify.info(result.text);
      }
      return result;
    } catch (cause) {
      if (activeRequest.current !== request) return;
      if (mutationFailureState(cause) === "unknown") {
        settled.current.add(key);
        setActions(current => current.filter(item => confirmationKey(item) !== key));
        const text = "Результат решения не подтверждён. Проверяем состояние без повторной отправки.";
        setResults(current => ({ ...current, [key]: text }));
        setContext(current => [...current, { key, actor: action.actor || "panel", question: `Попытка ${decision === "confirm" ? "подтверждения" : "отказа"}: ${action.action} · ${action.service}. Операция ${action.operation_id || ""}`, answer: text }].slice(-32));
        if (decision === "confirm" && action.operation_id) {
          const snapshot = { id: action.operation_id, state: "unknown", message: text };
          trackOperation(snapshot, `Nodera · ${action.action} · ${action.service}`);
          operation.current(snapshot);
        }
        else notify.error(text);
      } else setErrors(current => ({ ...current, [key]: cause instanceof Error ? cause.message : "Действие не выполнено" }));
    } finally {
      if (activeRequest.current === request) { writing.current.delete(key); setWorking([...writing.current, ...batching.current]); }
    }
  }, [request, notify, trackOperation]);
  const decide = useCallback(async (action: PendingAction, decision: "confirm" | "reject") => { const result = await decideOne(action, decision); return result?.display_text || result?.text; }, [decideOne]);
  const decideGroup = useCallback(async (group: ConfirmationGroup, decision: "confirm" | "reject") => {
    if (!group.presentation || batching.current.has(group.key) || activeRequest.current !== request) return;
    const selected = group.actions.filter(item => !settled.current.has(confirmationKey(item)));
    if (!selected.length || selected.some(item => writing.current.has(confirmationKey(item)) || !currentActions.current.some(current => confirmationKey(current) === confirmationKey(item)))) return;
    batching.current.add(group.key); generation.current++;
    setWorking([...writing.current, ...batching.current]);
    setErrors(current => { const next = { ...current }; delete next[group.key]; return next; });
    const stopped = () => setErrors(current => ({ ...current, [group.key]: "План остановлен. Уже отправленные действия не повторяем; оставшиеся ожидают вашего решения." }));
    try {
      for (const action of selected) {
        if (activeRequest.current !== request) return;
        const result = await decideOne(action, decision, group.key);
        if (activeRequest.current !== request) return;
        if (!result) { stopped(); return; }
        if (decision !== "confirm" || !result.operation?.id) continue;
        const id = result.operation.id;
        // Respect the existing admission worker: wait for success before the next mutation.
        while (activeRequest.current === request) {
          await readOperation(id);
          if (activeRequest.current !== request) return;
          const snapshot = tracked.current.get(id)?.snapshot;
          if (snapshot?.state === "succeeded") break;
          if (!snapshot || !["queued", "running"].includes(snapshot.state || "") || selected.some(item => item.expires * 1000 <= Date.now())) { stopped(); return; }
          await new Promise(resolve => setTimeout(resolve, 1000));
        }
      }
      if (activeRequest.current === request) notify.info(`${decision === "confirm" ? "План выполнен" : "План отклонён"}: ${group.presentation.title} · ${selected.length}`);
    } finally {
      if (activeRequest.current === request) { batching.current.delete(group.key); setWorking([...writing.current, ...batching.current]); }
    }
  }, [request, decideOne, readOperation, notify]);
  useEffect(() => {
    activeRequest.current = request; generation.current++;
    const sessionOperations = tracked.current;
    writing.current.clear(); batching.current.clear(); settled.current.clear(); tracked.current.clear();
    queueMicrotask(() => { if (activeRequest.current === request) { setActions([]); setContext([]); setWorking([]); setResults({}); setErrors({}); } });
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
    for (const group of confirmationGroups(actions)) {
      if (group.presentation) {
        const id = `nodera-confirm:${group.key}`;
        const busy = working.includes(group.key);
        ids.add(id);
        notifications.upsert({ id, source: "nodera", title: group.presentation.title, state: "info", kind: "confirmation",
          message: `${errors[group.key] ? errors[group.key] + " " : ""}${busy ? "Выполняем план. " : ""}${group.presentation.reason}`,
          confirmationItems: group.actions.map(item => `${confirmationSummary(item).title}${confirmationSummary(item).lines.length ? " · " + confirmationSummary(item).lines.slice(0, 2).join(" · ") : ""}${errors[confirmationKey(item)] ? " — " + errors[confirmationKey(item)] : ""}`),
          details: group.actions.filter(item => item.changes).map(item => `${item.action} · ${item.service}\n${JSON.stringify(item.changes, null, 2)}`).join("\n\n") || undefined,
          action: busy ? undefined : { label: `Подтвердить все (${group.actions.length})`, run: async () => { await decideGroup(group, "confirm"); } },
          secondaryAction: busy ? undefined : { label: `Отклонить все (${group.actions.length})`, run: async () => { await decideGroup(group, "reject"); } } });
        continue;
      }
      const action = group.actions[0];
      const id = `nodera-confirm:${confirmationKey(action)}`;
      ids.add(id);
      notifications.upsert({ id, source: "nodera", title: action.confirmation_request?.title || "Nodera · Подтверждение", state: "info", kind: "confirmation",
        message: `${errors[confirmationKey(action)] ? errors[confirmationKey(action)] + " · " : ""}${confirmationSummary(action).title}. ${action.confirmation_request ? action.confirmation_request.reason + " · " : ""}${action.actor_name || "Администратор"}. До ${new Date(action.expires * 1000).toLocaleTimeString("ru-RU")}.`,
        details: action.changes ? JSON.stringify(action.changes, null, 2) : undefined,
        action: { label: "Подтвердить", run: async () => { await decide(action, "confirm"); } },
        secondaryAction: { label: "Отказать", run: async () => { await decide(action, "reject"); } } });
    }
    for (const id of registered.current) if (!ids.has(id)) {
      const error = errors[id.slice("nodera-confirm:".length)];
      if (error) notifications.upsert({ id, source: "nodera", title: "Nodera · Подтверждение", kind: "message", state: "error", message: error });
      else notifications.resolve(id);
    }
    registered.current = ids;
  }, [actions, errors, working, notifications, decide, decideGroup]);
  useEffect(() => () => { for (const id of registered.current) notifications.resolve(id); }, [notifications, request]);
  const value = useMemo(() => ({ context, actions, working, results, errors, decide, decideGroup, ingest }), [context, actions, working, results, errors, decide, decideGroup, ingest]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function confirmationSummary(action: PendingAction) {
  const path = action.service.replace(/^Mihomo\s+/, "");
  const titles: Record<string, string> = { "/routing/settings": "Изменить правила и группы маршрутизации", "/dns/settings": "Изменить настройки DNS", "/routing/presets": "Изменить пресеты маршрутизации" };
  const title = titles[path] || (path.startsWith("/profiles/") ? (action.action === "DELETE" ? (path.includes("/devices/") ? "Удалить устройство Mihomo" : "Удалить профиль Mihomo") : "Изменить профиль Mihomo") : `${({ start: "Запустить", stop: "Остановить", restart: "Перезапустить", POST: "Создать", DELETE: "Удалить", PATCH: "Изменить" } as Record<string, string>)[action.action] || action.action} · ${action.service}`);
  const lines: string[] = [];
  const values = action.changes?.values;
  if (values && typeof values === "object" && !Array.isArray(values)) {
    const raw = (values as Record<string, unknown>).structured_policy;
    if (raw) {
      try {
        const policy = typeof raw === "string" ? JSON.parse(raw) : raw;
        if (Array.isArray(policy.groups)) lines.push(`Группы (${policy.groups.length}): ${policy.groups.map((group: { name?: string }) => group.name || "Без имени").join(", ") || "нет"}`);
        if (Array.isArray(policy.rules)) {
          lines.push(`Правила: ${policy.rules.length}`);
          for (const rule of policy.rules.slice(0, 5)) lines.push(`${rule.value} → ${rule.target || "GATE.312"}`);
          if (policy.rules.length > 5) lines.push(`Ещё правил: ${policy.rules.length - 5}`);
        }
      } catch { /* The validated payload remains available in technical details. */ }
    }
  }
  return { title, lines };
}

function confirmationChanges(action: PendingAction) {
  const summary = confirmationSummary(action);
  return <div className="mcpConfirmationChange"><strong>{summary.title}</strong>
    {summary.lines.length > 0 && <ul>{summary.lines.map((line, index) => <li key={index}>{line}</li>)}</ul>}
    {action.changes && <details><summary>Технические данные</summary><pre>{JSON.stringify(action.changes, null, 2)}</pre></details>}
  </div>;
}

export function ConfirmationCards({ actor }: { actor: string }) {
  const queue = useAssistantConfirmations();
  const actions = queue.actions.filter(item => (item.actor || "panel") === (actor || "panel"));
  if (!actions.length) return null;
  return <section className="mcpConfirmations" role="region" aria-label="Подтверждение действий">
    <header><strong>Требуется ваше решение</strong><small>Действий: {actions.length} · Изменения ещё не применены</small></header>
    {confirmationGroups(actions).map(group => group.presentation ? <article key={group.key} className="mcpConfirmationCard" aria-label={group.presentation.title}>
      <header className="mcpConfirmationHead"><div><strong>{group.presentation.title}</strong><small>{group.actions[0].actor_name} · По порядку, {group.actions.length} действий</small></div><span>До {new Date(Math.min(...group.actions.map(item => item.expires)) * 1000).toLocaleTimeString("ru-RU")}</span></header>
      <div className="mcpConfirmationBody"><p>{group.presentation.reason}</p>
      <ol className="mcpConfirmationItems">{group.actions.map(action => <li key={confirmationKey(action)}>
        {confirmationChanges(action)}
        {queue.errors[confirmationKey(action)] && <p className="error" role="alert">{queue.errors[confirmationKey(action)]}</p>}
      </li>)}</ol></div>
      {queue.errors[group.key] && <p className="error" role="alert">{queue.errors[group.key]}</p>}
      <footer><button type="button" className="primaryButton" disabled={queue.working.includes(group.key) || group.actions.some(item => queue.working.includes(confirmationKey(item)))} onClick={() => void queue.decideGroup(group, "confirm")}>{queue.working.includes(group.key) ? "Выполняем план…" : `Подтвердить все (${group.actions.length})`}</button>
        <button type="button" className="ghostButton" disabled={queue.working.includes(group.key) || group.actions.some(item => queue.working.includes(confirmationKey(item)))} onClick={() => void queue.decideGroup(group, "reject")}>Отклонить все ({group.actions.length})</button></footer>
    </article> : group.actions.map(action => <article key={confirmationKey(action)} className="mcpConfirmationCard" aria-label={`${action.action} · ${action.service}`}>
      <header className="mcpConfirmationHead"><div><strong>{action.confirmation_request?.title || confirmationSummary(action).title}</strong><small>{action.actor_name || "Администратор"}</small></div><span>До {new Date(action.expires * 1000).toLocaleTimeString("ru-RU")}</span></header>
      <div className="mcpConfirmationBody">{action.confirmation_request && <p>{action.confirmation_request.reason}</p>}{confirmationChanges(action)}</div>
      {queue.errors[confirmationKey(action)] && <p className="error" role="alert">{queue.errors[confirmationKey(action)]}</p>}
      <footer><button type="button" className="primaryButton" disabled={queue.working.includes(confirmationKey(action))} onClick={() => void queue.decide(action, "confirm")}>{queue.working.includes(confirmationKey(action)) ? "Отправляем решение…" : "Подтвердить"}</button>
        <button type="button" className="ghostButton" disabled={queue.working.includes(confirmationKey(action))} onClick={() => void queue.decide(action, "reject")}>Отказать</button></footer>
    </article>))}
  </section>;
}
