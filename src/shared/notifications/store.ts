export type NotificationState = "running" | "unknown" | "success" | "error" | "info" | "cancelled";
export type NotificationAction = { label: string; run: () => void | Promise<void>; disabled?: boolean };
export type NotificationInput = {
  id: string;
  source: string;
  title: string;
  message: string;
  state: NotificationState;
  kind?: "message" | "operation" | "confirmation";
  secondaryAction?: NotificationAction;
  details?: string;
  confirmationItems?: string[];
  progress?: number;
  startedAt?: number;
  action?: NotificationAction;
  onCancel?: () => void | Promise<void>;
  onDismiss?: () => void;
};
export type Notification = NotificationInput & { count: number; createdAt: number; finishedAt?: number; expiresAt?: number };
export type NotificationFailure = { message: string; network?: boolean };
export function notificationFailure(cause: unknown, fallback: string) {
  return { message: cause instanceof Error ? cause.message : fallback,
    network: cause instanceof Error && "kind" in cause && (cause.kind === "network" || cause.kind === "response") };
}

export const isPending = (item: Pick<Notification, "state" | "kind">) => item.kind === "confirmation" || item.state === "running" || item.state === "unknown";
const fingerprint = (item: NotificationInput) => JSON.stringify([item.kind, item.state, item.title, item.message, item.progress, item.startedAt, item.action?.label, item.secondaryAction?.label, item.details, item.confirmationItems, Boolean(item.onCancel)]);

