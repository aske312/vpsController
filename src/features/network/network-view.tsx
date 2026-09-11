"use client";

import type { Dispatch, SetStateAction } from "react";
import type { DnsCheck, DnsSettings, DnsStatus, NetworkStatus } from "../../shared/types/control-plane";
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
};

const text = (value?: string | null) => value || "—";

function StatusMark({ state }: { state: "ok" | "warn" | "bad" }) {
  return <i className={`networkStatusMark ${state}`} aria-hidden="true" />;
}

export function NetworkView({ status, loading, onRefresh, dns, dnsDraft, dnsChecks, checkingDns, busy, setDnsDraft, checkDnsProviders, saveDnsSettings }: Props) {
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

        <div className="networkControlGrid networkBottomGrid">
          <article className="networkSection"><header><div><p className="eyebrow">LISTENERS</p><h3>Порты на VPS</h3></div><span>{status.listeners.length} слушателей</span></header><div className="networkListenerRows">{status.listeners.length ? status.listeners.map((listener) => <div key={`${listener.port}-${listener.protocol}`}><strong>{listener.protocol}</strong><span>{listener.process}</span><code>:{listener.port}</code></div>) : <p className="networkMuted">Порты не определены.</p>}</div></article>
          <article className="networkSection"><header><div><p className="eyebrow">DETECTION LOGIC</p><h3>Почему выбран этот режим</h3></div></header><ul className="networkEvidence">{[...status.route.evidence, ...status.edge.evidence].map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></article>
        </div>
      </section>

      <section className="networkDnsSection">
        <header className="networkSubsectionHead"><div><p className="eyebrow">DNS POLICY / ALL CHANNELS</p><h2>Единая настройка DNS</h2><p>Один выбранный профиль для серверного ядра и новых конфигураций защищённых каналов.</p></div></header>
        <DnsView dns={dns} dnsDraft={dnsDraft} dnsChecks={dnsChecks} checkingDns={checkingDns} busy={busy} setDnsDraft={setDnsDraft} checkDnsProviders={checkDnsProviders} saveDnsSettings={saveDnsSettings} />
      </section>
    </>}
  </section>;
}
