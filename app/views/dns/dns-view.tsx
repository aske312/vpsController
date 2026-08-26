"use client";

import type { Dispatch, SetStateAction } from "react";
import type { DnsCheck, DnsSettings, DnsStatus } from "../../types/control-plane";

type Props = { dns: DnsStatus | null; dnsDraft: DnsSettings | null; dnsChecks: Record<string, DnsCheck>; checkingDns: boolean; busy: boolean; setDnsDraft: Dispatch<SetStateAction<DnsSettings | null>>; checkDnsProviders: (providerId?: string) => Promise<void> | void; saveDnsSettings: () => Promise<void> | void };
const scope = [
  ["apply_system", "VPS", "DNS самого VPS", "Локальный resolver настраивается автоматически"],
  ["apply_wg", "WG", "WireGuard", "DNS новых профилей"],
  ["apply_awg", "AWG", "AmneziaWG", "DNS новых профилей"],
  ["apply_shadowsocks", "SS", "Shadowsocks", "Рекомендация клиенту"],
  ["apply_vrx", "VLESS", "Xray core", "Серверный resolver"],
] as const;
const protocolByScope = { apply_wg: "wg", apply_awg: "awg", apply_shadowsocks: "shadowsocks", apply_vrx: "vless-reality-xhttp" } as const;

