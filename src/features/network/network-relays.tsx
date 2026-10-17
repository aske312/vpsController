"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { ConfirmationDialog } from "../../shared/components/confirmation-dialog";
import { useDialogFocus } from "../../shared/components/use-dialog-focus";
import { useNotifier } from "../../shared/notifications/notification-center";
import { SystemOperationUnknown, systemActionNeedsPolling } from "../../control-panel/system-operation";
import type { SystemAction } from "../../shared/types/control-plane";
import type { NetworkAdmission, NetworkRequest } from "./network-api";
import { relayConnection, relaySnapshot, type RelayStatus, type RelayHost } from "./relay-state";
import { NetworkRouteBadge } from "./network-route-badge";

type Props = { request: NetworkRequest; admit: NetworkAdmission; operation?: SystemAction; onOperation: (value: SystemAction) => void; busy: boolean; onDirtyChange: (dirty: boolean) => void; refreshKey?: number };

export function useNetworkRelays({ request, admit, operation, onOperation, busy, onDirtyChange, refreshKey }: Props) {
  const [status, setStatus] = useState<RelayStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const [reading, setReading] = useState(false);
  const [sending, setSending] = useState(false);
  const [dialog, setDialog] = useState(false);
  const [embedded, setEmbedded] = useState(false);
  const [changed, setChanged] = useState(false);
  const [discard, setDiscard] = useState(false);
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [connection, setConnection] = useState("");
  const [edit, setEdit] = useState<RelayHost | null>(null);
  const [confirmation, setConfirmation] = useState<RelayHost | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const generation = useRef(0);
  const inFlight = useRef<{ controller: AbortController; run: number } | null>(null);
  const hasSnapshot = useRef(false);
  const saving = useRef(false);
  const terminalRead = useRef("");
  const { error: notifyError } = useNotifier("relay", "Relay");
  const load = useCallback(async (force = false) => {
    if (saving.current || inFlight.current && !force) return;
    inFlight.current?.controller.abort();
    const job = { controller: new AbortController(), run: ++generation.current };
    inFlight.current = job; setReading(true);
    const current = () => job.run === generation.current && !job.controller.signal.aborted;
    let livePublished = false;
    if (!hasSnapshot.current || force) {
      void request("/network/relays?view=inventory", { cache: "no-store", signal: job.controller.signal })
        .then(relaySnapshot).then(data => {
          if (current() && !livePublished) { hasSnapshot.current = true; setStatus(data); }
        }).catch(() => { /* The live read remains authoritative. */ });
    }
    try {
      const data = relaySnapshot(await request("/network/relays", { cache: "no-store", signal: job.controller.signal }));
      if (current()) { livePublished = true; hasSnapshot.current = true; setStatus(data); setFailed(false); }
    } catch { if (current()) setFailed(true); }
    finally {
      if (current()) setReading(false);
      if (inFlight.current === job) inFlight.current = null;
    }
  }, [request]);
  useEffect(() => {
    hasSnapshot.current = false;
    return () => { generation.current++; inFlight.current?.controller.abort(); inFlight.current = null; };
  }, [request]);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
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
    const key = `${operation.id}:${operation.state}`;
    if (terminalRead.current === key) return;
    const timer = window.setTimeout(() => { terminalRead.current = key; setPending(null); void load(true); }, 0);
    return () => window.clearTimeout(timer);
  }, [operation, pending, load]);
  const locked = busy || sending || reading || failed || !status || Boolean(pending);
  async function submit(path: string, init: RequestInit) {
    if (saving.current) return false;
    saving.current = true; generation.current++; inFlight.current?.controller.abort(); inFlight.current = null; setReading(false); setSending(true);
    try {
      const started = await admit(path, init);
      setPending(systemActionNeedsPolling(started) ? started.id || null : null); onOperation(started); return true;
    } catch (cause) {
      if (cause instanceof SystemOperationUnknown) { setPending(cause.id); return true; }
      notifyError(cause instanceof Error ? cause.message : "Не удалось начать операцию Relay"); return false;
    } finally { saving.current = false; setSending(false); }
  }
  function resetDialog() { setDialog(false); setChanged(false); setName(""); setConnection(""); setAddress(""); setEdit(null); }
  function close() { if (changed) setDiscard(true); else resetDialog(); }
  function openHost(selected?: RelayHost, inline = false) {
    setEdit(selected || null); setName(selected?.name || ""); setAddress(selected?.client_address || "");
    setConnection(""); setEmbedded(inline); setDialog(true); setChanged(false);
  }
  async function saveHost() {
    if (locked || !status || status.pending && !edit) return;
    try {
      const params = connection ? relayConnection(connection) : edit ? { agent_url: edit.agent_url, certificate_sha256: edit.certificate_sha256, api_version: 1, token: "" } : relayConnection("");
      setConnection("");
      if (await submit(status.pending ? "/network/relays/reconnect" : "/network/relays", { method: "PUT", body: JSON.stringify({ ...params, name: name.trim(), client_address: address.trim(), id: edit?.id || null, expected_revision: status.revision }) })) resetDialog();
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Некорректные параметры подключения"); }
  }
  function removeConfirmed(confirmed: boolean) {
    const selected = confirmation; setConfirmation(null);
    if (!confirmed || !selected || locked || !status || status.pending) return;
    void submit(`/network/relays/${selected.id}`, { method: "DELETE", headers: { "If-Match": status.revision } });
  }
  return {
    status, failed, reading, locked, load, submit, dialog, embedded, name, address, connection, edit, changed, discard, confirmation,
    openHost, close, resetDialog, saveHost, removeConfirmed, setDiscard, setConfirmation,
    changeName: (value: string) => { setName(value); setChanged(true); },
    changeAddress: (value: string) => { setAddress(value); setChanged(true); },
    changeConnection: (value: string) => { setConnection(value); setChanged(true); },
  };
}
export type RelayControl = ReturnType<typeof useNetworkRelays>;

