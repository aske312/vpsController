"use client";

import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";
import type { DnsCheck, DnsSettings, DnsStatus, NetworkStatus } from "../../shared/types/control-plane";
import { useNotifier } from "../../shared/notifications/notification-center";
import { probeNetworkDns, readNetworkControl, saveNetworkDns, type NetworkRequest } from "./network-api";
import { SystemDnsControl } from "./system-dns-control";
// DNS самого VPS управляется отдельным блоком сети через apply_system.

type Tone = "good" | "attention" | "critical";
type Props = { request: NetworkRequest; refreshKey?: number };
type WorkspaceProps = { status: NetworkStatus | null; loading: boolean; onRefresh: () => void; dns: DnsStatus | null; dnsDraft: DnsSettings | null; dnsChecks: Record<string, DnsCheck>; checkingDns: boolean; busy: boolean; setDnsDraft: Dispatch<SetStateAction<DnsSettings | null>>; checkDnsProviders: (providerId?: string) => Promise<void> | void; saveDnsSettings: () => Promise<void> | void };
const scope = [["apply_wg", "WG", "WireGuard"], ["apply_awg", "AWG", "AmneziaWG"], ["apply_shadowsocks", "SS", "Shadowsocks"], ["apply_vrx", "VLESS", "Прямой VLESS"]] as const;
const scopeProtocol = { apply_wg: "wg", apply_awg: "awg", apply_shadowsocks: "shadowsocks", apply_vrx: "vless-reality-xhttp" } as const;
// Контракт применения: «Изменения применяются только к отмеченным каналам». Для VLESS Xray получит выбранные resolver-ы и перезапустится; для SS серверный трафик не изменяется.
const empty = (text?: string | null) => text || "—";
const routeTone = (status: NetworkStatus | null): Tone => status?.route.mode === "direct" ? "good" : status?.route.mode === "proxy_or_cdn" ? "attention" : "critical";
const domainTone = (route: string): Tone => route === "direct" ? "good" : route === "proxy_or_cdn" ? "attention" : "critical";

function Signal({ tone }: { tone: Tone }) { return <i className={`networkSignal ${tone}`} aria-hidden="true" />; }
function Caption({ children }: { children: string }) { return <span className="networkCaption">{children}</span>; }

