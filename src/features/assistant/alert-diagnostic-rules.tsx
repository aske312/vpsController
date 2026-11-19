import { useEffect, useRef, useState } from "react";
import type { createApiClient } from "../../shared/lib/api-request";
import { useNotifier, usePageError } from "../../shared/notifications/notification-center";

const defaults = {
  service_error: true, local_health: true, disk_pressure: true, http_error_rate: true,
  cpu_pressure: true, memory_pressure: true, load_pressure: true, application_recovery: true,
  relay_unavailable: true, relay_route: true, relay_address: true, connection_errors: true,
  cpu_percent: 90, memory_percent: 90, load_per_cpu: 1.5, connection_error_count: 10,
  disk_percent: 95, http_error_percent: 10, http_min_requests: 20, window_minutes: 5, trigger_samples: 2, recovery_samples: 2,
};
type Rules = typeof defaults & { revision: string };
type Toggle = { [K in keyof typeof defaults]: typeof defaults[K] extends boolean ? K : never }[keyof typeof defaults];
type Threshold = Exclude<keyof typeof defaults, Toggle>;
const limits: Record<Threshold, [number, number, number]> = {
  cpu_percent: [10, 100, 0.1], memory_percent: [10, 100, 0.1], disk_percent: [50, 100, 0.1], load_per_cpu: [0.1, 100, 0.1],
  http_error_percent: [0.1, 100, 0.1], connection_error_count: [1, 1000000, 1], http_min_requests: [1, 1000000, 1],
  window_minutes: [1, 60, 1], trigger_samples: [1, 60, 1], recovery_samples: [1, 60, 1],
};
const normalize = (value: Rules): Rules => ({ ...defaults, ...value });

export function AlertDiagnosticRules({ request, blocked }: { request: ReturnType<typeof createApiClient>; blocked: boolean }) {
  const [saved, setSaved] = useState<Rules>();
  const [draft, setDraft] = useState<Rules>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const client = useRef(request);
  const notify = useNotifier("alert-rules", "Условия алертов");
  const reload = () => { setBusy(true); setRevision(value => value + 1); };
  usePageError("alert-rules", "Условия алертов", error, { label: "Обновить", run: reload });
  useEffect(() => {
    client.current = request;
    const controller = new AbortController();
    void request<Rules>("/assistant/alerts/rules", { signal: controller.signal }).then(result => { const value = normalize(result); if (!controller.signal.aborted) { setSaved(value); setDraft(value); setError(""); } })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Условия недоступны"); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [request, revision]);
  const save = async () => {
    if (!draft || busy || blocked) return;
    if (Object.entries(limits).some(([key, [min, max, step]]) => {
      const value = draft[key as Threshold];
      return !Number.isFinite(value) || value < min || value > max || step === 1 && !Number.isInteger(value);
    })) { notify.error("Проверьте пороги и интервалы алертов."); return; }
    setBusy(true);
    try {
      const value = normalize(await request<Rules>("/assistant/alerts/rules", { method: "PUT", body: JSON.stringify(draft) }));
      if (client.current !== request) return;
      setSaved(value); setDraft(value); setError(""); notify.success("Условия алертов сохранены.");
    } catch (cause) { notify.error(cause instanceof Error ? cause.message : "Не удалось сохранить условия"); }
    finally { if (client.current === request) setBusy(false); }
  };
  if (!draft) return null;
  const input = (key: Threshold, title: string) => <input aria-label={title} type="number" min={limits[key][0]} max={limits[key][1]} step={limits[key][2]} value={draft[key]} required onChange={event => setDraft({ ...draft, [key]: Number(event.target.value) })}/>;
  const row = (key: Toggle, title: string, threshold?: Threshold, unit?: string) => <div className="alertCondition" key={key}>
    <label><input type="checkbox" role="switch" checked={draft[key]} onChange={event => setDraft({ ...draft, [key]: event.target.checked })}/>{title}</label>
    {threshold && <span className="alertThreshold">{input(threshold, title + ": порог")}<span>{unit}</span></span>}
  </div>;
  return <section className="alertRules alertBasicRules"><fieldset disabled={blocked || busy}>
    <details className="alertGroup" open><summary>Сервер</summary>
      {row('cpu_pressure', 'Загрузка CPU', 'cpu_percent', '%')}{row('memory_pressure', 'Занято RAM', 'memory_percent', '%')}
      {row('disk_pressure', 'Занято диска', 'disk_percent', '%')}{row('load_pressure', 'Нагрузка за 5 минут', 'load_per_cpu', '/ ядро')}
    </details>
    <details className="alertGroup"><summary>Приложение и компоненты</summary>
      {row('service_error', 'Сбои служб')}{row('local_health', 'Проверки компонентов')}
      {row('application_recovery', 'Требуется восстановление')}{row('http_error_rate', 'HTTP-ошибки', 'http_error_percent', '%')}
    </details>
    <details className="alertGroup"><summary>Relay и соединения</summary>
      {row('relay_unavailable', 'Связь с Relay Agent')}{row('relay_route', 'Состояние маршрутов')}
      {row('relay_address', 'Адреса подключения')}{row('connection_errors', 'Ошибки обмена за минуту', 'connection_error_count', 'ошибок')}
    </details>
    <details className="alertGroup"><summary>Дополнительно</summary><div className="alertFields">
      {([['http_min_requests', 'Минимум HTTP-запросов'], ['window_minutes', 'Окно HTTP, минут'], ['trigger_samples', 'Проверок для срабатывания'], ['recovery_samples', 'Проверок для восстановления']] as const).map(([key, title]) => <label key={key}>{title}{input(key, title)}</label>)}
    </div></details>
    <div className="buttonRow"><button type="button" disabled={JSON.stringify(saved) === JSON.stringify(draft)} onClick={() => void save()}>Сохранить условия</button>
      <button type="button" onClick={() => setDraft({ ...defaults, revision: draft.revision })}>Базовый набор</button>
      <button type="button" onClick={() => setDraft(saved)}>Сбросить</button><button type="button" onClick={reload}>Обновить условия</button></div>
  </fieldset></section>;
}
