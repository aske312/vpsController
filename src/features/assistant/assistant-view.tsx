/* eslint-disable @next/next/no-img-element */
"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ProtocolIcon } from "../../shared/components/protocol-icon";
import { createApiClient } from "../../shared/lib/api-request";
import { ChatIcon } from "./chat-icon";
import { AssistantAnswer } from "./assistant-answer";
import { conversationMarkdown } from "./answer-format";
import { chatHistory, fileAccept, readChatFiles, type ChatFile } from "./chat-files";
import type { ConfirmationRequest } from "../../shared/types/control-plane";
import type { SystemAction } from "../../control-panel/system-operation";
import { changed, chatModels, makeDraft, moduleState, settingsPayload, type Draft, type McpStatus, type ModuleId, type McpId } from "./assistant-state";

type Client = ReturnType<typeof createApiClient>;
type Props = {
  mode: "agent" | "bot";
  request: Client; refreshKey: number; busy: boolean;
  onDirtyChange: (dirty: boolean) => void;
  confirmAction: (options: Omit<ConfirmationRequest, "resolve">) => Promise<boolean>;
  admitOperation: (path: string, options: RequestInit) => Promise<SystemAction | null | undefined>;
};
const descriptions: Record<ModuleId, string> = {
  "telegram-mcp": "Диалоги, история и поиск в Telegram.",
  "vps-controller-mcp": "Метрики VPS, службы и компоненты.",
  "nodera-bot": "Telegram-интерфейс агента. Принимает сообщения разрешённых пользователей в личном чате.",
  "ai-agent": "Запросы к AI-провайдеру и вызовы разрешённых MCP-инструментов.",
};
const panels = [
  { id: 'chat', label: 'Чат', icon: 'chat' }, { id: 'provider', label: 'Подключение', icon: 'settings' },
  { id: 'modules', label: 'MCP', icon: 'module' }, { id: 'access', label: 'Доступ', icon: 'users' },
  { id: 'behavior', label: 'Поведение', icon: 'person' },
] as const;
type Turn = { id: number; question: string; model: string; files: ChatFile[]; files_expired?: boolean; answer?: string; tools?: string[]; error?: string };
type Conversation = { id: string; title: string; updated_at: string; turns: number };
function rememberConversation(actor: string, identity: string | null) {
  try { window.sessionStorage.setItem(`nodera-conversation:${actor}`, identity ?? 'new'); } catch { /* Storage may be unavailable. */ }
}

