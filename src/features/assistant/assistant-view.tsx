"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
  const [status, setStatus] = useState<McpStatus>();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loadError, setLoadError] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [working, setWorking] = useState("");
  const [actor, setActor] = useState("");
  const [prompt, setPrompt] = useState("");
  const [answer, setAnswer] = useState<{ answer: string; tools_used: string[] }>();
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
    setDraft(current => current ? { ...current, ...update } : current);
  }
  async function save() {
    if (!draft || writing.current) return;
    writing.current = true; version.current += 1;
    setWorking("save"); setError(""); setMessage("");
    try {
      const next = await request<McpStatus>("/assistant", { method: "PUT", body: JSON.stringify({ ...settingsPayload(draft), component: agentView ? "ai-agent" : "nodera-bot" }) });
      if (client.current !== request) return;
      setStatus(next); setDraft(makeDraft(next.settings)); dirtyRef.current = false;
      setChecks({}); setMessage("Настройки сохранены. Подключения проверяются отдельно.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Настройки не сохранены"); }
    finally { writing.current = false; setWorking(""); }
  }
  async function check(id: ModuleId) {
    if (!status || writing.current) return;
    const revision = status.settings.revision;
    writing.current = true; setWorking(id); setError("");
    try {
      await request(`/assistant/check/${id}`, { method: "POST", body: JSON.stringify({ telegram_id: actor ? Number(actor) : null }) });
      setChecks(current => ({ ...current, [id]: { ok: true, text: "Подключение проверено", revision, actor } }));
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
    writing.current = true; setWorking("chat"); setError(""); setAnswer(undefined);
    try {
      const result = await request<{ result: { answer: string; tools_used: string[] } }>("/assistant/chat", {
        method: "POST", body: JSON.stringify({ prompt: prompt.trim(), telegram_id: actor ? Number(actor) : null }),
      });
      setAnswer(result.result);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "AI не ответил"); }
    finally { writing.current = false; setWorking(""); }
  }
  if (!status || !draft) return <section className="mcpWorkspace"><p role="status">{loadError || "Загружаем модули MCP…"}</p><button type="button" className="ghostButton" onClick={() => void load()}>Повторить загрузку</button></section>;
  const selectedComponent = status.modules.find(item => item.id === (agentView ? "ai-agent" : "nodera-bot"));
  if (!selectedComponent?.installed) return <section className="mcpWorkspace"><p>Компонент {agentView ? "AI-agent" : "NoderaBot"} не установлен. Установите его образ в разделе «Обзор → Компоненты».</p></section>;
  const readOnly = selectedComponent.management?.state !== "managed" || selectedComponent.management?.retained;
  const blocked = busy || Boolean(working) || Boolean(loadError) || Boolean(readOnly);
  const settingsBlocked = blocked || status.bot.active === true;
  const conflict = status.settings.revision !== draft.expected_revision;
  const changeAccount = (index: number, update: Partial<Draft["accounts"][number]>) => patch({ accounts: draft.accounts.map((item, i) => i === index ? { ...item, ...update } : item) });
  return <div className="mcpWorkspace">
    <section className="mcpSummary">
      <div><p className="eyebrow">{agentView ? "AI AGENT" : "TELEGRAM BOT"}</p><h2>{agentView ? "Модель, инструменты и контуры доступа" : "NoderaBot"}</h2><p>{agentView ? "Настройки AI-провайдера и подключаемые MCP-навыки. Агент доступен из панели и через Telegram-бота." : "Отдельный Telegram-интерфейс. Команды бота работают самостоятельно, для AI-ответов используется модуль агента."}</p></div>
      <div className="mcpRuntime"><span>Компонент установлен из образа</span><span>Обновление и удаление: Обзор → Компоненты</span></div>
    </section>
    {loadError && <p className="mcpNotice error" role="alert">{loadError} Состояние модулей не обновлено.</p>}
    {readOnly && <p className="mcpNotice">Компонент доступен для просмотра. Примите его под управление в разделе «Обзор → Компоненты».</p>}
    {!agentView && status.legacy_bot.active && <p className="mcpNotice">На сервере работает прежний Telegram-бот. Он не принят под управление NoderaBot. Запуск нового бота заблокирован до отдельного переключения.</p>}
    {error && <p className="mcpNotice error" role="alert">{error}</p>}
    {message && <p className="mcpNotice" role="status">{message}</p>}
    <div className="mcpModuleGrid">{status.modules.filter(item => item.id === (agentView ? "ai-agent" : "nodera-bot")).map(module => {
      const result = checks[module.id];
      return <article className="mcpModule" key={module.id}>
        <header><h3>{module.name}</h3><span className="mcpBadge">{moduleState(status, module.id)}</span></header>
        <p>{descriptions[module.id]}</p>
        <label className="mcpToggle"><input type="checkbox" checked={draft.modules[module.id as "ai-agent" | "nodera-bot"]} disabled={settingsBlocked} onChange={event => patch({ modules: { ...draft.modules, [module.id]: event.target.checked } })} />Включить модуль</label>
        {module.id in status.tools && <p className="mcpToolList">{status.tools[module.id as keyof McpStatus["tools"]].join(" · ")}</p>}
        <footer><button type="button" className="ghostButton" disabled={blocked || dirty || !module.enabled || !module.configured || !module.installed} onClick={() => void check(module.id)}>{working === module.id ? "Проверяем…" : "Проверить подключение"}</button>
          {module.id === "nodera-bot" && <button type="button" className="ghostButton" disabled={blocked || dirty || !module.installed || status.legacy_bot.active === true || !module.enabled || !module.configured} onClick={() => void runAction(status.bot.active ? "restart" : "start")}>{status.bot.active ? "Перезапустить" : "Запустить"}</button>}
          {module.id === "nodera-bot" && status.bot.active && <button type="button" className="ghostButton" disabled={blocked} onClick={() => void runAction("stop")}>Остановить</button>}
        </footer>
        {result && result.revision === status.settings.revision && result.actor === actor && <p className={result.ok ? "mcpCheck" : "mcpCheck error"} role="status">{result.text}</p>}
      </article>;
    })}</div>
    {agentView && <section className="mcpSkills"><header><p className="eyebrow">MCP SKILLS</p><h2>Подключаемые инструменты агента</h2><p>Каждое MCP-подключение включается отдельно. Доступные навыки ограничены чтением.</p></header>
      <label>Контур запроса<select value={actor} disabled={blocked} onChange={event => { setActor(event.target.value); setAnswer(undefined); }}><option value="">Администратор панели · только VPS</option>{status.settings.accounts.map(item => <option key={item.telegram_id} value={item.telegram_id}>{item.name || item.telegram_id} · {item.telegram_id}</option>)}</select></label>
      <p>Выбранный пользователь определяет права для проверок подключения и запросов к агенту ниже.</p>
      <div className="mcpModuleGrid">{status.mcp_modules.map(skill => {
      const result = checks[skill.id];
      return <article className="mcpModule" key={skill.id}><header><h3>{skill.name}</h3><span className="mcpBadge">{moduleState(status, skill.id)}</span></header><p>{descriptions[skill.id]}</p>
        <label className="mcpToggle"><input type="checkbox" checked={draft.mcp[skill.id as McpId]} disabled={settingsBlocked} onChange={event => patch({ mcp: { ...draft.mcp, [skill.id]: event.target.checked } })} />Подключить к агенту</label>
        <p className="mcpToolList">{status.tools[skill.id].join(" · ")}</p>
        <footer><button type="button" className="ghostButton" disabled={blocked || dirty || !skill.enabled || !skill.configured || !status.runtime_installed || skill.id === "telegram-mcp" && !actor} onClick={() => void check(skill.id)}>{working === skill.id ? "Проверяем…" : "Проверить подключение"}</button></footer>
        {result && result.revision === status.settings.revision && result.actor === actor && <p className={result.ok ? "mcpCheck" : "mcpCheck error"} role="status">{result.text}</p>}
      </article>;
    })}</div></section>}
    <form className="mcpSettings" onSubmit={event => { event.preventDefault(); void save(); }}>
      <header><div><p className="eyebrow">CONFIGURATION</p><h2>Настройки модулей</h2></div><div className="mcpActions"><button type="button" className="ghostButton" disabled={blocked || !dirty} onClick={() => { setDraft(makeDraft(status.settings)); dirtyRef.current = false; }}>Отменить правки</button><button type="submit" className="primaryButton" disabled={settingsBlocked || !dirty || conflict}>Сохранить</button></div></header>
      {status.bot.active && <p className="mcpNotice">Для изменения настроек остановите NoderaBot. После сохранения запустите его снова.</p>}
      {conflict && <p className="mcpNotice error">Настройки изменились на сервере. Ваши правки сохранены в форме. Отмените их, чтобы загрузить текущую версию.</p>}
      {agentView && <fieldset disabled={settingsBlocked} className="mcpFields"><legend>AI-agent</legend>
        <label>Base URL провайдера<input type="url" value={draft.provider_url} placeholder="HTTPS URL API с поддержкой chat/completions" onChange={event => patch({ provider_url: event.target.value })} /></label>
        <label>Модель<input value={draft.model} maxLength={128} onChange={event => patch({ model: event.target.value })} /></label>
        <Secret label="API-ключ" configured={draft.api_key_configured} value={draft.api_key} onChange={api_key => patch({ api_key })} />
        <p className="mcpFieldHint">Поддерживается API chat/completions с вызовами инструментов. Провайдер и модель задаются вами.</p>
      </fieldset>}
      {!agentView && <fieldset disabled={settingsBlocked} className="mcpFields"><legend>NoderaBot</legend><Secret label="Токен бота" configured={draft.bot_token_configured} value={draft.bot_token} onChange={bot_token => patch({ bot_token })} /><p className="mcpFieldHint">Long polling. Для эксперимента используйте отдельного бота. Чат с агентом и команды /start, /help, /status; история диалога между запросами не сохраняется.</p></fieldset>}
      {agentView && <fieldset disabled={settingsBlocked} className="mcpFields"><legend>Личный Telegram · MCP</legend>
        <label>Telegram API ID<input type="number" min={1} value={draft.telegram_api_id ?? ""} onChange={event => patch({ telegram_api_id: event.target.value ? Number(event.target.value) : null })} /></label>
        <Secret label="Telegram API hash" configured={draft.telegram_api_hash_configured} value={draft.telegram_api_hash} onChange={telegram_api_hash => patch({ telegram_api_hash })} />
        <p className="mcpFieldHint">Используются ваши реквизиты Telegram API и отдельная Telethon StringSession каждого пользователя. Вход по QR в этом варианте ещё не подключён.</p>
      </fieldset>}
      <fieldset disabled={settingsBlocked} className="mcpAccounts"><legend>{agentView ? "Пользователи и контуры MCP" : "Пользователи NoderaBot"}</legend><p>{agentView ? "Общий список пользователей приложения. Доступ к личному Telegram и к VPS выдаётся отдельно от доступа к боту." : "Разрешите использование бота нужным Telegram ID. Права на MCP-инструменты настраиваются в разделе AI-агента."}</p>
        {draft.accounts.map((item, index) => <article className="mcpAccount" key={index}>
          <div className="mcpAccountFields"><label>Telegram ID<input type="number" min={1} required value={item.telegram_id || ""} onChange={event => changeAccount(index, { telegram_id: Number(event.target.value) })} /></label><label>Имя<input value={item.name} maxLength={80} onChange={event => changeAccount(index, { name: event.target.value })} /></label>
          {agentView && <><Secret label="Telethon StringSession" configured={item.session_configured} value={item.session} onChange={session => changeAccount(index, { session })} />
          <label>Исключённые ID диалогов<input value={item.excluded_dialogs_text} placeholder="ID через запятую" onChange={event => changeAccount(index, { excluded_dialogs_text: event.target.value })} /></label></>}</div>
          <div className="mcpAccountRights">{agentView ? <><label><input type="checkbox" checked={item.telegram_access} onChange={event => changeAccount(index, { telegram_access: event.target.checked })} />Личный Telegram · чтение</label><label><input type="checkbox" checked={item.vps_access} onChange={event => changeAccount(index, { vps_access: event.target.checked })} />vpsController · чтение</label></> : <label><input type="checkbox" checked={item.bot_access} onChange={event => changeAccount(index, { bot_access: event.target.checked })} />Разрешить NoderaBot</label>}<button type="button" className="ghostButton" onClick={() => patch({ accounts: draft.accounts.filter((_, i) => i !== index) })}>Удалить пользователя из приложения</button></div>
        </article>)}
        <button type="button" className="ghostButton" disabled={draft.accounts.length >= 30} onClick={() => patch({ accounts: [...draft.accounts, { telegram_id: 0, name: "", bot_access: false, telegram_access: false, vps_access: false, excluded_dialogs: [], excluded_dialogs_text: "", session: null, session_configured: false }] })}>Добавить пользователя</button>
      </fieldset>
    </form>
    {agentView && <section className="mcpPlayground"><header><div><p className="eyebrow">AGENT</p><h2>Проверить запрос</h2></div></header>
      <p>Контур запроса выбран выше, в настройках MCP. Каждый запрос начинает новый диалог. Данные вызванных инструментов передаются выбранному AI-провайдеру.</p>
      <label>Запрос<textarea value={prompt} maxLength={12000} rows={3} disabled={blocked} onChange={event => setPrompt(event.target.value)} placeholder="Например: покажи состояние служб сервера" /></label>
      <button type="button" className="primaryButton" disabled={blocked || dirty || !prompt.trim() || !status.runtime_installed || !status.settings.modules["ai-agent"]} onClick={() => void send()}>{working === "chat" ? "Агент обрабатывает запрос…" : "Отправить агенту"}</button>
      {answer && <article className="mcpAnswer" aria-live="polite"><p>{answer.answer}</p><small>Вызванные инструменты: {answer.tools_used.join(", ") || "нет"}</small></article>}
    </section>}
  </div>;
}

function Secret({ label, configured, value, onChange }: { label: string; configured: boolean; value: string | null; onChange: (value: string | null) => void }) {
  return <label>{label}<span className="mcpSecret"><input type="password" autoComplete="new-password" value={value ?? ""} placeholder={configured && value === null ? "Сохранён · оставьте пустым для сохранения" : "Не задан"} onChange={event => onChange(event.target.value || null)} /><button type="button" className="ghostButton" onClick={() => onChange(value === "" ? null : "")}>{value === "" ? "Сохранить прежний" : "Очистить"}</button></span></label>;
}
