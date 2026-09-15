"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { DnsCheck, DnsSettings, DnsStatus, NetworkEndpointSettings, NetworkIpIdentity, NetworkStatus } from "../../shared/types/control-plane";
import { useNotifier } from "../../shared/notifications/notification-center";
import { probeNetworkDns, readNetworkControl, saveNetworkDns, saveNetworkEndpoints, type NetworkRequest } from "./network-api";
import { DnsView } from "./network-dns";
import { NetworkEch } from "./network-ech";
import { NetworkEndpoints } from "./network-endpoints";
import { dnsComponents } from "./system-dns-control";

type Props = { request: NetworkRequest; refreshKey?: number };
type Section = "diagnostics" | "dns";
const routeLabels = { direct: "Напрямую", proxy_or_cdn: "Через прокси / CDN", unresolved: "Нет DNS-ответа", none: "Не определён" };

function NetworkIcon() {
  return <svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><rect x="11" y="3" width="10" height="8" rx="2" /><path d="M16 11v7M6 23v-5h20v5" /><rect x="2" y="23" width="8" height="6" rx="1.5" /><rect x="22" y="23" width="8" height="6" rx="1.5" /></svg>;
}

function validAccessPoints(status: NetworkStatus) {
  const candidates = [["Панель", status.access.panel_url], ["Прямой доступ", status.access.direct_url], ["Защищённый доступ", status.access.protected_url]] as const;
  const seen = new Set<string>();
  return candidates.filter(([, value]) => {
    if (!value || seen.has(value)) return false;
    try { const url = new URL(value); if (!["http:", "https:"].includes(url.protocol) || !url.hostname) return false; } catch { return false; }
    seen.add(value); return true;
  });
}

