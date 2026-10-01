export class ApiError extends Error { constructor(message: string, readonly status: number) { super(message); } }
export class LightApi {
  constructor(private readonly credentials: string) {}
  async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`/api${path}`, { ...init, headers: { "Content-Type": "application/json", Authorization: `Basic ${this.credentials}`, ...init?.headers } });
    if (!response.ok) {
      const raw = await response.text(); let detail = raw;
      try { detail = (JSON.parse(raw) as { detail?: string }).detail || raw; } catch { /* text response */ }
      throw new ApiError(response.status === 401 ? "Сессия завершена" : detail || `Ошибка ${response.status}`, response.status);
    }
    return response.json() as Promise<T>;
  }
}
export function basicCredentials(username: string, password: string) { return btoa(unescape(encodeURIComponent(`${username}:${password}`))); }
