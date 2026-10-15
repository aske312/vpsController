import type { createApiClient } from "../shared/lib/api-request";
import type { ApplicationStatus, SystemAction } from "../shared/types/control-plane";
import type { CdnSecurityStatus } from "../shared/lib/cdn-security-operation";

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function runtime(value: unknown) {
  return value === undefined || (record(value) && ["running", "stopped", "error", "unknown"].includes(String(value.state))
    && typeof value.reason === "string" && typeof value.checked_at === "string");
}

export function applicationStatusSnapshot(value: unknown, validateOperation: (value: unknown) => SystemAction, validateCdn?: (value: unknown) => CdnSecurityStatus): ApplicationStatus {
  if (!record(value) || !record(value.api) || !Array.isArray(value.containers)) throw new Error("Некорректное состояние приложения");
  const api = value.api;
  if (["active", "enabled"].some((key) => api[key] !== null && typeof api[key] !== "boolean")
    || !runtime(api.runtime)) throw new Error("Некорректное состояние приложения");
  for (const container of value.containers) {
    if (!record(container) || !runtime(container.runtime)
      || ["Name", "Service", "State", "Status", "Health", "component_name", "purpose", "status_text"].some((key) => container[key] !== undefined && typeof container[key] !== "string")
      || (container.healthy != null && typeof container.healthy !== "boolean")) throw new Error("Некорректное состояние компонентов приложения");
  }
  if (value.runtime !== undefined && (!record(value.runtime) || !["systemd", "legacy-docker", "incomplete", "unknown"].includes(String(value.runtime.mode))
    || typeof value.runtime.migration_required !== "boolean")) throw new Error("Некорректный режим приложения");
  for (const [key, fields] of [["service_mode", ["active", "rollback_available"]], ["recovery", ["available", "report_available", "application_required"]]] as const) {
    const settings = value[key];
    if (settings !== undefined && (!record(settings) || fields.some((field) => settings[field] !== undefined && typeof settings[field] !== "boolean"))) throw new Error("Некорректные настройки приложения");
  }
  if (record(value.recovery) && value.recovery.application_command !== undefined && (typeof value.recovery.application_command !== "string" || !["rollback", "commit"].includes(value.recovery.application_command))) throw new Error("Некорректная команда восстановления приложения");
  let cdn: CdnSecurityStatus | undefined;
  if (value.cdn_security !== undefined) {
    if (!validateCdn) throw new Error("Не удалось проверить состояние CF/ECH");
    cdn = validateCdn(value.cdn_security);
  }
  return { ...value, action: validateOperation(value.action), ...(cdn ? { cdn_security: cdn } : {}) } as ApplicationStatus;
}

/** Operation observations must not be served by stale-while-revalidate caches. */
export function observedApplicationRequest(client: ReturnType<typeof createApiClient>, snapshot: (value: unknown) => ApplicationStatus): ReturnType<typeof createApiClient> {
  return async <T>(path: string, options: RequestInit = {}) => {
    const observation = path === "/application/status";
    const value = await client<T>(path, observation ? { ...options, cache: "no-store" } : options);
    return (observation ? snapshot(value) : value) as T;
  };
}

export function applicationStatusUnavailable(current: ApplicationStatus | null, pending: boolean): ApplicationStatus | null {
  if (!current) return null;
  const unknown = { state: "unknown" as const, reason: "Состояние приложения не подтверждено; обновите данные", checked_at: "" };
  return { ...current, api: { active: null, enabled: null, runtime: unknown },
    containers: current.containers.map((container) => ({ ...container, State: "unknown", Status: undefined, Health: undefined, healthy: null, runtime: unknown, status_text: unknown.reason })),
    action: pending ? { ...current.action, state: "unknown", result: undefined, progress: undefined, message: "Состояние операции не подтверждено; проверяем без повторной отправки" } : current.action };
}