export function NetworkView({ request, refreshKey = 0 }: Props) {
  const { error: notifyError, success: notifySuccess } = useNotifier("network", "Сеть");
  const [status, setStatus] = useState<NetworkStatus | null>(null);
  const [dns, setDns] = useState<DnsStatus | null>(null);
  const [dnsDraft, setDnsDraft] = useState<DnsSettings | null>(null);
  const [endpointDraft, setEndpointDraft] = useState<NetworkEndpointSettings | null>(null);
  const [dnsChecks, setDnsChecks] = useState<Record<string, DnsCheck>>({});
  const [loading, setLoading] = useState(true);
  const [checkingDns, setCheckingDns] = useState(false);
  const [busy, setBusy] = useState(false);
  const [section, setSection] = useState<Section>("diagnostics");
  const [loadFailed, setLoadFailed] = useState(false);
  const loadingRef = useRef(false);
  const savingRef = useRef(false);
  const checkingRef = useRef(false);
  const savedRef = useRef<DnsSettings | null>(null);
  const savedEndpointRef = useRef<NetworkEndpointSettings | null>(null);
  const dirty = Boolean(dns && dnsDraft && JSON.stringify(dns.settings) !== JSON.stringify(dnsDraft));
  const endpointDirty = Boolean(status && endpointDraft && JSON.stringify(status.transport_endpoints) !== JSON.stringify(endpointDraft));

  const load = useCallback(async () => {
    if (loadingRef.current || savingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    try {
      const next = await readNetworkControl(request, refreshKey > 0);
      const previous = savedRef.current;
      setStatus(next.network);
      setEndpointDraft((current) => !current || JSON.stringify(current) === JSON.stringify(savedEndpointRef.current) ? next.network.transport_endpoints : current);
      setDnsDraft((current) => !current || JSON.stringify(current) === JSON.stringify(previous) ? next.dns.settings : current);
      savedRef.current = next.dns.settings;
      savedEndpointRef.current = next.network.transport_endpoints;
      setDns(next.dns);
      setLoadFailed(false);
    } catch (cause) {
      setLoadFailed(true);
      notifyError(cause instanceof Error ? cause.message : "Не удалось загрузить состояние сети");
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  }, [notifyError, refreshKey, request]);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load, refreshKey]);

  const checkDnsProviders = useCallback(async () => {
    if (checkingRef.current) return;
    checkingRef.current = true;
    setCheckingDns(true);
    setDnsChecks({});
    try {
      const items = await probeNetworkDns(request);
      setDnsChecks(Object.fromEntries(items.map((item) => [item.id, item])));
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "Проверка DNS не выполнена");
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
      const settings = { ...dnsDraft, custom: dnsDraft.custom ? { ...dnsDraft.custom, addresses: dnsDraft.custom.addresses.map((address) => address.trim()), doh_url: dnsDraft.custom.doh_url.trim() } : dnsDraft.custom };
      for (const component of dnsComponents) {
        if (component.key !== "apply_system" && !dns?.protocol_effect_details?.[component.id]?.installed) settings[component.key] = false;
      }
      const next = await saveNetworkDns(request, settings);
      savedRef.current = next.settings;
      setDns(next);
      setDnsDraft(next.settings);
      notifySuccess("DNS-политика сохранена и применена");
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "Не удалось сохранить DNS");
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
      const settings = Object.fromEntries(Object.entries(endpointDraft).map(([key, value]) => [key, value.trim()])) as NetworkEndpointSettings;
      const next = await saveNetworkEndpoints(request, settings);
      setStatus(next);
      setEndpointDraft(next.transport_endpoints);
      savedEndpointRef.current = next.transport_endpoints;
      notifySuccess("Домены защищённых каналов сохранены");
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "Не удалось сохранить домены защищённых каналов");
    } finally { savingRef.current = false; setBusy(false); }
  }, [endpointDraft, notifyError, notifySuccess, request]);

  return <div data-network-page="true"><main className="networkBoard">
    <header className="networkPageHeader"><div className="networkPageIdentity"><p className="eyebrow">Network</p><h1>Сеть</h1></div><div className="networkToolbar"><nav className="networkTabs" aria-label="Разделы сети">{([["diagnostics", "Состояние сети"], ["dns", "Настройки DNS"]] as const).map(([id, label]) => <button type="button" key={id} className={section === id ? "active" : ""} aria-pressed={section === id} onClick={() => setSection(id)}>{label}{id === "dns" && dirty && <span className="networkUnsavedDot" aria-label="Несохранённые изменения" />}</button>)}</nav><div className="networkRefresh"><span>{loading ? "Обновляем…" : loadFailed ? "Ошибка обновления" : status ? formatTime(status.detected_at) : "Нет данных"}</span><button type="button" onClick={() => void load()} disabled={loading || busy}>Обновить</button></div></div></header>
    {!status ? <section className="networkEmpty" role="status"><NetworkIcon /><h2>{loading ? "Загружаем настройки сети" : "Не удалось получить данные"}</h2><p>{loading ? "Получаем состояние сервера и DNS." : "Повторите загрузку кнопкой «Обновить»."}</p></section> : <>
      {loadFailed && <p className="networkNotice" role="status">Обновление не удалось. Показаны последние полученные данные.</p>}
      <div hidden={section !== "dns"}><DnsView dns={dns} dnsDraft={dnsDraft} dnsChecks={dnsChecks} checkingDns={checkingDns} busy={busy} loading={loading} dirty={dirty} setDnsDraft={setDnsDraft} checkDnsProviders={checkDnsProviders} saveDnsSettings={saveDnsSettings} /></div>
      <div hidden={section !== "diagnostics"}><DiagnosticsV2 status={status} request={request} endpointDraft={endpointDraft} endpointDirty={endpointDirty} busy={busy} setEndpointDraft={setEndpointDraft} saveEndpoints={saveEndpoints} /></div>
    </>}
  </main></div>;
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Время неизвестно" : date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function ipOwnerLabel(items: NetworkIpIdentity[] | undefined) {
  const item = items?.find((candidate) => candidate.provider || candidate.asn || candidate.network);
  if (!item) return "Unknown";
  return [item.hoster, item.asn].filter(Boolean).join(" · ") || "Unknown";
}

