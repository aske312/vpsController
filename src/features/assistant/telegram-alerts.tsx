import { useCallback, useEffect, useRef, useState } from "react";
import type { createApiClient } from "../../shared/lib/api-request";
import { useNotifier, usePageError } from "../../shared/notifications/notification-center";
import { AlertWatches } from "./alert-watches";
import { AlertDiagnosticRules } from "./alert-diagnostic-rules";

export type AlertWatch = { id: string; name: string; enabled: boolean; source: "operation" | "service"; target: string; operation_id: string | null;
  events: ("failed" | "unknown" | "stalled" | "succeeded" | "cancelled" | "recovered")[]; after_seconds: number; snoozed_until: number };
export type AlertCatalog = { services: { id: string; name: string; unit: string }[]; operation_actions: string[] };
type Config = { enabled: boolean; owner_id: number | null; failures: boolean; recoveries: boolean; boots: boolean;
  operations: boolean; operation_timeout_seconds: number; delivery_interval_seconds: number; snoozed_until: number; watches: AlertWatch[] };
type Status = { settings: Config; revision: string; owners: { telegram_id: number; name: string }[]; token_configured: boolean; pending: number; last_delivery: { state: string; at: number } | null;
  dropped?: number; deliveries?: { id: string; at: number; state: string; zone: string; text: string }[]; watch_status?: Record<string, { checked_at?: number; active: number }> };
const alertDefaults = { operations: false, operation_timeout_seconds: 600, delivery_interval_seconds: 10, snoozed_until: 0, watches: [] };
const normalizeStatus = (value: Status): Status => ({ ...value, settings: { ...alertDefaults, ...value.settings } });
const deliveryNames: Record<string, string> = { sent: "Доставлено", pending: "Ожидание после ограничения Telegram", unknown: "Результат неизвестен — повтор не выполняется", failed: "Ошибка доставки", token_missing: "Нет токена бота" };