export function NetworkView({ request, refreshKey = 0 }: Props) {
  const { error: notifyError, success: notifySuccess } = useNotifier("network", "Сеть");
  const [status, setStatus] = useState<NetworkStatus | null>(null);
  const [dns, setDns] = useState<DnsStatus | null>(null);
  const [dnsDraft, setDnsDraft] = useState<DnsSettings | null>(null);
  const [dnsChecks, setDnsChecks] = useState<Record<string, DnsCheck>>({});
  const [loading, setLoading] = useState(true);
  const [checkingDns, setCheckingDns] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await readNetworkControl(request);
      setStatus(next.network);
      setDns(next.dns);
      setDnsDraft((current) => current ?? next.dns.settings);
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "Не удалось определить сетевую конфигурацию");
    } finally {
      setLoading(false);
    }
  }, [notifyError, request]);

  // Network status is synchronized with the selected VPS and refresh key.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load, refreshKey]);

  const checkDnsProviders = useCallback(async (providerId?: string) => {
    setCheckingDns(true);
    try {
      const items = await probeNetworkDns(request, providerId);
      setDnsChecks((current) => ({ ...current, ...Object.fromEntries(items.map((item) => [item.id, item])) }));
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "Проверка DNS не выполнена");
    } finally {
      setCheckingDns(false);
    }
  }, [notifyError, request]);

  const saveDnsSettings = useCallback(async () => {
    if (!dnsDraft) return;
    setBusy(true);
    try {
      const next = await saveNetworkDns(request, dnsDraft);
      setDns(next);
      setDnsDraft(next.settings);
      notifySuccess("DNS-политика сохранена и применена");
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "Не удалось сохранить DNS");
    } finally {
      setBusy(false);
    }
  }, [dnsDraft, notifyError, notifySuccess, request]);

  return <div data-network-page="true"><NetworkWorkspace status={status} loading={loading} onRefresh={() => void load()} dns={dns} dnsDraft={dnsDraft} dnsChecks={dnsChecks} checkingDns={checkingDns} busy={busy} setDnsDraft={setDnsDraft} checkDnsProviders={checkDnsProviders} saveDnsSettings={saveDnsSettings} /></div>;
}
function NetworkWorkspace({ status, loading, onRefresh, dns, dnsDraft, dnsChecks, checkingDns, busy, setDnsDraft, checkDnsProviders, saveDnsSettings }: WorkspaceProps) {
  const tone = routeTone(status);
  if (!status) return <main className="networkBoard"><NetworkHeader status={status} loading={loading} onRefresh={onRefresh} tone={tone} /><div className="networkWaiting"><Signal tone="attention" /><strong>Сетевая конфигурация загружается</strong><span>Получаем домены, маршруты и точки доступа VPS.</span></div></main>;
  const direct = status.domains.filter((item) => item.route === "direct").length;
  const cdn = status.domains.filter((item) => item.route === "proxy_or_cdn").length;
  return <main className="networkBoard">
     <NetworkHeader status={status} loading={loading} onRefresh={onRefresh} tone={tone} />
     <section className="networkControlLead" aria-labelledby="network-control-title"><div><Caption>CONTROL PLANE</Caption><h2 id="network-control-title">Настройка сетевого доступа</h2><p>Управляйте DNS VPS и прямых протоколов из одной точки. Настройки Mihomo находятся в его собственном разделе.</p></div><div className="networkControlMeta"><span><Caption>ACTIVE PROFILE</Caption><strong>{dnsDraft?.profiles?.system || dnsDraft?.selected_id || "—"}</strong></span><span><Caption>CHANNELS</Caption><strong>{Object.values(dns?.protocol_effect_details || {}).filter((item) => item.installed).length}</strong></span></div></section>
     <SystemDnsControl dns={dns} dnsDraft={dnsDraft} setDnsDraft={setDnsDraft} />
     <DnsView dns={dns} dnsDraft={dnsDraft} dnsChecks={dnsChecks} checkingDns={checkingDns} busy={busy} setDnsDraft={setDnsDraft} checkDnsProviders={checkDnsProviders} saveDnsSettings={saveDnsSettings} />
     <section className="networkDiagnostics" aria-label="Диагностика сетевого состояния">
       <div className="networkIntro"><div><Caption>LIVE NETWORK MAP</Caption><h2>Состояние сети</h2><p>Фактический маршрут, точки доступа и доменные записи сервера.</p></div><div className="networkCounters"><span><b>{status.domains.length}</b> доменов</span><span><b>{direct}</b> direct</span><span><b>{cdn}</b> CDN</span></div></div>
     <section className="networkBoardGrid" aria-label="Состояние сети">
      <aside className="networkInfoRail"><div className="networkRailTitle"><Caption>SERVER POSTURE</Caption><Signal tone={tone} /></div><strong className={`networkRouteValue ${tone}`}>{status.route.label}</strong><p>{status.route.evidence[0] || "Маршрут не определён"}</p><dl><div><dt>EDGE</dt><dd>{status.edge.provider}<small>{status.edge.mode}</small></dd></div><div><dt>TLS</dt><dd>{status.tls.mode}<small>{status.tls.certificate_source}</small></dd></div><div><dt>RESOLVERS</dt><dd>{status.resolvers.length}<small>{status.resolvers.join(" · ") || "не настроены"}</small></dd></div></dl></aside>
      <div className="networkRouteCanvas"><div className="networkPath" aria-hidden="true" /><div className="networkPathNode"><b>01</b><div><Caption>REQUEST</Caption><strong>Домен ресурса</strong><span>DNS → access point</span></div></div><div className={`networkPathNode selected ${tone}`}><b>02</b><div><Caption>ROUTE</Caption><strong>{status.route.label}</strong><span>{status.edge.provider} · {status.edge.mode}</span></div></div><div className="networkPathNode"><b>03</b><div><Caption>ORIGIN</Caption><strong>{status.domains.length} доменов</strong><span>{direct} direct · {cdn} через CDN</span></div></div></div>
      <aside className="networkAccessRail"><div className="networkRailTitle"><Caption>ACCESS POINTS</Caption><Signal tone={status.access.mode === "protected" ? "good" : "attention"} /></div><div className="networkAccessPrimary"><Caption>ACTIVE ADDRESS</Caption><code>{empty(status.access.panel_url)}</code><b>{status.access.mode}</b></div><div><Caption>DIRECT ORIGIN</Caption><code>{empty(status.access.direct_url)}</code></div><div><Caption>PROTECTED ADDRESS</Caption><code>{empty(status.access.protected_url)}</code></div></aside>
     </section>
     <section className="networkDomains"><header><div><Caption>DOMAIN ROUTES</Caption><h2>Маршруты доменов</h2></div><span>{status.domains.length} записей</span></header><div className="networkDomainHeader"><span>ДОМЕН / РОЛЬ</span><span>RESOLVED ADDRESSES</span><span>MODE</span></div>{status.domains.length ? status.domains.map((domain) => { const itemTone = domainTone(domain.route); return <div className="networkDomain" key={`${domain.role}-${domain.value}`}><div><Signal tone={itemTone} /><strong>{domain.value}</strong><small>{domain.role} · {domain.source}</small></div><code>{domain.resolved.join(", ") || "не разрешается"}</code><b className={itemTone}>{domain.route === "direct" ? "DIRECT" : domain.route === "proxy_or_cdn" ? "CDN" : "ERROR"}</b></div>; }) : <p className="networkNoData">Домены не настроены.</p>}</section>
     </section>
  </main>;
}

