import type { Module, PersonalRule, PolicySettings, Profile, ProfilePreset, ProfileStats, Status } from "./types";
import type { NetworkStatus } from "../../shared/types/control-plane";

type Section = { path: string; accept: (value: unknown) => void; onFailure?: (cause: unknown) => void };

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

/** Runtime observations cannot be inferred from an empty or malformed response. */
export function managerStatusSnapshot(value: unknown): Status {
  if (!record(value) || (value.active !== null && typeof value.active !== "boolean")) throw new Error("Некорректное состояние менеджера Mihomo");
  for (const key of ["core_version", "endpoint"]) {
    if (value[key] !== undefined && typeof value[key] !== "string") throw new Error("Некорректные данные менеджера Mihomo");
  }
  for (const key of ["profiles", "profiles_in_use", "credentials", "channels_installed", "modules_installed", "modules_total"]) {
    if (value[key] !== undefined && (!Number.isInteger(value[key]) || Number(value[key]) < 0)) throw new Error("Некорректные счётчики менеджера Mihomo");
  }
  if ((value.recovery_required !== undefined && typeof value.recovery_required !== "boolean")
    || (value.channels_in_use !== undefined && (!Array.isArray(value.channels_in_use) || value.channels_in_use.some((id) => typeof id !== "string")))) {
    throw new Error("Некорректные данные менеджера Mihomo");
  }
  for (const [key, states] of [["runtime", ["running", "stopped", "error", "unknown"]], ["health", ["healthy", "possible_issues", "error", "unchecked", "unknown"]]] as const) {
    const observation = value[key];
    if (observation !== undefined && (!record(observation) || !states.includes(observation.state as never)
      || (observation.reason !== undefined && typeof observation.reason !== "string")
      || (observation.checked_at != null && typeof observation.checked_at !== "string"))) throw new Error("Некорректное наблюдение менеджера Mihomo");
  }
  if (record(value.runtime)) {
    const observedActive = value.runtime.state === "running" ? true : value.runtime.state === "stopped" ? false : null;
    if (value.active !== observedActive) throw new Error("Противоречивое состояние менеджера Mihomo");
  }
  return value as Status;
}

/** Validate the endpoint evidence consumed by Mihomo, retaining legacy no-evidence reads. */
export function networkRoutesSnapshot(value: unknown): NetworkStatus {
  if (!record(value)) throw new Error("Некорректные данные маршрутов сети");
  for (const key of ["transport_endpoint_checks", "transport_endpoint_checks_by_domain"]) {
    const checks = value[key];
    if (checks === undefined) continue;
    if (!record(checks)) throw new Error("Некорректный список проверок маршрутов");
    for (const check of Object.values(checks)) {
      if (!record(check) || !["cdn", "tls_relay", "udp_relay"].includes(String(check.kind))
        || typeof check.domain !== "string" || !Array.isArray(check.resolved) || check.resolved.some((ip) => typeof ip !== "string")
        || !["ready", "unchecked", "warning", "error", "unresolved", "stale"].includes(String(check.status))
        || typeof check.ready !== "boolean") throw new Error("Некорректная проверка маршрута");
      for (const field of ["binding_id", "check_id", "checked_at", "consumer_id"]) {
        if (check[field] != null && typeof check[field] !== "string") throw new Error("Некорректная квитанция проверки маршрута");
      }
      for (const field of ["desired_revision", "checked_revision", "failed_checks"]) {
        if (check[field] != null && (!Number.isInteger(check[field]) || Number(check[field]) < 0)) throw new Error("Некорректная ревизия проверки маршрута");
      }
      if ((check.selectable !== undefined && typeof check.selectable !== "boolean")
        || (check.verification !== undefined && !["dns_only", "end_to_end"].includes(String(check.verification)))) throw new Error("Некорректная доступность маршрута");
    }
  }
  return value as NetworkStatus;
}

function namedCollection(value: unknown): Record<string, unknown>[] {
  if (!record(value) || !Array.isArray(value.items)) throw new Error("Некорректный ответ списка Mihomo");
  const ids = new Set<string>();
  for (const item of value.items) {
    if (!record(item) || typeof item.id !== "string" || !item.id || typeof item.name !== "string" || ids.has(item.id)) {
      throw new Error("Некорректная или неоднозначная запись списка Mihomo");
    }
    ids.add(item.id);
  }
  return value.items;
}