export function TelegramAlerts({ request, refreshKey, blocked = false }: { request: ReturnType<typeof createApiClient>; refreshKey: number; blocked?: boolean }) {
  const [status, setStatus] = useState<Status>();
  const [draft, setDraft] = useState<Config>();
  const [busy, setBusy] = useState(false);
  const [catalog, setCatalog] = useState<AlertCatalog>();
  const [catalogError, setCatalogError] = useState("");
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  const [error, setError] = useState("");
  const notify = useNotifier("telegram-alerts", "Telegram · оповещения");
  usePageError("telegram-alerts", "Telegram · оповещения", error);
  usePageError("alert-catalog", "Каталог алертов", catalogError);
  const editing = useRef(false);
  const working = useRef(false);
  const generation = useRef(0);
  const edit = (next: Config) => {
    editing.current = JSON.stringify(next) !== JSON.stringify(status?.settings);
    setDraft(next);
  };
  const load = useCallback(async (signal?: AbortSignal) => {
    const current = ++generation.current;
    const value = normalizeStatus(await request<Status>("/assistant/alerts", { signal }));
    if (current !== generation.current || signal?.aborted) return;
    editing.current = false; setStatus(value); setDraft(value.settings);
    setNow(Math.floor(Date.now() / 1000));
  }, [request]);
  useEffect(() => {
    const controller = new AbortController();
    let reading = false;
    const read = () => {
      if (working.current || reading) return;
      reading = true;
      const current = ++generation.current;
      void request<Status>("/assistant/alerts", { signal: controller.signal }).then(normalizeStatus).then(value => {
      if (!controller.signal.aborted && current === generation.current) {
        setError("");
        setNow(Math.floor(Date.now() / 1000));
        setStatus(previous => editing.current && previous ? { ...value, settings: previous.settings, revision: previous.revision } : value);
        if (!editing.current) setDraft(value.settings);
      }
      }).catch(cause => { if (!controller.signal.aborted && current === generation.current) setError(String(cause)); })
        .finally(() => { reading = false; });
    };
    read();
    const timer = typeof window === "undefined" ? undefined : window.setInterval(() => { if (document.visibilityState !== "hidden") read(); }, 15000);
    return () => { controller.abort(); if (timer !== undefined) window.clearInterval(timer); };
  }, [request, refreshKey]);
  useEffect(() => {
    const controller = new AbortController();
    void request<AlertCatalog>("/assistant/alerts/catalog", { signal: controller.signal }).then(value => { if (!controller.signal.aborted) { setCatalog(value); setCatalogError(""); } })
      .catch(cause => { if (!controller.signal.aborted) setCatalogError(cause instanceof Error ? cause.message : "Каталог недоступен"); });
    return () => controller.abort();
  }, [request]);
  const act = async (test = false) => {
    if (!draft || !status || blocked || working.current) return;
    if (draft.watches.some(rule => !rule.name.trim() || !rule.events.length || rule.source === "service" && !rule.target)) { notify.error("Укажите название, источник и события для каждого правила."); return; }
    generation.current++;
    working.current = true;
    setBusy(true);
    try {
      if (test) { await request("/assistant/alerts/test", { method: "POST" }); notify.success("Тест добавлен в очередь."); }
      else { const value = normalizeStatus(await request<Status>("/assistant/alerts", { method: "PUT", body: JSON.stringify({ ...draft, expected_revision: status.revision }) })); editing.current = false; setStatus(value); setDraft(value.settings); setError(""); notify.success("Настройки алертов сохранены."); }
    } catch (cause) { notify.error(String(cause)); } finally { working.current = false; setBusy(false); }
  };
  const refresh = async () => {
    if (working.current) return;
    working.current = true;
    setBusy(true); setError("");
    try { await load(); setError(""); } catch (cause) { setError(String(cause)); } finally { working.current = false; setBusy(false); }
  };
  const dirty = JSON.stringify(draft) !== JSON.stringify(status?.settings);
  return <div className="noderaOwnerAlerts">

    {draft && status && <><label className="noderaPermissionSwitch"><input type="checkbox" role="switch" checked={draft.enabled} disabled={busy || blocked} onChange={event => edit({ ...draft, enabled: event.target.checked })}/>Включить алерты</label>
      <label>Получатель<select value={draft.owner_id ?? ""} disabled={busy || blocked} onChange={event => edit({ ...draft, owner_id: event.target.value ? Number(event.target.value) : null })}><option value="">Владелец или администратор</option>{status.owners.map(owner => <option key={owner.telegram_id} value={owner.telegram_id}>{owner.name || owner.telegram_id}</option>)}</select></label>
      <p>Токен бота: {status.token_configured ? "настроен" : "не настроен"}</p>
      <div className="noderaPermissionList">{([['failures', 'Диагностические сбои'], ['recoveries', 'Восстановление диагностики'], ['boots', 'Загрузка VPS'], ['operations', 'Сбои и зависания операций']] as const).map(([key, title]) => <label className="noderaPermissionSwitch" key={key}><input type="checkbox" role="switch" checked={draft[key]} disabled={busy || blocked} onChange={event => edit({ ...draft, [key]: event.target.checked })}/>{title}</label>)}</div>
      <div className="alertFields"><label>Интервал доставки, секунд<input type="number" min={10} max={3600} value={draft.delivery_interval_seconds} disabled={busy || blocked} onChange={event => edit({ ...draft, delivery_interval_seconds: Number(event.target.value) })}/></label>
        {draft.operations && <label>Длительная операция, минут<input type="number" min={1} max={1440} value={draft.operation_timeout_seconds / 60} disabled={busy || blocked} onChange={event => edit({ ...draft, operation_timeout_seconds: Number(event.target.value) * 60 })}/></label>}</div>
      <div className="buttonRow"><button type="button" disabled={busy || blocked} onClick={() => edit({ ...draft, snoozed_until: draft.snoozed_until > now ? 0 : Math.floor(Date.now() / 1000) + 3600 })}>{draft.snoozed_until > now ? "Возобновить алерты" : "Пауза на час"}</button>{draft.snoozed_until > now && <time>До {new Date(draft.snoozed_until * 1000).toLocaleString()}</time>}</div>
      <AlertWatches value={draft.watches} catalog={catalog} now={now} disabled={busy || blocked} status={status.watch_status} onChange={watches => edit({ ...draft, watches })}/>
      <p>В очереди: {status.pending}. {status.last_delivery ? `${deliveryNames[status.last_delivery.state] ?? status.last_delivery.state} · ${new Date(status.last_delivery.at * 1000).toLocaleString()}` : "Доставок пока нет"}</p>
      <div className="buttonRow"><button type="button" disabled={busy || blocked || !dirty} onClick={() => void act()}>Сохранить алерты</button><button type="button" disabled={busy || blocked || dirty || !status.settings.enabled} onClick={() => void act(true)}>Тест доставки</button><button type="button" disabled={busy} onClick={() => void refresh()}>{dirty ? "Отменить правки и обновить" : "Обновить статус"}</button></div>
      <AlertDiagnosticRules request={request} blocked={busy || blocked}/>
      <section className="alertDeliveryHistory"><h3>Доставка</h3><dl className="alertDeliveryCounters"><div><dt>В очереди</dt><dd>{status.pending}</dd></div><div><dt>Не помещено в очередь</dt><dd>{status.dropped ?? 0}</dd></div></dl>
        {status.deliveries?.map(row => <details key={row.id}><summary><strong>{row.zone}</strong><span>{deliveryNames[row.state] || row.state}</span><time>{new Date(row.at * 1000).toLocaleString()}</time></summary><pre>{row.text}</pre></details>)}
      </section>
    </>}
  </div>;
}
