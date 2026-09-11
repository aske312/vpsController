"use client";

import { useState, type Dispatch, type SetStateAction } from "react";
import type { DnsCheck, DnsSettings, DnsStatus, MihomoDnsStatus, NetworkStatus } from "../../shared/types/control-plane";
import { DnsView } from "../dns/dns-view";

type Props = {
  status: NetworkStatus | null;
  loading: boolean;
  onRefresh: () => void;
  dns: DnsStatus | null;
  dnsDraft: DnsSettings | null;
  dnsChecks: Record<string, DnsCheck>;
  checkingDns: boolean;
  busy: boolean;
  setDnsDraft: Dispatch<SetStateAction<DnsSettings | null>>;
  checkDnsProviders: (providerId?: string) => Promise<void> | void;
  saveDnsSettings: () => Promise<void> | void;
  mihomoDns: MihomoDnsStatus | null;
  mihomoDnsDraft: Record<string, string | number | boolean>;
  mihomoDnsBusy: boolean;
  setMihomoDnsDraft: Dispatch<SetStateAction<Record<string, string | number | boolean>>>;
  saveMihomoDns: () => Promise<void> | void;
};

const value = (item?: string | null) => item || "—";
const routeState = (status: NetworkStatus | null): "ok" | "warn" | "bad" => status?.route.mode === "direct" ? "ok" : status?.route.mode === "proxy_or_cdn" ? "warn" : "bad";

function StatusMark({ state }: { state: "ok" | "warn" | "bad" }) {
  return <i className={`networkStatusMark ${state}`} aria-hidden="true" />;
}

export function NetworkView(props: Props) {
  const { status, loading, onRefresh, dns, dnsDraft, dnsChecks, checkingDns, busy, setDnsDraft, checkDnsProviders, saveDnsSettings, mihomoDns, mihomoDnsDraft, mihomoDnsBusy, setMihomoDnsDraft, saveMihomoDns } = props;
  const [mihomoSelected, setMihomoSelected] = useState(true);
  const state = routeState(status);
  const directCount = status?.domains.filter((item) => item.route === "direct").length || 0;
  const proxyCount = status?.domains.filter((item) => item.route === "proxy_or_cdn").length || 0;

  return <section className="networkWorkspace">
    <header className="networkHero">
      <div className="networkHeroCopy"><p className="eyebrow">NETWORK / VPS CONFIGURATION</p><h1>Сеть</h1><p>Маршрут, домены, точки доступа и единая DNS политика сервера.</p></div>
      <div className="networkHeroState"><StatusMark state={state} /><strong>{status?.route.label || "Определяем сеть"}</strong><span>{status?.edge.provider || "Параметры ещё загружаются"}</span></div>
      <button type="button" className="networkRefreshButton" onClick={onRefresh} disabled={loading}>{loading ? "Проверяем…" : "Обновить"}</button>
    </header>

    {!status ? <div className="networkEmpty">Автоопределение сетевой конфигурации ещё не завершено.</div> : <>
      <section className="networkSummary" aria-label="Состояние сети">
        <article className={`networkSummaryCard ${state}`}><small>МАРШРУТ</small><strong>{status.route.label}</strong><span>{status.route.evidence[0] || "Режим не определён"}</span></article>
        <article className="networkSummaryCard"><small>ДОМЕНЫ</small><strong>{status.domains.length}</strong><span>{directCount} direct · {proxyCount} через CDN</span></article>
        <article className="networkSummaryCard"><small>TLS / CDN</small><strong>{status.tls.mode}</strong><span>{status.edge.provider} · {status.edge.mode}</span></article>
        <article className="networkSummaryCard"><small>ПАНЕЛЬ</small><strong>{status.access.mode}</strong><span>{status.access.panel_url || "Адрес не определён"}</span></article>
      </section>

      <section className="networkDetails">
        <article className="networkPanel networkDomainsPanel"><header className="networkPanelHead"><div><p className="eyebrow">DOMAINS</p><h2>Домены</h2></div><span>{status.domains.length} записей</span></header><div className="networkDomainRows">{status.domains.length ? status.domains.map((domain) => { const domainState = domain.route === "direct" ? "ok" : domain.route === "proxy_or_cdn" ? "warn" : "bad"; return <div className="networkDomainRow" key={`${domain.role}-${domain.value}`}><div className="networkDomainIdentity"><StatusMark state={domainState} /><span><strong>{domain.value}</strong><small>{domain.role} · {domain.source}</small></span></div><code>{domain.resolved.join(", ") || "не разрешается"}</code><b className={domainState}>{domain.route === "direct" ? "DIRECT" : domain.route === "proxy_or_cdn" ? "CDN" : "ERROR"}</b></div>; }) : <p className="networkMuted">Домены не настроены.</p>}</div></article>
        <article className="networkPanel networkAccessPanel"><header className="networkPanelHead"><div><p className="eyebrow">ACCESS POINTS</p><h2>Точки доступа</h2></div><StatusMark state={status.access.mode === "protected" ? "ok" : "warn"} /></header><div className="networkEndpointRows"><div><small>Публичная панель</small><code>{value(status.access.panel_url)}</code></div><div><small>Прямой origin</small><code>{value(status.access.direct_url)}</code></div><div><small>Защищённый адрес</small><code>{value(status.access.protected_url)}</code></div><div><small>Резолверы VPS</small><code>{status.resolvers.join(", ") || "—"}</code></div></div></article>
      </section>

      <section className="networkDnsSection"><header className="networkDnsHead"><div><p className="eyebrow">DNS POLICY</p><h2>DNS сервера</h2></div><span>Общий профиль для каналов и Mihomo</span></header><DnsView dns={dns} dnsDraft={dnsDraft} dnsChecks={dnsChecks} checkingDns={checkingDns} busy={busy} setDnsDraft={setDnsDraft} checkDnsProviders={checkDnsProviders} saveDnsSettings={saveDnsSettings} mihomoAvailable={Boolean(mihomoDns)} mihomoSelected={mihomoSelected} onMihomoSelected={setMihomoSelected} />{mihomoDns && mihomoSelected && <MihomoDnsPanel policy={mihomoDns} draft={mihomoDnsDraft} busy={mihomoDnsBusy} setDraft={setMihomoDnsDraft} onSave={saveMihomoDns} />}</section>
    </>}
  </section>;
}

