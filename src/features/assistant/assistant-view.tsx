"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ProtocolIcon } from "../../shared/components/protocol-icon";
import { createApiClient } from "../../shared/lib/api-request";
import type { ConfirmationRequest } from "../../shared/types/control-plane";
import type { SystemAction } from "../../control-panel/system-operation";
import { changed, makeDraft, moduleState, settingsPayload, type Draft, type McpStatus, type ModuleId, type McpId } from "./assistant-state";

type Client = ReturnType<typeof createApiClient>;
type Props = {
  mode: "agent" | "bot";
  request: Client; refreshKey: number; busy: boolean;
  onDirtyChange: (dirty: boolean) => void;
  confirmAction: (options: Omit<ConfirmationRequest, "resolve">) => Promise<boolean>;
  admitOperation: (path: string, options: RequestInit) => Promise<SystemAction | null | undefined>;
};
const descriptions: Record<ModuleId, string> = {
  "telegram-mcp": "Чтение личного Telegram через сессию выбранного пользователя.",
  "vps-controller-mcp": "Обзор сервера, состояние служб и установленные компоненты.",
  "nodera-bot": "Telegram-интерфейс агента. Принимает сообщения разрешённых пользователей в личном чате.",
  "ai-agent": "Запросы к AI-провайдеру и вызовы разрешённых MCP-инструментов.",
};

