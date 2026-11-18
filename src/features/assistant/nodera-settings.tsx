import { useNotifier } from "../../shared/notifications/notification-center";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { createApiClient } from "../../shared/lib/api-request";
import { ChatIcon } from "./chat-icon";
import { TelegramAlerts } from "./telegram-alerts";
import { enabledTelegramTools } from "./assistant-state";
import { type ProviderConnection } from "./provider-connection";
import { dollars, latestObservation, quotaFacts, quotaText, queueProviderCatalog, type ProviderObservation, type ProviderCatalog } from "./provider-details";
import { accountProducts, productNames, type Product, fixedRoles, userPermissions, permissionGroups, accountPermissions, accountRole, permissionNames, roleDefaults, roleNames, type Role, type Permission, moduleState, type Draft, type McpStatus, type ModuleId, type ProviderDraft } from "./assistant-state";

export const panels = [
  { id: "chat", label: "Чат", icon: "chat" },
  { id: "settings", label: "Настройки AI", icon: "gear" },
  { id: "users", label: "Доступ", icon: "users" },
] as const;
export type Panel = typeof panels[number]["id"];
type AccountDraft = Draft["accounts"][number];
type Check = { ok: boolean; text: string; revision: string; actor: string; username?: string };
type ProviderCheck = ProviderObservation & { text: string; failed?: boolean; checking?: boolean };
type BotContact = { telegram_id: number; username: string; name: string; seen_at: number; profile_name?: string; blocked?: boolean; last_name?: string; language_code?: string; is_premium?: boolean | null; bio?: string; birthdate?: { day: number; month: number; year?: number | null } | null };
type Props = {
  apiConnection?: ProviderConnection;
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
};

const blankAccount = (): AccountDraft => ({
  role: "guest", products: [], permission_overrides: {}, telegram_id: 0, name: "", bot_access: false, bot_chat_access: false, address_as: "",
  telegram_access: false, vps_access: false, excluded_dialogs: [],
  excluded_dialogs_text: "", session: null, session_configured: false,
});

function PermissionGroupNav({ selected, select }: { selected: string; select: (value: string) => void }) {
  return <nav className="noderaPermissionNav" aria-label="Группы разрешений">{permissionGroups.map(group => <button type="button" key={group.title} aria-pressed={selected === group.title} onClick={() => select(group.title)}>{group.title}</button>)}</nav>;
}

