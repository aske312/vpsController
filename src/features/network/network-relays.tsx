"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { ConfirmationDialog } from "../../shared/components/confirmation-dialog";
import { useDialogFocus } from "../../shared/components/use-dialog-focus";
import { useNotifier } from "../../shared/notifications/notification-center";
import { SystemOperationUnknown, systemActionNeedsPolling } from "../../control-panel/system-operation";
import type { SystemAction } from "../../shared/types/control-plane";
import type { NetworkAdmission, NetworkRequest } from "./network-api";
import { relayConnection, relaySnapshot, type RelayStatus, type RelayHost, type RelayRoute } from "./relay-state";

type Props = { request: NetworkRequest; admit: NetworkAdmission; operation?: SystemAction; onOperation: (value: SystemAction) => void; busy: boolean; onDirtyChange: (dirty: boolean) => void; refreshKey?: number };

export function useNetworkRelays({ request, admit, operation, onOperation, busy, onDirtyChange, refreshKey }: Props) {
  const [status, setStatus] = useState<RelayStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const [reading, setReading] = useState(false);
  const [sending, setSending] = useState(false);
  const [dialog, setDialog] = useState<"host" | "route" | null>(null);
  const [changed, setChanged] = useState(false);
  const [discard, setDiscard] = useState(false);
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
  const resumeHostUrl = useRef<string | null>(null);
  const portEdited = useRef(false);
  const { error: notifyError } = useNotifier("relay", "Relay");

  const load = useCallback(async () => {
    if (inFlight.current || saving.current) return;
    inFlight.current = true; setReading(true);
    const run = generation.current;
    try {
      const data = relaySnapshot(await request("/network/relays", { cache: "no-store" }));
      if (run === generation.current) {
        setStatus(data); setFailed(false);
        const added = data.hosts.find(item => item.agent_url === resumeHostUrl.current);
        if (added) { setHostId(added.id); resumeHostUrl.current = null; }
      }
    } catch { if (run === generation.current) setFailed(true); }
    finally { if (run === generation.current) setReading(false); inFlight.current = false; }
  }, [request]);
  useEffect(() => {
    const current = generation;
    const timer = window.setTimeout(() => void load(), 0);
    return () => { window.clearTimeout(timer); current.current++; };
  }, [load, refreshKey]);
  useEffect(() => { onDirtyChange(changed); }, [changed, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  useEffect(() => {
    if (!changed) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, [changed]);
  useEffect(() => {
    if (!operation?.action?.startsWith("relay-") || systemActionNeedsPolling(operation)) return;
    if (pending && operation.id !== pending) return;
    const timer = window.setTimeout(() => { setPending(null); void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, [operation, pending, load]);
  const locked = busy || sending || reading || failed || !status || Boolean(pending);
  const host = status?.hosts.find(item => item.id === hostId);
  const [transport, targetPort] = target.split(":");
  const portConflict = Boolean(host?.routes.some(route => route.transport === transport && route.listen_port === Number(port)));
  const validTarget = Boolean(status?.targets.some(item => item.transport === transport && item.port === Number(targetPort)));

  async function submit(path: string, init: RequestInit) {
    if (saving.current) return false;
    saving.current = true; generation.current++; setSending(true);
    try {
      const started = await admit(path, init);
      setPending(systemActionNeedsPolling(started) ? started.id || null : null);
      onOperation(started);
      return true;
    } catch (cause) {
      if (cause instanceof SystemOperationUnknown) { setPending(cause.id); return true; }
      notifyError(cause instanceof Error ? cause.message : "Не удалось начать операцию relay");
      return false;
    } finally { saving.current = false; setSending(false); }
  }
  function resetDialog() {
    setDialog(null); setChanged(false); setName(""); setConnection(""); setEdit(null);
    setTarget(""); resumeHostUrl.current = null; portEdited.current = false;
  }
  function close() { if (changed) setDiscard(true); else resetDialog(); }
  function suggestPort(selected: RelayHost | undefined, protocol: string) {
    const used = new Set(selected?.routes.filter(route => route.transport === protocol).map(route => route.listen_port));
    for (let candidate = 20000; candidate <= 20999; candidate++) if (!used.has(candidate)) return String(candidate);
    return "";
  }
  function openRoute(selected?: RelayHost) {
    const available = selected || status?.hosts.find(item => item.state === "connected");
    setHostId(available?.id || ""); setTarget(""); setPort(suggestPort(available, "tcp")); portEdited.current = false;
    setDialog(status?.hosts.length ? "route" : "host"); setEdit(null); setName(""); setConnection(""); setChanged(false);
  }
  function openHost(selected?: RelayHost) {
    setEdit(selected || null); setName(selected?.name || ""); setConnection(""); setDialog("host"); setChanged(false);
  }
  async function saveHost() {
    if (locked || !status || status.pending && !edit) return;
    try {
      const params = relayConnection(connection);
      setConnection("");
      const accepted = await submit(status.pending ? "/network/relays/reconnect" : "/network/relays", { method: "PUT", body: JSON.stringify({ ...params, name: name.trim(), id: edit?.id || null, expected_revision: status.revision }) });
      if (accepted) {
        if (edit) resetDialog();
        else {
          resumeHostUrl.current = params.agent_url; setHostId(""); setName(""); setChanged(false);
          setTarget(""); setPort("20000"); portEdited.current = false; setDialog("route");
        }
      }
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Некорректные параметры подключения"); }
  }
  async function saveRoute() {
    if (locked || !status || status.pending || !host || host.state !== "connected" || host.agent_revision === undefined || portConflict || !validTarget) return;
    if (await submit(`/network/relays/${host.id}/routes`, { method: "POST", body: JSON.stringify({ transport, target_port: Number(targetPort), listen_port: Number(port), expected_revision: status.revision, agent_revision: host.agent_revision }) })) resetDialog();
  }
  async function exportFile(file: File, selectedHost: RelayHost, route: RelayRoute) {
    if (locked || status?.pending || route.state !== "listening") return;
    try {
      if (file.size > 262144) throw new Error("Размер файла должен быть не более 256 КБ");
      const value = await request<{ config: string; replaced: number }>(`/network/relays/${selectedHost.id}/routes/${route.id}/export`, { method: "POST", body: JSON.stringify({ config: await file.text() }) });
      if (typeof value.config !== "string" || typeof value.replaced !== "number" || value.replaced < 1) throw new Error("Некорректный ответ экспорта");
      const url = URL.createObjectURL(new Blob([value.config], { type: "text/plain;charset=utf-8" }));
      const link = document.createElement("a"); link.href = url; link.download = file.name.replace(/(\.[^.]+)?$/, "-relay$1"); link.click(); URL.revokeObjectURL(url);
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось подготовить relay-копию"); }
  }
  function removeConfirmed(confirmed: boolean) {
    const selected = confirmation; setConfirmation(null);
    if (!confirmed || !selected || locked || !status || status.pending || selected.route && selected.host.agent_revision === undefined) return;
    const path = `/network/relays/${selected.host.id}` + (selected.route ? `/routes/${selected.route.id}?agent_revision=${selected.host.agent_revision}` : "");
    void submit(path, { method: "DELETE", headers: { "If-Match": status.revision } });
  }
  return {
    status, failed, reading, locked, load, submit, host, hostId, target, port, consumerIds, confirmation,
    dialog, name, connection, edit, changed, discard, portConflict, validTarget,
    openRoute, openHost, close, resetDialog, saveHost, saveRoute, exportFile, removeConfirmed,
    setDiscard, setConfirmation, setConsumerIds,
    changeName: (value: string) => { setName(value); setChanged(true); },
    changeConnection: (value: string) => { setConnection(value); setChanged(true); },
    changePort: (value: string) => { setPort(value); portEdited.current = true; setChanged(true); },
    changeHost: (value: string) => { setHostId(value); if (!portEdited.current) setPort(suggestPort(status?.hosts.find(item => item.id === value), transport || "tcp")); setChanged(true); },
    changeTarget: (value: string) => { setTarget(value); if (!portEdited.current) setPort(suggestPort(host, value.split(":")[0])); setChanged(true); },
  };
}
type RelayControl = ReturnType<typeof useNetworkRelays>;

export function NetworkRelayNotice({ relay }: { relay: RelayControl }) {
  return <>
    {relay.failed && <p role="status" className="networkNotice">Relay UNKNOWN — обновление не удалось. Действия заблокированы до успешного чтения. <button disabled={relay.reading} onClick={() => void relay.load()}>Повторить чтение Relay</button></p>}
    {relay.status?.pending && <div className="networkNotice" role="status"><strong>Результат изменения relay не подтверждён.</strong><p>Проверка прочитает сохранённый маршрут с агента без повторной отправки команды.</p><button disabled={relay.locked} onClick={() => void relay.submit("/network/relays/reconcile", { method: "POST" })}>Проверить результат операции</button></div>}
  </>;
}

export function NetworkRelayRows({ relay, query, expanded, onToggle }: { relay: RelayControl; query: string; expanded: string[]; onToggle: (key: string) => void }) {
  const status = relay.status;
  if (!status) return null;
  return <>{status.hosts.filter(item => relayMatches(item, query)).map(item => {
    const key = `relay-${item.id}`;
    const open = expanded.includes(key);
    const address = new URL(item.agent_url).hostname;
    const unknown = relay.failed || item.state !== "connected";
    return <Fragment key={item.id}>
      <tr className={`networkRouteRow${open ? " is-expanded" : ""}`}>
        <td><button type="button" className="networkRouteExpand" aria-expanded={open} onClick={() => onToggle(key)}><span className="networkRouteExpandMark" aria-hidden="true" /><span><strong>{address}</strong><small>{item.name} · Relay Agent · правил: {item.routes.length}</small></span></button></td>
        <td><span className={`networkRelayState ${unknown ? "unknown" : "connected"}`}>{unknown ? "UNKNOWN" : "CONNECTED"}</span><small>{unknown ? "Агент не подтверждён" : item.routes.length ? `${item.routes.filter(route => route.state === "listening").length}/${item.routes.length} портов LISTENING` : "Порты не настроены"}</small></td>
        <td><span>RELAY</span><small>Пересылка на этот VPS</small></td>
        <td><div className="relayPortList">{item.routes.length ? item.routes.map(route => <code key={route.id}>{route.transport.toUpperCase()} :{route.listen_port}</code>) : <small>Порты не настроены</small>}</div></td>
        <td><button type="button" disabled={relay.locked} onClick={() => { if (!open) onToggle(key); relay.openRoute(item); }}>Настроить</button></td>
      </tr>
      {open && <tr className="networkRouteDetailRow"><td colSpan={5}><div className="networkRouteCascade">
        <header className="networkBlockHeader"><div><p className="eyebrow networkSectionEyebrow">RELAY AGENT</p><strong>{item.name} · {address}</strong><p><small>{unknown ? "UNKNOWN" : `Agent ${item.version}`} · {item.agent_url}</small></p></div><div className="relayActions"><button disabled={relay.locked || status.pending || unknown} onClick={() => relay.openRoute(item)}>Добавить порт</button><button disabled={relay.locked} onClick={() => relay.openHost(item)}>Подключение к агенту</button><button disabled={relay.locked || status.pending || item.routes.length > 0} onClick={() => relay.setConfirmation({ host: item })}>Удалить узел из PRO</button></div></header>
        <details className="relayFingerprint"><summary>Сертификат и управление</summary><p>SHA-256 {item.certificate_sha256}</p><p>Маршрутов вне управления этого PRO: {item.foreign_routes}.</p></details>
        {item.error && <p role="status">{item.error}</p>}
        {!item.routes.length && <p>Добавьте TCP/UDP-порт, чтобы пересылать трафик на этот VPS.</p>}
        {item.routes.map(route => <div className="relayRoute" key={route.id}>
          <div><strong>{route.transport.toUpperCase()} · {address}:{route.listen_port}</strong><p>→ {route.target_ip}:{route.target_port}</p><small>{relay.failed ? "UNKNOWN" : route.state.toUpperCase()} · порт на relay</small></div>
          <div><strong>Протокол: {unknown || route.state !== "listening" ? "UNKNOWN" : route.check?.state.toUpperCase() || "UNCHECKED"}</strong>{route.check && <p><small>{route.check.consumer_id} · {new Date(route.check.checked_at).toLocaleString("ru-RU")} · проверка с сервера, не из клиентской сети</small></p>}<p><small>Сессии: {relay.failed ? "—" : route.sessions ?? "—"} · Передано: {relay.failed ? "—" : route.sent_bytes ?? "—"} / {relay.failed ? "—" : route.received_bytes ?? "—"} байт</small></p></div>
          <div className="relayActions"><label>Подключение для проверки<select value={relay.consumerIds[route.id] || ""} disabled={relay.locked || status.pending} onChange={event => relay.setConsumerIds(current => ({ ...current, [route.id]: event.target.value }))}><option value="">Выберите подключение</option>{status.consumers?.map(consumer => <option key={consumer.id} value={consumer.id}>{consumer.label} · {consumer.id}</option>)}</select></label><button disabled={relay.locked || status.pending || route.state !== "listening" || !relay.consumerIds[route.id]} onClick={() => void relay.submit(`/network/relays/${item.id}/routes/${route.id}/check?consumer_id=${encodeURIComponent(relay.consumerIds[route.id])}`, { method: "POST", headers: { "If-Match": status.revision } })}>Проверить протокол</button></div>
          <div className="relayActions"><label className="relayFileButton">Скачать relay-копию<input type="file" accept=".conf,.json,.yaml,.yml,.txt" disabled={relay.locked || status.pending || route.state !== "listening"} onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void relay.exportFile(file, item, route); }} /></label><button disabled={relay.locked || status.pending || item.state !== "connected" || route.state === "drift"} onClick={() => relay.setConfirmation({ host: item, route })}>Удалить маршрут</button></div>
        </div>)}
        <p className="networkNotice">Для клиента загрузите его конфигурацию и скачайте relay-копию с новым адресом и портом. Ключи и TLS-имя сохраняются.</p>
      </div></td></tr>}
    </Fragment>;
  })}</>;
}

export function relayMatches(host: RelayHost, query: string) {
  return `${host.name} ${host.agent_url} relay ${host.routes.map(route => `${route.transport} ${route.listen_port} ${route.target_ip}:${route.target_port}`).join(" ")}`.toLowerCase().includes(query.trim().toLowerCase());
}

export function NetworkRelayDialogs({ relay }: { relay: RelayControl }) {
  const dialogRef = useDialogFocus(Boolean(relay.dialog), relay.close);
  const status = relay.status;
  return <>
    {relay.dialog && <div className="networkModalBackdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) relay.close(); }}><section ref={dialogRef} tabIndex={-1} className="networkModal relayModal" role="dialog" aria-modal="true" aria-labelledby="relay-dialog-title">
      <header className="networkSectionHeading networkModalHeader"><div><p className="eyebrow networkSectionEyebrow">ROUTES · RELAY</p><h2 id="relay-dialog-title">{relay.dialog === "host" ? relay.edit ? "Подключение к Relay Agent" : "Подключить Relay Agent" : "Добавить TCP / UDP-порт"}</h2></div><button className="networkModalClose" type="button" aria-label="Закрыть" onClick={relay.close}>×</button></header>
      <NetworkRelayNotice relay={relay} />
      {relay.dialog === "host" ? <form className="relayForm" onSubmit={event => { event.preventDefault(); void relay.saveHost(); }}>
        <p>Скопируйте JSON из раздела Agent на внешнем VPS. После проверки узла можно выбрать порт для пересылки.</p>
        <label>Название узла<input required maxLength={64} value={relay.name} onChange={event => relay.changeName(event.target.value)} disabled={relay.locked || status?.pending && !relay.edit} /></label>
        <label>JSON подключения<input type="password" autoComplete="new-password" spellCheck={false} required value={relay.connection} onChange={event => relay.changeConnection(event.target.value)} disabled={relay.locked || status?.pending && !relay.edit} placeholder="Параметры из раздела Agent на узле" /></label>
        <small>Содержит адрес, токен и отпечаток сертификата. Новый отпечаток применяется после явного сохранения и проверки узла.</small>
        <div className="relayActions"><button type="submit" disabled={relay.locked || status?.pending && !relay.edit || !relay.name.trim() || !relay.connection}>Проверить и сохранить</button><button type="button" onClick={relay.close}>Отмена</button></div>
      </form> : <form className="relayForm" onSubmit={event => { event.preventDefault(); void relay.saveRoute(); }}>
        <label>Relay-узел<select value={relay.hostId} onChange={event => relay.changeHost(event.target.value)} disabled={relay.locked || status?.pending} required><option value="">Выберите узел</option>{status?.hosts.map(item => <option key={item.id} value={item.id} disabled={item.state !== "connected"}>{item.name} · {new URL(item.agent_url).hostname}{item.state !== "connected" ? " · UNKNOWN" : ""}</option>)}</select></label>
        <button type="button" disabled={relay.locked || status?.pending} onClick={() => relay.openHost()}>Подключить другой Relay Agent</button>
        <label>Назначение на этом VPS<select value={relay.target} onChange={event => relay.changeTarget(event.target.value)} disabled={relay.locked || status?.pending} required><option value="">Выберите публичный порт протокола</option>{status?.targets.map(item => <option key={`${item.transport}:${item.port}`} value={`${item.transport}:${item.port}`}>{item.transport.toUpperCase()} · {status.origin_ip}:{item.port}</option>)}</select></label>
        <label>Внешний порт relay<input type="number" min={20000} max={20999} required value={relay.port} onChange={event => relay.changePort(event.target.value)} disabled={relay.locked || status?.pending} /></label>
        <small>Предложен свободный порт среди правил этого PRO, диапазон 20000–20999. TCP и UDP учитываются отдельно; занятость другими правилами проверит агент.</small>
        {relay.portConflict && <p role="status">Этот порт уже занят правилом {relay.target.split(":")[0].toUpperCase()} на выбранном узле.</p>}
        {relay.host && relay.validTarget && <div className="networkNotice relayPreview"><strong>{relay.target.split(":")[0].toUpperCase()} · {new URL(relay.host.agent_url).hostname}:{relay.port || "…"} → {status?.origin_ip}:{relay.target.split(":")[1]}</strong></div>}
        {relay.target === "tcp:443" && <p className="networkNotice">TCP 443 также может передавать доступ к публичным сайтам и панели этого VPS. TLS проходит до origin.</p>}
        <div className="relayActions"><button type="submit" disabled={relay.locked || status?.pending || relay.host?.state !== "connected" || !relay.validTarget || relay.portConflict}>Создать маршрут</button><button type="button" onClick={relay.close}>Отмена</button></div>
      </form>}
    </section></div>}
    {relay.discard && <ConfirmationDialog request={{ title: "Закрыть без сохранения?", message: "Изменения настройки Relay будут потеряны.", cancelLabel: "Остаться", confirmLabel: "Уйти без сохранения" }} onClose={confirmed => { relay.setDiscard(false); if (confirmed) relay.resetDialog(); }} />}
    {relay.confirmation && <ConfirmationDialog request={{ title: relay.confirmation.route ? "Удалить маршрут relay?" : "Удалить узел из PRO?", message: relay.confirmation.route ? `Порт ${relay.confirmation.route.listen_port}/${relay.confirmation.route.transport} на ${relay.confirmation.host.name} закроется. Активные соединения этого маршрута прервутся; его клиентские копии перестанут подключаться. Настройки origin сохранятся.` : `Параметры и токен ${relay.confirmation.host.name} будут удалены из PRO. Сам агент продолжит работать.`, confirmLabel: "Удалить", danger: true }} onClose={relay.removeConfirmed} />}
  </>;
}
