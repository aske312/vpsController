import type { createApiClient } from "./api-request";

export type CdnOperation = {
  id: string;
  enabled: boolean;
  state: "queued" | "running" | "succeeded" | "failed" | "unknown";
  progress: number;
  message: string;
};
export type CdnSecurityStatus = { authenticated_origin_pulls: boolean; operation?: CdnOperation | null };
type Request = ReturnType<typeof createApiClient>;
type Report = (operation: CdnOperation) => void;
type Options = { signal?: AbortSignal; pollDelayMs?: number; timeoutMs?: number };

export function connectionInterrupted(error: unknown): boolean {
  return error instanceof Error && "kind" in error && error.kind === "network";
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
      const status = await request<CdnSecurityStatus>(`/application/cdn-security?operation_id=${initial.id}`, { signal: options.signal });
      options.signal?.throwIfAborted();
      if (!status.operation || status.operation.id !== initial.id) throw new CdnResultUnknown();
      operation = status.operation;
      report(operation);
      if (operation.state === "succeeded" || operation.state === "failed") return status;
    } catch (error) {
      options.signal?.throwIfAborted();
      const missing = error instanceof Error && "status" in error && error.status === 404;
      if (missing && Date.now() >= missingDeadline) throw new CdnResultUnknown();
      if (!connectionInterrupted(error) && !missing) throw new CdnResultUnknown(error instanceof Error ? error.message : undefined);
      report({ ...operation, state: "running", message: "Восстанавливаем связь и проверяем результат команды…" });
    }
    await pause(options.pollDelayMs ?? 1500, options.signal);
  }
  throw new CdnResultUnknown();
}

export async function submitCdnSecurity(request: Request, operation: CdnOperation, report: Report, options: Options = {}) {
  report(operation);
  let tracked = operation;
  try {
    const status = await request<CdnSecurityStatus>("/application/cdn-security", {
      method: "PUT", body: JSON.stringify({ authenticated_origin_pulls: operation.enabled, operation_id: operation.id }), signal: options.signal,
    });
    options.signal?.throwIfAborted();
    if (status.operation?.id !== operation.id) throw new CdnResultUnknown();
    tracked = status.operation;
    report(status.operation);
    if (["succeeded", "failed"].includes(status.operation.state)) return status;
  } catch (error) {
    options.signal?.throwIfAborted();
    if (!connectionInterrupted(error)) throw error;
    report({ ...operation, state: "running", message: "Проверяем результат команды после разрыва связи…" });
  }
  // Never replay a write, even when its acknowledgement was lost.
  return followCdnSecurity(request, tracked, report, options);
}

export function newCdnOperation(enabled: boolean): CdnOperation {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return { id: Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join(""), enabled, state: "queued", progress: 0, message: "Отправляем команду проверки CF…" };
}
