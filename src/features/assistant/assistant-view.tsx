/* eslint-disable @next/next/no-img-element */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ProtocolIcon } from "../../shared/components/protocol-icon";
import { createApiClient } from "../../shared/lib/api-request";
import { ChatIcon } from "./chat-icon";
import { AssistantAnswer } from "./assistant-answer";
import { conversationMarkdown } from "./answer-format";
import { chatHistory, fileAccept, readChatFiles, type ChatFile } from "./chat-files";
import type { ConfirmationRequest } from "../../shared/types/control-plane";
import type { SystemAction } from "../../control-panel/system-operation";
import { changed, chatModels, makeDraft, settingsPayload, type Draft, type McpStatus, type ModuleId, type ProviderUsage } from "./assistant-state";
import { NoderaSettings, panels, type Panel } from "./nodera-settings";

type Client = ReturnType<typeof createApiClient>;
type Props = {
  request: Client; refreshKey: number; busy: boolean;
  onDirtyChange: (dirty: boolean) => void;
  confirmAction: (options: Omit<ConfirmationRequest, "resolve">) => Promise<boolean>;
  admitOperation: (path: string, options: RequestInit) => Promise<SystemAction | null | undefined>;
  onOperation: (operation: SystemAction) => void;
};
type Turn = { id: number; question: string; model: string; files: ChatFile[]; files_expired?: boolean; answer?: string; tools?: string[]; error?: string };
type Conversation = { id: string; title: string; updated_at: string; turns: number };
function rememberConversation(actor: string, identity: string | null) {
  try { window.sessionStorage.setItem(`nodera-conversation:${actor}`, identity ?? 'new'); } catch { /* Storage may be unavailable. */ }
}

