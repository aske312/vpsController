import type { ConnectionStats } from "./types";

export type Activity = { configured: number; active: number; activity_available?: boolean; activity_known?: number; activity_unknown?: number };

export function connectionActivity(rows: Array<ConnectionStats | undefined>): Activity {
  const known = rows.filter(row => row?.activity_available !== false && typeof row?.active === "boolean");
  return { configured: rows.length, active: known.filter(row => row?.active).length,
    activity_available: known.length === rows.length, activity_known: known.length, activity_unknown: rows.length - known.length };
}

export function activityCount(value?: Activity): string {
  if (!value) return "—";
  if (value.activity_available !== false) return String(value.active);
  return value.active > 0 || (value.activity_known ?? 0) > 0 ? `≥ ${value.active}` : "—";
}

export function activityDescription(value?: Activity): string {
  if (!value) return "Данные об активности пока не получены";
  if (value.activity_available !== false) return `Активных каналов: ${value.active} из ${value.configured}`;
  return `Подтверждено активных: ${value.active}. ${value.activity_unknown == null ? "Часть каналов не наблюдается" : `Нет данных по каналам: ${value.activity_unknown}`}`;
}