export function NetworkRelayNotice({ relay }: { relay: RelayControl }) {
  return <>
    {relay.failed && <p role="status" className="networkNotice">Relay UNKNOWN — обновление не удалось. Действия заблокированы до успешного чтения. <button disabled={relay.reading} onClick={() => void relay.load()}>Повторить чтение Relay</button></p>}
    {relay.status?.pending && <div className="networkNotice" role="status"><strong>Результат изменения Relay не подтверждён.</strong><p>Проверка прочитает состояние агента без повторной отправки команды.</p><button disabled={relay.locked} onClick={() => void relay.submit("/network/relays/reconcile", { method: "POST" })}>Проверить результат операции</button></div>}
  </>;
}

export function NetworkRelayRows({ relay, query, expanded, onToggle }: { relay: RelayControl; query: string; expanded: string[]; onToggle: (key: string) => void }) {
  return <>{relay.status?.hosts.filter(item => relayMatches(item, query)).map(item => {
    const key = `relay-${item.id}`, open = expanded.includes(key), address = item.client_address || new URL(item.agent_url).hostname;
    const unknown = relay.failed || item.state !== "connected";
    return <Fragment key={item.id}>
      <tr className={`networkRouteRow networkRelayRow${open ? " is-expanded" : ""}`}>
        <td><button type="button" className="networkRouteExpand" aria-expanded={open} onClick={() => onToggle(key)}><span className="networkRouteExpandMark" aria-hidden="true" /><span><strong>{address}</strong><small>{item.name} · Relay Agent</small></span></button></td>
        <td><NetworkRouteBadge tone={unknown ? "unchecked" : "active"}>{unknown ? relay.reading ? "Проверяем…" : "UNKNOWN" : "CONNECTED"}</NetworkRouteBadge></td>
        <td><span>RELAY · {item.routes.length}</span><small>{unknown ? "Порты неизвестны" : `Слушают ${item.routes.filter(route => route.state === "listening").length}/${item.routes.length}`}</small></td>
        <td><div className="networkRouteTags"><span className="networkRouteTag">TCP {item.routes.filter(route => route.transport === "tcp").length}</span><span className="networkRouteTag">UDP {item.routes.filter(route => route.transport === "udp").length}</span></div></td>
        <td><div className="networkRelayActions"><button type="button" className="networkRouteConfigure" disabled={relay.locked} onClick={() => relay.openHost(item)}>Настроить</button><button type="button" className="networkRouteDelete" disabled={relay.locked || relay.status?.pending} onClick={() => relay.setConfirmation(item)}>Удалить</button></div></td>
      </tr>
      {open && <tr className="networkRouteDetailRow"><td colSpan={5}><div className="relayCompactSummary">
        <span title={item.agent_url}>Agent {unknown ? "?" : item.version}</span>
        <span title={`SHA-256 ${item.certificate_sha256}`}>PIN</span>
        <span>{unknown ? "Порты ?" : `${item.routes.filter(route => route.state === "listening").length}/${item.routes.length} LISTEN`}</span>
        {item.foreign_routes > 0 && <span>{item.foreign_routes} вне PRO</span>}
        {item.error && <p role="status">{item.error}</p>}
      </div></td></tr>}
    </Fragment>;
  })}</>;
}

