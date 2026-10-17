"use client";

import { SystemOperationUnknown, systemActionNeedsPolling } from "../../control-panel/system-operation";
import { ConfirmationDialog } from "../../shared/components/confirmation-dialog";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
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
import { NetworkEch } from "./network-ech";
import { NetworkEndpoints } from "./network-endpoints";
import { NetworkRelayDialogs, NetworkRelayNotice, NetworkRelayRows, relayMatches, useNetworkRelays } from "./network-relays";
import { NetworkRouteSummaryBadge } from "./network-route-badge";
import { domainRouteSummary } from "./route-summary";
import { NetworkCloudflare, type NetworkCloudflareControl } from "./network-cloudflare";
import { dnsComponents } from "./system-dns-control";
import { NetworkServerAddressRows, useNetworkAddresses } from "./network-addresses";
import { addressMatches } from "./server-address-state";

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

  useEffect(() => { onDirtyChange?.(dirty || endpointDirty || relayDirty); }, [dirty, endpointDirty, relayDirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const load = useCallback(async () => {
    if (loadingRef.current || savingRef.current) return;
    loadingRef.current = true;
    const run = ++loadingRun.current;
    onLoadingChange?.(true, run);
    setLoading(true);
    try {
      const next = await readNetworkControl(request);
      const previous = savedRef.current;
      setStatus(next.network);
      setEndpointDraft((current) =>
        !current ||
        JSON.stringify(current) === JSON.stringify(savedEndpointRef.current)
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
      setLoadFailed(true);
      notifyError(
        cause instanceof Error
          ? cause.message
          : "Не удалось загрузить состояние сети",
      );
    } finally {
      loadingRef.current = false;
      setLoading(false);
      onLoadingChange?.(false, run);
    }
  }, [notifyError, onLoadingChange, request]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
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
    if (!dnsDraft || loadFailed || busy || savingRef.current || loadingRef.current) return;
    savingRef.current = true;
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
  }, [admitOperation, busy, dns, dnsDraft, loadFailed, notifyError, onOperation]);

  const saveEndpoints = useCallback(async () => {
    if (!endpointDraft || loadFailed || busy || savingRef.current || loadingRef.current) return;
    savingRef.current = true;
    setBusy(true);
    try {
      const settings = Object.fromEntries(
        Object.entries(endpointDraft).map(([key, value]) => [
          key,
          Array.isArray(value) ? value.map((item) => item.trim()) : String(value || "").trim(),
        ]),
      ) as NetworkEndpointSettings;
      const started = await saveNetworkEndpoints(admitOperation, settings);
      setPending({ id: started.id, kind: "endpoints", draft: endpointDraft });
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
  }, [admitOperation, busy, endpointDraft, loadFailed, notifyError, onOperation]);

  const removeEndpoint = useCallback(async (kind: NetworkEndpointCheck["kind"], domain: string) => {
    if (loadFailed || busy || savingRef.current || loadingRef.current) return;
    savingRef.current = true;
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
  }, [admitOperation, busy, loadFailed, loadingRef, notifyError, onOperation, status]);

  return (
    <div data-network-page="true">
      {confirmRecovery && <ConfirmationDialog request={{ title: "Восстановить DNS?", message: "Будут восстановлены DNS-файлы и состояния служб до прерванного применения. Подключения могут кратковременно прерваться.", confirmLabel: "Восстановить DNS", danger: true }} onClose={(confirmed) => {
        setConfirmRecovery(false);
        if (!confirmed || loadFailed || busy || savingRef.current || loadingRef.current) return;
        savingRef.current = true;
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
                  ["diagnostics", "Состояние сети"],
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

function NetworkStateFact({ label, value, note, mono = false }: { label: string; value: string; note: string; mono?: boolean }) {
  return (
    <div>
      <small>{label}</small>
      <strong className={mono ? "mono" : ""}>{value}</strong>
      <span>{note}</span>
    </div>
  );
}

function NetworkIdentityDetails({
  domain,
}: {
  domain: NetworkStatus["domains"][number];
}) {
  const dns = domain.dns;
  const ipInfo = domain.ip_info || [];
  const addresses = domain.resolved.length
    ? domain.resolved
    : ipInfo.map((item) => item.address);
  if (!dns && !domain.edge && !addresses.length)
    return (
      <div className="networkIdentityEmpty">DNS/IP-сведения не получены</div>
    );
  return (
    <div className="networkIdentityBody">
      {dns && (
        <div className="networkIdentityDns">
          <span>Авторитетный DNS</span>
          <strong>{dns.provider}</strong>
          <small>
            {dns.nameservers.length
              ? dns.nameservers.join(", ")
              : "NS не получены"}
          </small>
          {dns.records && (
            <small>
              A: {dns.records.a.length ? dns.records.a.join(", ") : "нет"} · AAAA: {dns.records.aaaa.length ? dns.records.aaaa.join(", ") : "нет"}
            </small>
          )}
        </div>
      )}
      {domain.edge && (
        <div className="networkIdentityDns">
          <span>CDN / edge</span>
          <strong>{domain.edge.provider}</strong>
          <small>
            {domain.edge.source}
            {domain.edge.cnames.length
              ? ` · ${domain.edge.cnames.join(", ")}`
              : ""}
          </small>
        </div>
      )}
      {addresses.length > 0 && (
        <div className="networkIdentityIps">
          {addresses.map((address) => {
            const item = ipInfo.find(
              (candidate) => candidate.address === address,
            );
            return (
              <div className="networkIdentityIp" key={address}>
                <code>{address}</code>
                <span>
                  {item
                    ? [item.hoster, item.asn].filter(Boolean).join(" · ") ||
                      "Unknown"
                    : "Сведения об IP не получены"}
                </span>
                {item?.provider && item.provider !== item.hoster && (
                  <small>RDAP: {item.provider}</small>
                )}
                {item?.ptr && <small>PTR: {item.ptr}</small>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
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
  saveEndpoints: () => void;
  onRefresh: () => void;
}) {
  const query = listState.query;
  const setQuery = (query: string) => onListStateChange({ ...listState, query });
  const [deletion, setDeletion] = useState<{ domain: string; remove: () => void } | null>(null);
  const expandedDomains = new Set(listState.expanded);
  const publicIpv4 = addresses.status?.items.find(row => row.family === "ipv4" && row.role === "primary")?.address || status?.server.public_ipv4 || (status && !status.server.public_ip.includes(":") ? status.server.public_ip : "");
  const publicIpv6 = addresses.status?.items.find(row => row.family === "ipv6" && row.role === "primary")?.address || status?.server.public_ipv6 || "";
  const domains = (status?.domains || []).filter((item) =>
    `${item.value} ${item.role} ${item.resolved.join(" ")}`
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  );
  const endpointKindFor = (domain: NetworkStatus["domains"][number], check?: NetworkEndpointCheck) => {
    if (check) return check.kind;
    if (domain.endpoint_kind) return domain.endpoint_kind;
    const role = domain.role.toLowerCase();
    if (role.includes("vless tls") || role.includes("tls relay") || role.includes("tls ·")) return "tls_relay" as const;
    if (role.includes("vless cdn") || role.includes("cdn endpoint")) return "cdn" as const;
    if (role.includes("udp relay")) return "udp_relay" as const;
    return undefined;
  };
  const endpointCheckFor = (domain: NetworkStatus["domains"][number]) => {
    const kind = endpointKindFor(domain);
    const keyed = kind ? status?.transport_endpoint_checks_by_domain?.[`${kind}:${domain.value}`] : undefined;
    return keyed || Object.values(status?.transport_endpoint_checks || {}).find(
      (check) => check?.kind === kind && (check?.domain || (check as NetworkEndpointCheck & { value?: string }).value)?.toLowerCase() === domain.value.toLowerCase(),
    );
  };
  const toggleDomain = (key: string) => {
    const next = new Set(listState.expanded);
    if (next.has(key)) next.delete(key); else next.add(key);
    onListStateChange({ ...listState, expanded: [...next] });
  };
  return (
    <div className="networkV2">
      {deletion && <ConfirmationDialog request={{ title: `Удалить маршрут ${deletion.domain}?`, message: "Адрес будет удалён из списка маршрутов. Существующие подключения и их настройки сохранятся с отметкой Stale. Новые подключения не смогут выбрать этот маршрут. Перенаправление на внешнем relay сохраняется.", confirmLabel: "Удалить маршрут", phrase: deletion.domain, danger: true }} onClose={(confirmed) => { const pending = deletion; setDeletion(null); if (confirmed) pending.remove(); }} />}

      <section
        className="networkStateStrip networkPanel"
        aria-label="Состояние сети"
      >
        <header className="networkStateHero">
          <div className="networkStateHeroIntro">
            <p className="eyebrow networkSectionEyebrow">STATE</p>
            <div className="networkStateTitleLine">
              <h2>Состояние сети</h2>
            </div>
          </div>
          <div className="networkStateFacts" aria-label="Факты о состоянии сети">
            <NetworkStateFact label="SERVER IPv4" value={publicIpv4 || (status ? "Не обнаружен" : "—")} note="origin VPS" mono />
            <NetworkStateFact label="SERVER IPv6" value={publicIpv6 || (status ? "Не обнаружен" : "—")} note={publicIpv6 ? "доступен для публикации" : status ? "не обнаружен" : "загружается"} mono />
            <NetworkStateFact label="DNS" value={status?.resolvers.join(", ") || "—"} note={status ? `${status.resolvers.length} резолвера` : "загружается"} mono />
            <NetworkStateFact label="EDGE" value={status?.edge.provider || "—"} note={status?.edge.mode || "загружается"} />
            <NetworkStateFact label="LISTENERS" value={status ? String(status.listeners.length) : "—"} note="TCP listeners" />
          </div>
        </header>
      </section>
      <section
        className="networkV2RouteBlock networkPanel"
        aria-label="Домены и IP серверов"
      >
        <header className="networkBlockHeader networkRoutesHeader">
          <div>
            <p className="eyebrow networkSectionEyebrow">ROUTES</p>
            <h2>Маршруты сети</h2>
          </div>
          <div className="networkV2Actions">
            <label className="networkSearch">
              <span className="networkSrOnly">Найти адрес</span>
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Найти адрес"
              />
            </label>
            {status && endpointDraft && (
                <NetworkEndpoints
                  relay={relay}
                  request={request}
                  draft={endpointDraft}
                  knownCdnDomains={[...new Set(status.domains.filter((domain) => domain.role.toLowerCase().includes("cdn")).map((domain) => domain.value))]}
                initialChecks={status.transport_endpoint_checks ? {
                  cdn_domain: status.transport_endpoint_checks.cdn,
                  tls_relay_domain: status.transport_endpoint_checks.tls_relay,
                  udp_relay_domain: status.transport_endpoint_checks.udp_relay,
                } : undefined}
                busy={busy}
                readFailed={readFailed}
                dirty={endpointDirty}
                onChange={(key, value) => {
                  const primaryKey = key as "cdn_domain" | "tls_relay_domain" | "udp_relay_domain";
                  const listKey = ({ cdn_domain: "cdn_domains", tls_relay_domain: "tls_relay_domains", udp_relay_domain: "udp_relay_domains" } as const)[primaryKey];
                  const current = endpointDraft[listKey] || (endpointDraft[primaryKey] ? [endpointDraft[primaryKey]] : []);
                  setEndpointDraft({ ...endpointDraft, [primaryKey]: value, [listKey]: [value, ...current.filter((item) => item !== endpointDraft[primaryKey] && item !== value)] });
                }}
                onRouteListChange={(key, values) => {
                  const primaryKey = key as "cdn_domain" | "tls_relay_domain" | "udp_relay_domain";
                  const listKey = ({ cdn_domain: "cdn_domains", tls_relay_domain: "tls_relay_domains", udp_relay_domain: "udp_relay_domains" } as const)[primaryKey];
                  setEndpointDraft({ ...endpointDraft, [primaryKey]: values[0] || "", [listKey]: values });
                }}
                onReset={() => setEndpointDraft(status.transport_endpoints)}
                onSave={saveEndpoints}
              />
            )}
          </div>
        </header>
        <NetworkRelayNotice relay={relay} />
        <div className="networkTableWrap">
          <table>
            <thead>
              <tr>
                <th>Адрес</th>
                <th>Статус</th>
                <th>Тип адреса</th>
                <th>Подключение</th>
                <th>Управление</th>
              </tr>
            </thead>
            <tbody>
              <NetworkServerAddressRows control={addresses} query={query} expanded={listState.expanded} onToggle={toggleDomain} renderIdentity={address => {
                const ipInfo = status?.server.ip_info?.filter(item => item.address === address);
                return ipInfo?.length ? <NetworkIdentityDetails domain={{ value: address, role: "SERVER", source: "VPS", resolved: [address], matches_origin: true, route: "direct", ip_info: ipInfo }} /> : null;
              }} />
              <NetworkRelayRows relay={relay} query={query} expanded={listState.expanded} onToggle={toggleDomain} />
              {domains.map((domain) => {
                const rowKey = `${domain.role}-${domain.value}`;
                const expanded = expandedDomains.has(rowKey);
                const endpointCheck = endpointCheckFor(domain);
                const endpointKind = endpointKindFor(domain, endpointCheck);
                const checks = [...new Map([
                  ...Object.values(status?.transport_endpoint_checks || {}),
                  ...Object.values(status?.transport_endpoint_checks_by_domain || {}),
                ].filter(check => check.domain.toLowerCase() === domain.value.toLowerCase()).map(check => [check.kind, check])).values()];
                const summary = domainRouteSummary(domain, checks, readFailed, [publicIpv4, publicIpv6, ...(addresses.status?.items.filter(row => row.presence === "present").map(row => row.address) || [])]);
                const removeRoute = () => {
                  if (endpointCheck) {
                    onRemoveRoute(endpointCheck.kind, domain.value);
                  } else if (endpointKind) {
                    onRemoveRoute(endpointKind, domain.value);
                  }
                };
                const rowClass =
                  domain.role === "SERVER"
                    ? "networkServerRouteRow"
                    : "networkRouteRow";
                return (
                  <Fragment key={rowKey}>
                    <tr
                      className={
                        expanded ? `${rowClass} is-expanded` : rowClass
                      }
                    >
                      <td>
                        <button
                          type="button"
                          className="networkRouteExpand"
                          aria-expanded={expanded}
                          onClick={() => toggleDomain(rowKey)}
                        >
                          <span
                            className="networkRouteExpandMark"
                            aria-hidden="true"
                          />
                          <span>
                            <strong>{domain.value}</strong>
                          </span>
                        </button>
                      </td>
                      <td>
                        <NetworkRouteSummaryBadge summary={summary} />
                      </td>
                      <td>
                        <span>{summary.type}</span>
                      </td>
                      <td>
                        <span className="networkRouteConnection">{summary.connection}</span>
                      </td>
                      <td>
                        {endpointKind && domain.role !== "SERVER" ? (
                          <button
                            type="button"
                            className="networkRouteDelete"
                            title={endpointDirty ? "Сначала сохраните или отмените правки адресов" : "Удалить маршрут"}
                            onClick={() => {
                              setDeletion({ domain: domain.value, remove: removeRoute });
                            }}
                            disabled={busy || readFailed || endpointDirty}
                          >
                            Удалить
                          </button>
                        ) : (
                          <span className="networkRouteActionEmpty">—</span>
                        )}
                      </td>
                    </tr>
                    {expanded && (
                      <tr className="networkRouteDetailRow">
                        <td colSpan={5}>
                          <div className="networkRouteCascade">
                            <div className="networkRouteCascadeHeader">
                              <div>
                                <p className="eyebrow networkSectionEyebrow">ROUTE DETAILS</p>
                                <strong>{domain.value}</strong>
                              </div>
                              <NetworkRouteSummaryBadge summary={summary} />
                            </div>
                            <p className="networkRouteEvidence">{summary.detail}</p>
                            <div className="networkRouteCascadeMeta">
                              <div>
                                <small>Роль</small>
                                <strong>{domain.role}</strong>
                              </div>
                              <div>
                                <small>Источник</small>
                                <strong>{domain.source}</strong>
                              </div>
                              <div>
                                <small>Адреса</small>
                                <strong>{domain.resolved.length ? domain.resolved.length + " IP" : "Нет IP"}</strong>
                              </div>
                            </div>
                            <NetworkIdentityDetails domain={domain} />
                            {checks.map(check => (
                              <div className={`networkRouteCheckNotice ${check.status}`} key={check.kind}>
                                <strong>Последняя проверка {({ cdn: "CDN", tls_relay: "TLS", udp_relay: "UDP" })[check.kind]}</strong>
                                <span>{check.message}</span>
                                <small>
                                  {check.checked_at ? formatTime(check.checked_at) : "Время не записано"}
                                  {check.failed_checks ? ` · неудач подряд: ${check.failed_checks}` : ""}
                                </small>
                              </div>
                            ))}
                            {domain.status === "stale" && domain.stale_usages?.length ? (
                              <div className="networkRouteStaleNotice">
                                <strong>Домен сохранён в подключениях:</strong>
                                <span>{domain.stale_usages.join(" · ")}</span>
                              </div>
                            ) : null}
                            {domain.role.includes("CDN") && (
                              <>
                              {cloudflare && <NetworkCloudflare domain={domain.value} detected={domain.edge?.provider.toLowerCase().includes("cloudflare") === true} request={request} control={{ ...cloudflare, blocked: cloudflare.blocked || busy || readFailed }} refreshKey={refreshKey} />}
                              <NetworkEch
                                domain={domain.value}
                                route={domain.route}
                                request={request}
                              />
                              </>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        {status && relay.status && !relay.reading && addresses.status && !addresses.failed && !addresses.reading && !domains.length && !addresses.status.items.some(row => addressMatches(row, query)) && !relay.status?.hosts.some(host => relayMatches(host, query)) && (
          <p className="networkEmpty">
            {query ? "Совпадений нет." : "Маршруты ещё не настроены."}
          </p>
        )}
      </section>
      {status?.capabilities && (
        <section className="networkV2Capabilities networkPanel">
          <header className="networkBlockHeader">
            <div>
              <p className="eyebrow networkSectionEyebrow">CAPABILITIES</p>
              <h2>Сетевые возможности</h2>
            </div>
            <div className="networkCapabilityActions">
              {status.capabilities.uplink && (
                <code>{status.capabilities.uplink}</code>
              )}
              <button type="button" onClick={onRefresh} disabled={busy}>
                Обновить проверки
              </button>
            </div>
          </header>
          <div className="networkCapabilityGrid">
            {status.capabilities.checks.map((check) => (
              <NetworkCapabilityCard check={check} key={check.id} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
