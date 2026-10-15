import type { createApiClient } from "../shared/lib/api-request";
import type { MetricsSettings, ServicesStatus } from "../shared/types/control-plane";

type Row = Record<string, unknown>;
const record = (value: unknown): value is Row => Boolean(value && typeof value === "object" && !Array.isArray(value));
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");
const count = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
const optional = (row: Row, keys: string[], valid: (value: unknown) => boolean) => keys.every((key) => row[key] === undefined || valid(row[key]));
const bool = (value: unknown) => typeof value === "boolean";
const choice = (value: unknown, values: string[]) => typeof value === "string" && values.includes(value);

export function servicesSnapshot(value: unknown): ServicesStatus {
  if (!record(value) || !Array.isArray(value.items) || !record(value.automation) || !record(value.timers)
    || (value.failed_units !== null && !count(value.failed_units)) || !bool(value.reboot_required)) throw new Error("Некорректное состояние служб");
  const ids = new Set<string>();
  for (const item of value.items) {
    if (!record(item) || ["id", "name", "unit", "state", "substate", "unit_file_state", "active_since", "description"].some((key) => typeof item[key] !== "string")
      || !item.id || ids.has(item.id as string) || !bool(item.installed) || !bool(item.active) || !bool(item.enabled)
      || !optional(item, ["unit_present"], (value) => value === null || bool(value))
      || (item.restarts !== null && !count(item.restarts)) || !strings(item.controls)
      || !optional(item, ["disabled_controls"], strings)) throw new Error("Некорректное наблюдение службы");
    ids.add(item.id as string);
    if (item.runtime !== undefined && (!record(item.runtime) || !choice(item.runtime.state, ["running", "stopped", "error", "unknown"])
      || typeof item.runtime.reason !== "string" || typeof item.runtime.checked_at !== "string")) throw new Error("Некорректный runtime службы");
    if (item.management !== undefined && (!record(item.management) || !choice(item.management.state, ["managed", "unmanaged", "unknown"])
      || typeof item.management.reason !== "string")) throw new Error("Некорректное управление службой");
  }
  for (const kind of ["reboot", "cleanup", "update"]) {
    const schedule = value.automation[kind];
    const timer = value.timers[kind];
    if (!record(schedule) || !bool(schedule.enabled) || !choice(schedule.cadence, ["daily", "weekly", "monthly"])
      || !choice(schedule.weekday, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
      || !Number.isInteger(schedule.hour) || !count(schedule.hour) || schedule.hour > 23
      || !Number.isInteger(schedule.minute) || !count(schedule.minute) || schedule.minute > 59) throw new Error("Некорректное расписание службы");
    if (!record(timer) || (timer.installed !== null && !bool(timer.installed)) || (timer.active !== null && !bool(timer.active))
      || (timer.last_trigger !== null && typeof timer.last_trigger !== "string") || (timer.next_run !== null && typeof timer.next_run !== "string")) throw new Error("Некорректное наблюдение таймера");
    if (timer.runtime !== undefined && (!record(timer.runtime) || !choice(timer.runtime.state, ["running", "stopped", "error", "unknown"])
      || typeof timer.runtime.reason !== "string" || typeof timer.runtime.checked_at !== "string")) throw new Error("Некорректный runtime таймера");
  }
  if (!optional(value, ["automation_recovery_required", "observation_failed"], bool)) throw new Error("Некорректное состояние восстановления служб");
  if (value.panel_access !== undefined) {
    const access = value.panel_access;
    if (!record(access) || !choice(access.mode, ["external", "vpn"]) || !bool(access.public) || !strings(access.vpn_urls)
      || !optional(access, ["internal_url", "external_url"], (value) => typeof value === "string")
      || !optional(access, ["available_channels"], strings) || !optional(access, ["can_enable"], bool)) throw new Error("Некорректные настройки доступа панели");
  }
  if (value.service_mode !== undefined && (!record(value.service_mode) || !bool(value.service_mode.active))) throw new Error("Некорректный сервисный режим");
  if (value.logging !== undefined && (!record(value.logging) || !bool(value.logging.persistent) || !count(value.logging.retention_days)
    || !bool(value.logging.automatic_cleanup) || typeof value.logging.disk_usage !== "string")) throw new Error("Некорректные настройки журнала");
  return value as ServicesStatus;
}

export function metricsSettingsSnapshot(value: unknown): MetricsSettings {
  if (!record(value) || !bool(value.enabled) || typeof value.revision !== "string" || !value.revision
    || ["raw_hours", "minute_days", "hour_days", "disk_limit_mb", "used_bytes"].some((key) => !count(value[key]))
    || !optional(value, ["trimmed_at"], (value) => value === null || count(value))
    || !optional(value, ["error"], (value) => typeof value === "string")) throw new Error("Некорректные настройки хранения метрик");
  return value as MetricsSettings;
}

export function servicesUnavailable(current: ServicesStatus | null): ServicesStatus | null {
  if (!current) return null;
  return { ...current, observation_failed: true, failed_units: null,
    timers: Object.fromEntries(Object.entries(current.timers).map(([kind, timer]) => [kind, { ...timer, installed: null, active: null, last_trigger: null, next_run: null,
      runtime: { state: "unknown", reason: "Состояние таймера не подтверждено", checked_at: "" } }])) as ServicesStatus["timers"],
    items: current.items.map((item) => ({
    ...item, active: false, enabled: false, state: "unknown", substate: "", unit_file_state: "unknown", restarts: null, active_since: "",
    runtime: { state: "unknown", reason: "Состояние службы не подтверждено; обновите данные", checked_at: "" },
  })) };
}

/** All paths, including command read-back, share the same observation contract. */
export function observedServicesRequest(client: ReturnType<typeof createApiClient>): ReturnType<typeof createApiClient> {
  return async <T>(path: string, options: RequestInit = {}) => {
    const observation = path === "/services" || path === "/services/metrics";
    const value = await client<T>(path, observation && (!options.method || options.method === "GET") ? { ...options, cache: "no-store" } : options);
    return (path === "/services" ? servicesSnapshot(value) : path === "/services/metrics" ? metricsSettingsSnapshot(value) : value) as T;
  };
}