export function moduleSnapshots(value: unknown): Module[] {
  const items = namedCollection(value);
  for (const item of items) {
    if (!["transport", "dns", "routing"].includes(String(item.category)) || typeof item.installed !== "boolean"
      || !optionalStrings(item, ["description", "category_name", "service", "installed_version", "available_version"])
      || ["active", "installable", "update_available", "update_breaking"].some((key) => item[key] !== undefined && typeof item[key] !== "boolean")
      || (item.settings_values !== undefined && !scalarRecord(item.settings_values))
      || ["settings", "connection_settings"].some((key) => item[key] !== undefined && !settingRows(item[key]))) {
      throw new Error("Некорректные данные компонента Mihomo");
    }
  }
  return items as Module[];
}

function subscriptionLimits(value: unknown): boolean {
  if (value === undefined) return true;
  if (!record(value)) return false;
  return ([['speed_mbps', 100000], ['traffic_gb', 1000000], ['days', 36500]] as const).every(([key, max]) => value[key] === null || (Number.isInteger(value[key]) && Number(value[key]) >= 1 && Number(value[key]) <= max));
}

function subscriptionLimitStatus(value: unknown): boolean {
  if (value === undefined) return true;
  return record(value) && ['active', 'expired', 'exhausted', 'unknown'].includes(String(value.state))
    && typeof value.enforced === 'boolean' && Number.isSafeInteger(value.used_bytes) && Number(value.used_bytes) >= 0
    && ['started_at', 'expires_at', 'checked_at'].every(key => value[key] === null || (typeof value[key] === 'number' && Number.isFinite(value[key]) && Number(value[key]) >= 0 && Number(value[key]) <= 253402300799));
}

export function profileSnapshots(value: unknown): Profile[] {
  const items = namedCollection(value);
  for (const item of items) {
    if (!optionalStrings(item, ["export_filename", "created_at", "updated_at", "common_device_id", "revision", "create_revision", "create_operation_id", "last_operation_id"])
      || (item.subscription_status !== undefined && !["active", "obsolete", "missing"].includes(String(item.subscription_status)))
      || (item.protection_status !== undefined && !protectionStatus(item.protection_status))
      || !subscriptionLimits(item.limits) || !subscriptionLimitStatus(item.limits_status)
      || (item.channels !== undefined && !stringList(item.channels))
      || !uniqueRows(item.connections) || item.connections.some((row) => !record(row) || typeof row.component !== "string"
        || !optionalStrings(row, ["name", "device_id"]) || !scalarRecord(row.settings) || !routeState(row.route_state))
      || (item.routing !== undefined && !scalarRecord(item.routing))
      || (item.devices !== undefined && (!uniqueRows(item.devices) || item.devices.some((row) => !deviceRow(row))))) {
      throw new Error("Некорректные данные профиля Mihomo");
    }
  }
  // The manager's ProfileDeviceInput serializes absent client metadata as null.
  // Keep the UI's optional-string contract without changing the server snapshot.
  return items.map((item) => {
    if (!Array.isArray(item.devices)) return item;
    let changed = false;
    const devices = item.devices.map((device) => {
      if (!deviceMetadata.some((key) => device[key] === null)) return device;
      changed = true;
      return Object.fromEntries(Object.entries(device).filter(([key, value]) => value !== null || !deviceMetadata.includes(key)));
    });
    return changed ? { ...item, devices } : item;
  }) as Profile[];
}

function stringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((entry) => typeof entry === "string");
}

function optionalStrings(row: Record<string, unknown>, keys: string[]) {
  return keys.every((key) => row[key] === undefined || typeof row[key] === "string");
}

function scalarRecord(value: unknown): value is Record<string, string | number | boolean> {
  return record(value) && Object.values(value).every((entry) => typeof entry === "string" || typeof entry === "boolean" || (typeof entry === "number" && Number.isFinite(entry)));
}

function uniqueRows(value: unknown): value is Record<string, unknown>[] {
  return Array.isArray(value) && value.every((row) => record(row) && typeof row.id === "string" && Boolean(row.id))
    && new Set(value.map((row) => row.id)).size === value.length;
}

function settingRows(value: unknown) {
  return Array.isArray(value) && value.every((field) => record(field) && typeof field.key === "string" && Boolean(field.key)
    && optionalStrings(field, ["label", "help", "export_key"])
    && (field.type === undefined || ["text", "number", "select", "textarea", "boolean"].includes(String(field.type)))
    && (field.default === undefined || scalarRecord({ value: field.default }))
    && ["min", "max"].every((key) => field[key] === undefined || (typeof field[key] === "number" && Number.isFinite(field[key])))
    && (field.formats === undefined || stringList(field.formats))
    && (field.options === undefined || (Array.isArray(field.options) && field.options.every((option) => typeof option === "string"
      || (record(option) && typeof option.value === "string" && typeof option.label === "string")))))
    && new Set(value.map((field) => field.key)).size === value.length;
}

