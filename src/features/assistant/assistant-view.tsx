/* eslint-disable @next/next/no-img-element */
"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ProtocolIcon } from "../../shared/components/protocol-icon";
import { createApiClient } from "../../shared/lib/api-request";
import { useNotifier, useNotifications } from "../../shared/notifications/notification-center";
import { mutationFailureState } from "../../shared/lib/api-request";
import { observeChatJob, rememberChat, savedChat, chatStage, ChatJobError, type ChatJob, type ChatOutput } from "./chat-jobs";
import { ConfirmationCards, useAssistantConfirmations, type PendingAction } from "./confirmation-queue";
import { ChatIcon } from "./chat-icon";
import { ChatTurnRow, type ChatTurn as Turn } from "./chat-turn";
import { conversationMarkdown } from "./answer-format";
import { chatHistory, fileAccept, readChatFiles, type ChatFile } from "./chat-files";
import { readChatSession, createChatSessionWriter, savedActor, rememberActor, chatProvider } from "./chat-session";
import type { ConfirmationRequest } from "../../shared/types/control-plane";
import { systemActionSnapshot, type SystemAction } from "../../control-panel/system-operation";
import { ChatCommandMenu } from "./chat-command-menu";
import { commandMatches, directCommand, type ChatCommand } from "./chat-commands";
import { changed, chatModels, makeDraft, settingsPayload, type Draft, type McpStatus, type ModuleId } from "./assistant-state";
import { ProviderMonitor, initialProviderConnection, providerStatusText, type ProviderConnection } from "./provider-connection";
import { queueProviderCatalog } from "./provider-details";
import { NoderaSettings, SettingsDialog, panels, type Panel } from "./nodera-settings";

type Client = ReturnType<typeof createApiClient>;
type Props = {
  active: boolean; onOpenChat: () => void;
  request: Client; refreshKey: number; busy: boolean;
  onDirtyChange: (dirty: boolean) => void;
  confirmAction: (options: Omit<ConfirmationRequest, "resolve">) => Promise<boolean>;
  admitOperation: (path: string, options: RequestInit) => Promise<SystemAction | null | undefined>;
  onOperation: (operation: SystemAction) => void;
};
type Conversation = { id: string; title: string; updated_at: string; turns: number };
function shortTitle(title: string) {
  const characters = Array.from(title);
  return characters.length > 20 ? characters.slice(0, 19).join("") + "…" : title;
}

