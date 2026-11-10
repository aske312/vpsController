import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { createApiClient } from "../../shared/lib/api-request";
import { ChatIcon } from "./chat-icon";
import { moduleState, type Draft, type McpId, type McpStatus, type ModuleId, type ProviderDraft } from "./assistant-state";

export const panels = [
  { id: "chat", label: "Чат", icon: "chat" },
  { id: "provider", label: "Подключения", icon: "settings" },
  { id: "telegram", label: "Telegram", icon: "telegram" },
  { id: "modules", label: "MCP", icon: "module" },
  { id: "access", label: "Доступ", icon: "users" },
  { id: "behavior", label: "Поведение", icon: "person" },
] as const;
export type Panel = typeof panels[number]["id"];
type AccountDraft = Draft["accounts"][number];
type Check = { ok: boolean; text: string; revision: string; actor: string };
type Quota = { unlimited: boolean; remaining: number; account_remaining?: number };
type Props = {
  panel: Exclude<Panel, "chat">;
  request: ReturnType<typeof createApiClient>;
  status: McpStatus;
  draft: Draft;
  patch: (update: Partial<Draft>) => void;
  blocked: boolean;
  dirty: boolean;
  working: string;
  actor: string;
  actorSelect: ReactNode;
  checks: Partial<Record<ModuleId, Check>>;
  check: (id: ModuleId) => Promise<void>;
  runAction: (action: "start" | "stop" | "restart") => Promise<void>;
};

const moduleDescriptions: Record<McpId, string> = {
  "telegram-mcp": "Диалоги, история и поиск.",
  "vps-controller-mcp": "Метрики, службы и компоненты.",
};
const blankAccount = (): AccountDraft => ({
  telegram_id: 0, name: "", bot_access: false, bot_chat_access: false, address_as: "",
  telegram_access: false, vps_access: false, excluded_dialogs: [],
  excluded_dialogs_text: "", session: null, session_configured: false,
});

