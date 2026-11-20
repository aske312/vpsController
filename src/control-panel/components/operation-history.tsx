"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createApiClient } from "../../shared/lib/api-request";
import { safeDateTime } from "../../shared/lib/control-plane-ui";
import { notificationText } from "../../shared/notifications/operation-presentation";
import { useNotifier, usePageError } from "../../shared/notifications/notification-center";
import { historyDuration, historyEntries, historyErrorCount, historyKey, historyMatches, historyPage, historySource, historyState, historyTitle, type HistoryEntry, type HistoryFilter } from "./operation-history-state";
import type { SystemAction } from "../system-operation";
import { OperationSavedJournal, type SavedOperationJournal } from "./operation-saved-journal";

type Request = ReturnType<typeof createApiClient>;
type Journal = { events: { at: string; state: string; progress?: number; message: string; stage?: string; result?: string; error_code?: string; command?: string; dependency?: string; exit_code?: number; duration_ms?: number }[]; dropped: number; available?: boolean; evidence?: SavedOperationJournal };
const filters: { id: HistoryFilter; label: string }[] = [{ id: "all", label: "Все" }, { id: "active", label: "В работе" }, { id: "attention", label: "Требуют внимания" }, { id: "warnings", label: "Предупреждения" }, { id: "resolved", label: "Решённые" }, { id: "completed", label: "Завершённые" }];

function OperationJournal({ request, entry }: { request: Request; entry: HistoryEntry }) {
  const [journal, setJournal] = useState<Journal | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  usePageError("operation-journal", "Этапы операции", error, { label: "Обновить этапы", run: () => setRevision(value => value + 1) });
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
        if (value.evidence && (!Array.isArray(value.evidence.lines) || typeof value.evidence.captured_at !== "string" || value.evidence.lines.some(line => !line || typeof line.at !== "string" || typeof line.message !== "string"))) throw Error("Invalid evidence snapshot");
        if (!cancelled) { setJournal(value); setError(""); }
      } catch { if (!cancelled) setError("Журнал не обновлён."); }
      finally { pending = false; if (!cancelled) setLoading(false); }
    }
    void load();
    const timer = active ? window.setInterval(() => void load(), 5000) : undefined;
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [request, entry.id, entry.source, active, revision]);
  return <section className="operationJournal" aria-label="Этапы выбранной операции">
    <div className="operationJournalHeading"><h4>Этапы выполнения</h4><button type="button" disabled={loading} onClick={() => setRevision(value => value + 1)}>{loading ? "Загрузка…" : "Обновить этапы"}</button></div>
    {!journal && !error && <p role="status">Загрузка…</p>}
    {journal?.available === false ? <p>Журнал недоступен.</p> : journal?.events.length === 0 && <p>Нет этапов.</p>}
    {Boolean(journal?.dropped) && <p className="operationHistoryNotice">Пропущено этапов: {journal?.dropped}</p>}
    <ol className="operationTimeline">{journal?.events.map((event, index) => <li key={`${event.at}:${index}`}>
      <div><time>{safeDateTime(event.at)}</time><span className={`operationState ${historyState({ state: event.state }).tone}`}>{historyState({ state: event.state }).label}</span></div>
      <p>{notificationText(event.message) || "Состояние обновлено"}</p>
      <small>{event.stage && `Этап: ${event.stage}`}{event.result && ` · результат этапа: ${event.result}`}{event.error_code && ` · ${event.error_code}`}{typeof event.exit_code === "number" && ` · exit ${event.exit_code}`}</small>
      {event.command && <small><code>{event.command}{event.dependency ? ` · ${event.dependency}` : ""}</code>{typeof event.duration_ms === "number" ? ` · ${Math.round(event.duration_ms)} мс` : ""}</small>}
    </li>)}</ol>
    {journal?.evidence && <OperationSavedJournal evidence={journal.evidence} />}
  </section>;
}

