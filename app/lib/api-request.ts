type RequestOptions = {
  formatHttpError?: (detail: string, status: number) => string;
  readTimeoutMs?: number;
  writeTimeoutMs?: number;
  retryDelaysMs?: number[];
};
type ApiResult = Awaited<ReturnType<Response["json"]>>;

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

// One client per authenticated UI. Concurrent reads share a request, not a cache.
export function createApiClient(token: string, options: RequestOptions = {}) {
  const pending = new Map<string, Promise<ApiResult>>();
  const request = async (path: string, init: RequestInit = {}): Promise<ApiResult> => {
    if (!token) throw new ApiRequestError("Сессия панели завершена. Войдите заново.", "http", 401);
    const method = (init.method || "GET").toUpperCase();
    const read = method === "GET" && init.body == null;
    const delays = read ? (options.retryDelaysMs ?? [400, 1200]) : [];
    // Do not expose query strings, subscription tokens, or authentication in errors.
    const operation = path.split("?")[0].replace(/\/s\/[^/]+|\/subscriptions\/[^/]+/g, "/subscription");
    for (let attempt = 0; ; attempt += 1) {
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
        if (!response.ok) {
          const raw = await response.text();
          let detail = `HTTP ${response.status}`;
          try {
            const body = JSON.parse(raw);
            detail = body?.detail || body?.message || detail;
            if (body?.operation_id) detail += ` · операция ${body.operation_id}`;
          } catch { /* Do not display an HTML gateway page in the UI. */ }
          throw new ApiRequestError(options.formatHttpError?.(detail, response.status) || detail, "http", response.status);
        }
        if (response.status === 204) return null;
        // Consume inside the retry boundary: a stream may break after headers arrive.
        const body = await response.text();
        if ((response.headers.get("content-type") || "").includes("text/plain")) return body;
        try { return JSON.parse(body); }
        catch { throw new ApiRequestError(`Панель получила неполный или некорректный ответ (${operation}). Обновите данные.`, "response"); }
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
  return <T = ApiResult>(path: string, init: RequestInit = {}): Promise<T> => {
    if (init.method && !["GET", "HEAD"].includes(init.method.toUpperCase())) {
      // A refresh after a command must not join a read started before that command.
      pending.clear();
      return request(path, init).finally(() => pending.clear()) as Promise<T>;
    }
    // Custom headers/signals may represent independent callers and must not merge.
    const share = (!init.method || init.method.toUpperCase() === "GET") && !init.body && !init.headers && !init.signal;
    if (!share) return request(path, init) as Promise<T>;
    const existing = pending.get(path);
    if (existing) return existing as Promise<T>;
    const result = request(path, init).finally(() => {
      if (pending.get(path) === result) pending.delete(path);
    });
    pending.set(path, result);
    return result as Promise<T>;
  };
}
