"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { DnsCheck, DnsSettings, DnsStatus, NetworkStatus } from "../../shared/types/control-plane";
import { useNotifier } from "../../shared/notifications/notification-center";
import { probeNetworkDns, readNetworkControl, saveNetworkDns, type NetworkRequest } from "./network-api";
import { DnsView } from "./network-dns";

type Props = { request: NetworkRequest; refreshKey?: number };
type Section = "diagnostics" | "dns";
const routeLabels = { direct: "Напрямую", proxy_or_cdn: "Через прокси / CDN", unresolved: "Нет DNS-ответа", none: "Не определён" };

function NetworkIcon() {
  return <svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><rect x="11" y="3" width="10" height="8" rx="2" /><path d="M16 11v7M6 23v-5h20v5" /><rect x="2" y="23" width="8" height="6" rx="1.5" /><rect x="22" y="23" width="8" height="6" rx="1.5" /></svg>;
}

export function NetworkView({ request, refreshKey = 0 }: Props) {
  const { error: notifyError, success: notifySuccess } = useNotifier("network", "Сеть");
  const [status, setStatus] = useState<NetworkStatus | null>(null);
  const [dns, setDns] = useState<DnsStatus | null>(null);
  const [dnsDraft, setDnsDraft] = useState<DnsSettings | null>(null);
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
  const dirty = Boolean(dns && dnsDraft && JSON.stringify(dns.settings) !== JSON.stringify(dnsDraft));

  const load = useCallback(async () => {
    if (loadingRef.current || savingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    try {
      const next = await readNetworkControl(request);
      const previous = savedRef.current;
      setStatus(next.network);
      setDnsDraft((current) => !current || JSON.stringify(current) === JSON.stringify(previous) ? next.dns.settings : current);
      savedRef.current = next.dns.settings;
      setDns(next.dns);
      setLoadFailed(false);
    } catch (cause) {
      setLoadFailed(true);
      notifyError(cause instanceof Error ? cause.message : "Не удалось загрузить состояние сети");
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  }, [notifyError, request]);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load, refreshKey]);

  const checkDnsProviders = useCallback(async () => {
    if (checkingRef.current) return;
    checkingRef.current = true;
    setCheckingDns(true);
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
  }, [dnsDraft, notifyError, notifySuccess, request]);

  return <div data-network-page="true"><main className="networkBoard">
    <header className="networkHeader">
      <div className="networkHeading"><span className="networkCaption">{status?.server.name || "VPS"} · состояние сети</span></div>
      <div className="networkRefresh"><span>{loading ? "Получаем данные…" : loadFailed ? "Не удалось обновить данные" : status ? `Снимок: ${formatTime(status.detected_at)}` : "Нет данных"}</span><button type="button" onClick={() => void load()} disabled={loading || busy}>{loading ? "Обновление…" : "Обновить состояние"}</button></div>
    </header>
    {!status ? <section className="networkEmpty" role="status"><NetworkIcon /><h2>{loading ? "Загружаем сеть сервера" : "Состояние сети недоступно"}</h2><p>{loading ? "Получаем адреса, маршруты и настройки DNS." : "Не удалось получить данные. Повторите загрузку кнопкой «Обновить состояние»."}</p></section> : <>
      {loadFailed && <p className="networkNotice" role="status">Показан последний полученный снимок. Обновление состояния не удалось.</p>}
      <section className="networkSummary" aria-label="Сводка сети">
        <article><span className="networkCaption">Маршрут доступа</span><strong><i className={`networkDot ${status.route.mode}`} />{status.route.label}</strong><small>{status.edge.provider || "Провайдер не определён"} · {status.edge.mode || "Режим не определён"}</small></article>
        <article><span className="networkCaption">Публичный IPv4</span><strong className="networkMono">{status.server.public_ipv4 || (status.server.public_ip.includes(":") ? "Не назначен" : status.server.public_ip) || "Не назначен"}</strong><small>IPv6: {status.server.public_ipv6 || (status.server.public_ip.includes(":") ? status.server.public_ip : "не назначен")}</small></article>
        <article><span className="networkCaption">TLS сервера</span><strong>{status.tls.mode || "Не определён"}</strong><small>{status.tls.certificate_source || "Источник сертификата неизвестен"}</small></article>
        <article><span className="networkCaption">Системный DNS</span><strong className="networkMono">{dns?.system?.addresses[0] || status.resolvers[0] || "Не определён"}</strong><small>{dns?.system?.managed ? "Управляется приложением" : dns?.system?.source || "Системная конфигурация"}</small></article>
      </section>
      <section className="networkAccess" aria-label="Адреса доступа"><Address label="Адрес панели" value={status.access.panel_url} primary /><Address label="Прямой адрес" value={status.access.direct_url} /><Address label="Защищённый адрес" value={status.access.protected_url} /></section>
      <nav className="networkTabs" aria-label="Разделы сети">{([["diagnostics", "Диагностика"], ["dns", "Настройки DNS"]] as const).map(([id, label]) => <button type="button" key={id} className={section === id ? "active" : ""} aria-pressed={section === id} onClick={() => setSection(id)}>{label}{id === "dns" && dirty && <span className="networkUnsavedDot" aria-label="Есть несохранённые изменения" />}</button>)}<span>{section === "diagnostics" ? "Фактическое состояние сервера" : "Профили и правила применения"}</span></nav>
      <div hidden={section !== "diagnostics"}><Diagnostics status={status} /></div>
      <div hidden={section !== "dns"}><DnsView dns={dns} dnsDraft={dnsDraft} dnsChecks={dnsChecks} checkingDns={checkingDns} busy={busy} loading={loading} dirty={dirty} setDnsDraft={setDnsDraft} checkDnsProviders={checkDnsProviders} saveDnsSettings={saveDnsSettings} /></div>
    </>}
  </main></div>;
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "время неизвестно" : date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function Address({ label, value, primary = false }: { label: string; value: string; primary?: boolean }) {
  return <div className={primary ? "primary" : ""}><span className="networkCaption">{label}</span><code>{value || "Не настроен"}</code></div>;
}

function Diagnostics({ status }: { status: NetworkStatus }) {
  const [query, setQuery] = useState("");
  const domains = status.domains.filter((domain) => `${domain.value} ${domain.role} ${domain.resolved.join(" ")}`.toLowerCase().includes(query.toLowerCase().trim()));
  const unresolved = status.domains.filter((domain) => domain.route === "unresolved").length;
  const evidence = [...new Set([...status.route.evidence, ...status.edge.evidence])];
  return <div className="networkDiagnostics">
    <section className="networkRoutePanel"><div className="networkSectionHeading"><div><span className="networkCaption">01 / Путь подключения</span><h2>Как доступен сервер</h2></div><span className={`networkBadge ${status.route.mode}`}>{routeLabels[status.route.mode]}</span></div>
      <div className="networkTopology"><article><span className="networkStep">01</span><div><small>Разрешение имени</small><strong>Домены</strong><p>{status.domains.length} записей{unresolved > 0 ? ` · без ответа: ${unresolved}` : ""}</p></div></article><span className="networkConnector" aria-hidden="true">→</span><article className="networkTopologyEdge"><span className="networkStep">02</span><div><small>Точка входа</small><strong>{status.route.mode === "direct" ? "Прямой доступ" : status.route.mode === "proxy_or_cdn" ? status.edge.provider || "Прокси / CDN" : "Не определена"}</strong><p>{status.edge.mode || "Нет данных"}</p></div></article><span className="networkConnector" aria-hidden="true">→</span><article><span className="networkStep">03</span><div><small>Сервер назначения</small><strong>{status.server.name || "VPS"}</strong><p className="networkMono">{status.server.public_ip || "IP не определён"}</p></div></article></div>
      <details className="networkEvidence"><summary>На основании чего определён маршрут</summary>{evidence.length ? <ul>{evidence.map((item) => <li key={item}>{item}</li>)}</ul> : <p>Сервер не вернул пояснений к маршруту.</p>}</details>
    </section>
    <div className="networkDiagnosticColumns"><section className="networkPanel networkDomains"><div className="networkSectionHeading"><div><span className="networkCaption">02 / DNS-записи</span><h2>Домены и маршруты <span className="networkCount">{status.domains.length}</span></h2></div><label className="networkSearch"><span className="networkSrOnly">Поиск домена, роли или IP</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти домен или IP" /></label></div><div className="networkTableWrap"><table><thead><tr><th>Домен / назначение</th><th>IP-адреса</th><th>Маршрут</th></tr></thead><tbody>{domains.map((domain) => <tr key={`${domain.role}-${domain.value}`}><td><strong>{domain.value}</strong><small>{domain.role} · {domain.source}</small></td><td><code>{domain.resolved.join(", ") || "Нет DNS-ответа"}</code></td><td><span className={`networkBadge ${domain.route}`}>{routeLabels[domain.route]}</span></td></tr>)}</tbody></table></div>{!domains.length && <div className="networkEmpty compact"><strong>{query ? "Совпадений нет" : "Домены не настроены"}</strong><p>{query ? "Попробуйте другое имя или IP-адрес." : "Здесь появятся домены, известные серверу."}</p></div>}</section>
      <aside className="networkPanel"><div className="networkSectionHeading"><div><span className="networkCaption">03 / Разрешение имён</span><h2>DNS сервера</h2></div></div><div className="networkResolverAddresses">{status.resolvers.length ? status.resolvers.map((address, index) => <div key={`${address}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><code>{address}</code></div>) : <p>Адреса DNS не получены.</p>}</div><p className="networkPanelNote">Фактические системные resolver’ы на момент обновления. Изменить профиль можно в настройках DNS.</p></aside></div>
  </div>;
}