export function OperationHistory({ token, action, settingsRevision }: { token: string; action?: SystemAction; settingsRevision?: string }) {
  const notify = useNotifier("operation-history", "Операции");
  const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<"operations" | "checks">("operations");
  const [snapshot, setSnapshot] = useState<{ owner: Request; kind: typeof kind; errors: number; items: HistoryEntry[]; at: Date } | null>(null);
  const [failure, setFailure] = useState<{ owner: Request; message: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<HistoryFilter>("all");
  const [selected, setSelected] = useState("");
  const [savingResolution, setSavingResolution] = useState(false);
  const [page, setPage] = useState(0);
  const request = useMemo(() => createApiClient(token), [token]);
  const items = snapshot?.owner === request && snapshot.kind === kind ? snapshot.items : null;
  const error = failure?.owner === request ? failure.message : "";
  usePageError("operation-history", "История операций", open ? error : "", { label: "Обновить историю", run: () => setRevision(value => value + 1) });
  const errorCount = snapshot?.owner === request ? snapshot.errors : 0;
  const errorLabel = errorCount ? `Нерешённых ошибок в истории: ${errorCount}${error ? "; данные устарели" : ""}` : "";
  const visible = items?.filter(item => historyMatches(item, filter, query)) || [];
  const pagination = historyPage(visible, page);
  const entry = pagination.items.find(item => historyKey(item) === selected) || pagination.items[0];
  useEffect(() => { if (open) dialog.current?.showModal(); }, [open]);
  useEffect(() => {
    let cancelled = false;
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true; setLoading(true);
      try {
        const value = await request<{ items: HistoryEntry[]; counts?: { errors: number } }>(`/application/operations${kind === "checks" ? "?kind=checks" : ""}`, { cache: "no-store" });
        const entries = historyEntries(value);
        const errors = Number.isSafeInteger(value.counts?.errors) && value.counts!.errors >= 0 ? value.counts!.errors : kind === "operations" ? historyErrorCount(entries) : 0;
        if (!cancelled) { setSnapshot({ owner: request, kind, errors, items: entries, at: new Date() }); setFailure(null); }
      } catch {
        if (!cancelled) setFailure({ owner: request, message: "История не обновлена." });
      } finally { pending = false; if (!cancelled) setLoading(false); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), open ? 10000 : 30000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [open, request, kind, revision, action?.id, action?.state, action?.result, settingsRevision]);
  function close() { dialog.current?.close(); setOpen(false); }
  async function copy(entry: HistoryEntry, context = false) {
    const text = context ? `Разбери операцию ${entry.id}. Источник: ${entry.source || "system"}; действие: ${entry.action}; state=${entry.state}; result=${entry.result || "не указан"}; stage=${entry.stage || "не указан"}; error_code=${entry.error_code || "не указан"}. Начни с operation_timeline(operation_id="${entry.id}", include_journal=true). Сопоставь authoritative_result, этапы, конфликты и current_admission. Не повторяй изменяющую команду.` : entry.id || "";
    try { await navigator.clipboard.writeText(text); notify.info(context ? "Контекст для Nodera скопирован" : "ID операции скопирован"); }
    catch { notify.error("Не удалось скопировать."); }
  }
  async function resolve(entry: HistoryEntry) {
    setSavingResolution(true);
    try {
      const value = await request<{ resolved_at: string | null; resolution_review?: HistoryEntry["resolution_review"] }>(`/application/operations/${entry.id}/resolution?source=${entry.source || "system"}`, { method: "PUT", body: JSON.stringify({ resolved: !entry.resolved_at }) });
      setSnapshot(current => current?.owner === request ? { ...current, items: current.items.map(item => historyKey(item) === historyKey(entry) ? { ...item, resolved_at: value.resolved_at, resolution_review: value.resolution_review } : item) } : current);
      setRevision(value => value + 1);
    } catch { notify.error("Не удалось сохранить отметку."); }
    finally { setSavingResolution(false); }
  }
  const presentation = entry ? historyState(entry) : null;
  return <>
    <button type="button" className="ghostButton operationHistoryButton" onClick={() => setOpen(true)} title={errorLabel || undefined} aria-label={errorLabel ? `Операции. ${errorLabel}` : "Операции"}>Операции{errorCount > 0 && <span className="operationHistoryErrorCount" aria-hidden="true">{errorCount}{error ? "?" : ""}</span>}</button>
    <dialog ref={dialog} className="operationHistoryDialog" aria-labelledby="operation-history-title" onCancel={close} onClose={() => setOpen(false)}>
      <div className="operationHistoryShell">
        <header className="operationHistoryHeader"><div><h2 id="operation-history-title">История операций</h2></div><button type="button" autoFocus onClick={close} aria-label="Закрыть историю операций">×</button></header>
        <div className="operationHistoryTools">
          <label className="operationHistoryKind">Раздел<select aria-label="Раздел журнала" value={kind} onChange={event => { setKind(event.target.value as typeof kind); setPage(0); setSelected(""); setFilter("all"); }}><option value="operations">Операции</option><option value="checks">Проверки</option></select></label>
          <label className="operationHistorySearch"><span>Найти операцию</span><input type="search" value={query} onChange={event => { setQuery(event.target.value); setPage(0); }} placeholder="Название, сообщение или ID" /></label>
          <button type="button" disabled={loading} onClick={() => setRevision(value => value + 1)}>{loading ? "Обновляем…" : "Обновить историю"}</button>
        </div>
        <nav className="operationHistoryFilters" aria-label="Фильтр состояния операций">{filters.map(item => <button key={item.id} type="button" aria-pressed={filter === item.id} onClick={() => { setFilter(item.id); setPage(0); }}>{item.label}<span>{items?.filter(entry => historyMatches(entry, item.id, "")).length ?? "—"}</span></button>)}</nav>
        <div className="operationHistoryWorkspace">
          <section className="operationHistoryList" aria-label="Список операций" aria-busy={loading}>
            <div className="operationHistoryListHeading"><span>{visible.length} из {items?.length ?? "—"}</span><span>Сначала новые</span></div>
            {!items && !error && <p className="operationHistoryEmpty" role="status">Загрузка истории…</p>}
            {items?.length === 0 && <p className="operationHistoryEmpty">Сохранённых операций пока нет.</p>}
            {Boolean(items?.length) && visible.length === 0 && <div className="operationHistoryEmpty"><p>Ничего не найдено.</p><button type="button" onClick={() => { setFilter("all"); setQuery(""); }}>Сбросить фильтры</button></div>}
            {pagination.items.map(item => <button type="button" className="operationHistoryItem" key={historyKey(item)} aria-pressed={entry && historyKey(entry) === historyKey(item)} onClick={() => setSelected(historyKey(item))}>
              <span className={`operationState ${historyState(item).tone}`}>{historyState(item).label}</span><strong>{historyTitle(item)}</strong>
              <span className="operationHistoryItemMeta"><time>{safeDateTime(item.started_at || item.updated_at)}</time><span>{historySource(item)}</span>{historyDuration(item) && <span>{historyDuration(item)}</span>}</span>
              {item.id && <code title={item.id}>{item.id.slice(0, 12)}</code>}
              {item.message && <span className="operationHistoryExcerpt">{notificationText(item.message)}</span>}
            </button>)}
            {pagination.pages > 1 && <nav className="operationHistoryPagination" aria-label="Страницы истории"><button type="button" disabled={pagination.index === 0} onClick={() => { setPage(pagination.index - 1); setSelected(""); }}>Назад</button><span>{pagination.index + 1} / {pagination.pages}</span><button type="button" disabled={pagination.index === pagination.pages - 1} onClick={() => { setPage(pagination.index + 1); setSelected(""); }}>Далее</button></nav>}
          </section>
          <section className="operationHistoryDetail" aria-label="Выбранная операция" aria-live="polite">
            {entry && presentation ? <>
              <div className="operationHistoryDetailHeading"><span className={`operationState ${presentation.tone}`}>{presentation.label}</span><h3>{historyTitle(entry)}</h3><p>{historySource(entry)} · {safeDateTime(entry.started_at || entry.updated_at)}</p></div>
              {entry.id && <div className="operationIdentity"><code>{entry.id}</code><div><button type="button" onClick={() => void copy(entry)}>Скопировать ID</button><button type="button" onClick={() => void copy(entry, true)}>Контекст для Nodera</button></div></div>}
              <dl className="operationEvidence"><div><dt>Состояние операции</dt><dd>{entry.state || "unknown"}</dd></div><div><dt>Результат</dt><dd>{entry.result || "не указан"}</dd></div>{historyDuration(entry) && <div><dt>Длительность</dt><dd>{historyDuration(entry)}</dd></div>}{entry.stage && <div><dt>Последний этап</dt><dd>{entry.stage} · {entry.stage_result || "не указан"}</dd></div>}{entry.error_code && <div><dt>Код ошибки</dt><dd>{entry.error_code}</dd></div>}</dl>


              {entry.message && <p className={`operationHistoryResult ${presentation.tone}`}>{notificationText(entry.message)}</p>}
              {entry.resolved_at && entry.resolution_review?.by === "assistant" && <p className="operationHistoryResult">Проверка ИИ: {entry.resolution_review.note}</p>}
              {entry.id && (presentation.tone === "error" || entry.resolved_at) && <div className="operationResolution"><label className="operationResolutionSwitch"><input type="checkbox" role="switch" aria-label="Операция решена" checked={Boolean(entry.resolved_at)} disabled={savingResolution} onChange={() => void resolve(entry)} /><span aria-hidden="true" /><strong>{savingResolution ? "Сохранение…" : entry.resolved_at ? "Решено" : "Не решено"}</strong></label>{entry.resolved_at && <time dateTime={entry.resolved_at}>{safeDateTime(entry.resolved_at)}</time>}</div>}
              {presentation.tone === "active" && Number.isFinite(entry.progress) && <div className="operationHistoryProgress"><progress max={100} value={Math.max(0, Math.min(100, entry.progress!))} aria-label="Ход операции" /><span>{Math.round(Math.max(0, Math.min(100, entry.progress!)))}%</span></div>}
              {open && entry.id && <OperationJournal key={`${token}:${historyKey(entry)}`} request={request} entry={entry} />}
              <details className="operationHistoryTechnical"><summary>Технические сведения</summary><dl>{entry.id && <><dt>ID операции</dt><dd><code>{entry.id}</code></dd></>}{entry.unit && <><dt>Процесс</dt><dd><code>{entry.unit}</code></dd></>}<dt>Обновлена</dt><dd>{safeDateTime(entry.updated_at || entry.started_at)}</dd></dl></details>
            </> : <p className="operationHistoryEmpty">Нет операций.</p>}
          </section>
        </div>
        <footer className="operationHistoryFooter"><span>Записей: {items?.length ?? "—"}</span>{snapshot?.owner === request && <time>{snapshot.at.toLocaleTimeString("ru-RU")}</time>}</footer>
      </div>
    </dialog>
  </>;
}