function routeState(value: unknown) {
  return value === undefined || (record(value) && ["current", "stale", "unknown"].includes(String(value.state))
    && typeof value.reason === "string" && stringList(value.addresses) && stringList(value.bindings));
}

function protectionStatus(value: unknown) {
  const count = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
  const timestamp = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 8.64e12;
  return record(value) && Object.values(value).every((status) => {
    if (!record(status) || !count(status.vless_connections) || typeof status.encryption_pending !== "boolean") return false;
    const total = status.vless_connections;
    const routes = status.vless_connections_by_route;
    if (routes !== undefined && (!record(routes) || ["reality", "cdn", "tls"].some((route) => !count(routes[route]) || routes[route] > total))) return false;
    return (status.previous_connections === undefined || count(status.previous_connections))
      && ["previous_valid_until", "yaml_served_at"].every((key) => status[key] === undefined || timestamp(status[key]));
  });
}

const deviceMetadata = ["hwid_hash", "created_at", "last_seen_at", "os", "os_version", "client_name", "client_version", "core_name", "core_version", "user_agent"];

function deviceRow(value: unknown) {
  if (!record(value) || typeof value.name !== "string"
    || !deviceMetadata.every((key) => value[key] == null || typeof value[key] === "string")
    || (value.scope !== undefined && !["common", "hwid", "manual", "legacy"].includes(String(value.scope)))
    || (value.manual !== undefined && typeof value.manual !== "boolean")
    || (value.routing !== undefined && !scalarRecord(value.routing))
    || (value.personal_rule_ids !== undefined && !stringList(value.personal_rule_ids))) return false;
  const formats = ["mihomo"];
  if ((value.subscription_format !== undefined && !formats.includes(String(value.subscription_format)))
    || (value.supported_formats !== undefined && (!stringList(value.supported_formats) || value.supported_formats.some((format) => !formats.includes(format))))) return false;
  return value.verified_clients === undefined || (uniqueRows(value.verified_clients) && value.verified_clients.every((client) =>
    formats.includes(String(client.format)) && ["client_name", "client_version", "core_name", "core_version", "os", "os_version", "app_url"].every((key) => typeof client[key] === "string")));
}

function identifiedRows(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) throw new Error("Некорректный список политики Mihomo");
  const ids = new Set<string>();
  for (const row of value) {
    if (!record(row) || typeof row.id !== "string" || !row.id || ids.has(row.id)) throw new Error("Неоднозначная запись политики Mihomo");
    ids.add(row.id);
  }
  return value;
}

function policyRevision(value: Record<string, unknown>) {
  for (const key of ["revision", "presets_revision"]) {
    if (value[key] !== undefined && (typeof value[key] !== "string" || !/^[a-f0-9]{64}$/.test(value[key]))) throw new Error("Некорректная revision политики Mihomo");
  }
}

function presetRows(value: unknown): ProfilePreset[] {
  const rows = identifiedRows(value);
  for (const row of rows) {
    if (typeof row.name !== "string" || (row.description !== undefined && typeof row.description !== "string") || !["fallback", "url-test", "select"].includes(String(row.strategy))
      || !Array.isArray(row.components) || row.components.some((component) => !record(component) || typeof component.id !== "string"
        || ["label", "transport"].some((key) => component[key] !== undefined && typeof component[key] !== "string")
        || (component.xray_protocol !== undefined && !["vless", "vmess", "trojan"].includes(String(component.xray_protocol)))
        || ["cdn", "tls"].some((key) => component[key] !== undefined && typeof component[key] !== "boolean"))) {
      throw new Error("Некорректный пресет Mihomo");
    }
  }
  return rows as ProfilePreset[];
}

export function personalRuleSnapshot(value: unknown): PersonalRule {
  const rule = identifiedRows([value])[0];
  policyRevision(rule);
  if (typeof rule.code !== "string" || typeof rule.title !== "string" || (rule.description !== undefined && typeof rule.description !== "string") || !["domain", "process"].includes(String(rule.kind))
    || !["GATE.312", "DIRECT", "REJECT"].includes(String(rule.target)) || !Array.isArray(rule.entries) || rule.entries.some((entry) => typeof entry !== "string")) {
    throw new Error("Некорректное личное правило Mihomo");
  }
  return rule as PersonalRule;
}

