type RequestOptions = {
  formatHttpError?: (detail: string, status: number) => string;
  readTimeoutMs?: number;
  writeTimeoutMs?: number;
  retryDelaysMs?: number[];
};
type ApiResult = Awaited<ReturnType<Response["json"]>>;
type CachedResponse = { value: ApiResult; cachedAt: number };
type CachePolicy = { ttlMs: number; staleMs: number; persist: boolean };
type SessionReads = {
  pending: Map<string, Promise<ApiResult>>;
  cache: Map<string, CachedResponse>;
  readVersions: Map<string, number>;
  generation: number;
  writes: number;
};

export type ApiRequestInit<T = unknown> = RequestInit & {
  readScope?: string | number;
  onRevalidated?: (value: T) => void;
  onRevalidationError?: (cause: unknown) => void;
};

const CACHE_POLICIES: Array<{ match: RegExp; policy: CachePolicy }> = [
  { match: /^\/overview$/, policy: { ttlMs: 5_000, staleMs: 60_000, persist: true } },
  { match: /^\/protocol-images$/, policy: { ttlMs: 30_000, staleMs: 300_000, persist: true } },
  { match: /^\/clients$/, policy: { ttlMs: 8_000, staleMs: 60_000, persist: true } },
  { match: /^\/services$/, policy: { ttlMs: 10_000, staleMs: 60_000, persist: true } },
  { match: /^\/security$/, policy: { ttlMs: 15_000, staleMs: 60_000, persist: false } },
  { match: /^\/application\/metadata$/, policy: { ttlMs: 30_000, staleMs: 300_000, persist: true } },
  { match: /^\/application\/status$/, policy: { ttlMs: 1_500, staleMs: 10_000, persist: false } },
  { match: /^\/network$/, policy: { ttlMs: 15_000, staleMs: 120_000, persist: true } },
  { match: /^\/dns$/, policy: { ttlMs: 15_000, staleMs: 120_000, persist: true } },
  { match: /^\/protocols\/[^/]+\/status$/, policy: { ttlMs: 5_000, staleMs: 30_000, persist: false } },
  { match: /^\/mihomo\/(?:status|modules|profiles)$/, policy: { ttlMs: 15_000, staleMs: 60_000, persist: false } },
];

const CACHE_STORAGE_PREFIX = "312-api-cache:";
let nextSessionGeneration = 0;
const sessionGenerations = new Map<string, number>();
const sessionReads = new Map<string, SessionReads>();
const sessionExpiryListeners = new Map<string, Set<() => void>>();

export function onApiSessionExpired(token: string, callback: () => void) {
  const listeners = sessionExpiryListeners.get(token) || new Set<() => void>();
  listeners.add(callback);
  sessionExpiryListeners.set(token, listeners);
  return () => {
    listeners.delete(callback);
    if (!listeners.size) sessionExpiryListeners.delete(token);
  };
}

export function invalidateApiSession(token: string, notify = true) {
  if (!sessionGenerations.delete(token)) return;
  sessionReads.delete(token);
  clearPersistedCache(token);
  if (notify) for (const callback of sessionExpiryListeners.get(token) || []) callback();
}

/** A server operation phase starts a new observation even without another POST. */
export function invalidateApiReads(token: string) {
  const state = sessionReads.get(token);
  if (!state) return;
  state.generation += 1;
  state.pending.clear();
  state.cache.clear();
  state.readVersions.clear();
  clearPersistedCache(token);
}

function cachePolicy(path: string): CachePolicy | null {
  const endpoint = path.split("?", 1)[0];
  return CACHE_POLICIES.find((item) => item.match.test(endpoint))?.policy || null;
}

function tokenFingerprint(token: string): string {
  let hash = 2166136261;
  for (let index = 0; index < token.length; index += 1) {
    hash ^= token.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16);
}

function storageKey(token: string, path: string): string {
  return `${CACHE_STORAGE_PREFIX}${tokenFingerprint(token)}:${encodeURIComponent(path)}`;
}

function readPersistedCache(token: string, cache: Map<string, CachedResponse>) {
  if (typeof window === "undefined") return;
  try {
    const prefix = `${CACHE_STORAGE_PREFIX}${tokenFingerprint(token)}:`;
    for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
      const key = sessionStorage.key(index);
      if (!key?.startsWith(prefix)) continue;
      const path = decodeURIComponent(key.slice(prefix.length));
      const policy = cachePolicy(path);
      if (!policy?.persist) continue;
      const raw = sessionStorage.getItem(key);
      if (!raw) continue;
      const entry = JSON.parse(raw) as CachedResponse;
      if (!entry || typeof entry.cachedAt !== "number" || Date.now() - entry.cachedAt > policy.staleMs) {
        sessionStorage.removeItem(key);
        continue;
      }
      cache.set(path, entry);
    }
  } catch {
    // Private browsing and quota-restricted contexts may reject sessionStorage.
  }
}

