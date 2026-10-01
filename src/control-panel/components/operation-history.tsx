"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createApiClient } from "../../shared/lib/api-request";
import { actionLabels, safeDateTime } from "../../shared/lib/control-plane-ui";
import type { SystemAction } from "../system-operation";

type Entry = SystemAction & { source?: string; updated_at?: string };
type Journal = { events: { at: string; state: string; progress?: number; message: string }[]; dropped: number; available?: boolean };

function OperationJournal({ request, entry }: { request: ReturnType<typeof createApiClient>; entry: Entry }) {
  const [journal, setJournal] = useState<Journal | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  async function load() {
    if (loading) return;
    setLoading(true);
    try {
      setJournal(await request<Journal>(`/application/operations/${entry.id}/log?source=${entry.source || "system"}`));
      setError("");
    } catch { setError("Не удалось обновить журнал. Ранее полученные этапы могут быть устаревшими."); }
    finally { setLoading(false); }
  }
  return <details onToggle={(event) => { if (event.currentTarget.open && !journal) void load(); }}>
    <summary>Этапы операции</summary>
    <button type="button" disabled={loading} onClick={() => void load()}>{loading ? "Загрузка…" : "Обновить журнал"}</button>
    {error && <p role="status">{error}</p>}
    {journal?.available === false && <p>Журнал ещё не создан или удалён по сроку хранения.</p>}
    {Boolean(journal?.dropped) && <p>Ранние этапы сокращены по лимиту журнала.</p>}
    <ol>{journal?.events.map((event, index) => <li key={`${event.at}:${index}`}><small>{safeDateTime(event.at)} · {event.state}</small><p>{event.message}</p></li>)}</ol>
  </details>;
}
const states: Record<string, string> = { queued: "В очереди", running: "Выполняется", active: "Выполняется", activating: "Запускается", unknown: "Результат неизвестен", succeeded: "Завершена", done: "Завершена", failed: "Ошибка", cancelled: "Отменена", rebooting: "Перезагрузка", "powering-off": "Выключение" };

export function OperationHistory({ token }: { token: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Entry[] | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const request = useMemo(() => createApiClient(token), [token]);
  useEffect(() => {
    if (!open) return;
    dialog.current?.showModal();
    let cancelled = false;
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const value = await request<{items: Entry[]}>("/application/operations");
        if (!cancelled) { setItems(value.items); setError(""); }
      } catch {
        if (!cancelled) setError("История временно недоступна. Ранее полученные записи могут быть устаревшими.");
      } finally { pending = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [open, request, revision]);
  function close() { dialog.current?.close(); setOpen(false); }
  return <>
    <button type="button" className="ghostButton" onClick={() => setOpen(true)}>Операции</button>
    <dialog ref={dialog} className="operationHistoryDialog" aria-labelledby="operation-history-title" onCancel={close} onClose={() => setOpen(false)}>
      <header><div><h2 id="operation-history-title">История операций</h2><p>Закрытие окна не останавливает выполнение.</p></div><button type="button" autoFocus onClick={close} aria-label="Закрыть историю операций">×</button></header>
      <nav><span>Последние 100 записей</span><button type="button" onClick={() => setRevision((value) => value + 1)}>Обновить</button></nav>
      {error && <p role="status">{error}</p>}
      <div className="operationHistoryList" aria-busy={!items && !error}>
        {!items && !error && <p>Загрузка истории…</p>}
        {items?.length === 0 && <p>Сохранённых операций пока нет.</p>}
        {items?.map((item) => {
          const [kind, component] = (item.action || "").split(":", 2);
          return <article key={`${item.source || "system"}:${item.id || item.unit || item.started_at}`}>
            <div><strong>{actionLabels[kind] || kind || "Операция"}{component ? ` · ${component}` : ""}</strong><span>{states[item.state || "unknown"] || "Результат неизвестен"}</span></div>
            <small>{safeDateTime(item.started_at || item.updated_at)} · {item.source || "system"}</small>
            {item.message && <p>{item.message}</p>}
            {item.id && <details><summary>Идентификатор операции</summary><code>{item.id}</code></details>}
            {item.id && <OperationJournal key={`${token}:${item.source}:${item.id}`} request={request} entry={item} />}
          </article>;
        })}
      </div>
    </dialog>
  </>;
}
