"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { SystemOperationUnknown, systemActionNeedsPolling } from "../../control-panel/system-operation";
import { useNotifier } from "../../shared/notifications/notification-center";
import type { NetworkCapabilityCheck, NetworkEndpointCheck, NetworkEndpointSettings, NetworkStatus, SystemAction } from "../../shared/types/control-plane";
import { appendNetworkEndpoints } from "./endpoint-additions";
import { useNetworkAddresses } from "./network-addresses";
import { deleteNetworkEndpoint, readNetworkControl, saveNetworkEndpoints, type NetworkAdmission, type NetworkRequest } from "./network-api";
import { type NetworkCloudflareControl } from "./network-cloudflare";
import { NetworkEndpoints } from "./network-endpoints";
import { NetworkRelayDialogs, useNetworkRelays } from "./network-relays";
import { NetworkRouteLibrary } from "./network-route-library";
import { needsAttention, routeEntries } from "./route-library-state";
import { serverAddressSummary } from "./route-summary";

export type NetworkListState = { query: string; expanded: string[] };

type Props = {
  cloudflare?: NetworkCloudflareControl;
  operation?: SystemAction;
  onOperation: (operation: SystemAction) => void;
  admitOperation: NetworkAdmission;
  listState: NetworkListState;
  onListStateChange: (state: NetworkListState) => void;
  request: NetworkRequest;
  onDirtyChange?: (dirty: boolean) => void;
  refreshKey?: number;
  onLoadingChange?: (loading: boolean, run?: number) => void;
};

function NetworkIcon() {
  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <rect x="11" y="3" width="10" height="8" rx="2" />
      <path d="M16 11v7M6 23v-5h20v5" />
      <rect x="2" y="23" width="8" height="6" rx="1.5" />
      <rect x="22" y="23" width="8" height="6" rx="1.5" />
    </svg>
  );
}

