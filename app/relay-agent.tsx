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

  const locked = busy || working;
  const apiReady = !!status?.active && !pollError;
  const apiLabel = pollError ? "Нет свежих данных" : !status ? "Проверяем API…" : status.active ? "API доступен" : "API недоступен";

  return <section className="agentWorkspace" aria-label="Relay Agent">
    <header className="panel agentHeader">
      <div className="agentIdentity"><ProtocolIcon protocol="relay-agent" /><div><p className="eyebrow">ИЗОЛИРОВАННАЯ СЕТЬ</p><h1>Relay Agent</h1><p>Подключение этого VPS к управляющей панели и передача трафика по маршрутам.</p></div></div>
      <span className={apiReady ? "onlinePill" : "warningPill"}>{apiLabel}</span>
    </header>

    {pollError && <div className="errorBox" role="alert">Не удалось обновить состояние. {pollError} {status && "Показаны последние полученные данные."}</div>}
    {!pollError && status?.error && <div className="errorBox" role="alert">{status.error}</div>}

    <div className="agentLayout">
      <article className="panel agentConnection" aria-labelledby="agent-connection-title">
        <div className="panelHead"><div><p className="eyebrow">ПОДКЛЮЧЕНИЕ УЗЛА</p><h2 id="agent-connection-title">Добавление в сеть</h2></div><span className="agentTag">HTTPS</span></div>
        <p className="agentDescription">Добавьте узел в управляющей панели: укажите адрес API, отпечаток сертификата и токен. После подключения назначьте маршруты.</p>
        <dl className="agentConnectionData">
          <div><dt>Адрес API</dt><dd className="mono">{status?.agent_url || (pollError ? "Не получен" : "Получаем адрес…")}</dd></div>
          <div><dt>Отпечаток сертификата · SHA-256</dt><dd className="agentFingerprint">{status?.certificate_sha256 || "—"}</dd></div>
        </dl>
        <div className="agentCredentialsBar"><div><h3>Параметры подключения</h3><p>Содержат токен управления маршрутами этого узла.</p></div><button className="primaryButton" disabled={locked} aria-expanded={!!credentials} aria-controls="agent-credentials" onClick={() => credentials ? setCredentials(null) : void reveal()}>{working ? "Выполняется…" : credentials ? "Скрыть параметры" : "Показать параметры"}</button></div>
        {credentials && <div className="agentCredentials" id="agent-credentials"><label htmlFor="agent-credentials-value">Параметры для добавления узла · содержат секрет</label><textarea id="agent-credentials-value" readOnly value={JSON.stringify(credentials, null, 2)} rows={8} spellCheck={false} /><button disabled={locked} onClick={() => void copy()}>Скопировать параметры</button></div>}
        {error && <p className="errorBox" role="alert">{error}</p>}
        {notice && <p className="agentNotice" role="status">{notice}</p>}
        <details className="agentTokenSettings"><summary>Замена токена доступа</summary><div><p>Старый токен перестанет работать. Обновите параметры узла в управляющей панели. Действующие маршруты сохранятся.</p><button disabled={locked} onClick={() => void reveal(true)}>Заменить токен</button></div></details>
      </article>

      <aside className="panel agentService" aria-labelledby="agent-service-title">
        <div className="panelHead"><div><p className="eyebrow">ЛОКАЛЬНАЯ СЛУЖБА</p><h2 id="agent-service-title">Управление агентом</h2></div></div>
        <dl className="agentServiceData"><div><dt>Состояние</dt><dd><span className={image.service_active ? "onlinePill" : "offlinePill"}>{image.service_active ? "Работает" : "Остановлена"}</span></dd></div><div><dt>Версия</dt><dd className="mono">{image.installed_version || image.version || "—"}</dd></div></dl>
        <div className="agentServiceButtons"><button disabled={locked} onClick={() => void onServiceAction(image.service_active ? "restart" : "start")}>{image.service_active ? "Перезапустить" : "Запустить"}</button>{image.service_active && <button disabled={locked} onClick={() => void onServiceAction("stop")}>Остановить</button>}</div>
        <div className="agentServiceRow"><div><h3>Обновление</h3><p>Установить актуальную версию агента.</p></div><button disabled={locked} onClick={onUpdate}>Обновить</button></div>
        <div className="agentServiceRow"><div><h3>Удаление агента</h3><p>Отключить этот узел от relay-сети.</p></div><button className="danger" disabled={locked} onClick={onRemove}>Удалить</button></div>
      </aside>
    </div>

    <article className="panel agentRoutes" aria-labelledby="agent-routes-title">
      <div className="panelHead"><div><p className="eyebrow">ПЕРЕДАЧА ТРАФИКА</p><h2 id="agent-routes-title">Маршруты <span className="agentCount">{status?.items.length ?? "—"}</span></h2></div><button disabled={locked} onClick={() => setRefreshKey((value) => value + 1)}>Обновить состояние</button></div>
      <div className="agentRouteMeta"><span>TCP / UDP · IPv4</span><span>Входящие порты 20000–20999</span><span>Назначаются из управляющей панели</span></div>
      {!!status?.items.length && <div className="agentRouteTable" tabIndex={0} role="region" aria-label="Таблица маршрутов"><table><caption className="agentTableCaption">Маршруты узла</caption><thead><tr><th scope="col">Маршрут</th><th scope="col">Вход</th><th scope="col">Назначение</th><th scope="col">Состояние</th></tr></thead><tbody>{status.items.map((route) => <tr key={route.id}><th scope="row" className="mono">{route.id}</th><td><span className="agentTag">{route.transport.toUpperCase()}</span> <span className="mono">:{route.listen_port}</span></td><td className="mono">{route.target_ip}:{route.target_port}</td><td><span className={route.state === "listening" ? "agentRouteListening" : "agentRouteFailed"}>{route.state === "listening" ? "Слушает порт" : "Ошибка listener"}</span></td></tr>)}</tbody></table></div>}
      {!!status?.items.length && <p className="agentRouteNote">Состояние порта не подтверждает передачу данных до сервера назначения.</p>}
      {status && !status.items.length && <div className="agentEmpty"><ProtocolIcon protocol="relay-agent" /><div><h3>Маршрутов пока нет</h3><p>{status.active ? "Добавьте этот узел в управляющую панель и назначьте сервер назначения." : "Запустите агент и проверьте доступность API, затем назначьте маршруты в управляющей панели."}</p></div></div>}
      {!status && <div className="agentEmpty"><p>{pollError ? "Маршруты не получены. Повторите обновление состояния." : "Получаем маршруты узла…"}</p></div>}
    </article>
  </section>;
}
