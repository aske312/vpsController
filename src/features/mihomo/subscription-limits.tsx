"use client";

import { useId } from "react";
import type { SubscriptionLimits, SubscriptionLimitsStatus } from "./types";

export const unlimitedSubscription: SubscriptionLimits = { speed_mbps: null, traffic_gb: null, days: null };

const fields = [
  { key: "speed_mbps", label: "Скорость", unit: "Мбит/с", max: 100000, defaultValue: 100, help: "Общая для всех устройств. Одинаковый потолок отдельно на приём и передачу." },
  { key: "traffic_gb", label: "Общий трафик", unit: "GB", max: 1000000, defaultValue: 100, help: "Приём + передача по всем протоколам и устройствам. 1 GB = 1 000 000 000 байт." },
  { key: "days", label: "Дни использования", unit: "дней", max: 36500, defaultValue: 30, help: "С момента включения ограничения. Изменение профиля не начинает срок заново." },
] as const;

export function SubscriptionLimitsFields({ value, status, disabled, onChange }: { value: SubscriptionLimits; status?: SubscriptionLimitsStatus; disabled: boolean; onChange: (value: SubscriptionLimits) => void }) {
  const id = useId();
  return <section className="mihomoSubscriptionLimits" aria-label="Контроль подписки">
    <header><div><b>Контроль подписки</b></div></header>
    <div className="mihomoSubscriptionLimitGrid">{fields.map(field => <div className="mihomoSubscriptionLimit" key={field.key} title={field.help}>
      <label htmlFor={`${id}-${field.key}`}>{field.label}</label>
      <select id={`${id}-${field.key}`} value={value[field.key] === null ? "unlimit" : "limited"} disabled={disabled} onChange={event => onChange({ ...value, [field.key]: event.target.value === "unlimit" ? null : field.defaultValue })}>
        <option value="unlimit">Unlimit — без ограничений</option><option value="limited">Ограничить</option>
      </select>
      {value[field.key] !== null && <label className="mihomoSubscriptionLimitValue"><input type="number" aria-label={`${field.label}, ${field.unit}`} value={value[field.key] ?? ""} min={1} max={field.max} step={1} required disabled={disabled} onChange={event => onChange({ ...value, [field.key]: event.target.value === "" ? 0 : Number(event.target.value) })} /><span>{field.unit}</span></label>}
    </div>)}</div>
    {status && <div className="mihomoSubscriptionUsage" role="status" data-state={status.state}>
      <span>{status.enforced || status.used_bytes > 0 ? `Использовано: ${(status.used_bytes / 1000000000).toLocaleString("ru-RU", { maximumFractionDigits: 3 })} GB` : "Учёт начнётся после сохранения контроля подписки."}</span>
      {status.expires_at !== null && <span>Срок до {new Date(status.expires_at * 1000).toLocaleString("ru-RU")}</span>}
      {status.state === "expired" && <strong>Срок подписки истёк. Доступ остановлен.</strong>}
      {status.state === "exhausted" && <strong>Объём трафика исчерпан. Доступ остановлен.</strong>}
      {status.state === "unknown" && <strong>Актуальность серверного контроля не подтверждена.</strong>}
    </div>}
    <details className="mihomoSubscriptionHelp"><summary>Об ограничениях</summary>
      <p>Единые серверные ограничения для всех протоколов и устройств этого профиля, включая Relay и CDN.</p>
      {fields.map(field => <p key={field.key}><b>{field.label}.</b> {field.help}</p>)}
      <p>При исчерпании трафика или срока доступ останавливается для всей подписки. Unlimit снимает соответствующее ограничение. Учёт трафика сохраняется при изменении настроек.</p>
      <p>Учитывается трафик через VPN-сервер. Прямой трафик DIRECT на устройстве в квоту не входит.</p>
    </details>
  </section>;
}
