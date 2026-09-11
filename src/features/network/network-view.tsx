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

const text = (value?: string | null) => value || "—";

function StatusMark({ state }: { state: "ok" | "warn" | "bad" }) {
  return <i className={`networkStatusMark ${state}`} aria-hidden="true" />;
}

export function NetworkView({ status, loading, onRefresh, dns, dnsDraft, dnsChecks, checkingDns, busy, setDnsDraft, checkDnsProviders, saveDnsSettings, mihomoDns, mihomoDnsDraft, mihomoDnsBusy, setMihomoDnsDraft, saveMihomoDns }: Props) {
  const [mihomoSelected, setMihomoSelected] = useState(true);
  const directCount = status?.domains.filter((domain) => domain.route === "direct").length || 0;
  const proxyCount = status?.domains.filter((domain) => domain.route === "proxy_or_cdn").length || 0;
  const routeState = status?.route.mode === "direct" ? "ok" : status?.route.mode === "proxy_or_cdn" ? "warn" : "bad";

  return <section className="networkWorkspace">
    <header className="networkOverview">
      <div className="networkOverviewCopy">
        <p className="eyebrow">SERVER NETWORK / LIVE SNAPSHOT</p>
        <h1>Сеть</h1>
        <p>Единая точка контроля внешнего доступа, доменов, TLS, CDN и DNS-политики этого сервера.</p>
      </div>
      <div className="networkOverviewStats">
        <span className={status ? "ok" : "muted"}><small>СОСТОЯНИЕ</small><strong>{status ? "Определено" : "Нет данных"}</strong></span>
        <span><small>ДОМЕНЫ</small><strong>{status?.domains.length || 0}</strong></span>
        <span><small>DIRECT</small><strong>{directCount}</strong></span>
        <span><small>PROXY/CDN</small><strong>{proxyCount}</strong></span>
        <span><small>ПОРТЫ</small><strong>{status?.listeners.length || 0}</strong></span>
      </div>
      <button type="button" className="networkRefreshButton" onClick={onRefresh} disabled={loading}>{loading ? "Определяем…" : "Обновить"}</button>
    </header>

    {!status ? <div className="networkEmpty">Автоопределение сетевой конфигурации ещё не завершено.</div> : <>
      <section className="networkControlPlane">
        <header className="networkControlHead">
          <div><p className="eyebrow">NETWORK CONTROL PLANE</p><h2>Как сервер доступен снаружи</h2></div>
          <div className="networkBadges"><span><StatusMark state={routeState} />{status.route.label}</span><span>{status.edge.provider}</span><span>{status.tls.mode}</span></div>
        </header>

        <div className="networkPolicyStrip">
          <article className={`networkPolicyCard ${routeState}`}><small>ROUTE</small><strong>{status.route.label}</strong><span>{status.route.evidence[0] || "Маршрут не определён"}</span></article>
          <article className="networkPolicyCard"><small>EDGE / CDN</small><strong>{status.edge.provider}</strong><span>{status.edge.mode}{status.edge.evidence[0] ? ` · ${status.edge.evidence[0]}` : ""}</span></article>
          <article className="networkPolicyCard"><small>TLS</small><strong>{status.tls.mode}</strong><span>{status.tls.certificate_source}</span></article>
          <article className="networkPolicyCard"><small>ORIGIN ACCESS</small><strong>{status.access.mode}</strong><span>Панель: {text(status.access.panel_url)}</span></article>
        </div>

        <div className="networkControlGrid">
          <article className="networkSection"><header><div><p className="eyebrow">DOMAINS</p><h3>Домены и DNS-ответы</h3></div><span>{status.domains.length} записей</span></header><div className="networkDomainRows">{status.domains.length ? status.domains.map((domain) => <div className="networkDomainRow" key={`${domain.role}-${domain.value}`}><div className="networkDomainIdentity"><StatusMark state={domain.route === "direct" ? "ok" : domain.route === "proxy_or_cdn" ? "warn" : "bad"} /><span><strong>{domain.value}</strong><small>{domain.role} · {domain.source}</small></span></div><code>{domain.resolved.join(", ") || "не разрешается"}</code><b className={domain.route === "direct" ? "ok" : domain.route === "proxy_or_cdn" ? "warn" : "bad"}>{domain.route === "direct" ? "DIRECT" : domain.route === "proxy_or_cdn" ? "PROXY / CDN" : "UNRESOLVED"}</b></div>) : <p className="networkMuted">Публичные домены не настроены.</p>}</div></article>

          <article className="networkSection"><header><div><p className="eyebrow">ENDPOINTS</p><h3>Точки доступа</h3></div></header><div className="networkEndpointRows"><div><small>Публичная панель</small><code>{text(status.access.panel_url)}</code></div><div><small>Прямой origin</small><code>{text(status.access.direct_url)}</code></div><div><small>Защищённый адрес</small><code>{text(status.access.protected_url)}</code></div><div><small>Резолверы VPS</small><code>{status.resolvers.join(", ") || "—"}</code></div></div></article>
        </div>

      </section>

      <section className="networkDnsSection">
        <DnsView dns={dns} dnsDraft={dnsDraft} dnsChecks={dnsChecks} checkingDns={checkingDns} busy={busy} setDnsDraft={setDnsDraft} checkDnsProviders={checkDnsProviders} saveDnsSettings={saveDnsSettings} mihomoAvailable={Boolean(mihomoDns)} mihomoSelected={mihomoSelected} onMihomoSelected={setMihomoSelected} />
        {mihomoDns && mihomoSelected && <MihomoDnsPanel policy={mihomoDns} draft={mihomoDnsDraft} busy={mihomoDnsBusy} setDraft={setMihomoDnsDraft} onSave={saveMihomoDns} />}
      </section>
    </>}
  </section>;
}

