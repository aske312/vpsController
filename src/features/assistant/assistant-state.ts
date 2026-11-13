export type ModuleId = "telegram-mcp" | "vps-controller-mcp" | "mihomo-mcp" | "nodera-bot" | "ai-agent";
export type McpId = "telegram-mcp" | "vps-controller-mcp" | "mihomo-mcp";
export type AppModuleId = "nodera-bot" | "ai-agent";
export const roleNames = { owner: "Владелец", administrator: "Администратор", moderator: "Модератор", user: "Пользователь", guest: "Гость" } as const;
export type Role = keyof typeof roleNames;
export const permissionNames = {
  chat: "Общение с AI", model_select: "Выбор модели AI", web: "Поиск в интернете", memory: "Личная память", telegram_read: "Чтение своего Telegram",
  server_read: "Просмотр сервера и служб", server_manage: "Управление службами", diagnostics: "Диагностика приложения", logs_read: "Чтение логов", network_read: "Просмотр сети", security_read: "Просмотр безопасности", terminal_read: "Терминал: только чтение",
  subscription_read: "Получение своей подписки", subscription_manage: "Редактирование своей подписки", subscription_name: "Название своей подписки", subscription_connections: "Подключения своей подписки", subscription_devices: "Устройства своей подписки", subscription_routing: "Маршрутизация своей подписки",
  subscription_quota: "Изменение квоты трафика", subscription_speed: "Изменение скорости", subscription_expiry: "Изменение срока действия", subscriptions_admin: "Доступ ко всем подпискам", subscriptions_create: "Создание подписок", subscriptions_delete: "Удаление подписок", modules_manage: "Управление модулями Mihomo", routing_manage: "Глобальная маршрутизация Mihomo", dns_manage: "Глобальные настройки DNS", recovery: "Восстановление Mihomo", roles_manage: "Управление ролями и правами",
} as const;
export type Permission = keyof typeof permissionNames;
export type Product = "assistant" | "mihomo";
export const productNames: Record<Product, string> = { assistant: "AI-ассистент", mihomo: "Подписка Mihomo" };
export const userPermissions: Permission[] = ["chat", "model_select", "web", "memory", "telegram_read", "subscription_read", "subscription_manage", "subscription_name", "subscription_connections", "subscription_devices", "subscription_routing"];
export const fixedRoles: Role[] = ["owner", "administrator", "guest"];
export const roleDefaults: Record<Role, Permission[]> = {
  owner: Object.keys(permissionNames) as Permission[], administrator: (Object.keys(permissionNames) as Permission[]).filter(key => key !== "roles_manage"),
  moderator: [...userPermissions, "server_read"], user: userPermissions, guest: [],
};
export const permissionGroups: {title: string; items: Permission[]}[] = [
  {title: "AI и общение", items: ["chat", "model_select", "web", "memory", "telegram_read"]},
  {title: "Своя подписка", items: ["subscription_read", "subscription_manage", "subscription_name", "subscription_connections", "subscription_devices", "subscription_routing"]},
  {title: "Сервис и диагностика", items: ["server_read", "server_manage", "diagnostics", "logs_read", "network_read", "security_read", "terminal_read"]},
  {title: "Администрирование Mihomo", items: ["subscription_quota", "subscription_speed", "subscription_expiry", "subscriptions_admin", "subscriptions_create", "subscriptions_delete", "modules_manage", "routing_manage", "dns_manage", "recovery"]},
  {title: "Владение", items: ["roles_manage"]},
];
function isMihomo(key: Permission) { return key.startsWith("subscription") || ["modules_manage", "routing_manage", "dns_manage", "recovery"].includes(key); }
export function accountRole(account: Account): Role { return account.role ?? (account.bot_access ? "administrator" : account.vps_access ? "moderator" : account.bot_chat_access || account.mihomo_profile_id || account.telegram_access ? "user" : "guest"); }
export function accountProducts(account: Account): Product[] {
  if (account.role === "owner" || account.role === "administrator" || !account.role && account.bot_access) return ["assistant", "mihomo"];
  if (account.role === "guest") return [];
  if (account.products != null) return account.products;
  const products: Product[] = [];
  if (account.bot_access || account.bot_chat_access || account.telegram_access || account.vps_access) products.push("assistant");
  if (account.mihomo_profile_id) products.push("mihomo");
  if (!products.length && (account.role === "user" || account.role === "moderator")) products.push("assistant");
  return products;
}
export function accountPermissions(settings: Pick<Settings, "role_permissions">, account: Account): Set<Permission> {
  if (account.role && fixedRoles.includes(account.role)) return new Set(roleDefaults[account.role]);
  let result: Set<Permission>;
  if (!account.role) {
    result = new Set<Permission>();
    if (account.bot_access) roleDefaults.administrator.filter(key => key !== "telegram_read").forEach(key => result.add(key));
    if (account.bot_access || account.bot_chat_access) ["chat", "model_select", "web", "memory"].forEach(key => result.add(key as Permission));
    if (account.vps_access) ["server_read", "server_manage", "web", "memory"].forEach(key => result.add(key as Permission));
    if (account.mihomo_profile_id) ["subscription_read", "subscription_manage", "subscription_name", "subscription_connections", "subscription_devices", "subscription_routing", "web", "memory"].forEach(key => result.add(key as Permission));
    if (account.telegram_access) result.add("telegram_read");
  } else {
    result = new Set(settings.role_permissions?.[account.role] ?? roleDefaults[account.role]);
    for (const [key, enabled] of Object.entries(account.permission_overrides ?? {})) { if (enabled) result.add(key as Permission); else result.delete(key as Permission); }
    if (account.role === "user") result = new Set([...result].filter(key => userPermissions.includes(key)));
    result.delete("roles_manage");
  }
  const products = accountProducts(account);
  return new Set([...result].filter(key => isMihomo(key) ? products.includes("mihomo") : products.includes("assistant")));
}
export type Account = {
  telegram_tools?: string[] | null; telegram_action_targets?: string[];
  products?: Product[] | null; role?: Role | null; permission_overrides?: Partial<Record<Permission, boolean>>;
  telegram_id: number; name: string; bot_access: boolean; telegram_access: boolean; vps_access: boolean;
  bot_chat_access?: boolean; address_as?: string; mihomo_profile_id?: string | null; address_gender?: "auto" | "neutral" | "male" | "female";
  excluded_dialogs: number[]; session_configured: boolean;
};
export type Provider = {
  id: string; name: string; provider_url: string; model: string;
  model_mode: "single" | "catalog"; api_key_configured: boolean;
};
export type ProviderDraft = Provider & { api_key: string | null };
export type ProviderUsage = { requests: number; prompt_tokens: number; completion_tokens: number; reported_requests: number };
export type Settings = {
  web_extract_enabled?: boolean; web_max_sources?: number; web_read_chars?: number;
  telegram_tool_catalog?: {name: string; title: string; profile: "read" | "actions"}[];
  role_policy_version?: 2; role_permissions?: Partial<Record<Role, Permission[]>>;
  revision: string; modules: Record<AppModuleId, boolean>; mcp: Record<McpId, boolean>; provider_url: string; model: string;
  model_mode?: "single" | "catalog";
  providers?: Provider[]; active_provider?: string | null;
  mcp_installed?: McpId[];
  assistant_style?: 'balanced' | 'concise' | 'friendly' | 'technical';
  assistant_character?: string; assistant_instructions?: string; personalization_enabled?: boolean; web_domains?: string[]; web_enabled?: boolean; memory_enabled?: boolean; memory_days?: number;
  telegram_api_id: number | null; accounts: Account[];
  api_key_configured: boolean; bot_token_configured: boolean; telegram_api_hash_configured: boolean;
};
export type Draft = Omit<Settings, "revision" | "accounts" | "providers"> & {
  expected_revision: string; api_key: string | null; bot_token: string | null; telegram_api_hash: string | null;
  accounts: (Account & { session: string | null; excluded_dialogs_text: string; telegram_action_targets_text?: string })[];
  providers?: ProviderDraft[];
};
export type Observation = { active?: boolean; unit_present?: boolean | null; state?: string; runtime?: { state: string; reason?: string } };
export type McpStatus = {
  provider_observations?: Record<string, import("./provider-details").ProviderObservation>;
  chat_attachments?: boolean;
  web_tools?: boolean; web_extract_tools?: boolean; context_tools?: boolean;
  chat_persona?: boolean; agent_version?: string; nodera_version?: string; nodera_available_version?: string;
  nodera_management?: { state: string; retained?: boolean };
  provider_usage?: Record<string, ProviderUsage>;
  bot_live_access?: boolean;
  bot_username?: string | null;
  settings: Settings; runtime_installed: boolean; bot: Observation; agent?: Observation; legacy_bot: Observation;
  modules: { id: ModuleId; name: string; enabled: boolean; configured: boolean; installed: boolean; management: {state: string; retained?: boolean}; execution: "service" | "on-demand" }[];
  mcp_modules: { id: McpId; name: string; description?: string; enabled: boolean; configured: boolean; installed?: boolean; execution: "on-demand" }[];
  tools: Record<McpId, string[]>;
};
export function makeDraft(settings: Settings): Draft {
  const { revision, accounts, providers, ...rest } = settings;
  const legacy = !Object.hasOwn(settings.mcp, "mihomo-mcp");
  const mcp = { ...settings.mcp, "mihomo-mcp": settings.mcp["mihomo-mcp"] ?? settings.mcp["vps-controller-mcp"] };
  const installed = settings.mcp_installed ?? Object.keys(mcp) as McpId[];
  const mcpInstalled = legacy && installed.includes("vps-controller-mcp") ? [...new Set([...installed, "mihomo-mcp" as const])] : installed;
  return { ...rest, mcp, assistant_style: settings.assistant_style ?? 'balanced', assistant_character: settings.assistant_character ?? '', assistant_instructions: settings.assistant_instructions ?? '', personalization_enabled: settings.personalization_enabled ?? true, web_extract_enabled: settings.web_extract_enabled ?? true, web_max_sources: settings.web_max_sources ?? 3, web_read_chars: settings.web_read_chars ?? 14000, web_domains: settings.web_domains ?? [], web_enabled: settings.web_enabled ?? true, memory_enabled: settings.memory_enabled ?? true, memory_days: settings.memory_days ?? 30, mcp_installed: mcpInstalled, model_mode: settings.model_mode ?? "single", expected_revision: revision, api_key: null, bot_token: null, telegram_api_hash: null,
    providers: providers?.map(provider => ({ ...provider, api_key: null })),
    accounts: accounts.map(account => ({ ...account, session: null, excluded_dialogs_text: account.excluded_dialogs.join(", "), telegram_action_targets_text: (account.telegram_action_targets ?? []).join(", ") })) };
}
export function settingsPayload(draft: Draft) {
  return {
    role_policy_version: 2, role_permissions: draft.role_permissions, expected_revision: draft.expected_revision, modules: draft.modules, mcp: draft.mcp, mcp_installed: draft.mcp_installed ?? Object.keys(draft.mcp),
    assistant_style: draft.assistant_style, assistant_character: draft.assistant_character, assistant_instructions: draft.assistant_instructions, personalization_enabled: draft.personalization_enabled ?? true, web_extract_enabled: draft.web_extract_enabled ?? true, web_max_sources: draft.web_max_sources ?? 3, web_read_chars: draft.web_read_chars ?? 14000, web_domains: draft.web_domains ?? [], web_enabled: draft.web_enabled ?? true, memory_enabled: draft.memory_enabled, memory_days: draft.memory_days,
    provider_url: draft.provider_url, model: draft.model, model_mode: draft.model_mode ?? "single", api_key: draft.api_key,
    providers: draft.providers?.map(({ id, name, provider_url, model, model_mode, api_key }) => ({ id, name, provider_url, model, model_mode, api_key })),
    active_provider: draft.active_provider,
    bot_token: draft.bot_token, telegram_api_id: draft.telegram_api_id, telegram_api_hash: draft.telegram_api_hash,
    accounts: draft.accounts.map(account => ({ role: account.role, products: accountProducts(account), permission_overrides: account.permission_overrides, telegram_id: account.telegram_id, name: account.name, bot_access: accountPermissions(draft, account).has("subscriptions_admin"),
      bot_chat_access: accountPermissions(draft, account).has("chat") && !accountPermissions(draft, account).has("subscriptions_admin"), address_as: account.address_as ?? "",
      mihomo_profile_id: account.mihomo_profile_id ?? null, address_gender: account.address_gender ?? "auto",
      telegram_access: accountPermissions(draft, account).has("telegram_read"), vps_access: ["server_read", "server_manage", "diagnostics", "logs_read", "network_read", "security_read"].some(key => accountPermissions(draft, account).has(key as Permission)), session: account.session,
      excluded_dialogs: account.excluded_dialogs_text.split(",").map(value => value.trim()).filter(Boolean).map(Number),
      telegram_tools: account.telegram_tools,
      telegram_action_targets: account.telegram_action_targets_text === undefined ? account.telegram_action_targets ?? [] : account.telegram_action_targets_text.split(",").map(value => value.trim()).filter(Boolean),
    })),
  };
}
export const defaultTelegramTools = ["tg_auth_status", "tg_get_my_dialogs", "tg_get_messages", "tg_search_messages", "tg_get_stats"];
export function enabledTelegramTools(account: Pick<Account, "telegram_tools">) { return account.telegram_tools ?? defaultTelegramTools; }
export function chatModels(settings: Settings, catalog: string[]): string[] {
  return [...new Set((settings.model_mode === "catalog" ? [settings.model, ...catalog] : [settings.model]).filter(Boolean))];
}
export function changed(draft: Draft | null, settings: Settings | undefined) {
  return Boolean(draft && settings && JSON.stringify(settingsPayload(draft)) !== JSON.stringify(settingsPayload(makeDraft(settings))));
}
export function moduleState(status: McpStatus, id: ModuleId) {
  const item = [...status.modules, ...status.mcp_modules].find(item => item.id === id);
  if (id === "nodera-bot" && status.bot.runtime?.state === "running") return "Служба запущена";
  if (id === "nodera-bot" && status.bot.runtime?.state === "error") return "Ошибка службы";
  if (!item?.enabled) return "Выключен";
  if (!status.modules.find(item => item.id === (id === "nodera-bot" ? id : "ai-agent"))?.installed) return "Компонент не установлен";
  if (id !== "nodera-bot" && status.agent?.runtime?.state === "stopped") return "Среда агента остановлена";
  if (id !== "nodera-bot" && status.agent?.runtime?.state === "error") return "Ошибка среды агента";
  if (!item.configured) return "Нужны настройки";
  if (id === "nodera-bot") {
    if (status.bot.unit_present === false) return "Служба не установлена";
    return status.bot.runtime?.state === "stopped" ? "Служба остановлена" : "Состояние службы неизвестно";
  }
  return "Запуск по запросу";
}
