export class SessionExpiredError extends Error {
  constructor() { super("Сессия завершена. Войдите в панель заново."); }
}

export class UnknownMutationError extends Error {
  constructor() { super("Связь с сервером прервана. Результат команды неизвестен: проверьте список подключений или состояние операции перед повтором."); }
}

export function apiErrorMessage(raw: string, status: number): string {
  try {
    const detail: unknown = JSON.parse(raw).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((entry: { loc?: unknown[]; msg?: string }) => {
        const field = entry.loc?.filter((part) => part !== "body").join(".");
        return `${field ? `${field}: ` : ""}${entry.msg || "Недопустимое значение"}`;
      }).join("; ");
    }
  } catch { /* A gateway may return plain text or HTML. */ }
  return raw && !raw.trim().startsWith("<") ? raw : `Ошибка сервера (${status})`;
}

export async function authorizedRequest<T>(path: string, token: string, init: RequestInit | undefined,
  session: { isCurrent(token: string): boolean; expire(): void }, transport: typeof fetch = fetch): Promise<T> {
  if (!token || !session.isCurrent(token)) throw new SessionExpiredError();
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  headers.set("Authorization", `Basic ${token}`);
  const changing = !["GET", "HEAD"].includes((init?.method || "GET").toUpperCase());
  let response: Response;
  try { response = await transport(`/api${path}`, { ...init, headers }); }
  catch (cause) {
    if (!session.isCurrent(token)) throw new SessionExpiredError();
    if (changing) throw new UnknownMutationError();
    throw cause;
  }
  if (!session.isCurrent(token)) throw new SessionExpiredError();
  if (response.status === 401) {
    session.expire();
    throw new SessionExpiredError();
  }
  if (!response.ok) throw new Error(apiErrorMessage(await response.text(), response.status));
  let data: T;
  try { data = await response.json(); }
  catch (cause) {
    if (changing) throw new UnknownMutationError();
    throw cause;
  }
  if (!session.isCurrent(token)) throw new SessionExpiredError();
  return data;
}
