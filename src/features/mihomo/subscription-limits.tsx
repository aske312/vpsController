"use client";

import { useId } from "react";
import { bytes } from "../../shared/lib/control-plane-ui";
import type { ProfileStats, SubscriptionLimits, SubscriptionLimitsStatus, TrafficUsage } from "./types";
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
  const used = traffic?.stats_available && traffic.used_bytes != null ? `${traffic.stats_partial ? "≥ " : ""}${bytes(traffic.used_bytes)}` : "—";
  return <div className={styles.usage} role="status" data-state={status?.state}>
    <span>{label}</span><b>{used}</b>
    {status?.expires_at != null && <span>до {new Date(status.expires_at * 1000).toLocaleDateString("ru-RU")}</span>}
    {status?.state === "expired" && <strong>Срок подписки истёк. Доступ остановлен.</strong>}
    {status?.state === "exhausted" && <strong>Объём трафика исчерпан. Доступ остановлен.</strong>}
    {status?.state === "unknown" && <strong>Актуальность серверного контроля не подтверждена.</strong>}
  </div>;
}