export function presetsSnapshot(value: unknown) {
  if (!record(value)) throw new Error("Некорректный ответ пресетов Mihomo");
  policyRevision(value);
  presetRows(value.presets);
  return value as { presets: ProfilePreset[]; revision?: string; last_operation_id?: string };
}

export function policySnapshot(value: unknown, kind: "dns" | "routing"): PolicySettings {
  if (!record(value) || !Array.isArray(value.schema) || !record(value.values)) throw new Error("Некорректный ответ политики Mihomo");
  policyRevision(value);
  const keys = new Set<string>();
  for (const field of value.schema) {
    if (!record(field) || typeof field.key !== "string" || !field.key || keys.has(field.key) || typeof field.label !== "string"
      || !["text", "number", "select", "textarea", "boolean"].includes(String(field.type))
      || (field.help !== undefined && typeof field.help !== "string")
      || (field.default !== undefined && (!["string", "boolean", "number"].includes(typeof field.default) || (typeof field.default === "number" && !Number.isFinite(field.default))))
      || (field.options !== undefined && (!Array.isArray(field.options) || field.options.some((option) => typeof option !== "string"
        && (!record(option) || typeof option.value !== "string" || typeof option.label !== "string"))))) throw new Error("Некорректная схема политики Mihomo");
    keys.add(field.key);
  }
  if (Object.values(value.values).some((entry) => !["string", "boolean", "number"].includes(typeof entry) || (typeof entry === "number" && !Number.isFinite(entry)))) {
    throw new Error("Некорректные значения политики Mihomo");
  }
  if (kind === "routing") {
    presetRows(value.presets);
    for (const rule of identifiedRows(value.personal_rules)) personalRuleSnapshot(rule);
    for (const row of identifiedRows(value.rule_lists)) {
      if (typeof row.key !== "string" || typeof row.title !== "string" || typeof row.default_rules !== "string" || typeof row.using_default !== "boolean") {
        throw new Error("Некорректный список правил Mihomo");
      }
    }
  }
  return value as PolicySettings;
}

/** Reject broken telemetry instead of rendering it as live activity or zeroes. */
export function profileStatsSnapshot(value: unknown): ProfileStats {
  if (!record(value) || !record(value.summary)) throw new Error("Некорректный ответ статистики профиля");
  const summary = value.summary;
  if (!Number.isInteger(summary.configured) || !Number.isInteger(summary.active)
    || Number(summary.configured) < 0 || Number(summary.active) < 0 || Number(summary.active) > Number(summary.configured)) {
    throw new Error("Некорректные счётчики активности профиля");
  }
  const connections = value.connections === undefined ? {} : value.connections;
  if (!record(connections)) throw new Error("Некорректная статистика подключений профиля");
  if (value.usage != null) {
    if (!record(value.usage) || !record(value.usage.devices)) throw new Error("Некорректный учёт трафика");
    for (const row of [value.usage.summary, ...Object.values(value.usage.devices)]) {
      if (!record(row) || typeof row.stats_available !== "boolean" || typeof row.stats_partial !== "boolean"
        || (row.used_bytes !== null && (typeof row.used_bytes !== "number" || !Number.isSafeInteger(row.used_bytes) || row.used_bytes < 0))
        || (row.stats_available && row.used_bytes === null)) throw new Error("Некорректный расход трафика");
    }
  }
  for (const row of [summary, ...Object.values(connections)]) {
    if (!record(row)) throw new Error("Некорректная статистика подключения");
    for (const key of ["stats_available", "stats_partial", "activity_available"]) {
      if (row[key] !== undefined && typeof row[key] !== "boolean") throw new Error("Некорректная доступность статистики профиля");
    }
    for (const key of ["rx_bytes", "tx_bytes", "used_bytes", "handshake_age_s", "last_handshake_age_s", "latency_ms"]) {
      if (row[key] != null && (typeof row[key] !== "number" || !Number.isFinite(row[key]) || row[key] < 0)) throw new Error("Некорректные значения статистики профиля");
    }
    if (row !== summary && row.active != null && typeof row.active !== "boolean") throw new Error("Некорректное состояние подключения");
  }
  return { ...value, connections } as ProfileStats;
}

/** Publish each successful section even when another read fails or stalls. */
export async function refreshWorkspaceSections(
  request: (path: string) => Promise<unknown>,
  sections: Record<string, Section>,
) {
  const failures: Record<string, unknown> = {};
  await Promise.all(Object.entries(sections).map(async ([key, section]) => {
    try { section.accept(await request(section.path)); }
    catch (cause) { failures[key] = cause; section.onFailure?.(cause); }
  }));
  return failures;
}
