import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { createApiClient } from "../../shared/lib/api-request";
import { ChatIcon } from "./chat-icon";
import { moduleState, type Draft, type McpId, type McpStatus, type ModuleId, type ProviderDraft } from "./assistant-state";

export const panels = [
  { id: "chat", label: "Чат", icon: "chat" },
  { id: "settings", label: "Настройки AI", icon: "gear" },
  { id: "users", label: "Доступ", icon: "users" },
] as const;
export type Panel = typeof panels[number]["id"];
type AccountDraft = Draft["accounts"][number];
type Check = { ok: boolean; text: string; revision: string; actor: string; username?: string };
type Quota = { unlimited: boolean; remaining: number; account_remaining?: number };
type BotContact = { telegram_id: number; username: string; name: string; seen_at: number };
type Props = {
  panel: Exclude<Panel, "chat">;
  request: ReturnType<typeof createApiClient>;
  status: McpStatus;
  draft: Draft;
  patch: (update: Partial<Draft>) => void;
  blocked: boolean;
  dirty: boolean;
  refreshKey: number;
  working: string;
  actor: string;
  actorSelect: ReactNode;
  checks: Partial<Record<ModuleId, Check>>;
  check: (id: ModuleId) => Promise<void>;
  runAction: (action: "start" | "stop") => Promise<void>;
};

const moduleDescriptions: Record<McpId, string> = {
  "telegram-mcp": "Диалоги, история и поиск.",
  "vps-controller-mcp": "Метрики, службы, компоненты и состояние панели.",
};
const blankAccount = (): AccountDraft => ({
  telegram_id: 0, name: "", bot_access: false, bot_chat_access: false, address_as: "",
  telegram_access: false, vps_access: false, excluded_dialogs: [],
  excluded_dialogs_text: "", session: null, session_configured: false,
});

