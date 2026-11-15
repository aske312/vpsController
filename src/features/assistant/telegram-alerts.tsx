import { useCallback, useEffect, useRef, useState } from "react";
import type { createApiClient } from "../../shared/lib/api-request";

type Config = { enabled: boolean; owner_id: number | null; failures: boolean; recoveries: boolean; boots: boolean };
type Status = { settings: Config; revision: string; owners: { telegram_id: number; name: string }[]; token_configured: boolean; pending: number; last_delivery: { state: string; at: number } | null };
const deliveryNames: Record<string, string> = { sent: "Доставлено", pending: "Ожидание после ограничения Telegram", unknown: "Результат неизвестен — повтор не выполняется", failed: "Ошибка доставки", token_missing: "Нет токена бота" };

export function TelegramAlerts({ request, refreshKey, blocked = false }: { request: ReturnType<typeof createApiClient>; refreshKey: number; blocked?: boolean }) {
  const [status, setStatus] = useState<Status>();
  const [draft, setDraft] = useState<Config>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const editing = useRef(false);
  const generation = useRef(0);
  const edit = (next: Config) => {
    editing.current = JSON.stringify(next) !== JSON.stringify(status?.settings);
    setDraft(next);
  };
  const load = useCallback(async (signal?: AbortSignal) => {
    const current = ++generation.current;
    const value = await request<Status>("/assistant/alerts", { signal });
    if (current !== generation.current || signal?.aborted) return;
    editing.current = false; setStatus(value); setDraft(value.settings);
  }, [request]);
  useEffect(() => {
    const controller = new AbortController();
    const current = ++generation.current;
    void request<Status>("/assistant/alerts", { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted && current === generation.current && !editing.current) { setStatus(value); setDraft(value.settings); }
    }).catch(cause => { if (!controller.signal.aborted) setError(String(cause)); });
    return () => controller.abort();
  }, [request, refreshKey]);
  const act = async (test = false) => {
    if (!draft || !status || blocked) return;
    generation.current++;
    setBusy(true); setError(""); setNotice("");
    try {
      if (test) { await request("/assistant/alerts/test", { method: "POST" }); setNotice("Тест добавлен в очередь. Статус доставки можно обновить."); }
      else { const value = await request<Status>("/assistant/alerts", { method: "PUT", body: JSON.stringify({ ...draft, expected_revision: status.revision }) }); editing.current = false; setStatus(value); setDraft(value.settings); setNotice("Настройки алертов сохранены."); }
    } catch (cause) { setError(String(cause)); } finally { setBusy(false); }
  };
  const refresh = async () => {
    setBusy(true); setError("");
    try { await load(); } catch (cause) { setError(String(cause)); } finally { setBusy(false); }
  };
  const dirty = JSON.stringify(draft) !== JSON.stringify(status?.settings);
  return <section className="noderaSettings noderaOwnerAlerts"><header className="noderaBlockHead"><h2>Уведомления владельцу</h2></header>
    <p>Сбои, восстановление и новая загрузка VPS. Работают независимо от AI и диалога с ботом. Полную недоступность VPS можно обнаружить только после восстановления.</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {draft && status && <><label className="noderaPermissionSwitch"><input type="checkbox" role="switch" checked={draft.enabled} disabled={busy || blocked} onChange={event => edit({ ...draft, enabled: event.target.checked })}/>Включить алерты</label>
      <label>Получатель<select value={draft.owner_id ?? ""} disabled={busy || blocked} onChange={event => edit({ ...draft, owner_id: event.target.value ? Number(event.target.value) : null })}><option value="">Выберите владельца</option>{status.owners.map(owner => <option key={owner.telegram_id} value={owner.telegram_id}>{owner.name || owner.telegram_id}</option>)}</select></label>
      <small>Список использует сохранённые роли. Токен бота: {status.token_configured ? "настроен" : "не настроен"}.</small>
      <div className="noderaPermissionList">{([['failures', 'Сбои'], ['recoveries', 'Восстановление'], ['boots', 'Загрузка VPS']] as const).map(([key, title]) => <label className="noderaPermissionSwitch" key={key}><input type="checkbox" role="switch" checked={draft[key]} disabled={busy || blocked} onChange={event => edit({ ...draft, [key]: event.target.checked })}/>{title}</label>)}</div>
      <p>В очереди: {status.pending}. {status.last_delivery ? `${deliveryNames[status.last_delivery.state] ?? status.last_delivery.state} · ${new Date(status.last_delivery.at * 1000).toLocaleString()}` : "Доставок пока нет"}</p>
      <div className="buttonRow"><button type="button" disabled={busy || blocked || !dirty} onClick={() => void act()}>Сохранить алерты</button><button type="button" disabled={busy || blocked || dirty || !status.settings.enabled} onClick={() => void act(true)}>Тест доставки</button><button type="button" disabled={busy} onClick={() => void refresh()}>{dirty ? "Отменить правки и обновить" : "Обновить статус"}</button></div>
    </>}
  </section>;
}
