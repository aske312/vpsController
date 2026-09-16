"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import type {
  DnsCheck,
  DnsSettings,
  DnsStatus,
  NetworkCapabilityCheck,
  NetworkEndpointCheck,
  NetworkEndpointSettings,
  NetworkStatus,
} from "../../shared/types/control-plane";
import { useNotifier } from "../../shared/notifications/notification-center";
import {
  probeNetworkDns,
  readNetworkControl,
  deleteNetworkEndpoint,
  saveNetworkDns,
  saveNetworkEndpoints,
  type NetworkRequest,
} from "./network-api";
import { DnsView } from "./network-dns";
import { NetworkEch } from "./network-ech";
import { NetworkEndpoints } from "./network-endpoints";
import { dnsComponents } from "./system-dns-control";

type Props = {
  request: NetworkRequest;
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

export function NetworkView({ request, refreshKey = 0, onLoadingChange }: Props) {
  const { error: notifyError, success: notifySuccess } = useNotifier(
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
  const [busy, setBusy] = useState(false);
  const [section, setSection] = useState<Section>("diagnostics");
  const [loadFailed, setLoadFailed] = useState(false);
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

  const load = useCallback(async () => {
    if (loadingRef.current || savingRef.current) return;
    loadingRef.current = true;
    const run = ++loadingRun.current;
    onLoadingChange?.(true, run);
    setLoading(true);
    try {
      const next = await readNetworkControl(request, refreshKey > 0);
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
  }, [notifyError, onLoadingChange, refreshKey, request]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load, refreshKey]);

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
    if (!dnsDraft || savingRef.current || loadingRef.current) return;
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
      const next = await saveNetworkDns(request, settings);
      savedRef.current = next.settings;
      setDns(next);
      setDnsDraft(next.settings);
      notifySuccess("DNS-политика сохранена и применена");
    } catch (cause) {
      notifyError(
        cause instanceof Error ? cause.message : "Не удалось сохранить DNS",
      );
    } finally {
      savingRef.current = false;
      setBusy(false);
    }
  }, [dns, dnsDraft, notifyError, notifySuccess, request]);

  const saveEndpoints = useCallback(async () => {
    if (!endpointDraft || savingRef.current || loadingRef.current) return;
    savingRef.current = true;
    setBusy(true);
    try {
      const settings = Object.fromEntries(
        Object.entries(endpointDraft).map(([key, value]) => [
          key,
          Array.isArray(value) ? value.map((item) => item.trim()) : String(value || "").trim(),
        ]),
      ) as NetworkEndpointSettings;
      const next = await saveNetworkEndpoints(request, settings);
      setStatus(next);
      setEndpointDraft(next.transport_endpoints);
      savedEndpointRef.current = next.transport_endpoints;
      notifySuccess("Домены защищённых каналов сохранены");
    } catch (cause) {
      notifyError(
        cause instanceof Error
          ? cause.message
          : "Не удалось сохранить домены защищённых каналов",
      );
    } finally {
      savingRef.current = false;
      setBusy(false);
    }
  }, [endpointDraft, notifyError, notifySuccess, request]);

  const removeEndpoint = useCallback(async (kind: NetworkEndpointCheck["kind"], domain: string) => {
    if (savingRef.current || loadingRef.current) return;
    savingRef.current = true;
    setBusy(true);
    try {
      const next = await deleteNetworkEndpoint(request, kind, domain);
      setStatus(next);
      setEndpointDraft(next.transport_endpoints);
      savedEndpointRef.current = next.transport_endpoints;
      notifySuccess(`Адрес ${domain} отключён от сервера и удалён из настроек`);
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "Не удалось отключить адрес");
    } finally {
      savingRef.current = false;
      setBusy(false);
    }
  }, [loadingRef, notifyError, notifySuccess, request]);

  return (
    <div data-network-page="true">
      <main className="networkBoard">
        <header className="networkPageHeader">
          <div className="networkPageIdentity">
            <p className="eyebrow">Network</p>
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
              onClick={() => void load()}
              disabled={loading || busy}
            >
              Обновить
            </button>
          </div>
        </header>
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
        ) : (
          <>
            {loadFailed && (
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
                loading={loading}
                dirty={dirty}
                setDnsDraft={setDnsDraft}
                checkDnsProviders={checkDnsProviders}
                saveDnsSettings={saveDnsSettings}
              />
            </div>
            <div hidden={section !== "diagnostics"}>
              <DiagnosticsV2
                status={status}
                request={request}
                endpointDraft={endpointDraft}
                endpointDirty={endpointDirty}
                busy={busy}
                onRemoveRoute={removeEndpoint}
                onRefresh={() => void load()}
                setEndpointDraft={setEndpointDraft}
                saveEndpoints={saveEndpoints}
              />
            </div>
          </>
        )}
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

function routeTags(domain: NetworkStatus["domains"][number]) {
  const role = domain.role.toLowerCase();
  const tags: Array<{ label: string; kind: string }> = [];
  const add = (label: string, kind: string) => {
    if (!tags.some((tag) => tag.label === label)) tags.push({ label, kind });
  };
  if (role.includes("panel")) add("UI", "ui");
  if (role.includes("server")) add("ORIGIN", "origin");
  if (role.includes("cdn")) add("CDN", "cdn");
  if (role.includes("udp")) {
    add("UDP", "udp");
    add("RELAY", "relay");
  }
  if (role.includes("tls")) add("TLS", "tls");
  if (
    !role.includes("udp") &&
    (role.includes("vless") ||
      role.includes("cdn") ||
      role.includes("tls") ||
      role.includes("panel") ||
      role.includes("relay"))
  )
    add("TCP", "tcp");
  if (domain.route === "proxy_or_cdn") add("PROXY", "proxy");
  else if (domain.route === "direct") add("DIRECT", "direct");
  else add("DNS?", "unknown");
  return tags;
}

function NetworkRouteTags({
  domain,
}: {
  domain: NetworkStatus["domains"][number];
}) {
  return (
    <div className="networkRouteTags" aria-label="Тип маршрута">
      {routeTags(domain).map((tag) => (
        <span className={`networkRouteTag ${tag.kind}`} key={tag.label}>
          {tag.label}
        </span>
      ))}
    </div>
  );
}

function routeStatusFor(domain: NetworkStatus["domains"][number]): NetworkEndpointCheck["status"] | null {
  if (domain.status === "stale") return "stale";
  const role = domain.role.toLowerCase();
  const isCdn = role.includes("cdn");
  const isTls = role.includes("tls");
  const isUdp = role.includes("udp");
  if (!isCdn && !isTls && !isUdp) return null;
  if (!domain.resolved.length || domain.route === "unresolved") return "unresolved";
  if (isCdn) return domain.route === "proxy_or_cdn" ? "ready" : "warning";
  return domain.route === "direct" ? "warning" : "ready";
}

function NetworkRouteStatus({ status }: { status: NetworkEndpointCheck["status"] | null }) {
  if (!status) return null;
  return <span className={`networkRouteStatus ${status}`}>{status === "ready" ? "READY" : status === "warning" ? "WARN" : status === "stale" ? "OBSOLETE" : "ERROR"}</span>;
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
  status,
  request,
  endpointDraft,
  endpointDirty,
  busy,
  onRemoveRoute,
  setEndpointDraft,
  saveEndpoints,
  onRefresh,
}: {
  status: NetworkStatus;
  request: NetworkRequest;
  endpointDraft: NetworkEndpointSettings | null;
  endpointDirty: boolean;
  busy: boolean;
  onRemoveRoute: (kind: NetworkEndpointCheck["kind"], domain: string) => void;
  setEndpointDraft: (value: NetworkEndpointSettings) => void;
  saveEndpoints: () => void;
  onRefresh: () => void;
}) {
  const [query, setQuery] = useState("");
  const [expandedDomains, setExpandedDomains] = useState<Set<string>>(
    new Set(),
  );
  const publicIpv4 =
    status.server.public_ipv4 ||
    (!status.server.public_ip.includes(":") ? status.server.public_ip : "");
  const publicIpv6 =
    status.server.public_ipv6 ||
    (status.server.public_ip.includes(":") ? status.server.public_ip : "");
  const serverRoutes: NetworkStatus["domains"] = [publicIpv4, publicIpv6]
    .filter(
      (value, index, values) =>
        Boolean(value) && values.indexOf(value) === index,
    )
    .map((value) => ({
      value,
      role: "SERVER",
      source: "VPS",
      resolved: [value],
      matches_origin: true,
      route: "direct",
      ip_info: status.server.ip_info?.filter((item) => item.address === value),
    }));
  const domains = [...serverRoutes, ...status.domains].filter((item) =>
    `${item.value} ${item.role} ${item.resolved.join(" ")}`
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  );
  const endpointCheckFor = (value: string) =>
    Object.entries(status.transport_endpoint_checks_by_domain || {}).find(([, check]) => check?.domain?.toLowerCase() === value.toLowerCase())?.[1] ||
    Object.values(status.transport_endpoint_checks || {}).find(
      (check) => (check?.domain || (check as NetworkEndpointCheck & { value?: string }).value)?.toLowerCase() === value.toLowerCase(),
    );
  const toggleDomain = (key: string) =>
    setExpandedDomains((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  return (
    <div className="networkV2">
      <section
        className="networkStateStrip networkPanel"
        aria-label="Состояние сети"
      >
        <header className="networkStateHeader">
          <div>
            <span className="networkKicker">STATE</span>
            <h2>Состояние сети</h2>
          </div>
          <strong>{status.route.label}</strong>
        </header>
        <div className="networkStateLine">
          <div>
            <small>SERVER IPv4</small>
            <code>{publicIpv4 || "Не обнаружен"}</code>
          </div>
          <div>
            <small>SERVER IPv6</small>
            <code>{publicIpv6 || "Не обнаружен"}</code>
          </div>
          <div>
            <small>DNS</small>
            <code>{status.resolvers.join(", ") || "Нет данных"}</code>
          </div>
          <div>
            <small>EDGE</small>
            <strong>{status.edge.provider}</strong>
          </div>
          <div>
            <small>LISTENERS</small>
            <strong>{status.listeners.length}</strong>
          </div>
        </div>
      </section>
      <section
        className="networkV2RouteBlock networkPanel"
        aria-label="Домены и IP серверов"
      >
        <header className="networkV2BlockHead">
          <div>
            <span className="networkKicker">ROUTES</span>
            <h2>Домены и IP серверов</h2>
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
            {endpointDraft && (
                <NetworkEndpoints
                  request={request}
                  draft={endpointDraft}
                  knownCdnDomains={[...new Set(status.domains.filter((domain) => domain.role.toLowerCase().includes("cdn")).map((domain) => domain.value))]}
                initialChecks={status.transport_endpoint_checks ? {
                  cdn_domain: status.transport_endpoint_checks.cdn,
                  tls_relay_domain: status.transport_endpoint_checks.tls_relay,
                  udp_relay_domain: status.transport_endpoint_checks.udp_relay,
                } : undefined}
                busy={busy}
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
                onSave={saveEndpoints}
              />
            )}
          </div>
        </header>
        <div className="networkTableWrap">
          <table>
            <thead>
              <tr>
                <th>Адрес</th>
                <th>Назначение</th>
                <th>Канал</th>
                <th>Проверка</th>
                <th>Управление</th>
              </tr>
            </thead>
            <tbody>
              {domains.map((domain) => {
                const rowKey = `${domain.role}-${domain.value}`;
                const expanded = expandedDomains.has(rowKey);
                const endpointCheck = endpointCheckFor(domain.value);
                const routeStatus = endpointCheck?.status || routeStatusFor(domain);
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
                            <small>
                              {domain.value.includes(":") || /^\d{1,3}(?:\.\d{1,3}){3}$/.test(domain.value)
                                ? "IP"
                                : "DOMAIN"}
                              {domain.resolved.length
                                ? ` · ${domain.resolved.length} IP · раскрыть сведения`
                                : " · раскрыть сведения"}
                            </small>
                          </span>
                        </button>
                      </td>
                      <td>
                        <span>{domain.role}</span>
                        <small>{domain.source}</small>
                      </td>
                      <td>
                        <NetworkRouteTags domain={domain} />
                      </td>
                      <td>
                        <NetworkRouteStatus status={routeStatus} />
                      </td>
                      <td>
                        {endpointCheck && domain.role !== "SERVER" ? (
                          <button
                            type="button"
                            className="networkRouteDelete"
                            onClick={() => {
                              if (window.confirm(`Удалить маршрут ${domain.value}?`)) {
                                onRemoveRoute(endpointCheck.kind, domain.value);
                              }
                            }}
                            disabled={busy}
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
                                <span className="networkKicker">ROUTE DETAILS</span>
                                <strong>{domain.value}</strong>
                              </div>
                              <NetworkRouteTags domain={domain} />
                            </div>
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
                            {domain.status === "stale" && domain.stale_usages?.length ? (
                              <div className="networkRouteStaleNotice">
                                <strong>Домен сохранён в подключениях:</strong>
                                <span>{domain.stale_usages.join(" · ")}</span>
                              </div>
                            ) : null}
                            {domain.role.includes("CDN") && (
                              <NetworkEch
                                domain={domain.value}
                                route={domain.route}
                                request={request}
                              />
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
        {!domains.length && (
          <p className="networkEmpty">
            {query ? "Совпадений нет." : "Маршруты ещё не настроены."}
          </p>
        )}
      </section>
      {status.capabilities && (
        <section className="networkV2Capabilities networkPanel">
          <header className="networkV2BlockHead">
            <div>
              <span className="networkKicker">CAPABILITIES</span>
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
