"use client";

import { useEffect, useState } from "react";
import { ProtocolIcon } from "./protocol-icon";
import type { ProtocolImage } from "./page";

type Connection = { api_version: number; agent_url: string; certificate_sha256: string; token?: string };
type Status = Connection & { active: boolean; error?: string; items: Array<{ id: string; transport: string; listen_port: number; target_ip: string; target_port: number; state: string }> };
type Props = {
  image: ProtocolImage;
  request(path: string, init?: RequestInit): Promise<unknown>;
  busy: boolean;
  confirmRotation(): Promise<boolean>;
  onServiceAction(action: "start" | "stop" | "restart"): Promise<void>;
  onUpdate(): void;
  onRemove(): void;
};

export function RelayAgent({ image, request, busy, confirmRotation, onServiceAction, onUpdate, onRemove }: Props) {
  const [status, setStatus] = useState<Status | null>(null);
  const [credentials, setCredentials] = useState<Connection | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pollError, setPollError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    if (busy) return;
    let current = true;
    let pending = false;
    async function refresh() {
      if (pending) return;
      pending = true;
      try {
        const value = await request("/relay-agent") as Status;
        if (current) { setStatus(value); setPollError(""); }
      } catch (cause) {
        if (current) setPollError(cause instanceof Error ? cause.message : "Не удалось получить состояние агента");
      } finally { pending = false; }
    }
    void refresh();
    const timer = setInterval(() => { void refresh(); }, 15000);
    return () => { current = false; clearInterval(timer); };
  }, [request, busy, image.service_active, refreshKey]);

  async function reveal(rotate = false) {
    if (rotate && !await confirmRotation()) return;
    setWorking(true); setError(""); setNotice("");
    if (rotate) setCredentials(null);
    try {
      const value = await request(rotate ? "/relay-agent/token/rotate" : "/relay-agent/credentials", { method: "POST" }) as Connection;
      setCredentials(value);
      if (rotate) setNotice("Токен заменён. Обновите параметры узла в управляющей панели; маршруты продолжают работать.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Результат не подтверждён. Проверьте параметры агента перед повтором.");
    } finally { setWorking(false); }
  }

  async function copy() {
    if (!credentials) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(credentials, null, 2));
      setNotice("Параметры подключения скопированы. Они содержат токен управления relay.");
    } catch { setError("Буфер обмена недоступен. Скопируйте параметры из поля вручную."); }
  }

  return <section className="protocolWorkspace agentWorkspace">
    <header className="protocolWorkspaceHero">
      <div className="protocolWorkspaceIdentity"><ProtocolIcon protocol="relay-agent" /><div><small>RELAY AGENT</small><h1>Agent</h1><p>Добавление текущего VPS в изолированную сеть</p></div></div>
      <div className="protocolWorkspaceEndpoint"><small>API ENDPOINT</small><strong>{status?.agent_url || "Проверяем…"}</strong><span>HTTPS · {image.installed_version || image.version}</span></div>
      <div className="agentServiceState"><strong>{image.service_active ? "Служба работает" : "Служба остановлена"}</strong><small>{status?.active ? "API доступен" : "API не подтверждён"}</small></div>
      <div className="protocolWorkspaceActions">
        <button disabled={busy || working} onClick={() => void onServiceAction(image.service_active ? "restart" : "start")}>{image.service_active ? "Перезапустить" : "Запустить"}</button>
        {image.service_active && <button disabled={busy || working} onClick={() => void onServiceAction("stop")}>Остановить</button>}
        <button disabled={busy || working} onClick={onUpdate}>Обновить агент</button>
        <button className="danger" disabled={busy || working} onClick={onRemove}>Удалить</button>
      </div>
    </header>
    <article className="panel relayAgentDetails">
    <div className="panelHead"><div><p className="eyebrow">ISOLATED NETWORK</p><h2>Добавление в изолированную сеть</h2></div></div>
    <p>Добавьте этот VPS в изолированную сеть по защищённому API. Управляющая панель задаёт TCP/UDP-маршруты узла; действующие VPN-подключения независимы.</p>
    <dl>
      <div><dt>API</dt><dd>{status?.agent_url || "Загрузка…"}</dd></div>
      <div><dt>Состояние</dt><dd>{status ? status.active ? "API доступен" : "API недоступен" : "Проверяем…"}</dd></div>
      <div><dt>SHA-256 сертификата</dt><dd className="relayFingerprint">{status?.certificate_sha256 || "—"}</dd></div>
      <div><dt>Порты маршрутов</dt><dd>20000–20999 · TCP / UDP · IPv4</dd></div>
    </dl>
    <p>При добавлении узла проверяйте сертификат по указанному отпечатку. Токен даёт право управлять маршрутами этого узла.</p>
    <div className="relayAgentActions">
      <button disabled={busy || working} onClick={() => credentials ? setCredentials(null) : void reveal()}>{working ? "Выполняется…" : credentials ? "Скрыть токен" : "Показать параметры подключения"}</button>
      <button disabled={busy || working} onClick={() => void reveal(true)}>Заменить токен</button>
    </div>
    {credentials && <div className="relayCredentials"><label>Параметры подключения · содержат секрет<textarea readOnly value={JSON.stringify(credentials, null, 2)} rows={8} spellCheck={false} /></label><button onClick={() => void copy()}>Скопировать параметры</button></div>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    </article>
    <article className="panel relayAgentRoutes">
      <div className="panelHead"><div><p className="eyebrow">RELAY ROUTES</p><h2>Маршруты · {status?.items.length ?? "—"}</h2></div><button disabled={busy || working} onClick={() => setRefreshKey((value) => value + 1)}>Обновить состояние</button></div>
      {!!status?.items.length && <div className="relayRouteList">{status.items.map((route) => <div key={route.id}><strong>{route.id}</strong><span>{route.transport.toUpperCase()} · {route.listen_port} → {route.target_ip}:{route.target_port}</span><small>{route.state === "listening" ? "Слушает порт · передача данных отдельно не проверена" : "Ошибка listener"}</small></div>)}</div>}
      {status?.active && !status.items.length && <p>Маршрутов пока нет. Добавьте узел в изолированную сеть и назначьте сервер назначения.</p>}
      {(pollError || status?.error) && <p role="status">{pollError || status?.error}</p>}
      {!status && !pollError && <p>Получаем состояние агента…</p>}
    </article>
  </section>;
}
