"use client";

import { SystemOperationUnknown, systemActionNeedsPolling } from "../../control-panel/system-operation";
import { ConfirmationDialog } from "../../shared/components/confirmation-dialog";
import { useCallback, useEffect, useRef, useState } from "react";
import type {
  DnsCheck,
  DnsSettings,
  DnsStatus,
  NetworkCapabilityCheck,
  NetworkEndpointCheck,
  NetworkEndpointSettings,
  NetworkStatus,
  SystemAction,
} from "../../shared/types/control-plane";
import { useNotifier } from "../../shared/notifications/notification-center";
import {
  probeNetworkDns,
  readNetworkControl,
  deleteNetworkEndpoint,
  saveNetworkDns,
  saveNetworkEndpoints,
  recoverNetworkDns,
  type NetworkAdmission,
  type NetworkRequest,
} from "./network-api";
import { DnsView } from "./network-dns";
import { appendNetworkEndpoints } from "./endpoint-additions";
import { NetworkEndpoints } from "./network-endpoints";
import { NetworkRelayDialogs, useNetworkRelays } from "./network-relays";
import { type NetworkCloudflareControl } from "./network-cloudflare";
import { dnsComponents } from "./system-dns-control";
import { useNetworkAddresses } from "./network-addresses";
import { NetworkRouteLibrary } from "./network-route-library";

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
type Section = "diagnostics" | "dns";

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
  const [dns, setDns] = useState<DnsStatus | null>(null);
  const [dnsDraft, setDnsDraft] = useState<DnsSettings | null>(null);
  const [endpointDraft, setEndpointDraft] =
    useState<NetworkEndpointSettings | null>(null);
  const [dnsChecks, setDnsChecks] = useState<Record<string, DnsCheck>>({});
  const [loading, setLoading] = useState(true);
  const [checkingDns, setCheckingDns] = useState(false);
  const [requestBusy, setBusy] = useState(false);
  const [confirmRecovery, setConfirmRecovery] = useState(false);
  const [pending, setPending] = useState<{ id?: string; kind: "dns" | "endpoints" | "delete"; draft?: DnsSettings | NetworkEndpointSettings } | null>(null);
  const operationBusy = requestBusy || Boolean(pending) || systemActionNeedsPolling(operation);
  const addresses = useNetworkAddresses({ request, busy: operationBusy, refreshKey });
  const busy = operationBusy || Boolean(addresses.pending);
  const [section, setSection] = useState<Section>("diagnostics");
  const [loadFailed, setLoadFailed] = useState(false);
  const [relayDirty, setRelayDirty] = useState(false);
  const [routeDirty, setRouteDirty] = useState(false);
  const relay = useNetworkRelays({ request, admit: admitOperation, operation, onOperation, busy: busy || loading || !status || loadFailed, onDirtyChange: setRelayDirty, refreshKey });
  const loadingRef = useRef(false);
  const loadingRun = useRef(0);
  const savingRef = useRef(false);
  const checkingRef = useRef(false);
  const savedRef = useRef<DnsSettings | null>(null);
  const savedEndpointRef = useRef<NetworkEndpointSettings | null>(null);
  const dirty = Boolean(
    dns &&
    dnsDraft &&
    JSON.stringify(dns.settings) !== JSON.stringify(dnsDraft),
  );
  const endpointDirty = Boolean(
    status &&
    endpointDraft &&
    JSON.stringify(status.transport_endpoints) !==
      JSON.stringify(endpointDraft),
  );

  useEffect(() => { onDirtyChange?.(dirty || endpointDirty || relayDirty || routeDirty); }, [dirty, endpointDirty, relayDirty, routeDirty, onDirtyChange]);
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
      const previous = savedRef.current;
      // The state updater may run after the refs below advance to this response.
      const previousEndpoints = savedEndpointRef.current;
      setStatus(next.network);
      setEndpointDraft((current) =>
        !current ||
        JSON.stringify(current) === JSON.stringify(previousEndpoints)
          ? next.network.transport_endpoints
          : current,
      );
      setDnsDraft((current) =>
        !current || JSON.stringify(current) === JSON.stringify(previous)
          ? next.dns.settings
          : current,
      );
      savedRef.current = next.dns.settings;
      savedEndpointRef.current = next.network.transport_endpoints;
      setDns(next.dns);
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
        setStatus(next.network); setDns(next.dns);
        setLoadFailed(false);
        savedRef.current = next.dns.settings;
        savedEndpointRef.current = next.network.transport_endpoints;
        if (operation.state === "succeeded") {
          if (pending.kind === "dns") setDnsDraft((current) => JSON.stringify(current) === JSON.stringify(pending.draft) ? next.dns.settings : current);
          else setEndpointDraft((current) => pending.kind === "delete" || JSON.stringify(current) === JSON.stringify(pending.draft) ? next.network.transport_endpoints : current);
        }
      } catch (cause) { if (!cancelled) { setLoadFailed(true); notifyError(cause instanceof Error ? cause.message : "Не удалось проверить применённые настройки"); } }
      finally { if (!cancelled) setPending(null); }
    })();
    return () => { cancelled = true; };
  }, [pending, operation?.id, operation?.state, request, notifyError]);

  const checkDnsProviders = useCallback(async () => {
    if (checkingRef.current) return;
    checkingRef.current = true;
    setCheckingDns(true);
    setDnsChecks({});
    try {
      const items = await probeNetworkDns(request);
      setDnsChecks(Object.fromEntries(items.map((item) => [item.id, item])));
    } catch (cause) {
      notifyError(
        cause instanceof Error ? cause.message : "Проверка DNS не выполнена",
      );
    } finally {
      checkingRef.current = false;
      setCheckingDns(false);
    }
  }, [notifyError, request]);

  const saveDnsSettings = useCallback(async () => {
    if (!dnsDraft || loadFailed || busy || savingRef.current || loading) return;
    savingRef.current = true; loadingRun.current++;
    setBusy(true);
    try {
      const settings = {
        ...dnsDraft,
        profiles: {},
        custom: dnsDraft.custom
          ? {
              ...dnsDraft.custom,
              addresses: dnsDraft.custom.addresses.map((address) =>
                address.trim(),
              ),
              doh_url: dnsDraft.custom.doh_url.trim(),
            }
          : dnsDraft.custom,
      };
      for (const component of dnsComponents) {
        if (
          component.key !== "apply_system" &&
          !dns?.protocol_effect_details?.[component.id]?.installed
        )
          settings[component.key] = false;
      }
      const started = await saveNetworkDns(admitOperation, settings);
      setPending({ id: started.id, kind: "dns", draft: dnsDraft });
      onOperation(started);
    } catch (cause) {
      if (cause instanceof SystemOperationUnknown) return;
      notifyError(
        cause instanceof Error ? cause.message : "Не удалось сохранить DNS",
      );
    } finally {
      savingRef.current = false;
      setBusy(false);
    }
  }, [admitOperation, busy, dns, dnsDraft, loadFailed, loading, notifyError, onOperation]);

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
      if (cause instanceof SystemOperationUnknown) {
        setEndpointDraft(settings);
        setPending({ id: cause.id, kind: "endpoints", draft: settings });
        return;
      }
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

  return (
    <div data-network-page="true">
      {confirmRecovery && <ConfirmationDialog request={{ title: "Восстановить DNS?", message: "Будут восстановлены DNS-файлы и состояния служб до прерванного применения. Подключения могут кратковременно прерваться.", confirmLabel: "Восстановить DNS", danger: true }} onClose={(confirmed) => {
        setConfirmRecovery(false);
        if (!confirmed || loadFailed || busy || savingRef.current || loading) return;
        savingRef.current = true; loadingRun.current++;
        setBusy(true);
        void recoverNetworkDns(admitOperation).then((started) => { setPending({ id: started.id, kind: "dns", draft: dnsDraft || undefined }); onOperation(started); }).catch((cause) => { if (!(cause instanceof SystemOperationUnknown)) notifyError(cause instanceof Error ? cause.message : "Не удалось начать восстановление DNS"); }).finally(() => { savingRef.current = false; setBusy(false); });
      }} />}
      <main className="networkBoard">
        <header className="networkPageHeader">
          <div className="networkPageIdentity">
            <p className="eyebrow networkSectionEyebrow">Network</p>
            <h1>Сеть</h1>
            <nav className="networkTabs" aria-label="Разделы сети">
              {(
                [
                  ["diagnostics", "Адреса"],
                  ["dns", "Настройки DNS"],
                ] as const
              ).map(([id, label]) => (
                <button
                  type="button"
                  key={id}
                  className={section === id ? "active" : ""}
                  aria-pressed={section === id}
                  onClick={() => setSection(id)}
                >
                  {label}
                  {id === "dns" && dirty && (
                    <span
                      className="networkUnsavedDot"
                      aria-label="Несохранённые изменения"
                    />
                  )}
                </button>
              ))}
            </nav>
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
              onClick={() => { void load(); void relay.load(); }}
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
            <div hidden={section !== "dns"}>
              <DnsView
                dns={dns}
                dnsDraft={dnsDraft}
                dnsChecks={dnsChecks}
                checkingDns={checkingDns}
                busy={busy}
                readFailed={loadFailed}
                loading={loading}
                dirty={dirty}
                setDnsDraft={setDnsDraft}
                checkDnsProviders={checkDnsProviders}
                saveDnsSettings={saveDnsSettings}
                recoverDns={() => setConfirmRecovery(true)}
              />
            </div>
            <div hidden={section !== "diagnostics"}>
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
    {status?.capabilities && <section className="routeLibraryDiagnostics" aria-label="Диагностика сети"><h2>Диагностика сети</h2><div className="networkCapabilityActions"><button type="button" onClick={onRefresh} disabled={busy}>Обновить проверки</button></div><div className="networkCapabilityGrid">{status.capabilities.checks.map(check => <NetworkCapabilityCard check={check} key={check.id} />)}</div></section>}
  </div>;
}