export function AssistantView({ mode, request, refreshKey, busy, onDirtyChange, confirmAction, admitOperation }: Props) {
  const agentView = mode === "agent";
  const [panel, setPanel] = useState<"chat" | "settings">("chat");
  const tabsId = useId();
  const [status, setStatus] = useState<McpStatus>();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loadError, setLoadError] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [working, setWorking] = useState("");
  const [actor, setActor] = useState("");
  const [prompt, setPrompt] = useState("");
  const [turns, setTurns] = useState<{ id: number; question: string; answer?: string; tools?: string[]; error?: string }[]>([]);
  const nextTurn = useRef(0);
  const [checks, setChecks] = useState<Partial<Record<ModuleId, { ok: boolean; text: string; revision: string; actor: string }>>>({});
  const version = useRef(0);
  const client = useRef(request);
  const writing = useRef(false);
  const dirtyRef = useRef(false);
  const dirty = changed(draft, status?.settings);
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
  function patch(update: Partial<Draft>) {
    dirtyRef.current = true;
    if ("bot_token" in update) setChecks(current => ({ ...current, "nodera-bot": undefined }));
    setDraft(current => current ? { ...current, ...update } : current);
  }
  async function save(startBot = false) {
    if (!draft || writing.current) return;
    writing.current = true; version.current += 1;
    setWorking("save"); setError(""); setMessage("");
    try {
      const next = await request<McpStatus>("/assistant", { method: "PUT", body: JSON.stringify({ ...settingsPayload(draft), component: agentView ? "ai-agent" : "nodera-bot" }) });
      if (client.current !== request) return;
      setStatus(next); setDraft(makeDraft(next.settings)); dirtyRef.current = false;
      setChecks({}); setMessage("Настройки сохранены. Подключения проверяются отдельно.");
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
  async function send() {
    if (!prompt.trim() || writing.current) return;
    const id = ++nextTurn.current;
    const question = prompt.trim();
    writing.current = true; setWorking("chat"); setError(""); setPrompt("");
    setTurns(current => [...current, { id, question }]);
    try {
      const result = await request<{ result: { answer: string; tools_used: string[] } }>("/assistant/chat", {
        method: "POST", body: JSON.stringify({ prompt: question, telegram_id: actor ? Number(actor) : null }),
      });
      if (client.current === request) setTurns(current => current.map(turn => turn.id === id ? { ...turn, answer: result.result.answer, tools: result.result.tools_used } : turn));
    } catch (cause) { setTurns(current => current.map(turn => turn.id === id ? { ...turn, error: cause instanceof Error ? cause.message : "AI не ответил" } : turn)); }
    finally { writing.current = false; setWorking(""); }
  }
  if (!status || !draft) return <section className="mcpWorkspace"><p role="status">{loadError || "Загрузка компонента…"}</p><button type="button" className="ghostButton" onClick={() => void load()}>Повторить</button></section>;
  const componentId = agentView ? "ai-agent" : "nodera-bot";
  const selectedComponent = status.modules.find(item => item.id === componentId);
  if (!selectedComponent?.installed) return <section className="mcpWorkspace"><p>Установите {agentView ? "AI-agent" : "NoderaBot"} в разделе «Обзор → Компоненты».</p></section>;
  const readOnly = selectedComponent.management?.state !== "managed" || selectedComponent.management?.retained;
  const blocked = busy || Boolean(working) || Boolean(loadError) || Boolean(readOnly);
  const settingsBlocked = blocked || status.bot.active === true;
  const conflict = status.settings.revision !== draft.expected_revision;
  const settingsVisible = !agentView || panel === "settings";
  const canStartBot = !agentView && draft.modules["nodera-bot"] && Boolean(draft.bot_token || draft.bot_token === null && draft.bot_token_configured) && draft.accounts.some(account => account.bot_access && account.telegram_id > 0);
  const canCheckConnection = agentView ? !dirty && selectedComponent.enabled && selectedComponent.configured : Boolean(draft.bot_token || draft.bot_token === null && draft.bot_token_configured);
  const changeAccount = (index: number, update: Partial<Draft["accounts"][number]>) => patch({ accounts: draft.accounts.map((item, i) => i === index ? { ...item, ...update } : item) });
  const chooseActor = (value: string) => { setActor(value); setTurns([]); };
  const actorSelect = <label className="mcpActor">Контур<select value={actor} disabled={blocked} onChange={event => chooseActor(event.target.value)}><option value="">Администратор · VPS</option>{status.settings.accounts.map(item => <option key={item.telegram_id} value={item.telegram_id}>{item.name || item.telegram_id} · {item.telegram_id}</option>)}</select></label>;
  const result = checks[componentId];
  const setupMessage = !agentView && !selectedComponent.configured ? "Для запуска задайте токен, добавьте пользователя и включите бота." : "";
  return <div className={`mcpWorkspace ${agentView ? "agentWorkspace" : "botWorkspace"}`}>
    <section className="mcpHero" aria-label={agentView ? "Обзор AI-агента" : "Обзор NoderaBot"}>
      <div className="mcpHeroContent">
        <p className="eyebrow">{agentView ? "AI WORKSPACE" : "TELEGRAM BOT"}</p>
        <div className="mcpIdentity"><ProtocolIcon protocol={componentId}/><h1>{agentView ? "AI-агент" : "NoderaBot"}</h1><span className="mcpBadge">{!agentView && !selectedComponent.configured ? "Нужна настройка" : moduleState(status, componentId)}</span></div>
        <p className="mcpHeroLead">{agentView ? "Диалог с AI и подключённые инструменты управления." : "Личный доступ к серверу и AI-агенту через Telegram."}</p>
        <dl className="mcpHeroFacts">
          <div><dt>{agentView ? "МОДЕЛЬ" : "ПОДКЛЮЧЕНИЕ"}</dt><dd>{agentView ? status.settings.model || "Не задана" : status.settings.bot_token_configured ? "Токен сохранён" : "Не настроено"}</dd></div>
          <div><dt>{agentView ? "MCP-МОДУЛИ" : "РЕЖИМ"}</dt><dd>{agentView ? `${status.mcp_modules.filter(item => item.enabled).length} / ${status.mcp_modules.length}` : "Long polling"}</dd></div>
          <div><dt>ПОЛЬЗОВАТЕЛИ</dt><dd>{status.settings.accounts.filter(item => agentView ? item.telegram_access || item.vps_access : item.bot_access).length}</dd></div>
        </dl>
      </div>
    </section>
    <header className="mcpToolbar">
      {!agentView && <span className="mcpSectionLabel">Настройки бота</span>}
      {agentView ? <div className="mcpTabs" role="tablist" aria-label="Разделы AI-агента">{(["chat", "settings"] as const).map((value, index) => <button key={value} id={`${tabsId}-${value}-tab`} type="button" role="tab" aria-selected={panel === value} aria-controls={`${tabsId}-${value}`} tabIndex={panel === value ? 0 : -1} onClick={() => setPanel(value)} onKeyDown={event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault(); const next = event.key === "Home" ? "chat" : event.key === "End" ? "settings" : index === 0 ? "settings" : "chat";
        setPanel(next); document.getElementById(`${tabsId}-${next}-tab`)?.focus();
      }}>{value === "chat" ? "Чат" : "Настройки"}{value === "settings" && dirty && <i aria-hidden="true" title="Есть несохранённые настройки"/>}</button>)}</div>
        : <div className="mcpActions">{status.bot.active && <><button type="button" className="ghostButton" disabled={blocked} onClick={() => void runAction("restart")}>Перезапустить</button><button type="button" className="ghostButton" disabled={blocked} onClick={() => void runAction("stop")}>Остановить</button></>}{!status.bot.active && selectedComponent.configured && selectedComponent.enabled && <button type="button" className="ghostButton" disabled={blocked || dirty || status.legacy_bot.active === true} onClick={() => void runAction("start")}>Запустить</button>}</div>}
    </header>
    {loadError && <p className="mcpNotice error" role="alert">{loadError}</p>}
    {readOnly && <p className="mcpNotice">Для изменения настроек примите компонент под управление в «Обзоре».</p>}
    {!agentView && status.legacy_bot.active && <p className="mcpNotice">Работает прежний Telegram-бот. Для запуска NoderaBot нужно отдельное переключение.</p>}
    {error && <p className="mcpNotice error" role="alert">{error}</p>}
    {message && <p className="mcpNotice" role="status">{message}</p>}
    {agentView && <section id={`${tabsId}-chat`} role="tabpanel" aria-labelledby={`${tabsId}-chat-tab`} hidden={panel !== "chat"} className="mcpChat">
      <div className="mcpChatHead">{actorSelect}<span>{status.settings.model || "Модель не настроена"}</span>{!selectedComponent.configured && <button type="button" className="ghostButton" onClick={() => setPanel("settings")}>Подключить AI</button>}</div>
      {dirty && <p className="mcpNotice">Перед запросом сохраните изменения во вкладке «Настройки».</p>}
      <div className="mcpTranscript" role="log" aria-label="Диалог с AI-агентом" aria-live="polite">
        {!turns.length && <div className="mcpChatEmpty"><ProtocolIcon protocol="ai-agent"/><h2>Чем помочь?</h2><p>Задайте вопрос или используйте подключённые MCP.</p><div><button type="button" disabled={blocked} onClick={() => setPrompt("Покажи состояние сервера")}>Состояние сервера</button><button type="button" disabled={blocked} onClick={() => setPrompt("Покажи состояние служб")}>Службы</button></div></div>}
        {turns.map(turn => <div className="mcpTurn" key={turn.id}><div className="mcpUserMessage">{turn.question}</div><article className={`mcpAnswer ${turn.error ? "error" : ""}`}><small>AI-агент</small><p>{turn.error || turn.answer || "Обрабатывает запрос…"}</p>{turn.tools?.length ? <details><summary>Инструменты · {turn.tools.length}</summary><p className="mcpToolList">{turn.tools.join(" · ")}</p></details> : null}</article></div>)}
      </div>
      <div className="mcpComposer"><label className="mcpPrompt">Сообщение<textarea value={prompt} maxLength={12000} rows={2} disabled={blocked} onChange={event => setPrompt(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); if (!blocked && !dirty && prompt.trim() && selectedComponent.enabled && selectedComponent.configured) void send(); } }} placeholder="Запрос к агенту…" /></label><button type="button" className="primaryButton" disabled={blocked || dirty || !prompt.trim() || !selectedComponent.enabled || !selectedComponent.configured} onClick={() => void send()}>{working === "chat" ? "Ожидание…" : "Отправить"}</button></div>
      <p className="mcpChatHint">Каждый запрос — отдельный контекст. Данные вызванных инструментов передаются AI-провайдеру.</p>
    </section>}
    <section id={`${tabsId}-settings`} role={agentView ? "tabpanel" : undefined} aria-labelledby={agentView ? `${tabsId}-settings-tab` : undefined} hidden={!settingsVisible}>
      <form className="mcpSettings" onSubmit={event => { event.preventDefault(); void save(canStartBot && !status.bot.active); }}>
        <header className="mcpSettingsHead"><label className="mcpToggle"><input type="checkbox" checked={draft.modules[componentId]} disabled={settingsBlocked} onChange={event => patch({ modules: { ...draft.modules, [componentId]: event.target.checked } })}/>{agentView ? "Агент включён" : "Бот включён"}</label><div className="mcpActions"><button type="button" className="ghostButton" disabled={blocked || !dirty} onClick={() => { setDraft(makeDraft(status.settings)); setChecks({}); dirtyRef.current = false; }}>Отменить</button><button type="submit" className="primaryButton" disabled={settingsBlocked || !dirty || conflict}>{canStartBot && !status.bot.active ? "Сохранить и запустить" : "Сохранить"}</button></div></header>
        {status.bot.active && <p className="mcpNotice">Для изменения настроек остановите NoderaBot.</p>}
        {conflict && <p className="mcpNotice error">Настройки изменились на сервере. Отмените правки для загрузки актуальной версии.</p>}
        {setupMessage && <p className="mcpNotice">{setupMessage}</p>}
        <fieldset disabled={settingsBlocked} className="mcpConnection"><legend>{agentView ? "Подключение к AI" : "Подключение бота"}</legend>
          <div className={`mcpFields ${agentView ? "providerFields" : "botFields"}`}>{agentView ? <><label>Base URL<input type="url" value={draft.provider_url} placeholder="https://…/v1" onChange={event => patch({ provider_url: event.target.value })}/></label><label>Модель<input value={draft.model} maxLength={128} onChange={event => patch({ model: event.target.value })}/></label><Secret label="API-ключ" configured={draft.api_key_configured} value={draft.api_key} onChange={api_key => patch({ api_key })}/></> : <Secret label="Токен бота" configured={draft.bot_token_configured} value={draft.bot_token} onChange={bot_token => patch({ bot_token })}/>}</div>
        </fieldset>
        <div className="mcpConnectionStatus"><div className="mcpConnectionFoot"><small>{agentView ? "API chat/completions с поддержкой инструментов." : "Токен можно проверить до сохранения, включения бота и добавления пользователей."}</small><button type="button" className="ghostButton" disabled={blocked || !canCheckConnection} onClick={() => void check(componentId)}>{working === componentId ? "Проверяем…" : "Проверить подключение"}</button></div>
          {result && result.revision === status.settings.revision && result.actor === actor && <p className={result.ok ? "mcpCheck" : "mcpCheck error"} role="status">{result.text}</p>}
        </div>
        {agentView && <fieldset disabled={settingsBlocked} className="mcpSkills"><legend>MCP-подключения</legend><div className="mcpModuleGrid">{status.mcp_modules.map(skill => {
          const checked = checks[skill.id];
          return <article className="mcpModule" key={skill.id}><header><label className="mcpToggle"><input type="checkbox" checked={draft.mcp[skill.id as McpId]} onChange={event => patch({ mcp: { ...draft.mcp, [skill.id]: event.target.checked } })}/><strong>{skill.name}</strong></label><span className="mcpBadge">{moduleState(status, skill.id)}</span></header><p>{descriptions[skill.id]}</p>
            {skill.id === "telegram-mcp" && draft.mcp[skill.id] && <div className="mcpFields"><label>Telegram API ID<input type="number" min={1} value={draft.telegram_api_id ?? ""} onChange={event => patch({ telegram_api_id: event.target.value ? Number(event.target.value) : null })}/></label><Secret label="Telegram API hash" configured={draft.telegram_api_hash_configured} value={draft.telegram_api_hash} onChange={telegram_api_hash => patch({ telegram_api_hash })}/></div>}
            <footer><details><summary>Инструменты · {status.tools[skill.id].length}</summary><p className="mcpToolList">{status.tools[skill.id].join(" · ")}</p></details><button type="button" className="ghostButton" disabled={blocked || dirty || !skill.enabled || !skill.configured || skill.id === "telegram-mcp" && !actor} onClick={() => void check(skill.id)}>{working === skill.id ? "Проверяем…" : "Проверить"}</button></footer>
            {checked && checked.revision === status.settings.revision && checked.actor === actor && <p className={checked.ok ? "mcpCheck" : "mcpCheck error"} role="status">{checked.text}</p>}
          </article>;
        })}</div><div className="mcpScope">{actorSelect}<small>Контур определяет права для проверок MCP и чата.</small></div></fieldset>}
        <fieldset disabled={settingsBlocked} className="mcpAccounts"><legend>{agentView ? "Пользователи и права MCP" : "Разрешённые пользователи"}</legend>
          {!draft.accounts.length && <p className="mcpFieldHint">Добавьте Telegram ID пользователя.</p>}
          {draft.accounts.map((item, index) => <article className="mcpAccount" key={index}><div className="mcpAccountRow"><label>Telegram ID<input type="number" min={1} required value={item.telegram_id || ""} onChange={event => changeAccount(index, { telegram_id: Number(event.target.value) })}/></label><label>Имя<input value={item.name} maxLength={80} onChange={event => changeAccount(index, { name: event.target.value })}/></label><div className="mcpAccountRights">{agentView ? <><label><input type="checkbox" checked={item.telegram_access} onChange={event => changeAccount(index, { telegram_access: event.target.checked })}/>Telegram</label><label><input type="checkbox" checked={item.vps_access} onChange={event => changeAccount(index, { vps_access: event.target.checked })}/>VPS</label></> : <label><input type="checkbox" checked={item.bot_access} onChange={event => changeAccount(index, { bot_access: event.target.checked })}/>Доступ к боту</label>}</div><button type="button" className="ghostButton mcpRemove" aria-label={`Удалить пользователя ${item.name || item.telegram_id || index + 1}`} onClick={() => patch({ accounts: draft.accounts.filter((_, i) => i !== index) })}>Удалить</button></div>
            {agentView && <details className="mcpAccountAdvanced"><summary>Сессия и исключения</summary><div className="mcpFields"><Secret label="Telethon StringSession" configured={item.session_configured} value={item.session} onChange={session => changeAccount(index, { session })}/><label>Исключённые ID диалогов<input value={item.excluded_dialogs_text} placeholder="ID через запятую" onChange={event => changeAccount(index, { excluded_dialogs_text: event.target.value })}/></label></div><small>Сессия принадлежит этому Telegram ID. Подключение по QR пока недоступно.</small></details>}
          </article>)}
          <button type="button" className="ghostButton" disabled={draft.accounts.length >= 30} onClick={() => patch({ accounts: [...draft.accounts, { telegram_id: 0, name: "", bot_access: false, telegram_access: false, vps_access: false, excluded_dialogs: [], excluded_dialogs_text: "", session: null, session_configured: false }] })}>Добавить пользователя</button>
        </fieldset>
      </form>
    </section>
  </div>;
}

function Secret({ label, configured, value, onChange }: { label: string; configured: boolean; value: string | null; onChange: (value: string | null) => void }) {
  const id = useId();
  return <div className="mcpSecretField"><label htmlFor={id}>{label}</label><div className="mcpSecret"><input id={id} type="password" autoComplete="new-password" value={value ?? ""} placeholder={value === "" ? "Будет удалён" : configured && value === null ? "Сохранён" : "Не задан"} onChange={event => onChange(event.target.value || null)} /><button type="button" className="ghostButton" aria-label={`${value === "" ? "Вернуть сохранённый" : "Очистить"} ${label}`} onClick={() => onChange(value === "" ? null : "")}>{value === "" ? "Вернуть" : "Очистить"}</button></div></div>;
}