/** One store per mounted application. Nothing is shared between SSR requests. */
export function createNotificationStore(now = Date.now) {
  let items: Notification[] = [];
  let visible: Notification[] = [];
  const inline = new Map<symbol, Set<string>>();
  let sequence = 0;
  let pausedAt: number | undefined;
  let networkSince: number | undefined;
  const listeners = new Set<() => void>();
  const dismissed = new Map<string, string>();
  const finishedOperations = new Set<string>();
  const failures = new Map<string, { title: string; values: Record<string, NotificationFailure>; reconnecting: boolean; action?: NotificationAction }>();
  const notices = new Map<symbol, NotificationInput>();
  const emit = () => {
    const hidden = new Set([...inline.values()].flatMap(ids => [...ids]));
    const next = items.filter(item => !hidden.has(item.id));
    if (next.length !== visible.length || next.some((item, index) => item !== visible[index])) visible = next;
    listeners.forEach((listener) => listener());
  };

  function resolve(id: string) {
    dismissed.delete(id);
    if (!items.some((item) => item.id === id)) return;
    items = items.filter((item) => item.id !== id);
    emit();
  }

  function upsert(input: NotificationInput) {
    if (finishedOperations.has(input.id)) return input.id;
    if (input.kind === "operation" && input.state !== "error") {
      const summaries = { running: "Выполняется…", unknown: "Ожидаем подтверждения результата.", success: "Готово.", info: "Ожидает выполнения.", cancelled: "Команда отменена." };
      input = { ...input, message: input.message.trim() || summaries[input.state] };
    }
    const previous = items.find((item) => item.id === input.id);
    if (input.kind === "operation") input = { ...input, onDismiss: input.onDismiss ?? previous?.onDismiss };
    input = { ...input, startedAt: Number.isFinite(input.startedAt) ? input.startedAt : previous?.startedAt };
    const signature = fingerprint(input);
    if (dismissed.get(input.id) === signature) return input.id;
    if (previous && fingerprint(previous) === signature && Boolean(previous.action?.disabled) === Boolean(input.action?.disabled) && Boolean(previous.secondaryAction?.disabled) === Boolean(input.secondaryAction?.disabled)) {
      // Callbacks may change while the visible message remains the same.
      previous.action = input.action;
      previous.secondaryAction = input.secondaryAction;
      previous.onCancel = input.onCancel;
      previous.onDismiss = input.onDismiss;
      return input.id;
    }
    dismissed.delete(input.id);
    const ttl = input.kind === "confirmation" ? 0 : input.state === "success" || input.state === "cancelled" ? 8000 : input.state === "info" ? 10000 : 0;
    const item: Notification = { ...input, count: previous?.count ?? 1, createdAt: previous?.createdAt ?? now(),
      finishedAt: input.kind === "operation" && !isPending(input) ? previous?.finishedAt ?? now() : undefined,
      expiresAt: ttl ? (pausedAt ?? now()) + ttl : undefined };
    items = previous ? items.map((entry) => entry.id === item.id ? item : entry) : [...items, item];
    emit();
    return item.id;
  }

  function notify(source: string, title: string, state: NotificationState, message: string) {
    if (!message.trim()) return;
    const previous = items.find((item) => item.kind === "message" && item.source === source && item.state === state && item.message === message);
    if (previous) {
      items = items.map((item) => item === previous ? { ...item, count: item.count + 1, expiresAt: item.expiresAt ? (pausedAt ?? now()) + (state === "info" ? 10000 : 8000) : undefined } : item);
      emit();
      return previous.id;
    }
    return upsert({ id: `message:${++sequence}`, source, title, state, message, kind: "message" });
  }

  function dismiss(id: string, explicit = true) {
    const item = items.find((entry) => entry.id === id);
    if (!item || isPending(item)) return;
    // Immutable server operation IDs cannot represent a new run. A second
    // publisher may change the title/message or deliver stale progress later.
    if (item.kind === "operation" && /^operation:(?:system|cdn):[a-f0-9]{32}$/.test(id)) {
      finishedOperations.add(id);
      if (finishedOperations.size > 200) finishedOperations.delete(finishedOperations.values().next().value!);
    }
    dismissed.set(id, fingerprint(item));
    if (dismissed.size > 200) dismissed.delete(dismissed.keys().next().value!);
    items = items.filter((entry) => entry.id !== id);
    emit();
    // Expiration removes a toast, not the operation's message in chat history.
    if (explicit || item.kind !== "operation") item.onDismiss?.();
  }

  function syncFailures() {
    const desired = new Set<string>();
    let network = false;
    let reconnecting = items.some((item) => item.kind === "operation" && isPending(item));
    for (const [source, group] of failures) {
      reconnecting ||= group.reconnecting;
      for (const [key, failure] of Object.entries(group.values)) {
        if (failure.network) { network = true; continue; }
        const id = `refresh:${source}:${key}`;
        desired.add(id);
        upsert({ id, source, title: group.title, state: "error", message: `${failure.message} Отображаются последние полученные данные.`, action: group.action });
      }
    }
    if (network) networkSince ??= now();
    else networkSince = undefined;
    if (network && !reconnecting && now() - networkSince! >= 15000) {
      desired.add("refresh:network");
      upsert({ id: "refresh:network", source: "connection", title: "Связь с панелью", state: "error", message: "Связь временно недоступна. Отображаются последние полученные данные; обновите их после восстановления соединения." });
    }
    for (const id of [...items.map((item) => item.id), ...dismissed.keys()]) {
      if (id.startsWith("refresh:") && !desired.has(id)) resolve(id);
    }
  }

  return {
    getSnapshot: () => items,
    getVisibleSnapshot: () => visible,
    setInline: (owner: symbol, ids: string[]) => {
      const previous = inline.get(owner);
      const next = new Set(ids);
      if (previous && previous.size === next.size && [...next].every(id => previous.has(id))) return;
      if (!previous && !next.size) return;
      if (next.size) inline.set(owner, next); else inline.delete(owner);
      emit();
    },
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    upsert, notify, resolve, dismiss,
    setNotice: (owner: symbol, input: NotificationInput | null) => {
      const previous = notices.get(owner);
      if (input) { notices.delete(owner); notices.set(owner, input); } else notices.delete(owner);
      for (const id of new Set([previous?.id, input?.id])) {
        if (!id) continue;
        const current = [...notices.values()].reverse().find(value => value.id === id);
        if (current) upsert(current); else resolve(id);
      }
    },
    finishOperation: (input: NotificationInput) => {
      upsert(input);
      // A confirmed result must not be replaced by a late progress poll,
      // including after the completed card has been dismissed.
      finishedOperations.add(input.id);
      if (finishedOperations.size > 200) finishedOperations.delete(finishedOperations.values().next().value!);
    },
    clearCompleted: () => [...items].filter((item) => !isPending(item)).forEach((item) => dismiss(item.id)),
    reset: () => { items = []; failures.clear(); notices.clear(); dismissed.clear(); finishedOperations.clear(); networkSince = undefined; pausedAt = undefined; emit(); },
    setFailures: (source: string, title: string, values: Record<string, NotificationFailure>, reconnecting = false, action?: NotificationAction) => {
      failures.set(source, { title, values, reconnecting, action });
      syncFailures();
    },
    clearFailures: (source: string) => { failures.delete(source); syncFailures(); },
    pause: () => { pausedAt ??= now(); },
    resume: () => {
      if (pausedAt === undefined) return;
      const pauseStart = pausedAt;
      items = items.map((item) => item.expiresAt ? { ...item, expiresAt: item.expiresAt + now() - pauseStart } : item);
      pausedAt = undefined;
      emit();
    },
    tick: () => {
      syncFailures();
      if (pausedAt !== undefined) return;
      for (const item of [...items]) if (item.expiresAt && item.expiresAt <= now()) dismiss(item.id, false);
    },
  };
}
export type NotificationStore = ReturnType<typeof createNotificationStore>;
