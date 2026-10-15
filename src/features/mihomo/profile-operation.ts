import type { createApiClient } from "../../shared/lib/api-request";
import type { Profile } from "./types";
import type { SystemAction } from "../../control-panel/system-operation";

type Request = ReturnType<typeof createApiClient>;
export type ProfileMutation = {
  profileId?: string;
  payload: { operation_id: string } & Record<string, unknown>;
};
type Report = (message: string) => void;
type Options = { timeoutMs?: number; pollDelayMs?: number; systemOperationId?: string; systemOperationUnit?: string;
  onOperation?: (action: SystemAction) => void; validateProfiles?: (value: unknown) => Profile[]; validateOperation?: (value: unknown) => SystemAction };

export function profileTransitionMessage(profile: Profile, deviceId?: string): string {
  const statuses = deviceId ? [profile.protection_status?.[deviceId]] : Object.values(profile.protection_status ?? {});
  const transitions = statuses.filter((status) => status?.previous_valid_until);
  if (!transitions.length) return "";
  const delivered = transitions.every((status) => status?.yaml_served_at != null);
  const deadlines = transitions.map((status) => status!.previous_valid_until! * 1000);
  const deadline = Math.min(...deadlines);
  return deadline > Date.now()
    ? `${delivered ? "Новая конфигурация выдана, но рабочее подключение ещё не подтверждено. " : ""}Старая конфигурация доступна до ${new Date(deadline).toLocaleTimeString("ru-RU")}. Обновите подписку в VPN-клиенте; до подтверждения перехода сохраняется прежнее подключение.`
    : "Старая конфигурация ожидает отключения. Обновите подписку в VPN-клиенте.";
}

export class ProfileResultUnknown extends Error {
  constructor() {
    super("Результат сохранения пока не подтверждён. Восстановите соединение, при необходимости обновите подписку в VPN-клиенте, затем проверьте результат.");
  }
}

export class ProfileMutationFailed extends Error {}
export class ProfileMutationSuperseded extends Error {
  constructor() {
    super("Операция сохранения завершена, но профиль уже изменён или удалён. Ваш ввод сохранён; загрузите актуальные данные.");
  }
}

function interrupted(cause: unknown) {
  return cause instanceof Error && "kind" in cause && (cause.kind === "network" || cause.kind === "response");
}

function retryableRead(cause: unknown) {
  return interrupted(cause) || (cause instanceof Error && "status" in cause
    && [404, 408, 429, 500, 502, 503, 504].includes(Number(cause.status)));
}

function matchingOperation(operation: SystemAction, identity: string, mutation: ProfileMutation, unit?: string) {
  return operation.id === identity && operation.action === (mutation.profileId ? "mihomo-profile-update" : "mihomo-profile-create")
    && typeof operation.unit === "string" && Boolean(operation.unit) && (!unit || operation.unit === unit)
    && ["queued", "running", "active", "activating", "unknown", "succeeded", "failed", "cancelled"].includes(operation.state || "");
}

function confirmedProfile(profile: Profile, mutation: ProfileMutation) {
  return mutation.profileId ? profile.id === mutation.profileId && profile.last_operation_id === mutation.payload.operation_id
    : profile.create_operation_id === mutation.payload.operation_id;
}

export async function checkProfileMutation(request: Request, mutation: ProfileMutation, report: Report, options: Options = {}): Promise<Profile> {
  if (!mutation.payload.operation_id) throw new ProfileResultUnknown();
  const identity = mutation.payload.operation_id.replace(/-/g, "");
  if (!options.systemOperationId && /^[a-f0-9]{32}$/.test(identity)) options = { ...options, systemOperationId: identity };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? 45_000);
  report("Проверяем подтверждённый результат сохранения профиля…");
  try {
    while (!controller.signal.aborted) {
      try {
        if (options.systemOperationId) {
          const raw = await request<SystemAction>(`/application/operations/${options.systemOperationId}`, { signal: controller.signal });
          const operation = options.validateOperation ? options.validateOperation(raw) : raw;
          if (!matchingOperation(operation, options.systemOperationId, mutation, options.systemOperationUnit)) {
            await new Promise((resolve) => setTimeout(resolve, options.pollDelayMs ?? 1500));
            continue;
          }
          options = { ...options, systemOperationUnit: operation.unit };
          options.onOperation?.(operation);
          if (operation.state === "failed" || operation.state === "cancelled" || (operation.result && !["success", "unknown"].includes(operation.result))) throw new ProfileMutationFailed(operation.message || "Изменение профиля не завершено успешно");
          if (operation.state !== "succeeded" || (operation.result && operation.result !== "success")) {
            await new Promise((resolve) => setTimeout(resolve, options.pollDelayMs ?? 1500));
            continue;
          }
        }
        const result = await request<{ items: Profile[] }>("/mihomo/profiles", { signal: controller.signal, cache: "no-store" });
        const profiles = options.validateProfiles ? options.validateProfiles(result) : result.items;
        const profile = profiles.find((item) => confirmedProfile(item, mutation));
        if (profile) return profile;
        if (options.systemOperationId && mutation.profileId) throw new ProfileMutationSuperseded();
      } catch (cause) {
        if (cause instanceof ProfileMutationFailed || cause instanceof ProfileMutationSuperseded) throw cause;
        if (!controller.signal.aborted && !retryableRead(cause)) throw new ProfileResultUnknown();
      }
      if (!controller.signal.aborted) await new Promise((resolve) => setTimeout(resolve, options.pollDelayMs ?? 1500));
    }
    throw new ProfileResultUnknown();
  } finally {
    clearTimeout(timer);
  }
}

export async function submitProfileMutation(request: Request, mutation: ProfileMutation, report: Report, options: Options = {}): Promise<Profile> {
  const identity = mutation.payload.operation_id.replace(/-/g, "");
  if (/^[a-f0-9]{32}$/.test(identity)) options = { ...options, systemOperationId: identity };
  try {
    const result = await request<Profile | SystemAction>(mutation.profileId ? `/mihomo/profiles/${mutation.profileId}` : "/mihomo/profiles", {
      method: mutation.profileId ? "PATCH" : "POST",
      body: JSON.stringify(mutation.payload),
      headers: options.systemOperationId ? { "X-Operation-ID": options.systemOperationId } : undefined,
    });
    if (result && typeof result === "object" && "action" in result) {
      let operation: SystemAction | undefined;
      try { operation = options.validateOperation ? options.validateOperation(result) : result; }
      catch { /* Validate admission by GET before publishing it. */ }
      if (operation && options.systemOperationId && matchingOperation(operation, options.systemOperationId, mutation)) {
        options.onOperation?.(operation);
        options = { ...options, systemOperationUnit: operation.unit };
      }
    } else if (result && typeof result === "object" && confirmedProfile(result as Profile, mutation)) {
      try { return options.validateProfiles ? options.validateProfiles({ items: [result] })[0] : result as Profile; }
      catch { /* A matching receipt still needs a valid snapshot; confirm it by reading. */ }
    }
  } catch (cause) {
    if (!interrupted(cause) && !(cause instanceof Error && "status" in cause && Number(cause.status) >= 500)) throw cause;
  }
  // A restart can drop the VPN carrying this request. Confirm the committed ID;
  // never replay the write or infer success from matching settings alone.
  return checkProfileMutation(request, mutation, report, options);
}
