import type { createApiClient } from "./api-request";

export type CdnOperation = {
  id: string;
  enabled: boolean;
  state: "queued" | "running" | "succeeded" | "failed" | "unknown";
  progress: number;
  message: string;
  kind?: "ech";
  domain?: string;
  result?: EchRecord;
};
export type EchRecord = { domain: string; public_name: string; type: string; priority: number; target: string; ttl: number; parameters: string; content: string };
export type CdnSecurityStatus = { authenticated_origin_pulls: boolean; operation?: CdnOperation | null; cloudflare_domains?: string[] };
type Request = ReturnType<typeof createApiClient>;
type Report = (operation: CdnOperation) => void;
type Options = { signal?: AbortSignal; pollDelayMs?: number; timeoutMs?: number };
const PENDING_KEY = "cdn-pending-operation:v1";
export const CDN_PENDING_CHANGED = "cdn-pending-operation-changed";

export function restorePendingCdnOperation(value: unknown): CdnOperation | null {
  if (!record(value) || typeof value.id !== "string" || !/^[a-f0-9]{32}$/.test(value.id) || typeof value.enabled !== "boolean"
    || (value.kind !== undefined && value.kind !== "ech")
    || (value.kind === "ech" && (typeof value.domain !== "string" || !value.domain.trim()))
    || (value.kind === undefined && value.domain !== undefined)) return null;
  return { id: value.id, enabled: value.enabled, ...(value.kind === "ech" ? { kind: "ech", domain: value.domain as string } : {}),
    state: "unknown", progress: 0, message: "Результат команды не подтверждён. Проверяем сохранённый идентификатор." };
}

export function readPendingCdnOperation(): CdnOperation | null {
  if (typeof window === "undefined") return null;
  try { return restorePendingCdnOperation(JSON.parse(window.sessionStorage.getItem(PENDING_KEY) || "null")); }
  catch { return null; }
}

export function persistPendingCdnOperation(operation: CdnOperation | null, completedId?: string) {
  if (typeof window === "undefined") return;
  const current = readPendingCdnOperation();
  if (!operation && current && current.id !== completedId) return;
  const descriptor = operation ? { id: operation.id, enabled: operation.enabled, ...(operation.kind === "ech" ? { kind: "ech", domain: operation.domain } : {}) } : null;
  try {
    if (descriptor) window.sessionStorage.setItem(PENDING_KEY, JSON.stringify(descriptor));
    else window.sessionStorage.removeItem(PENDING_KEY);
  } catch { /* The active hook still guards its in-memory operation. */ }
  if (typeof window.dispatchEvent === "function") window.dispatchEvent(new window.CustomEvent(CDN_PENDING_CHANGED, { detail: descriptor }));
}

export function connectionInterrupted(error: unknown): boolean {
  return error instanceof Error && "kind" in error && (error.kind === "network" || error.kind === "response");
}

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

export function cdnSecuritySnapshot(value: unknown): CdnSecurityStatus {
  if (!record(value) || typeof value.authenticated_origin_pulls !== "boolean") throw new CdnResultUnknown("Некорректное состояние проверки сертификата CF");
  if (value.cloudflare_domains !== undefined && (!Array.isArray(value.cloudflare_domains) || value.cloudflare_domains.some(domain => typeof domain !== "string" || !domain))) throw new CdnResultUnknown("Некорректный список Cloudflare CDN");
  const operation = value.operation;
  if (operation != null) {
    if (!record(operation) || typeof operation.id !== "string" || !/^[a-f0-9]{32}$/.test(operation.id)
      || typeof operation.enabled !== "boolean" || !["queued", "running", "succeeded", "failed", "unknown"].includes(String(operation.state))
      || typeof operation.message !== "string" || typeof operation.progress !== "number" || !Number.isFinite(operation.progress) || operation.progress < 0 || operation.progress > 100
      || (operation.kind !== undefined && operation.kind !== "ech")
      || (operation.domain !== undefined && typeof operation.domain !== "string")) throw new CdnResultUnknown("Некорректные данные команды CF/ECH");
    if (operation.kind === "ech") {
      if (typeof operation.domain !== "string" || !operation.domain) throw new CdnResultUnknown("Не подтверждён домен команды ECH");
      const result = operation.result;
      if ((operation.state === "succeeded" && !record(result)) || (result !== undefined && (!record(result)
        || ["domain", "public_name", "type", "target", "parameters", "content"].some((key) => typeof result[key] !== "string")
        || ["priority", "ttl"].some((key) => !Number.isInteger(result[key]) || Number(result[key]) < 0)))) throw new CdnResultUnknown("Некорректная DNS-запись ECH");
      if (record(result) && typeof result.domain === "string" && canonicalDomain(result.domain) !== canonicalDomain(operation.domain)) throw new CdnResultUnknown("DNS-запись не соответствует домену команды ECH");
    }
  }
  return value as CdnSecurityStatus;
}

