export type ModuleId = "telegram-mcp" | "vps-controller-mcp" | "nodera-bot" | "ai-agent";
export type McpId = "telegram-mcp" | "vps-controller-mcp";
export type AppModuleId = "nodera-bot" | "ai-agent";
export const roleNames = { owner: "Владелец", administrator: "Администратор", moderator: "Модератор", user: "Пользователь", guest: "Гость" } as const;
export type Role = keyof typeof roleNames;
export const permissionNames = {
  chat: "Общение с AI", model_select: "Выбор модели", web: "Поиск в интернете", memory: "Личная память",
  telegram_read: "Чтение своего Telegram", server_read: "Просмотр сервера и служб", server_manage: "Управление службами",
  diagnostics: "Диагностика, сеть, безопасность и логи", subscription_read: "Просмотр своей подписки", subscription_manage: "Настройка своей подписки и устройств",
  subscriptions_admin: "Управление всеми подписками", roles_manage: "Управление ролями и правами",
} as const;
export type Permission = keyof typeof permissionNames;
export const roleDefaults: Record<Role, Permission[]> = {
  owner: Object.keys(permissionNames) as Permission[],
  administrator: (Object.keys(permissionNames) as Permission[]).filter(key => key !== "roles_manage"),
  moderator: ["chat", "model_select", "web", "memory", "telegram_read", "server_read", "subscription_read", "subscription_manage"],
  user: ["chat", "model_select", "web", "memory", "telegram_read", "subscription_read", "subscription_manage"], guest: ["chat", "web"],
};
export function accountRole(account: Account): Role { return account.role ?? (account.bot_access ? "administrator" : account.vps_access ? "moderator" : account.bot_chat_access || account.mihomo_profile_id || account.telegram_access ? "user" : "guest"); }
export function accountPermissions(settings: Pick<Settings, "role_permissions">, account: Account): Set<Permission> {
  if (!account.role) {
    const result = new Set<Permission>();
    if (account.bot_access || account.bot_chat_access) ["chat", "model_select", "web", "memory"].forEach(key => result.add(key as Permission));
    if (account.bot_access || account.vps_access) ["server_read", "server_manage", "web", "memory"].forEach(key => result.add(key as Permission));
    if (account.bot_access) ["diagnostics", "subscriptions_admin", "subscription_read", "subscription_manage"].forEach(key => result.add(key as Permission));
    if (account.mihomo_profile_id) ["subscription_read", "subscription_manage", "web", "memory"].forEach(key => result.add(key as Permission));
    if (account.telegram_access) result.add("telegram_read");
    return result;
  }
  const result = new Set(settings.role_permissions?.[account.role] ?? roleDefaults[account.role]);
  if (account.role !== "owner") { for (const [key, enabled] of Object.entries(account.permission_overrides ?? {})) { if (enabled) result.add(key as Permission); else result.delete(key as Permission); } result.delete("roles_manage"); }
  return result;
}
export type Account = {
  role?: Role | null; permission_overrides?: Partial<Record<Permission, boolean>>;
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
  role_permissions?: Partial<Record<Role, Permission[]>>;
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
  accounts: (Account & { session: string | null; excluded_dialogs_text: string })[];
  providers?: ProviderDraft[];
};
export type Observation = { active?: boolean; unit_present?: boolean | null; state?: string; runtime?: { state: string; reason?: string } };
export type McpStatus = {
  chat_attachments?: boolean;
  web_tools?: boolean; context_tools?: boolean;
  chat_persona?: boolean; agent_version?: string; nodera_version?: string; nodera_available_version?: string;
  nodera_management?: { state: string; retained?: boolean };
  provider_usage?: Record<string, ProviderUsage>;
  bot_live_access?: boolean;
  bot_username?: string | null;
  settings: Settings; runtime_installed: boolean; bot: Observation; agent?: Observation; legacy_bot: Observation;
  modules: { id: ModuleId; name: string; enabled: boolean; configured: boolean; installed: boolean; management: {state: string; retained?: boolean}; execution: "service" | "on-demand" }[];
  mcp_modules: { id: McpId; name: string; description?: string; enabled: boolean; configured: boolean; installed?: boolean; execution: "on-demand" }[];
  tools: Record<"telegram-mcp" | "vps-controller-mcp", string[]>;
};
export function makeDraft(settings: Settings): Draft {
  const { revision, accounts, providers, ...rest } = settings;
  return { ...rest, assistant_style: settings.assistant_style ?? 'balanced', assistant_character: settings.assistant_character ?? '', assistant_instructions: settings.assistant_instructions ?? '', personalization_enabled: settings.personalization_enabled ?? true, web_domains: settings.web_domains ?? [], web_enabled: settings.web_enabled ?? true, memory_enabled: settings.memory_enabled ?? true, memory_days: settings.memory_days ?? 30, mcp_installed: settings.mcp_installed ?? Object.keys(settings.mcp) as McpId[], model_mode: settings.model_mode ?? "single", expected_revision: revision, api_key: null, bot_token: null, telegram_api_hash: null,
    providers: providers?.map(provider => ({ ...provider, api_key: null })),
    accounts: accounts.map(account => ({ ...account, session: null, excluded_dialogs_text: account.excluded_dialogs.join(", ") })) };
}
export function settingsPayload(draft: Draft) {
  return {
    role_permissions: draft.role_permissions, expected_revision: draft.expected_revision, modules: draft.modules, mcp: draft.mcp, mcp_installed: draft.mcp_installed ?? Object.keys(draft.mcp),
    assistant_style: draft.assistant_style, assistant_character: draft.assistant_character, assistant_instructions: draft.assistant_instructions, personalization_enabled: draft.personalization_enabled ?? true, web_domains: draft.web_domains ?? [], web_enabled: draft.web_enabled ?? true, memory_enabled: draft.memory_enabled, memory_days: draft.memory_days,
    provider_url: draft.provider_url, model: draft.model, model_mode: draft.model_mode ?? "single", api_key: draft.api_key,
    providers: draft.providers?.map(({ id, name, provider_url, model, model_mode, api_key }) => ({ id, name, provider_url, model, model_mode, api_key })),
    active_provider: draft.active_provider,
    bot_token: draft.bot_token, telegram_api_id: draft.telegram_api_id, telegram_api_hash: draft.telegram_api_hash,
    accounts: draft.accounts.map(account => ({ role: account.role, permission_overrides: account.permission_overrides, telegram_id: account.telegram_id, name: account.name, bot_access: accountPermissions(draft, account).has("subscriptions_admin"),
      bot_chat_access: accountPermissions(draft, account).has("chat") && !accountPermissions(draft, account).has("subscriptions_admin"), address_as: account.address_as ?? "",
      mihomo_profile_id: account.mihomo_profile_id ?? null, address_gender: account.address_gender ?? "auto",
      telegram_access: accountPermissions(draft, account).has("telegram_read"), vps_access: ["server_read", "server_manage", "diagnostics"].some(key => accountPermissions(draft, account).has(key as Permission)), session: account.session,
      excluded_dialogs: account.excluded_dialogs_text.split(",").map(value => value.trim()).filter(Boolean).map(Number),
    })),
  };
}
export function chatModels(settings: Settings, catalog: string[]): string[] {
  return [...new Set((settings.model_mode === "catalog" ? catalog : [settings.model]).filter(Boolean))];
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
