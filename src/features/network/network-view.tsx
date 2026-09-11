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

type Tone = "ok" | "warn" | "bad";
const value = (item?: string | null) => item || "—";
const routeTone = (status: NetworkStatus | null): Tone => status?.route.mode === "direct" ? "ok" : status?.route.mode === "proxy_or_cdn" ? "warn" : "bad";
const domainTone = (route: string): Tone => route === "direct" ? "ok" : route === "proxy_or_cdn" ? "warn" : "bad";

function StatusMark({ tone }: { tone: Tone }) {
  return <i className={`networkStatusMark ${tone}`} aria-hidden="true" />;
}

function PanelTitle({ eyebrow, title, detail }: { eyebrow: string; title: string; detail?: string }) {
  return <div className="networkPanelTitle"><p className="eyebrow">{eyebrow}</p><h2>{title}</h2>{detail && <span>{detail}</span>}</div>;
}

export function NetworkView(props: Props) {
  const { status, loading, onRefresh, dns, dnsDraft, dnsChecks, checkingDns, busy, setDnsDraft, checkDnsProviders, saveDnsSettings, mihomoDns, mihomoDnsDraft, mihomoDnsBusy, setMihomoDnsDraft, saveMihomoDns } = props;
  const [mihomoSelected, setMihomoSelected] = useState(true);
  const tone = routeTone(status);
  const directCount = status?.domains.filter((item) => item.route === "direct").length || 0;
  const cdnCount = status?.domains.filter((item) => item.route === "proxy_or_cdn").length || 0;

  return <section className="networkWorkspace">
    <header className="networkHeader">
      <div className="networkHeaderCopy"><p className="eyebrow">NETWORK CONTROL</p><h1>Сеть</h1><p>Единая точка управления маршрутом, доступом и DNS на сервере.</p></div>
      <div className={`networkHealth ${tone}`}><StatusMark tone={tone} /><div><span>СОСТОЯНИЕ МАРШРУТА</span><strong>{status?.route.label || "Определяем…"}</strong></div><small>{status?.edge.provider || "Ожидание данных"}</small></div>
      <button type="button" className="networkRefreshButton" onClick={onRefresh} disabled={loading}>{loading ? "Проверяем…" : "Обновить"}</button>
    </header>

    {!status ? <div className="networkEmpty"><StatusMark tone="warn" /><span>Автоопределение сетевой конфигурации ещё не завершено.</span></div> : <>
      <section className="networkSnapshot" aria-label="Сводка сети">
        <div className={`networkSnapshotMain ${tone}`}><StatusMark tone={tone} /><div><span>ТЕКУЩИЙ МАРШРУТ</span><strong>{status.route.label}</strong><small>{status.route.evidence[0] || "Детали маршрута не определены"}</small></div></div>
        <div className="networkMetric"><span>ДОМЕНЫ</span><strong>{status.domains.length}</strong><small>{directCount} direct · {cdnCount} CDN</small></div>
        <div className="networkMetric"><span>TLS / CDN</span><strong>{status.tls.mode}</strong><small>{status.edge.provider} · {status.edge.mode}</small></div>
        <div className="networkMetric"><span>ПАНЕЛЬ</span><strong>{status.access.mode}</strong><small>{status.access.panel_url || "Адрес не определён"}</small></div>
      </section>

      <section className="networkMap" aria-label="Сетевая карта">
        <article className="networkCard networkDomainsCard"><header className="networkCardHeader"><PanelTitle eyebrow="ROUTING" title="Домены" detail={`${status.domains.length} настроено`} /><span className="networkCardHint">Куда направляется трафик</span></header><div className="networkDomainRows">{status.domains.length ? status.domains.map((domain) => { const domainState = domainTone(domain.route); return <div className="networkDomainRow" key={`${domain.role}-${domain.value}`}><div className="networkDomainName"><StatusMark tone={domainState} /><div><strong>{domain.value}</strong><small>{domain.role} · {domain.source}</small></div></div><code>{domain.resolved.join(", ") || "не разрешается"}</code><b className={domainState}>{domain.route === "direct" ? "DIRECT" : domain.route === "proxy_or_cdn" ? "CDN" : "ERROR"}</b></div>; }) : <p className="networkMuted">Домены не настроены.</p>}</div></article>
        <article className="networkCard networkAccessCard"><header className="networkCardHeader"><PanelTitle eyebrow="ACCESS" title="Точки доступа" detail="Адреса сервера" /><StatusMark tone={status.access.mode === "protected" ? "ok" : "warn"} /></header><div className="networkAccessList"><div className="networkAccessItem primary"><span>Панель сейчас</span><code>{value(status.access.panel_url)}</code></div><div className="networkAccessItem"><span>Прямой origin</span><code>{value(status.access.direct_url)}</code></div><div className="networkAccessItem"><span>Защищённый адрес</span><code>{value(status.access.protected_url)}</code></div><div className="networkAccessItem"><span>Резолверы VPS</span><code>{status.resolvers.join(", ") || "—"}</code></div></div></article>
      </section>

      <section className="networkDns" aria-label="Настройки DNS"><header className="networkSectionHeader"><div><p className="eyebrow">DNS POLICY</p><h2>DNS сервера</h2></div><span>Один профиль для протоколов и Mihomo</span></header><div className="networkDnsBody"><DnsView dns={dns} dnsDraft={dnsDraft} dnsChecks={dnsChecks} checkingDns={checkingDns} busy={busy} setDnsDraft={setDnsDraft} checkDnsProviders={checkDnsProviders} saveDnsSettings={saveDnsSettings} mihomoAvailable={Boolean(mihomoDns)} mihomoSelected={mihomoSelected} onMihomoSelected={setMihomoSelected} />{mihomoDns && mihomoSelected && <MihomoDnsPanel policy={mihomoDns} draft={mihomoDnsDraft} busy={mihomoDnsBusy} setDraft={setMihomoDnsDraft} onSave={saveMihomoDns} />}</div></section>
    </>}
  </section>;
}

