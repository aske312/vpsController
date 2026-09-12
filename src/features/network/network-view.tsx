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
    <div className="networkToolbar"><nav className="networkTabs" aria-label="Разделы сети">{([["diagnostics", "Состояние сети"], ["dns", "Настройки DNS"]] as const).map(([id, label]) => <button type="button" key={id} className={section === id ? "active" : ""} aria-pressed={section === id} onClick={() => setSection(id)}>{label}{id === "dns" && dirty && <span className="networkUnsavedDot" aria-label="Несохранённые изменения" />}</button>)}</nav><div className="networkRefresh"><span>{loading ? "Обновляем…" : loadFailed ? "Ошибка обновления" : status ? formatTime(status.detected_at) : "Нет данных"}</span><button type="button" onClick={() => void load()} disabled={loading || busy}>Обновить</button></div></div>
    {!status ? <section className="networkEmpty" role="status"><NetworkIcon /><h2>{loading ? "Загружаем настройки сети" : "Не удалось получить данные"}</h2><p>{loading ? "Получаем состояние сервера и DNS." : "Повторите загрузку кнопкой «Обновить»."}</p></section> : <>
      {loadFailed && <p className="networkNotice" role="status">Обновление не удалось. Показаны последние полученные данные.</p>}
      <div hidden={section !== "dns"}><DnsView dns={dns} dnsDraft={dnsDraft} dnsChecks={dnsChecks} checkingDns={checkingDns} busy={busy} loading={loading} dirty={dirty} setDnsDraft={setDnsDraft} checkDnsProviders={checkDnsProviders} saveDnsSettings={saveDnsSettings} /></div>
      <div hidden={section !== "diagnostics"}><Diagnostics status={status} /></div>
    </>}
  </main></div>;
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Время неизвестно" : date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function Diagnostics({ status }: { status: NetworkStatus }) {
  const [query, setQuery] = useState("");
  const domains = status.domains.filter((domain) => `${domain.value} ${domain.role} ${domain.resolved.join(" ")}`.toLowerCase().includes(query.toLowerCase().trim()));
  const evidence = [...new Set([...status.route.evidence, ...status.edge.evidence])];
  return <div className="networkDiagnostics">
    <section className="networkFacts" aria-label="Состояние сервера"><div><small>Маршрут</small><strong>{status.route.label}</strong><span>{status.edge.provider} · {status.edge.mode}</span></div><div><small>IPv4</small><code>{status.server.public_ipv4 || (!status.server.public_ip.includes(":") ? status.server.public_ip : "Не назначен")}</code><span>IPv6: {status.server.public_ipv6 || (status.server.public_ip.includes(":") ? status.server.public_ip : "не назначен")}</span></div><div><small>TLS</small><strong>{status.tls.mode}</strong><span>{status.tls.certificate_source}</span></div><div><small>DNS-серверы VPS</small><code>{status.resolvers.join(", ") || "Нет данных"}</code></div></section>
    <section className="networkAccess" aria-label="Адреса доступа">{[["Панель", status.access.panel_url], ["Прямой доступ", status.access.direct_url], ["Защищённый доступ", status.access.protected_url]].filter(([, value], index, items) => value && items.findIndex(([, address]) => address === value) === index).map(([label, value]) => <div key={value}><small>{label}</small><code>{value}</code></div>)}</section>
    <section className="networkPanel"><header className="networkSectionHeading"><div><h2>Домены и маршруты</h2><p>DNS-записи домена указывают, куда ведёт его имя. Здесь показаны полученные адреса; сами записи меняются у DNS-провайдера домена, отдельно от DNS-серверов VPS и протоколов.</p></div><label className="networkSearch"><span className="networkSrOnly">Поиск домена или IP</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти домен или IP" /></label></header><div className="networkTableWrap"><table><thead><tr><th>Домен</th><th>Назначение</th><th>Адреса из DNS домена</th><th>Маршрут</th></tr></thead><tbody>{domains.map((domain) => <tr key={`${domain.role}-${domain.value}`}><td><strong>{domain.value}</strong></td><td><span>{domain.role}</span><small>{domain.source}</small></td><td><code>{domain.resolved.join(", ") || "Нет DNS-ответа"}</code></td><td><span className={`networkBadge ${domain.route}`}>{routeLabels[domain.route]}</span></td></tr>)}</tbody></table></div>{!domains.length && <p className="networkEmpty">{query ? "Совпадений нет." : "Домены не настроены."}</p>}<details className="networkEvidence"><summary>Как определён маршрут</summary>{evidence.length ? <ul>{evidence.map((item) => <li key={item}>{item}</li>)}</ul> : <p>Сервер не вернул пояснений.</p>}</details></section>
  </div>;
}