function MihomoDnsPanel({ policy, draft, busy, setDraft, onSave }: { policy: MihomoDnsStatus; draft: Record<string, string | number | boolean>; busy: boolean; setDraft: Dispatch<SetStateAction<Record<string, string | number | boolean>>>; onSave: () => Promise<void> | void }) {
  const field = (key: string) => policy.schema.find((item) => item.key === key);
  const update = (key: string, value: string | number | boolean) => setDraft((current) => ({ ...current, [key]: value }));
  const optionLabel = (key: string, value: string) => { const option = field(key)?.options?.find((item) => typeof item === "string" ? item === value : item.value === value); return typeof option === "string" ? option : option?.label || value; };
  const mode = String(draft.enhanced_mode || "fake-ip");
  return <section className="networkMihomoDns"><header className="networkMihomoHead"><div><p className="eyebrow">MIHOMO DNS</p><h2>DNS Mihomo</h2><span>Дополнительные параметры ядра применяются из общей сетевой политики.</span></div><button type="button" className="networkDnsSave" onClick={() => void onSave()} disabled={busy}>{busy ? "Сохраняем…" : "Сохранить DNS Mihomo"}</button></header><div className="networkMihomoSummary"><span><small>РЕЖИМ</small><strong>{mode === "fake-ip" ? "Fake IP" : "Redir host"}</strong><em>{mode === "fake-ip" ? "Для правил по доменам" : "Максимальная совместимость"}</em></span><span><small>ОСНОВНОЙ</small><strong>{optionLabel("nameserver", String(draft.nameserver || ""))}</strong><em>{String(draft.nameserver || "Не выбран")}</em></span><span><small>РЕЗЕРВНЫЙ</small><strong>{optionLabel("fallback", String(draft.fallback || ""))}</strong><em>{String(draft.fallback || "Не выбран")}</em></span></div><div className="networkMihomoGrid"><div className="networkMihomoMode"><header><b>Режим обработки</b><small>Как Mihomo сопоставляет домены с правилами.</small></header><div>{(field("enhanced_mode")?.options || ["fake-ip", "redir-host"]).map((option) => { const value = typeof option === "string" ? option : option.value; return <button type="button" key={value} className={mode === value ? "is-selected" : ""} onClick={() => update("enhanced_mode", value)}><b>{value === "fake-ip" ? "Fake IP" : "Redir host"}</b><span>{value === "fake-ip" ? "FAST" : "COMPAT"}</span></button>; })}</div></div><div className="networkMihomoAdvanced"><header><b>Дополнительная обработка</b><small>Параметры DNS-секции профилей.</small></header><div><label><span>IPv6</span><input type="checkbox" checked={Boolean(draft.ipv6)} onChange={(event) => update("ipv6", event.target.checked)} /></label><label><span>HTTP/3 для DoH</span><input type="checkbox" checked={Boolean(draft.prefer_h3)} onChange={(event) => update("prefer_h3", event.target.checked)} /></label><label><span>Кэш</span><select value={String(draft.cache_algorithm || "lru")} onChange={(event) => update("cache_algorithm", event.target.value)}>{(field("cache_algorithm")?.options || ["lru", "arc"]).map((option) => { const value = typeof option === "string" ? option : option.value; return <option key={value} value={value}>{typeof option === "string" ? value.toUpperCase() : option.label}</option>; })}</select></label></div></div></div><div className="networkMihomoResolvers"><header><b>Резолверы Mihomo</b><small>Основной и резервный DNS должны отличаться.</small></header><div>{(["nameserver", "fallback"] as const).map((key) => <label key={key}><span>{key === "nameserver" ? "Основной DNS" : "Резервный DNS"}</span><select value={String(draft[key] || "")} onChange={(event) => update(key, event.target.value)}>{(field(key)?.options || []).map((option) => { const value = typeof option === "string" ? option : option.value; return <option key={value} value={value}>{typeof option === "string" ? value : option.label}</option>; })}</select></label>)}</div></div>{mode === "fake-ip" && <label className="networkFakeIp"><span><b>Исключения Fake IP</b><small>По одному домену или маске на строку.</small></span><textarea rows={3} value={String(draft.fake_ip_filter || "")} onChange={(event) => update("fake_ip_filter", event.target.value)} /></label>}</section>;
}
