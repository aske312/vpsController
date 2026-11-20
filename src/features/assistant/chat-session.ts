import { messageCommandIds } from "./command-message-state.ts";
import type { createApiClient } from "../../shared/lib/api-request";
import type { ChatReplyStatus } from "./chat-jobs";

type Client = Pick<ReturnType<typeof createApiClient>, "sessionKey">;
export type SessionTurn = { status?: ChatReplyStatus; duration_ms?: number; command_ids?: string[]; system_event?: boolean; request_id?: string; id: number; question: string; model: string; answer?: string; error?: string; tools?: string[]; context_safe?: boolean; files: [] };
export type ChatSession = { model: string; provider: string; streaming: boolean; conversation_id: string | null; prompt: string; turns: SessionTurn[] };
const MAX_CHARS = 240000;
function replyState(turn: { status?: unknown; duration_ms?: unknown }): { status?: ChatReplyStatus; duration_ms?: number } {
  return {
    ...(turn.status === "incomplete" || turn.status === "partial" || turn.status === "awaiting_confirmation" ? { status: turn.status } : {}),
    ...(typeof turn.duration_ms === "number" && Number.isFinite(turn.duration_ms) && turn.duration_ms >= 0 ? { duration_ms: turn.duration_ms } : {}),
  };
}
function commandLinks(turn: Parameters<typeof messageCommandIds>[0]) {
  const ids = messageCommandIds(turn);
  return ids.length ? { command_ids: ids } : {};
}
type Snapshot = Parameters<typeof saveChatSession>[2];
type Writer = { schedule: (value: Snapshot) => void; flush: () => void; dispose: (flush?: boolean) => void };
const writers = new Map<Writer, string | undefined>();

export function createChatSessionWriter(client: Client, actor: string, delay = 300): Writer {
  let pending: Snapshot | undefined, timer: ReturnType<typeof setTimeout> | undefined, active = true;
  const flush = () => {
    clearTimeout(timer); timer = undefined;
    if (!active || !pending) return;
    const value = pending; pending = undefined;
    saveChatSession(client, actor, value);
  };
  const writer: Writer = {
    schedule(value) { if (!active) return; pending = value; clearTimeout(timer); timer = setTimeout(flush, delay); },
    flush,
    dispose(save = true) { if (save) flush(); active = false; pending = undefined; clearTimeout(timer); writers.delete(writer); },
  };
  writers.set(writer, client.sessionKey);
  return writer;
}
export function chatProvider(settings: { active_provider?: string | null; provider_url: string; model_mode?: string }) {
  return JSON.stringify([settings.active_provider || "", settings.provider_url, settings.model_mode || "single"]);
}
function key(client: Client, actor?: string) {
  return client.sessionKey ? `nodera-session:${client.sessionKey}:${actor === undefined ? "actor" : actor || "panel"}` : null;
}
export function savedActor(client: Client) {
  try { const value = sessionStorage.getItem(key(client) || ""); return value && /^[1-9][0-9]{0,15}$/.test(value) ? value : ""; } catch { return ""; }
}
export function rememberActor(client: Client, actor: string) {
  const identity = key(client);
  if (identity) try { sessionStorage.setItem(identity, actor); } catch { /* Optional browser persistence. */ }
}
export function readChatSession(client: Client, actor: string): ChatSession | undefined {
  const identity = key(client, actor);
  if (!identity) return;
  try {
    const raw = sessionStorage.getItem(identity);
    if (!raw || raw.length > MAX_CHARS) return;
    const value = JSON.parse(raw);
    if (typeof value.model !== "string" || typeof value.provider !== "string" || typeof value.streaming !== "boolean" || typeof value.prompt !== "string" || value.prompt.length > 12000 || !Array.isArray(value.turns) || value.turns.length > 40) return;
    if (value.conversation_id !== null && !/^[a-f0-9]{32}$/.test(value.conversation_id)) return;
    if (value.turns.some((turn: SessionTurn) => !Number.isSafeInteger(turn.id) || turn.id < 1 || typeof turn.question !== "string" || typeof turn.model !== "string" || turn.answer !== undefined && typeof turn.answer !== "string" || turn.error !== undefined && typeof turn.error !== "string")) return;
    return { ...value, turns: value.turns.map((turn: SessionTurn) => ({ ...replyState(turn), ...(turn.request_id && /^[a-f0-9]{32}$/.test(turn.request_id) ? { request_id: turn.request_id } : {}), ...(turn.system_event === true ? { system_event: true } : {}), ...commandLinks({ id: turn.id, command_ids: turn.command_ids }), id: turn.id, question: turn.question, model: turn.model, answer: turn.answer, error: turn.error, tools: Array.isArray(turn.tools) ? turn.tools.filter(tool => typeof tool === "string") : [], context_safe: typeof turn.context_safe === "boolean" ? turn.context_safe : undefined, files: [] })) };
  } catch { /* A damaged snapshot must not block server history. */ }
}
export function saveChatSession(client: Client, actor: string, value: Omit<ChatSession, "turns"> & { turns: (Omit<SessionTurn, "files"> & { files?: unknown[]; queue_state?: "queued" | "running"; pending_action?: { operation_id?: string }; pending_actions?: { operation_id?: string }[] })[] }) {
  const identity = key(client, actor);
  if (!identity) return;
  const turns: SessionTurn[] = value.turns.filter(turn => turn.queue_state !== "running" && turn.queue_state !== "queued").slice(-40).map(turn => ({ ...replyState(turn), ...(turn.request_id && /^[a-f0-9]{32}$/.test(turn.request_id) ? { request_id: turn.request_id } : {}), ...(turn.system_event === true ? { system_event: true } : {}), ...commandLinks(turn), id: turn.id, question: turn.question.slice(0, 12000), model: turn.model, answer: turn.answer?.slice(0, 20000), tools: turn.tools, context_safe: turn.context_safe, files: [],
    error: turn.error?.slice(0, 2000) }));
  const snapshot = { ...value, turns };
  let size = JSON.stringify({ ...value, turns: [] }).length;
  const sizes = turns.map(turn => JSON.stringify(turn).length + 1);
  size += sizes.reduce((sum, length) => sum + length, 0);
  while (size > MAX_CHARS && turns.length) { turns.shift(); size -= sizes.shift()!; }
  try { sessionStorage.setItem(identity, JSON.stringify(snapshot)); } catch { /* Server history remains authoritative. */ }
}
export function clearChatSessions(client: Client) {
  if (!client.sessionKey) return;
  // Logout must also cancel buffered drafts; unmount cannot restore them later.
  for (const [writer, scope] of writers) if (scope === client.sessionKey) writer.dispose(false);
  try {
    for (let index = sessionStorage.length - 1; index >= 0; index--) {
      const identity = sessionStorage.key(index);
      if (identity?.startsWith(`nodera-session:${client.sessionKey}:`)) sessionStorage.removeItem(identity);
    }
  } catch { /* Browser storage may be unavailable. */ }
}
