import { useEffect, useRef, useState } from "react";
import type { createApiClient } from "../../shared/lib/api-request";
import { useNotifier } from "../../shared/notifications/notification-center";

type Request = ReturnType<typeof createApiClient>;
type RecordRow = { type: string; cursor?: string; at?: number; message?: string; unit?: string };
function download(lines: string[]) {
  const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a"); link.href = url; link.download = "live-journal.log"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function LiveJournal({ request, services }: { request: Request; services: { id: string; name: string }[] }) {
  const [source, setSource] = useState("application");
  const [priority, setPriority] = useState("warning");
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<string[]>([]);
  const [hasGap, setHasGap] = useState(false);
  const notify = useNotifier("live-journal", "Прямой эфир журнала");
  const cursor = useRef<string | undefined>(undefined);
  const known = useRef(new Set<string>());
  useEffect(() => {
    if (!running) return;
    const stream = request.events;
    if (!stream) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ service: source, priority, minutes: "60" });
    if (cursor.current) params.set("cursor", cursor.current);
    void stream(`/observability/journal/stream?${params}`, value => {
      const row = value as RecordRow;
      if (row.type === "gap") { cursor.current = undefined; setHasGap(true); notify.info(row.message || "В журнале есть пропуск"); }
      if (row.type === "paused") notify.info(row.message || "Поток приостановлен");
      if (row.type !== "record" || !row.cursor || known.current.has(row.cursor)) return;
      cursor.current = row.cursor;
      known.current.add(row.cursor);
      if (known.current.size > 2000) known.current.delete(known.current.values().next().value!);
      setLines(previous => [...previous, `${new Date((row.at ?? 0) * 1000).toLocaleString()} ${row.unit ?? ""} ${row.message ?? ""}`].slice(-2000));
    }, controller.signal).catch(cause => { if (!controller.signal.aborted) notify.error(String(cause)); })
      .finally(() => { if (!controller.signal.aborted) setRunning(false); });
    return () => controller.abort();
  }, [request, running, source, priority, notify]);
  const reset = () => { setRunning(false); cursor.current = undefined; known.current.clear(); setLines([]); setHasGap(false); };
  return <><div className="servicesLogControls">
    <label className="opsField">Источник<select value={source} onChange={event => { reset(); setSource(event.target.value); }}>
      <option value="application">Приложение</option><option value="system">Система</option><option value="dns">DNS</option><option value="gateway">Gateway</option>
      {services.filter(item => !["application", "system", "dns", "gateway"].includes(item.id)).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>
    <label className="opsField">Уровень и выше<select value={priority} onChange={event => { reset(); setPriority(event.target.value); }}><option value="err">Ошибки</option><option value="warning">Предупреждения</option><option value="info">Информация</option><option value="debug">Отладка</option></select></label>
    <button type="button" onClick={() => setRunning(!running)}>{running ? "Пауза" : lines.length ? "Продолжить" : "Начать просмотр"}</button>
    <button type="button" onClick={reset}>Новый просмотр</button><button type="button" disabled={!lines.length} onClick={() => download(lines)}>Скачать показанные строки</button>
  </div><small>{running ? "Прямой эфир" : "Пауза"} · {lines.length} / 2000{hasGap && " · есть пропуски"}</small>
    <pre className="observabilityTerminal" tabIndex={0} aria-label="Журнал в реальном времени">{lines.join("\n") || "Выберите источник и начните просмотр."}</pre></>;
}

type Network = { available: boolean; lines: string[]; checked_at: number; truncated: boolean; reason?: string; state_filter_supported?: boolean };
function HostConnections({ request }: { request: Request }) {
  const [source, setSource] = useState("ss");
  const [protocol, setProtocol] = useState("tcp");
  const [family, setFamily] = useState("ipv4");
  const [state, setState] = useState("established");
  const [result, setResult] = useState<Network>();
  const notify = useNotifier("host-connections", "Соединения VPS");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  useEffect(() => () => { generation.current++; }, []);
  const read = async () => {
    const current = ++generation.current;
    setBusy(true); setResult(undefined);
    try { const value = await request<Network>(`/observability/network?${new URLSearchParams({ source, protocol, family, state, limit: "120" })}`); if (current === generation.current) setResult(value); }
    catch (cause) { if (current === generation.current) notify.error(String(cause)); }
    finally { if (current === generation.current) setBusy(false); }
  };
  return <><div className="servicesLogControls">{([
    ["Источник", source, setSource, [["ss", "Сокеты · ss"], ["conntrack", "Таблица conntrack"]]],
    ["Протокол", protocol, setProtocol, [["tcp", "TCP"], ["udp", "UDP"]]],
    ["Семейство", family, setFamily, [["ipv4", "IPv4"], ["ipv6", "IPv6"]]],
    ["Состояние", state, setState, [["established", "Установленные"], ["all", "Все"]]],
  ] as const).map(([label, value, setter, options]) => <label className="opsField" key={label}>{label}<select value={value} disabled={busy} onChange={event => { setter(event.target.value); setResult(undefined); }}>{options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>)}
    <button type="button" disabled={busy} onClick={() => void read()}>{busy ? "Чтение…" : "Показать соединения"}</button></div>
    {result && <><p>{new Date(result.checked_at * 1000).toLocaleString()} · {result.available ? `${result.lines.length} строк${result.truncated ? " · выдача ограничена" : ""}` : `Источник недоступен (${result.reason})`}. Наблюдение в сетевом пространстве VPS.{result.state_filter_supported === false && " Для UDP conntrack фильтр established не поддерживается; показан протокол целиком."}</p>{result.available && <pre className="observabilityTerminal" tabIndex={0}>{result.lines.join("\n") || "В выбранной области записей нет."}</pre>}</>}
  </>;
}

export function LiveObservation({ request, services }: { request: Request; services: { id: string; name: string }[] }) {
  const [open, setOpen] = useState(false);
  return <details className="observabilityJournal" onToggle={event => setOpen(event.currentTarget.open)}><summary><strong>Прямой эфир и соединения VPS</strong></summary>{open && <><HostConnections request={request}/><LiveJournal request={request} services={services}/></>}</details>;
}
