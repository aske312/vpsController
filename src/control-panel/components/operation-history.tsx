"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createApiClient } from "../../shared/lib/api-request";
import { safeDateTime } from "../../shared/lib/control-plane-ui";
import { notificationText } from "../../shared/notifications/operation-presentation";
import { historyEntries, historyKey, historyMatches, historySource, historyState, historyTitle, type HistoryEntry, type HistoryFilter } from "./operation-history-state";

type Request = ReturnType<typeof createApiClient>;
type Journal = { events: { at: string; state: string; progress?: number; message: string }[]; dropped: number; available?: boolean };
const filters: { id: HistoryFilter; label: string }[] = [{ id: "all", label: "Все" }, { id: "active", label: "В работе" }, { id: "attention", label: "Требуют внимания" }, { id: "completed", label: "Завершённые" }];

function OperationJournal({ request, entry }: { request: Request; entry: HistoryEntry }) {
  const [journal, setJournal] = useState<Journal | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const active = historyState(entry).tone === "active" || historyState(entry).tone === "unknown";
  useEffect(() => {
    let cancelled = false;
    let pending = false;
    async function load() {
      if (pending) return;
      pending = true; setLoading(true);
      try {
        const value = await request<Journal>(`/application/operations/${entry.id}/log?source=${encodeURIComponent(entry.source || "system")}`, { cache: "no-store" });
        if (!value || !Array.isArray(value.events) || value.events.some(event => !event || typeof event.at !== "string" || typeof event.state !== "string" || typeof event.message !== "string")) throw Error("Invalid journal");
        if (!cancelled) { setJournal(value); setError(""); }
      } catch { if (!cancelled) setError("Не удалось обновить журнал. Показаны последние полученные этапы."); }
      finally { pending = false; if (!cancelled) setLoading(false); }
    }
    void load();
    const timer = active ? window.setInterval(() => void load(), 5000) : undefined;
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [request, entry.id, entry.source, active, revision]);
  return <section className="operationJournal" aria-label="Этапы выбранной операции">
    <div className="operationJournalHeading"><h4>Этапы выполнения</h4><button type="button" disabled={loading} onClick={() => setRevision(value => value + 1)}>{loading ? "Загрузка…" : "Обновить этапы"}</button></div>
    {error && <p className="operationHistoryNotice" role="status">{error}</p>}
    {!journal && !error && <p role="status">Загружаем этапы операции…</p>}
    {journal?.available === false ? <p>Журнал ещё не создан или удалён по сроку хранения.</p> : journal?.events.length === 0 && <p>Записанных этапов пока нет.</p>}
    {Boolean(journal?.dropped) && <p className="operationHistoryNotice">Ранние этапы сокращены по лимиту журнала.</p>}
    <ol className="operationTimeline">{journal?.events.map((event, index) => <li key={`${event.at}:${index}`}>
      <div><time>{safeDateTime(event.at)}</time><span className={`operationState ${historyState({ state: event.state }).tone}`}>{historyState({ state: event.state }).label}</span></div>
      <p>{notificationText(event.message) || "Состояние обновлено"}</p>
    </li>)}</ol>
  </section>;
}

export function OperationHistory({ token }: { token: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState<{ owner: Request; items: HistoryEntry[]; at: Date } | null>(null);
  const [failure, setFailure] = useState<{ owner: Request; message: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<HistoryFilter>("all");
  const [selected, setSelected] = useState("");
  const request = useMemo(() => createApiClient(token), [token]);
  const items = snapshot?.owner === request ? snapshot.items : null;
  const error = failure?.owner === request ? failure.message : "";
  const visible = items?.filter(item => historyMatches(item, filter, query)) || [];
  const entry = visible.find(item => historyKey(item) === selected) || visible[0];
  useEffect(() => { if (open) dialog.current?.showModal(); }, [open]);
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true; setLoading(true);
      try {
        const value = await request<{ items: HistoryEntry[] }>("/application/operations", { cache: "no-store" });
        const entries = historyEntries(value);
        if (!cancelled) { setSnapshot({ owner: request, items: entries, at: new Date() }); setFailure(null); }
      } catch {
        if (!cancelled) setFailure({ owner: request, message: "История временно недоступна. Ранее полученные записи могут быть устаревшими." });
      } finally { pending = false; if (!cancelled) setLoading(false); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [open, request, revision]);
  function close() { dialog.current?.close(); setOpen(false); }
  const presentation = entry ? historyState(entry) : null;
  return <>
    <button type="button" className="ghostButton" onClick={() => setOpen(true)}>Операции</button>
    <dialog ref={dialog} className="operationHistoryDialog" aria-labelledby="operation-history-title" onCancel={close} onClose={() => setOpen(false)}>
      <div className="operationHistoryShell">
        <header className="operationHistoryHeader"><div><p className="eyebrow">OPERATIONS</p><h2 id="operation-history-title">История операций</h2><p>Выберите операцию, чтобы посмотреть результат и этапы выполнения.</p></div><button type="button" autoFocus onClick={close} aria-label="Закрыть историю операций">×</button></header>
        <div className="operationHistoryTools">
          <label className="operationHistorySearch"><span>Найти операцию</span><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Название, сообщение или ID" /></label>
          <button type="button" disabled={loading} onClick={() => setRevision(value => value + 1)}>{loading ? "Обновляем…" : "Обновить историю"}</button>
        </div>
        <nav className="operationHistoryFilters" aria-label="Фильтр состояния операций">{filters.map(item => <button key={item.id} type="button" aria-pressed={filter === item.id} onClick={() => setFilter(item.id)}>{item.label}<span>{items?.filter(entry => historyMatches(entry, item.id, "")).length ?? "—"}</span></button>)}</nav>
        {error && <p className="operationHistoryNotice" role="status">{error}</p>}
        <div className="operationHistoryWorkspace">
          <section className="operationHistoryList" aria-label="Список операций" aria-busy={loading}>
            <div className="operationHistoryListHeading"><span>{visible.length} из {items?.length ?? "—"}</span><span>Сначала новые</span></div>
            {!items && !error && <p className="operationHistoryEmpty" role="status">Загрузка истории…</p>}
            {items?.length === 0 && <p className="operationHistoryEmpty">Сохранённых операций пока нет.</p>}
            {Boolean(items?.length) && visible.length === 0 && <div className="operationHistoryEmpty"><p>По вашему запросу операций нет.</p><button type="button" onClick={() => { setFilter("all"); setQuery(""); }}>Сбросить фильтры</button></div>}
            {visible.map(item => <button type="button" className="operationHistoryItem" key={historyKey(item)} aria-pressed={entry && historyKey(entry) === historyKey(item)} onClick={() => setSelected(historyKey(item))}>
              <span className={`operationState ${historyState(item).tone}`}>{historyState(item).label}</span><strong>{historyTitle(item)}</strong>
              <span className="operationHistoryItemMeta"><time>{safeDateTime(item.started_at || item.updated_at)}</time><span>{historySource(item)}</span></span>
              {item.message && <span className="operationHistoryExcerpt">{notificationText(item.message)}</span>}
            </button>)}
          </section>
          <section className="operationHistoryDetail" aria-label="Выбранная операция" aria-live="polite">
            {entry && presentation ? <>
              <div className="operationHistoryDetailHeading"><span className={`operationState ${presentation.tone}`}>{presentation.label}</span><h3>{historyTitle(entry)}</h3><p>{historySource(entry)} · {safeDateTime(entry.started_at || entry.updated_at)}</p></div>
              {entry.message && <p className={`operationHistoryResult ${presentation.tone}`}>{notificationText(entry.message)}</p>}
              {presentation.tone === "active" && Number.isFinite(entry.progress) && <div className="operationHistoryProgress"><progress max={100} value={Math.max(0, Math.min(100, entry.progress!))} aria-label="Ход операции" /><span>{Math.round(Math.max(0, Math.min(100, entry.progress!)))}%</span></div>}
              {open && entry.id && <OperationJournal key={`${token}:${historyKey(entry)}`} request={request} entry={entry} />}
              <details className="operationHistoryTechnical"><summary>Технические сведения</summary><dl>{entry.id && <><dt>ID операции</dt><dd><code>{entry.id}</code></dd></>}{entry.unit && <><dt>Процесс</dt><dd><code>{entry.unit}</code></dd></>}<dt>Обновлена</dt><dd>{safeDateTime(entry.updated_at || entry.started_at)}</dd></dl></details>
            </> : <p className="operationHistoryEmpty">{items?.length ? "Выберите другой фильтр или измените запрос." : "Здесь появятся результат и этапы выбранной операции."}</p>}
          </section>
        </div>
        <footer className="operationHistoryFooter"><span>Последние 100 записей · автообновление каждые 10 с{snapshot?.owner === request ? ` · ${snapshot.at.toLocaleTimeString("ru-RU")}` : ""}</span><span>Закрытие окна не останавливает операции.</span></footer>
      </div>
    </dialog>
  </>;
}