function NetworkIdentityDetails({ domain }: { domain: NetworkStatus["domains"][number] }) {
  const dns = domain.dns;
  const ipInfo = domain.ip_info || [];
  if (!dns && !ipInfo.length) return null;
  return <details className="networkIdentityDetails"><summary>DNS, CDN и сведения об IP</summary><div className="networkIdentityBody">
    {dns && <div className="networkIdentityDns"><span>Авторитетный DNS</span><strong>{dns.provider}</strong><small>{dns.nameservers.length ? dns.nameservers.join(", ") : "NS не получены"}</small></div>}
    {domain.edge && domain.edge.provider !== "Unknown" && <div className="networkIdentityDns"><span>CDN / edge</span><strong>{domain.edge.provider}</strong><small>{domain.edge.source}{domain.edge.cnames.length ? ` · ${domain.edge.cnames.join(", ")}` : ""}</small></div>}
    {ipInfo.length > 0 && <div className="networkIdentityIps">{ipInfo.map((item) => <div className="networkIdentityIp" key={item.address}><code>{item.address}</code><span>{[item.hoster, item.asn].filter(Boolean).join(" · ") || "Unknown"}</span>{item.provider && item.provider !== item.hoster && <small>RDAP: {item.provider}</small>}{item.ptr && <small>PTR: {item.ptr}</small>}</div>)}</div>}
  </div></details>;
}

