import type { createApiClient } from "../../shared/lib/api-request";
import type { Profile } from "./types";

type Request = ReturnType<typeof createApiClient>;
export type ProfileMutation = {
  profileId?: string;
  payload: { operation_id: string } & Record<string, unknown>;
};
type Report = (message: string) => void;
type Options = { timeoutMs?: number; pollDelayMs?: number };

export function profileTransitionMessage(profile: Profile, deviceId?: string): string {
  const statuses = deviceId ? [profile.protection_status?.[deviceId]] : Object.values(profile.protection_status ?? {});
  const transitions = statuses.filter((status) => status?.previous_valid_until);
  if (!transitions.length) return "";
  const waiting = transitions.filter((status) => status?.yaml_served_at == null);
  if (!waiting.length) return "Новая конфигурация выдана клиенту. Ожидание обновления завершено; прежние подключения отключаются.";
  const deadlines = waiting.map((status) => status!.previous_valid_until! * 1000);
  const deadline = Math.min(...deadlines);
  return deadline > Date.now()
    ? `Старая конфигурация доступна до ${new Date(deadline).toLocaleTimeString("ru-RU")} или до обновления подписки клиентом. Обновите подписку в VPN-клиенте; до перехода действует прежняя защита.`
    : "Старая конфигурация ожидает отключения. Обновите подписку в VPN-клиенте.";
}

export class ProfileResultUnknown extends Error {
  constructor() {
    super("Результат сохранения пока не подтверждён. Восстановите соединение, при необходимости обновите подписку в VPN-клиенте, затем проверьте результат.");
  }
}

function interrupted(cause: unknown) {
  return cause instanceof Error && "kind" in cause && (cause.kind === "network" || cause.kind === "response");
}

export async function checkProfileMutation(request: Request, mutation: ProfileMutation, report: Report, options: Options = {}): Promise<Profile> {
  if (!mutation.payload.operation_id) throw new ProfileResultUnknown();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? 45_000);
  report("Связь прервалась. Проверяем результат сохранения профиля…");
  try {
    while (!controller.signal.aborted) {
      try {
        const result = await request<{ items: Profile[] }>("/mihomo/profiles", { signal: controller.signal });
        const profile = result.items.find((item) => mutation.profileId
          ? item.id === mutation.profileId && item.last_operation_id === mutation.payload.operation_id
          : item.create_operation_id === mutation.payload.operation_id);
        if (profile) return profile;
      } catch (cause) {
        if (!controller.signal.aborted && !interrupted(cause)) throw cause;
      }
      if (!controller.signal.aborted) await new Promise((resolve) => setTimeout(resolve, options.pollDelayMs ?? 1500));
    }
    throw new ProfileResultUnknown();
  } finally {
    clearTimeout(timer);
  }
}

export async function submitProfileMutation(request: Request, mutation: ProfileMutation, report: Report, options: Options = {}): Promise<Profile> {
  try {
    return await request<Profile>(mutation.profileId ? `/mihomo/profiles/${mutation.profileId}` : "/mihomo/profiles", {
      method: mutation.profileId ? "PATCH" : "POST",
      body: JSON.stringify(mutation.payload),
    });
  } catch (cause) {
    if (!interrupted(cause)) throw cause;
  }
  // A restart can drop the VPN carrying this request. Confirm the committed ID;
  // never replay the write or infer success from matching settings alone.
  return checkProfileMutation(request, mutation, report, options);
}