export function relayMatches(host: RelayHost, query: string) {
  return `${host.name} ${host.agent_url} ${host.client_address || ""} relay`.toLowerCase().includes(query.trim().toLowerCase());
}

export function NetworkRelayDialogs({ relay }: { relay: RelayControl }) {
  const dialogRef = useDialogFocus(relay.dialog && !relay.embedded, relay.close);
  return <>
    {relay.dialog && !relay.embedded && <div className="networkModalBackdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) relay.close(); }}><section ref={dialogRef} tabIndex={-1} className="networkModal relayModal" role="dialog" aria-modal="true" aria-labelledby="relay-dialog-title">
      <header className="networkSectionHeading networkModalHeader"><div><p className="eyebrow networkSectionEyebrow">ROUTES · RELAY</p><h2 id="relay-dialog-title">{relay.edit ? "Подключение к Relay Agent" : "Подключить Relay Agent"}</h2></div><button className="networkModalClose" type="button" aria-label="Закрыть" onClick={relay.close}>×</button></header>
      <NetworkRelayForm relay={relay} />
    </section></div>}
    {relay.discard && <ConfirmationDialog request={{ title: "Закрыть без сохранения?", message: "Изменения подключения к Relay будут потеряны.", cancelLabel: "Остаться", confirmLabel: "Уйти без сохранения" }} onClose={confirmed => { relay.setDiscard(false); if (confirmed) relay.resetDialog(); }} />}
    {relay.confirmation && <ConfirmationDialog request={{ title: "Удалить Relay?", message: `Узел ${relay.confirmation.name} и его управляемые пересылки (${relay.confirmation.routes.length}) будут удалены. Настройки клиентов и профилей сохранятся, Relay станет недоступен. Сам агент и чужие маршруты сохранятся.`, confirmLabel: "Удалить", danger: true }} onClose={relay.removeConfirmed} />}
  </>;
}

export function NetworkRelayForm({ relay }: { relay: RelayControl }) {
  return <div className="relayConnectionEditor">
    <NetworkRelayNotice relay={relay} />
    <p className="relayConnectionHint">Подключите установленный Relay Agent. TCP/UDP и внешний порт будут выбраны автоматически для протокола.</p>
    <form className="relayForm" onSubmit={event => { event.preventDefault(); void relay.saveHost(); }}>
      <label>Название узла<input required maxLength={64} value={relay.name} onChange={event => relay.changeName(event.target.value)} disabled={relay.locked || relay.status?.pending && !relay.edit} placeholder="Например, Relay Amsterdam" /></label>
      <label>IP или домен для клиентов<input maxLength={253} value={relay.address} onChange={event => relay.changeAddress(event.target.value)} disabled={relay.locked} placeholder="IP агента или relay.example.com" /></label>
      <small>Пустое поле использует IP агента. Для домена: DNS-запись на IP агента, без CDN proxy. Порт указывать не нужно.</small>
      <label>JSON подключения Agent<input type="password" autoComplete="new-password" spellCheck={false} required={!relay.edit} value={relay.connection} onChange={event => relay.changeConnection(event.target.value)} disabled={relay.locked || relay.status?.pending && !relay.edit} placeholder={relay.edit ? "Новые параметры для замены токена / сертификата" : "Вставьте JSON из раздела Agent на Relay-сервере"} /></label>
      <small>Адрес API, токен и отпечаток сертификата. Сохранённый токен в браузер не возвращается.</small>
      <div className="relayActions"><button type="submit" disabled={relay.locked || relay.status?.pending && !relay.edit || !relay.name.trim() || !relay.edit && !relay.connection}>Проверить и подключить</button><button type="button" onClick={relay.close}>Отмена</button></div>
    </form>
  </div>;
}