export function AssistantView({ mode, request, refreshKey, busy, onDirtyChange, confirmAction, admitOperation }: Props) {
  const agentView = mode === "agent";
  const [panel, setPanel] = useState<typeof panels[number]['id']>('chat');
  const tabsId = useId();
  const [status, setStatus] = useState<McpStatus>();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loadError, setLoadError] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [working, setWorking] = useState("");
  const [actor, setActor] = useState("");
  const [username, setUsername] = useState("");
  const [foundUser, setFoundUser] = useState<{ telegram_id: number; username: string; name: string } | null>(null);
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
  const catalogVersion = useRef(0);
  const [turns, setTurns] = useState<Turn[]>([]);
  const nextTurn = useRef(0);
  const [checks, setChecks] = useState<Partial<Record<ModuleId, { ok: boolean; text: string; revision: string; actor: string }>>>({});
  const version = useRef(0);
  const client = useRef(request);
  const writing = useRef(false);
  const dirtyRef = useRef(false);
  const dirty = changed(draft, status?.settings);
  const catalogRevision = agentView && status?.settings.model_mode === "catalog" && status.settings.api_key_configured ? status.settings.revision : "";
  const savedProviderUrl = status?.settings.provider_url ?? "";
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
  const loadModels = useCallback(async (provider_url: string, api_key: string | null) => {
    const generation = ++catalogVersion.current;
    setCatalogLoading(true); setCatalogError("");
    try {
      const result = await request<{ models: string[] }>("/assistant/models", { method: "POST", body: JSON.stringify({ provider_url, api_key }) });
      if (generation !== catalogVersion.current || client.current !== request) return;
      setCatalog(result.models);
    } catch (cause) {
      if (generation === catalogVersion.current && client.current === request) {
        setCatalog([]);
        setCatalogError(cause instanceof Error ? cause.message : "Каталог моделей недоступен");
      }
    } finally { if (generation === catalogVersion.current) setCatalogLoading(false); }
  }, [request]);
  useEffect(() => {
    if (catalogRevision && savedProviderUrl && !dirtyRef.current) void loadModels(savedProviderUrl, null);
  }, [catalogRevision, savedProviderUrl, loadModels]);
  useEffect(() => () => { catalogVersion.current += 1; }, []);
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
    if (agentView && agentInstalled && cacheEnabled) void loadConversations(true);
    else { setConversations([]); setMemoryBusy(false); }
    return invalidateMemory;
  }, [agentView, agentInstalled, cacheEnabled, loadConversations, invalidateMemory]);
  useEffect(() => { transcript.current?.scrollTo({ top: transcript.current.scrollHeight, behavior: "smooth" }); }, [turns]);
  async function attachFiles(incoming: File[]) {
    if (!incoming.length || fileRead.current || writing.current) return;
    if (!status?.chat_attachments) { setError("Для файлов обновите компонент AI-agent в Обзоре"); return; }
    interacted.current = true; fileRead.current = true; setReadingFiles(true); setError("");
    try { const added = await readChatFiles(incoming, files); if (client.current === request) setFiles(current => [...current, ...added]); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось добавить файл"); }
    finally { fileRead.current = false; setReadingFiles(false); }
  }
  function patch(update: Partial<Draft>) {
    dirtyRef.current = true;
    if ("provider_url" in update || "api_key" in update) {
      catalogVersion.current += 1; setCatalog([]); setCatalogLoading(false); setCatalogError(""); setModel("");
    }
    if ("bot_token" in update) setChecks(current => ({ ...current, "nodera-bot": undefined }));
    setDraft(current => current ? { ...current, ...update } : current);
  }
  function discardSettings() {
    if (!status) return;
    catalogVersion.current += 1; setCatalog([]); setCatalogLoading(false); setCatalogError("");
    setDraft(makeDraft(status.settings)); setChecks({}); dirtyRef.current = false;
    if (status.settings.model_mode === "catalog") void loadModels(status.settings.provider_url, null);
  }
  async function save(startBot = false) {
    if (!draft || writing.current) return;
    writing.current = true; version.current += 1;
    setWorking("save"); setError(""); setMessage("");
    try {
      const next = await request<McpStatus>("/assistant", { method: "PUT", body: JSON.stringify({ ...settingsPayload(draft), component: agentView ? "ai-agent" : "nodera-bot" }) });
      if (client.current !== request) return;
      setStatus(next); setDraft(makeDraft(next.settings)); dirtyRef.current = false;
      setChecks({}); setMessage("Настройки сохранены");
      if (startBot && next.modules.find(item => item.id === "nodera-bot")?.configured && next.settings.modules["nodera-bot"]) {
        await admitOperation("/assistant/action", { method: "POST", body: JSON.stringify({ action: "start" }) });
        setMessage("Настройки сохранены. Команда запуска принята.");
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Настройки не сохранены"); }
    finally { writing.current = false; setWorking(""); }
  }
  async function check(id: ModuleId) {
    if (!status || writing.current) return;
    const revision = status.settings.revision;
    writing.current = true; setWorking(id); setError("");
    try {
      const checked = await request<{ result?: { username?: string } }>(`/assistant/check/${id}`, { method: "POST", body: JSON.stringify({ telegram_id: actor ? Number(actor) : null, ...(id === "nodera-bot" ? { bot_token: draft?.bot_token } : {}) }) });
      setChecks(current => ({ ...current, [id]: { ok: true, text: id === "nodera-bot" && checked.result?.username ? `Токен подтверждён · @${checked.result.username}` : "Подключение проверено", revision, actor } }));
    } catch (cause) {
      setChecks(current => ({ ...current, [id]: { ok: false, text: cause instanceof Error ? cause.message : "Проверка не выполнена", revision, actor } }));
    } finally { writing.current = false; setWorking(""); }
  }
  async function runAction(action: "start" | "stop" | "restart") {
    if (!await confirmAction({ title: "Изменить состояние NoderaBot?",
      message: action === "stop" ? "NoderaBot остановится, его автозапуск будет отключён." : "NoderaBot запустится с сохранёнными настройками. Будет включён автозапуск.",
      confirmLabel: action === "stop" ? "Остановить" : action === "restart" ? "Перезапустить" : "Запустить", danger: action === "stop" })) return;
    setError("");
    try { await admitOperation("/assistant/action", { method: "POST", body: JSON.stringify({ action }) }); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Команда не принята"); }
  }
  async function lookupUser() {
    if (!username.trim() || writing.current) return;
    writing.current = true; setWorking("lookup"); setError(""); setFoundUser(null);
    try { setFoundUser(await request("/assistant/bot-users/lookup", { method: "POST", body: JSON.stringify({ username: username.trim() }) })); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Пользователь не найден"); }
    finally { writing.current = false; setWorking(""); }
  }
  function addFoundUser() {
    if (!foundUser || !draft) return;
    const existing = draft.accounts.find(item => item.telegram_id === foundUser.telegram_id);
    patch({ accounts: existing ? draft.accounts.map(item => item === existing ? { ...item, bot_access: true } : item) : [...draft.accounts, {
      telegram_id: foundUser.telegram_id, name: `${foundUser.name} (@${foundUser.username})`.trim().slice(0, 80), bot_access: true,
      telegram_access: false, vps_access: false, excluded_dialogs: [], excluded_dialogs_text: "", session: null, session_configured: false,
    }] });
    setFoundUser(null); setUsername("");
  }
  async function send() {
    if (!(prompt.trim() || files.length) || !selectedModel || writing.current || fileRead.current) return;
    const id = ++nextTurn.current;
    const question = prompt.trim();
    const attached = files;
    interacted.current = true;
    const history = status?.chat_attachments ? chatHistory(turns, attached) : [];
    writing.current = true; setWorking("chat"); setError(""); setPrompt(""); setFiles([]); setModelMenu(false);
    setTurns(current => [...current, { id, question, model: selectedModel, files: attached }]);
    try {
      const result = await request<{ result: { answer: string; tools_used: string[] }; conversation_id?: string; cache_error?: string }>("/assistant/chat", {
        method: "POST", body: JSON.stringify({ prompt: question, telegram_id: actor ? Number(actor) : null, model: selectedModel,
          attachments: attached.map(({ name, mime, data }) => ({ name, mime, data })), history, conversation_id: conversationId }),
      });
      if (client.current === request) {
        setTurns(current => current.map(turn => turn.id === id ? { ...turn, answer: result.result.answer, tools: result.result.tools_used } : turn));
        setConversationId(result.conversation_id ?? null); rememberConversation(actor, result.conversation_id ?? null); setMemoryError(result.cache_error ?? '');
        if (cacheEnabled) void loadConversations();
      }
    } catch (cause) { setTurns(current => current.map(turn => turn.id === id ? { ...turn, error: cause instanceof Error ? cause.message : "AI не ответил" } : turn)); }
    finally { writing.current = false; setWorking(""); }
  }
  if (!status || !draft) return <section className="mcpWorkspace"><p role="status">{loadError || "Загрузка компонента…"}</p><button type="button" className="ghostButton" onClick={() => void load()}>Повторить</button></section>;
  const componentId = agentView ? "ai-agent" : "nodera-bot";
  const availableModels = chatModels(status.settings, catalog);
  const selectedModel = status.settings.model_mode === "catalog" ? (availableModels.includes(model) ? model : "") : status.settings.model;
  const selectedComponent = status.modules.find(item => item.id === componentId);
  if (!selectedComponent?.installed) return <section className="mcpWorkspace"><p>Установите {agentView ? "AI-agent" : "NoderaBot"} в разделе «Обзор → Компоненты».</p></section>;
  const readOnly = selectedComponent.management?.state !== "managed" || selectedComponent.management?.retained;
  const blocked = busy || Boolean(working) || readingFiles || memoryBusy || Boolean(loadError) || Boolean(readOnly);
  const credentialsBlocked = blocked || !agentView && status.bot.active === true;
  const settingsBlocked = blocked;
  const conflict = status.settings.revision !== draft.expected_revision;
  const settingsVisible = !agentView || panel !== 'chat';
  const canStartBot = !agentView && draft.modules["nodera-bot"] && Boolean(draft.bot_token || draft.bot_token === null && draft.bot_token_configured);
  const canCheckConnection = agentView ? !dirty && selectedComponent.enabled && selectedComponent.configured : Boolean(draft.bot_token || draft.bot_token === null && draft.bot_token_configured);
  const changeAccount = (index: number, update: Partial<Draft["accounts"][number]>) => patch({ accounts: draft.accounts.map((item, i) => i === index ? { ...item, ...update } : item) });
  const chooseActor = (value: string) => { memoryGeneration.current++; activeActor.current = value; interacted.current = false; setActor(value); setTurns([]); setFiles([]); setConversationId(null); setMemoryBusy(false); setCopied(undefined); setMemoryError(''); };
  const actorSelect = <label className="mcpActor">Контур<select aria-label="Контур" value={actor} disabled={blocked} onChange={event => chooseActor(event.target.value)}><option value="">Администратор · VPS</option>{status.settings.accounts.map(item => <option key={item.telegram_id} value={item.telegram_id}>{item.name || item.telegram_id} · {item.telegram_id}</option>)}</select></label>;
  const result = checks[componentId];
  const newChat = () => { memoryGeneration.current++; interacted.current = true; rememberConversation(actor, null); setConversationId(null); setTurns([]); setFiles([]); setPrompt(''); setCopied(undefined); setMemoryError(''); };
  async function removeConversation() {
    if (!conversationId || !await confirmAction({ title: 'Удалить диалог?', message: 'Диалог и его вложения будут удалены из кеша на сервере.', confirmLabel: 'Удалить', danger: true })) return;
    try { await request(`/assistant/conversations/${conversationId}${actor ? `?telegram_id=${actor}` : ''}`, { method: 'DELETE' }); newChat(); await loadConversations(); }
    catch (cause) { setMemoryError(cause instanceof Error ? cause.message : 'Не удалось удалить диалог'); }
  }
  async function updateAgent() {
    if (!await confirmAction({ title: 'Обновить AI-agent?', message: 'Штатное обновление среды агента добавит поддержку файлов, памяти и поведения Нодеры. Настройки сохранятся.', confirmLabel: 'Обновить' })) return;
    try { await admitOperation('/protocol-images/ai-agent/update', { method: 'POST' }); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Обновление не принято'); }
  }
  function exportConversation() {
    const url = URL.createObjectURL(new Blob([conversationMarkdown(turns)], { type: 'text/markdown;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = 'nodera-chat.md'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const setupMessage = !agentView && !selectedComponent.configured ? "Для запуска сохраните токен и включите бота. Затем отправьте ему /id и добавьте себя в администраторы." : "";
  return <div className={`mcpWorkspace ${agentView ? "agentWorkspace" : "botWorkspace"}`}>
    <section className="mcpHero" aria-label={agentView ? "Обзор AI-агента" : "Обзор NoderaBot"}>
      <div className="mcpHeroContent">
        <p className="eyebrow">{agentView ? "AI WORKSPACE" : "TELEGRAM BOT"}</p>
        <div className="mcpIdentity"><ProtocolIcon protocol={componentId}/><h1>{agentView ? "AI-агент" : "NoderaBot"}</h1><span className="mcpBadge">{!agentView && !selectedComponent.configured ? "Нужна настройка" : moduleState(status, componentId)}</span></div>
        <p className="mcpHeroLead">{agentView ? "Диалог с AI и подключённые инструменты управления." : "Личный доступ к серверу и AI-агенту через Telegram."}</p>
        <dl className="mcpHeroFacts">
          <div><dt>{agentView ? "МОДЕЛЬ" : "ПОДКЛЮЧЕНИЕ"}</dt><dd>{agentView ? status.settings.model_mode === "catalog" ? "Выбор в чате" : status.settings.model || "Не задана" : status.settings.bot_token_configured ? "Токен сохранён" : "Не настроено"}</dd></div>
          <div><dt>{agentView ? "MCP-МОДУЛИ" : "РЕЖИМ"}</dt><dd>{agentView ? `${status.mcp_modules.filter(item => item.enabled).length} / ${status.mcp_modules.length}` : "Long polling"}</dd></div>
          <div><dt>{agentView ? "ПОЛЬЗОВАТЕЛИ" : "АДМИНИСТРАТОРЫ"}</dt><dd>{status.settings.accounts.filter(item => agentView ? item.telegram_access || item.vps_access : item.bot_access).length}</dd></div>
        </dl>
      </div>
    </section>
    <header className="mcpToolbar">
      {!agentView && <span className="mcpSectionLabel">Настройки бота</span>}
      {agentView ? <nav className="mcpTabs" role="tablist" aria-label="Разделы AI-агента">{panels.map((item, index) => <button key={item.id} id={`${tabsId}-${item.id}-tab`} type="button" role="tab" aria-selected={panel === item.id} aria-controls={`${tabsId}-${item.id}`} tabIndex={panel === item.id ? 0 : -1} onClick={() => { setPanel(item.id); setModelMenu(false); }} onKeyDown={event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault(); const next = panels[event.key === 'Home' ? 0 : event.key === 'End' ? panels.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + panels.length) % panels.length].id;
        setPanel(next); document.getElementById(`${tabsId}-${next}-tab`)?.focus();
      }}><ChatIcon name={item.icon}/><span>{item.label}</span>{item.id === 'modules' && <small>{draft.mcp_installed?.length ?? 0}</small>}{item.id === 'access' && <small>{draft.accounts.length}</small>}{item.id !== 'chat' && dirty && <i aria-hidden="true" title="Есть несохранённые настройки"/>}</button>)}</nav>
        : <div className="mcpActions">{status.bot.active && <><button type="button" className="ghostButton" disabled={blocked} onClick={() => void runAction("restart")}>Перезапустить</button><button type="button" className="ghostButton" disabled={blocked} onClick={() => void runAction("stop")}>Остановить</button></>}{!status.bot.active && selectedComponent.configured && selectedComponent.enabled && <button type="button" className="ghostButton" disabled={blocked || dirty || status.legacy_bot.active === true} onClick={() => void runAction("start")}>Запустить</button>}</div>}
    </header>
    {loadError && <p className="mcpNotice error" role="alert">{loadError}</p>}
    {readOnly && <p className="mcpNotice">Для изменения настроек примите компонент под управление в «Обзоре».</p>}
    {!agentView && status.bot_live_access === false && <p className="mcpNotice">Для гостевых ответов, /id и поиска по @username обновите компонент NoderaBot в «Обзоре» до версии 1.1.0 или новее.</p>}
    {!agentView && status.legacy_bot.active && <p className="mcpNotice">Работает прежний Telegram-бот. Для запуска NoderaBot нужно отдельное переключение.</p>}
    {error && <p className="mcpNotice error" role="alert">{error}</p>}
    {message && <p className="mcpNotice" role="status">{message}</p>}
    {agentView && status.chat_persona === false && <div className="mcpRuntimeUpdate" role="status"><span>AI-agent {status.agent_version || 'старой версии'} · для файлов, памяти и поведения нужно обновить компонент</span><button type="button" className="ghostButton" disabled={blocked || !status.agent_available_version || status.agent_available_version === status.agent_version} onClick={() => void updateAgent()}>Обновить до {status.agent_available_version || 'новой версии'}</button></div>}
    {agentView && <section id={`${tabsId}-chat`} role="tabpanel" aria-labelledby={`${tabsId}-chat-tab`} hidden={panel !== "chat"} className={`mcpChat ${dragging ? "isDragging" : ""}`} onDragOver={event => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); setDragging(true); } }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (!blocked) void attachFiles(Array.from(event.dataTransfer.files)); }}>
      <div className="mcpChatHead">{status.settings.accounts.length > 0 && actorSelect}{cacheEnabled && <label className="mcpConversationPicker">Диалог<select aria-label="Сохранённый диалог" value={conversationId ?? ''} disabled={blocked} onChange={event => { if (event.target.value) void loadConversation(event.target.value); else newChat(); }}><option value="">Новый диалог</option>{conversations.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>}<div className="mcpChatActions"><button type="button" className="mcpIconButton" aria-label="Новый чат" title="Новый чат" disabled={blocked || !turns.length} onClick={newChat}><ChatIcon name="new"/></button><button type="button" className="mcpIconButton" aria-label="Скачать диалог" title="Скачать Markdown" disabled={!turns.length} onClick={exportConversation}><ChatIcon name="download"/></button><button type="button" className="mcpIconButton" aria-label="Удалить диалог" title="Удалить диалог" disabled={blocked || !conversationId} onClick={() => void removeConversation()}><ChatIcon name="close"/></button></div>{!selectedComponent.configured && <button type="button" className="ghostButton" onClick={() => setPanel('provider')}>Подключить AI</button>}</div>
      {memoryError && <p className="mcpNotice error" role="alert">{memoryError}</p>}
      {catalogError && status.settings.model_mode === "catalog" && <p className="mcpNotice error" role="alert">{catalogError}</p>}
      {dirty && <p className="mcpNotice">Перед запросом сохраните изменения во вкладке «Настройки».</p>}
      <div ref={transcript} className="mcpTranscript" role="log" aria-label="Диалог с AI-агентом" aria-live="polite">
        {!turns.length && <div className="mcpChatEmpty"><ProtocolIcon protocol="ai-agent"/><h2>Чем помочь?</h2>{status.settings.mcp["vps-controller-mcp"] && <div><button type="button" disabled={blocked} onClick={() => setPrompt("Покажи состояние сервера")}>Состояние сервера</button><button type="button" disabled={blocked} onClick={() => setPrompt("Покажи состояние служб")}>Службы</button></div>}</div>}
        {turns.map(turn => <div className="mcpTurn" key={turn.id}><div className="mcpUserMessage">{turn.files.length > 0 && <div className="mcpFileList">{turn.files.map(file => <span className="mcpFileChip" key={file.id}>{file.mime.startsWith("image/") ? <img src={`data:${file.mime};base64,${file.data}`} alt={file.name}/> : <ChatIcon name="file"/>}<span>{file.name}</span></span>)}</div>}{turn.question}{turn.files_expired && <small className="mcpExpiredFiles">Вложения удалены из кеша по лимиту</small>}</div><article className={`mcpAnswer ${turn.error ? "error" : ""}`}><small>{turn.model || "AI-агент"}</small><AssistantAnswer text={turn.error || turn.answer || "Обрабатывает запрос…"}/>{turn.tools?.length ? <p className="mcpToolList">{turn.tools.join(" · ")}</p> : null}{turn.answer && <button type="button" className="mcpIconButton" aria-label="Скопировать ответ" title={copied === turn.id ? "Скопировано" : "Скопировать ответ"} onClick={() => { void navigator.clipboard.writeText(turn.answer!).then(() => setCopied(turn.id)).catch(() => setError("Не удалось скопировать ответ")); }}><ChatIcon name={copied === turn.id ? "check" : "copy"}/></button>}{turn.error && <button type="button" className="ghostButton" disabled={blocked} onClick={() => { setPrompt(turn.question); setFiles(turn.files); }}>Повторить</button>}</article></div>)}
      </div>
      <div className="mcpComposer">
        {files.length > 0 && <div className="mcpFileList">{files.map(file => <span className="mcpFileChip" key={file.id}>{file.mime.startsWith("image/") ? <img src={`data:${file.mime};base64,${file.data}`} alt={file.name}/> : <ChatIcon name="file"/>}<span>{file.name}</span><button type="button" className="mcpIconButton" aria-label={`Удалить файл ${file.name}`} disabled={blocked} onClick={() => setFiles(current => current.filter(item => item.id !== file.id))}><ChatIcon name="close"/></button></span>)}</div>}
        <label className="mcpPrompt">Сообщение<textarea value={prompt} maxLength={12000} rows={2} disabled={blocked} onChange={event => { interacted.current = true; setPrompt(event.target.value); event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 160)}px`; }} onPaste={event => { const incoming = Array.from(event.clipboardData.files); if (incoming.length) { event.preventDefault(); void attachFiles(incoming); } }} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent?.isComposing) { event.preventDefault(); if (!blocked && !dirty && (prompt.trim() || files.length) && selectedModel && selectedComponent.enabled && selectedComponent.configured) void send(); } }} placeholder="Спросите что-нибудь…" /></label>
        <div className="mcpComposerTools"><input ref={fileInput} type="file" aria-label="Файлы к сообщению" accept={fileAccept} multiple hidden onChange={event => { const incoming = Array.from(event.target.files ?? []); event.target.value = ""; void attachFiles(incoming); }}/><button type="button" className="mcpIconButton" aria-label="Добавить файлы или изображения" title={status.chat_attachments ? "Файлы и изображения · до 2 МБ каждый" : "Обновите компонент AI-agent для файлов"} disabled={blocked || !status.chat_attachments} onClick={() => fileInput.current?.click()}><ChatIcon name="plus"/></button>
          <div className="mcpModelControl" onKeyDown={event => { if (event.key === 'Escape') { setModelMenu(false); modelButton.current?.focus(); } }}>
            <button type="button" ref={modelButton} className="mcpModelButton" aria-label="Модель для запроса" aria-haspopup="dialog" aria-expanded={modelMenu} disabled={blocked || dirty || status.settings.model_mode !== 'catalog'} onClick={() => setModelMenu(current => !current)}><span>{selectedModel || (catalogLoading ? 'Загрузка моделей…' : 'Выбрать модель')}</span><ChatIcon name="chevron"/></button>
            {modelMenu && <><button type="button" className="mcpMenuBackdrop" aria-label="Закрыть выбор модели" onClick={() => setModelMenu(false)}/><div ref={modelMenuPanel} className="mcpModelMenu" role="dialog" aria-label="Выбор модели" onKeyDown={event => {
              const options = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="option"]'));
              const index = options.indexOf(document.activeElement as HTMLButtonElement);
              if (options.length && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : event.key === 'ArrowDown' ? (index + 1) % options.length : (index - 1 + options.length) % options.length; options[next].focus();
              }
            }}><header><span>Модели · {availableModels.length}</span><button type="button" className="mcpIconButton" aria-label="Обновить модели" disabled={catalogLoading} onClick={() => void loadModels(savedProviderUrl, null)}><ChatIcon name="refresh"/></button></header><div role="listbox" aria-label="Доступные модели">{availableModels.map(id => <button type="button" role="option" aria-selected={id === selectedModel} key={id} onClick={() => { setModel(id); setModelMenu(false); modelButton.current?.focus(); }}><span>{id}</span>{id === selectedModel && <ChatIcon name="check"/>}</button>)}{!availableModels.length && <p>{catalogLoading ? 'Загрузка…' : 'Каталог пуст'}</p>}</div></div></>}
          </div>
          <button type="button" className="mcpSendButton" aria-label="Отправить сообщение" title="Отправить" disabled={blocked || dirty || !(prompt.trim() || files.length) || !selectedModel || !selectedComponent.enabled || !selectedComponent.configured} onClick={() => void send()}>{working === "chat" || readingFiles ? <span className="mcpSpinner"/> : <ChatIcon name="send"/>}</button></div>
      </div>
      <p className="mcpChatHint">Shift+Enter — новая строка · файлы передаются выбранному AI-провайдеру</p>
    </section>}
    <section className="mcpConfigPage" hidden={!settingsVisible}>
      <form className="mcpSettings" onSubmit={event => { event.preventDefault(); void save(canStartBot && !status.bot.active); }}>
        <header className="mcpSettingsHead">{agentView && <h2>{panels.find(item => item.id === panel)?.label}</h2>}<label className="mcpToggle"><input type="checkbox" checked={draft.modules[componentId]} disabled={credentialsBlocked} onChange={event => patch({ modules: { ...draft.modules, [componentId]: event.target.checked } })}/>{agentView ? "Агент включён" : "Бот включён"}</label><div className="mcpActions"><button type="button" className="ghostButton" disabled={blocked || !dirty} onClick={discardSettings}>Отменить</button><button type="submit" className="primaryButton" disabled={settingsBlocked || !dirty || conflict}>{canStartBot && !status.bot.active ? "Сохранить и запустить" : "Сохранить"}</button></div></header>
        {!agentView && status.bot.active && <p className="mcpNotice">Администраторов можно менять без остановки. Для смены токена или включения сначала остановите бота.</p>}
        {conflict && <p className="mcpNotice error">Настройки изменились на сервере. Отмените правки для загрузки актуальной версии.</p>}
        {setupMessage && <p className="mcpNotice">{setupMessage}</p>}
        {!agentView && <p className="mcpFieldHint">Гости получают приветствие, справку и свой /id. AI, MCP и сведения о сервере доступны только администраторам бота; доступ к веб-панели этим не выдаётся.</p>}
        <div id={`${tabsId}-provider`} role={agentView ? 'tabpanel' : undefined} aria-labelledby={agentView ? `${tabsId}-provider-tab` : undefined} hidden={agentView && panel !== 'provider'} className="mcpProviderSection"><fieldset disabled={credentialsBlocked} className="mcpConnection"><legend>{agentView ? "API провайдера" : "Подключение бота"}</legend>
          {agentView && <label className="mcpModelMode">Модели<select value={draft.model_mode ?? "single"} onChange={event => patch({ model_mode: event.target.value as "single" | "catalog" })}><option value="single">Одна модель</option><option value="catalog">Авто · каталог API</option></select></label>}
          <div className={`mcpFields ${agentView ? draft.model_mode === "catalog" ? "catalogProviderFields" : "providerFields" : "botFields"}`}>{agentView ? <><label>Base URL<input type="url" value={draft.provider_url} placeholder="https://api.nyxos.shop/v1" onChange={event => patch({ provider_url: event.target.value })}/></label>{draft.model_mode !== "catalog" && <label>Модель<input value={draft.model} maxLength={128} onChange={event => patch({ model: event.target.value })}/></label>}<Secret label="API-ключ" configured={draft.api_key_configured} value={draft.api_key} onChange={api_key => patch({ api_key })}/></> : <Secret label="Токен бота" configured={draft.bot_token_configured} value={draft.bot_token} onChange={bot_token => patch({ bot_token })}/>}</div>
          {agentView && draft.model_mode === "catalog" && <div className="mcpCatalog"><button type="button" className="ghostButton" disabled={catalogLoading || !draft.provider_url || !(draft.api_key || draft.api_key === null && draft.api_key_configured)} onClick={() => void loadModels(draft.provider_url, draft.api_key)}>{catalogLoading ? "Загрузка…" : "Загрузить модели"}</button>{catalog.length > 0 && <span>{catalog.length} моделей · выбор в чате</span>}{catalogError && <p className="mcpNotice error" role="alert">{catalogError}</p>}</div>}
        </fieldset>
        {!(agentView && draft.model_mode === "catalog") && <div className="mcpConnectionStatus"><div className="mcpConnectionFoot">{!agentView && <small>Токен можно проверить до сохранения.</small>}<button type="button" className="ghostButton" disabled={blocked || !canCheckConnection} onClick={() => void check(componentId)}>{working === componentId ? "Проверяем…" : "Проверить подключение"}</button></div>
          {result && result.revision === status.settings.revision && result.actor === actor && <p className={result.ok ? "mcpCheck" : "mcpCheck error"} role="status">{result.text}</p>}
        </div>}</div>
        {agentView && <fieldset id={`${tabsId}-modules`} role="tabpanel" aria-labelledby={`${tabsId}-modules-tab`} hidden={panel !== 'modules'} disabled={settingsBlocked} className="mcpSkills"><legend>Инструменты агента</legend>{[true, false].map(added => {
          const modules = status.mcp_modules.filter(skill => draft.mcp_installed?.includes(skill.id) === added);
          return modules.length > 0 && <section className="mcpModuleGroup" key={String(added)}><h3>{added ? 'Подключённые' : 'Каталог'} <small>{modules.length}</small></h3><div className="mcpModuleGrid">{modules.map(skill => {
          const checked = checks[skill.id];
          const installed = draft.mcp_installed?.includes(skill.id) === true;
          return <article className={`mcpModule ${installed ? "isInstalled" : ""}`} key={skill.id}><header><div className="mcpModuleIdentity"><span className="mcpModuleIcon"><ChatIcon name="module"/></span><strong>{skill.name}</strong></div>{installed && <button type="button" className="mcpIconButton" aria-label={`Удалить модуль ${skill.name}`} title="Удалить модуль" onClick={() => patch({ mcp_installed: draft.mcp_installed?.filter(id => id !== skill.id), mcp: { ...draft.mcp, [skill.id]: false } })}><ChatIcon name="close"/></button>}</header><p>{skill.description ?? descriptions[skill.id]}</p>
            <footer><span>{status.tools[skill.id].length} инстр.</span>{installed ? <><label className="mcpToggle"><input type="checkbox" aria-label={`Включить ${skill.name}`} checked={draft.mcp[skill.id as McpId]} onChange={event => patch({ mcp: { ...draft.mcp, [skill.id]: event.target.checked } })}/>Вкл.</label><button type="button" className="mcpIconButton" aria-label={`Проверить ${skill.name}`} title="Проверить подключение" disabled={blocked || dirty || !skill.enabled || !skill.configured || skill.id === "telegram-mcp" && !actor} onClick={() => void check(skill.id)}><ChatIcon name={working === skill.id ? 'refresh' : 'check'}/></button></> : <button type="button" className="ghostButton" onClick={() => patch({ mcp_installed: [...(draft.mcp_installed ?? []), skill.id] })}><ChatIcon name="plus"/>Добавить</button>}</footer>
            {checked && checked.revision === status.settings.revision && checked.actor === actor && <p className={checked.ok ? "mcpCheck" : "mcpCheck error"} role="status">{checked.text}</p>}
          </article>;
        })}</div></section>; })}{draft.mcp_installed?.includes("telegram-mcp") && <div className="mcpScope mcpModuleCredentials"><label>Telegram API ID<input type="number" min={1} value={draft.telegram_api_id ?? ""} onChange={event => patch({ telegram_api_id: event.target.value ? Number(event.target.value) : null })}/></label><Secret label="Telegram API hash" configured={draft.telegram_api_hash_configured} value={draft.telegram_api_hash} onChange={telegram_api_hash => patch({ telegram_api_hash })}/>{actorSelect}</div>}</fieldset>}
        <fieldset id={`${tabsId}-access`} role={agentView ? 'tabpanel' : undefined} aria-labelledby={agentView ? `${tabsId}-access-tab` : undefined} hidden={agentView && panel !== 'access'} disabled={settingsBlocked} className="mcpAccounts"><legend>{agentView ? "Пользователи и права MCP" : "Администраторы бота"}</legend>
          {agentView && draft.modules["nodera-bot"] && draft.accounts.some(item => item.bot_access) && <p className="mcpFieldHint">Администраторы бота имеют доступ к VPS.</p>}
          {!agentView && <div className="mcpUserLookup"><label>Добавить по @username<input value={username} maxLength={65} placeholder="@aske312" onChange={event => { setUsername(event.target.value); setFoundUser(null); }} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); void lookupUser(); } }} /></label><button type="button" className="ghostButton" disabled={!username.trim()} onClick={() => void lookupUser()}>{working === "lookup" ? "Ищем…" : "Найти пользователя"}</button><small>Пользователь должен сначала написать этому боту /start или /id. Тег проверяется в Telegram, доступ сохраняется за числовым ID.</small>
            {foundUser && <div className="mcpFoundUser"><span><strong>@{foundUser.username}</strong> · {foundUser.name}<small>Telegram ID: {foundUser.telegram_id}</small></span><button type="button" className="ghostButton" disabled={draft.accounts.some(item => item.telegram_id === foundUser.telegram_id && item.bot_access) || draft.accounts.length >= 30 && !draft.accounts.some(item => item.telegram_id === foundUser.telegram_id)} onClick={addFoundUser}>Добавить администратора</button></div>}
          </div>}
          {!draft.accounts.length && <p className="mcpFieldHint">Добавьте Telegram ID пользователя.</p>}
          {draft.accounts.map((item, index) => <article className="mcpAccount" key={index}><div className="mcpAccountRow"><label>Telegram ID<input type="number" min={1} required={!agentView || panel === "access"} value={item.telegram_id || ""} onChange={event => changeAccount(index, { telegram_id: Number(event.target.value) })}/></label><label>Имя<input value={item.name} maxLength={80} onChange={event => changeAccount(index, { name: event.target.value })}/></label><div className="mcpAccountRights">{agentView ? <><label><input type="checkbox" checked={item.telegram_access} onChange={event => changeAccount(index, { telegram_access: event.target.checked })}/>Telegram</label><label><input type="checkbox" checked={item.vps_access || draft.modules["nodera-bot"] && item.bot_access} disabled={draft.modules["nodera-bot"] && item.bot_access} onChange={event => changeAccount(index, { vps_access: event.target.checked })}/>VPS</label></> : <label><input type="checkbox" checked={item.bot_access} onChange={event => changeAccount(index, { bot_access: event.target.checked })}/>Администратор</label>}</div><button type="button" className="ghostButton mcpRemove" aria-label={`Удалить пользователя ${item.name || item.telegram_id || index + 1}`} onClick={() => patch({ accounts: draft.accounts.filter((_, i) => i !== index) })}>Удалить</button></div>
            {agentView && <div className="mcpAccountAdvanced"><div className="mcpFields"><Secret label="Telegram-сессия" configured={item.session_configured} value={item.session} onChange={session => changeAccount(index, { session })}/><label>Исключённые диалоги<input value={item.excluded_dialogs_text} placeholder="ID через запятую" onChange={event => changeAccount(index, { excluded_dialogs_text: event.target.value })}/></label></div></div>}
          </article>)}
          <button type="button" className="ghostButton" disabled={draft.accounts.length >= 30} onClick={() => patch({ accounts: [...draft.accounts, { telegram_id: 0, name: "", bot_access: !agentView, telegram_access: false, vps_access: false, excluded_dialogs: [], excluded_dialogs_text: "", session: null, session_configured: false }] })}>Добавить пользователя</button>
        </fieldset>
        {agentView && <fieldset id={`${tabsId}-behavior`} role="tabpanel" aria-labelledby={`${tabsId}-behavior-tab`} hidden={panel !== 'behavior'} disabled={settingsBlocked} className="mcpBehavior"><legend>Нодера · агент этого сервера</legend><div className="mcpBehaviorFields"><label>Манера общения<select aria-label="Манера общения" value={draft.assistant_style} onChange={event => patch({ assistant_style: event.target.value as Draft['assistant_style'] })}><option value="balanced">Спокойно и по делу</option><option value="concise">Кратко</option><option value="friendly">Дружелюбно</option><option value="technical">Технически подробно</option></select></label><label className="mcpToggle"><input type="checkbox" checked={draft.memory_enabled} onChange={event => patch({ memory_enabled: event.target.checked })}/>Сохранять диалоги на сервере</label><label>Хранить, дней<input type="number" min={1} max={90} value={draft.memory_days} onChange={event => patch({ memory_days: Number(event.target.value) })}/></label></div><label>Дополнительные инструкции<textarea value={draft.assistant_instructions} maxLength={4000} rows={4} placeholder="Предпочтения в ответах, термины и формат отчётов…" onChange={event => patch({ assistant_instructions: event.target.value })}/></label><small>Нодера говорит о себе в женском роде. Манера общения не меняет доступные ей инструменты и права.</small></fieldset>}
      </form>
    </section>
  </div>;
}

function Secret({ label, configured, value, onChange }: { label: string; configured: boolean; value: string | null; onChange: (value: string | null) => void }) {
  const id = useId();
  return <div className="mcpSecretField"><label htmlFor={id}>{label}</label><div className="mcpSecret"><input id={id} type="password" autoComplete="new-password" value={value ?? ""} placeholder={value === "" ? "Будет удалён" : configured && value === null ? "Сохранён" : "Не задан"} onChange={event => onChange(event.target.value || null)} /><button type="button" className="ghostButton" aria-label={`${value === "" ? "Вернуть сохранённый" : "Очистить"} ${label}`} onClick={() => onChange(value === "" ? null : "")}>{value === "" ? "Вернуть" : "Очистить"}</button></div></div>;
}