function NetworkHeader({ status, loading, onRefresh, tone }: { status: NetworkStatus | null; loading: boolean; onRefresh: () => void; tone: Tone }) { return <header className="networkHeader"><div className="networkHeaderTitle"><span className="networkLogo">↗</span><div><Caption>NETWORK / VPS</Caption><h1>Сеть</h1></div></div><div className={`networkCurrentRoute ${tone}`}><Signal tone={tone} /><div><Caption>ACTIVE ROUTE</Caption><strong>{status?.route.label || "Определяем…"}</strong></div></div><button type="button" className="networkReload" onClick={onRefresh} disabled={loading}>{loading ? "Проверяем…" : "Обновить"}</button></header>; }

export function DnsView({ dns, dnsDraft, dnsChecks, checkingDns, busy, setDnsDraft, checkDnsProviders, saveDnsSettings }: { dns: DnsStatus | null; dnsDraft: DnsSettings | null; dnsChecks: Record<string, DnsCheck>; checkingDns: boolean; busy: boolean; setDnsDraft: Dispatch<SetStateAction<DnsSettings | null>>; checkDnsProviders: (providerId?: string) => Promise<void> | void; saveDnsSettings: () => Promise<void> | void }) {
  const activeScope = "system";
  const activeProfileId = dnsDraft?.profiles?.[activeScope] || dnsDraft?.selected_id;
  const activeSelected = dns?.providers.find((item) => item.id === activeProfileId);
  const selectedName = activeSelected?.name || dnsDraft?.custom?.name || "DNS не выбран";
  const effects = Object.entries(dns?.protocol_effect_details || {}).filter(([, item]) => item.installed);
  const available = (key: typeof scope[number][0]) => Boolean(dns?.protocol_effect_details?.[scopeProtocol[key]]?.installed);
  const effectScope = (name: string) => name === "server_xray" ? "СРАЗУ" : name === "new_profiles" ? "НОВЫЕ КОНФИГИ" : "РЕКОМЕНДАЦИЯ";
  const choose = (id: string) => setDnsDraft((current) => current ? { ...current, selected_id: activeScope === "system" ? id : current.selected_id, profiles: { ...(current.profiles || {}), [activeScope]: id } } : current);
  return <section className="networkDnsBoard" aria-label="DNS control"><header className="networkDnsTitle"><div><Caption>DNS CONTROL</Caption><h2>Профили DNS VPS и протоколов</h2><p>Назначьте профиль системному DNS или отдельному прямому протоколу. Настройки Mihomo находятся в разделе Mihomo.</p></div><div className="networkDnsSelected"><Caption>VPS PROFILE</Caption><strong>{selectedName}</strong></div></header><div className="networkDnsMain"><article className="networkResolverCatalog"><header><div><Caption>RESOLVERS</Caption><h3>Каталог DNS</h3></div><button type="button" onClick={() => void checkDnsProviders()} disabled={checkingDns}>{checkingDns ? "Проверяем…" : "Проверить все"}</button></header><div>{(dns?.providers || []).map((provider) => { const check = dnsChecks[provider.id]; const active = activeProfileId === provider.id; return <button type="button" className={`networkResolver ${active ? "active" : ""}`} key={provider.id} onClick={() => choose(provider.id)}><span>{provider.country}</span><div><strong>{provider.name}</strong><small>{provider.filter}</small><code>{provider.addresses.join("  ")}</code></div><aside><b className={check?.udp_ok ? "ok" : ""}>UDP {check?.udp_ms != null ? `${check.udp_ms} ms` : "—"}</b><b className={check?.doh_ok ? "ok" : ""}>DoH {check?.doh_ms != null ? `${check.doh_ms} ms` : provider.doh_url ? "—" : "нет"}</b></aside><em>{active ? "ВЫБРАН" : "ВЫБРАТЬ"}</em></button>; })}</div></article><aside className="networkScope"><header><Caption>APPLICATION SCOPE</Caption><h3>Куда применять</h3></header><div>{scope.map(([key, code, title]) => { const enabled = available(key); return <label className={`${dnsDraft?.[key] && enabled ? "active" : ""} ${enabled ? "" : "disabled"}`} key={key}><input type="checkbox" disabled={!enabled} checked={enabled && (dnsDraft?.[key] ?? true)} onChange={(event) => setDnsDraft((current) => current ? { ...current, [key]: event.target.checked } : current)} /><b>{code}</b><span><strong>{title}</strong><small>{enabled ? key === "apply_vrx" ? "Применяется сразу" : "Только новые конфиги клиентов" : "Протокол не установлен"}</small></span></label>; })}</div><div className="networkDnsOptions"><label><span><strong>Резервный DNS</strong><small>Использовать второй адрес</small></span><input type="checkbox" checked={dnsDraft?.fallback_enabled ?? true} onChange={(event) => setDnsDraft((current) => current ? { ...current, fallback_enabled: event.target.checked } : current)} /></label><label className={dns?.protocol_effect_details?.["vless-reality-xhttp"]?.installed ? "" : "disabled"}><span><strong>DoH для VLESS</strong><small>Зашифрованный resolver для Xray</small></span><input type="checkbox" disabled={!dns?.protocol_effect_details?.["vless-reality-xhttp"]?.installed} checked={dnsDraft?.prefer_encrypted ?? false} onChange={(event) => setDnsDraft((current) => current ? { ...current, prefer_encrypted: event.target.checked } : current)} /></label></div></aside></div><div className="networkDnsExtras"><article><header><div><Caption>EXTERNAL RESOLVER</Caption><h3>Сторонний DNS</h3><p>Добавьте адрес, которого нет в каталоге.</p></div><button type="button" className={dnsDraft?.selected_id === "custom" ? "active" : ""} onClick={() => setDnsDraft((current) => current ? { ...current, selected_id: "custom", custom: current.custom || { name: "Сторонний DNS", addresses: [""], doh_url: "" } } : current)}>{dnsDraft?.selected_id === "custom" ? "Выбран" : "Добавить"}</button></header>{dnsDraft?.selected_id === "custom" && <div className="networkCustomFields"><label>Название<input value={dnsDraft.custom?.name || ""} onChange={(event) => setDnsDraft((current) => current ? { ...current, selected_id: "custom", custom: { ...(current.custom || { addresses: [""], doh_url: "" }), name: event.target.value } } : current)} /></label><label>IP-адреса<input value={(dnsDraft.custom?.addresses || []).join(", ")} onChange={(event) => setDnsDraft((current) => current ? { ...current, selected_id: "custom", custom: { ...(current.custom || { name: "DNS", doh_url: "" }), addresses: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) } } : current)} /></label><label>DoH URL<input value={dnsDraft.custom?.doh_url || ""} onChange={(event) => setDnsDraft((current) => current ? { ...current, selected_id: "custom", custom: { ...(current.custom || { name: "DNS", addresses: [""] }), doh_url: event.target.value } } : current)} /></label></div>}</article><article><header><div><Caption>EFFECTIVE STATE</Caption><h3>Фактическое применение</h3><p>Текущее состояние установленных протоколов.</p></div><span>{effects.length ? `${effects.length} протокола` : "Нет установленных протоколов"}</span></header><div className="networkEffectiveRows">{effects.length ? effects.map(([protocol, effect]) => <div className={effect.matches_selected ? "same" : "different"} key={protocol}><b>{protocol === "vless-reality-xhttp" ? "VLESS" : protocol === "shadowsocks" ? "SS" : protocol.toUpperCase()}</b><code>{effect.value}</code><small>{effectScope(effect.scope)} · {effect.matches_selected ? "СОВПАДАЕТ" : "ОТЛИЧАЕТСЯ"}</small></div>) : <p>После установки протокола появится его фактический DNS.</p>}</div></article></div><aside className="networkDnsImpact"><strong>После применения</strong><span><b>VLESS</b> перезапустит Xray с новым resolver.</span><span><b>WG/AWG</b> получат DNS в новых конфигурациях.</span><span><b>SS</b> не изменит серверный трафик.</span></aside><footer className="networkApply"><div><Caption>APPLY CHANGES</Caption><strong>{selectedName}</strong><small>Изменения применяются только к отмеченным каналам.</small></div><button type="button" onClick={() => void saveDnsSettings()} disabled={busy || !dnsDraft}>{busy ? "Применяем…" : "Применить DNS"}</button></footer></section>;
}