function MihomoDnsPanel({ policy, draft, busy, setDraft, onSave }: { policy: MihomoDnsStatus; draft: Record<string, string | number | boolean>; busy: boolean; setDraft: Dispatch<SetStateAction<Record<string, string | number | boolean>>>; onSave: () => Promise<void> | void }) {
  const field = (key: string) => policy.schema.find((item) => item.key === key);
  const update = (key: string, next: string | number | boolean) => setDraft((current) => ({ ...current, [key]: next }));
  const optionLabel = (key: string, selected: string) => { const option = field(key)?.options?.find((item) => typeof item === "string" ? item === selected : item.value === selected); return typeof option === "string" ? option : option?.label || selected; };
  const mode = String(draft.enhanced_mode || "fake-ip");
  return <section className="networkMihomo"><header className="networkMihomoHeader"><div><p className="eyebrow">MIHOMO CORE</p><h3>Настройки DNS ядра</h3></div><button type="button" className="networkSaveButton" onClick={() => void onSave()} disabled={busy}>{busy ? "Сохраняем…" : "Сохранить"}</button></header><div className="networkMihomoSummary"><div><span>РЕЖИМ</span><strong>{mode === "fake-ip" ? "Fake IP" : "Redir host"}</strong></div><div><span>ОСНОВНОЙ</span><strong>{optionLabel("nameserver", String(draft.nameserver || ""))}</strong></div><div><span>РЕЗЕРВНЫЙ</span><strong>{optionLabel("fallback", String(draft.fallback || ""))}</strong></div></div><div className="networkMihomoGrid"><div className="networkMihomoBox"><header><b>Режим обработки</b><small>Как ядро отвечает приложениям</small></header><div className="networkModeOptions">{(field("enhanced_mode")?.options || ["fake-ip", "redir-host"]).map((option) => { const next = typeof option === "string" ? option : option.value; return <button type="button" key={next} className={mode === next ? "is-selected" : ""} onClick={() => update("enhanced_mode", next)}><strong>{next === "fake-ip" ? "Fake IP" : "Redir host"}</strong><span>{next === "fake-ip" ? "Быстрый режим" : "Совместимый режим"}</span></button>; })}</div></div><div className="networkMihomoBox"><header><b>Дополнительная обработка</b><small>Параметры разрешения имён</small></header><div className="networkOptions"><label><span>IPv6</span><input type="checkbox" checked={Boolean(draft.ipv6)} onChange={(event) => update("ipv6", event.target.checked)} /></label><label><span>HTTP/3 для DoH</span><input type="checkbox" checked={Boolean(draft.prefer_h3)} onChange={(event) => update("prefer_h3", event.target.checked)} /></label><label><span>Алгоритм кэша</span><select value={String(draft.cache_algorithm || "lru")} onChange={(event) => update("cache_algorithm", event.target.value)}>{(field("cache_algorithm")?.options || ["lru", "arc"]).map((option) => { const next = typeof option === "string" ? option : option.value; return <option key={next} value={next}>{typeof option === "string" ? next.toUpperCase() : option.label}</option>; })}</select></label></div></div><div className="networkMihomoBox networkResolvers"><header><b>Резолверы</b><small>Профили для основного и резервного запросов</small></header><div className="networkResolverGrid">{(["nameserver", "fallback"] as const).map((key) => <label key={key}><span>{key === "nameserver" ? "Основной DNS" : "Резервный DNS"}</span><select value={String(draft[key] || "")} onChange={(event) => update(key, event.target.value)}>{(field(key)?.options || []).map((option) => { const next = typeof option === "string" ? option : option.value; return <option key={next} value={next}>{typeof option === "string" ? next : option.label}</option>; })}</select></label>)}</div></div></div>{mode === "fake-ip" && <label className="networkFakeIp"><span><b>Исключения Fake IP</b><small>Домены из списка будут разрешаться напрямую</small></span><textarea rows={2} value={String(draft.fake_ip_filter || "")} onChange={(event) => update("fake_ip_filter", event.target.value)} /></label>}</section>;
}
