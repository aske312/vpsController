import type { PendingAction } from "./confirmation-queue";
import { ApiRequestError, type createApiClient } from "../../shared/lib/api-request.ts";

type Client = ReturnType<typeof createApiClient>;
export type ChatReplyStatus = "incomplete" | "partial" | "awaiting_confirmation";
export type ChatOutput = { result: { answer: string; tools_used: string[]; status?: ChatReplyStatus }; conversation_id?: string; cache_error?: string; duration_ms?: number;
  provider_usage?: Record<string, import("./assistant-state").ProviderUsage>;
  pending_action?: PendingAction; pending_actions?: PendingAction[] };
export type ChatJob = { id: string; seq: number; state: "queued" | "running" | "done" | "failed" | "unknown";
  stage: string; question: string; model: string; stream: boolean; partial?: string; output?: ChatOutput;
  created_at?: number; queue_ahead?: number; duration_ms?: number; error?: { code: string; message: string } };
export class ChatJobError extends Error {
  job: ChatJob;
  constructor(job: ChatJob) { super(`${job.error?.message ?? "Результат AI не подтверждён"} · ${job.error?.code ?? job.state} · запрос ${job.id}`); this.job = job; }
}
export function pendingChatKey(client: Client, actor: string) { return `nodera-pending:${client.sessionKey}:${actor}`; }
export function rememberChat(client: Client, actor: string, id: string | null) {
  if (!client.sessionKey) return;
  try {
    if (id) sessionStorage.setItem(pendingChatKey(client, actor), JSON.stringify([...new Set([...savedChats(client, actor), id])].slice(0, 16)));
    else sessionStorage.removeItem(pendingChatKey(client, actor));
  } catch { /* Server result remains available if browser storage is disabled. */ }
}
export function savedChat(client: Client, actor: string) {
  return savedChats(client, actor)[0] ?? null;
}
export function savedChats(client: Client, actor: string): string[] {
  if (!client.sessionKey) return [];
  try {
    const raw = sessionStorage.getItem(pendingChatKey(client, actor));
    if (!raw) return [];
    if (/^[0-9a-f]{32}$/.test(raw)) return [raw];
    const ids: unknown = JSON.parse(raw);
    return Array.isArray(ids) ? [...new Set(ids.filter((id): id is string => typeof id === 'string' && /^[0-9a-f]{32}$/.test(id)))].slice(0,16) : [];
  } catch { return []; }
}
export function forgetChat(client: Client, actor: string, id: string) {
  const ids = savedChats(client, actor).filter(value => value !== id);
  if (!client.sessionKey) return;
  try {
    if (ids.length) sessionStorage.setItem(pendingChatKey(client, actor), JSON.stringify(ids));
    else sessionStorage.removeItem(pendingChatKey(client, actor));
  } catch { /* Server queue remains authoritative. */ }
}
export const chatStage = (stage: string) => ({ queued: "Подготавливаю запрос", waiting_provider: "Ожидаю завершения другого запроса", starting: "Подключаюсь к модели",
  waiting_model: "Обдумываю ваш запрос", receiving: "Формирую ответ", tools: "Работаю с инструментами", reconnecting: "Восстанавливаю связь" }[stage] ?? "Запрос в работе");

export function chatActivityLabel(turn: { queue_state?: string; activity_stage?: string; queue_ahead?: number }, earlier = 0) {
  const ahead = turn.queue_ahead ?? earlier;
  if (turn.queue_state === "queued" && ahead > 0) return `В очереди · запросов впереди: ${ahead}`;
  return chatStage(turn.activity_stage ?? "queued");
}

class ChatJobProtocolError extends Error {}
function checkedJob(value: unknown, identity: string): ChatJob {
  const invalid = () => { throw new ChatJobProtocolError(`Некорректный ответ о запросе AI ${identity}. Повторной отправки не было`); };
  if (!value || typeof value !== "object") return invalid();
  const job = value as ChatJob;
  if (job.id !== identity || !["queued", "running", "done", "failed", "unknown"].includes(job.state)
      || !Number.isSafeInteger(job.seq) || job.seq < 0 || typeof job.stage !== "string"
      || typeof job.question !== "string" || typeof job.model !== "string"
      || job.partial !== undefined && typeof job.partial !== "string") return invalid();
  if (job.error !== undefined && (!job.error || typeof job.error.code !== "string" || typeof job.error.message !== "string")) return invalid();
  if (job.queue_ahead !== undefined && (!Number.isSafeInteger(job.queue_ahead) || job.queue_ahead < 0 || job.queue_ahead > 64)) return invalid();
  if (job.created_at !== undefined && (typeof job.created_at !== "number" || !Number.isFinite(job.created_at) || job.created_at < 0)) return invalid();
  if (job.duration_ms !== undefined && (typeof job.duration_ms !== "number" || !Number.isFinite(job.duration_ms) || job.duration_ms < 0)) return invalid();
  if (job.output !== undefined || job.state === "done") {
    const result = job.output?.result;
    if (!result || typeof result.answer !== "string" || !Array.isArray(result.tools_used)
        || result.tools_used.some(name => typeof name !== "string")
        || result.status !== undefined && !["incomplete", "partial", "awaiting_confirmation"].includes(result.status)) return invalid();
  }
  return job;
}

export async function observeChatJob(client: Client, id: string, actor: string, streaming: boolean,
  publish: (job: ChatJob) => void, signal: AbortSignal): Promise<ChatOutput> {
  const path = `/assistant/requests/${id}`;
  const query = actor ? `?telegram_id=${actor}` : "";
  let terminal: ChatJob | undefined;
  let failures = 0;
  let sequence = -1;
  const started = Date.now();
  const receive = (value: unknown) => {
    const job = checkedJob(value, id);
    if (job.seq < sequence) return;
    sequence = job.seq;
    publish(job);
    if (["done", "failed", "unknown"].includes(job.state)) terminal = job;
  };
  while (!signal.aborted && Date.now() - started < 2400000) {
    try {
      receive(await client<ChatJob>(path + query, { cache: "no-store", signal }));
      if (terminal) break;
      if (streaming && failures < 3 && client.events) await client.events(path + "/stream" + query, receive, signal);
      if (terminal) break;
    } catch (cause) {
      if (signal.aborted) throw cause;
      if (cause instanceof ChatJobProtocolError) throw cause;
      if (cause instanceof ApiRequestError && [401, 403, 404, 410].includes(cause.status ?? 0)) {
        if ([404, 410].includes(cause.status ?? 0)) forgetChat(client, actor, id);
        throw cause;
      }
      failures++;
    }
    await new Promise<void>((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(signal.reason ?? new Error("Наблюдение остановлено")); };
      const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, Math.min(10000, 1500 * (failures + 1)));
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) abort();
    });
  }
  if (!terminal) throw new Error(`Связь с AI не восстановлена. Результат сохранён под ID ${id}; повторной отправки не было`);
  forgetChat(client, actor, id);
  if (terminal.state !== "done" || !terminal.output) throw new ChatJobError(terminal);
  return { ...terminal.output, ...(terminal.duration_ms !== undefined ? { duration_ms: terminal.duration_ms } : {}) };
}
