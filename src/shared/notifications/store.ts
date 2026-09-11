export type NotificationState = "running" | "unknown" | "success" | "error" | "info";
export type NotificationAction = { label: string; run: () => void | Promise<void> };
export type NotificationInput = {
  id: string;
  source: string;
  title: string;
  message: string;
  state: NotificationState;
  kind?: "message" | "operation";
  progress?: number;
  action?: NotificationAction;
  onCancel?: () => void | Promise<void>;
  onDismiss?: () => void;
};
export type Notification = NotificationInput & { count: number; createdAt: number; expiresAt?: number };
export type NotificationFailure = { message: string; network?: boolean };
export function notificationFailure(cause: unknown, fallback: string) {
  return { message: cause instanceof Error ? cause.message : fallback,
    network: cause instanceof Error && "kind" in cause && (cause.kind === "network" || cause.kind === "response") };
}

export const isPending = (item: Pick<Notification, "state">) => item.state === "running" || item.state === "unknown";
const fingerprint = (item: NotificationInput) => JSON.stringify([item.state, item.title, item.message, item.progress, item.action?.label, Boolean(item.onCancel)]);

/** One store per mounted application. Nothing is shared between SSR requests. */
export function createNotificationStore(now = Date.now) {
  let items: Notification[] = [];
  let sequence = 0;
  let pausedAt: number | undefined;
  let networkSince: number | undefined;
  const listeners = new Set<() => void>();
  const dismissed = new Map<string, string>();
  const finishedOperations = new Set<string>();
  const failures = new Map<string, { title: string; values: Record<string, NotificationFailure>; reconnecting: boolean; action?: NotificationAction }>();
  const emit = () => listeners.forEach((listener) => listener());

  function resolve(id: string) {
    dismissed.delete(id);
    if (!items.some((item) => item.id === id)) return;
    items = items.filter((item) => item.id !== id);
    emit();
  }

  function upsert(input: NotificationInput) {
    if (finishedOperations.has(input.id)) return input.id;
    if (input.kind === "operation" && input.state !== "error") {
      const summaries = { running: "Выполняется…", unknown: "Ожидаем подтверждения результата.", success: "Готово.", info: "Ожидает выполнения." };
      input = { ...input, message: summaries[input.state] };
    }
    const previous = items.find((item) => item.id === input.id);
    const signature = fingerprint(input);
    if (dismissed.get(input.id) === signature) return input.id;
    if (previous && fingerprint(previous) === signature) {
      // Callbacks may change while the visible message remains the same.
      previous.action = input.action;
      previous.onCancel = input.onCancel;
      previous.onDismiss = input.onDismiss;
      return input.id;
    }
    dismissed.delete(input.id);
    const ttl = input.state === "success" ? 8000 : input.state === "info" ? 10000 : 0;
    const item: Notification = { ...input, count: previous?.count ?? 1, createdAt: previous?.createdAt ?? now(), expiresAt: ttl ? (pausedAt ?? now()) + ttl : undefined };
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

  function dismiss(id: string) {
    const item = items.find((entry) => entry.id === id);
    if (!item || isPending(item)) return;
    dismissed.set(id, fingerprint(item));
    if (dismissed.size > 200) dismissed.delete(dismissed.keys().next().value!);
    items = items.filter((entry) => entry.id !== id);
    emit();
    item.onDismiss?.();
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
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    upsert, notify, resolve, dismiss,
    finishOperation: (input: NotificationInput) => {
      upsert(input);
      // A confirmed result must not be replaced by a late progress poll,
      // including after the completed card has been dismissed.
      finishedOperations.add(input.id);
      if (finishedOperations.size > 200) finishedOperations.delete(finishedOperations.values().next().value!);
    },
    clearCompleted: () => [...items].filter((item) => !isPending(item)).forEach((item) => dismiss(item.id)),
    reset: () => { items = []; failures.clear(); dismissed.clear(); finishedOperations.clear(); networkSince = undefined; pausedAt = undefined; emit(); },
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
      for (const item of [...items]) if (item.expiresAt && item.expiresAt <= now()) dismiss(item.id);
    },
  };
}
export type NotificationStore = ReturnType<typeof createNotificationStore>;