export function NoderaSettings({ panel, request, status, draft, patch, blocked, dirty, refreshKey, actor, actorSelect, checks, check, runAction, working }: Props) {
  const [providerEdit, setProviderEdit] = useState<ProviderDraft>();
  const [providerChecks, setProviderChecks] = useState<Record<string, { text: string; failed?: boolean; quota?: Quota | null }>>({});
  const [checkingProvider, setCheckingProvider] = useState<string>();
  const [telegramMcpOpen, setTelegramMcpOpen] = useState(false);
  const [userEdit, setUserEdit] = useState<{ index: number; value: AccountDraft; observed?: boolean }>();
  const [lookup, setLookup] = useState("");
  const [lookingUp, setLookingUp] = useState(false);
  const [lookupError, setLookupError] = useState("");
  const [contacts, setContacts] = useState<{ revision: string; rows: BotContact[]; error?: string }>();
  const [contactsLimit, setContactsLimit] = useState(20);

  const providers = draft.providers ?? [];
  const revision = status.settings.revision;
  const contactsEnabled = status.settings.bot_token_configured;
  useEffect(() => {
    if (panel !== "users" || !contactsEnabled) return;
    let current = true;
    void request<{ items: BotContact[] }>("/assistant/bot-users", { cache: "no-store" }).then(
      result => { if (current) setContacts({ revision, rows: result.items ?? [] }); },
      () => { if (current) setContacts(previous => ({ revision, rows: previous?.revision === revision ? previous.rows : [], error: "Контакты Telegram недоступны" })); },
    );
    return () => { current = false; };
  }, [panel, request, contactsEnabled, revision, refreshKey]);
  const recent = contactsEnabled && contacts?.revision === revision ? contacts.rows : [];
  const contactsById = new Map(recent.map(item => [item.telegram_id, item]));
  const assignedIds = new Set(draft.accounts.map(item => item.telegram_id));
  const guests = recent.filter(item => !assignedIds.has(item.telegram_id));
  const agent = status.modules.find(item => item.id === "ai-agent");
  const bot = status.modules.find(item => item.id === "nodera-bot");
  const botLocked = blocked || !bot?.installed || bot.management?.state !== "managed";
  const botCredentialsLocked = botLocked || status.bot.active === true;
  const account = userEdit?.value;

  function editAccount(value: Partial<AccountDraft>) {
    setUserEdit(current => current && { ...current, value: { ...current.value, ...value } });
  }

  function storeProvider(provider: ProviderDraft) {
    const exists = providers.some(item => item.id === provider.id);
    patch({ providers: exists ? providers.map(item => item.id === provider.id ? provider : item) : [...providers, provider],
      active_provider: draft.active_provider ?? provider.id });
    setProviderChecks(current => ({ ...current, [provider.id]: { text: "" } }));
    setProviderEdit(undefined);
  }

  async function probe(provider: ProviderDraft) {
    setCheckingProvider(provider.id);
    try {
      const result = await request<{ models: string[]; quota?: Quota | null }>("/assistant/models", {
        method: "POST", body: JSON.stringify({ provider_url: provider.provider_url, api_key: provider.api_key, provider_id: provider.id, include_quota: true }),
      });
      const available = provider.model_mode === "catalog" || result.models.includes(provider.model);
      setProviderChecks(current => ({ ...current, [provider.id]: available
        ? { text: `Каталог доступен · моделей ${result.models.length}`, quota: result.quota }
        : { text: "Модель отсутствует в каталоге", failed: true, quota: result.quota } }));
    } catch (cause) {
      setProviderChecks(current => ({ ...current, [provider.id]: { text: cause instanceof Error ? cause.message : "Проверка не выполнена", failed: true } }));
    } finally {
      setCheckingProvider(undefined);
    }
  }

  async function findUser() {
    setLookingUp(true);
    setLookupError("");
    try {
      const found = await request<{ telegram_id: number; username: string; name: string }>("/assistant/bot-users/lookup", {
        method: "POST", body: JSON.stringify({ username: lookup.trim() }),
      });
      const index = draft.accounts.findIndex(item => item.telegram_id === found.telegram_id);
      setUserEdit({ index, value: index >= 0 ? draft.accounts[index] : {
        ...blankAccount(), telegram_id: found.telegram_id, name: found.name || found.username,
      } });
    } catch (cause) {
      setLookupError(cause instanceof Error ? cause.message : "Пользователь не найден");
    } finally {
      setLookingUp(false);
    }
  }

  const checkResult = (id: ModuleId) => {
    const result = checks[id];
    return result?.revision === status.settings.revision && result.actor === actor &&
      <p className={result.ok ? "mcpCheck" : "mcpCheck error"} role="status">{result.text}</p>;
  };

  const connections = <section className="noderaSettings" aria-label="Подключения">
    <header className="noderaBlockHead"><h2>Подключения</h2>
      <div className="noderaBar">
      <label className="mcpToggle"><input type="checkbox" checked={draft.modules["ai-agent"]} disabled={blocked || !agent?.installed}
        onChange={event => patch({ modules: { ...draft.modules, "ai-agent": event.target.checked } })}/>AI включён</label>
      <button type="button" className="ghostButton" disabled={blocked || providers.length >= 12}
        onClick={() => setProviderEdit({ id: Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join(""), name: "", provider_url: "", api_key: null, api_key_configured: false, model: "", model_mode: "catalog" })}>
        <ChatIcon name="plus"/>Добавить API
      </button>
      </div>
    </header>
    {!providers.length ? <p className="mcpFieldHint">Нет подключений</p> : <div className="noderaTableScroll"><table className="noderaProviderTable">
      <thead><tr><th>Провайдер</th><th className="noderaProviderStat">Модели</th><th className="noderaProviderStat">Запросы</th><th className="noderaProviderStat">Токены вход / выход</th><th className="noderaProviderStat">Квота ключа</th><th aria-label="Действия"/></tr></thead>
      <tbody>{providers.map(provider => {
        const usage = status.provider_usage?.[provider.id];
        const active = draft.active_provider === provider.id;
        const probeResult = providerChecks[provider.id];
        const models = provider.model_mode === "catalog" ? "Каталог" : provider.model || "—";
        const tokens = usage?.reported_requests ? `${usage.prompt_tokens.toLocaleString("ru")} / ${usage.completion_tokens.toLocaleString("ru")}` : "—";
        const quota = probeResult?.quota ? probeResult.quota.unlimited ? "Без лимита" : probeResult.quota.remaining.toLocaleString("ru") : "—";
        return <tr key={provider.id}>
          <th scope="row"><strong>{provider.name}</strong>{active && <span className="mcpBadge">Активно</span>}
            <small>{provider.provider_url}</small>
            <span className="noderaProviderCompact">
              <small>{models} · Запросы {usage?.requests ?? 0}</small>
              <small>Токены {tokens} · Квота {quota}</small>
              {probeResult?.quota?.account_remaining !== undefined && <small>Аккаунт · {probeResult.quota.account_remaining.toLocaleString("ru")}</small>}
            </span>
            {probeResult?.text && <small className={probeResult.failed ? "mcpCheck error" : "mcpCheck"} role="status">{probeResult.text}</small>}
          </th>
          <td className="noderaProviderStat">{models}</td>
          <td className="noderaProviderStat">{usage?.requests ?? 0}</td>
          <td className="noderaProviderStat" title={usage ? `Данные провайдера для ${usage.reported_requests} из ${usage.requests} запросов` : undefined}>
            {tokens}
          </td>
          <td className="noderaProviderStat">{quota}
            {probeResult?.quota?.account_remaining !== undefined && <small>Аккаунт · {probeResult.quota.account_remaining.toLocaleString("ru")}</small>}
          </td>
          <td><div className="noderaTableActions">
            {!active && <button type="button" className="ghostButton" disabled={blocked} onClick={() => patch({ active_provider: provider.id })}>Активировать</button>}
            <button type="button" className="mcpIconButton" aria-label={`Проверить API ${provider.name}`} title="Проверить API" disabled={blocked || !!checkingProvider || !(provider.api_key ?? provider.api_key_configured)} onClick={() => void probe(provider)}>
              <ChatIcon name="refresh"/>
            </button>
            <button type="button" className="mcpIconButton" aria-label={`Настроить ${provider.name}`} title="Настроить" disabled={blocked} onClick={() => setProviderEdit(provider)}><ChatIcon name="gear"/></button>
          </div></td>
        </tr>;
      })}</tbody>
    </table></div>}
    {providerEdit && <SettingsDialog title="Подключение API" close={() => setProviderEdit(undefined)}
      submit={() => storeProvider({ ...providerEdit, name: providerEdit.name.trim() || new URL(providerEdit.provider_url).hostname })} blocked={blocked}>
      <label>Название<input value={providerEdit.name} maxLength={80} onChange={event => setProviderEdit({ ...providerEdit, name: event.target.value })}/></label>
      <label>Base URL<input type="url" required pattern="https://.+" value={providerEdit.provider_url} placeholder="https://api.nyxos.shop/v1" maxLength={512}
        onChange={event => setProviderEdit({ ...providerEdit, provider_url: event.target.value, api_key: null, api_key_configured: false })}/></label>
      <Secret label="API-ключ" configured={providerEdit.api_key_configured} value={providerEdit.api_key} onChange={api_key => setProviderEdit({ ...providerEdit, api_key })}/>
      <label>Модели<select value={providerEdit.model_mode} onChange={event => setProviderEdit({ ...providerEdit, model_mode: event.target.value as ProviderDraft["model_mode"] })}>
        <option value="catalog">Каталог API</option><option value="single">Одна модель</option></select></label>
      {providerEdit.model_mode === "single" && <label>Модель<input value={providerEdit.model} required maxLength={128} onChange={event => setProviderEdit({ ...providerEdit, model: event.target.value })}/></label>}
      {providers.some(item => item.id === providerEdit.id) && <button type="button" className="ghostButton" disabled={blocked} onClick={() => {
        const remaining = providers.filter(item => item.id !== providerEdit.id);
        patch({ providers: remaining, active_provider: draft.active_provider === providerEdit.id ? remaining[0]?.id ?? null : draft.active_provider });
        setProviderEdit(undefined);
      }}>Удалить подключение</button>}
    </SettingsDialog>}
  </section>;

  const botCheck = checks["nodera-bot"];
  const botUsername = draft.bot_token === null ? status.bot_username || botCheck?.username : botCheck?.username;
  const tokenPresent = Boolean(draft.bot_token ?? draft.bot_token_configured);
  const botAction = status.bot.active ? "stop" : botUsername ? "start" : "check";
  const telegram = <section className="noderaSettings" aria-label="Telegram">
    <header className="noderaBlockHead"><h2>Telegram</h2><span className="mcpBadge">{moduleState(status, "nodera-bot")}</span></header>
    <fieldset className="noderaTelegram" disabled={botLocked}>
      <div className="noderaCredentials">
        <fieldset disabled={botCredentialsLocked} className="noderaBotToken">
          <Secret label="Токен бота" configured={draft.bot_token_configured} value={draft.bot_token} onChange={bot_token => patch({ bot_token })} compact/>
        </fieldset>
        <button type="button" className="primaryButton" disabled={!tokenPresent && botAction !== "stop" || botAction === "start" && status.legacy_bot.active === true}
          onClick={() => { if (botAction === "check") void check("nodera-bot"); else void runAction(botAction); }}>
          {botAction === "stop" ? "Остановить" : botAction === "start" ? "Запустить" : working === "nodera-bot" ? "Проверяем…" : "Проверить токен"}
        </button>
      </div>
      <div className="noderaBar">
        {botUsername && tokenPresent ? <strong>@{botUsername}</strong> : draft.bot_token === null && draft.bot_token_configured && <span>Токен сохранён</span>}
        {botCheck && !botCheck.ok && <p className="mcpCheck error" role="status">{botCheck.text}</p>}
        {status.legacy_bot.active && <p className="mcpNotice">Для запуска остановите прежнего Telegram-бота.</p>}
      </div>
    </fieldset>
  </section>;

  const modules = <section className="noderaSettings" aria-label="MCP">
    <header className="noderaBlockHead"><h2>MCP</h2></header>
    <fieldset disabled={blocked} className="mcpSkills">
      <div className="mcpModuleGrid">{status.mcp_modules.map(item => {
        const enabled = draft.mcp[item.id];
        return <article key={item.id} className={`mcpModule ${enabled ? "isInstalled" : ""}`}>
          <header><div className="mcpModuleIdentity"><span className="mcpModuleIcon"><ChatIcon name={item.id === "telegram-mcp" ? "telegram" : "module"}/></span>
            <strong>{item.id === "vps-controller-mcp" ? "mcp_312_node_net" : item.name}</strong></div></header>
          <p>{item.description ?? moduleDescriptions[item.id]}</p>
          <footer>
            <button type="button" className="ghostButton" aria-pressed={enabled} aria-label={`${item.name} · ${enabled ? "Включён" : "Выключен"}`}
              onClick={() => patch({ mcp: { ...draft.mcp, [item.id]: !enabled },
                mcp_installed: !enabled ? [...new Set([...(draft.mcp_installed ?? []), item.id])] : draft.mcp_installed })}>
              {enabled ? "Включён" : "Выключен"}
            </button>
            {item.id === "telegram-mcp" && <button type="button" className="mcpIconButton" aria-label="Настроить Telegram MCP" title="Настроить" onClick={() => setTelegramMcpOpen(true)}><ChatIcon name="gear"/></button>}
          </footer>
        </article>;
      })}</div>
    </fieldset>
    {telegramMcpOpen && <SettingsDialog title="Telegram MCP" close={() => setTelegramMcpOpen(false)}>
      <label>Telegram API ID<input type="number" min={1} value={draft.telegram_api_id ?? ""}
        onChange={event => patch({ telegram_api_id: event.target.value ? Number(event.target.value) : null })}/></label>
      <Secret label="Telegram API hash" configured={draft.telegram_api_hash_configured} value={draft.telegram_api_hash} onChange={telegram_api_hash => patch({ telegram_api_hash })}/>
      {actorSelect}
      <button type="button" className="ghostButton" disabled={blocked || dirty || !actor || !status.mcp_modules.some(item => item.id === "telegram-mcp" && item.enabled && item.configured)}
        onClick={() => void check("telegram-mcp")}>Проверить модуль</button>
      {checkResult("telegram-mcp")}
    </SettingsDialog>}
  </section>;

  const users = <section className="noderaSettings" aria-label="Пользователи">
    <header className="noderaBlockHead"><h2>Пользователи</h2>
      <button type="button" className="ghostButton" disabled={blocked || draft.accounts.length >= 30} onClick={() => {
        setLookup(""); setLookupError(""); setUserEdit({ index: -1, value: blankAccount() });
      }}><ChatIcon name="plus"/>Добавить пользователя</button>
    </header>
    <div className="noderaTableScroll"><table className="noderaAccessTable">
      <thead><tr><th>Пользователь</th><th className="noderaIdColumn">Telegram ID</th><th>Роль бота</th><th>MCP</th><th aria-label="Действия"/></tr></thead>
      <tbody>
        <tr><th scope="row">Администратор панели</th><td className="noderaIdColumn">—</td><td>—</td><td>VPS</td><td>—</td></tr>
        {draft.accounts.map((item, index) => <tr key={item.telegram_id}>
          <th scope="row">{item.name || item.telegram_id}{item.name && <small className="noderaInlineId">{item.telegram_id}</small>}
            {contactsById.get(item.telegram_id)?.username && <small>@{contactsById.get(item.telegram_id)?.username}</small>}
            {!item.bot_access && item.address_as && <small>{item.address_as}</small>}</th>
          <td className="noderaIdColumn">{item.telegram_id}</td><td>{item.bot_access ? "Администратор" : item.bot_chat_access ? "Чат" : "Гость"}</td>
          <td>{[item.telegram_access && "Telegram", (item.vps_access || item.bot_access && draft.modules["nodera-bot"]) && "VPS"].filter(Boolean).join(", ") || "—"}</td>
          <td><button type="button" className="mcpIconButton" aria-label={`Настроить пользователя ${item.name || item.telegram_id}`} disabled={blocked}
            onClick={() => setUserEdit({ index, value: item })}><ChatIcon name="gear"/></button></td>
        </tr>)}
        {guests.slice(0, contactsLimit).map(item => <tr key={item.telegram_id}>
          <th scope="row">{item.name || item.telegram_id}
            {item.name && <small className="noderaInlineId">{item.telegram_id}</small>}
            {item.username && <small>@{item.username}</small>}
          </th>
          <td className="noderaIdColumn">{item.telegram_id}</td><td>Гость</td><td>—</td>
          <td><button type="button" className="mcpIconButton" aria-label={`Настроить пользователя ${item.name || item.telegram_id}`}
            title="Настроить права" disabled={blocked || draft.accounts.length >= 30} onClick={() => {
              setLookup(""); setLookupError("");
              setUserEdit({ index: -1, observed: true, value: { ...blankAccount(), telegram_id: item.telegram_id, name: item.name || item.username } });
            }}><ChatIcon name="gear"/></button></td>
        </tr>)}
      </tbody>
    </table></div>
    {contacts?.revision === revision && contacts.error && <small role="status">{contacts.error}</small>}
    {guests.length > contactsLimit && <button type="button" className="ghostButton noderaMoreContacts" onClick={() => setContactsLimit(value => value + 20)}>Ещё пользователи</button>}
    {userEdit && account && <SettingsDialog title={userEdit.index < 0 ? "Добавить пользователя" : "Пользователь"} close={() => setUserEdit(undefined)}
      blocked={blocked || !account.telegram_id || draft.accounts.some((item, index) => item.telegram_id === account.telegram_id && index !== userEdit.index)}
      submit={() => {
        patch({ accounts: userEdit.index < 0 ? [...draft.accounts, account] : draft.accounts.map((item, index) => index === userEdit.index ? account : item) });
        setUserEdit(undefined);
      }}>
      {userEdit.index < 0 && !userEdit.observed && bot?.installed && status.settings.bot_token_configured && <div className="noderaBar">
        <label>Найти в Telegram<input value={lookup} placeholder="@username" maxLength={65} onChange={event => setLookup(event.target.value)}/></label>
        <button type="button" className="ghostButton" disabled={blocked || lookingUp || !lookup.trim()} onClick={() => void findUser()}>Найти</button>
        {lookupError && <small role="alert">{lookupError}</small>}
      </div>}
      <label>Telegram ID<input type="number" min={1} max={2 ** 52} required readOnly={userEdit.index >= 0 || userEdit.observed} value={account.telegram_id || ""} onChange={event => editAccount({ telegram_id: Number(event.target.value) })}/></label>
      <label>Имя в таблице<input value={account.name} maxLength={80} onChange={event => editAccount({ name: event.target.value })}/></label>
      <label>Роль в Telegram<select value={account.bot_access ? "admin" : account.bot_chat_access ? "chat" : "guest"}
        onChange={event => editAccount({ bot_access: event.target.value === "admin", bot_chat_access: event.target.value === "chat", ...(event.target.value === "admin" ? { address_as: "" } : {}) })}>
        <option value="guest">Гость</option><option value="chat">Чат с Нодерой</option><option value="admin">Администратор бота</option>
      </select></label>
      {!account.bot_access && <label>Как Нодера обращается к пользователю<input value={account.address_as ?? ""} maxLength={80} onChange={event => editAccount({ address_as: event.target.value })}/></label>}
      <div className="noderaBar">
        <label className="mcpToggle"><input type="checkbox" checked={account.telegram_access} onChange={event => editAccount({ telegram_access: event.target.checked })}/>Telegram MCP</label>
        <label className="mcpToggle"><input type="checkbox" checked={account.vps_access || account.bot_access && draft.modules["nodera-bot"]} disabled={account.bot_access && draft.modules["nodera-bot"]} onChange={event => editAccount({ vps_access: event.target.checked })}/>312node.net MCP</label>
      </div>
      {account.telegram_access && <>
        <Secret label="Telegram-сессия" configured={account.session_configured} value={account.session} onChange={session => editAccount({ session })}/>
        <label>Исключённые диалоги<input value={account.excluded_dialogs_text} placeholder="ID через запятую" onChange={event => editAccount({ excluded_dialogs_text: event.target.value })}/></label>
      </>}
      {userEdit.index >= 0 && <button type="button" className="ghostButton" disabled={blocked} onClick={() => {
        patch({ accounts: draft.accounts.filter((_, index) => index !== userEdit.index) });
        setUserEdit(undefined);
      }}>Удалить пользователя</button>}
    </SettingsDialog>}
  </section>;

  const behavior = <section className="noderaSettings" aria-label="Поведение">
    <header className="noderaBlockHead"><h2>Поведение</h2></header>
    <fieldset className="mcpBehavior" disabled={blocked}>
      <div className="mcpBehaviorFields">
        <label>Манера общения<select value={draft.assistant_style} onChange={event => patch({ assistant_style: event.target.value as Draft["assistant_style"] })}>
          <option value="balanced">Спокойно и по делу</option><option value="concise">Кратко</option><option value="friendly">Дружелюбно</option><option value="technical">Технически подробно</option>
        </select></label>
        <div className="noderaMemory">
          <label className="mcpToggle"><input type="checkbox" checked={draft.memory_enabled} onChange={event => patch({ memory_enabled: event.target.checked })}/>Сохранять диалоги</label>
          {draft.memory_enabled && <><label>Срок хранения, дни<input type="number" min={1} max={90} value={draft.memory_days}
            onChange={event => patch({ memory_days: Number(event.target.value) })}/></label>
            <small>От последнего сообщения в диалоге.</small></>}
        </div>
      </div>
      <label className="noderaInstructions">Дополнительные инструкции<textarea value={draft.assistant_instructions} maxLength={4000} rows={5}
        onChange={event => patch({ assistant_instructions: event.target.value })}/></label>
    </fieldset>
  </section>;
  return <div className="noderaBlocks">{panel === "settings" ? <>{connections}{modules}{behavior}</> : <>{telegram}{users}</>}</div>;
}

