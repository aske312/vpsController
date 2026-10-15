import type { createApiClient } from "../../shared/lib/api-request";
import type { PersonalRule, PolicySettings, ProfilePreset } from "./types";
import type { submitSystemOperation, SystemAction } from "../../control-panel/system-operation";

type Request = ReturnType<typeof createApiClient>;
export type PolicySave = { id: string; kind: "dns" | "routing" | "presets" | "personal"; ruleId?: string; action?: never }
  | { id: string; kind: "operation"; action: string; ruleId?: never };
export type PresetsResult = { presets: ProfilePreset[]; revision?: string; last_operation_id?: string };
type Options = { attempts?: number; delayMs?: number; admit?: typeof submitSystemOperation; validate?: (value: unknown) => unknown };
const STORAGE_KEY = "mihomo-pending-policy-save:v1";
export const POLICY_SAVE_CHANGED = "mihomo-policy-save-changed";

export class PolicyResultUnknown extends Error {
  constructor() {
    super("Результат сохранения не подтверждён. Новые изменения приостановлены. Проверьте результат после восстановления соединения.");
  }
}

export function readPendingPolicySave(): PolicySave | null {
  if (typeof window === "undefined") return null;
  try {
    const value = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) || "null");
    if (!value || typeof value.id !== "string" || !/^[a-f0-9]{32}$/.test(value.id) || !["dns", "routing", "presets", "personal", "operation"].includes(value.kind)
      || (value.ruleId !== undefined && (value.kind !== "personal" || !/^personal-[a-f0-9]{12}$/.test(value.ruleId)))) return null;
    if (value.kind === "operation") {
      if (typeof value.action !== "string" || !/^mihomo-(?:profile|module)-[a-z0-9:-]{1,100}$/.test(value.action)) return null;
      return { id: value.id, kind: "operation", action: value.action };
    }
    return { id: value.id, kind: value.kind, ...(value.ruleId ? { ruleId: value.ruleId } : {}) };
  } catch { return null; }
}

export function persistPendingPolicySave(save: PolicySave | null) {
  if (typeof window === "undefined") return;
  try {
    if (save) window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ id: save.id, kind: save.kind, ...(save.kind === "operation" ? { action: save.action } : save.ruleId ? { ruleId: save.ruleId } : {}) }));
    else window.sessionStorage.removeItem(STORAGE_KEY);
  } catch { /* The in-memory guard remains active if storage is unavailable. */ }
  if (typeof window.dispatchEvent === "function") window.dispatchEvent(new window.CustomEvent(POLICY_SAVE_CHANGED, { detail: save }));
}

export async function checkPolicySave<T>(request: Request, save: PolicySave, options: Options = {}): Promise<T> {
  for (let attempt = 0; attempt < (options.attempts ?? 3); attempt += 1) {
    if (attempt) await new Promise((resolve) => setTimeout(resolve, options.delayMs ?? 500));
    try {
      if (save.kind === "operation") {
        const result = await request<SystemAction>(`/application/operations/${save.id}`);
        if (operationMatches(save, result)) return (options.validate ? options.validate(result) : result) as T;
      } else if (save.kind === "dns") {
        const result = await request<PolicySettings>("/mihomo/dns/settings");
        if (result.last_operation_id === save.id) return (options.validate ? options.validate(result) : result) as T;
      } else {
        const result = await request<PolicySettings>("/mihomo/routing/schema");
        if (save.kind === "routing" && result.last_operation_id === save.id) return (options.validate ? options.validate(result) : result) as T;
        if (save.kind === "presets" && result.presets_last_operation_id === save.id) {
          const confirmed = { presets: result.presets, revision: result.presets_revision, last_operation_id: save.id };
          return (options.validate ? options.validate(confirmed) : confirmed) as T;
        }
        if (save.kind === "personal") {
          const rule = result.personal_rules?.find((item: PersonalRule) => item.last_operation_id === save.id && (!save.ruleId || item.id === save.ruleId));
          if (rule) return (options.validate ? options.validate(rule) : rule) as T;
        }
      }
    } catch { /* A failed read does not establish whether the write committed. */ }
  }
  throw new PolicyResultUnknown();
}

function operationMatches(save: Extract<PolicySave, { kind: "operation" }>, result: SystemAction) {
  return result.id === save.id && result.action === save.action && Boolean(result.unit)
    && ["queued", "running", "active", "activating", "unknown", "succeeded", "failed", "cancelled"].includes(result.state || "");
}

export async function submitPolicySave<T>(request: Request, save: PolicySave, path: string, payload: Record<string, unknown>, method = "PATCH", options: Options = {}): Promise<T> {
  if (save.kind === "operation" && !options.admit) throw new Error("Не удалось подготовить отправку операции");
  try {
    if (save.kind === "operation") {
      const result = await options.admit!(request, path, { method, ...(Object.keys(payload).length ? { body: JSON.stringify(payload) } : {}) }, save.id);
      if (operationMatches(save, result)) return (options.validate ? options.validate(result) : result) as T;
      throw new PolicyResultUnknown();
    }
    const result = await request<T>(path, { method, body: JSON.stringify({ ...payload, operation_id: save.id }) });
    return (options.validate ? options.validate(result) : result) as T;
  } catch (cause) {
    if (cause instanceof Error && "kind" in cause && cause.kind === "http" && "status" in cause
      && typeof cause.status === "number" && cause.status >= 400 && cause.status < 500) throw cause;
    // Never resend the mutation: only the exact committed receipt proves success.
    return checkPolicySave<T>(request, save, options);
  }
}
