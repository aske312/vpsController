"use client";

import { useId, useState } from "react";
import { bytes } from "../../shared/lib/control-plane-ui";
import type { ProfileStats, SubscriptionLimits, SubscriptionLimitsStatus, TrafficAmount, TrafficPeriod, TrafficUsage } from "./types";
import styles from "./profile-general.module.css";

export const unlimitedSubscription: SubscriptionLimits = { speed_mbps: null, traffic_gb: null, days: null };
export type SubscriptionTraffic = TrafficUsage;

const fields = [
  { key: "speed_mbps", label: "Скорость", unit: "Мбит/с", max: 100000, presets: [0, 10, 25, 50, 100, 200, 500, 1000] },
  { key: "traffic_gb", label: "Трафик", unit: "GB", max: 1000000, presets: [0, 10, 50, 100, 250, 500, 1000, 5000] },
  { key: "days", label: "Срок", unit: "дней", max: 36500, presets: [0, 7, 14, 30, 90, 180, 365] },
] as const;

export function subscriptionLimitChange(value: SubscriptionLimits, key: keyof SubscriptionLimits, input: number): SubscriptionLimits {
  return { ...value, [key]: input === 0 ? null : input };
}

/** Only durable server accounting; transient core counters are not lifetime usage. */
export function subscriptionTraffic(stats: ProfileStats | undefined, deviceId?: string): SubscriptionTraffic | undefined {
  return deviceId === undefined ? stats?.usage?.summary : stats?.usage?.devices[deviceId];
}

export function aggregateUsage(rows: (TrafficAmount | null | undefined)[]): TrafficAmount {
  const known = rows.filter((row): row is TrafficAmount & { used_bytes: number } => Boolean(row?.stats_available && row.used_bytes != null));
  return {
    used_bytes: rows.length === 0 || known.length > 0 ? known.reduce((sum, row) => sum + row.used_bytes, 0) : null,
    stats_available: rows.length === 0 || known.length > 0,
    stats_partial: known.length !== rows.length || known.some(row => row.stats_partial),
  };
}

export function usageBytes(traffic: TrafficAmount | null | undefined): string {
  return traffic?.stats_available && traffic.used_bytes != null ? `${traffic.stats_partial ? "≥ " : ""}${bytes(traffic.used_bytes)}` : "—";
}

export function usagePeriodLabel(traffic: TrafficUsage | null | undefined): string {
  return traffic?.history_started_at != null
    ? `с ${utcDate(traffic.history_started_at)}`
    : "За всё время";
}

/** Never substitute an all-time total for missing period history. */
export function periodTraffic(traffic: SubscriptionTraffic | undefined, period: TrafficPeriod) {
  return period === "all" ? traffic : traffic?.periods?.[period];
}

function utcDate(timestamp: number, withTime = false) {
  return new Date(timestamp * 1000).toLocaleString("ru-RU", {
    timeZone: "UTC", day: "2-digit", month: "2-digit", year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } as const : {}),
  });
}

export function SubscriptionLimitsFields({ value, status, traffic, disabled, onChange }: { value: SubscriptionLimits; status?: SubscriptionLimitsStatus; traffic?: SubscriptionTraffic; disabled: boolean; onChange: (value: SubscriptionLimits) => void }) {
  const id = useId();
  return <section className={styles.panel} aria-label="Контроль подписки">
    <header className={styles.heading}><h3>Лимиты подписки</h3></header>
    <div className={styles.limitRows}>{fields.map(field => <label className={styles.limitRow} key={field.key}>
      <span>{field.label}</span>
      <span className={styles.valueBox}><input className={styles.limitInput} type="number" aria-label={`${field.label}, ${field.unit}`} list={`${id}-${field.key}`} value={value[field.key] ?? 0} min={0} max={field.max} step={1} required disabled={disabled}
        onFocus={event => event.currentTarget.select()} onChange={event => onChange(subscriptionLimitChange(value, field.key, Number(event.target.value)))} />
      <span>{field.unit}</span></span>
      <datalist id={`${id}-${field.key}`}>{field.presets.map(preset => <option key={preset} value={preset} />)}</datalist>
    </label>)}</div>
    <SubscriptionUsage status={status} traffic={traffic} />
  </section>;
}

export function SubscriptionUsage({ status, traffic, label = "Использовано" }: { status?: SubscriptionLimitsStatus; traffic?: SubscriptionTraffic; label?: string }) {
  const [period, setPeriod] = useState<TrafficPeriod>("all");
  const selected = periodTraffic(traffic, period);
  const window = period !== "all" ? traffic?.periods?.[period] : undefined;
  const used = usageBytes(selected);
  return <div className={styles.usage} role="status" data-state={status?.state}>
    <span>{label}</span><b>{used}</b>
    <label className={styles.usageFilter}><span>Период</span><select aria-label="Период трафика" value={period} onChange={event => setPeriod(event.target.value as TrafficPeriod)}>
      <option value="day">За день</option><option value="week">За 7 дней</option><option value="month">За 30 дней</option><option value="all">За всё время</option>
    </select></label>
    <small className={styles.usagePeriod}>{period === "all" ? usagePeriodLabel(traffic)
      : window ? `${utcDate(window.started_at)} — ${utcDate(window.ended_at)} · дни по UTC, включая сегодня` : "История за выбранный период пока недоступна"}</small>
    {window && !selected?.stats_available && <small className={styles.usagePeriod}>За выбранный период пока нет доступных измерений.</small>}
    {period !== "all" && traffic?.history_started_at != null && <small className={styles.usagePeriod}>
      История собирается с {utcDate(traffic.history_started_at, true)} UTC. {window && traffic.history_started_at > window.started_at ? "Период учтён частично. Ранние данные доступны в итоге за всё время." : "Учтённый объём отмечен знаком ≥."}
    </small>}
    {status?.expires_at != null && <span>Подписка до {new Date(status.expires_at * 1000).toLocaleDateString("ru-RU")}</span>}
    {status?.state === "expired" && <strong>Срок подписки истёк. Доступ остановлен.</strong>}
    {status?.state === "exhausted" && <strong>Объём трафика исчерпан. Доступ остановлен.</strong>}
    {status?.state === "unknown" && <strong>Актуальность серверного контроля не подтверждена.</strong>}
  </div>;
}