function Diagnostics({ status, request, endpointDraft, endpointDirty, busy, setEndpointDraft, saveEndpoints }: { status: NetworkStatus; request: NetworkRequest; endpointDraft: NetworkEndpointSettings | null; endpointDirty: boolean; busy: boolean; setEndpointDraft: (value: NetworkEndpointSettings) => void; saveEndpoints: () => void }) {
  const [query, setQuery] = useState("");
  const domains = status.domains.filter((domain) => `${domain.value} ${domain.role} ${domain.resolved.join(" ")}`.toLowerCase().includes(query.toLowerCase().trim()));
  const evidence = [...new Set([...status.route.evidence, ...status.edge.evidence])];
  return <div className="networkDiagnostics">
    <section className="networkWorkspace" aria-label="Состояние сети и внешние маршруты">
    <section className="networkStateOverview" aria-label="Состояние системы">
      <div className="networkRouteOverview"><small className="networkKicker">МАРШРУТ ПОДКЛЮЧЕНИЯ</small><strong>{status.route.label}</strong><p>{status.edge.provider} · {status.edge.mode}</p><div className="networkRoutePath"><span>Клиент</span><i aria-hidden="true" /><span>{status.edge.mode || "Внешняя сеть"}</span><i aria-hidden="true" /><span>Система</span></div><dl className="networkRouteMetrics"><div><dt>Домены</dt><dd>{status.domains.length}</dd></div><div><dt>IP-ответы</dt><dd>{new Set(status.domains.flatMap((item) => item.resolved)).size}</dd></div><div><dt>Слушатели</dt><dd>{status.listeners.length}</dd></div></dl></div>
      <dl className="networkSystemFacts"><div><dt>IPv4 сервера</dt><dd><code>{status.server.public_ipv4 || (!status.server.public_ip.includes(":") ? status.server.public_ip : "Не назначен")}</code></dd></div><div><dt>IPv6 сервера</dt><dd><code>{status.server.public_ipv6 || (status.server.public_ip.includes(":") ? status.server.public_ip : "Не назначен")}</code></dd></div><div><dt>TLS</dt><dd>{status.tls.mode}<small>{status.tls.certificate_source}</small></dd></div><div><dt>Текущий DNS панели</dt><dd><code>{status.resolvers.join(", ") || "Нет данных"}</code></dd></div></dl>
    </section>
    <section className="networkAccessBoard" aria-label="Точки доступа"><header><h2>Точки доступа</h2><p>Адреса подключения к панели</p></header><div>{validAccessPoints(status).map(([label, value]) => <div className="networkAccessEntry" key={value}><span>{label}</span><code>{value}</code></div>)}{!validAccessPoints(status).length && <p className="networkEmpty">Нет корректных точек доступа.</p>}</div></section>
    <section className="networkPanel networkRoutesPanel"><header className="networkSectionHeading"><div><span className="networkKicker">ИСТОЧНИКИ МАРШРУТА</span><h2>Домены и IP серверов</h2><p>DNS-провайдер, ответ домена и сведения о владельце каждого IP.</p></div><label className="networkSearch"><span className="networkSrOnly">Поиск домена или IP</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти домен или IP" /></label></header><div className="networkTableWrap"><table><thead><tr><th>Адрес</th><th>Роль</th><th>DNS и IP</th><th>Маршрут</th></tr></thead><tbody>{domains.map((domain) => <tr key={`${domain.role}-${domain.value}`}><td><strong>{domain.value}</strong>{domain.role.includes("CDN") && <NetworkEch domain={domain.value} route={domain.route} request={request} />}</td><td><span>{domain.role}</span><small>{domain.source}</small></td><td><code>{domain.resolved.join(", ") || "Нет ответа"}</code><NetworkIdentityDetails domain={domain} /></td><td><span className={`networkBadge ${domain.route}`}>{routeLabels[domain.route]}</span></td></tr>)}</tbody></table></div>{!domains.length && <p className="networkEmpty">{query ? "Совпадений нет." : "Домены не настроены."}</p>}<details className="networkEvidence"><summary>Пояснения проверки</summary>{evidence.length ? <ul>{evidence.map((item) => <li key={item}>{item}</li>)}</ul> : <p>Сервер не вернул пояснений.</p>}</details></section>
    </section>
    {status.capabilities && <section className="networkCapabilities" aria-label="Сетевые возможности сервера"><header><div><span className="networkKicker">УСИЛЕНИЕ РЕСУРСА</span><h2>Сетевые возможности сервера</h2><p>Проверки помогают выбрать транспорт и найти ограничения до публикации маршрута.</p></div>{status.capabilities.uplink && <code>{status.capabilities.uplink}</code>}</header><div className="networkCapabilityGrid">{status.capabilities.checks.map((check) => <article className={`networkCapabilityCard ${check.status}`} key={check.id}><span>{check.label}</span><strong>{check.value}</strong><small>{check.detail}</small></article>)}</div></section>}
  </div>;
}