export function AssistantView({ request, refreshKey, busy, onDirtyChange, confirmAction, admitOperation, onOperation }: Props) {
  const [panel, setPanel] = useState<Panel>('chat');
  const sectionMenu = useRef<HTMLDetailsElement>(null);
  const [status, setStatus] = useState<McpStatus>();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loadError, setLoadError] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
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
  const catalogVersion = useRef(0);
  const [turns, setTurns] = useState<Turn[]>([]);
  const nextTurn = useRef(0);
  const [checks, setChecks] = useState<Partial<Record<ModuleId, { ok: boolean; text: string; revision: string; actor: string }>>>({});
  const version = useRef(0);
  const client = useRef(request);
  const writing = useRef(false);
  const dirtyRef = useRef(false);
  const dirty = changed(draft, status?.settings);
  const catalogRevision = status?.settings.model_mode === "catalog" && status.settings.api_key_configured ? status.settings.revision : "";
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
    if (agentInstalled && cacheEnabled) void loadConversations(true);
    else { setConversations([]); setMemoryBusy(false); }
    return invalidateMemory;
  }, [agentInstalled, cacheEnabled, loadConversations, invalidateMemory]);
  useEffect(() => { transcript.current?.scrollTo({ top: transcript.current.scrollHeight, behavior: "smooth" }); }, [turns]);
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
  async function save() {
    if (!draft || writing.current) return;
    writing.current = true; version.current += 1;
    setWorking("save"); setError(""); setMessage("");
    try {
      const next = await request<McpStatus>("/assistant", { method: "PUT", body: JSON.stringify({ ...settingsPayload(draft), component: "nodera" }) });
      if (client.current !== request) return;
      if (status?.settings.active_provider !== next.settings.active_provider) {
        setModel("");
        setCatalog([]);
      }
      setStatus(next); setDraft(makeDraft(next.settings)); dirtyRef.current = false;
      setChecks({}); setMessage("Настройки сохранены");

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
    try {
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
    interacted.current = true;
    const history = status?.chat_attachments ? chatHistory(turns, attached) : [];
    writing.current = true; setWorking("chat"); setError(""); setPrompt(""); setFiles([]); setModelMenu(false);
    setTurns(current => [...current, { id, question, model: selectedModel, files: attached }]);
    try {
      const result = await request<{ result: { answer: string; tools_used: string[] }; conversation_id?: string; cache_error?: string; provider_usage?: Record<string, ProviderUsage> }>("/assistant/chat", {
        method: "POST", body: JSON.stringify({ prompt: question, telegram_id: actor ? Number(actor) : null, model: selectedModel,
          attachments: attached.map(({ name, mime, data }) => ({ name, mime, data })), history, conversation_id: conversationId }),
      });
      if (client.current === request) {
        setTurns(current => current.map(turn => turn.id === id ? { ...turn, answer: result.result.answer, tools: result.result.tools_used } : turn));
        setConversationId(result.conversation_id ?? null); rememberConversation(actor, result.conversation_id ?? null); setMemoryError(result.cache_error ?? '');
        if (result.provider_usage) setStatus(current => current ? { ...current, provider_usage: result.provider_usage } : current);
        if (cacheEnabled) void loadConversations();
      }
    } catch (cause) { setTurns(current => current.map(turn => turn.id === id ? { ...turn, error: cause instanceof Error ? cause.message : "AI не ответил" } : turn)); }
    finally { writing.current = false; setWorking(""); }
  }
  if (!status || !draft) return <section className="mcpWorkspace"><p role="status">{loadError || "Загрузка компонента…"}</p><button type="button" className="ghostButton" onClick={() => void load()}>Повторить</button></section>;
  const selectedComponent = status.modules.find(item => item.id === "ai-agent");
  const botComponent = status.modules.find(item => item.id === "nodera-bot");
  const availableModels = chatModels(status.settings, catalog);
  const selectedModel = status.settings.model_mode === "catalog" ? (availableModels.includes(model) ? model : "") : status.settings.model;
  const managedComponent = selectedComponent?.installed ? selectedComponent : botComponent;
  if (!managedComponent?.installed) return <section className="mcpWorkspace"><p>Установите Nodera в разделе «Обзор → Компоненты».</p></section>;
  const readOnly = managedComponent.management?.state !== "managed" || managedComponent.management?.retained;
  const blocked = busy || Boolean(working) || readingFiles || memoryBusy || Boolean(loadError) || Boolean(readOnly);
  const conflict = status.settings.revision !== draft.expected_revision;
  const agentReady = selectedComponent?.installed && selectedComponent.enabled && selectedComponent.configured;
  const chooseActor = (value: string) => { memoryGeneration.current++; activeActor.current = value; interacted.current = false; setActor(value); setTurns([]); setFiles([]); setConversationId(null); setMemoryBusy(false); setCopied(undefined); setMemoryError(''); };
  const actorSelect = <label className="mcpActor">Контур<select aria-label="Контур" value={actor} disabled={blocked} onChange={event => chooseActor(event.target.value)}><option value="">Администратор · VPS</option>{status.settings.accounts.map(item => <option key={item.telegram_id} value={item.telegram_id}>{item.name || item.telegram_id} · {item.telegram_id}</option>)}</select></label>;
  const newChat = () => { memoryGeneration.current++; interacted.current = true; rememberConversation(actor, null); setConversationId(null); setTurns([]); setFiles([]); setPrompt(''); setCopied(undefined); setMemoryError(''); };
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
    sectionMenu.current?.removeAttribute("open");
  };
  const sectionPicker = <details ref={sectionMenu} className="noderaSectionMenu" onKeyDown={event => {
    if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); }
  }}>
    <summary className="mcpIconButton" aria-label="Разделы Nodera" title="Разделы Nodera"><ChatIcon name="gear"/>{dirty && <i/>}</summary>
    <nav aria-label="Разделы Nodera">{panels.filter(item => item.id !== panel).map(item =>
      <button key={item.id} type="button" onClick={() => selectPanel(item.id)}><ChatIcon name={item.icon}/>{item.label}</button>)}</nav>
  </details>;
  const needsUpdate = status.nodera_available_version && status.nodera_version !== status.nodera_available_version;
  const canUpdate = !status.nodera_management || status.nodera_management.state === "managed" && !status.nodera_management.retained;
  return <div className="mcpWorkspace noderaWorkspace">
    {loadError && <p className="mcpNotice error" role="alert">{loadError}</p>}
    {readOnly && <p className="mcpNotice">Для изменения настроек примите компонент под управление в «Обзоре».</p>}
    {error && <p className="mcpNotice error" role="alert">{error}</p>}
    {message && <span className="sr-only" role="status">{message}</span>}
    {needsUpdate && <div className="mcpRuntimeUpdate" role="status"><span>{status.nodera_version ? "Доступно обновление Nodera" : "Доступен переход на единый компонент"}</span><button type="button" className="ghostButton" disabled={blocked || dirty || !canUpdate} onClick={() => void updateAgent()}>Обновить Nodera</button></div>}
    <section aria-label="Чат Nodera" hidden={panel !== "chat"} className={`mcpChat ${dragging ? "isDragging" : ""}`} onDragOver={event => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); setDragging(true); } }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (!blocked) void attachFiles(Array.from(event.dataTransfer.files)); }}>
      <div className="mcpChatHead">{status.settings.accounts.length > 0 && actorSelect}{cacheEnabled && <label className="mcpConversationPicker">Диалог<select aria-label="Сохранённый диалог" value={conversationId ?? ''} disabled={blocked} onChange={event => { if (event.target.value) void loadConversation(event.target.value); else newChat(); }}><option value="">Новый диалог</option>{conversations.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>}<div className="mcpChatActions"><button type="button" className="mcpIconButton" aria-label="Новый чат" title="Новый чат" disabled={blocked || !turns.length} onClick={newChat}><ChatIcon name="new"/></button><button type="button" className="mcpIconButton" aria-label="Скачать диалог" title="Скачать Markdown" disabled={!turns.length} onClick={exportConversation}><ChatIcon name="download"/></button><button type="button" className="mcpIconButton" aria-label="Удалить диалог" title="Удалить диалог" disabled={blocked || !conversationId} onClick={() => void removeConversation()}><ChatIcon name="close"/></button></div>{!selectedComponent?.configured && <button type="button" className="ghostButton" onClick={() => selectPanel("provider")}>Подключить AI</button>}{panel === "chat" && sectionPicker}</div>
      {memoryError && <p className="mcpNotice error" role="alert">{memoryError}</p>}
      {catalogError && status.settings.model_mode === "catalog" && <p className="mcpNotice error" role="alert">{catalogError}</p>}
      {dirty && <p className="mcpNotice">Перед запросом сохраните настройки.</p>}
      <div ref={transcript} className="mcpTranscript" role="log" aria-label="Диалог с AI-агентом" aria-live="polite">
        {!turns.length && <div className="mcpChatEmpty"><ProtocolIcon protocol="nodera"/><h2>Чем помочь?</h2>{status.settings.mcp["vps-controller-mcp"] && <div><button type="button" disabled={blocked} onClick={() => setPrompt("Покажи состояние сервера")}>Состояние сервера</button><button type="button" disabled={blocked} onClick={() => setPrompt("Покажи состояние служб")}>Службы</button></div>}</div>}
        {turns.map(turn => <div className="mcpTurn" key={turn.id}><div className="mcpUserMessage">{turn.files.length > 0 && <div className="mcpFileList">{turn.files.map(file => <span className="mcpFileChip" key={file.id}>{file.mime.startsWith("image/") ? <img src={`data:${file.mime};base64,${file.data}`} alt={file.name}/> : <ChatIcon name="file"/>}<span>{file.name}</span></span>)}</div>}{turn.question}{turn.files_expired && <small className="mcpExpiredFiles">Вложения удалены из кеша по лимиту</small>}</div><article className={`mcpAnswer ${turn.error ? "error" : ""}`}><small>{turn.model || "AI-агент"}</small><AssistantAnswer text={turn.error || turn.answer || "Обрабатывает запрос…"}/>{turn.tools?.length ? <p className="mcpToolList">{turn.tools.join(" · ")}</p> : null}{turn.answer && <button type="button" className="mcpIconButton" aria-label="Скопировать ответ" title={copied === turn.id ? "Скопировано" : "Скопировать ответ"} onClick={() => { void navigator.clipboard.writeText(turn.answer!).then(() => setCopied(turn.id)).catch(() => setError("Не удалось скопировать ответ")); }}><ChatIcon name={copied === turn.id ? "check" : "copy"}/></button>}{turn.error && <button type="button" className="ghostButton" disabled={blocked} onClick={() => { setPrompt(turn.question); setFiles(turn.files); }}>Повторить</button>}</article></div>)}
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
            }}><header><span>Модели · {availableModels.length}</span><button type="button" className="mcpIconButton" aria-label="Обновить модели" disabled={catalogLoading} onClick={() => void loadModels(savedProviderUrl, null)}><ChatIcon name="refresh"/></button></header><div role="listbox" aria-label="Доступные модели">{availableModels.map(id => <button type="button" role="option" aria-selected={id === selectedModel} key={id} onClick={() => { setModel(id); setModelMenu(false); modelButton.current?.focus(); }}><span>{id}</span>{id === selectedModel && <ChatIcon name="check"/>}</button>)}{!availableModels.length && <p>{catalogLoading ? 'Загрузка…' : 'Каталог пуст'}</p>}</div></div></>}
          </div>
          <button type="button" className="mcpSendButton" aria-label="Отправить сообщение" title="Отправить" disabled={blocked || dirty || !(prompt.trim() || files.length) || !selectedModel || !agentReady} onClick={() => void send()}>{working === "chat" || readingFiles ? <span className="mcpSpinner"/> : <ChatIcon name="send"/>}</button></div>
      </div>
      <p className="mcpChatHint">Shift+Enter — новая строка · файлы передаются выбранному AI-провайдеру</p>
    </section>
    {panel !== "chat" && <>
      <section className={`mcpHero noderaHero ${panel === "telegram" ? "isTelegram" : ""}`}>
        <div className="mcpIdentity"><ProtocolIcon protocol={panel === "telegram" ? "nodera-bot" : "nodera"}/><h1>{panels.find(item => item.id === panel)?.label}</h1></div>
        <div className="mcpActions">
          <label className="mcpToggle"><input type="checkbox" checked={draft.modules["ai-agent"]} disabled={blocked || !agentInstalled} onChange={event => patch({ modules: { ...draft.modules, "ai-agent": event.target.checked } })}/>AI</label>
          {dirty && <>
            <button type="button" className="ghostButton" disabled={blocked} onClick={discardSettings}>Отменить</button>
            <button type="button" className="primaryButton" disabled={blocked || conflict} onClick={() => void save()}>Сохранить</button>
          </>}
          {sectionPicker}
        </div>
      </section>
      {conflict && <p className="mcpNotice error">Настройки изменились на сервере. Отмените правки и повторите.</p>}
      <NoderaSettings panel={panel} request={request} status={status} draft={draft} patch={patch}
        blocked={blocked} dirty={dirty} actor={actor} actorSelect={actorSelect} checks={checks} check={check}
        runAction={runAction} working={working}/>
    </>}
  </div>;
}