export function AssistantView({ active, onOpenChat, request, refreshKey, busy, onDirtyChange, confirmAction, admitOperation, onOperation }: Props) {
  const [panel, setPanel] = useState<Panel>('chat');
  const [streaming, setStreaming] = useState(false);
  const [recoveryKey, setRecoveryKey] = useState(0);
  const [hasPendingChat, setHasPendingChat] = useState(false);
  const [chatStageText, setChatStageText] = useState("");
  const observation = useRef<AbortController | null>(null);
  useEffect(() => () => { observation.current?.abort(); pendingTurns.current = []; writing.current = false; chatRunning.current = false; }, [request]);
  const [chatSettingsOpen, setChatSettingsOpen] = useState(false);
  const sectionMenu = useRef<HTMLDetailsElement>(null);
  const [status, setStatus] = useState<McpStatus>();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loadError, setLoadError] = useState("");
  const notify = useNotifier("nodera", "Nodera");
  const notifications = useNotifications();
  const visible = useRef(active);
  const openChat = useRef(onOpenChat);
  useEffect(() => { openChat.current = onOpenChat; }, [onOpenChat]);
  useEffect(() => { visible.current = active; if (active) setPanel('chat'); }, [active]);
  useEffect(() => {
    const markRead = () => {
      if (!active || panel !== "chat" || typeof document !== "undefined" && document.hidden) return;
      for (const item of notifications.getSnapshot()) if (item.id.startsWith("nodera-answer:")) notifications.resolve(item.id);
    };
    markRead();
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", markRead);
    return () => { if (typeof document !== "undefined") document.removeEventListener("visibilitychange", markRead); };
  }, [active, panel, notifications]);
  const setError = (text: string) => { if (text) notify.error(text); };
  const setMessage = (text: string) => { if (text) notify.success(text); };
  const [working, setWorking] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [actor, setActor] = useState("");
  const [sessionReady, setSessionReady] = useState(!request.sessionKey);
  const selectedConversation = useRef<string | null | undefined>(undefined);
  const [prompt, setPrompt] = useState("");
  const [commands, setCommands] = useState<ChatCommand[]>([]);
  const [commandMenu, setCommandMenu] = useState(false);
  const [commandIndex, setCommandIndex] = useState(0);
  const [commandLoading, setCommandLoading] = useState(false);
  const [commandError, setCommandError] = useState("");
  const [commandBusy, setCommandBusy] = useState(false);
  const commandRunning = useRef(false);
  const commandVersion = useRef(0);
  const promptInput = useRef<HTMLTextAreaElement>(null);
  const promptFrame = useRef<number | null>(null);
  useEffect(() => () => { if (promptFrame.current !== null) cancelAnimationFrame(promptFrame.current); }, []);
  const [catalog, setCatalog] = useState<string[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const [model, setModel] = useState("");
  const [modelMenu, setModelMenu] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [memoryBusy, setMemoryBusy] = useState(false);
  const [memoryError, setMemoryError] = useState('');
  const memoryGeneration = useRef(0);
  const activeActor = useRef('');
  const interacted = useRef(false);
  const [files, setFiles] = useState<ChatFile[]>([]);
  const [readingFiles, setReadingFiles] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const followTranscript = useRef(true);
  const sessionWriter = useRef<ReturnType<typeof createChatSessionWriter> | null>(null);
  const modelButton = useRef<HTMLButtonElement>(null);
  const modelMenuPanel = useRef<HTMLDivElement>(null);
  const fileRead = useRef(false);
  const apiMonitor = useRef<ProviderMonitor | null>(null);
  const [apiConnection, setApiConnection] = useState<ProviderConnection>(initialProviderConnection);
  const [chatConnection, setChatConnection] = useState<"unknown" | "responding" | "failed">("unknown");
  const [turns, setTurns] = useState<Turn[]>([]);
  const confirmations = useAssistantConfirmations();
  const ingestConfirmations = confirmations.ingest;
  const nextTurn = useRef(0);
  const decisionContext = useRef(new Set<string>());
  useEffect(() => {
    const incoming = (confirmations.context ?? []).filter(item => item.actor === (actor || "panel") && !decisionContext.current.has(item.key));
    if (!incoming.length) return;
    for (const item of incoming) decisionContext.current.add(item.key);
    setTurns(current => [...current, ...incoming.filter(item => !current.some(turn => /^\/(confirm|cancel)\s+/i.test(turn.question) && turn.question.trim().endsWith(item.key.split(":").at(-1)!))).map(item => ({ id: ++nextTurn.current, question: item.question, answer: item.answer, files: [], model: "", context_safe: true }))]);
  }, [confirmations.context, actor]);
  const [checks, setChecks] = useState<Partial<Record<ModuleId, { ok: boolean; text: string; revision: string; actor: string; username?: string }>>>({});
  const version = useRef(0);
  const client = useRef(request);
  const writing = useRef(false);
  const chatRunning = useRef(false);
  const pendingTurns = useRef<Turn[]>([]);
  const chatRequestId = useRef("");
  const [chatQueue, setChatQueue] = useState<{state: string; position: number; waiting?: number} | null>(null);
  const dirtyRef = useRef(false);
  const dirty = useMemo(() => changed(draft, status?.settings), [draft, status?.settings]);
  const availableModels = useMemo(() => status ? chatModels(status.settings, catalog.length ? catalog : model ? [model] : []) : [], [status, catalog, model]);
  useEffect(() => {
    if (!active && dirty && status) setDraft(makeDraft(status.settings));
  }, [active, dirty, status]);
  const catalogRevision = status?.settings.api_key_configured ? status.settings.revision : "";
  const savedProviderUrl = status?.settings.provider_url ?? "";
  const savedProviderId = status?.settings.active_provider ?? null;
  const cacheEnabled = status?.settings.memory_enabled ?? true;
  const agentInstalled = status?.modules.some(item => item.id === 'ai-agent' && item.installed) === true;
  useEffect(() => {
    if (!status || sessionReady) return;
    const previousActor = savedActor(request);
    const identity = status.settings.accounts.some(item => String(item.telegram_id) === previousActor) ? previousActor : "";
    const saved = readChatSession(request, identity);
    activeActor.current = identity; setActor(identity);
    if (saved) {
      selectedConversation.current = saved.conversation_id;
      setConversationId(saved.conversation_id); setPrompt(saved.prompt); setStreaming(saved.streaming); setTurns(saved.turns);
      nextTurn.current = Math.max(0, ...saved.turns.map(turn => turn.id));
      if (saved.provider === chatProvider(status.settings)) setModel(saved.model);
    }
    setSessionReady(true);
  }, [status, request, sessionReady]);
  useEffect(() => {
    const writer = createChatSessionWriter(request, actor);
    sessionWriter.current = writer;
    const flush = () => writer.flush();
    const hidden = () => { if (document.hidden) writer.flush(); };
    if (typeof window !== "undefined") window.addEventListener("pagehide", flush);
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", hidden);
    return () => {
      if (typeof window !== "undefined") window.removeEventListener("pagehide", flush);
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", hidden);
      writer.dispose(); if (sessionWriter.current === writer) sessionWriter.current = null;
    };
  }, [request, actor]);
  const sessionProvider = status ? chatProvider(status.settings) : "";
  useEffect(() => { if (sessionReady) rememberActor(request, actor); }, [request, sessionReady, actor]);
  useEffect(() => {
    if (!sessionReady || !status || activeActor.current !== actor) return;
    sessionWriter.current?.schedule({ model, provider: sessionProvider, streaming, conversation_id: conversationId, prompt, turns });
  }, [request, sessionReady, sessionProvider, actor, model, streaming, conversationId, prompt, turns, status]);
  useEffect(() => { client.current = request; }, [request]);
  useEffect(() => { dirtyRef.current = dirty; if (active) onDirtyChange(dirty); }, [dirty, active, onDirtyChange]);
  useEffect(() => () => { version.current += 1; onDirtyChange(false); }, [onDirtyChange]);
  const load = useCallback(async () => {
    if (writing.current) return;
    const generation = ++version.current;
    try {
      const next = await request<McpStatus>("/assistant");
      if (generation !== version.current || client.current !== request) return;
      setStatus(next);
      if (!dirtyRef.current) setDraft(makeDraft(next.settings));
      setLoadError("");
    } catch (cause) {
      if (generation === version.current && client.current === request) setLoadError(cause instanceof Error ? cause.message : "Не удалось загрузить MCP");
    }
  }, [request]);
  useEffect(() => { void load(); }, [load, refreshKey]);
  const loadCommands = useCallback(async () => {
    if (!sessionReady) return;
    const generation = ++commandVersion.current;
    setCommandLoading(true);
    try {
      const value = await request<{ items: ChatCommand[] }>(`/assistant/commands${actor ? `?telegram_id=${actor}` : ""}`, { cache: "no-store" });
      if (generation !== commandVersion.current || client.current !== request || activeActor.current !== actor) return;
      setCommands(Array.isArray(value.items) ? value.items : []); setCommandError("");
    } catch (cause) {
      if (generation === commandVersion.current && client.current === request) setCommandError(cause instanceof Error ? cause.message : "Команды недоступны");
    } finally { if (generation === commandVersion.current) setCommandLoading(false); }
  }, [request, actor, sessionReady]);
  useEffect(() => { void loadCommands(); }, [loadCommands, status?.settings.revision]);
  const loadModels = useCallback(async () => { await apiMonitor.current?.refresh(); }, []);
  useEffect(() => {
    apiMonitor.current?.stop();
    setCatalog([]); setCatalogError(""); setCatalogLoading(false); setApiConnection(initialProviderConnection); setChatConnection("unknown");
    if (!catalogRevision || !savedProviderUrl) { apiMonitor.current = null; return; }
    const monitor = new ProviderMonitor({
      automatic: typeof window !== "undefined",
      canPoll: () => !writing.current && (typeof document === "undefined" || document.visibilityState !== "hidden"),
      fetch: signal => queueProviderCatalog(`${catalogRevision}:${savedProviderId}`, () => request<import("./provider-details").ProviderCatalog>("/assistant/models", { method: "POST", signal,
        body: JSON.stringify({ provider_url: savedProviderUrl, provider_id: savedProviderId, api_key: null, include_quota: true }) }), signal),
      publish: value => { setApiConnection(value); setCatalog(value.models); setCatalogError(value.error); setCatalogLoading(value.state === "checking"); },
    });
    apiMonitor.current = monitor;
    void monitor.refresh();
    const resume = () => monitor.resume();
    if (typeof window !== "undefined") { window.addEventListener("online", resume); window.addEventListener("focus", resume); }
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", resume);
    return () => {
      monitor.stop();
      if (typeof window !== "undefined") { window.removeEventListener("online", resume); window.removeEventListener("focus", resume); }
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", resume);
    };
  }, [catalogRevision, savedProviderUrl, savedProviderId, request]);
  useEffect(() => { if (modelMenu) modelMenuPanel.current?.querySelector<HTMLButtonElement>('[role="option"]')?.focus(); }, [modelMenu]);
  const loadConversation = useCallback(async (identity: string, restore = false) => {
    if (writing.current || fileRead.current) return;
    const generation = ++memoryGeneration.current;
    setMemoryBusy(true); setMemoryError('');
    try {
      const value = await request<{ id: string; turns: Turn[] }>(`/assistant/conversations/${identity}${actor ? `?telegram_id=${actor}` : ''}`);
      if (generation !== memoryGeneration.current || client.current !== request || activeActor.current !== actor) return;
      setConversationId(value.id);
      if (restore) {
        setTurns(current => {
          if (!current.length) return value.turns;
          const available = [...value.turns].reverse();
          return [...current].reverse().map(turn => {
            const index = available.findIndex(item => item.question === turn.question && item.answer === turn.answer);
            const server = index >= 0 ? available.splice(index, 1)[0] : undefined;
            return server ? { ...turn, files: server.files, files_expired: server.files_expired } : turn;
          }).reverse();
        });
        setModel(current => current || value.turns.at(-1)?.model || '');
      } else { setTurns(value.turns); setFiles([]); setPrompt(''); setModel(value.turns.at(-1)?.model ?? ''); }
      interacted.current = true; selectedConversation.current = value.id;
      nextTurn.current = Math.max(nextTurn.current, ...value.turns.map(turn => turn.id));
    } catch (cause) { if (generation === memoryGeneration.current) setMemoryError(cause instanceof Error ? cause.message : 'Не удалось загрузить диалог'); }
    finally { if (generation === memoryGeneration.current) setMemoryBusy(false); }
  }, [request, actor]);
  const loadConversations = useCallback(async (restore = false) => {
    try {
      const value = await request<{ enabled: boolean; items: Conversation[] }>(`/assistant/conversations${actor ? `?telegram_id=${actor}` : ''}`);
      if (client.current !== request || activeActor.current !== actor) return;
      setConversations(value.items ?? []);
      if (restore && value.items?.length && !writing.current && !interacted.current) {
        const previous = selectedConversation.current;
        const identity = previous === undefined ? value.items[0].id : value.items.find(item => item.id === previous)?.id;
        if (identity) await loadConversation(identity, true);
      }
    } catch (cause) { if (client.current === request && activeActor.current === actor) setMemoryError(cause instanceof Error ? cause.message : 'Кеш диалогов недоступен'); }
  }, [request, actor, loadConversation]);
  const invalidateMemory = useCallback(() => { memoryGeneration.current++; }, []);
  useEffect(() => {
    if (!sessionReady) return;
    if (agentInstalled && cacheEnabled) void loadConversations(true);
    else { setConversations([]); setMemoryBusy(false); }
    return invalidateMemory;
  }, [agentInstalled, cacheEnabled, loadConversations, invalidateMemory, sessionReady]);
  useEffect(() => {
    if (!sessionReady || !status?.chat_jobs || chatRunning.current) return;
    const identity = savedChat(request, actor);
    setHasPendingChat(Boolean(identity));
    if (!identity) return;
    const controller = new AbortController();
    observation.current = controller;
    chatRunning.current = true; interacted.current = true;
    setChatBusy(true);
    const turnId = ++nextTurn.current;
    let inserted = false;
    void observeChatJob(request, identity, actor, streaming, job => {
      if (controller.signal.aborted) return;
      setChatStageText(chatStage(job.stage));
      if (!inserted) {
        inserted = true;
        setTurns(current => [...current, { id: turnId, request_id: identity, question: job.question, model: job.model, files: [], queue_state: "running" }]);
      }
      if (streaming) setTurns(current => current.map(turn => turn.id === turnId ? { ...turn, answer: job.partial || undefined } : turn));
    }, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      setTurns(current => current.map(turn => turn.id === turnId ? { ...turn, queue_state: undefined, answer: result.result.answer, tools: result.result.tools_used, pending_action: result.pending_action, pending_actions: result.pending_actions } : turn));
      ingestConfirmations(actor || "panel", result.pending_actions ?? (result.pending_action ? [result.pending_action] : []));
      setConversationId(result.conversation_id ?? null); selectedConversation.current = result.conversation_id ?? null;
    }).catch(cause => {
      if (controller.signal.aborted) return;
      if (cause instanceof ChatJobError) rememberChat(request, actor, null);
      setMemoryError(cause instanceof Error ? cause.message : "Результат AI не подтверждён");
      setTurns(current => current.map(turn => turn.id === turnId ? { ...turn, queue_state: undefined, error: cause instanceof Error ? cause.message : "Результат AI не подтверждён" } : turn));
    }).finally(() => {
      if (controller.signal.aborted) return;
      chatRunning.current = false; setChatBusy(false); setChatStageText(""); setHasPendingChat(Boolean(savedChat(request, actor)));
    });
    return () => { controller.abort(); chatRunning.current = false; setChatBusy(false); };
  }, [request, actor, status?.chat_jobs, streaming, recoveryKey, ingestConfirmations, sessionReady]);
  useEffect(() => {
    setTurns(current => {
      const next = current.map(turn => {
      const actions = turn.pending_actions ?? (turn.pending_action ? [turn.pending_action] : []);
      const text = actions.map(action => confirmations.results[`${action.actor || actor || "panel"}:${action.code}`]).filter(Boolean).join("\n");
      return text && text !== turn.action_result ? { ...turn, action_result: text } : turn;
      });
      return next.some((turn, index) => turn !== current[index]) ? next : current;
    });
  }, [confirmations.results, actor]);
  useEffect(() => { followTranscript.current = true; }, [actor, conversationId, panel]);
  useEffect(() => {
    if (!followTranscript.current || panel !== "chat" || typeof requestAnimationFrame === "undefined") return;
    const frame = requestAnimationFrame(() => {
      if (followTranscript.current && transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [turns, panel, actor, conversationId]);
  const retryTurn = useCallback((turn: Turn) => { setPrompt(turn.question); setFiles(turn.files); }, []);
  const cancelTurn = useCallback((id: number) => {
    pendingTurns.current = pendingTurns.current.filter(item => item.id !== id);
    setTurns(current => current.filter(item => item.id !== id));
  }, []);
  const downloadTrace = useCallback(async (identity: string) => {
    try {
      const trace = await request(`/assistant/requests/${identity}/trace${actor ? `?telegram_id=${actor}` : ""}`, { cache: "no-store" });
      if (client.current !== request || activeActor.current !== actor) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify(trace, null, 2)], { type: "application/json" }));
      const link = document.createElement("a"); link.href = url; link.download = `nodera-request-${identity}.json`; link.click(); URL.revokeObjectURL(url);
    } catch (cause) { if (client.current === request && activeActor.current === actor) notify.error(cause instanceof Error ? cause.message : "Журнал запроса недоступен"); }
  }, [request, actor, notify]);
  async function attachFiles(incoming: File[]) {
    if (!incoming.length || fileRead.current || writing.current) return;
    if (!status?.chat_attachments) { setError("Для файлов обновите Nodera в Обзоре"); return; }
    interacted.current = true; fileRead.current = true; setReadingFiles(true); setError("");
    try { const added = await readChatFiles(incoming, files); if (client.current === request) setFiles(current => [...current, ...added]); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось добавить файл"); }
    finally { fileRead.current = false; setReadingFiles(false); }
  }
  function patch(update: Partial<Draft>) {
    dirtyRef.current = true;
    if ("provider_url" in update || "api_key" in update) {
      setModel("");
    }
    if ("bot_token" in update) setChecks(current => ({ ...current, "nodera-bot": undefined }));
    setDraft(current => current ? { ...current, ...update } : current);
  }
  function discardSettings() {
    if (!status) return;
    setModel("");
    setDraft(makeDraft(status.settings)); setChecks({}); dirtyRef.current = false;
    if (status.settings.model_mode === "catalog") void loadModels();
  }
  async function save(value = draft, botOnly = false) {
    if (!value || writing.current) return;
    writing.current = true; version.current += 1;
    setWorking("save"); setError(""); setMessage("");
    try {
      const next = await request<McpStatus>("/assistant", { method: "PUT", body: JSON.stringify({ ...settingsPayload(value), component: "nodera" }) });
      if (client.current !== request) return;
      if (status?.settings.active_provider !== next.settings.active_provider) {
        setModel("");
        setCatalog([]);
      }
      const saved = makeDraft(next.settings);
      setStatus(next);
      if (botOnly && draft) {
        const remaining = {
          ...draft, expected_revision: saved.expected_revision, bot_token: saved.bot_token, bot_token_configured: saved.bot_token_configured,
          modules: { ...draft.modules, "nodera-bot": saved.modules["nodera-bot"] },
        };
        dirtyRef.current = changed(remaining, next.settings);
        setDraft(remaining);
      } else {
        dirtyRef.current = false;
        setDraft(saved);
      }
      setChecks({}); setMessage(next.settings_apply?.bot_restarted ? "Настройки сохранены; Telegram-бот перезапущен" : "Настройки сохранены");
      return next;
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Настройки не сохранены"); }
    finally { writing.current = false; setWorking(""); }
  }
  async function check(id: ModuleId) {
    if (!status || writing.current) return;
    const revision = status.settings.revision;
    writing.current = true; setWorking(id); setError("");
    try {
      const checked = await request<{ result?: { username?: string } }>(`/assistant/check/${id}`, { method: "POST", body: JSON.stringify({ telegram_id: actor ? Number(actor) : null, ...(id === "nodera-bot" ? { bot_token: draft?.bot_token } : {}) }) });
      notify.success("Подключение проверено");
      setChecks(current => ({ ...current, [id]: { ok: true, text: "Подключение проверено", username: checked.result?.username, revision, actor } }));
    } catch (cause) {
      notify.error(cause instanceof Error ? cause.message : "Проверка не выполнена");
      setChecks(current => ({ ...current, [id]: { ok: false, text: cause instanceof Error ? cause.message : "Проверка не выполнена", revision, actor } }));
    } finally { writing.current = false; setWorking(""); }
  }
  async function runAction(action: "start" | "stop") {
    if (!status || !draft || writing.current) return;
    const stopping = action === "stop";
    if (!await confirmAction({ title: stopping ? "Остановить бота?" : "Запустить бота?",
      message: stopping ? "Автозапуск Telegram-бота будет отключён." : "Токен будет сохранён. Бот запустится с автозапуском.",
      confirmLabel: stopping ? "Остановить" : "Запустить", danger: stopping })) return;
    setError("");
    try {
      if (!stopping && (draft.bot_token !== null || !status.settings.modules["nodera-bot"])) {
        const saved = makeDraft(status.settings);
        saved.bot_token = draft.bot_token;
        saved.modules["nodera-bot"] = true;
        if (!await save(saved, true)) return;
      }
      const operation = await admitOperation("/assistant/action", { method: "POST", body: JSON.stringify({ action }) });
      if (operation) onOperation(operation);
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Команда не принята"); }
  }
  async function runCommand(command: string, clearPrompt = true) {
    if (commandRunning.current || writing.current || fileRead.current) return;
    commandRunning.current = true; setCommandBusy(true); setCommandMenu(false); setModelMenu(false);
    const id = ++nextTurn.current;
    if (clearPrompt) setPrompt("");
    setTurns(current => [...current, { id, question: command, files: [], model: "", answer: "Выполняем команду…", context_safe: false }]);
    try {
      const result = await request<{ text: string; display_text?: string; model?: string; pending_actions?: PendingAction[]; operation?: SystemAction; context_safe?: boolean }>("/assistant/commands", {
        method: "POST", body: JSON.stringify({ command, telegram_id: actor ? Number(actor) : null, selected_model: selectedModel || null }),
      });
      if (client.current !== request || activeActor.current !== actor) return;
      setTurns(current => current.map(turn => turn.id === id ? { ...turn, answer: result.display_text || result.text, context_safe: result.context_safe === true } : turn));
      if (result.pending_actions) ingestConfirmations(actor || "panel", result.pending_actions);
      if (typeof result.model === "string") {
        const chosen = result.model; setModel(chosen);
        if (chosen) setCatalog(current => current.includes(chosen) ? current : [...current, chosen]);
      }
      if (result.operation && /^\/confirm(?:\s|$)/i.test(command)) onOperation(systemActionSnapshot(result.operation));
    } catch (cause) {
      if (client.current !== request || activeActor.current !== actor) return;
      const text = mutationFailureState(cause) === "unknown" ? "Результат команды не подтверждён. Проверьте ожидающие действия и операции; команда автоматически не повторяется." : cause instanceof Error ? cause.message : "Команда не выполнена";
      setTurns(current => current.map(turn => turn.id === id ? { ...turn, answer: undefined, error: text } : turn));
    } finally { commandRunning.current = false; if (client.current === request) setCommandBusy(false); }
  }
  function chooseCommand(item: ChatCommand) {
    setCommandMenu(false);
    if (item.needs_args) { setPrompt(item.insert); promptInput.current?.focus(); }
    else void runCommand(item.command, prompt.trim().startsWith("/"));
  }
  async function send() {
    if (directCommand(prompt) && files.length) { setError("Команды не принимают вложения. Уберите файлы или отправьте обычный вопрос AI."); return; }
    const command = prompt.trim().match(/^\/(confirm|cancel)\s+([0-9a-f]{8})$/i);
    if (command) {
      const action = confirmations.actions.find(item => (item.actor || "panel") === (actor || "panel") && item.code === command[2].toLowerCase());
      if (!action) { await runCommand(prompt.trim()); return; }
      const question = prompt.trim(), id = ++nextTurn.current;
      setPrompt("");
      setTurns(current => [...current, { id, question, files: [], model: "", answer: "Отправляем решение…" }]);
      const text = await confirmations.decide(action, command[1].toLowerCase() === "confirm" ? "confirm" : "reject");
      if (client.current !== request || activeActor.current !== actor) return;
      setTurns(current => current.map(turn => turn.id === id ? { ...turn, answer: text || "Результат решения не подтверждён. Проверьте уведомление." } : turn));
      return;
    }
    if (directCommand(prompt)) { await runCommand(prompt.trim()); return; }
    const question = /^\/ai(?:\s|$)/i.test(prompt.trim()) ? prompt.trim().replace(/^\/ai\s*/i, "") : prompt.trim();
    if (!question && !files.length) { setError("Напишите вопрос после /ai"); return; }
    if (hasPendingChat && !chatRunning.current || !(prompt.trim() || files.length) || !selectedModel || writing.current || fileRead.current) return;
    if (pendingTurns.current.length >= 5) { setError("В очереди уже 5 сообщений. Дождитесь ответа."); return; }
    const turn: Turn = { id: ++nextTurn.current, question, model: selectedModel, files, queue_state: "queued" };
    pendingTurns.current.push(turn);
    interacted.current = true;
    setPrompt(""); setFiles([]); setModelMenu(false);
    setTurns(current => [...current, turn]);
    if (chatRunning.current) return;
    chatRunning.current = true;
    setChatBusy(true); setError("");
    const connection = apiMonitor.current;
    const context = [...turns];
    let conversation = conversationId;
    try {
      while (pendingTurns.current.length && client.current === request) {
        const next = pendingTurns.current.shift()!;
        const { id, question, files: attached } = next;
        chatRequestId.current = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
        setChatQueue({state: "submitting", position: 0});
        const diagnosticRequestId = chatRequestId.current;
        setTurns(current => current.map(item => item.id === id ? { ...item, request_id: diagnosticRequestId, queue_state: "running" } : item));
        try {
          const identity = chatRequestId.current;
          const decisions = (confirmations.context ?? []).filter(item => item.actor === (actor || "panel") && !context.some(turn => turn.question === item.question && turn.answer === item.answer));
          const payload = { prompt: question, telegram_id: actor ? Number(actor) : null, model: next.model,
            request_id: identity, stream: streaming && status?.chat_streaming === true,
            attachments: attached.map(({ name, mime, data }) => ({ name, mime, data })), history: status?.chat_attachments ? chatHistory([...context, ...decisions], attached) : [], conversation_id: conversation };
          if (!status?.chat_jobs) throw new Error("Обновите панель для защищённого выполнения чата");
          rememberChat(request, actor, identity); setHasPendingChat(true);
          try {
            const accepted = await request<ChatJob>("/assistant/requests", { method: "POST", body: JSON.stringify(payload) });
            if (client.current === request) setChatStageText(chatStage(accepted.stage));
          }
          catch (cause) { if (mutationFailureState(cause) !== "unknown") { rememberChat(request, actor, null); throw cause; } }
          const controller = new AbortController();
          observation.current = controller;
          const result: ChatOutput = await observeChatJob(request, identity, actor, payload.stream, job => {
            if (client.current !== request || controller.signal.aborted) return;
            setChatStageText(chatStage(job.stage));
            if (payload.stream) setTurns(current => current.map(turn => turn.id === id ? { ...turn, answer: job.partial || undefined } : turn));
          }, controller.signal);
          if (client.current === request) {
            if (apiMonitor.current === connection) setChatConnection("responding");
            const completed = { ...next, queue_state: undefined, answer: result.result.answer, tools: result.result.tools_used, pending_action: result.pending_action, pending_actions: result.pending_actions };
            context.push(completed);
            conversation = result.conversation_id ?? null;
            setTurns(current => current.map(turn => turn.id === id ? completed : turn));
            ingestConfirmations(actor || "panel", result.pending_actions ?? (result.pending_action ? [result.pending_action] : []));
            setConversationId(conversation); selectedConversation.current = conversation; setMemoryError(result.cache_error ?? '');
            if (result.provider_usage) setStatus(current => current ? { ...current, provider_usage: result.provider_usage } : current);
            if (cacheEnabled) void loadConversations();
            if (!visible.current || typeof document !== "undefined" && document.hidden) notifications.upsert({
              id: `nodera-answer:${chatRequestId.current}`, source: "nodera", title: "Nodera ответила", message: question || "Ответ на сообщение с вложениями",
              state: "success", kind: "message", action: { label: "Открыть чат", run: () => { setPanel('chat'); openChat.current(); } },
            });
            if (result.pending_actions?.length || result.pending_action) {
              const waiting = new Set(pendingTurns.current.map(item => item.id));
              pendingTurns.current = [];
              setTurns(current => current.map(item => waiting.has(item.id) ? { ...item, queue_state: undefined,
                error: "Не отправлено: предыдущий ответ требует подтверждения действия. Повторите вопрос вручную после его проверки." } : item));
              break;
            }
          }
        } catch (cause) {
          if (apiMonitor.current === connection) { setChatConnection("failed"); void loadModels(); }
          const waiting = new Set(pendingTurns.current.map(item => item.id));
          pendingTurns.current = [];
          setTurns(current => current.map(turn => turn.id === id ? { ...turn, queue_state: undefined, error: cause instanceof Error ? cause.message : "AI не ответил" }
            : waiting.has(turn.id) ? { ...turn, queue_state: undefined, error: "Не отправлено: предыдущий запрос завершился ошибкой. Можно повторить вручную." } : turn));
          break;
        }
      }
    } finally { if (client.current === request) { chatRunning.current = false; setChatBusy(false); setChatQueue(null); setChatStageText(""); setHasPendingChat(Boolean(savedChat(request, actor))); } }
  }
  if (!status || !draft) return <section hidden={!active} className="mcpWorkspace"><p role="status">{loadError || "Загрузка компонента…"}</p><button type="button" className="ghostButton" onClick={() => void load()}>Повторить</button></section>;
  const selectedComponent = status.modules.find(item => item.id === "ai-agent");
  const botComponent = status.modules.find(item => item.id === "nodera-bot");
  const selectedModel = status.settings.model_mode === "catalog" ? (availableModels.includes(model) ? model : availableModels.includes(status.settings.model) ? status.settings.model : "") : status.settings.model;
  const managedComponent = selectedComponent?.installed ? selectedComponent : botComponent;
  if (!managedComponent?.installed) return <section hidden={!active} className="mcpWorkspace"><p>Установите Nodera в разделе «Обзор → Компоненты».</p></section>;
  const readOnly = managedComponent.management?.state !== "managed" || managedComponent.management?.retained;
  const editingBlocked = working === "save" || Boolean(readOnly);
  const settingsBlocked = busy || Boolean(working) || Boolean(loadError) || Boolean(readOnly);
  const blocked = !sessionReady || settingsBlocked || chatBusy || readingFiles || memoryBusy || commandBusy;
  const composerBlocked = !sessionReady || hasPendingChat && !chatBusy || settingsBlocked || readingFiles || memoryBusy || commandBusy;
  const commandBlocked = !sessionReady || commandBusy || readingFiles || Boolean(working) || Boolean(loadError);
  const isDirectCommand = directCommand(prompt);
  const promptBlocked = isDirectCommand ? commandBlocked : composerBlocked;
  const agentReady = selectedComponent?.installed && selectedComponent.enabled && selectedComponent.configured;
  const canSend = Boolean(prompt.trim() || files.length) && (isDirectCommand ? !commandBlocked : !composerBlocked && !dirty && Boolean(selectedModel && agentReady));
  const matchingCommands = commandMatches(commands, prompt);
  const activeCommandIndex = Math.min(commandIndex, Math.max(0, matchingCommands.length - 1));
  const conflict = status.settings.revision !== draft.expected_revision;
  const chooseActor = (value: string) => { const saved = readChatSession(request, value); setCommandMenu(false); setCommandIndex(0); setCommands([]); memoryGeneration.current++; activeActor.current = value; interacted.current = false; setActor(value); setModel(saved?.provider === chatProvider(status.settings) ? saved.model : ""); setTurns(saved?.turns ?? []); nextTurn.current = Math.max(0, ...(saved?.turns ?? []).map(turn => turn.id)); setFiles([]); setPrompt(saved?.prompt ?? ""); setStreaming(saved?.streaming ?? streaming); setConversationId(saved?.conversation_id ?? null); selectedConversation.current = saved ? saved.conversation_id : undefined; setMemoryBusy(false); setMemoryError(''); };
  const actorName = status.settings.accounts.find(item => String(item.telegram_id) === actor)?.name || actor || "Администратор";
  const actorSelect = <label className="mcpActor">Контур<select aria-label="Контур" title={actorName} value={actor} disabled={blocked} onChange={event => chooseActor(event.target.value)}><option value="">Администратор</option>{status.settings.accounts.map(item => <option key={item.telegram_id} value={item.telegram_id}>{item.name || item.telegram_id}</option>)}</select></label>;
  const newChat = () => { memoryGeneration.current++; interacted.current = true; selectedConversation.current = null; setModel(""); setConversationId(null); setTurns([]); setFiles([]); setPrompt(''); setMemoryError(''); };
  async function removeConversation() {
    if (!conversationId || !await confirmAction({ title: 'Удалить диалог?', message: 'Диалог и его вложения будут удалены из кеша на сервере.', confirmLabel: 'Удалить', danger: true })) return;
    try { await request(`/assistant/conversations/${conversationId}${actor ? `?telegram_id=${actor}` : ''}`, { method: 'DELETE' }); newChat(); await loadConversations(); }
    catch (cause) { setMemoryError(cause instanceof Error ? cause.message : 'Не удалось удалить диалог'); }
  }
  async function updateAgent() {
    if (!await confirmAction({ title: 'Обновить Nodera?', message: 'Будут обновлены чат и Telegram-служба. Работающий бот кратковременно перезапустится.', confirmLabel: 'Обновить' })) return;
    try {
      const operation = await admitOperation('/protocol-images/nodera/update', { method: 'POST' });
      if (operation) onOperation(operation);
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Обновление не принято'); }
  }
  function exportConversation() {
    const url = URL.createObjectURL(new Blob([conversationMarkdown(turns)], { type: 'text/markdown;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = 'nodera-chat.md'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const selectPanel = (next: Panel) => {
    setPanel(next);
    setModelMenu(false);
    setChatSettingsOpen(false);
    sectionMenu.current?.removeAttribute("open");
  };
  const sectionPicker = <details ref={sectionMenu} className="noderaSectionMenu" onKeyDown={event => {
    if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); }
  }}>
    <summary role="button" className="mcpIconButton noderaSettingsButton" aria-label="Настройки" title="Настройки"><ChatIcon name="gear"/>{dirty && <i/>}</summary>
    <nav aria-label="Настройки Nodera">
      <button type="button" onClick={() => { sectionMenu.current?.removeAttribute("open"); setChatSettingsOpen(true); }}><ChatIcon name="chat"/>Настройки чата</button>
      {panels.filter(item => item.id !== "chat").map(item => <button key={item.id} type="button" onClick={() => selectPanel(item.id)}><ChatIcon name={item.icon}/>{item.label}</button>)}
    </nav>
  </details>;
  const needsUpdate = status.nodera_available_version && status.nodera_version !== status.nodera_available_version;
  const canUpdate = !status.nodera_management || status.nodera_management.state === "managed" && !status.nodera_management.retained;
  const isChat = panel === "chat";
  const activeProvider = status.settings.providers?.find(item => item.id === status.settings.active_provider);
  const connectionLabel = chatConnection === "failed" ? "Ошибка последнего запроса к AI" : providerStatusText(apiConnection);
  const pendingLabel = (turn: Turn) => turn.queue_state === "queued"
    ? `В очереди · ${turns.filter(item => item.queue_state === "queued" && item.id <= turn.id).length}`
    : chatQueue?.state === "queued" ? `В очереди AI · ${chatQueue.position}` : chatStageText || "Обрабатывает запрос…";
  return <div hidden={!active} className={`mcpWorkspace noderaWorkspace ${active && isChat ? "isChat" : ""}`}>
    {loadError && <p className="mcpNotice error" role="alert">{loadError}</p>}
    {readOnly && <p className="mcpNotice">Для изменения настроек примите компонент под управление в «Обзоре».</p>}
    {needsUpdate && <div className="mcpRuntimeUpdate" role="status"><span>{status.nodera_version ? "Доступно обновление Nodera" : "Доступен переход на единый компонент"}</span><button type="button" className="ghostButton" disabled={blocked || dirty || !canUpdate} onClick={() => void updateAgent()}>Обновить Nodera</button></div>}
    <section aria-label="Чат Nodera" hidden={panel !== "chat"} className={`mcpChat ${dragging ? "isDragging" : ""}`} onDragOver={event => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); setDragging(true); } }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (!composerBlocked) void attachFiles(Array.from(event.dataTransfer.files)); }}>
      <div className="mcpChatHead">
        <span className={`mcpConnectionDot ${chatConnection === "failed" || apiConnection.state === "error" ? "failed" : apiConnection.state === "available" ? "available" : "unknown"}`} role="img" aria-label={connectionLabel} title={apiConnection.error || connectionLabel}/>
        <div className="noderaChatScope"><strong>Nodera</strong><small title={actorName}>{actorName}</small></div>
        {!selectedComponent?.configured && <button type="button" className="ghostButton" onClick={() => selectPanel("settings")}>Подключить AI</button>}
        <div className="mcpChatActions">
          <button type="button" className="mcpIconButton" aria-label="Новый чат" title="Новый чат" disabled={blocked || !turns.length} onClick={newChat}><ChatIcon name="new"/></button>
          <button type="button" className="mcpIconButton" aria-label="Скачать диалог" title="Скачать Markdown" disabled={!turns.length} onClick={exportConversation}><ChatIcon name="download"/></button>
          <button type="button" className="mcpIconButton" aria-label="Удалить диалог" title="Удалить диалог" disabled={blocked || !conversationId} onClick={() => void removeConversation()}><ChatIcon name="close"/></button>
          {isChat && sectionPicker}
        </div>
      </div>
      {memoryError && <p className="mcpNotice error" role="alert">{memoryError}</p>}
      {catalogError && <p className="mcpNotice error" role="alert">{catalogError}</p>}
      {apiConnection.state === "error" && <p className="mcpNotice" role="status">Проверка повторяется автоматически. Сохранённая модель и последний каталог доступны. <button type="button" className="ghostButton" onClick={() => void loadModels()}>Проверить API сейчас</button></p>}
      {dirty && <p className="mcpNotice">Перед запросом сохраните настройки.</p>}
      <div ref={transcript} className="mcpTranscript" role="log" aria-label="Диалог с AI-агентом" aria-live="polite" onScroll={event => {
        const element = event.currentTarget; followTranscript.current = element.scrollHeight - element.scrollTop - element.clientHeight < 96;
      }}>
        {!turns.length && !confirmations.actions.some(item => (item.actor || "panel") === (actor || "panel")) && <div className="mcpChatEmpty"><ProtocolIcon protocol="nodera"/><h2>Чем помочь?</h2>{commands.some(item => ["/status", "/services"].includes(item.command)) && <div>{commands.some(item => item.command === "/status") && <button type="button" disabled={commandBlocked} onClick={() => void runCommand("/status", false)}>Состояние сервера</button>}{commands.some(item => item.command === "/services") && <button type="button" disabled={commandBlocked} onClick={() => void runCommand("/services", false)}>Службы</button>}</div>}</div>}
        {turns.map(turn => <ChatTurnRow key={turn.id} turn={turn} waiting={turn.answer || turn.error ? "" : pendingLabel(turn)} canRetry={Boolean(turn.error) && !blocked} onRetry={retryTurn} onCancel={cancelTurn} onError={notify.error} onTrace={downloadTrace}/>)}
      </div>
      <div className="mcpDecisionDock"><ConfirmationCards actor={actor}/></div>
      <div className="mcpComposer">
        {commandMenu && <ChatCommandMenu items={matchingCommands} loading={commandLoading} error={commandError} activeIndex={activeCommandIndex} onSelect={chooseCommand} onRetry={() => void loadCommands()}/>}
        {files.length > 0 && <div className="mcpFileList">{files.map(file => <span className="mcpFileChip" key={file.id}>{file.mime.startsWith("image/") ? <img src={`data:${file.mime};base64,${file.data}`} alt={file.name}/> : <ChatIcon name="file"/>}<span>{file.name}</span><button type="button" className="mcpIconButton" aria-label={`Удалить файл ${file.name}`} disabled={composerBlocked} onClick={() => setFiles(current => current.filter(item => item.id !== file.id))}><ChatIcon name="close"/></button></span>)}</div>}
        <label className="mcpPrompt">Сообщение<textarea ref={promptInput} value={prompt} maxLength={12000} rows={2} disabled={promptBlocked} aria-controls={commandMenu ? "mcp-command-menu" : undefined} aria-activedescendant={commandMenu && matchingCommands.length ? `mcp-command-${activeCommandIndex}` : undefined} onChange={event => { interacted.current = true; setPrompt(event.target.value); setCommandMenu(/^\/[^\s]*$/.test(event.target.value.trimStart())); setCommandIndex(0); if (typeof requestAnimationFrame !== "undefined" && promptFrame.current === null) promptFrame.current = requestAnimationFrame(() => { promptFrame.current = null; const input = promptInput.current; if (input) { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 160)}px`; } }); }} onPaste={event => { const incoming = Array.from(event.clipboardData.files); if (incoming.length) { event.preventDefault(); void attachFiles(incoming); } }} onKeyDown={event => {
          if (commandMenu && event.key === "Escape") { event.preventDefault(); setCommandMenu(false); return; }
          if (commandMenu && ["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); setCommandIndex(current => Math.max(0, Math.min(matchingCommands.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)))); return; }
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent?.isComposing) {
            event.preventDefault();
            if (commandMenu && matchingCommands[activeCommandIndex] && !commandBlocked) { chooseCommand(matchingCommands[activeCommandIndex]); return; }
            if (canSend) void send();
          }
        }} placeholder="Спросите что-нибудь…" /></label>
        <div className="mcpComposerTools"><input ref={fileInput} type="file" aria-label="Файлы к сообщению" accept={fileAccept} multiple hidden onChange={event => { const incoming = Array.from(event.target.files ?? []); event.target.value = ""; void attachFiles(incoming); }}/><button type="button" className="mcpIconButton" aria-label="Добавить файлы или изображения" title={status.chat_attachments ? "Файлы и изображения · до 2 МБ каждый" : "Обновите Nodera для файлов"} disabled={composerBlocked || !status.chat_attachments} onClick={() => fileInput.current?.click()}><ChatIcon name="plus"/></button>
          <button type="button" className="mcpCommandButton" aria-label="Команды" aria-haspopup="listbox" aria-expanded={commandMenu} aria-controls="mcp-command-menu" disabled={commandBlocked} onClick={() => { setCommandMenu(current => !current); setCommandIndex(0); setModelMenu(false); promptInput.current?.focus(); }}><ChatIcon name="commands"/><span>Команды</span></button>
          <div className="mcpModelControl" onKeyDown={event => { if (event.key === 'Escape') { setModelMenu(false); modelButton.current?.focus(); } }}>
            <button type="button" ref={modelButton} className="mcpModelButton" aria-label="Модель для запроса" aria-haspopup="dialog" aria-expanded={modelMenu} disabled={composerBlocked || dirty || status.settings.model_mode !== 'catalog'} onClick={() => setModelMenu(current => !current)}><span>{selectedModel || (catalogLoading ? 'Загрузка моделей…' : 'Выбрать модель')}</span><ChatIcon name="chevron"/></button>
            {modelMenu && <><button type="button" className="mcpMenuBackdrop" aria-label="Закрыть выбор модели" onClick={() => setModelMenu(false)}/><div ref={modelMenuPanel} className="mcpModelMenu" role="dialog" aria-label="Выбор модели" onKeyDown={event => {
              const options = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="option"]'));
              const index = options.indexOf(document.activeElement as HTMLButtonElement);
              if (options.length && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : event.key === 'ArrowDown' ? (index + 1) % options.length : (index - 1 + options.length) % options.length; options[next].focus();
              }
            }}><header><span>Модели · {availableModels.length}</span><button type="button" className="mcpIconButton" aria-label="Обновить модели" disabled={catalogLoading} onClick={() => void loadModels()}><ChatIcon name="refresh"/></button></header><div role="listbox" aria-label="Доступные модели">{availableModels.map(id => <button type="button" role="option" aria-selected={id === selectedModel} key={id} onClick={() => { setModel(id); setModelMenu(false); modelButton.current?.focus(); }}><span>{id}</span>{id === selectedModel && <ChatIcon name="check"/>}</button>)}{!availableModels.length && <p>{catalogLoading ? 'Загрузка…' : 'Каталог пуст'}</p>}</div></div></>}
          </div>
          <button type="button" className="mcpSendButton" aria-label="Отправить сообщение" title={chatBusy ? "Добавить в очередь" : "Отправить"} disabled={!canSend} onClick={() => void send()}>{readingFiles ? <span className="mcpSpinner"/> : <ChatIcon name="send"/>}</button></div>
      </div>
      {hasPendingChat && !chatBusy && !working && <button type="button" className="ghostButton" onClick={() => setRecoveryKey(value => value + 1)}>Восстановить ответ</button>}
      <p className="mcpChatHint">{chatBusy ? "Можно отправить ещё вопрос — он попадёт в очередь и получит контекст предыдущего ответа." : "Shift+Enter — новая строка · файлы передаются выбранному AI-провайдеру"}</p>
    </section>
    {chatSettingsOpen && <SettingsDialog title="Настройки чата" close={() => setChatSettingsOpen(false)}>
        <label className="mcpToggle"><input type="checkbox" checked={streaming} disabled={chatBusy || !status.chat_streaming} onChange={event => {
          setStreaming(event.target.checked);
        }}/>Потоковый ответ</label>
        <p>{status.chat_streaming ? "Показывать текст по мере получения. При выключении ответ появится целиком." : "Для потоковых ответов обновите компонент Nodera."}</p>
        {status.settings.accounts.length > 0 && actorSelect}
        {cacheEnabled && <label className="mcpConversationPicker">Диалог<select aria-label="Сохранённый диалог" value={conversationId ?? ""} title={conversations.find(item => item.id === conversationId)?.title} disabled={blocked} onChange={event => {
          if (event.target.value) void loadConversation(event.target.value); else newChat();
        }}>
          <option value="">Новый диалог</option>{conversations.map(item => <option key={item.id} value={item.id}>{shortTitle(item.title)}</option>)}
        </select></label>}
    </SettingsDialog>}
    {panel !== "chat" && <>
      <section className={`mcpHero noderaHero ${panel === "users" ? "isTelegram" : ""}`}>
        <div className="noderaHeroContent">
          <div className="mcpIdentity">
            <ProtocolIcon protocol={panel === "users" ? "nodera-bot" : "nodera"}/>
            <div><span className="eyebrow">NODERA</span><h1>{panels.find(item => item.id === panel)?.label}</h1><p className="noderaHeroDescription">{panel === "users" ? "Кто общается с ассистентом и какие действия ему доступны." : "Подключения AI, модель по умолчанию и инструменты ассистента."}</p></div>

          </div>
          <dl className="noderaHeroFacts">
            {panel === "settings" ? <>
              <div><dt>API по умолчанию</dt><dd>{activeProvider?.name ?? "—"}</dd></div><div><dt>Модель по умолчанию</dt><dd title={status.settings.model}>{status.settings.model || "Не выбрана"}</dd></div>
              <div><dt>AI</dt><dd><button type="button" role="switch" aria-label="AI" aria-checked={draft.modules["ai-agent"]} className="noderaAISwitch" title={draft.modules["ai-agent"] ? "Выключить AI" : "Включить AI"} disabled={blocked || !selectedComponent?.installed} onClick={() => patch({ modules: { ...draft.modules, "ai-agent": !draft.modules["ai-agent"] } })}><span className="noderaSwitchTrack"><span/></span><span className="noderaSwitchLabel">{draft.modules["ai-agent"] ? "AI включён" : "AI выключен"}</span></button></dd></div>
              <div><dt>Инструменты</dt><dd>{Number(status.web_tools && status.settings.web_enabled !== false) + Number(status.context_tools && status.settings.personalization_enabled !== false)} / 2</dd></div><div><dt>MCP</dt><dd>{status.mcp_modules.filter(item => item.enabled).length} / {status.mcp_modules.length}</dd></div>
            </> : <>

              <div><dt>Имя бота</dt><dd title={status.bot_username ? `@${status.bot_username}` : undefined}>{status.bot_username ? `@${status.bot_username}` : "Не подключён"}</dd></div>
              <div><dt title={status.bot_username ? `@${status.bot_username}` : undefined}>Telegram</dt><dd><button type="button" role="switch" aria-label="Telegram bot" aria-checked={status.bot.active === true} className="noderaAISwitch" disabled={blocked || !botComponent?.installed || botComponent.management?.state !== "managed" || typeof status.bot.active !== "boolean" || !status.bot.active && (!(draft.bot_token ?? draft.bot_token_configured) || status.legacy_bot.active === true)} title={status.bot.active ? "Остановить Telegram-бота" : "Запустить Telegram-бота"} onClick={() => void runAction(status.bot.active ? "stop" : "start")}><span className="noderaSwitchTrack"><span/></span><span className="noderaSwitchLabel">{status.bot.active ? "Включён" : "Выключен"}</span></button></dd></div>
              <div><dt>Пользователи</dt><dd>{status.settings.accounts.length} / {status.settings.accounts.filter(item => item.bot_access).length} админ</dd></div>
            </>}
          </dl>
        </div>
        <div className="noderaHeroBar">
          <nav className="noderaHeroNav" aria-label="Разделы Nodera">
            {panels.map(item => <button key={item.id} type="button" aria-current={item.id === panel ? "page" : undefined}
              onClick={() => selectPanel(item.id)}><ChatIcon name={item.icon}/>{item.label}</button>)}
          </nav>
          {dirty && <div className="mcpActions">
            <button type="button" className="ghostButton" disabled={settingsBlocked} onClick={discardSettings}>Отменить</button>
            <button type="button" className="primaryButton" disabled={settingsBlocked || conflict} onClick={() => void save()}>Сохранить</button>
          </div>}
        </div>
      </section>
      {conflict && <p className="mcpNotice error">Настройки изменились на сервере. Отмените правки и повторите.</p>}
      {dirty && settingsBlocked && !editingBlocked && <p className="mcpNotice" role="status">Настройки можно редактировать. Сохранение станет доступно после завершения текущей операции или восстановления связи.</p>}
      <NoderaSettings apiConnection={apiConnection} panel={panel} request={request} status={status} draft={draft} patch={patch}
        blocked={editingBlocked} dirty={dirty} refreshKey={refreshKey} actor={actor} actorSelect={actorSelect} checks={checks} check={check}
        working={working}/>
    </>}
  </div>;
}
