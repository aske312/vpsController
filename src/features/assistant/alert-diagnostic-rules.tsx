import { useEffect, useRef, useState } from "react";
import type { createApiClient } from "../../shared/lib/api-request";
import { useNotifier, usePageError } from "../../shared/notifications/notification-center";

type Rules = { revision: string; service_error: boolean; local_health: boolean; disk_pressure: boolean; http_error_rate: boolean;
  disk_percent: number; http_error_percent: number; http_min_requests: number; window_minutes: number; trigger_samples: number; recovery_samples: number };

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
    void request<Rules>("/assistant/alerts/rules", { signal: controller.signal }).then(value => { if (!controller.signal.aborted) { setSaved(value); setDraft(value); setError(""); } })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Условия недоступны"); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [request, revision]);
  const save = async () => {
    if (!draft || busy || blocked) return;
    setBusy(true);
    try {
      const value = await request<Rules>("/assistant/alerts/rules", { method: "PUT", body: JSON.stringify(draft) });
      if (client.current !== request) return;
      setSaved(value); setDraft(value); setError(""); notify.success("Условия алертов сохранены.");
    } catch (cause) { notify.error(cause instanceof Error ? cause.message : "Не удалось сохранить условия"); }
    finally { if (client.current === request) setBusy(false); }
  };
  if (!draft) return null;
  return <section className="alertRules"><h3>Диагностические условия</h3><fieldset disabled={blocked || busy}>
    <div className="alertEvents">{([['service_error', 'Сбои служб'], ['local_health', 'Проверки компонентов'], ['disk_pressure', 'Заполнение диска'], ['http_error_rate', 'HTTP-ошибки']] as const).map(([key, title]) => <label key={key}><input type="checkbox" role="switch" checked={draft[key]} onChange={event => setDraft({ ...draft, [key]: event.target.checked })}/>{title}</label>)}</div>
    <div className="alertFields">{([
      ['disk_percent', 'Диск, %', 50, 100], ['http_error_percent', 'HTTP-ошибки, %', 0.1, 100], ['http_min_requests', 'Минимум запросов', 1, 1000000],
      ['window_minutes', 'Окно, минут', 1, 60], ['trigger_samples', 'Проверок для срабатывания', 1, 60], ['recovery_samples', 'Проверок для восстановления', 1, 60],
    ] as const).map(([key, title, min, max]) => <label key={key}>{title}<input type="number" min={min} max={max} step={key.endsWith('percent') ? 0.1 : 1} value={draft[key]} required onChange={event => setDraft({ ...draft, [key]: Number(event.target.value) })}/></label>)}</div>
    <div className="buttonRow"><button type="button" disabled={JSON.stringify(saved) === JSON.stringify(draft)} onClick={() => void save()}>Сохранить условия</button><button type="button" onClick={() => setDraft(saved)}>Сбросить</button><button type="button" onClick={reload}>Обновить условия</button></div>
  </fieldset></section>;
}
