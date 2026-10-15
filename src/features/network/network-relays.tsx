"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ConfirmationDialog } from "../../shared/components/confirmation-dialog";
import { useNotifier } from "../../shared/notifications/notification-center";
import { SystemOperationUnknown, systemActionNeedsPolling } from "../../control-panel/system-operation";
import type { SystemAction } from "../../shared/types/control-plane";
import type { NetworkAdmission, NetworkRequest } from "./network-api";
import { relayConnection, relaySnapshot, type RelayStatus, type RelayHost, type RelayRoute } from "./relay-state";

type Props = { request: NetworkRequest; admit: NetworkAdmission; operation?: SystemAction; onOperation: (value: SystemAction) => void; busy: boolean; onDirtyChange: (dirty: boolean) => void };

export function NetworkRelays({ request, admit, operation, onOperation, busy, onDirtyChange }: Props) {
  const [status, setStatus] = useState<RelayStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const [reading, setReading] = useState(false);
  const [sending, setSending] = useState(false);
  const [name, setName] = useState("");
  const [connection, setConnection] = useState("");
  const [edit, setEdit] = useState<RelayHost | null>(null);
  const [hostId, setHostId] = useState("");
  const [target, setTarget] = useState("");
  const [port, setPort] = useState("20000");
  const [consumerIds, setConsumerIds] = useState<Record<string, string>>({});
  const [confirmation, setConfirmation] = useState<{ host: RelayHost; route?: RelayRoute } | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const generation = useRef(0);
  const inFlight = useRef(false);
  const saving = useRef(false);
  const { error: notifyError } = useNotifier("relay", "Relay");
  const load = useCallback(async () => {
    if (inFlight.current || saving.current) return;
    inFlight.current = true; setReading(true);
    const run = generation.current;
    try {
      const data = relaySnapshot(await request("/network/relays", { cache: "no-store" }));
      if (run === generation.current) { setStatus(data); setFailed(false); }
    } catch { if (run === generation.current) setFailed(true); }
    finally { if (run === generation.current) setReading(false); inFlight.current = false; }
  }, [request]);
  useEffect(() => {
    const current = generation;
    const timer = window.setTimeout(() => void load(), 0);
    return () => { window.clearTimeout(timer); current.current++; };
  }, [load]);
  useEffect(() => { onDirtyChange(Boolean(name || connection)); }, [name, connection, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  useEffect(() => {
    if (!name && !connection) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, [name, connection]);
  useEffect(() => {
    if (!operation?.action?.startsWith("relay-") || systemActionNeedsPolling(operation)) return;
    if (pending && operation.id !== pending) return;
    const timer = window.setTimeout(() => { setPending(null); void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, [operation, pending, load]);
  const locked = busy || sending || reading || failed || !status || Boolean(pending);
  const host = status?.hosts.find(item => item.id === hostId);

  async function submit(path: string, init: RequestInit) {
    if (saving.current) return;
    saving.current = true; generation.current++; setSending(true);
    try {
      const started = await admit(path, init);
      setPending(systemActionNeedsPolling(started) ? started.id || null : null);
      onOperation(started);
    } catch (cause) {
      if (cause instanceof SystemOperationUnknown) setPending(cause.id);
      else notifyError(cause instanceof Error ? cause.message : "Не удалось начать операцию relay");
    } finally { saving.current = false; setSending(false); }
  }

  async function saveHost() {
    if (locked || !status || status.pending && !edit) return;
    try {
      const params = relayConnection(connection);
      setConnection("");
      await submit(status.pending ? "/network/relays/reconnect" : "/network/relays", { method: "PUT", body: JSON.stringify({ ...params, name: name.trim(), id: edit?.id || null, expected_revision: status.revision }) });
      setName(""); setEdit(null);
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Некорректные параметры подключения"); }
  }

  async function exportFile(file: File, selectedHost: RelayHost, route: RelayRoute) {
    try {
      if (file.size > 262144) throw new Error("Размер файла должен быть не более 256 КБ");
      const value = await request<{ config: string; replaced: number }>(`/network/relays/${selectedHost.id}/routes/${route.id}/export`, { method: "POST", body: JSON.stringify({ config: await file.text() }) });
      if (typeof value.config !== "string" || typeof value.replaced !== "number" || value.replaced < 1) throw new Error("Некорректный ответ экспорта");
      const url = URL.createObjectURL(new Blob([value.config], { type: "text/plain;charset=utf-8" }));
      const link = document.createElement("a"); link.href = url; link.download = file.name.replace(/(\.[^.]+)?$/, "-relay$1"); link.click(); URL.revokeObjectURL(url);
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось подготовить relay-копию"); }
  }

  return <section className="relayWorkspace">
    <header className="networkBlockHeader"><div><p className="eyebrow">Relay</p><h2>Промежуточные узлы</h2></div><button type="button" disabled={reading || sending || busy} onClick={() => void load()}>{reading ? "Проверяем…" : "Проверить состояние"}</button></header>
    <p className="networkNotice">Relay передаёт TCP/UDP на этот VPS. Адрес и порт клиента меняются в отдельной копии конфигурации; ключи и TLS-имя сохраняются. Существующие файлы и подписки продолжают работать с прежними адресами.</p>
    {failed && <p role="status" className="networkNotice">UNKNOWN — обновление не удалось. Последние сведения не подтверждают доступность узлов; действия заблокированы до успешного чтения.</p>}
    {status?.pending && <div className="networkNotice" role="status"><strong>Результат изменения relay не подтверждён.</strong><p>Проверка прочитает сохранённый маршрут с агента без повторной отправки команды.</p><button disabled={locked} onClick={() => void submit("/network/relays/reconcile", { method: "POST" })}>Проверить результат операции</button></div>}
    <div className="relayGrid">
      <form className="networkPanel relayForm" onSubmit={event => { event.preventDefault(); void saveHost(); }}>
        <h3>{edit ? `Обновить подключение: ${edit.name}` : "Добавить Relay Agent"}</h3>
        <label>Название<input required maxLength={64} value={name} onChange={event => setName(event.target.value)} disabled={locked || status?.pending && !edit} /></label>
        <label>JSON подключения<input type="password" autoComplete="new-password" spellCheck={false} required value={connection} onChange={event => setConnection(event.target.value)} disabled={locked || status?.pending && !edit} placeholder="Параметры из раздела Agent на узле" /></label>
        <small>Содержит адрес, токен и отпечаток сертификата. Новый отпечаток применяется только после явного сохранения и проверки узла.</small>
        <div className="relayActions"><button type="submit" disabled={locked || status?.pending && !edit || !name.trim() || !connection}>Проверить и сохранить</button><button type="button" disabled={sending} onClick={() => { setName(""); setConnection(""); setEdit(null); }}>Отменить</button></div>
      </form>
      <form className="networkPanel relayForm" onSubmit={event => {
        event.preventDefault(); if (locked || !status || status.pending || !host || host.agent_revision === undefined) return;
        const [transport, targetPort] = target.split(":");
        void submit(`/network/relays/${host.id}/routes`, { method: "POST", body: JSON.stringify({ transport, target_port: Number(targetPort), listen_port: Number(port), expected_revision: status.revision, agent_revision: host.agent_revision }) });
      }}>
        <h3>Создать маршрут</h3>
        <label>Узел<select value={hostId} onChange={event => setHostId(event.target.value)} disabled={locked || status?.pending} required><option value="">Выберите узел</option>{status?.hosts.map(item => <option key={item.id} value={item.id} disabled={item.state !== "connected"}>{item.name}</option>)}</select></label>
        <label>Назначение на этом VPS<select value={target} onChange={event => setTarget(event.target.value)} disabled={locked || status?.pending} required><option value="">Выберите публичный порт протокола</option>{status?.targets.map(item => <option key={`${item.transport}:${item.port}`} value={`${item.transport}:${item.port}`}>{item.transport.toUpperCase()} · {status.origin_ip}:{item.port}</option>)}</select></label>
        <label>Внешний порт relay<input type="number" min={20000} max={20999} required value={port} onChange={event => setPort(event.target.value)} disabled={locked || status?.pending} /></label>
        <small>Диапазон 20000–20999. TCP и UDP резервируются отдельно. TLS проходит до origin. TCP 443 может также передавать доступ к публичным сайтам и панели этого VPS.</small>
        <button type="submit" disabled={locked || status?.pending || host?.state !== "connected" || !target}>Создать маршрут</button>
      </form>
    </div>
    {!status?.hosts.length && !reading && <p className="networkEmpty">Узлы ещё не добавлены.</p>}
    {status?.hosts.map(item => <article className="networkPanel relayHost" key={item.id}>
      <header className="networkBlockHeader"><div><h3>{item.name}</h3><code>{item.agent_url}</code></div><strong>{failed || item.state !== "connected" ? "UNKNOWN" : `CONNECTED · ${item.version}`}</strong></header>
      <p className="relayFingerprint">SHA-256 {item.certificate_sha256}</p>
      {item.error && <p role="status">{item.error}</p>}
      {item.foreign_routes > 0 && <p>Маршрутов вне управления этого PRO: {item.foreign_routes}. Они сохраняются.</p>}
      <div className="relayActions"><button disabled={locked} onClick={() => { setEdit(item); setName(item.name); setConnection(""); }}>Обновить параметры подключения</button><button disabled={locked || status.pending || item.routes.length > 0} onClick={() => setConfirmation({ host: item })}>Удалить узел из PRO</button></div>
      <div className="relayRoutes">{item.routes.map(route => <div className="relayRoute" key={route.id}>
        <div><strong>{route.transport.toUpperCase()} · {new URL(item.agent_url).hostname}:{route.listen_port}</strong><p>→ {route.target_ip}:{route.target_port}</p><code>{route.id}</code></div>
        <div><strong>{failed ? "UNKNOWN" : route.state.toUpperCase()}</strong><p>Полный протокол: {failed ? "UNKNOWN" : route.check?.state.toUpperCase() || "UNCHECKED"}</p>{route.check && <small>{route.check.consumer_id} · проверка с сервера, не из клиентской сети</small>}<p><small>Сессии: {failed ? "—" : route.sessions ?? "—"} · Передано: {failed ? "—" : route.sent_bytes ?? "—"} / {failed ? "—" : route.received_bytes ?? "—"} байт</small></p></div>
        <div className="relayActions"><label>Подключение для проверки<select value={consumerIds[route.id] || ""} disabled={locked || status.pending} onChange={event => setConsumerIds(current => ({ ...current, [route.id]: event.target.value }))}><option value="">Выберите подключение</option>{status.consumers?.map(consumer => <option key={consumer.id} value={consumer.id}>{consumer.label} · {consumer.id}</option>)}</select></label><button disabled={locked || status.pending || route.state !== "listening" || !consumerIds[route.id]} onClick={() => void submit(`/network/relays/${item.id}/routes/${route.id}/check?consumer_id=${encodeURIComponent(consumerIds[route.id])}`, { method: "POST", headers: { "If-Match": status.revision } })}>Проверить протокол</button></div>
        <div className="relayActions"><label className="relayFileButton">Скачать relay-копию<input type="file" accept=".conf,.json,.yaml,.yml,.txt" disabled={locked || status.pending || route.state !== "listening"} onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void exportFile(file, item, route); }} /></label><button disabled={locked || status.pending || item.state !== "connected" || route.state === "drift"} onClick={() => setConfirmation({ host: item, route })}>Удалить маршрут</button></div>
      </div>)}</div>
    </article>)}
    {confirmation && <ConfirmationDialog request={{ title: confirmation.route ? "Удалить маршрут relay?" : "Удалить узел из PRO?", message: confirmation.route ? `Порт ${confirmation.route.listen_port}/${confirmation.route.transport} на ${confirmation.host.name} закроется. Активные соединения этого маршрута прервутся; его клиентские копии перестанут подключаться. Настройки origin сохранятся.` : `Параметры и токен ${confirmation.host.name} будут удалены из PRO. Сам агент продолжит работать.`, confirmLabel: "Удалить", danger: true }} onClose={confirmed => {
      const selected = confirmation; setConfirmation(null);
      if (!confirmed || locked || !status || status.pending || selected.route && selected.host.agent_revision === undefined) return;
      const path = `/network/relays/${selected.host.id}`+(selected.route ? `/routes/${selected.route.id}?agent_revision=${selected.host.agent_revision}` : "");
      void submit(path, { method: "DELETE", headers: { "If-Match": status.revision } });
    }} />}
  </section>;
}