export function NetworkView({ request, admitOperation, refreshKey = 0, onLoadingChange, onDirtyChange, listState, onListStateChange, operation, onOperation, cloudflare }: Props) {
  const { error: notifyError } = useNotifier(
    "network",
    "Сеть",
  );
  const [status, setStatus] = useState<NetworkStatus | null>(null);
  const [endpointDraft, setEndpointDraft] =
    useState<NetworkEndpointSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [requestBusy, setBusy] = useState(false);
  const [pending, setPending] = useState<{ id?: string; kind: "endpoints" | "delete"; draft?: NetworkEndpointSettings } | null>(null);
  const operationBusy = requestBusy || Boolean(pending) || systemActionNeedsPolling(operation);
  const addresses = useNetworkAddresses({ request, busy: operationBusy, refreshKey });
  const busy = operationBusy || Boolean(addresses.pending);
  const [loadFailed, setLoadFailed] = useState(false);
  const [relayDirty, setRelayDirty] = useState(false);
  const [routeDirty, setRouteDirty] = useState(false);
  const relay = useNetworkRelays({ request, admit: admitOperation, operation, onOperation, busy: busy || loading || !status || loadFailed, onDirtyChange: setRelayDirty, refreshKey });
  const loadingRef = useRef(false);
  const loadingRun = useRef(0);
  const savingRef = useRef(false);
  const savedEndpointRef = useRef<NetworkEndpointSettings | null>(null);
  const endpointDirty = Boolean(
    status &&
    endpointDraft &&
    JSON.stringify(status.transport_endpoints) !==
      JSON.stringify(endpointDraft),
  );

  useEffect(() => { onDirtyChange?.(endpointDirty || relayDirty || routeDirty); }, [endpointDirty, relayDirty, routeDirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const load = useCallback(async (background = false) => {
    if (loadingRef.current || savingRef.current) return;
    loadingRef.current = true;
    const run = ++loadingRun.current;
    const showLoading = !background || !savedEndpointRef.current;
    if (showLoading) { onLoadingChange?.(true, run); setLoading(true); }
    try {
      const next = await readNetworkControl(request);
      if (run !== loadingRun.current) return;
      // The state updater may run after the refs below advance to this response.
      const previousEndpoints = savedEndpointRef.current;
      setStatus(next.network);
      setEndpointDraft((current) =>
        !current ||
        JSON.stringify(current) === JSON.stringify(previousEndpoints)
          ? next.network.transport_endpoints
          : current,
      );
      savedEndpointRef.current = next.network.transport_endpoints;
      setLoadFailed(false);
    } catch (cause) {
      if (run !== loadingRun.current) return;
      setLoadFailed(true);
      notifyError(
        cause instanceof Error
          ? cause.message
          : "Не удалось загрузить состояние сети",
      );
    } finally {
      loadingRef.current = false;
      if (showLoading) { setLoading(false); onLoadingChange?.(false, run); }
    }
  }, [notifyError, onLoadingChange, request]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(true);
  }, [load, refreshKey]);

  useEffect(() => {
    if (!pending?.id || operation?.id !== pending.id) return;
    if (!["succeeded", "failed", "cancelled"].includes(operation.state || "")) return;
    let cancelled = false;
    void (async () => {
      try {
        const next = await readNetworkControl(request);
        if (cancelled) return;
        setStatus(next.network);
        setLoadFailed(false);
        savedEndpointRef.current = next.network.transport_endpoints;
        if (operation.state === "succeeded") {
          setEndpointDraft((current) => pending.kind === "delete" || JSON.stringify(current) === JSON.stringify(pending.draft) ? next.network.transport_endpoints : current);
        }
      } catch (cause) { if (!cancelled) { setLoadFailed(true); notifyError(cause instanceof Error ? cause.message : "Не удалось проверить применённые настройки"); } }
      finally { if (!cancelled) setPending(null); }
    })();
    return () => { cancelled = true; };
  }, [pending, operation?.id, operation?.state, request, notifyError]);

  const saveEndpoints = useCallback(async (additions: NetworkEndpointSettings) => {
    if (!status || loadFailed || busy || savingRef.current || loading) return;
    const settings = appendNetworkEndpoints(status.transport_endpoints, additions);
    savingRef.current = true; loadingRun.current++;
    setBusy(true);
    try {
      const started = await saveNetworkEndpoints(admitOperation, settings);
      setEndpointDraft(settings);
      setPending({ id: started.id, kind: "endpoints", draft: settings });
      onOperation(started);
    } catch (cause) {
      if (cause instanceof SystemOperationUnknown) return;
      notifyError(
        cause instanceof Error
          ? cause.message
          : "Не удалось сохранить домены защищённых каналов",
      );
    } finally {
      savingRef.current = false;
      setBusy(false);
    }
  }, [admitOperation, busy, status, loadFailed, loading, notifyError, onOperation]);

  const removeEndpoint = useCallback(async (kind: NetworkEndpointCheck["kind"], domain: string) => {
    if (loadFailed || busy || savingRef.current || loading) return;
    savingRef.current = true; loadingRun.current++;
    setBusy(true);
    try {
      const started = await deleteNetworkEndpoint(admitOperation, kind, domain, status?.transport_endpoints.revision);
      setPending({ id: started.id, kind: "delete" });
      onOperation(started);
    } catch (cause) {
      if (cause instanceof SystemOperationUnknown) return;
      notifyError(cause instanceof Error ? cause.message : "Не удалось отключить адрес");
    } finally {
      savingRef.current = false;
      setBusy(false);
    }
  }, [admitOperation, busy, loadFailed, loading, notifyError, onOperation, status]);

  const entries = routeEntries(status, relay.status?.hosts || [], loadFailed, relay.failed, Boolean(relay.status?.pending), relay.reading);
  const addressProblem = addresses.failed || addresses.status?.discovery.state === "error";
  const attention = entries.filter(entry => needsAttention(entry.summary)).length + (addresses.status?.items.filter(row => needsAttention(serverAddressSummary(row, addressProblem, Boolean(addresses.pending)))).length || 0);

  return (
    <div data-network-page="true">
      <main className="networkBoard">
        <header className="networkPageHeader">
          <div className="networkPageIdentity">
            <p className="eyebrow networkSectionEyebrow">NETWORK WORKSPACE</p>
            <h1>Сеть</h1>
            <p className="networkHeroLead">Домены, адреса сервера и Relay — ресурсы и подключения в одном месте.</p>
            <dl className="networkHeroFacts">
              <div><dt>АДРЕСА ВХОДА</dt><dd>{status ? new Set(status.domains.map(item => item.value.toLowerCase())).size : "—"}</dd></div>
              <div><dt>RELAY</dt><dd>{relay.status?.hosts.length ?? "—"}</dd></div>
              <div><dt>IP СЕРВЕРА</dt><dd>{addresses.status?.items.length ?? "—"}</dd></div>
              <div data-attention={attention > 0 || undefined}><dt>ТРЕБУЮТ ВНИМАНИЯ</dt><dd>{status && relay.status && addresses.status ? attention : "—"}</dd></div>
            </dl>
          </div>
          <div className="networkRefresh">
            <span>
              {loading
                ? "Обновляем…"
                : loadFailed
                  ? "Ошибка обновления"
                  : status
                    ? formatTime(status.detected_at)
                    : "Нет данных"}
            </span>
            <button
              type="button"
              onClick={() => { void load(); void relay.load(); void addresses.load(); }}
              disabled={loading || busy}
            >
              Обновить
            </button>
          </div>
        </header>
        <NetworkRelayDialogs relay={relay} />
        {!status ? (
          <section className="networkEmpty" role="status">
            <NetworkIcon />
            <h2>
              {loading
                ? "Загружаем настройки сети"
                : "Не удалось получить данные"}
            </h2>
            <p>
              {loading
                ? "Получаем состояние сервера и DNS."
                : "Повторите загрузку кнопкой «Обновить»."}
            </p>
          </section>
        ) : null}
          <>
            {status && loadFailed && (
              <p className="networkNotice" role="status">
                Обновление не удалось. Показаны последние полученные данные.
              </p>
            )}
            <div>
              <DiagnosticsV2
                cloudflare={cloudflare}
                refreshKey={refreshKey}
                relay={relay}
                addresses={addresses}
                listState={listState}
                onListStateChange={onListStateChange}
                status={status}
                request={request}
                endpointDraft={endpointDraft}
                endpointDirty={endpointDirty}
                busy={busy}
                readFailed={loadFailed}
                onRemoveRoute={removeEndpoint}
                onRefresh={() => { void load(); void relay.load(); void addresses.load(); }}
                setEndpointDraft={setEndpointDraft}
                onRouteDirtyChange={setRouteDirty}
                saveEndpoints={saveEndpoints}
              />
            </div>
          </>
      </main>
    </div>
  );
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Время неизвестно"
    : date.toLocaleString("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
}

function NetworkCapabilityCard({ check }: { check: NetworkCapabilityCheck }) {
  const [copied, setCopied] = useState(false);
  const canPublishIpv6 =
    check.id === "ipv6" && check.status === "ready" && check.value.includes(":");
  const copyIpv6 = async () => {
    if (!canPublishIpv6 || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(check.value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  };
  return (
    <article className={`networkCapabilityCard ${check.status}`}>
      <div className="networkCapabilityCardHead">
        <span>{check.label}</span>
        <strong>{check.value}</strong>
      </div>
      <small>{check.detail}</small>
      {canPublishIpv6 && (
        <div className="networkCapabilityAction">
          <p>DNS: добавьте AAAA-запись домена на этот IPv6.</p>
          <button type="button" onClick={() => void copyIpv6()}>
            {copied ? "Скопировано" : "Скопировать IPv6"}
          </button>
        </div>
      )}
    </article>
  );
}
function DiagnosticsV2({
  cloudflare,
  refreshKey,
  relay,
  addresses,
  listState,
  onListStateChange,
  status,
  request,
  endpointDraft,
  endpointDirty,
  busy,
  readFailed,
  onRemoveRoute,
  setEndpointDraft,
  onRouteDirtyChange,
  saveEndpoints,
  onRefresh,
}: {
  cloudflare?: NetworkCloudflareControl;
  refreshKey: number;
  relay: ReturnType<typeof useNetworkRelays>;
  addresses: ReturnType<typeof useNetworkAddresses>;
  listState: NetworkListState;
  onListStateChange: (state: NetworkListState) => void;
  status: NetworkStatus | null;
  request: NetworkRequest;
  endpointDraft: NetworkEndpointSettings | null;
  endpointDirty: boolean;
  busy: boolean;
  readFailed: boolean;
  onRemoveRoute: (kind: NetworkEndpointCheck["kind"], domain: string) => void;
  setEndpointDraft: (value: NetworkEndpointSettings) => void;
  onRouteDirtyChange: (dirty: boolean) => void;
  saveEndpoints: (additions: NetworkEndpointSettings) => void;
  onRefresh: () => void;
}) {
  return <div className="networkV2">
    <NetworkRouteLibrary status={status} relay={relay} addresses={addresses} request={request} listState={listState} onListStateChange={onListStateChange} busy={busy} failed={readFailed} endpointDirty={endpointDirty} onRemove={onRemoveRoute} onRefresh={onRefresh} cloudflare={cloudflare} refreshKey={refreshKey} add={status && endpointDraft && <NetworkEndpoints relay={relay} request={request} existing={status.transport_endpoints} busy={busy} readFailed={readFailed} onDirtyChange={onRouteDirtyChange} onReset={() => setEndpointDraft(status.transport_endpoints)} onSave={saveEndpoints} />} />
    {status?.capabilities && <section className="routeLibraryDiagnostics" aria-label="Диагностика сети"><div className="routeDiagnosticsHeader"><h2>Диагностика сети</h2><div className="networkCapabilityActions"><button type="button" onClick={onRefresh} disabled={busy}>Обновить проверки</button></div></div><div className="networkCapabilityGrid">{status.capabilities.checks.map(check => <NetworkCapabilityCard check={check} key={check.id} />)}</div></section>}
  </div>;
}