export function NoderaSettings({ panel, request, status, draft, patch, blocked, dirty, actor, actorSelect, checks, check, runAction, working }: Props) {
  const [providerEdit, setProviderEdit] = useState<ProviderDraft>();
  const [providerChecks, setProviderChecks] = useState<Record<string, { text: string; failed?: boolean; quota?: Quota | null }>>({});
  const [checkingProvider, setCheckingProvider] = useState<string>();
  const [moduleEdit, setModuleEdit] = useState<McpId>();
  const [userEdit, setUserEdit] = useState<{ index: number; value: AccountDraft }>();
  const [lookup, setLookup] = useState("");
  const [lookingUp, setLookingUp] = useState(false);
  const [lookupError, setLookupError] = useState("");

  const providers = draft.providers ?? [];
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

  if (panel === "provider") return <section className="noderaSettings">
    <div className="noderaBar"><button type="button" className="ghostButton" disabled={blocked || providers.length >= 12}
      onClick={() => setProviderEdit({ id: Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join(""), name: "", provider_url: "", api_key: null, api_key_configured: false, model: "", model_mode: "catalog" })}>
      <ChatIcon name="plus"/>Добавить API
    </button></div>
    {!providers.length && <p className="mcpFieldHint">Нет подключений</p>}
    <div className="noderaProviders">
      {providers.map(provider => {
        const usage = status.provider_usage?.[provider.id];
        const active = draft.active_provider === provider.id;
        const probeResult = providerChecks[provider.id];
        return <article key={provider.id} className="noderaProvider">
          <header><div><strong>{provider.name}</strong><small>{provider.provider_url}</small></div>
            <span className="mcpBadge">{active ? "Активно" : "Сохранено"}</span>
            <button type="button" className="mcpIconButton" aria-label={`Настроить ${provider.name}`} title="Настроить" disabled={blocked} onClick={() => setProviderEdit(provider)}><ChatIcon name="gear"/></button>
          </header>
          <dl><div><dt>Режим</dt><dd>{provider.model_mode === "catalog" ? "Каталог" : provider.model || "—"}</dd></div>
            <div><dt>Запросы Nodera</dt><dd>{usage?.requests ?? 0}</dd></div>
            <div><dt>Токены · вход / выход</dt><dd title={usage ? `Отчёт провайдера для ${usage.reported_requests} из ${usage.requests} запросов` : undefined}>{usage?.reported_requests ? `${usage.prompt_tokens.toLocaleString("ru")} / ${usage.completion_tokens.toLocaleString("ru")}` : "—"}</dd></div>
            {probeResult?.quota && <div><dt>Остаток квоты ключа</dt><dd>{probeResult.quota.unlimited ? "Без лимита" : probeResult.quota.remaining.toLocaleString("ru")}</dd></div>}
            {probeResult?.quota?.account_remaining !== undefined && <div><dt>Квота аккаунта</dt><dd>{probeResult.quota.account_remaining.toLocaleString("ru")}</dd></div>}
          </dl>
          <footer>
            {!active && <button type="button" className="ghostButton" disabled={blocked} onClick={() => patch({ active_provider: provider.id })}>Сделать активным</button>}
            <button type="button" className="ghostButton" disabled={blocked || !!checkingProvider || !(provider.api_key ?? provider.api_key_configured)}
              onClick={() => void probe(provider)}>{checkingProvider === provider.id ? "Проверяем…" : "Проверить API"}</button>
            {probeResult?.text && <small className={probeResult.failed ? "mcpCheck error" : "mcpCheck"} role="status">{probeResult.text}</small>}
          </footer>
        </article>;
      })}
    </div>
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

  if (panel === "telegram") return <section className="noderaSettings">
    <fieldset className="noderaTelegram" disabled={botLocked}>
      <div className="noderaBar"><span className="mcpBadge">{moduleState(status, "nodera-bot")}</span>
        {status.bot.active ? <>
          <button type="button" className="ghostButton" onClick={() => void runAction("restart")}>Перезапустить</button>
          <button type="button" className="ghostButton" onClick={() => void runAction("stop")}>Остановить</button>
        </> : <button type="button" className="ghostButton" disabled={dirty || !bot?.configured || !bot.enabled || status.legacy_bot.active === true}
          onClick={() => void runAction("start")}>Запустить</button>}
      </div>
      <fieldset disabled={botCredentialsLocked} className="noderaCredentials">
        <label className="mcpToggle"><input type="checkbox" checked={draft.modules["nodera-bot"]} onChange={event => patch({ modules: { ...draft.modules, "nodera-bot": event.target.checked } })}/>Бот включён</label>
        <Secret label="Токен бота" configured={draft.bot_token_configured} value={draft.bot_token} onChange={bot_token => patch({ bot_token })}/>
      </fieldset>
      {status.bot.active && <small>Для смены токена остановите бота.</small>}
      {status.legacy_bot.active && <p className="mcpNotice">Для запуска остановите прежнего Telegram-бота.</p>}
      <div className="noderaBar"><button type="button" className="ghostButton" disabled={!(draft.bot_token ?? draft.bot_token_configured)}
        onClick={() => void check("nodera-bot")}>{working === "nodera-bot" ? "Проверяем…" : "Проверить токен"}</button>{checkResult("nodera-bot")}</div>
    </fieldset>
  </section>;

  if (panel === "modules") return <section className="noderaSettings">
    <fieldset disabled={blocked} className="mcpSkills">
      {[true, false].map(installed => {
        const entries = status.mcp_modules.filter(item => (draft.mcp_installed?.includes(item.id) ?? false) === installed);
        return entries.length > 0 && <section className="mcpModuleGroup" key={String(installed)}>
          <h3>{installed ? "Подключённые" : "Каталог"}</h3>
          <div className="mcpModuleGrid">{entries.map(item => <article key={item.id} className={`mcpModule ${installed ? "isInstalled" : ""}`}>
            <header><div className="mcpModuleIdentity"><span className="mcpModuleIcon"><ChatIcon name="module"/></span><strong>{item.name}</strong></div>
              {installed && <button type="button" className="mcpIconButton" aria-label={`Настроить ${item.name}`} title="Настроить" onClick={() => setModuleEdit(item.id)}><ChatIcon name="gear"/></button>}
            </header>
            <p>{item.description ?? moduleDescriptions[item.id]}</p>
            <footer><span>{status.tools[item.id]?.length ?? 0} инстр.</span>{installed
              ? <label className="mcpToggle"><input type="checkbox" aria-label={`Включить ${item.name}`} checked={draft.mcp[item.id]}
                onChange={event => patch({ mcp: { ...draft.mcp, [item.id]: event.target.checked } })}/>Вкл.</label>
              : <button type="button" className="ghostButton" onClick={() => patch({ mcp_installed: [...(draft.mcp_installed ?? []), item.id] })}>Добавить</button>}
            </footer>
          </article>)}</div>
        </section>;
      })}
    </fieldset>
    {moduleEdit && <SettingsDialog title={status.mcp_modules.find(item => item.id === moduleEdit)?.name ?? "MCP"} close={() => setModuleEdit(undefined)}>
      {moduleEdit === "telegram-mcp" && <>
        <label>Telegram API ID<input type="number" min={1} value={draft.telegram_api_id ?? ""}
          onChange={event => patch({ telegram_api_id: event.target.value ? Number(event.target.value) : null })}/></label>
        <Secret label="Telegram API hash" configured={draft.telegram_api_hash_configured} value={draft.telegram_api_hash} onChange={telegram_api_hash => patch({ telegram_api_hash })}/>
        {actorSelect}
      </>}
      <small>{status.tools[moduleEdit]?.join(" · ")}</small>
      <button type="button" className="ghostButton" disabled={blocked || dirty || !status.mcp_modules.some(item => item.id === moduleEdit && item.enabled && item.configured) || moduleEdit === "telegram-mcp" && !actor}
        onClick={() => void check(moduleEdit)}>Проверить модуль</button>
      {checkResult(moduleEdit)}
      <button type="button" className="ghostButton" disabled={blocked} onClick={() => {
        patch({ mcp_installed: draft.mcp_installed?.filter(id => id !== moduleEdit), mcp: { ...draft.mcp, [moduleEdit]: false } });
        setModuleEdit(undefined);
      }}>Удалить модуль</button>
    </SettingsDialog>}
  </section>;

  if (panel === "access") return <section className="noderaSettings">
    <div className="noderaBar"><button type="button" className="ghostButton" disabled={blocked || draft.accounts.length >= 30} onClick={() => {
      setLookup(""); setLookupError(""); setUserEdit({ index: -1, value: blankAccount() });
    }}><ChatIcon name="plus"/>Добавить пользователя</button></div>
    <div className="noderaTableScroll"><table className="noderaAccessTable">
      <thead><tr><th>Пользователь</th><th className="noderaIdColumn">Telegram ID</th><th>Роль бота</th><th>MCP</th><th aria-label="Действия"/></tr></thead>
      <tbody>
        <tr><th scope="row">Администратор панели</th><td className="noderaIdColumn">—</td><td>—</td><td>VPS</td><td>—</td></tr>
        {draft.accounts.map((item, index) => <tr key={item.telegram_id}>
          <th scope="row">{item.name || item.telegram_id}{item.name && <small className="noderaInlineId">{item.telegram_id}</small>}{!item.bot_access && item.address_as && <small>{item.address_as}</small>}</th>
          <td className="noderaIdColumn">{item.telegram_id}</td><td>{item.bot_access ? "Администратор" : item.bot_chat_access ? "Чат" : "Гость"}</td>
          <td>{[item.telegram_access && "Telegram", (item.vps_access || item.bot_access && draft.modules["nodera-bot"]) && "VPS"].filter(Boolean).join(", ") || "—"}</td>
          <td><button type="button" className="mcpIconButton" aria-label={`Настроить пользователя ${item.name || item.telegram_id}`} disabled={blocked}
            onClick={() => setUserEdit({ index, value: item })}><ChatIcon name="gear"/></button></td>
        </tr>)}
      </tbody>
    </table></div>
    {userEdit && account && <SettingsDialog title={userEdit.index < 0 ? "Добавить пользователя" : "Пользователь"} close={() => setUserEdit(undefined)}
      blocked={blocked || !account.telegram_id || draft.accounts.some((item, index) => item.telegram_id === account.telegram_id && index !== userEdit.index)}
      submit={() => {
        patch({ accounts: userEdit.index < 0 ? [...draft.accounts, account] : draft.accounts.map((item, index) => index === userEdit.index ? account : item) });
        setUserEdit(undefined);
      }}>
      {userEdit.index < 0 && bot?.installed && status.settings.bot_token_configured && <div className="noderaBar">
        <label>Найти в Telegram<input value={lookup} placeholder="@username" maxLength={65} onChange={event => setLookup(event.target.value)}/></label>
        <button type="button" className="ghostButton" disabled={blocked || lookingUp || !lookup.trim()} onClick={() => void findUser()}>Найти</button>
        {lookupError && <small role="alert">{lookupError}</small>}
      </div>}
      <label>Telegram ID<input type="number" min={1} max={2 ** 52} required readOnly={userEdit.index >= 0} value={account.telegram_id || ""} onChange={event => editAccount({ telegram_id: Number(event.target.value) })}/></label>
      <label>Имя в таблице<input value={account.name} maxLength={80} onChange={event => editAccount({ name: event.target.value })}/></label>
      <label>Роль в Telegram<select value={account.bot_access ? "admin" : account.bot_chat_access ? "chat" : "guest"}
        onChange={event => editAccount({ bot_access: event.target.value === "admin", bot_chat_access: event.target.value === "chat", ...(event.target.value === "admin" ? { address_as: "" } : {}) })}>
        <option value="guest">Гость</option><option value="chat">Чат с Нодерой</option><option value="admin">Администратор бота</option>
      </select></label>
      {!account.bot_access && <label>Как Нодера обращается к пользователю<input value={account.address_as ?? ""} maxLength={80} onChange={event => editAccount({ address_as: event.target.value })}/></label>}
      <div className="noderaBar">
        <label className="mcpToggle"><input type="checkbox" checked={account.telegram_access} onChange={event => editAccount({ telegram_access: event.target.checked })}/>Telegram MCP</label>
        <label className="mcpToggle"><input type="checkbox" checked={account.vps_access || account.bot_access && draft.modules["nodera-bot"]} disabled={account.bot_access && draft.modules["nodera-bot"]} onChange={event => editAccount({ vps_access: event.target.checked })}/>VPS MCP</label>
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

  return <section className="noderaSettings">
    <fieldset className="mcpBehavior" disabled={blocked}>
      <div className="mcpBehaviorFields">
        <label>Манера общения<select value={draft.assistant_style} onChange={event => patch({ assistant_style: event.target.value as Draft["assistant_style"] })}>
          <option value="balanced">Спокойно и по делу</option><option value="concise">Кратко</option><option value="friendly">Дружелюбно</option><option value="technical">Технически подробно</option>
        </select></label>
        <label className="mcpToggle"><input type="checkbox" checked={draft.memory_enabled} onChange={event => patch({ memory_enabled: event.target.checked })}/>Сохранять диалоги</label>
        <label>Срок хранения, дни<input type="number" min={1} max={90} disabled={!draft.memory_enabled} value={draft.memory_days}
          onChange={event => patch({ memory_days: Number(event.target.value) })}/></label>
      </div>
      {draft.memory_enabled && <small>Срок отсчитывается от последнего сообщения в диалоге.</small>}
      <label>Дополнительные инструкции<textarea value={draft.assistant_instructions} maxLength={4000} rows={4}
        onChange={event => patch({ assistant_instructions: event.target.value })}/></label>
    </fieldset>
  </section>;
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

function Secret({ label, configured, value, onChange }: { label: string; configured: boolean; value: string | null; onChange: (value: string | null) => void }) {
  const id = useId();
  return <div className="mcpSecretField"><label htmlFor={id}>{label}</label>
    <div className="mcpSecret"><input id={id} type="password" autoComplete="new-password" value={value ?? ""}
      placeholder={value === "" ? "Будет удалён" : configured && value === null ? "Сохранён" : "Не задан"} onChange={event => onChange(event.target.value || null)}/>
      <button type="button" className="ghostButton" aria-label={`${value === "" ? "Вернуть сохранённый" : "Очистить"} ${label}`}
        onClick={() => onChange(value === "" ? null : "")}>{value === "" ? "Вернуть" : "Очистить"}</button>
    </div>
  </div>;
}