function DiagnosticsV2({ status, request, endpointDraft, endpointDirty, busy, setEndpointDraft, saveEndpoints }: { status: NetworkStatus; request: NetworkRequest; endpointDraft: NetworkEndpointSettings | null; endpointDirty: boolean; busy: boolean; setEndpointDraft: (value: NetworkEndpointSettings) => void; saveEndpoints: () => void }) {
  const [query, setQuery] = useState("");
  const domains = status.domains.filter((item) => `${item.value} ${item.role} ${item.resolved.join(" ")}`.toLowerCase().includes(query.trim().toLowerCase()));
  const evidence = [...new Set([...status.route.evidence, ...status.edge.evidence])];
  const access = validAccessPoints(status);
  const ipCount = new Set(status.domains.flatMap((item) => item.resolved)).size;
  return <div className="networkV2">
    <section className="networkV2Hero"><div className="networkV2HeroCopy"><span className="networkKicker">NETWORK CONTROL</span><h2>Сеть и маршруты</h2><p>Единое состояние внешних адресов, DNS и точек подключения сервера.</p><div className="networkV2HeroStats"><span><strong>{status.domains.length}</strong> маршрутов</span><span><strong>{ipCount}</strong> IP-ответов</span><span><strong>{status.listeners.length}</strong> слушателей</span></div></div></section>
    <section className="networkV2RouteBlock networkPanel" aria-label="Домены и IP серверов"><header className="networkV2BlockHead"><div><span className="networkKicker">МАРШРУТЫ</span><h2>Домены и IP серверов</h2><p>DNS-провайдер, ответ домена и сведения о владельце каждого IP.</p></div><div className="networkV2Actions"><label className="networkSearch"><span className="networkSrOnly">Поиск домена или IP</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти адрес" /></label>{endpointDraft && <NetworkEndpoints request={request} draft={endpointDraft} busy={busy} dirty={endpointDirty} onChange={(key, value) => setEndpointDraft({ ...endpointDraft, [key]: value })} onSave={saveEndpoints} />}</div></header><div className="networkTableWrap"><table><thead><tr><th>Адрес</th><th>Роль</th><th>DNS и IP</th><th>Маршрут</th></tr></thead><tbody>{domains.map((domain) => <tr key={`${domain.role}-${domain.value}`}><td><strong>{domain.value}</strong>{domain.role.includes("CDN") && <NetworkEch domain={domain.value} route={domain.route} request={request} />}</td><td><span>{domain.role}</span><small>{domain.source}</small></td><td><code>{domain.resolved.join(", ") || "Нет ответа"}</code><NetworkIdentityDetails domain={domain} /></td><td><span className={`networkBadge ${domain.route}`}>{routeLabels[domain.route]}</span></td></tr>)}</tbody></table></div>{!domains.length && <p className="networkEmpty">{query ? "Совпадений нет." : "Маршруты ещё не настроены."}</p>}<details className="networkEvidence"><summary>Детали проверки</summary>{evidence.length ? <ul>{evidence.map((item) => <li key={item}>{item}</li>)}</ul> : <p>Дополнительных данных нет.</p>}</details></section>
    <div className="networkV2Columns"><section className="networkV2Card networkPanel"><header><span className="networkKicker">СОСТОЯНИЕ</span><h2>Маршрут подключения</h2><p>{status.route.label}</p></header><div className="networkV2RouteLine"><b>Клиент</b><i /><b>{status.edge.mode || "Внешняя сеть"}</b><i /><b>Сервер</b></div><dl className="networkV2Facts"><div><dt>IPv4</dt><dd>{status.server.public_ipv4 || "Не назначен"}</dd></div><div><dt>IPv6</dt><dd>{status.server.public_ipv6 || "Не обнаружен"}</dd></div><div><dt>DNS панели</dt><dd>{status.resolvers.join(", ") || "Нет данных"}</dd></div><div><dt>Провайдер IP</dt><dd>{ipOwnerLabel(status.server.ip_info)}</dd></div><div><dt>TLS</dt><dd>{status.tls.mode}</dd></div></dl></section><section className="networkV2Card networkPanel"><header><span className="networkKicker">ДОСТУП</span><h2>Точки доступа</h2><p>Только адреса, доступные как UI панели.</p></header><div className="networkV2AccessList">{access.map(([label, value]) => <div key={value}><span>{label}</span><code>{value}</code></div>)}{!access.length && <p>Корректных точек доступа нет.</p>}</div></section></div>
    {status.capabilities && <section className="networkV2Capabilities networkPanel"><header className="networkV2BlockHead"><div><span className="networkKicker">РЕСУРС</span><h2>Сетевые возможности</h2><p>Проверки перед включением новых транспортов и relay.</p></div>{status.capabilities.uplink && <code>{status.capabilities.uplink}</code>}</header><div className="networkCapabilityGrid">{status.capabilities.checks.map((check) => <article className={`networkCapabilityCard ${check.status}`} key={check.id}><span>{check.label}</span><strong>{check.value}</strong><small>{check.detail}</small></article>)}</div></section>}
  </div>;
}
