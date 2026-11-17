import type { createApiClient } from "../../shared/lib/api-request";

type Client = Pick<ReturnType<typeof createApiClient>, "sessionKey">;
export type SessionTurn = { id: number; question: string; model: string; answer?: string; error?: string; tools?: string[]; context_safe?: boolean; files: [] };
export type ChatSession = { model: string; provider: string; streaming: boolean; conversation_id: string | null; prompt: string; turns: SessionTurn[] };
const MAX_CHARS = 240000;
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
    return { ...value, turns: value.turns.map((turn: SessionTurn) => ({ id: turn.id, question: turn.question, model: turn.model, answer: turn.answer, error: turn.error, tools: Array.isArray(turn.tools) ? turn.tools.filter(tool => typeof tool === "string") : [], context_safe: typeof turn.context_safe === "boolean" ? turn.context_safe : undefined, files: [] })) };
  } catch { /* A damaged snapshot must not block server history. */ }
}
export function saveChatSession(client: Client, actor: string, value: Omit<ChatSession, "turns"> & { turns: (Omit<SessionTurn, "files"> & { files?: unknown[]; queue_state?: "queued" | "running" })[] }) {
  const identity = key(client, actor);
  if (!identity) return;
  const turns: SessionTurn[] = value.turns.filter(turn => turn.queue_state !== "running").slice(-40).map(turn => ({ id: turn.id, question: turn.question.slice(0, 12000), model: turn.model, answer: turn.answer?.slice(0, 20000), tools: turn.tools, context_safe: turn.context_safe, files: [],
    error: turn.queue_state === "queued" ? "Не отправлено после обновления страницы. Отправьте сообщение вручную." : turn.error?.slice(0, 2000) }));
  const snapshot = { ...value, turns };
  while (JSON.stringify(snapshot).length > MAX_CHARS && turns.length) turns.shift();
  try { sessionStorage.setItem(identity, JSON.stringify(snapshot)); } catch { /* Server history remains authoritative. */ }
}
export function clearChatSessions(client: Client) {
  if (!client.sessionKey) return;
  try {
    for (let index = sessionStorage.length - 1; index >= 0; index--) {
      const identity = sessionStorage.key(index);
      if (identity?.startsWith(`nodera-session:${client.sessionKey}:`)) sessionStorage.removeItem(identity);
    }
  } catch { /* Browser storage may be unavailable. */ }
}