export function NoderaSettings({ apiConnection, panel, request, status, draft, patch, blocked, dirty, refreshKey, actor, actorSelect, checks, check, working }: Props) {
  const notify = useNotifier("nodera", "Nodera");
  const [sitesText, setSitesText] = useState((draft.web_domains ?? []).join(", "));
  useEffect(() => {
    setSitesText(previous => JSON.stringify(previous.split(",").map(value => value.trim()).filter(Boolean)) === JSON.stringify(draft.web_domains ?? []) ? previous : (draft.web_domains ?? []).join(", "));
  }, [draft.web_domains]);
  const [internetOpen, setInternetOpen] = useState(false);
  const [permanent, setPermanent] = useState(true);
  const [personalTab, setPersonalTab] = useState<"character" | "behavior" | "memory">("character");
  const [roleEdit, setRoleEdit] = useState<Role>("user");
  const [rolePolicy, setRolePolicy] = useState<Draft["role_permissions"]>();
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [roleFilter, setRoleFilter] = useState<"all" | "enabled" | "disabled">("all");
  const [permissionSearch, setPermissionSearch] = useState("");
  const [roleGroup, setRoleGroup] = useState(permissionGroups[0].title);
  const [userPermissionGroup, setUserPermissionGroup] = useState(permissionGroups[0].title);
  const [userSection, setUserSection] = useState("profile");
  const [factLimit, setFactLimit] = useState(8);
  const [contextOpen, setContextOpen] = useState(false);
  const [contextActor, setContextActor] = useState("");
  const [contextData, setContextData] = useState<{ facts: {id: string; text: string; permanent?: boolean}[]; recent_topics: string[]; cache?: { used_bytes: number; limit_bytes: number } }>();
  const [contextText, setContextText] = useState("");
  const [contextBusy, setContextBusy] = useState(false);
  const [contextError, setContextError] = useState("");
  const contextGeneration = useRef(0);
  useEffect(() => {
    if (!contextOpen) return;
    const generation = ++contextGeneration.current;
    setContextData(undefined); setContextText(""); setFactLimit(8); setContextError("");
    void request<{facts: {id: string; text: string; permanent?: boolean}[]; recent_topics: string[]; cache?: { used_bytes: number; limit_bytes: number }}>(`/assistant/context${contextActor ? `?telegram_id=${contextActor}` : ""}`).then(
      result => { if (generation === contextGeneration.current) setContextData(result); },
      error => { if (generation === contextGeneration.current) setContextError(error instanceof Error ? error.message : "Контекст недоступен"); },
    );
    return () => { contextGeneration.current = generation + 1; };
  }, [contextOpen, contextActor, request]);
  async function changeContext(operation: "remember" | "forget", identity = "all") {
    if (contextBusy) return;
    const generation = contextGeneration.current;
    setContextBusy(true);
    try {
      const result = await request<{facts: {id: string; text: string; permanent?: boolean}[]; recent_topics: string[]; cache?: { used_bytes: number; limit_bytes: number }}>(`/assistant/context${contextActor ? `?telegram_id=${contextActor}` : ""}`,
        { method: "POST", body: JSON.stringify({ operation, identity, text: contextText, permanent }) });
      if (generation === contextGeneration.current) { setContextData(result); setContextText(""); }
      notify.success(operation === "remember" ? "Предпочтение сохранено" : "Контекст удалён");
    } catch (error) { notify.error(error instanceof Error ? error.message : "Контекст не изменён"); }
    finally { setContextBusy(false); }
  }
  const [providerEdit, setProviderEdit] = useState<ProviderDraft>();
  const [providerChecks, setProviderChecks] = useState<Record<string, ProviderCheck>>({});
  const providerChecksRef = useRef(providerChecks);
  providerChecksRef.current = providerChecks;
  const [providerModels, setProviderModels] = useState<Record<string, string[]>>({});
  const [checkingProvider, setCheckingProvider] = useState<string>();
  const [telegramMcpOpen, setTelegramMcpOpen] = useState(false);
  const [userEdit, setUserEdit] = useState<{ index: number; value: AccountDraft; original?: AccountDraft; observed?: boolean }>();
  const [lookup, setLookup] = useState("");
  const [lookingUp, setLookingUp] = useState(false);
  const [lookupError, setLookupError] = useState("");
  const [contacts, setContacts] = useState<{ revision: string; rows: BotContact[]; error?: string }>();
  const [contactsLimit, setContactsLimit] = useState(20);
  const [profileEdit, setProfileEdit] = useState<{ contact: BotContact; name: string; birthday: string; initialName: string; initialBirthday: string }>();
  const [profileBusy, setProfileBusy] = useState(false);
  const profileWriting = useRef(false);
  const [profileError, setProfileError] = useState("");
  const [deleteProfile, setDeleteProfile] = useState(false);
  const [subscriptions, setSubscriptions] = useState<{ items: {id: string; name: string}[]; error?: string }>({ items: [] });

  const providers = draft.providers ?? [];
  const providersRef = useRef(providers);
  providersRef.current = providers;
  useEffect(() => {
    if (panel !== "settings" || blocked || dirty) return;
    const controller = new AbortController();
    const saved = status.settings.providers ?? [];
    async function collect() {
      for (const provider of saved) {
        if (controller.signal.aborted) return;
        if (!provider.api_key_configured) continue;
        const cached = latestObservation(status.provider_observations?.[provider.id], providerChecksRef.current[provider.id]);
        if (cached?.quota_checked_at && Date.now() / 1000 - cached.quota_checked_at < 60) continue;
        if (provider.id === status.settings.active_provider) continue; // The existing monitor refreshes the active connection.
        setProviderChecks(current => ({ ...current, [provider.id]: { ...current[provider.id], text: "Проверка API…", checking: true } }));
        try {
          const result = await queueProviderCatalog(`${status.settings.revision}:${provider.id}`, () => request<ProviderCatalog>("/assistant/models", { method: "POST", signal: controller.signal,
            body: JSON.stringify({ provider_url: provider.provider_url, provider_id: provider.id, api_key: null, include_quota: true }) }), controller.signal);
          if (controller.signal.aborted) return;
          setProviderModels(current => ({ ...current, [provider.id]: result.models }));
          setProviderChecks(current => ({ ...current, [provider.id]: { ...result, text: `Каталог доступен · моделей ${result.models.length}` } }));
        } catch (cause) {
          if (controller.signal.aborted) return;
          setProviderChecks(current => ({ ...current, [provider.id]: { ...current[provider.id], text: cause instanceof Error ? cause.message : "Проверка недоступна", failed: true, checking: false } }));
        }
      }
    }
    void collect();
    return () => controller.abort();
  }, [panel, blocked, dirty, request, status.settings.revision, status.settings.providers, status.settings.active_provider, status.provider_observations]);
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
  useEffect(() => {
    if (panel !== "users") return;
    let current = true;
    void request<{items: {id: string; name: string}[]}>("/assistant/mihomo-profiles", { cache: "no-store" }).then(
      value => { if (current) setSubscriptions({ items: value.items ?? [] }); },
      () => { if (current) setSubscriptions({ items: [], error: "Подписки Mihomo недоступны. Проверьте компонент." }); },
    );
    return () => { current = false; };
  }, [panel, request, refreshKey]);
  const recent = contactsEnabled && contacts?.revision === revision ? contacts.rows : [];
  const contactsById = new Map(recent.map(item => [item.telegram_id, item]));
  const assignedIds = new Set(draft.accounts.map(item => item.telegram_id));
  const guests = recent.filter(item => !assignedIds.has(item.telegram_id));
  const bot = status.modules.find(item => item.id === "nodera-bot");
  const botLocked = blocked || !bot?.installed || bot.management?.state !== "managed";
  const botCredentialsLocked = botLocked;
  const account = userEdit?.value;
  const accountGrants = account ? accountPermissions(draft, account) : new Set<Permission>();

  function openProfile(contact: BotContact) {
    const date = contact.birthdate;
    const name = contact.profile_name ?? "";
    const birthday = date ? `${String(date.day).padStart(2, "0")}.${String(date.month).padStart(2, "0")}${date.year ? `.${date.year}` : ""}` : "";
    setProfileEdit({ contact, name, birthday, initialName: name, initialBirthday: birthday });
    setProfileError(""); setDeleteProfile(false);
  }
  function openUser(value: AccountDraft, index: number, contact?: BotContact) {
    setUserEdit({ index, value, original: value, observed: index < 0 && !!contact });
    setUserSection("profile"); setPermissionSearch(""); setLookup(""); setLookupError(""); setProfileError(""); setDeleteProfile(false);
    if (contact || index >= 0) openProfile(contact ?? {telegram_id:value.telegram_id, name:value.name, username:"", seen_at:0});
    else setProfileEdit(undefined);
  }
  function closeUser() {
    if (profileWriting.current) return;
    setUserEdit(undefined); setProfileEdit(undefined); setUserSection("profile"); setPermissionSearch("");
  }
  async function applyUser() {
    if (!userEdit || profileWriting.current) return;
    const changedAccount = !userEdit.observed || JSON.stringify(userEdit.value) !== JSON.stringify(userEdit.original);
    if (changedAccount && userEdit.index < 0 && draft.accounts.length >= 30) { setProfileError("Можно добавить не более 30 пользователей с настройками доступа"); return; }
    if (profileEdit && (profileEdit.name.trim() !== profileEdit.initialName || profileEdit.birthday.trim() !== profileEdit.initialBirthday)) {
      if (!await changeProfile("save")) return;
    }
    if (changedAccount) patch({ accounts: userEdit.index < 0 ? [...draft.accounts, userEdit.value] : draft.accounts.map((item, index) => index === userEdit.index ? userEdit.value : item) });
    closeUser();
  }
  async function changeProfile(action: "save" | "block" | "delete") {
    if (!profileEdit || profileWriting.current || blocked) return;
    let body: Record<string, unknown> = {};
    if (action === "save") {
      const match = /^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$/.exec(profileEdit.birthday.trim());
      if (profileEdit.birthday.trim() && !match) { setProfileError("Дата рождения: ДД.ММ.ГГГГ или ДД.ММ без года"); return; }
      body = { display_name: profileEdit.name.trim(), birthdate: match ? { day: Number(match[1]), month: Number(match[2]), year: match[3] ? Number(match[3]) : null } : null };
    } else if (action === "block") body = { blocked: !profileEdit.contact.blocked };
    profileWriting.current = true; setProfileBusy(true); setProfileError("");
    try {
      const result = await request<BotContact>(`/assistant/bot-users/${profileEdit.contact.telegram_id}`, { method: action === "delete" ? "DELETE" : "PATCH", ...(action === "delete" ? {} : { body: JSON.stringify(body) }) });
      setContacts(current => current && { ...current, rows: action === "delete" ? current.rows.filter(row => row.telegram_id !== profileEdit.contact.telegram_id) : [...current.rows.filter(row => row.telegram_id !== result.telegram_id), result] });
      if (action === "delete") {
        if (draft.accounts.some(row => row.telegram_id === profileEdit.contact.telegram_id)) patch({ accounts: draft.accounts.filter(row => row.telegram_id !== profileEdit.contact.telegram_id) });
        setProfileEdit(undefined); setUserEdit(undefined);
      } else if (action === "block") setProfileEdit(current => current && { ...current, contact: result });
      notify.success(action === "delete" ? "Профиль пользователя удалён" : action === "block" ? result.blocked ? "Пользователь заблокирован" : "Пользователь разблокирован" : "Профиль сохранён");
      return true;
    } catch (cause) { setProfileError(cause instanceof Error ? cause.message : "Профиль не изменён"); return false; }
    finally { profileWriting.current = false; setProfileBusy(false); }
  }

  function editAccount(value: Partial<AccountDraft>) {
    if (profileWriting.current) return;
    setUserEdit(current => current && { ...current, value: { ...current.value, ...value } });
  }

  function storeProvider(provider: ProviderDraft) {
    const exists = providers.some(item => item.id === provider.id);
    patch({ providers: exists ? providers.map(item => item.id === provider.id ? provider : item) : [...providers, provider],
      active_provider: draft.active_provider ?? provider.id });
    setProviderChecks(current => ({ ...current, [provider.id]: { text: "" } }));
    setProviderModels(current => ({ ...current, [provider.id]: [] }));
    setProviderEdit(undefined);
  }

  async function probe(provider: ProviderDraft) {
    setCheckingProvider(provider.id);
    try {
      const result = await queueProviderCatalog(provider.api_key === null ? `${status.settings.revision}:${provider.id}` : `draft:${provider.id}:${Date.now()}`, () => request<ProviderCatalog>("/assistant/models", {
        method: "POST", body: JSON.stringify({ provider_url: provider.provider_url, api_key: provider.api_key, provider_id: provider.id, include_quota: true }),
      }));
      const current = providersRef.current.find(row => row.id === provider.id);
      if (!current || current.provider_url !== provider.provider_url || current.api_key !== provider.api_key) return;
      setProviderModels(current => ({ ...current, [provider.id]: result.models }));
      notify.success(`Подключение проверено · моделей ${result.models.length}`);
      const available = provider.model_mode === "catalog" || result.models.includes(provider.model);
      setProviderChecks(current => ({ ...current, [provider.id]: available
        ? { ...result, text: `Каталог доступен · моделей ${result.models.length}` }
        : { ...result, text: "Модель отсутствует в каталоге", failed: true } }));
    } catch (cause) {
      const current = providersRef.current.find(row => row.id === provider.id);
      if (!current || current.provider_url !== provider.provider_url || current.api_key !== provider.api_key) return;
      notify.error(cause instanceof Error ? cause.message : "Проверка не выполнена");
      setProviderChecks(current => ({ ...current, [provider.id]: { ...current[provider.id], text: cause instanceof Error ? cause.message : "Проверка не выполнена", failed: true, checking: false } }));
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
      openUser(index >= 0 ? draft.accounts[index] : {
        ...blankAccount(), telegram_id: found.telegram_id, name: found.name || found.username,
      }, index, contactsById.get(found.telegram_id) ?? { ...found, seen_at:0 });
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
      <button type="button" className="ghostButton" disabled={blocked || providers.length >= 12}
        onClick={() => setProviderEdit({ id: Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join(""), name: "", provider_url: "", api_key: null, api_key_configured: false, model: "", model_mode: "catalog" })}>
        <ChatIcon name="plus"/>Добавить API
      </button>
      </div>
    </header>
    {!providers.length ? <p className="mcpFieldHint">Нет подключений</p> : <div className="noderaConnectionList">{providers.map(provider => {
        const usage = status.provider_usage?.[provider.id];
        const active = draft.active_provider === provider.id;
        const probeResult = providerChecks[provider.id];
        const observed = provider.id === status.settings.active_provider && provider.api_key === null && provider.provider_url === status.settings.provider_url ? apiConnection : undefined;
        const saved = status.settings.providers?.find(row => row.id === provider.id);
        const cached = provider.api_key === null && saved?.provider_url === provider.provider_url ? status.provider_observations?.[provider.id] : undefined;
        const observation = latestObservation(cached, probeResult, observed?.observation);
        const info = observation?.provider_info;
        const checkedModels = observed?.models.length ? observed.models : providerModels[provider.id];
        const modelCount = info?.models_count ?? checkedModels?.length;
        const models = [...new Set([provider.model, ...(observed?.models ?? []), ...(providerModels[provider.id] ?? [])].filter(Boolean))];
        const tokens = usage?.reported_requests ? `${usage.prompt_tokens.toLocaleString("ru")} / ${usage.completion_tokens.toLocaleString("ru")}` : "—";
        const quota = quotaText(observation?.quota);
        const requests = usage?.requests.toLocaleString("ru") ?? "—";
        const checking = checkingProvider === provider.id || probeResult?.checking || observed?.state === "checking";
        const state = checking ? "checking" : observed?.state === "error" || probeResult?.failed ? "failed" : info ? "available" : "unknown";
        const stateLabel = ({ checking: "Проверка API…", failed: "Ошибка проверки API", available: "API доступен", unknown: "API ещё не проверен" })[state];
        const checkedAt = info?.checked_at;
        return <article key={provider.id} className={`noderaConnection ${active ? "isDefaultProvider" : ""}`} aria-label={provider.name}>
          <header className="noderaConnectionHead">
            <div className="noderaConnectionIdentity"><h3>{provider.name}</h3><small>{provider.provider_url}</small></div>
            <div className="noderaConnectionActions">
              <button type="button" className="noderaTag" aria-pressed={active} disabled={blocked} onClick={() => patch({ active_provider: provider.id })}>{active ? "По умолчанию" : "Сделать основным"}</button>
              <button type="button" className="mcpIconButton" aria-label={`Настроить ${provider.name}`} title="Настроить API" disabled={blocked} onClick={() => setProviderEdit(provider)}><ChatIcon name="gear"/></button>
            </div>
          </header>
          <div className="noderaConnectionStatus" role="status" title={observed?.error || probeResult?.text || stateLabel}>
            <span className={`mcpConnectionDot ${state}`} aria-hidden="true"/>{stateLabel}
            {typeof checkedAt === "number" && <small>Проверка каталога: {new Date(checkedAt * 1000).toLocaleString("ru-RU")}</small>}
          </div>
          <div className="noderaConnectionBody">
            <div className="noderaConnectionModel"><label><span>Модель по умолчанию</span><select aria-label={`Модель по умолчанию: ${provider.name}`} value={provider.model} disabled={blocked || provider.model_mode === "single"}
            onChange={event => patch({ providers: providers.map(item => item.id === provider.id ? { ...item, model: event.target.value } : item) })}>
            <option value="">Выберите модель</option>{models.map(model => <option key={model} value={model}>{model}</option>)}
            </select></label>
              <div className="noderaConnectionCatalog"><small>{typeof modelCount === "number" ? `Моделей: ${modelCount}` : provider.model_mode === "single" ? "Одна модель" : "Каталог не загружен"}</small>
                <button type="button" className="ghostButton" aria-label={`Загрузить модели: ${provider.name}`} disabled={blocked || !!checkingProvider || !(provider.api_key ?? provider.api_key_configured)} onClick={() => void probe(provider)}><ChatIcon name="refresh"/>{checking ? "Проверка…" : "Проверить API"}</button>
              </div>
            </div>
            <dl className="noderaConnectionMetrics">
              <div><dt title="Длительность загрузки каталога; не задержка ответа AI">Отклик каталога</dt><dd>{typeof info?.catalog_latency_ms === "number" ? `${Math.round(info.catalog_latency_ms)} мс` : "—"}</dd></div>
              <div><dt title="Запросы, учтённые этой панелью">Запросы панели</dt><dd>{requests}</dd></div>
              <div><dt title="Вход / выход, учтённые этой панелью">Токены: вход / выход</dt><dd>{tokens}</dd></div>
              <div><dt>Остаток ключа{observation?.quota?.remaining_usd === undefined && observation?.quota?.unlimited !== true && typeof observation?.quota?.remaining === "number" ? " (ед.)" : ""}</dt><dd>{quota}</dd></div>
              <div><dt>Баланс аккаунта, USD</dt><dd>{dollars(observation?.quota?.balance_usd)}</dd></div>
              <div><dt>Обновление квоты</dt><dd>{typeof observation?.quota_checked_at === "number" ? new Date(observation.quota_checked_at * 1000).toLocaleString("ru-RU") : "—"}</dd></div>
            </dl>
          </div>
        </article>;
      })}</div>}

    {providerEdit && <SettingsDialog title="Подключение API" close={() => setProviderEdit(undefined)}
      submit={() => storeProvider({ ...providerEdit, name: providerEdit.name.trim() || new URL(providerEdit.provider_url).hostname })} blocked={blocked}>
      <label>Название<input value={providerEdit.name} maxLength={80} onChange={event => setProviderEdit({ ...providerEdit, name: event.target.value })}/></label>
      <label>Base URL<input type="url" required pattern="https://.+" value={providerEdit.provider_url} placeholder="https://api.nyxos.shop/v1" maxLength={512}
        onChange={event => setProviderEdit({ ...providerEdit, provider_url: event.target.value, api_key: null, api_key_configured: false })}/></label>
      <Secret label="API-ключ" configured={providerEdit.api_key_configured} value={providerEdit.api_key} onChange={api_key => setProviderEdit({ ...providerEdit, api_key })}/>
      <label>Модели<select value={providerEdit.model_mode} onChange={event => setProviderEdit({ ...providerEdit, model_mode: event.target.value as ProviderDraft["model_mode"] })}>
        <option value="catalog">Каталог API</option><option value="single">Одна модель</option></select></label>
      <label>{providerEdit.model_mode === "single" ? "Модель" : "Модель по умолчанию (ID)"}<input value={providerEdit.model} required={providerEdit.model_mode === "single"} maxLength={128} onChange={event => setProviderEdit({ ...providerEdit, model: event.target.value })}/></label>
      <details className="noderaProviderDetails"><summary>Квота и сведения провайдера</summary>{quotaFacts(latestObservation(status.provider_observations?.[providerEdit.id], providerChecks[providerEdit.id])).map(fact => <p key={fact.label} title={fact.title}>{fact.label} · {fact.value}</p>)}</details>
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
  const telegram = <section className="noderaSettings" aria-label="Telegram">
    <header className="noderaBlockHead"><h2>Telegram</h2><span className="mcpBadge">{moduleState(status, "nodera-bot")}</span></header>

    <fieldset className="noderaTelegram" disabled={botLocked}>
      <div className="noderaCredentials">
        <fieldset disabled={botCredentialsLocked} className="noderaBotToken">
          <Secret label="Токен бота" configured={draft.bot_token_configured} value={draft.bot_token} onChange={bot_token => patch({ bot_token })} compact/>
        </fieldset>
        <button type="button" className="ghostButton" disabled={!tokenPresent} onClick={() => void check("nodera-bot")}>{working === "nodera-bot" ? "Проверяем…" : "Проверить токен"}</button>
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
        const selectedAccount = draft.accounts.find(account => String(account.telegram_id) === actor);
        const telegramAllowed = selectedAccount && accountPermissions(draft, selectedAccount).has("telegram_read") && Boolean(selectedAccount.session || selectedAccount.session_configured);
        return <article key={item.id} className={`mcpModule ${enabled ? "isInstalled" : ""}`}>
          <header><div className="mcpModuleIdentity"><span className="mcpModuleIcon"><ChatIcon name={item.id === "telegram-mcp" ? "telegram" : "module"}/></span>
            <strong>{item.name}</strong></div></header>

          <p className="mcpFieldHint" aria-label={`Количество инструментов ${item.name}`}>Инструментов: {(status.tools[item.id] ?? []).length}
            {item.id === "telegram-mcp" && actor && <> · разрешено пользователю: {telegramAllowed ? enabledTelegramTools(selectedAccount).filter(name => (status.tools[item.id] ?? []).includes(name)).length : 0}</>}
          </p>
          <footer>
            <button type="button" className="ghostButton" aria-pressed={enabled} aria-label={`${item.name} · ${enabled ? "Включён" : "Выключен"}`}
              onClick={() => patch({ mcp: { ...draft.mcp, [item.id]: !enabled },
                mcp_installed: !enabled ? [...new Set([...(draft.mcp_installed ?? []), item.id])] : draft.mcp_installed })}>
              {enabled ? "Включён" : "Выключен"}
            </button>
            {item.id === "telegram-mcp" && <button type="button" className="mcpIconButton" aria-label="Настроить Telegram MCP" title="Настроить" onClick={() => setTelegramMcpOpen(true)}><ChatIcon name="gear"/></button>}
            {item.id !== "telegram-mcp" && <button type="button" className="ghostButton" disabled={blocked || dirty || !item.enabled || !item.configured} onClick={() => void check(item.id)}>Проверить подключение</button>}
          </footer>
          {checkResult(item.id)}
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
            </>}
        </div>
      </div>
      <label className="noderaInstructions">Дополнительные инструкции<textarea value={draft.assistant_instructions} maxLength={4000} rows={5}
        onChange={event => patch({ assistant_instructions: event.target.value })}/></label>
    </fieldset>
  </section>;
  const tools = <section className="noderaSettings" aria-label="Инструменты">
    <header className="noderaBlockHead"><h2>Инструменты</h2></header>
    <div className="noderaToolsList">
      {([{ id: "web", name: "Интернет", description: "Поиск и чтение источников", enabled: draft.web_enabled ?? true, available: status.web_tools, change: () => patch({ web_enabled: !draft.web_enabled }), settings: () => setInternetOpen(true) },
        { id: "memory", name: "Персонализация", description: "Характер, память и контекст собеседника", enabled: draft.personalization_enabled ?? true, available: status.context_tools, change: () => patch({ personalization_enabled: !draft.personalization_enabled }), settings: () => { setContextActor(actor); setContextOpen(true); } }]).map(tool => <div className="noderaToolRow" key={tool.id}>
        <span><strong>{tool.name}</strong>{!tool.available && <small>Обновите Nodera</small>}</span>
        <button type="button" role="switch" className="noderaAISwitch" aria-label={tool.name} aria-checked={tool.enabled} disabled={blocked || !tool.available} onClick={tool.change}><span className="noderaSwitchTrack"><span/></span></button>
        <button type="button" className="mcpIconButton" aria-label={`Настроить ${tool.name}`} title="Настроить" disabled={blocked || !tool.available || contextBusy} onClick={tool.settings}><ChatIcon name="gear"/></button>
      </div>)}
    </div>
    {internetOpen && <SettingsDialog title="Интернет" close={() => setInternetOpen(false)}>

      <label className="noderaPermissionSwitch"><span>Парсинг страниц</span><input type="checkbox" role="switch" aria-label="Парсинг страниц" checked={draft.web_extract_enabled ?? true} disabled={blocked} onChange={event => patch({ web_extract_enabled: event.target.checked })}/></label>
      <label>Источников для исследования<select value={draft.web_max_sources ?? 5} disabled={blocked} onChange={event => patch({ web_max_sources: Number(event.target.value) })}>{[1,2,3,4,5,6,7,8].map(value => <option value={value} key={value}>{value}</option>)}</select></label>
      <label>Лимит чтения страницы<select value={draft.web_read_chars ?? 20000} disabled={blocked} onChange={event => patch({ web_read_chars: Number(event.target.value) })}>{[2000,6000,14000,20000,30000,40000].map(value => <option value={value} key={value}>{value.toLocaleString("ru-RU")} символов</option>)}</select></label>
      <label>Предпочитаемые сайты<input placeholder="example.com, docs.example.org" value={sitesText} disabled={blocked} onChange={event => { setSitesText(event.target.value); patch({ web_domains: event.target.value.split(",").map(value => value.trim()).filter(Boolean) }); }}/></label>


    </SettingsDialog>}
    {contextOpen && <SettingsDialog title="Персонализация" wide close={() => setContextOpen(false)}>
      <nav className="noderaDialogTabs" aria-label="Разделы персонализации">{([{id: "character", label: "Характер"}, {id: "behavior", label: "Общение"}, {id: "memory", label: "Память пользователей"}] as const).map(tab => <button type="button" key={tab.id} className="ghostButton" aria-pressed={personalTab === tab.id} onClick={() => setPersonalTab(tab.id)}>{tab.label}</button>)}</nav>
      {personalTab === "character" && <section className="noderaPersonalSection"><h3>Персонаж Nodera</h3><label>Характер Nodera<textarea rows={8} maxLength={2000} value={draft.assistant_character ?? ""} disabled={blocked} onChange={event => patch({ assistant_character: event.target.value })}/></label></section>}
      {personalTab === "behavior" && <section className="noderaPersonalSection">{behavior}</section>}
      {personalTab === "memory" && <section className="noderaPersonalSection"><h3>Личная память собеседника</h3>
      {contextData?.cache && <p className="mcpFieldHint">Кеш персонализации: {(contextData.cache.used_bytes / 1024 / 1024).toFixed(1)} МБ / 5 ГБ. Временный контекст — {draft.memory_days ?? 30} дней; важные сведения хранятся до удаления.</p>}
      <label>Собеседник<select value={contextActor} disabled={contextBusy} onChange={event => setContextActor(event.target.value)}><option value="">Администратор панели</option>{draft.accounts.map(account => <option key={account.telegram_id} value={String(account.telegram_id)}>{account.name || account.telegram_id}</option>)}</select></label>
      {contextError && <p role="alert">{contextError}</p>}
      {!contextData && !contextError && <p role="status">Загрузка контекста…</p>}
      {contextData && <><h3>Сохранённые предпочтения</h3>{contextData.facts.length ? contextData.facts.slice(0, factLimit).map(fact => <div className="noderaContextFact" key={fact.id}><span>{fact.text}<small>{fact.permanent ? "Долгосрочно" : `${draft.memory_days ?? 30} дней`}</small></span><button type="button" className="ghostButton" disabled={contextBusy} onClick={() => void changeContext("forget", fact.id)}>Удалить</button></div>) : <p>Нет сохранённых предпочтений.</p>}
        {contextData.facts.length > factLimit && <button type="button" className="ghostButton" onClick={() => setFactLimit(value => value + 8)}>Ещё факты</button>}
        <label>Добавить предпочтение<input maxLength={300} value={contextText} disabled={contextBusy} onChange={event => setContextText(event.target.value)}/></label>
        <label className="mcpToggle"><input type="checkbox" checked={permanent} disabled={contextBusy} onChange={event => setPermanent(event.target.checked)}/>Хранить надолго</label>
        <button type="button" className="ghostButton" disabled={contextBusy || !contextText.trim()} onClick={() => void changeContext("remember")}>Запомнить</button>
        <details><summary>Недавние темы</summary>{contextData.recent_topics.length ? <ul>{contextData.recent_topics.map((topic, index) => <li key={index}>{topic}</li>)}</ul> : <p>Нет контекста из других диалогов.</p>}
        </details><button type="button" className="ghostButton" disabled={contextBusy} onClick={() => void changeContext("forget")}>Очистить персональный контекст</button></>}
      </section>}
    </SettingsDialog>}
  </section>;

  const users = <section className="noderaSettings" aria-label="Пользователи">
    <header className="noderaBlockHead"><h2>Пользователи</h2>
      <button type="button" className="ghostButton" disabled={blocked || draft.accounts.length >= 30} onClick={() => {
        openUser(blankAccount(), -1);
      }}><ChatIcon name="plus"/>Добавить пользователя</button>
    </header>
    <div className="noderaTableScroll" tabIndex={0} role="region" aria-label="Данные и права пользователей"><table className="noderaAccessTable">
      <thead><tr><th>Пользователь</th><th>Права</th><th>Подключения</th><th aria-label="Действия"/></tr></thead>
      <tbody>
        <tr><th scope="row">Панель</th><td>Владелец</td><td><div className="noderaTags"><span className="noderaTag">VPS MCP</span><span className="noderaTag">Nodera</span></div></td><td>—</td></tr>
        {draft.accounts.map((item, index) => {
          const contact = contactsById.get(item.telegram_id);
          return <tr key={item.telegram_id}>
          <th scope="row"><strong>{item.name || contact?.name || item.telegram_id}</strong><small>{contact?.username ? `@${contact.username} · ` : ""}{item.telegram_id}</small></th>
          <td>{roleNames[accountRole(item)]}{contact?.blocked && " · Заблокирован"}</td>
          <td><div className="noderaTags">{accountProducts(item).includes("assistant") && <span className="noderaTag isSelected">AI-ассистент</span>}{accountPermissions(draft, item).has("telegram_read") && <span className="noderaTag">Telegram MCP</span>}{["server_read", "server_manage", "diagnostics", "logs_read", "network_read", "security_read", "roles_manage"].some(key => accountPermissions(draft, item).has(key as Permission)) && <span className="noderaTag">VPS MCP</span>}{accountProducts(item).includes("mihomo") && <span className="noderaTag isSelected">Mihomo · {subscriptions.items.find(profile => profile.id === item.mihomo_profile_id)?.name || item.mihomo_profile_id || "не привязана"}</span>}</div></td>
          <td><button type="button" className="mcpIconButton" aria-label={`Настроить пользователя ${item.name || item.telegram_id}`} title="Настроить пользователя" disabled={blocked} onClick={() => openUser(item, index, contact)}><ChatIcon name="gear"/></button></td>
        </tr>;})}
        {guests.slice(0, contactsLimit).map(item => <tr key={item.telegram_id}>
          <th scope="row"><strong>{item.profile_name || item.name || item.telegram_id}</strong><small>{item.username ? `@${item.username} · ` : ""}{item.telegram_id}</small></th><td>Гость{item.blocked && " · Заблокирован"}</td><td>—</td>
          <td><button type="button" className="mcpIconButton" aria-label={`Настроить пользователя ${item.name || item.telegram_id}`} title="Настроить пользователя" disabled={blocked} onClick={() => openUser({ ...blankAccount(), telegram_id: item.telegram_id, name: item.name || item.username }, -1, item)}><ChatIcon name="gear"/></button></td>
        </tr>)}
      </tbody>
    </table></div>
    {subscriptions.error && <small role="status">{subscriptions.error}</small>}
    {contacts?.revision === revision && contacts.error && <small role="status">{contacts.error}</small>}
    {guests.length > contactsLimit && <button type="button" className="ghostButton noderaMoreContacts" onClick={() => setContactsLimit(value => value + 20)}>Ещё пользователи</button>}
    {userEdit && account && <SettingsDialog title={userEdit.index < 0 && !userEdit.observed ? "Добавить пользователя" : "Пользователь"} wide close={closeUser} dismissible={!profileBusy}
      blocked={blocked || profileBusy || !account.telegram_id || draft.accounts.some((item, index) => item.telegram_id === account.telegram_id && index !== userEdit.index)}
      submit={() => void applyUser()}>
      <div className="noderaUserSummary"><strong>{account.name || "Новый пользователь"}</strong><span>{account.telegram_id || "Telegram ID не указан"} · {roleNames[accountRole(account)]}</span></div>
      <div className="noderaUserEditor"><nav className="noderaUserNav" aria-label="Настройки пользователя">{[{id:"profile",label:"Профиль"},{id:"access",label:"Роль и продукты"},{id:"permissions",label:"Личные права"},{id:"telegram",label:"Telegram"},{id:"info",label:"Сведения"}].map(item => <button type="button" key={item.id} aria-pressed={userSection === item.id} onClick={() => { setUserSection(item.id); setPermissionSearch(""); }}>{item.label}</button>)}</nav><div className="noderaUserContent">
      <section className="noderaUserSection" hidden={userSection !== "profile"}>
      {userEdit.index < 0 && !userEdit.observed && bot?.installed && status.settings.bot_token_configured && <div className="noderaBar">
        <label>Найти в Telegram<input value={lookup} placeholder="@username" maxLength={65} onChange={event => setLookup(event.target.value)}/></label>
        <button type="button" className="ghostButton" disabled={blocked || lookingUp || !lookup.trim()} onClick={() => void findUser()}>Найти</button>
        {lookupError && <small role="alert">{lookupError}</small>}
      </div>}
      <label>Telegram ID<input type="number" min={1} max={2 ** 52} required readOnly={userEdit.index >= 0 || userEdit.observed} value={account.telegram_id || ""} onChange={event => editAccount({ telegram_id: Number(event.target.value) })}/></label>
      <label>Имя в таблице<input value={account.name} maxLength={80} onChange={event => editAccount({ name: event.target.value })}/></label>
      {profileEdit && <>
        <label>Имя в профиле<input value={profileEdit.name} maxLength={80} disabled={profileBusy} onChange={event => setProfileEdit({ ...profileEdit, name:event.target.value })}/></label>
        <label>Дата рождения<input aria-label="Дата рождения" value={profileEdit.birthday} maxLength={10} disabled={profileBusy} placeholder="ДД.ММ.ГГГГ или ДД.ММ" onChange={event => setProfileEdit({ ...profileEdit, birthday:event.target.value })}/></label>
        <small>Год можно не указывать. Пустое поле удаляет дату из профиля Nodera.</small>
      </>}

      </section><section className="noderaUserSection" hidden={userSection !== "access"}>
      <h3>Роль пользователя</h3>
      <div className="noderaAccountRoles">{(Object.keys(roleNames) as Role[]).map(role => <button type="button" key={role} aria-pressed={accountRole(account) === role} disabled={blocked} onClick={() => accountRole(account) !== role && editAccount({ role, products: role === "guest" ? [] : ["owner", "administrator"].includes(role) ? ["assistant", "mihomo"] : accountProducts(account).length ? accountProducts(account) : ["assistant"], permission_overrides: {} })}><strong>{roleNames[role]}</strong></button>)}</div>
      <small>При смене роли личные исключения сбрасываются. Права роли настраиваются в разделе «Роли и права».</small>
      <fieldset className="noderaProducts"><legend>Доступ к продуктам</legend>{(Object.keys(productNames) as Product[]).map(product => <label className="noderaPermissionSwitch" key={product}><span>{productNames[product]}</span><input type="checkbox" role="switch" aria-label={productNames[product]} checked={accountProducts(account).includes(product)} disabled={blocked || ["owner", "administrator", "guest"].includes(accountRole(account))} onChange={event => { const products = new Set(accountProducts(account)); if (event.target.checked) products.add(product); else products.delete(product); editAccount({ products: [...products] }); }}/></label>)}</fieldset>
      </section><section className="noderaUserSection" hidden={userSection !== "permissions"}>
      <h3>Личные права · {Object.keys(account.permission_overrides ?? {}).length} исключений</h3>
        <button type="button" className="ghostButton" disabled={blocked || fixedRoles.includes(accountRole(account))} onClick={() => editAccount({ role: accountRole(account), permission_overrides: {} })}>Использовать права роли</button>
        <label>Поиск разрешения<input type="search" value={permissionSearch} onChange={event => setPermissionSearch(event.target.value)}/></label>
        <PermissionGroupNav selected={userPermissionGroup} select={setUserPermissionGroup}/>
        {permissionGroups.filter(group => permissionSearch.trim() || group.title === userPermissionGroup).map(group => <section className="noderaPermissionGroup" key={group.title}><h3>{group.title}</h3><div className="noderaPermissionGrid">{group.items.filter(id => permissionNames[id].toLocaleLowerCase().includes(permissionSearch.trim().toLocaleLowerCase())).map(id => <div className="noderaOverrideRow" key={id}><label className="noderaPermissionSwitch"><span>{permissionNames[id]}<small>{account.permission_overrides?.[id] === undefined ? "По роли" : account.permission_overrides[id] ? "Разрешено лично" : "Запрещено лично"}</small></span><input type="checkbox" role="switch" aria-label={permissionNames[id]} checked={accountGrants.has(id)} disabled={blocked || fixedRoles.includes(accountRole(account)) || id === "roles_manage" || accountRole(account) === "user" && !userPermissions.includes(id)} onChange={event => editAccount({ role: accountRole(account), permission_overrides: { ...account.permission_overrides, [id]: event.target.checked } })}/></label><button type="button" className="mcpIconButton" aria-label={`По роли: ${permissionNames[id]}`} title="Вернуть значение роли" disabled={blocked || fixedRoles.includes(accountRole(account)) || account.permission_overrides?.[id] === undefined} onClick={() => { const permissions = { ...account.permission_overrides }; delete permissions[id]; editAccount({ permission_overrides: permissions }); }}><ChatIcon name="refresh"/></button></div>)}</div></section>)}
      </section><section className="noderaUserSection" hidden={userSection !== "profile"}>
      <label>Как Нодера обращается к пользователю<input value={account.address_as ?? ""} maxLength={80} onChange={event => editAccount({ address_as: event.target.value })}/></label>
      <label>Форма обращения<select aria-label="Форма обращения" value={account.address_gender ?? "auto"} onChange={event => editAccount({ address_gender: event.target.value as AccountDraft["address_gender"] })}>
        <option value="auto">По имени, при сомнении нейтрально</option><option value="neutral">Нейтральная</option><option value="male">Мужской род</option><option value="female">Женский род</option>
      </select></label>
      </section><section className="noderaUserSection" hidden={userSection !== "access"}>
      <label>Подписка Mihomo<select aria-label="Подписка Mihomo" value={account.mihomo_profile_id ?? ""} onChange={event => editAccount({ mihomo_profile_id: event.target.value || null })}>
        <option value="">Без привязки</option>{subscriptions.items.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
        {account.mihomo_profile_id && !subscriptions.items.some(profile => profile.id === account.mihomo_profile_id) && <option value={account.mihomo_profile_id}>{account.mihomo_profile_id} · не подтверждена</option>}
      </select></label>
      {subscriptions.error && <small role="alert">{subscriptions.error}</small>}
      </section><section className="noderaUserSection" hidden={userSection !== "info"}>
      {(() => {
        const contact = contactsById.get(account.telegram_id);
        return contact && <dl className="noderaContactFacts"><div><dt>Telegram</dt><dd>{[contact.name, contact.last_name].filter(Boolean).join(" ") || "—"}{contact.username ? ` · @${contact.username}` : ""}</dd></div>
          <div><dt>Язык / Premium</dt><dd>{contact.language_code || "Не указан"} / {contact.is_premium == null ? "Нет данных" : contact.is_premium ? "Да" : "Нет"}</dd></div>
          <div><dt>Дата рождения</dt><dd>{contact.birthdate ? `${contact.birthdate.day}.${contact.birthdate.month}${contact.birthdate.year ? `.${contact.birthdate.year}` : ""}` : "Не предоставлена Telegram"}</dd></div>
          <div><dt>Последний контакт</dt><dd>{new Date(contact.seen_at * 1000).toLocaleString("ru-RU")}</dd></div>{contact.bio && <div><dt>О себе</dt><dd>{contact.bio}</dd></div>}<div><dt>Пол</dt><dd>Telegram не предоставляет</dd></div></dl>;
      })()}
      </section><section className="noderaUserSection" hidden={userSection !== "telegram"}>
      {!accountGrants.has("telegram_read") && <p>Чтение Telegram не разрешено. Настройте продукты и права пользователя.</p>}
      {accountGrants.has("telegram_read") && <>
        <Secret label="Telegram-сессия" configured={account.session_configured} value={account.session} onChange={session => editAccount({ session })}/>
        <label>Исключённые диалоги<input value={account.excluded_dialogs_text} placeholder="ID через запятую" onChange={event => editAccount({ excluded_dialogs_text: event.target.value })}/></label>

        {(["read", "actions"] as const).map(profile => <details className="noderaPermissionOverrides" key={profile}>
          <summary>{profile === "read" ? "Чтение, поиск и вложения" : "Действия и пакетные операции"} · {(draft.telegram_tool_catalog ?? []).filter(tool => tool.profile === profile && enabledTelegramTools(account).includes(tool.name)).length} включено</summary>
          <div className="noderaPermissionGrid">{(draft.telegram_tool_catalog ?? []).filter(tool => tool.profile === profile).map(tool => <label className="noderaPermissionSwitch" key={tool.name} title={tool.name}><span>{tool.title}</span>
            <input type="checkbox" role="switch" aria-label={tool.title} disabled={blocked} checked={enabledTelegramTools(account).includes(tool.name)} onChange={event => {
              const tools = new Set(enabledTelegramTools(account));
              if (event.target.checked) tools.add(tool.name); else tools.delete(tool.name);
              editAccount({ telegram_tools: [...tools] });
            }}/></label>)}</div>
        </details>)}
        <label>Разрешённые адресаты действий<input value={account.telegram_action_targets_text ?? (account.telegram_action_targets ?? []).join(", ")} placeholder="ID, username, телефон или название новой группы через запятую" onChange={event => editAccount({ telegram_action_targets_text: event.target.value })}/></label>
        <small>Пустой список запрещает действия. Отправка, удаление и управление требуют подтверждения кнопкой или /confirm. Выбор сессии доступен только для своего аккаунта. Вложения до 20 МБ сохраняются в отдельном каталоге пользователя; пути сервера недоступны.</small>
      </>}
      </section>
      </div></div>
      {profileError && <p role="alert">{profileError}</p>}
      {profileEdit && <>
        <div className="noderaUserActions">
          <button type="button" className="ghostButton" disabled={blocked || profileBusy || dirty} onClick={() => void changeProfile("block")}>{profileEdit.contact.blocked ? "Разблокировать" : "Заблокировать"}</button>
          <button type="button" className="dangerButton" disabled={blocked || profileBusy || dirty} onClick={() => setDeleteProfile(true)}>Удалить пользователя</button>
        </div>
        {deleteProfile && <div role="alert"><p>Удалить профиль? Для запрета обращений используйте блокировку.</p><button type="button" className="dangerButton" disabled={blocked || profileBusy} onClick={() => void changeProfile("delete")}>Подтвердить удаление</button></div>}
        {dirty && <small>Сначала сохраните изменения прав доступа.</small>}
      </>}
    </SettingsDialog>}
  </section>;

  const roleGrants = new Set(fixedRoles.includes(roleEdit) ? roleDefaults[roleEdit] : rolePolicy?.[roleEdit] ?? roleDefaults[roleEdit]);
  const matchingRoleGroups = permissionGroups.filter(group => permissionSearch.trim() || group.title === roleGroup).map(group => ({
    ...group, items: group.items.filter(id => permissionNames[id].toLocaleLowerCase().includes(permissionSearch.trim().toLocaleLowerCase())
      && (roleFilter === "all" || roleGrants.has(id) === (roleFilter === "enabled"))),
  })).filter(group => group.items.length);
  const closeRoles = () => { setRolePolicy(undefined); setPermissionSearch(""); };
  const roleSettings = <section className="noderaSettings" aria-label="Роли и права"><header className="noderaBlockHead">
    <div><h2>Роли и права</h2></div>
    <button type="button" className="ghostButton" disabled={blocked} onClick={() => { setRolePolicy({ ...draft.role_permissions }); setPermissionSearch(""); setRoleFilter("all"); }}><ChatIcon name="gear"/>Настроить роли</button>
    </header>
    {rolePolicy && <SettingsDialog title="Роли и права" wide close={closeRoles} blocked={blocked} submit={() => { patch({ role_permissions: rolePolicy }); closeRoles(); }}>
      <nav className="noderaRolePicker" aria-label="Роли">{(Object.keys(roleNames) as Role[]).map(role => <button type="button" key={role} aria-label={`Роль: ${roleNames[role]}`} aria-pressed={roleEdit === role} onClick={() => { setRoleEdit(role); setPermissionSearch(""); setRoleFilter("all"); }}>
        <strong>{roleNames[role]}</strong><small>Пользователей: {draft.accounts.filter(item => accountRole(item) === role).length}</small>
      </button>)}</nav>
      <header className="noderaRoleSummary"><div><h3>{roleNames[roleEdit]}</h3>{fixedRoles.includes(roleEdit) && <span className="noderaTag">Закреплённая роль</span>}</div><span className="noderaTag">Разрешено: {roleGrants.size} из {Object.keys(permissionNames).length}</span></header>

      <div className="noderaRoleToolbar">
        <label>Поиск по всем разрешениям роли<input type="search" value={permissionSearch} onChange={event => setPermissionSearch(event.target.value)} placeholder="Название разрешения"/></label>
        <label>Показать<select value={roleFilter} onChange={event => setRoleFilter(event.target.value as typeof roleFilter)}><option value="all">Все разрешения</option><option value="enabled">Разрешённые</option><option value="disabled">Запрещённые</option></select></label>
      </div>
      <div className="noderaRolePermissions"><nav aria-label="Группы разрешений">{permissionGroups.map(group => <button type="button" key={group.title} aria-pressed={!permissionSearch.trim() && roleGroup === group.title} onClick={() => { setRoleGroup(group.title); setPermissionSearch(""); }}>
        <span>{group.title}</span><small>{group.items.filter(id => roleGrants.has(id)).length} / {group.items.length}</small>
      </button>)}</nav><div className="noderaRolePermissionList">
        {matchingRoleGroups.length ? matchingRoleGroups.map(group => <section className="noderaPermissionGroup" key={group.title}><h3>{group.title}</h3><div className="noderaPermissionGrid">{group.items.map(id => {
          const fixed = fixedRoles.includes(roleEdit) || id === "roles_manage" || roleEdit === "user" && !userPermissions.includes(id);
          return <label className="noderaPermissionSwitch" key={id} title={fixed ? "Неизменяемое право роли" : undefined}><span>{permissionNames[id]}</span>
            <input type="checkbox" role="switch" aria-label={permissionNames[id]} checked={roleGrants.has(id)} disabled={blocked || fixed} onChange={event => { const grants = new Set(rolePolicy[roleEdit] ?? roleDefaults[roleEdit]); if (event.target.checked) grants.add(id); else grants.delete(id); setRolePolicy({ ...rolePolicy, [roleEdit]: [...grants] }); }}/>
          </label>;
        })}</div></section>) : <p className="mcpFieldHint" role="status">Нет разрешений по выбранным условиям.</p>}
      </div></div>

    </SettingsDialog>}
  </section>;

  const alerts = <section className="noderaSettings"><header className="noderaBlockHead"><h2>Алерты Telegram</h2><button type="button" className="ghostButton" onClick={() => setAlertsOpen(true)}>Настроить алерты</button></header>
    {alertsOpen && <SettingsDialog title="Алерты Telegram" close={() => setAlertsOpen(false)}><TelegramAlerts request={request} refreshKey={refreshKey} blocked={blocked}/></SettingsDialog>}
  </section>;
  return <div className="noderaBlocks">{panel === "settings" ? <>{connections}{tools}{modules}</> : <>{telegram}{alerts}{roleSettings}{users}</>}</div>;
}

export function SettingsDialog({ title, close, submit, blocked, children, wide = false, submitLabel = "Применить", dismissible = true }: {
  wide?: boolean; title: string; close: () => void; submit?: () => void; blocked?: boolean; children: ReactNode; submitLabel?: string; dismissible?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className={`noderaDialog${wide ? " noderaDialogWide" : ""}`} aria-labelledby={id} onClose={close} onCancel={event => { if (!dismissible) event.preventDefault(); }}>
    <form onSubmit={event => { event.preventDefault(); if (!blocked) submit?.(); }}>
      <header><h2 id={id}>{title}</h2><button type="button" className="mcpIconButton" aria-label="Закрыть" disabled={!dismissible} onClick={close}><ChatIcon name="close"/></button></header>
      <div className="noderaDialogFields">{children}</div>
      <footer>{submit && <button type="submit" className="primaryButton" disabled={blocked}>{submitLabel}</button>}
        <button type="button" className="ghostButton" disabled={!dismissible} onClick={close}>{submit ? "Отмена" : "Готово"}</button></footer>
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
