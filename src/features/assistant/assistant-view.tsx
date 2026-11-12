/* eslint-disable @next/next/no-img-element */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ProtocolIcon } from "../../shared/components/protocol-icon";
import { createApiClient } from "../../shared/lib/api-request";
import { useNotifier } from "../../shared/notifications/notification-center";
import { mutationFailureState } from "../../shared/lib/api-request";
import { ChatIcon } from "./chat-icon";
import { AssistantAnswer } from "./assistant-answer";
import { conversationMarkdown } from "./answer-format";
import { chatHistory, fileAccept, readChatFiles, type ChatFile } from "./chat-files";
import type { ConfirmationRequest } from "../../shared/types/control-plane";
import { systemActionSnapshot, type SystemAction } from "../../control-panel/system-operation";
import { changed, chatModels, makeDraft, settingsPayload, type Draft, type McpStatus, type ModuleId, type ProviderUsage } from "./assistant-state";
import { ProviderMonitor, initialProviderConnection, providerStatusText, type ProviderConnection } from "./provider-connection";
import { queueProviderCatalog } from "./provider-details";
import { NoderaSettings, SettingsDialog, panels, type Panel } from "./nodera-settings";

type Client = ReturnType<typeof createApiClient>;
type Props = {
  request: Client; refreshKey: number; busy: boolean;
  onDirtyChange: (dirty: boolean) => void;
  confirmAction: (options: Omit<ConfirmationRequest, "resolve">) => Promise<boolean>;
  admitOperation: (path: string, options: RequestInit) => Promise<SystemAction | null | undefined>;
  onOperation: (operation: SystemAction) => void;
};
type PendingAction = { service: string; action: string; code: string; expires: number; operation_id?: string; changes?: Record<string, unknown> };
type Turn = { pending_action?: PendingAction; action_result?: string; id: number; question: string; model: string; files: ChatFile[]; files_expired?: boolean; answer?: string; tools?: string[]; error?: string };
type Conversation = { id: string; title: string; updated_at: string; turns: number };
function shortTitle(title: string) {
  const characters = Array.from(title);
  return characters.length > 20 ? characters.slice(0, 19).join("") + "…" : title;
}
function rememberConversation(actor: string, identity: string | null) {
  try { window.sessionStorage.setItem(`nodera-conversation:${actor}`, identity ?? 'new'); } catch { /* Storage may be unavailable. */ }
}

