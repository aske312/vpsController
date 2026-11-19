import { useState } from "react";
import type { AlertWatch, AlertCatalog } from "./telegram-alerts";

const eventNames: Record<AlertWatch["events"][number], string> = { failed: "Ошибка", unknown: "Нет подтверждения", stalled: "Превышено время", succeeded: "Завершение", cancelled: "Отмена", recovered: "Восстановление" };

export function AlertWatches({ value, catalog, now, disabled, status, onChange }: {
  value: AlertWatch[]; catalog?: AlertCatalog; disabled: boolean;
  status?: Record<string, { checked_at?: number; active: number }>;
  now: number;
  onChange: (rules: AlertWatch[]) => void;
}) {
  const [expanded, setExpanded] = useState("");
  const change = (id: string, patch: Partial<AlertWatch>) => onChange(value.map(rule => rule.id === id ? { ...rule, ...patch } : rule));
  const add = () => {
    const id = crypto.randomUUID().replaceAll("-", "");
    onChange([...value, { id, name: "Новое правило", enabled: true, source: "operation", target: "", operation_id: null,
      events: ["failed", "unknown", "stalled"], after_seconds: 600, snoozed_until: 0 }]);
    setExpanded(id);
  };
  return <section className="alertRules"><header><h3>Операции и службы</h3><button type="button" disabled={disabled || value.length >= 32} onClick={add}>Добавить правило</button></header>
    {value.map(rule => <details className="alertWatch" key={rule.id} open={expanded === rule.id || undefined}>
      <summary><strong>{rule.name}</strong><span>{rule.source === "service" ? "Служба" : "Операция"} · {rule.enabled ? rule.snoozed_until > now ? "Пауза" : "Включено" : "Выключено"}{status?.[rule.id]?.active ? ` · Активных: ${status[rule.id].active}` : ""}</span></summary>
      <fieldset disabled={disabled}>
        <label className="noderaPermissionSwitch"><input type="checkbox" role="switch" checked={rule.enabled} onChange={event => change(rule.id, { enabled: event.target.checked })}/>Включено</label>
        <div className="alertFields"><label>Название<input value={rule.name} maxLength={80} required onChange={event => change(rule.id, { name: event.target.value })}/></label>
          <label>Источник<select value={rule.source} onChange={event => change(rule.id, { source: event.target.value as AlertWatch["source"], target: "", operation_id: null, events: event.target.value === "service" ? ["failed", "unknown", "recovered"] : ["failed", "unknown", "stalled"] })}><option value="operation">Операция</option><option value="service">Служба / процесс</option></select></label>
          {rule.source === "service" ? <label>Служба<select value={rule.target} required onChange={event => change(rule.id, { target: event.target.value })}><option value="">Выберите службу</option>{rule.target && !catalog?.services.some(row => row.id === rule.target) && <option value={rule.target}>{rule.target} · нет в каталоге</option>}{catalog?.services.map(row => <option key={row.id} value={row.id}>{row.name} · {row.unit}</option>)}</select></label>
            : <><label>Вид операции<input value={rule.target} list={`alert-actions-${rule.id}`} maxLength={80} placeholder="Все операции" pattern="[a-zA-Z0-9_-]*" onChange={event => change(rule.id, { target: event.target.value })}/><datalist id={`alert-actions-${rule.id}`}>{catalog?.operation_actions.map(action => <option key={action} value={action}/>)}</datalist></label>
              <label>ID операции<input value={rule.operation_id || ""} maxLength={32} pattern="[0-9a-f]{32}" placeholder="Все операции выбранного вида" onChange={event => change(rule.id, { operation_id: event.target.value || null })}/></label>
            </>}
          {(rule.source === "service" || rule.events.includes("stalled")) && <label>{rule.source === "service" ? "Нет данных дольше, минут" : "Дольше, минут"}<input type="number" min={1} max={1440} required value={rule.after_seconds / 60} onChange={event => change(rule.id, { after_seconds: Number(event.target.value) * 60 })}/></label>}
        </div>
        <div className="alertEvents">{Object.entries(eventNames).filter(([event]) => rule.source === "operation" || ["failed", "unknown", "recovered"].includes(event)).map(([key, label]) => <label key={key}><input type="checkbox" checked={rule.events.includes(key as AlertWatch["events"][number])} onChange={event => change(rule.id, { events: event.target.checked ? [...rule.events, key as AlertWatch["events"][number]] : rule.events.filter(item => item !== key) })}/>{label}</label>)}</div>
        <div className="buttonRow"><button type="button" onClick={() => change(rule.id, { snoozed_until: rule.snoozed_until > now ? 0 : now + 3600 })}>{rule.snoozed_until > now ? "Возобновить правило" : "Пауза на час"}</button><button type="button" onClick={() => onChange(value.filter(row => row.id !== rule.id))}>Удалить правило</button></div>
        {rule.snoozed_until > now && <small>Пауза до {new Date(rule.snoozed_until * 1000).toLocaleString()}</small>}
      </fieldset>
    </details>)}
  </section>;
}