function MihomoDnsPanel({ policy, draft, busy, setDraft, onSave }: { policy: MihomoDnsStatus; draft: Record<string, string | number | boolean>; busy: boolean; setDraft: Dispatch<SetStateAction<Record<string, string | number | boolean>>>; onSave: () => Promise<void> | void }) {
  const field = (key: string) => policy.schema.find((item) => item.key === key);
  const update = (key: string, next: string | number | boolean) => setDraft((current) => ({ ...current, [key]: next }));
  const optionLabel = (key: string, selected: string) => { const option = field(key)?.options?.find((item) => typeof item === "string" ? item === selected : item.value === selected); return typeof option === "string" ? option : option?.label || selected; };
  const mode = String(draft.enhanced_mode || "fake-ip");
  return <section className="networkMihomoDns"><header className="networkMihomoHead"><div><p className="eyebrow">MIHOMO</p><h3>DNS ядра</h3></div><button type="button" className="networkDnsSave" onClick={() => void onSave()} disabled={busy}>{busy ? "Сохраняем…" : "Сохранить"}</button></header><div className="networkMihomoSummary"><span><small>РЕЖИМ</small><strong>{mode === "fake-ip" ? "Fake IP" : "Redir host"}</strong></span><span><small>ОСНОВНОЙ</small><strong>{optionLabel("nameserver", String(draft.nameserver || ""))}</strong></span><span><small>РЕЗЕРВНЫЙ</small><strong>{optionLabel("fallback", String(draft.fallback || ""))}</strong></span></div><div className="networkMihomoGrid"><div className="networkMihomoMode"><header><b>Режим обработки</b></header><div>{(field("enhanced_mode")?.options || ["fake-ip", "redir-host"]).map((option) => { const next = typeof option === "string" ? option : option.value; return <button type="button" key={next} className={mode === next ? "is-selected" : ""} onClick={() => update("enhanced_mode", next)}>{next === "fake-ip" ? "Fake IP" : "Redir host"}</button>; })}</div></div><div className="networkMihomoAdvanced"><header><b>Дополнительная обработка</b></header><div><label><span>IPv6</span><input type="checkbox" checked={Boolean(draft.ipv6)} onChange={(event) => update("ipv6", event.target.checked)} /></label><label><span>HTTP/3 для DoH</span><input type="checkbox" checked={Boolean(draft.prefer_h3)} onChange={(event) => update("prefer_h3", event.target.checked)} /></label><label><span>Кэш</span><select value={String(draft.cache_algorithm || "lru")} onChange={(event) => update("cache_algorithm", event.target.value)}>{(field("cache_algorithm")?.options || ["lru", "arc"]).map((option) => { const next = typeof option === "string" ? option : option.value; return <option key={next} value={next}>{typeof option === "string" ? next.toUpperCase() : option.label}</option>; })}</select></label></div></div></div><div className="networkMihomoResolvers"><header><b>Резолверы</b></header><div>{(["nameserver", "fallback"] as const).map((key) => <label key={key}><span>{key === "nameserver" ? "Основной DNS" : "Резервный DNS"}</span><select value={String(draft[key] || "")} onChange={(event) => update(key, event.target.value)}>{(field(key)?.options || []).map((option) => { const next = typeof option === "string" ? option : option.value; return <option key={next} value={next}>{typeof option === "string" ? next : option.label}</option>; })}</select></label>)}</div></div>{mode === "fake-ip" && <label className="networkFakeIp"><span><b>Исключения Fake IP</b></span><textarea rows={2} value={String(draft.fake_ip_filter || "")} onChange={(event) => update("fake_ip_filter", event.target.value)} /></label>}</section>;
}
