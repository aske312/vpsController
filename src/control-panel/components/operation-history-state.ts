import { actionLabels } from "../../shared/lib/control-plane-ui";
import type { SystemAction } from "../system-operation";

export type HistoryEntry = SystemAction & { source?: string; updated_at?: string };
export type HistoryFilter = "all" | "active" | "attention" | "completed";
export function historyEntries(value: unknown): HistoryEntry[] {
  if (!value || typeof value !== "object" || !("items" in value) || !Array.isArray(value.items)) throw Error("Invalid history");
  return value.items.map((entry: unknown) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) throw Error("Invalid operation");
    const item = entry as Record<string, unknown>;
    for (const key of ["id", "unit", "action", "source", "message", "state", "started_at", "updated_at"]) {
      if (item[key] != null && typeof item[key] !== "string") throw Error("Invalid operation field");
    }
    if (!item.id && !item.unit && !item.started_at && !item.updated_at) throw Error("Missing operation identity");
    if (item.progress != null && (typeof item.progress !== "number" || !Number.isFinite(item.progress))) throw Error("Invalid progress");
    return { ...item, started_at: item.started_at || undefined, updated_at: item.updated_at || undefined } as HistoryEntry;
  });
}
const active = new Set(["queued", "running", "active", "activating", "rebooting", "powering-off"]);
const completed = new Set(["succeeded", "done", "cancelled"]);
const states: Record<string, string> = { queued: "В очереди", running: "Выполняется", active: "Выполняется", activating: "Запускается", unknown: "Результат неизвестен", succeeded: "Завершена", done: "Завершена", failed: "Ошибка", cancelled: "Отменена", rebooting: "Перезагрузка", "powering-off": "Выключение" };
const sources: Record<string, string> = { system: "Система", mihomo: "Mihomo", cdn: "CDN" };
export function historyKey(entry: HistoryEntry) { return `${entry.source || "system"}:${entry.id || entry.unit || entry.started_at || entry.updated_at}`; }
export function historySource(entry: HistoryEntry) { return sources[entry.source || "system"] || entry.source || "Система"; }
export function historyTitle(entry: HistoryEntry) {
  const [kind, ...component] = (entry.action || "").split(":");
  return actionLabels[entry.action || ""] || `${actionLabels[kind] || kind || "Операция"}${component.length ? ` · ${component.join(" · ")}` : ""}`;
}
export function historyState(entry: HistoryEntry) {
  const state = entry.state || "unknown";
  return { label: states[state] || states.unknown, tone: active.has(state) ? "active" : state === "succeeded" || state === "done" ? "success" : state === "failed" ? "error" : state === "cancelled" ? "neutral" : "unknown" };
}
export function historyMatches(entry: HistoryEntry, filter: HistoryFilter, query: string) {
  const state = entry.state || "unknown";
  if (filter === "active" && !active.has(state) || filter === "completed" && !completed.has(state) || filter === "attention" && (active.has(state) || completed.has(state))) return false;
  const text = [historyTitle(entry), historySource(entry), historyState(entry).label, entry.action, entry.id, entry.unit, entry.message].join(" ").toLocaleLowerCase();
  return query.trim().toLocaleLowerCase().split(/\s+/).every(word => text.includes(word));
}