export function AssistantView({ request, refreshKey, busy, onDirtyChange, confirmAction, admitOperation, onOperation }: Props) {
  const [panel, setPanel] = useState<Panel>('chat');
  const [chatSettingsOpen, setChatSettingsOpen] = useState(false);
  const sectionMenu = useRef<HTMLDetailsElement>(null);
  const [status, setStatus] = useState<McpStatus>();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loadError, setLoadError] = useState("");
  const notify = useNotifier("nodera", "Nodera");
  const setError = (text: string) => { if (text) notify.error(text); };
  const setMessage = (text: string) => { if (text) notify.success(text); };
  const [working, setWorking] = useState("");
  const [actor, setActor] = useState("");
  const [prompt, setPrompt] = useState("");
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
  const [copied, setCopied] = useState<number>();
  const fileInput = useRef<HTMLInputElement>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const modelButton = useRef<HTMLButtonElement>(null);
  const modelMenuPanel = useRef<HTMLDivElement>(null);
  const fileRead = useRef(false);
  const apiMonitor = useRef<ProviderMonitor | null>(null);
  const [apiConnection, setApiConnection] = useState<ProviderConnection>(initialProviderConnection);
  const [chatConnection, setChatConnection] = useState<"unknown" | "responding" | "failed">("unknown");
  const [turns, setTurns] = useState<Turn[]>([]);
  const nextTurn = useRef(0);
  const [checks, setChecks] = useState<Partial<Record<ModuleId, { ok: boolean; text: string; revision: string; actor: string; username?: string }>>>({});
  const version = useRef(0);
  const client = useRef(request);
  const writing = useRef(false);
  const chatRequestId = useRef("");
  const [chatQueue, setChatQueue] = useState<{state: string; position: number; waiting?: number} | null>(null);
  const dirtyRef = useRef(false);
  const dirty = changed(draft, status?.settings);
  const catalogRevision = status?.settings.api_key_configured ? status.settings.revision : "";
  const savedProviderUrl = status?.settings.provider_url ?? "";
  const savedProviderId = status?.settings.active_provider ?? null;
  const cacheEnabled = status?.settings.memory_enabled ?? true;
  const agentInstalled = status?.modules.some(item => item.id === 'ai-agent' && item.installed) === true;
  useEffect(() => { client.current = request; }, [request]);
  useEffect(() => { dirtyRef.current = dirty; onDirtyChange(dirty); }, [dirty, onDirtyChange]);
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
  useEffect(() => {
    if (working !== "chat") return;
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    const observe = async () => {
      try {
        const value = await request<{state: string; position: number; waiting: number}>(`/assistant/queue?request_id=${chatRequestId.current}${actor ? `&telegram_id=${actor}` : ""}`, {cache: "no-store"});
        if (current) setChatQueue(value);
      } catch { /* The chat result remains authoritative; never repeat its POST. */ }
      if (current && typeof window !== "undefined") timer = setTimeout(() => void observe(), 1500);
    };
    void observe();
    return () => { current = false; clearTimeout(timer); };
  }, [working, request, actor]);
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
  const loadConversation = useCallback(async (identity: string) => {
    if (writing.current || fileRead.current) return;
    const generation = ++memoryGeneration.current;
    setMemoryBusy(true); setMemoryError('');
    try {
      const value = await request<{ id: string; turns: Turn[] }>(`/assistant/conversations/${identity}${actor ? `?telegram_id=${actor}` : ''}`);
      if (generation !== memoryGeneration.current || client.current !== request || activeActor.current !== actor) return;
      setConversationId(value.id); setTurns(value.turns); setFiles([]); setPrompt('');
      setModel(value.turns.at(-1)?.model ?? ''); interacted.current = true; rememberConversation(actor, value.id);
      nextTurn.current = Math.max(0, ...value.turns.map(turn => turn.id));
    } catch (cause) { if (generation === memoryGeneration.current) setMemoryError(cause instanceof Error ? cause.message : 'Не удалось загрузить диалог'); }
    finally { if (generation === memoryGeneration.current) setMemoryBusy(false); }
  }, [request, actor]);
  const loadConversations = useCallback(async (restore = false) => {
    try {
      const value = await request<{ enabled: boolean; items: Conversation[] }>(`/assistant/conversations${actor ? `?telegram_id=${actor}` : ''}`);
      if (client.current !== request || activeActor.current !== actor) return;
      setConversations(value.items ?? []);
      if (restore && value.items?.length && !writing.current && !interacted.current) {
        let previous: string | null = null;
        try { previous = window.sessionStorage.getItem(`nodera-conversation:${actor}`); } catch { /* Optional hint only. */ }
        if (previous !== 'new') await loadConversation(value.items.find(item => item.id === previous)?.id ?? value.items[0].id);
      }
    } catch (cause) { if (client.current === request && activeActor.current === actor) setMemoryError(cause instanceof Error ? cause.message : 'Кеш диалогов недоступен'); }
  }, [request, actor, loadConversation]);
  const invalidateMemory = useCallback(() => { memoryGeneration.current++; }, []);
  useEffect(() => {
    if (agentInstalled && cacheEnabled) void loadConversations(true);
    else { setConversations([]); setMemoryBusy(false); }
    return invalidateMemory;
  }, [agentInstalled, cacheEnabled, loadConversations, invalidateMemory]);
  useEffect(() => { transcript.current?.scrollTo({ top: transcript.current.scrollHeight }); }, [turns, panel]);
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
      setChecks({}); setMessage("Настройки сохранены");
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
  async function send() {
    if (!(prompt.trim() || files.length) || !selectedModel || writing.current || fileRead.current) return;
    const id = ++nextTurn.current;
    const question = prompt.trim();
    const attached = files;
    const connection = apiMonitor.current;
    chatRequestId.current = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
    setChatQueue({state: "submitting", position: 0});
    interacted.current = true;
    const history = status?.chat_attachments ? chatHistory(turns, attached) : [];
    writing.current = true; setWorking("chat"); setError(""); setPrompt(""); setFiles([]); setModelMenu(false);
    setTurns(current => [...current, { id, question, model: selectedModel, files: attached }]);
    try {
      const result = await request<{ result: { answer: string; tools_used: string[] }; conversation_id?: string; cache_error?: string; provider_usage?: Record<string, ProviderUsage>; pending_action?: PendingAction }>("/assistant/chat", {
        method: "POST", body: JSON.stringify({ prompt: question, telegram_id: actor ? Number(actor) : null, model: selectedModel,
          request_id: chatRequestId.current,
          attachments: attached.map(({ name, mime, data }) => ({ name, mime, data })), history, conversation_id: conversationId }),
      });
      if (client.current === request) {
        if (apiMonitor.current === connection) setChatConnection("responding");
        setTurns(current => current.map(turn => turn.id === id ? { ...turn, answer: result.result.answer, tools: result.result.tools_used, pending_action: result.pending_action } : turn));
        setConversationId(result.conversation_id ?? null); rememberConversation(actor, result.conversation_id ?? null); setMemoryError(result.cache_error ?? '');
        if (result.provider_usage) setStatus(current => current ? { ...current, provider_usage: result.provider_usage } : current);
        if (cacheEnabled) void loadConversations();
      }
    } catch (cause) { if (apiMonitor.current === connection) { setChatConnection("failed"); void loadModels(); } setTurns(current => current.map(turn => turn.id === id ? { ...turn, error: cause instanceof Error ? cause.message : "AI не ответил" } : turn)); }
    finally { writing.current = false; setWorking(""); }
  }
  async function confirmService(turn: Turn) {
    const action = turn.pending_action;
    if (!action || writing.current || !await confirmAction({ title: `${action.action}: ${action.service}?`,
      message: "Подтвердите изменение. Оно может повлиять на подключения и устройства.", confirmLabel: "Подтвердить", danger: action.action !== "start" })) return;
    writing.current = true; setWorking("service"); setError("");
    try {
      const result = await request<{ text: string; operation?: SystemAction | null }>("/assistant/service-confirm", { method: "POST",
        body: JSON.stringify({ code: action.code, telegram_id: actor ? Number(actor) : null }) });
      if (result.operation) onOperation(systemActionSnapshot(result.operation));
      else notify.info(result.text);
      setTurns(current => current.map(item => item.id === turn.id ? { ...item, pending_action: undefined } : item));
    } catch (cause) {
      if (mutationFailureState(cause) === "unknown" && action.operation_id) {
        onOperation({ id: action.operation_id, state: "unknown", message: "Проверяем результат отправленной команды." });
        setTurns(current => current.map(item => item.id === turn.id ? { ...item, pending_action: undefined } : item));
      } else setError(cause instanceof Error ? cause.message : "Действие не выполнено");
    }
    finally { writing.current = false; setWorking(""); }
  }
  if (!status || !draft) return <section className="mcpWorkspace"><p role="status">{loadError || "Загрузка компонента…"}</p><button type="button" className="ghostButton" onClick={() => void load()}>Повторить</button></section>;
  const selectedComponent = status.modules.find(item => item.id === "ai-agent");
  const botComponent = status.modules.find(item => item.id === "nodera-bot");
  const availableModels = chatModels(status.settings, catalog);
  const selectedModel = status.settings.model_mode === "catalog" ? (availableModels.includes(model) ? model : availableModels.includes(status.settings.model) ? status.settings.model : "") : status.settings.model;
  const managedComponent = selectedComponent?.installed ? selectedComponent : botComponent;
  if (!managedComponent?.installed) return <section className="mcpWorkspace"><p>Установите Nodera в разделе «Обзор → Компоненты».</p></section>;
  const readOnly = managedComponent.management?.state !== "managed" || managedComponent.management?.retained;
  const blocked = busy || Boolean(working) || readingFiles || memoryBusy || Boolean(loadError) || Boolean(readOnly);
  const conflict = status.settings.revision !== draft.expected_revision;
  const agentReady = selectedComponent?.installed && selectedComponent.enabled && selectedComponent.configured;
  const chooseActor = (value: string) => { memoryGeneration.current++; activeActor.current = value; interacted.current = false; setActor(value); setModel(""); setTurns([]); setFiles([]); setConversationId(null); setMemoryBusy(false); setCopied(undefined); setMemoryError(''); };
  const actorName = status.settings.accounts.find(item => String(item.telegram_id) === actor)?.name || actor || "Администратор";
  const actorSelect = <label className="mcpActor">Контур<select aria-label="Контур" title={actorName} value={actor} disabled={blocked} onChange={event => chooseActor(event.target.value)}><option value="">Администратор</option>{status.settings.accounts.map(item => <option key={item.telegram_id} value={item.telegram_id}>{item.name || item.telegram_id}</option>)}</select></label>;
  const newChat = () => { memoryGeneration.current++; interacted.current = true; rememberConversation(actor, null); setModel(""); setConversationId(null); setTurns([]); setFiles([]); setPrompt(''); setCopied(undefined); setMemoryError(''); };
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
  return <div className={`mcpWorkspace noderaWorkspace ${isChat ? "isChat" : ""}`}>
    {loadError && <p className="mcpNotice error" role="alert">{loadError}</p>}
    {readOnly && <p className="mcpNotice">Для изменения настроек примите компонент под управление в «Обзоре».</p>}
    {needsUpdate && <div className="mcpRuntimeUpdate" role="status"><span>{status.nodera_version ? "Доступно обновление Nodera" : "Доступен переход на единый компонент"}</span><button type="button" className="ghostButton" disabled={blocked || dirty || !canUpdate} onClick={() => void updateAgent()}>Обновить Nodera</button></div>}
    <section aria-label="Чат Nodera" hidden={panel !== "chat"} className={`mcpChat ${dragging ? "isDragging" : ""}`} onDragOver={event => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); setDragging(true); } }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (!blocked) void attachFiles(Array.from(event.dataTransfer.files)); }}>
      <div className="mcpChatHead">
        <div className="noderaChatScope"><strong>Nodera</strong><small title={actorName}>{actorName}</small></div>
        <div className="mcpApiStatus" role="status" aria-live="polite">
          <span title={apiConnection.error || (apiConnection.checkedAt ? new Date(apiConnection.checkedAt).toLocaleTimeString("ru-RU") : "")}>{providerStatusText(apiConnection)}</span>
          <small>{chatConnection === "responding" ? "Последний ответ AI получен" : chatConnection === "failed" ? "Ошибка последнего запроса к AI" : "Ответ AI ещё не проверен"}</small>
        </div>
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
      {working === "chat" && <p className="mcpNotice" role="status">{chatQueue?.state === "queued" ? `Запрос в очереди AI · позиция ${chatQueue.position}` : chatQueue?.state === "running" ? "AI выполняет запрос" : "Запрос принят, ожидаем ответ AI…"}</p>}
      <div ref={transcript} className="mcpTranscript" role="log" aria-label="Диалог с AI-агентом" aria-live="polite">
        {!turns.length && <div className="mcpChatEmpty"><ProtocolIcon protocol="nodera"/><h2>Чем помочь?</h2>{status.settings.mcp["vps-controller-mcp"] && <div><button type="button" disabled={blocked} onClick={() => setPrompt("Покажи состояние сервера")}>Состояние сервера</button><button type="button" disabled={blocked} onClick={() => setPrompt("Покажи состояние служб")}>Службы</button></div>}</div>}
        {turns.map(turn => <div className="mcpTurn" key={turn.id}><div className="mcpUserMessage">{turn.files.length > 0 && <div className="mcpFileList">{turn.files.map(file => <span className="mcpFileChip" key={file.id}>{file.mime.startsWith("image/") ? <img src={`data:${file.mime};base64,${file.data}`} alt={file.name}/> : <ChatIcon name="file"/>}<span>{file.name}</span></span>)}</div>}{turn.question}{turn.files_expired && <small className="mcpExpiredFiles">Вложения удалены из кеша по лимиту</small>}</div><article className={`mcpAnswer ${turn.error ? "error" : ""}`}><small>Nodera</small><AssistantAnswer text={turn.error || turn.answer || "Обрабатывает запрос…"}/>{turn.tools?.length ? <p className="mcpToolList">{turn.tools.join(" · ")}</p> : null}{turn.error && <button type="button" className="ghostButton" disabled={blocked} onClick={() => { setPrompt(turn.question); setFiles(turn.files); }}>Повторить</button>}</article>{turn.pending_action && <div className="mcpServiceConfirmation"><span>{turn.pending_action.action} · {turn.pending_action.service}</span>{turn.pending_action.changes && <details><summary>Изменения</summary><pre>{JSON.stringify(turn.pending_action.changes, null, 2)}</pre></details>}<button type="button" className="ghostButton" disabled={blocked} onClick={() => void confirmService(turn)}>Подтвердить действие</button></div>}<div className="mcpAnswerActions">{turn.answer && <button type="button" className="mcpIconButton" aria-label="Скопировать ответ" title={copied === turn.id ? "Скопировано" : "Скопировать ответ"} onClick={() => { void navigator.clipboard.writeText(turn.answer!).then(() => setCopied(turn.id)).catch(() => setError("Не удалось скопировать ответ")); }}><ChatIcon name={copied === turn.id ? "check" : "copy"}/></button>}</div></div>)}
      </div>
      <div className="mcpComposer">
        {files.length > 0 && <div className="mcpFileList">{files.map(file => <span className="mcpFileChip" key={file.id}>{file.mime.startsWith("image/") ? <img src={`data:${file.mime};base64,${file.data}`} alt={file.name}/> : <ChatIcon name="file"/>}<span>{file.name}</span><button type="button" className="mcpIconButton" aria-label={`Удалить файл ${file.name}`} disabled={blocked} onClick={() => setFiles(current => current.filter(item => item.id !== file.id))}><ChatIcon name="close"/></button></span>)}</div>}
        <label className="mcpPrompt">Сообщение<textarea value={prompt} maxLength={12000} rows={2} disabled={blocked} onChange={event => { interacted.current = true; setPrompt(event.target.value); event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 160)}px`; }} onPaste={event => { const incoming = Array.from(event.clipboardData.files); if (incoming.length) { event.preventDefault(); void attachFiles(incoming); } }} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent?.isComposing) { event.preventDefault(); if (!blocked && !dirty && (prompt.trim() || files.length) && selectedModel && agentReady) void send(); } }} placeholder="Спросите что-нибудь…" /></label>
        <div className="mcpComposerTools"><input ref={fileInput} type="file" aria-label="Файлы к сообщению" accept={fileAccept} multiple hidden onChange={event => { const incoming = Array.from(event.target.files ?? []); event.target.value = ""; void attachFiles(incoming); }}/><button type="button" className="mcpIconButton" aria-label="Добавить файлы или изображения" title={status.chat_attachments ? "Файлы и изображения · до 2 МБ каждый" : "Обновите Nodera для файлов"} disabled={blocked || !status.chat_attachments} onClick={() => fileInput.current?.click()}><ChatIcon name="plus"/></button>
          <div className="mcpModelControl" onKeyDown={event => { if (event.key === 'Escape') { setModelMenu(false); modelButton.current?.focus(); } }}>
            <button type="button" ref={modelButton} className="mcpModelButton" aria-label="Модель для запроса" aria-haspopup="dialog" aria-expanded={modelMenu} disabled={blocked || dirty || status.settings.model_mode !== 'catalog'} onClick={() => setModelMenu(current => !current)}><span>{selectedModel || (catalogLoading ? 'Загрузка моделей…' : 'Выбрать модель')}</span><ChatIcon name="chevron"/></button>
            {modelMenu && <><button type="button" className="mcpMenuBackdrop" aria-label="Закрыть выбор модели" onClick={() => setModelMenu(false)}/><div ref={modelMenuPanel} className="mcpModelMenu" role="dialog" aria-label="Выбор модели" onKeyDown={event => {
              const options = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="option"]'));
              const index = options.indexOf(document.activeElement as HTMLButtonElement);
              if (options.length && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : event.key === 'ArrowDown' ? (index + 1) % options.length : (index - 1 + options.length) % options.length; options[next].focus();
              }
            }}><header><span>Модели · {availableModels.length}</span><button type="button" className="mcpIconButton" aria-label="Обновить модели" disabled={catalogLoading} onClick={() => void loadModels()}><ChatIcon name="refresh"/></button></header><div role="listbox" aria-label="Доступные модели">{availableModels.map(id => <button type="button" role="option" aria-selected={id === selectedModel} key={id} onClick={() => { setModel(id); setModelMenu(false); modelButton.current?.focus(); }}><span>{id}</span>{id === selectedModel && <ChatIcon name="check"/>}</button>)}{!availableModels.length && <p>{catalogLoading ? 'Загрузка…' : 'Каталог пуст'}</p>}</div></div></>}
          </div>
          <button type="button" className="mcpSendButton" aria-label="Отправить сообщение" title="Отправить" disabled={blocked || dirty || !(prompt.trim() || files.length) || !selectedModel || !agentReady} onClick={() => void send()}>{working === "chat" || readingFiles ? <span className="mcpSpinner"/> : <ChatIcon name="send"/>}</button></div>
      </div>
      <p className="mcpChatHint">Shift+Enter — новая строка · файлы передаются выбранному AI-провайдеру</p>
    </section>
    {chatSettingsOpen && <SettingsDialog title="Настройки чата" close={() => setChatSettingsOpen(false)}>
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
            <button type="button" className="ghostButton" disabled={blocked} onClick={discardSettings}>Отменить</button>
            <button type="button" className="primaryButton" disabled={blocked || conflict} onClick={() => void save()}>Сохранить</button>
          </div>}
        </div>
      </section>
      {conflict && <p className="mcpNotice error">Настройки изменились на сервере. Отмените правки и повторите.</p>}
      <NoderaSettings apiConnection={apiConnection} panel={panel} request={request} status={status} draft={draft} patch={patch}
        blocked={blocked} dirty={dirty} refreshKey={refreshKey} actor={actor} actorSelect={actorSelect} checks={checks} check={check}
        working={working}/>
    </>}
  </div>;
}
