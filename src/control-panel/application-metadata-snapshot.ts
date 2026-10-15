import type { ApplicationMetadata } from "../shared/types/control-plane";

type Row = Record<string, unknown>;
const record = (value: unknown): value is Row => Boolean(value && typeof value === "object" && !Array.isArray(value));
const string = (value: unknown) => typeof value === "string";
const bool = (value: unknown) => typeof value === "boolean";
const count = (value: unknown) => value === null || (typeof value === "number" && Number.isFinite(value) && value >= 0);
const optional = (row: Row, fields: string[], valid: (value: unknown) => boolean) => fields.every((key) => row[key] === undefined || valid(row[key]));

export function applicationMetadataSnapshot(value: unknown): ApplicationMetadata {
  if (!record(value) || !record(value.application_version) || !record(value.updates)) throw new Error("Некорректные сведения о версии приложения");
  const version = value.application_version;
  const updates = value.updates;
  if (!optional(version, ["branch", "current_commit", "latest_commit", "error"], string)
    || !optional(version, ["outdated"], (value) => value === null || bool(value))
    || !optional(version, ["refreshing"], bool) || !optional(version, ["checked_at"], (value) => value === null || string(value))) throw new Error("Некорректная ревизия приложения");
  if (!optional(updates, ["available", "security"], count) || !optional(updates, ["kernel_available"], (value) => value === null || bool(value))
    || !optional(updates, ["automatic"], (value) => value === null || bool(value)) || !optional(updates, ["reboot_required", "refreshing"], bool)
    || !optional(updates, ["source", "error"], string) || !optional(updates, ["checked_at"], (value) => value === null || string(value))) throw new Error("Некорректные сведения об обновлениях");
  if (value.dependencies !== undefined) {
    if (!Array.isArray(value.dependencies)) throw new Error("Некорректные зависимости приложения");
    const ids = new Set<string>();
    for (const item of value.dependencies) {
      if (!record(item) || typeof item.id !== "string" || !item.id || ids.has(item.id) || typeof item.name !== "string"
        || !optional(item, ["version"], string)) throw new Error("Некорректная версия зависимости приложения");
      ids.add(item.id);
    }
  }
  return value as ApplicationMetadata;
}

export function applicationMetadataUnavailable(current: ApplicationMetadata | null): ApplicationMetadata | null {
  if (!current) return null;
  return { application_version: { outdated: null, refreshing: false }, updates: {},
    dependencies: current.dependencies?.map(({ id, name }) => ({ id, name })) };
}
