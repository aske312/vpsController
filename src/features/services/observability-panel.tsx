"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createApiClient } from "../../shared/lib/api-request";

type Policy = { enabled: boolean; errors_only: boolean; retention_days: number; max_events: number; revision: number };
type Evidence = {
  settings: Policy;
  summary: { requests: number; errors: number; affected_profiles: number; first_error_at: number | null; last_error_at: number | null };
  subscriptions: { attempts: number; succeeded: number; failed: number };
  events: { id: number; at: number; request_id: string; service: string; route: string; method: string; status: number; duration_ms: number; reason: string; profile_id: string | null }[];
};
type Connections = { available: boolean; observed_active_channels?: number; known_established_connections?: number; established_connections: number | null; channels_without_connection_count?: number };
const at = (value: number | null) => value === null ? "—" : new Date(value * 1000).toLocaleString("ru-RU");

export function ObservabilityPanel({ request, busy, services = [] }: { request: ReturnType<typeof createApiClient>; busy: boolean; services?: { id: string; name: string }[] }) {
  const [data, setData] = useState<Evidence>();
  const [draft, setDraft] = useState<Policy>();
  const [connections, setConnections] = useState<Connections>();
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [minutes, setMinutes] = useState(60);
  const [service, setService] = useState("");
  const [identity, setIdentity] = useState("");
  const [errors, setErrors] = useState(false);
  const [journalService, setJournalService] = useState("application");
  const [priority, setPriority] = useState("warning");
  const [journal, setJournal] = useState<string[]>();
  const generation = useRef(0);
  const load = useCallback(async () => {
    const current = ++generation.current;
    setWorking(true);
    setError("");
    try {
      const query = new URLSearchParams({ minutes: String(minutes), errors: String(errors), limit: "60" });
      if (service) query.set("service", service);
      if (identity.trim()) query.set("request_id", identity.trim());
      const result = await request<Evidence>(`/observability/events?${query}`);
      if (current !== generation.current) return;
      setData(result);
      setDraft(previous => previous ?? result.settings);
    } catch (cause) {
      if (current === generation.current) setError(cause instanceof Error ? cause.message : "Журнал недоступен");
    } finally { if (current === generation.current) setWorking(false); }
  }, [request, minutes, service, identity, errors]);
  // Apply filters explicitly, so typing an ID does not send partial searches.
  const initialLoad = useRef(load);
  useEffect(() => {
    const counter = generation;
    void initialLoad.current();
    return () => { counter.current++; };
  }, []);
  const save = async () => {
    if (!draft) return;
    setWorking(true);
    setError("");
    try {
      const policy = await request<Policy>("/observability/settings", { method: "PUT", body: JSON.stringify(draft) });
      setDraft(policy);
      setData(previous => previous ? { ...previous, settings: policy } : previous);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось сохранить настройки"); }
    finally { setWorking(false); }
  };
  const observeConnections = async () => {
    setWorking(true);
    setError("");
    try { setConnections(await request<Connections>("/observability/connections")); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Соединения недоступны"); }
    finally { setWorking(false); }
  };
  const blocked = busy || working;
  const readJournal = async () => {
    setWorking(true);
    setError("");
    setJournal(undefined);
    try {
      const query = new URLSearchParams({ service: journalService, priority, minutes: String(minutes), limit: "120" });
      const result = await request<{ lines: string[] }>(`/observability/journal?${query}`);
      setJournal(result.lines);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Системный журнал недоступен"); }
    finally { setWorking(false); }
  };
  return <section className="servicesLog servicesObservability">
    <div className="operationsTitle"><p className="eyebrow">OBSERVABILITY</p><h3>Запросы и ошибки</h3></div>
    <div className="operationsBody">
      {error && <p role="alert">{error}</p>}
      <div className="servicesLogControls">
        <label className="opsField">Период<select value={minutes} disabled={blocked} onChange={event => setMinutes(Number(event.target.value))}><option value={60}>Час</option><option value={1440}>Сутки</option><option value={10080}>Неделя</option></select></label>
        <label className="opsField">Слой<select value={service} disabled={blocked} onChange={event => setService(event.target.value)}><option value="">Все</option><option value="panel">Панель</option><option value="mihomo">Mihomo</option></select></label>
        <label className="opsField">ID запроса<input value={identity} maxLength={32} placeholder="X-Request-ID" disabled={blocked} onChange={event => setIdentity(event.target.value)} /></label>
        <label><input type="checkbox" checked={errors} disabled={blocked} onChange={event => setErrors(event.target.checked)} /> Только ошибки в списке</label>
        <button type="button" className="ghostButton" disabled={blocked} onClick={() => void load()}>Обновить</button>
      </div>
      {data && <>
        <p>В сохранённом окне: запросов {data.summary.requests}, ошибок {data.summary.errors}, затронутых известных профилей {data.summary.affected_profiles}.</p>
        <p>Выдача конфигураций: успешно {data.subscriptions.succeeded}, ошибок {data.subscriptions.failed}. Первая ошибка: {at(data.summary.first_error_at)}; последняя: {at(data.summary.last_error_at)}.</p>
        <details><summary>Последние события · {data.events.length}</summary><div style={{ overflowX: "auto" }}><table><thead><tr><th>Время</th><th>Слой / запрос</th><th>Результат</th><th>ID запроса / профиль</th></tr></thead><tbody>{data.events.map(event => <tr key={event.id}><td>{at(event.at)}</td><td>{event.service}<br />{event.method} {event.route}</td><td>{event.status} · {event.duration_ms} мс<br />{event.reason}</td><td><code>{event.request_id}</code><br />{event.profile_id || "—"}</td></tr>)}</tbody></table></div>{!data.events.length && <p>Событий в выбранном окне нет.</p>}</details>
      </>}
      <p>Статистика ограничена сроком хранения и лимитом записей. Выдача подписки не подтверждает подключение клиента. HTTP-ошибка сама по себе не определяет сбой DNS, outbound или сети клиента.</p>
      <button type="button" className="ghostButton" disabled={blocked} onClick={() => void observeConnections()}>Проверить соединения</button>
      {connections && <p>{connections.available ? `Активных каналов: ${connections.observed_active_channels}. Установленных соединений: ${connections.established_connections ?? "полный счётчик недоступен"}; подтверждено доступными счётчиками: ${connections.known_established_connections}. Каналов без счётчика: ${connections.channels_without_connection_count}.` : "Наблюдения транспорта Mihomo недоступны."}</p>}
      <details><summary>Журналы сервисов и системы</summary>
        <div className="servicesLogControls">
          <label className="opsField">Источник<select value={journalService} disabled={blocked} onChange={event => { setJournalService(event.target.value); setJournal(undefined); }}>
            <option value="application">Приложение</option><option value="system">Система</option><option value="dns">DNS · systemd-resolved</option><option value="gateway">Gateway · Caddy</option>
            {services.filter(item => !["application", "system", "dns", "gateway"].includes(item.id)).map(item => <option value={item.id} key={item.id}>{item.name}</option>)}
          </select></label>
          <label className="opsField">Уровень и выше<select value={priority} disabled={blocked} onChange={event => { setPriority(event.target.value); setJournal(undefined); }}><option value="err">Ошибки</option><option value="warning">Предупреждения</option><option value="info">Информация</option><option value="debug">Отладка</option></select></label>
          <button type="button" className="ghostButton" disabled={blocked} onClick={() => void readJournal()}>Прочитать за выбранный период</button>
        </div>
        {journal && (journal.length ? <pre style={{ maxHeight: "24rem", overflow: "auto", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{journal.join("\n")}</pre> : <p>Записей по выбранным фильтрам нет.</p>)}
      </details>
      {draft && <form onSubmit={event => { event.preventDefault(); void save(); }}>
        <div className="servicesLogControls">
          <label><input type="checkbox" checked={draft.enabled} disabled={blocked} onChange={event => setDraft({ ...draft, enabled: event.target.checked })} /> Собирать события</label>
          <label><input type="checkbox" checked={draft.errors_only} disabled={blocked} onChange={event => setDraft({ ...draft, errors_only: event.target.checked })} /> Сохранять только ошибки</label>
          <label className="opsField">Хранить дней<input type="number" min={1} max={90} required value={draft.retention_days} disabled={blocked} onChange={event => setDraft({ ...draft, retention_days: Number(event.target.value) })} /></label>
          <label className="opsField">Лимит событий<input type="number" min={100} max={100000} required value={draft.max_events} disabled={blocked} onChange={event => setDraft({ ...draft, max_events: Number(event.target.value) })} /></label>
        </div>
        <p>Сокращение срока или лимита удаляет старые события. При сохранении только ошибок успешные выдачи в статистике не учитываются.</p>
        <div className="servicesLogActions"><button className="servicePrimary" disabled={blocked}>Сохранить сбор событий</button><button type="button" className="ghostButton" disabled={blocked || !data} onClick={() => setDraft(data?.settings)}>Отменить правки</button></div>
      </form>}
    </div>
  </section>;
}