export function DnsView({ dns, dnsDraft, dnsChecks, checkingDns, busy, setDnsDraft, checkDnsProviders, saveDnsSettings }: Props) {
  const selected = dns?.providers.find((item) => item.id === dnsDraft?.selected_id);
  const selectedName = selected?.name || dnsDraft?.custom?.name || "DNS не выбран";
  const systemDnsAvailable = dns?.system_resolver?.available !== false;
  const installedEffects = Object.entries(dns?.protocol_effect_details || {}).filter(([, effect]) => effect.installed);
  const scopeAvailable = (key: typeof scope[number][0]) => key === "apply_system"
    ? systemDnsAvailable
    : Boolean(dns?.protocol_effect_details?.[protocolByScope[key]]?.installed);
  const enabledScopes = scope.filter(([key]) => scopeAvailable(key) && Boolean(dnsDraft?.[key])).length;

  return <section className="dnsWorkspace dnsWorkspaceV2">
    <header className="dnsPageHead plainPageHead"><div><p className="eyebrow">NETWORK & DNS</p><h1>Сеть и DNS</h1><p>Выбор внешнего resolver, системный DNS VPS и DNS установленных протоколов.</p></div><div className="dnsPageFacts"><span><small>ПРОФИЛЬ</small><strong>{selectedName}</strong></span><span><small>КОНТУРЫ</small><strong>{enabledScopes}</strong></span><span><small>VPS DNS</small><strong>{dns?.system_resolver?.managed ? "MANAGED" : "SYSTEM"}</strong></span></div></header>

    <div className="dnsWorkbench">
      <article className="dnsResolverBrowser"><div className="dnsResolverArt" aria-hidden="true" /><header><div><p className="eyebrow">RESOLVER CATALOG</p><h2>Доступные DNS</h2><span>Адреса, назначение и результат проверки с этого VPS.</span></div><button type="button" onClick={() => void checkDnsProviders()} disabled={checkingDns}>{checkingDns ? "Проверяем…" : "Проверить все"}</button></header><div className="dnsResolverRows">{(dns?.providers || []).map((provider) => { const check = dnsChecks[provider.id]; const active = dnsDraft?.selected_id === provider.id; return <button type="button" className={`dnsResolverCard ${active ? "selected" : ""}`} key={provider.id} onClick={() => setDnsDraft((value) => value ? { ...value, selected_id: provider.id } : value)}><span className="dnsResolverCountry">{provider.country}</span><span className="dnsResolverName"><strong>{provider.name}</strong><small>{provider.filter}</small><code>{provider.addresses.join("  ")}</code></span><span className="dnsResolverProbe"><b className={check?.udp_ok ? "ok" : ""}>UDP {check?.udp_ms != null ? `${check.udp_ms} ms` : "—"}</b><b className={check?.doh_ok ? "ok" : ""}>DoH {check?.doh_ms != null ? `${check.doh_ms} ms` : provider.doh_url ? "—" : "нет"}</b></span><em>{active ? "ВЫБРАН" : "ВЫБРАТЬ"}</em></button>; })}</div></article>

      <aside className="dnsPolicyConsole"><header><div><p className="eyebrow">DNS POLICY</p><h2>Куда применять</h2></div></header><div className="dnsScopeList">{scope.map(([key, code, title, description]) => { const available = scopeAvailable(key); const details = key === "apply_system" ? `${dns?.system_resolver?.mode || "resolver"} · ${(dns?.system_resolver?.servers || []).join(" · ") || "будет настроен автоматически"}` : description; return <label className={`${dnsDraft?.[key] && available ? "active" : ""} ${available ? "" : "disabled"}`} key={key}><input type="checkbox" disabled={!available} checked={available && (dnsDraft?.[key] ?? true)} onChange={(event) => setDnsDraft((value) => value ? { ...value, [key]: event.target.checked } : value)} /><b>{code}</b><span><strong>{title}</strong><small>{available ? details : "Протокол не установлен"}</small></span><i /></label>; })}</div><div className="dnsOptionList"><label><span><strong>Резервный DNS</strong><small>Использовать второй адрес</small></span><input type="checkbox" checked={dnsDraft?.fallback_enabled ?? true} onChange={(event) => setDnsDraft((value) => value ? { ...value, fallback_enabled: event.target.checked } : value)} /></label><label className={dns?.protocol_effect_details?.["vless-reality-xhttp"]?.installed ? "" : "disabled"}><span><strong>DoH для VLESS</strong><small>{dns?.protocol_effect_details?.["vless-reality-xhttp"]?.installed ? "Encrypted resolver для Xray" : "VLESS не установлен"}</small></span><input type="checkbox" disabled={!dns?.protocol_effect_details?.["vless-reality-xhttp"]?.installed} checked={dnsDraft?.prefer_encrypted ?? false} onChange={(event) => setDnsDraft((value) => value ? { ...value, prefer_encrypted: event.target.checked } : value)} /></label></div></aside>
    </div>

    <div className="dnsLowerGrid">
      <article className="dnsCustomPanel"><header><div><p className="eyebrow">EXTERNAL RESOLVER</p><h3>Сторонний DNS</h3><span>Добавьте внешний DNS, которого нет в каталоге. Локальный resolver VPS настраивается автоматически через DNS Policy.</span></div><button type="button" className={dnsDraft?.selected_id === "custom" ? "active" : ""} onClick={() => setDnsDraft((value) => value ? { ...value, selected_id: "custom", custom: value.custom || { name: "Сторонний DNS", addresses: [""], doh_url: "" } } : value)}>{dnsDraft?.selected_id === "custom" ? "Выбран" : "Добавить DNS"}</button></header>{dnsDraft?.selected_id === "custom" && <div><label><span>Название</span><input value={dnsDraft.custom?.name || ""} onChange={(event) => setDnsDraft((value) => value ? { ...value, custom: { ...(value.custom || { addresses: [""], doh_url: "" }), name: event.target.value } } : value)} /></label><label><span>IP-адреса</span><input placeholder="1.1.1.1, 1.0.0.1" value={(dnsDraft.custom?.addresses || []).join(", ")} onChange={(event) => setDnsDraft((value) => value ? { ...value, custom: { ...(value.custom || { name: "DNS", doh_url: "" }), addresses: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) } } : value)} /></label><label><span>DoH URL, необязательно</span><input placeholder="https://…/dns-query" value={dnsDraft.custom?.doh_url || ""} onChange={(event) => setDnsDraft((value) => value ? { ...value, custom: { ...(value.custom || { name: "DNS", addresses: [""] }), doh_url: event.target.value } } : value)} /></label></div>}</article>

      <article className="dnsEffectivePanel"><header><div><p className="eyebrow">EFFECTIVE STATE</p><h3>DNS установленных протоколов</h3></div><span>{installedEffects.length ? `${installedEffects.length} активных контуров` : "Нет установленных протоколов"}</span></header><div>{installedEffects.length ? installedEffects.map(([protocol, effect]) => <span className={effect.matches_selected ? "matches" : "differs"} key={protocol}><b>{protocol === "vless-reality-xhttp" ? "VLESS" : protocol === "shadowsocks" ? "SS" : protocol.toUpperCase()}</b><code>{effect.value}</code><em>{effect.matches_selected ? "MATCH" : "DIFF"}</em></span>) : <p className="dnsEffectiveEmpty">После установки протокола здесь появится его фактический DNS и соответствие выбранной политике.</p>}</div></article>
    </div>

    <footer className="dnsApplyDock"><div><p className="eyebrow">READY TO APPLY</p><strong>{selectedName}</strong><span>{dnsDraft?.apply_system && systemDnsAvailable ? "DNS самого VPS будет настроен автоматически с проверкой и откатом при ошибке." : "DNS самого VPS останется без изменений."}</span></div><button type="button" onClick={() => void saveDnsSettings()} disabled={busy || !dnsDraft}>{busy ? "Применяем…" : "Применить политику"}</button></footer>
  </section>;
}
