"use client";

import { useEffect, useState } from "react";
import type { createApiClient } from "../../shared/lib/api-request";
import { NotificationNotice } from "../../shared/notifications/notification-center";
import { ChatIcon } from "./chat-icon";

export type UsageRow = { actor: string; channel: string; provider: string; model: string; requests: number;
  completed: number; incomplete: number; failed: number; unknown: number; model_calls: number;
  reported_calls: number; prompt_tokens: number; completion_tokens: number; retries: number; unmeasured_requests: number };
type Report = { days: number; earliest_record_at: number | null; retention_days: number; rows: UsageRow[]; cost_available: boolean };
const counters = ["requests", "completed", "incomplete", "failed", "unknown", "model_calls", "reported_calls", "prompt_tokens", "completion_tokens", "retries", "unmeasured_requests"] as const;

export function checkedUsageReport(value: unknown): Report {
  const report = value as Report | null;
  if (!report || !Number.isInteger(report.days) || report.days < 1 || report.days > 90
      || !Number.isInteger(report.retention_days) || report.retention_days < 1 || report.retention_days > 90
      || report.earliest_record_at !== null && (typeof report.earliest_record_at !== "number" || !Number.isFinite(report.earliest_record_at) || report.earliest_record_at < 0)
      || !Array.isArray(report.rows) || report.rows.some(row => !row || typeof row.actor !== "string" || !/^(panel|[1-9][0-9]{0,15})$/.test(row.actor)
        || !["panel", "bot"].includes(row.channel) || typeof row.model !== "string" || row.model.length > 160
        || typeof row.provider !== "string" || !/^(?:[a-f0-9]{32})?$/.test(row.provider)
        || counters.some(key => !Number.isSafeInteger(row[key]) || row[key] < 0))) {
    throw new Error("Статистика AI получена в неверном формате");
  }
  return report;
}

export function usageByUser(rows: UsageRow[]) {
  const users = new Map<string, UsageRow & { models: string[] }>();
  for (const row of rows) {
    const previous = users.get(row.actor);
    if (!previous) users.set(row.actor, { ...row, models: row.model ? [row.model] : [] });
    else {
      for (const key of counters) previous[key] += row[key];
      if (row.model && !previous.models.includes(row.model)) previous.models.push(row.model);
    }
  }
  return [...users.values()].sort((a, b) => b.requests - a.requests || a.actor.localeCompare(b.actor));
}

export function AIUsage({ request, names }: { request: ReturnType<typeof createApiClient>; names: Record<string, string> }) {
  const [open, setOpen] = useState(false);
  const [days, setDays] = useState(30);
  const [refresh, setRefresh] = useState(0);
  const [snapshot, setSnapshot] = useState<{ request: typeof request; days: number; refresh: number; data?: Report; error?: string }>();
  const ready = snapshot?.request === request && snapshot.days === days && snapshot.refresh === refresh;
  const data = ready ? snapshot.data : undefined;
  const error = ready ? snapshot.error : "";
  const loading = open && !ready;
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    void request<Report>(`/assistant/usage?days=${days}`, { signal: controller.signal, cache: "no-store" }).then(value => {
      if (!controller.signal.aborted) setSnapshot({ request, days, refresh, data: checkedUsageReport(value) });
    }).catch(cause => {
      if (!controller.signal.aborted) setSnapshot({ request, days, refresh, error: cause instanceof Error ? cause.message : "Статистика недоступна" });
    });
    return () => controller.abort();
  }, [request, open, days, refresh]);
  const users = data?.days === days ? usageByUser(data.rows) : [];
  return <details className="noderaUsage" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Статистика AI <span>по пользователям</span></summary>
    {open && <div className="noderaUsageBody">
      <div className="noderaUsageToolbar"><label>Период<select value={days} onChange={event => setDays(Number(event.target.value))}>
        <option value={1}>24 часа</option><option value={7}>7 дней</option><option value={30}>30 дней</option><option value={90}>90 дней</option>
      </select></label><button type="button" className="mcpIconButton" aria-label="Обновить статистику AI" title="Обновить" disabled={loading} onClick={() => setRefresh(value => value + 1)}><ChatIcon name="refresh"/></button></div>
      {error && <NotificationNotice id="nodera:usage" title="Статистика AI" message={error}/>}
      {loading && <p className="mcpFieldHint" role="status">Загружаю статистику…</p>}
      {!loading && !error && !users.length && <p className="mcpFieldHint">За этот период запросов не учтено.</p>}
      {users.length > 0 && <AIUsageTable users={users} names={names}/>}
      <details className="noderaUsageNotes"><summary>Как считается</summary>
        <p className="mcpFieldHint">Один запрос может включать несколько обращений к модели. Токены — по данным провайдера, включая известный расход до ошибок. Неполный учёт не означает нулевой расход.</p>
        <p className="mcpFieldHint">Денежные списания недоступны. Лимиты пользователей не включены.</p>
        {data?.earliest_record_at && <p className="mcpFieldHint">Данные с {new Date(data.earliest_record_at * 1000).toLocaleDateString("ru-RU")} · хранение {data.retention_days} дней. Старые запросы — в пределах сохранённых журналов.</p>}
      </details>
    </div>}
  </details>;
}

export function AIUsageTable({ users, names }: { users: ReturnType<typeof usageByUser>; names: Record<string, string> }) {
  const number = (value: number) => value.toLocaleString("ru-RU");
  return <div className="noderaTableScroll" tabIndex={0} role="region" aria-label="Расход AI по пользователям"><table className="noderaAccessTable">
    <thead><tr><th>Пользователь</th><th>Запросы</th><th>Токены</th><th>Результаты</th></tr></thead>
    <tbody>{users.map(row => <tr key={row.actor}>
      <th scope="row"><strong title={`${row.actor === "panel" ? "Администратор панели" : row.actor} · ${row.models.join(" · ") || "Модель не сохранена"}`}>{row.actor === "panel" ? "Панель" : names[row.actor] || row.actor}</strong>
        {row.actor !== "panel" && names[row.actor] && <small>{row.actor}</small>}</th>
      <td><strong>{number(row.requests)}</strong><small title="Обращения к модели, включая шаги агента">К модели: {number(row.model_calls)}</small>{row.retries > 0 && <small>Повторов: {number(row.retries)}</small>}</td>
      <td>{row.reported_calls ? <><strong>{number(row.prompt_tokens + row.completion_tokens)}</strong><small title="Входящие / исходящие токены">вх. {number(row.prompt_tokens)} · вых. {number(row.completion_tokens)}</small></> : "—"}
        {row.unmeasured_requests > 0 && <small className="noderaUsageIncomplete">Учёт неполный</small>}</td>
      <td><span>Ответов: {number(row.completed)}</span><div className="noderaUsageIssues">{row.incomplete > 0 && <small>Неполных: {number(row.incomplete)}</small>}
        {row.failed > 0 && <small>Ошибок: {number(row.failed)}</small>}{row.unknown > 0 && <small title="Запросы без итогового статуса">Без итога: {number(row.unknown)}</small>}</div></td>
    </tr>)}</tbody>
  </table></div>;
}
