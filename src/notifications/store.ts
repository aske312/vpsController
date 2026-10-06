export type NotificationState = "running" | "unknown" | "success" | "error" | "info";

export type NotificationInput = {
  id: string;
  source: string;
  title: string;
  message: string;
  state: NotificationState;
  kind?: "message" | "operation";
  progress?: number;
};

export type Notification = NotificationInput & {
  count: number;
  createdAt: number;
  expiresAt?: number;
};

export const isPending = (item: Pick<Notification, "state">) => item.state === "running" || item.state === "unknown";

const fingerprint = (item: NotificationInput) => JSON.stringify([
  item.state, item.title, item.message, item.progress,
]);

export function createNotificationStore(now = Date.now) {
  let items: Notification[] = [];
  let sequence = 0;
  let pausedAt: number | undefined;
  const listeners = new Set<() => void>();
  const dismissed = new Map<string, string>();
  const finishedOperations = new Set<string>();
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
      const summaries = {
        running: "Выполняется…",
        unknown: "Ожидаем подтверждения результата.",
        success: "Готово.",
        info: "Ожидает выполнения.",
      };
      input = { ...input, message: summaries[input.state] };
    }
    const previous = items.find((item) => item.id === input.id);
    const signature = fingerprint(input);
    if (dismissed.get(input.id) === signature) return input.id;
    if (previous && fingerprint(previous) === signature) return input.id;
    dismissed.delete(input.id);
    const ttl = input.state === "success" ? 8000 : input.state === "info" ? 10000 : 0;
    const item: Notification = {
      ...input,
      count: previous?.count ?? 1,
      createdAt: previous?.createdAt ?? now(),
      expiresAt: ttl ? (pausedAt ?? now()) + ttl : undefined,
    };
    items = previous ? items.map((entry) => entry.id === item.id ? item : entry) : [...items, item];
    emit();
    return item.id;
  }

  function notify(source: string, title: string, state: NotificationState, message: string) {
    if (!message.trim()) return;
    const previous = items.find((item) => item.kind === "message" && item.source === source && item.state === state && item.message === message);
    if (previous) {
      items = items.map((item) => item === previous ? {
        ...item,
        count: item.count + 1,
        expiresAt: item.expiresAt ? (pausedAt ?? now()) + (state === "info" ? 10000 : 8000) : undefined,
      } : item);
      emit();
      return previous.id;
    }
    return upsert({ id: `message:${++sequence}`, source, title, state, message, kind: "message" });
  }

  function dismiss(id: string) {
    const item = items.find((entry) => entry.id === id);
    if (!item || isPending(item)) return;
    dismissed.set(id, fingerprint(item));
    items = items.filter((entry) => entry.id !== id);
    emit();
  }

  return {
    getSnapshot: () => items,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => listeners.delete(listener); },
    upsert,
    notify,
    resolve,
    dismiss,
    finishOperation: (input: NotificationInput) => {
      upsert(input);
      finishedOperations.add(input.id);
    },
    clearCompleted: () => [...items].filter((item) => !isPending(item)).forEach((item) => dismiss(item.id)),
    reset: () => { items = []; dismissed.clear(); finishedOperations.clear(); pausedAt = undefined; emit(); },
    pause: () => { pausedAt ??= now(); },
    resume: () => {
      if (pausedAt === undefined) return;
      const started = pausedAt;
      items = items.map((item) => item.expiresAt ? { ...item, expiresAt: item.expiresAt + now() - started } : item);
      pausedAt = undefined;
      emit();
    },
    tick: () => {
      if (pausedAt !== undefined) return;
      for (const item of [...items]) if (item.expiresAt && item.expiresAt <= now()) dismiss(item.id);
    },
  };
}

export type NotificationStore = ReturnType<typeof createNotificationStore>;