function canonicalDomain(value: string) {
  return value.trim().toLowerCase().replace(/\.+$/, "");
}

function matchingOperation(status: CdnSecurityStatus, initial: CdnOperation): CdnOperation {
  const operation = status.operation;
  if (!operation || operation.id !== initial.id || operation.enabled !== initial.enabled || operation.kind !== initial.kind
    || (initial.kind === "ech" && canonicalDomain(operation.domain || "") !== canonicalDomain(initial.domain || ""))) throw new CdnResultUnknown();
  if (operation.state === "succeeded" && operation.kind !== "ech" && status.authenticated_origin_pulls !== initial.enabled) {
    return { ...operation, message: "Команда завершена. Текущая настройка CF уже отличается от отправленной; проверьте состояние перед новой командой." };
  }
  return operation;
}

export class CdnResultUnknown extends Error {
  constructor(message = "Результат пока не подтверждён. Проверьте его после восстановления связи.") {
    super(message);
  }
}

function pause(delay: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal?.reason); };
    const timer = setTimeout(() => { signal?.removeEventListener("abort", abort); resolve(); }, delay);
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
  });
}

export async function followCdnSecurity(request: Request, initial: CdnOperation, report: Report, options: Options = {}): Promise<CdnSecurityStatus> {
  const deadline = Date.now() + (options.timeoutMs ?? 360000);
  const missingDeadline = Date.now() + 15000;
  let operation = initial;
  while (Date.now() < deadline) {
    options.signal?.throwIfAborted();
    try {
      const status = cdnSecuritySnapshot(await request<CdnSecurityStatus>(`/application/cdn-security?operation_id=${initial.id}`, { signal: options.signal, cache: "no-store" }));
      options.signal?.throwIfAborted();
      operation = matchingOperation(status, initial);
      report(operation);
      if (operation.state === "succeeded" || operation.state === "failed") return { ...status, operation };
    } catch (error) {
      options.signal?.throwIfAborted();
      const missing = error instanceof Error && "status" in error && error.status === 404;
      if (missing && Date.now() >= missingDeadline) throw new CdnResultUnknown();
      const transient = error instanceof Error && "status" in error && ([408, 429].includes(Number(error.status)) || Number(error.status) >= 500);
      if (!connectionInterrupted(error) && !missing && !transient) throw new CdnResultUnknown(error instanceof Error ? error.message : undefined);
      report({ ...operation, state: "unknown", message: "Связь прервана; продолжаем проверять результат команды…" });
    }
    await pause(options.pollDelayMs ?? 1500, options.signal);
  }
  throw new CdnResultUnknown();
}

export async function submitCdnSecurity(request: Request, operation: CdnOperation, report: Report, options: Options = {}) {
  report(operation);
  let tracked = operation;
  try {
    const status = cdnSecuritySnapshot(await request<CdnSecurityStatus>(operation.kind === "ech" ? "/application/ech" : "/application/cdn-security", {
      method: "PUT", body: JSON.stringify(operation.kind === "ech" ? { domain: operation.domain, operation_id: operation.id } : { authenticated_origin_pulls: operation.enabled, operation_id: operation.id }), signal: options.signal,
    }));
    options.signal?.throwIfAborted();
    tracked = matchingOperation(status, operation);
    report(tracked);
    if (["succeeded", "failed"].includes(tracked.state)) return { ...status, operation: tracked };
  } catch (error) {
    options.signal?.throwIfAborted();
    const rejected = error instanceof Error && "status" in error && Number(error.status) >= 400 && Number(error.status) < 500;
    const serverError = error instanceof Error && "status" in error && Number(error.status) >= 500;
    if (rejected || (!connectionInterrupted(error) && !serverError && !(error instanceof CdnResultUnknown))) throw error;
    report({ ...operation, state: "unknown", message: "Проверяем результат команды после потери подтверждения…" });
  }
  // Never replay a write, even when its acknowledgement was lost.
  return followCdnSecurity(request, tracked, report, options);
}

export function newCdnOperation(enabled: boolean): CdnOperation {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return { id: Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join(""), enabled, state: "queued", progress: 0, message: "Отправляем команду проверки CF…" };
}