function persistCache(token: string, path: string, entry: CachedResponse, policy: CachePolicy | null) {
  if (!policy?.persist || typeof window === "undefined") return;
  try { sessionStorage.setItem(storageKey(token, path), JSON.stringify(entry)); } catch { /* best effort */ }
}

function clearPersistedCache(token: string) {
  if (typeof window === "undefined") return;
  try {
    const prefix = `${CACHE_STORAGE_PREFIX}${tokenFingerprint(token)}:`;
    for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
      const key = sessionStorage.key(index);
      if (key?.startsWith(prefix)) sessionStorage.removeItem(key);
    }
  } catch { /* best effort */ }
}

export class ApiRequestError extends Error {
  kind: "network" | "http" | "response";
  status?: number;
  constructor(message: string, kind: "network" | "http" | "response", status?: number) {
    super(message);
    this.name = "ApiRequestError";
    this.kind = kind;
    this.status = status;
  }
}

export function mutationFailureState(cause: unknown): "unknown" | "error" {
  return cause instanceof Error && "kind" in cause && (cause.kind === "network" || cause.kind === "response")
    ? "unknown" : "error";
}

// All clients in one authenticated session share reads. Custom cancellation and
// headers remain independent; fresh observations never join a cached revalidation.
export function createApiClient(token: string, options: RequestOptions = {}) {
  if (!sessionGenerations.has(token)) sessionGenerations.set(token, ++nextSessionGeneration);
  const sessionGeneration = sessionGenerations.get(token);
  const currentSession = () => Boolean(token) && sessionGenerations.get(token) === sessionGeneration;
  const expired = () => new ApiRequestError("Сессия панели завершена. Войдите заново.", "http", 401);
  if (!sessionReads.has(token)) {
    const cache = new Map<string, CachedResponse>();
    readPersistedCache(token, cache);
    sessionReads.set(token, { pending: new Map(), cache, readVersions: new Map(), generation: 0, writes: 0 });
  }
  const state = sessionReads.get(token)!;
  const { pending, cache, readVersions } = state;
  const transportKey = JSON.stringify([options.readTimeoutMs ?? 20000, options.retryDelaysMs ?? [400, 1200]]);
  const formatCause = (cause: unknown) => cause instanceof ApiRequestError && cause.kind === "http" && options.formatHttpError
    ? new ApiRequestError(options.formatHttpError(cause.message, cause.status || 500), cause.kind, cause.status) : cause;
  const invalidateReads = () => {
    state.generation += 1;
    pending.clear();
    cache.clear();
    readVersions.clear();
    if (currentSession()) clearPersistedCache(token);
  };
  const request = async (path: string, init: RequestInit = {}): Promise<ApiResult> => {
    if (!currentSession()) throw expired();
    const method = (init.method || "GET").toUpperCase();
    const read = method === "GET" && init.body == null;
    const generation = state.generation;
    const canonicalRead = read && !init.headers && !init.signal;
    const version = canonicalRead && cachePolicy(path) ? (readVersions.get(path) || 0) + 1 : 0;
    if (version) readVersions.set(path, version);
    const delays = read ? (options.retryDelaysMs ?? [400, 1200]) : [];
    // Do not expose query strings, subscription tokens, or authentication in errors.
    const operation = path.split("?")[0].replace(/\/s\/[^/]+|\/subscriptions\/[^/]+/g, "/subscription");
    for (let attempt = 0; ; attempt += 1) {
      if (!currentSession()) throw expired();
      if (init.signal?.aborted) throw init.signal.reason || new DOMException("Aborted", "AbortError");
      const controller = new AbortController();
      const abort = () => controller.abort(init.signal?.reason);
      init.signal?.addEventListener("abort", abort, { once: true });
      const timeout = setTimeout(() => controller.abort(), read ? (options.readTimeoutMs ?? 20000) : (options.writeTimeoutMs ?? 300000));
      try {
        const headers = new Headers(init.headers);
        headers.set("Authorization", `Basic ${token}`);
        if (init.body != null && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
        const response = await fetch(`/api${path}`, { ...init, method, headers, signal: controller.signal, cache: read ? "no-store" : init.cache });
        if (!currentSession()) throw expired();
        if (!response.ok) {
          if (response.status === 401) invalidateApiSession(token);
          const raw = await response.text();
          let detail = `HTTP ${response.status}`;
          try {
            const body = JSON.parse(raw);
            const value = body?.detail || body?.message;
            if (typeof value === "string") detail = value;
            else if (Array.isArray(value)) {
              detail = value.map((item) => typeof item?.msg === "string" ? item.msg : "").filter(Boolean).join("; ") || detail;
            }
            if (body?.operation_id) detail += ` · операция ${body.operation_id}`;
          } catch { /* Do not display an HTML gateway page in the UI. */ }
          throw new ApiRequestError(detail, "http", response.status);
        }
        if (response.status === 204 || method === "HEAD") return null;
        // Consume inside the retry boundary: a stream may break after headers arrive.
        const body = await response.text();
        if (!currentSession()) throw expired();
        if ((response.headers.get("content-type") || "").includes("text/plain")) return body;
        let value: ApiResult;
        try { value = JSON.parse(body); }
        catch { throw new ApiRequestError(`Панель получила неполный или некорректный ответ (${operation}). Обновите данные.`, "response"); }
        const policy = canonicalRead ? cachePolicy(path) : null;
        if (policy && !state.writes && generation === state.generation && readVersions.get(path) === version) {
          const entry = { value, cachedAt: Date.now() };
          cache.set(path, entry);
          persistCache(token, path, entry, policy);
        }
        return value;
      } catch (cause) {
        if (init.signal?.aborted) throw init.signal.reason || cause;
        const transient = !(cause instanceof ApiRequestError) || cause.kind === "response" || [502, 503, 504].includes(cause.status || 0);
        if (!transient) throw cause;
        if (attempt >= delays.length) {
          const message = read
            ? `Не удалось обновить данные (${operation}): связь с панелью прервана или сервер не ответил. Повторите обновление после восстановления соединения.`
            : `Связь с панелью прервана (${operation}). Результат команды неизвестен: проверьте состояние перед повторной отправкой.`;
          throw new ApiRequestError(message, "network", cause instanceof ApiRequestError ? cause.status : undefined);
        }
      } finally {
        clearTimeout(timeout);
        init.signal?.removeEventListener("abort", abort);
      }
      await new Promise((resolve) => setTimeout(resolve, delays[attempt]));
    }
  };
  const execute = <T = ApiResult>(path: string, options: ApiRequestInit<T> = {}): Promise<T> => {
    const { onRevalidated, onRevalidationError, readScope = "", ...init } = options;
    if (!currentSession()) return Promise.reject(expired());
    const method = (init.method || "GET").toUpperCase();
    const read = method === "GET" && init.body == null;
    const share = read && !init.headers && !init.signal && init.cache !== "reload";
    const policy = share ? cachePolicy(path) : null;
    const bypassCache = init.cache === "no-store" || init.cache === "reload";
    const pendingKey = JSON.stringify([transportKey, bypassCache ? "fresh" : "cached", readScope, path]);
    if (read && share && policy && !bypassCache) {
      const entry = cache.get(path);
      if (entry) {
        const age = Date.now() - entry.cachedAt;
        if (age <= policy.ttlMs) return Promise.resolve(entry.value) as Promise<T>;
        if (age <= policy.staleMs) {
          if (!pending.has(pendingKey)) {
            const revalidation = request(path, init).finally(() => {
              if (pending.get(pendingKey) === revalidation) pending.delete(pendingKey);
            });
            pending.set(pendingKey, revalidation);
            // Revalidation also runs for callers that only need a cached preview.
            void revalidation.catch(() => undefined);
          }
          const generation = state.generation;
          const version = readVersions.get(path);
          const current = () => currentSession() && generation === state.generation && readVersions.get(path) === version;
          void pending.get(pendingKey)!.then((value) => {
            if (current()) onRevalidated?.(structuredClone(value) as T);
          }).catch((cause) => {
            if (current()) onRevalidationError?.(formatCause(cause));
          }).catch(() => undefined);
          return Promise.resolve(entry.value) as Promise<T>;
        }
      }
    }
    if (init.method && !["GET", "HEAD"].includes(method)) {
      // A refresh after a command must not join a read started before that command.
      state.writes += 1;
      invalidateReads();
      return request(path, init).finally(() => {
        // Reads that started while the command ran are not a post-command baseline.
        state.writes -= 1;
        invalidateReads();
      }) as Promise<T>;
    }
    // Custom headers/signals may represent independent callers and must not merge.
    if (!share) return request(path, init) as Promise<T>;
    const existing = pending.get(pendingKey);
    if (existing) return existing as Promise<T>;
    const result = request(path, init).finally(() => {
      if (pending.get(pendingKey) === result) pending.delete(pendingKey);
    });
    pending.set(pendingKey, result);
    return result as Promise<T>;
  };
  return <T = ApiResult>(path: string, init: ApiRequestInit<T> = {}): Promise<T> => execute<T>(path, init).then((value) => {
    if (!currentSession()) throw expired();
    // Draft editing by one consumer must not mutate another section's cache.
    return structuredClone(value);
  }).catch((cause) => { throw formatCause(cause); });
}
