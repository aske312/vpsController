"use client";

import { useEffect, useState } from "react";

type Connection = { api_version: number; agent_url: string; certificate_sha256: string; token?: string };
type Status = Connection & { active: boolean; error?: string; items: Array<{ id: string; transport: string; listen_port: number; target_ip: string; target_port: number; state: string }> };
type Props = {
  request(path: string, init?: RequestInit): Promise<unknown>;
  busy: boolean;
  confirmRotation(): Promise<boolean>;
};

export function RelayAgent({ request, busy, confirmRotation }: Props) {
  const [status, setStatus] = useState<Status | null>(null);
  const [credentials, setCredentials] = useState<Connection | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let current = true;
    async function refresh() {
      try {
        const value = await request("/relay-agent") as Status;
        if (current) setStatus(value);
      } catch (cause) {
        if (current) setError(cause instanceof Error ? cause.message : "Не удалось получить состояние агента");
      }
    }
    void refresh();
    const timer = setInterval(() => { void refresh(); }, 15000);
    return () => { current = false; clearInterval(timer); };
  }, [request]);

  async function reveal(rotate = false) {
    if (rotate && !await confirmRotation()) return;
    setWorking(true); setError(""); setNotice("");
    if (rotate) setCredentials(null);
    try {
      const value = await request(rotate ? "/relay-agent/token/rotate" : "/relay-agent/credentials", { method: "POST" }) as Connection;
      setCredentials(value);
      if (rotate) setNotice("Токен заменён. Обновите параметры узла в PRO; маршруты продолжают работать.");
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

  return <div className="relayAgentDetails">
    <p>Подключите этот VPS в PRO по защищённому API. TCP/UDP-маршруты создаются из PRO; действующие VPN-подключения независимы.</p>
    <dl>
      <div><dt>API</dt><dd>{status?.agent_url || "Загрузка…"}</dd></div>
      <div><dt>Состояние</dt><dd>{status ? status.active ? "API доступен" : "API недоступен" : "Проверяем…"}</dd></div>
      <div><dt>SHA-256 сертификата</dt><dd className="relayFingerprint">{status?.certificate_sha256 || "—"}</dd></div>
      <div><dt>Порты маршрутов</dt><dd>20000–20999 · TCP / UDP · IPv4</dd></div>
    </dl>
    <p>В PRO проверяйте сертификат по указанному отпечатку. Токен даёт право управлять маршрутами этого узла.</p>
    {!!status?.items.length && <ul>{status.items.map((route) => <li key={route.id}><strong>{route.id}</strong> · {route.transport.toUpperCase()} {route.listen_port} → {route.target_ip}:{route.target_port} · {route.state === "listening" ? "Слушает порт" : "Ошибка listener"}</li>)}</ul>}
    {status?.active && !status.items.length && <p>Маршрутов пока нет. Добавьте узел и назначьте сервер назначения в PRO.</p>}
    {status?.error && <p role="status">{status.error}</p>}
    <div className="relayAgentActions">
      <button disabled={busy || working} onClick={() => credentials ? setCredentials(null) : void reveal()}>{working ? "Выполняется…" : credentials ? "Скрыть токен" : "Показать параметры PRO"}</button>
      <button disabled={busy || working} onClick={() => void reveal(true)}>Заменить токен</button>
    </div>
    {credentials && <div className="relayCredentials"><label>Параметры подключения · содержат секрет<textarea readOnly value={JSON.stringify(credentials, null, 2)} rows={8} spellCheck={false} /></label><button onClick={() => void copy()}>Скопировать параметры</button></div>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </div>;
}