function SettingsDialog({ title, close, submit, blocked, children }: {
  title: string; close: () => void; submit?: () => void; blocked?: boolean; children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className="noderaDialog" aria-labelledby={id} onClose={close}>
    <form onSubmit={event => { event.preventDefault(); if (!blocked) submit?.(); }}>
      <header><h2 id={id}>{title}</h2><button type="button" className="mcpIconButton" aria-label="Закрыть" onClick={close}><ChatIcon name="close"/></button></header>
      <div className="noderaDialogFields">{children}</div>
      <footer>{submit && <button type="submit" className="primaryButton" disabled={blocked}>Применить</button>}
        <button type="button" className="ghostButton" onClick={close}>{submit ? "Отмена" : "Готово"}</button></footer>
    </form>
  </dialog>;
}

function Secret({ label, configured, value, onChange, compact }: { label: string; configured: boolean; value: string | null; onChange: (value: string | null) => void; compact?: boolean }) {
  const id = useId();
  return <div className="mcpSecretField"><label htmlFor={id}>{label}</label>
    <div className="mcpSecret"><input id={id} type="password" autoComplete="new-password" value={value ?? ""}
      placeholder={value === "" ? "Будет удалён" : configured && value === null ? "Сохранён" : "Не задан"} onChange={event => onChange(event.target.value || null)}/>
      <button type="button" className={compact ? "mcpIconButton" : "ghostButton"} aria-label={`${value === "" ? "Вернуть сохранённый" : "Очистить"} ${label}`}
        disabled={compact && value === null && !configured}
        title={value === "" ? "Вернуть сохранённый токен" : "Очистить"}
        onClick={() => onChange(value === "" || compact && !configured ? null : "")}>{compact ? <ChatIcon name={value === "" ? "refresh" : "close"}/> : value === "" ? "Вернуть" : "Очистить"}</button>
    </div>
  </div>;
}
