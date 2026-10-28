"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ConfirmationDialog } from "../../shared/components/confirmation-dialog";
import { useDialogFocus } from "../../shared/components/use-dialog-focus";
import { useNotifier } from "../../shared/notifications/notification-center";
import { SystemOperationUnknown, systemActionNeedsPolling } from "../../control-panel/system-operation";
import type { SystemAction } from "../../shared/types/control-plane";
import type { NetworkAdmission, NetworkRequest } from "./network-api";
import { relayAddresses, relayConnection, relaySnapshot, type RelayStatus, type RelayHost } from "./relay-state";

import { RouteTypeIcon } from "./route-type-icon";

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
  const [addressConfirmation, setAddressConfirmation] = useState<{ host: RelayHost; address: string } | null>(null);
  const [recoveryConfirmation, setRecoveryConfirmation] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const generation = useRef(0);
  const inFlight = useRef<{ controller: AbortController; run: number } | null>(null);
  const hasSnapshot = useRef(false);
  const saving = useRef(false);
  const terminalRead = useRef("");
  const { error: notifyError } = useNotifier("relay", "Relay");
  const cancelLoad = useCallback(() => {
    generation.current++;
    inFlight.current?.controller.abort();
    inFlight.current = null;
  }, []);
  const load = useCallback(async (force = false) => {
    if (saving.current || inFlight.current && !force) return;
    inFlight.current?.controller.abort();
    const job = { controller: new AbortController(), run: ++generation.current };
    inFlight.current = job; setReading(!hasSnapshot.current);
    const current = () => job.run === generation.current && !job.controller.signal.aborted;
    let livePublished = false;
    if (!hasSnapshot.current) {
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
    return cancelLoad;
  }, [request, cancelLoad]);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load, refreshKey]);
  const testingDevice = status?.hosts.some(host => host.routes.some(route => ["preparing", "awaiting_client", "processing"].includes(route.device_test?.state || "")) || host.agent_update?.polling);
  useEffect(() => {
    if (!testingDevice) return;
    const timer = window.setInterval(() => { if (document.visibilityState !== "hidden") void load(); }, 3000);
    return () => window.clearInterval(timer);
  }, [testingDevice, load]);
  useEffect(() => { onDirtyChange(changed); }, [changed, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  useEffect(() => {
    if (!changed) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, [changed]);
  useEffect(() => {
    if (!(operation?.action?.startsWith("relay-") || operation?.action === "mihomo-profile-relay-prepare") || systemActionNeedsPolling(operation)) return;
    if (pending && operation.id !== pending) return;
    const key = `${operation.id}:${operation.state}`;
    if (terminalRead.current === key) return;
    const timer = window.setTimeout(() => { terminalRead.current = key; setPending(null); void load(true); }, 0);
    return () => window.clearTimeout(timer);
  }, [operation, pending, load]);
  const locked = busy || sending || reading || failed || !status || Boolean(pending);
  async function submit(path: string, init: RequestInit) {
    if (saving.current) return false;
    saving.current = true; cancelLoad(); setReading(false); setSending(true);
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
    if (locked || !status || status.pending && !edit) return false;
    try {
      const params = connection ? relayConnection(connection) : edit ? { agent_url: edit.agent_url, certificate_sha256: edit.certificate_sha256, api_version: 1, token: "" } : relayConnection("");
      setConnection("");
      if (await submit(status.pending ? "/network/relays/reconnect" : "/network/relays", { method: "PUT", body: JSON.stringify({ ...params, name: name.trim(), client_address: address.trim(), id: edit?.id || null, expected_revision: status.revision }) })) { resetDialog(); return true; }
      return false;
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Некорректные параметры подключения"); return false; }
  }
  function removeConfirmed(confirmed: boolean) {
    const selected = confirmation; setConfirmation(null);
    if (!confirmed || !selected || locked || !status || status.pending) return;
    void submit(`/network/relays/${selected.id}`, { method: "DELETE", headers: { "If-Match": status.revision } });
  }
  async function downloadTest(hostId: string, routeId: string, testId: string) {
    try {
      const kit = await request<{ filename: string; content_base64: string }>(`/network/relays/${hostId}/routes/${routeId}/device-tests/${testId}/kit`, { cache: "no-store" });
      if (typeof kit.content_base64 !== "string" || kit.content_base64.length > 100000 || !/^relay-test-[a-f0-9]{8}\.zip$/.test(kit.filename)) throw new Error("Некорректный комплект теста");
      const bytes = Uint8Array.from(atob(kit.content_base64), char => char.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/zip" }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = kit.filename; anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось скачать тест"); }
  }
  async function uploadTest(hostId: string, routeId: string, testId: string, file: File) {
    try {
      if (file.size > 16384) throw new Error("Результат теста слишком большой");
      const report: unknown = JSON.parse(await file.text());
      await submit(`/network/relays/${hostId}/routes/${routeId}/device-tests/${testId}/report`, { method: "POST", headers: { "If-Match": status?.revision || "" }, body: JSON.stringify(report) });
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось прочитать результат теста"); }
  }
  return {
    status, failed, reading, locked, load, submit, downloadTest, uploadTest, dialog, embedded, name, address, connection, edit, changed, discard, confirmation, addressConfirmation, setAddressConfirmation,
    openHost, close, resetDialog, saveHost, removeConfirmed, setDiscard, setConfirmation, recoveryConfirmation, setRecoveryConfirmation,
    changeName: (value: string) => { setName(value); setChanged(true); },
    changeAddress: (value: string) => { setAddress(value); setChanged(true); },
    changeConnection: (value: string) => { setConnection(value); setChanged(true); },
  };
}
export type RelayControl = ReturnType<typeof useNetworkRelays>;

export function NetworkRelayNotice({ relay }: { relay: RelayControl }) {
  const recovery = relay.status?.recovery;
  const host = relay.status?.hosts.find(item => item.id === recovery?.host_id);
  return <>
    {relay.failed && <p role="status" className="networkNotice">Relay UNKNOWN — обновление не удалось. Действия заблокированы до успешного чтения. <button disabled={relay.reading} onClick={() => void relay.load()}>Повторить чтение Relay</button></p>}
    {relay.status?.pending && <div className="networkNotice" role="status"><strong>Результат изменения Relay не подтверждён{host ? ` · ${host.name} (${host.client_address || new URL(host.agent_url).hostname})` : ""}.</strong>
      {recovery && <p>{recovery.action === "put" ? "Создание" : "Удаление"} {recovery.transport.toUpperCase()} {recovery.listen_port} → {recovery.target_ip}:{recovery.target_port}. {recovery.error === "response_unknown" ? "Ответ на команду не получен." : `Агент ответил HTTP ${recovery.error.replace("agent_http_", "")}.`}</p>}
      <p>Проверка только прочитает состояние. Восстановление отдельно повторит сохранённую команду с тем же ID и ревизией, если результат ещё не подтверждён. После восстановления повторно сохраните настройки профиля.</p>
      <button disabled={relay.locked} onClick={() => void relay.submit("/network/relays/reconcile", { method: "POST" })}>Проверить результат операции</button>
      {recovery && <button disabled={relay.locked} onClick={() => relay.setRecoveryConfirmation(true)}>Восстановить пересылку</button>}
    </div>}
  </>;
}

export function relayMatches(host: RelayHost, query: string) {
  return relayAddresses(host).some(address => relayAddressMatches(host, address, query));
}

function relayAddressMatches(host: RelayHost, address: string, query: string) {
  return `${host.name} ${host.agent_url} ${address} relay`.toLowerCase().includes(query.trim().toLowerCase());
}

export function NetworkRelayDialogs({ relay }: { relay: RelayControl }) {
  const dialogRef = useDialogFocus(relay.dialog && !relay.embedded, relay.close);
  return <>
    {relay.addressConfirmation && <ConfirmationDialog request={{ title: "Удалить адрес Relay?", message: `Адрес ${relay.addressConfirmation.address} узла ${relay.addressConfirmation.host.name} будет исключён из списка. Другие адреса, пересылки и существующие подключения сохранятся.`, confirmLabel: "Удалить адрес", danger: true }} onClose={confirmed => {
      const selected = relay.addressConfirmation;
      relay.setAddressConfirmation(null);
      if (confirmed && selected && !relay.locked && relay.status && !relay.status.pending) void relay.submit(`/network/relays/${selected.host.id}/address?address=${encodeURIComponent(selected.address)}`, { method: "DELETE", headers: { "If-Match": relay.status.revision } });
    }} />}
    {relay.recoveryConfirmation && relay.status?.recovery && <ConfirmationDialog request={{ title: "Восстановить пересылку Relay?", message: "Сначала будет прочитан точный маршрут. При необходимости повторится только сохранённая команда с прежними ID, параметрами и ревизией. Дубликат маршрута не создаётся; при изменении агента операция остановится. Настройки профиля затем нужно сохранить повторно.", confirmLabel: "Восстановить пересылку" }} onClose={confirmed => {
      relay.setRecoveryConfirmation(false);
      if (confirmed && !relay.locked && relay.status?.recovery) void relay.submit("/network/relays/resume", { method: "POST", body: JSON.stringify({ pending_operation_id: relay.status.recovery.operation_id }) });
    }} />}
    {relay.dialog && !relay.embedded && <div className="networkModalBackdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) relay.close(); }}><section ref={dialogRef} tabIndex={-1} className="networkModal relayModal relaySettingsModal" role="dialog" aria-modal="true" aria-labelledby="relay-dialog-title">
      <header className="networkSectionHeading networkModalHeader"><div className="relaySettingsHeading"><span className="routeModalSymbol"><RouteTypeIcon kind="relay" /></span><div><p className="eyebrow networkSectionEyebrow">RELAY AGENT</p><h2 id="relay-dialog-title">{relay.edit ? "Настройки Relay" : "Подключить Relay"}</h2><p>{relay.edit?.name || "Параметры узла и доступа к Agent"}</p></div></div><button className="networkModalClose" type="button" aria-label="Закрыть" onClick={relay.close}>×</button></header>
      <NetworkRelayForm relay={relay} />
    </section></div>}
    {relay.discard && <ConfirmationDialog request={{ title: "Закрыть без сохранения?", message: "Изменения подключения к Relay будут потеряны.", cancelLabel: "Остаться", confirmLabel: "Уйти без сохранения" }} onClose={confirmed => { relay.setDiscard(false); if (confirmed) relay.resetDialog(); }} />}
    {relay.confirmation && <ConfirmationDialog request={{ title: "Удалить Relay?", message: `Узел ${relay.confirmation.name} и его управляемые пересылки (${relay.confirmation.routes.length}) будут удалены. Настройки клиентов и профилей сохранятся, Relay станет недоступен. Сам агент и чужие маршруты сохранятся.`, confirmLabel: "Удалить", danger: true }} onClose={relay.removeConfirmed} />}
  </>;
}

export function NetworkRelayForm({ relay }: { relay: RelayControl }) {
  let preview: ReturnType<typeof relayConnection> | null = null;
  if (relay.connection.trim()) { try { preview = relayConnection(relay.connection); } catch { /* Validation is shown next to the field; credentials remain private. */ } }
  const invalid = Boolean(relay.connection.trim() && !preview);
  return <div className="relayConnectionEditor relaySettingsEditor">
    <NetworkRelayNotice relay={relay} />
    <form className="relayForm" onSubmit={event => { event.preventDefault(); if (!invalid) void relay.saveHost(); }}>
      <section className="relaySettingsSection"><header><h3>Узел</h3></header>
        <label>Название узла<input required maxLength={64} value={relay.name} onChange={event => relay.changeName(event.target.value)} disabled={relay.locked || relay.status?.pending && !relay.edit} placeholder="Например, Relay Amsterdam" /></label>
        <label>IP или домен для клиентов<input maxLength={253} value={relay.address} onChange={event => relay.changeAddress(event.target.value)} disabled={relay.locked} placeholder="По умолчанию — IP Agent" /></label>
        <small>Без CDN, схемы и порта.</small>
      </section>
      <section className="relaySettingsSection"><header><h3>Доступ к Agent</h3></header>
        {relay.edit && <dl className="relaySavedConnection"><div><dt>Agent</dt><dd>{relay.edit.agent_url}</dd></div><div><dt>Ключ доступа</dt><dd>{relay.edit.token_configured ? "Сохранён" : "Не настроен"}</dd></div></dl>}
        <details className="relayCredentialEditor" open={!relay.edit || undefined}><summary>{relay.edit ? relay.connection ? "Новые данные подключения введены" : "Заменить данные подключения" : "JSON подключения"}<span aria-hidden="true">+</span></summary><div>
          <label>JSON подключения Agent<input type="password" autoComplete="new-password" spellCheck={false} required={!relay.edit} value={relay.connection} onChange={event => relay.changeConnection(event.target.value)} disabled={relay.locked || relay.status?.pending && !relay.edit} aria-invalid={invalid || undefined} placeholder="Вставьте JSON из раздела Agent" /></label>
          {relay.edit && <small>Пустое поле сохранит текущий ключ.</small>}
          {invalid && <p role="status" className="relayFormValidation">JSON не распознан. Вставьте полные данные подключения Agent.</p>}
          {preview && <p className="relayConnectionPreview">Agent: <code>{preview.agent_url}</code></p>}
          {relay.connection && <button type="button" disabled={relay.locked} onClick={() => relay.changeConnection("")}>Убрать новые данные</button>}
        </div></details>
      </section>
      {!!relay.edit?.excluded_addresses?.length && <section className="relaySettingsSection"><header><h3>Исключённые адреса</h3></header><ul className="relayExcludedAddresses">{relay.edit.excluded_addresses.map(address => <li key={address}><code>{address}</code><button type="button" disabled={relay.locked || relay.status?.pending} onClick={() => {
        if (relay.edit && relay.status) void relay.submit(`/network/relays/${relay.edit.id}/address?address=${encodeURIComponent(address)}&restore=true`, { method: "DELETE", headers: { "If-Match": relay.status.revision } }).then(started => { if (started) relay.resetDialog(); });
      }}>Вернуть</button></li>)}</ul></section>}
      <footer className="relaySettingsFooter">{relay.edit && <button type="button" className="routeLibraryDanger" disabled={relay.locked || relay.status?.pending} onClick={() => relay.setConfirmation(relay.edit)}>Удалить Relay</button>}<div className="relayActions"><button type="button" onClick={relay.close}>Отмена</button><button type="submit" className="networkPrimaryButton" disabled={invalid || relay.locked || relay.status?.pending && !relay.edit || !relay.name.trim() || !relay.edit && !relay.connection}>{relay.edit ? "Проверить и сохранить" : "Подключить Relay"}</button></div></footer>
    </form>
  </div>;
}
