"use client";

import type { NetworkStatus } from "../../shared/types/control-plane";
import type { Dispatch, SetStateAction } from "react";
import type { DnsCheck, DnsSettings, DnsStatus } from "../../shared/types/control-plane";
import { DnsView } from "../dns/dns-view";

type Props = { status: NetworkStatus | null; loading: boolean; onRefresh: () => void; dns: DnsStatus | null; dnsDraft: DnsSettings | null; dnsChecks: Record<string, DnsCheck>; checkingDns: boolean; busy: boolean; setDnsDraft: Dispatch<SetStateAction<DnsSettings | null>>; checkDnsProviders: (providerId?: string) => Promise<void> | void; saveDnsSettings: () => Promise<void> | void };

const value = (item?: string | null) => item || "—";

export function NetworkView({ status, loading, onRefresh, dns, dnsDraft, dnsChecks, checkingDns, busy, setDnsDraft, checkDnsProviders, saveDnsSettings }: Props) {
  return <section className="networkWorkspace">
    <header className="networkPageHead plainPageHead">
      <div><p className="eyebrow">SERVER CONFIGURATION</p><h1>Сеть</h1><p>Текущие домены, маршрутизация, TLS и сетевые параметры этого VPS.</p></div>
      <button type="button" className="networkRefreshButton" onClick={onRefresh} disabled={loading}>{loading ? "Определяем…" : "Обновить определение"}</button>
    </header>

    {!status && <div className="networkEmpty">Нет данных о сетевой конфигурации. Запустите определение параметров.</div>}
    {status && <>
      <div className="networkSignalGrid">
        <article className="networkSignalCard"><span>ROUTE</span><strong>{status.route.label}</strong><small>{status.route.evidence[0] || "Домен не настроен"}</small></article>
        <article className="networkSignalCard"><span>EDGE / CDN</span><strong>{status.edge.provider}</strong><small>{status.edge.mode}</small></article>
        <article className="networkSignalCard"><span>TLS</span><strong>{status.tls.mode}</strong><small>{status.tls.certificate_source}</small></article>
        <article className="networkSignalCard"><span>ACCESS</span><strong>{status.access.mode}</strong><small>Проверено {new Date(status.detected_at).toLocaleTimeString("ru-RU")}</small></article>
      </div>

      <div className="networkGrid">
        <article className="networkPanel"><header><div><p className="eyebrow">DOMAINS</p><h2>Домены сервера</h2></div><span>{status.domains.length} обнаружено</span></header>
          <div className="networkRows">{status.domains.length ? status.domains.map((domain) => <div className="networkRow" key={`${domain.role}-${domain.value}`}><div><strong>{domain.value}</strong><small>{domain.role} · {domain.source}</small></div><b className={domain.route === "direct" ? "ok" : domain.route === "proxy_or_cdn" ? "warn" : "bad"}>{domain.route === "direct" ? "DIRECT" : domain.route === "proxy_or_cdn" ? "PROXY / CDN" : "НЕ РАЗРЕШЁН"}</b><code>{domain.resolved.join(", ") || "—"}</code></div>) : <p className="networkMuted">Публичный домен не задан.</p>}</div>
        </article>

        <article className="networkPanel"><header><div><p className="eyebrow">ENDPOINTS</p><h2>Адреса доступа</h2></div></header>
          <div className="networkEndpointList"><label><span>Публичная панель</span><code>{value(status.access.panel_url)}</code></label><label><span>Прямой origin</span><code>{value(status.access.direct_url)}</code></label><label><span>Защищённый адрес</span><code>{value(status.access.protected_url)}</code></label><label><span>Резолверы VPS</span><code>{status.resolvers.join(", ") || "—"}</code></label></div>
        </article>
      </div>

      <div className="networkGrid">
        <article className="networkPanel"><header><div><p className="eyebrow">LISTENERS</p><h2>Слушающие порты</h2></div></header><div className="networkRows">{status.listeners.length ? status.listeners.map((listener) => <div className="networkRow" key={`${listener.port}-${listener.protocol}`}><div><strong>{listener.protocol}</strong><small>{listener.process}</small></div><code>:{listener.port}</code></div>) : <p className="networkMuted">Слушающие порты не определены.</p>}</div></article>
        <article className="networkPanel"><header><div><p className="eyebrow">DETECTION</p><h2>Основания определения</h2></div></header><ul className="networkEvidence">{[...status.route.evidence, ...status.edge.evidence].map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></article>
      </div>
      <div className="networkDnsSection"><DnsView dns={dns} dnsDraft={dnsDraft} dnsChecks={dnsChecks} checkingDns={checkingDns} busy={busy} setDnsDraft={setDnsDraft} checkDnsProviders={checkDnsProviders} saveDnsSettings={saveDnsSettings} /></div>
    </>}
  </section>;
}
