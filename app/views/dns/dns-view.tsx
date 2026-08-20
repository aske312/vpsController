"use client";

import type { Dispatch, SetStateAction } from "react";
import type { DnsCheck, DnsSettings, DnsStatus } from "../../types/control-plane";

type DnsViewProps = {
  dns: DnsStatus | null;
  dnsDraft: DnsSettings | null;
  dnsChecks: Record<string, DnsCheck>;
  checkingDns: boolean;
  busy: boolean;
  setDnsDraft: Dispatch<SetStateAction<DnsSettings | null>>;
  checkDnsProviders: (providerId?: string) => Promise<void> | void;
  saveDnsSettings: () => Promise<void> | void;
};

export function DnsView({ dns, dnsDraft, dnsChecks, checkingDns, busy, setDnsDraft, checkDnsProviders, saveDnsSettings }: DnsViewProps) {
  return <section className="dnsWorkspace">
        <article className="dnsOverview">
          <div className="dnsOverviewCopy">
            <p className="eyebrow">312.NET / NETWORK & DNS</p>
            <h1>Сеть и DNS</h1>
            <p>Единая DNS-политика для новых конфигураций и серверного VRX без изменения уже работающих WG/AWG-подключений.</p>
          </div>
          <div className="dnsOverviewStats">
            <span><small>SELECTED</small><strong>{dns?.providers.find((item) => item.id === dnsDraft?.selected_id)?.name || dnsDraft?.custom?.name || "—"}</strong></span>
            <span><small>RESOLVERS</small><strong>{dns?.providers.length || 0}</strong></span>
            <span><small>SCOPE</small><strong>{[dnsDraft?.apply_wg, dnsDraft?.apply_awg, dnsDraft?.apply_shadowsocks, dnsDraft?.apply_vrx].filter(Boolean).length}/4</strong></span>
            <span className={dnsDraft?.fallback_enabled ? "ok" : "muted"}><small>FALLBACK</small><strong>{dnsDraft?.fallback_enabled ? "ON" : "OFF"}</strong></span>
            <span className={dnsDraft?.prefer_encrypted ? "ok" : "muted"}><small>DOH / VRX</small><strong>{dnsDraft?.prefer_encrypted ? "ON" : "OFF"}</strong></span>
          </div>
          <button className="dnsCheckAll" type="button" onClick={() => void checkDnsProviders()} disabled={checkingDns}>{checkingDns ? "Проверяем DNS…" : "Проверить DNS"}</button>
        </article>

        <article className="dnsControlPlane">
          <div className="dnsControlArt" aria-hidden="true" />
          <div className="dnsControlShade" aria-hidden="true" />
          <div className="dnsControlContent">
            <header className="dnsControlHead">
              <div>
                <p className="eyebrow">DNS CONTROL PLANE</p>
                <h2>Резолверы и политика применения</h2>
                <span>Выбор, scope и фактическая задержка собраны в одном рабочем контуре.</span>
              </div>
              <div className="dnsControlBadges">
                <span>{dns?.providers.find((item) => item.id === dnsDraft?.selected_id)?.country || (dnsDraft?.selected_id === "custom" ? "CUSTOM" : "—")}</span>
                <span>{[dnsDraft?.apply_wg && "WG", dnsDraft?.apply_awg && "AWG", dnsDraft?.apply_shadowsocks && "SS", dnsDraft?.apply_vrx && "VRX"].filter(Boolean).join(" · ") || "NO SCOPE"}</span>
              </div>
            </header>

            <div className="dnsPolicyStrip">
              <div className="dnsScopePills">
                <label className={dnsDraft?.apply_wg ? "active" : ""}><input type="checkbox" checked={dnsDraft?.apply_wg ?? true} onChange={(event) => setDnsDraft((value) => value ? { ...value, apply_wg: event.target.checked } : value)} /><span><strong>WireGuard</strong><small>новые профили</small></span></label>
                <label className={dnsDraft?.apply_awg ? "active" : ""}><input type="checkbox" checked={dnsDraft?.apply_awg ?? true} onChange={(event) => setDnsDraft((value) => value ? { ...value, apply_awg: event.target.checked } : value)} /><span><strong>AmneziaWG</strong><small>новые профили</small></span></label>
                <label className={dnsDraft?.apply_shadowsocks ? "active" : ""}><input type="checkbox" checked={dnsDraft?.apply_shadowsocks ?? true} onChange={(event) => setDnsDraft((value) => value ? { ...value, apply_shadowsocks: event.target.checked } : value)} /><span><strong>Shadowsocks</strong><small>client hint</small></span></label>
                <label className={dnsDraft?.apply_vrx ? "active" : ""}><input type="checkbox" checked={dnsDraft?.apply_vrx ?? true} onChange={(event) => setDnsDraft((value) => value ? { ...value, apply_vrx: event.target.checked } : value)} /><span><strong>VLESS Reality</strong><small>server Xray</small></span></label>
              </div>
              <div className="dnsPolicyToggles">
                <label><span><strong>Fallback</strong><small>резервный адрес</small></span><input type="checkbox" checked={dnsDraft?.fallback_enabled ?? true} onChange={(event) => setDnsDraft((value) => value ? { ...value, fallback_enabled: event.target.checked } : value)} /></label>
                <label><span><strong>DoH / VRX</strong><small>encrypted DNS</small></span><input type="checkbox" checked={dnsDraft?.prefer_encrypted ?? false} onChange={(event) => setDnsDraft((value) => value ? { ...value, prefer_encrypted: event.target.checked } : value)} /></label>
              </div>
            </div>

            <section className="dnsResolverSection">
              <div className="dnsSectionTitle"><div><p className="eyebrow">RESOLVERS</p><h3>Доступные DNS</h3></div><span>UDP / TCP / DoH</span></div>
              <div className="dnsResolverRows">
                {(dns?.providers || []).map((provider) => {
                  const check = dnsChecks[provider.id];
                  const selected = dnsDraft?.selected_id === provider.id;
                  return <div className={`dnsResolverRow ${selected ? "selected" : ""}`} key={provider.id}>
                    <div className="dnsResolverIdentity">
                      <span className={provider.country === "RU" ? "dnsCountry ru" : "dnsCountry"}>{provider.country}</span>
                      <div><strong>{provider.name}</strong><small>{provider.filter}</small><code>{provider.addresses.join(" · ")}</code></div>
                    </div>
                    <div className="dnsResolverLatency">
                      <span className={check?.udp_ok ? "ok" : ""}><small>UDP</small><strong>{check?.udp_ms != null ? `${check.udp_ms} мс` : "—"}</strong></span>
                      <span className={check?.tcp_ok ? "ok" : ""}><small>TCP</small><strong>{check?.tcp_ms != null ? `${check.tcp_ms} мс` : "—"}</strong></span>
                      <span className={check?.doh_ok ? "ok" : ""}><small>DoH</small><strong>{check?.doh_ms != null ? `${check.doh_ms} мс` : provider.doh_url ? "—" : "нет"}</strong></span>
                    </div>
                    <div className="dnsResolverActions">
                      <button type="button" onClick={() => void checkDnsProviders(provider.id)} disabled={checkingDns}>Проверить</button>
                      <button type="button" className="primary" disabled={selected} onClick={() => setDnsDraft((value) => value ? { ...value, selected_id: provider.id } : value)}>{selected ? "Выбран" : "Выбрать"}</button>
                    </div>
                  </div>;
                })}
              </div>
            </section>

            <section className="dnsCustomStrip">
              <div className="dnsSectionTitle compact"><div><p className="eyebrow">CUSTOM DNS</p><h3>Собственный резолвер</h3></div><button type="button" className={dnsDraft?.selected_id === "custom" ? "active" : ""} onClick={() => setDnsDraft((value) => value ? { ...value, selected_id: "custom", custom: value.custom || { name: "Собственный DNS", addresses: [""], doh_url: "" } } : value)}>{dnsDraft?.selected_id === "custom" ? "Используется" : "Использовать"}</button></div>
              {dnsDraft?.selected_id === "custom" && <div className="dnsCustomFields">
                <label><span>Название</span><input value={dnsDraft.custom?.name || ""} onChange={(event) => setDnsDraft((value) => value ? { ...value, custom: { ...(value.custom || { addresses: [""], doh_url: "" }), name: event.target.value } } : value)} /></label>
                <label><span>IP-адреса</span><input value={(dnsDraft.custom?.addresses || []).join(", ")} onChange={(event) => setDnsDraft((value) => value ? { ...value, custom: { ...(value.custom || { name: "Собственный DNS", doh_url: "" }), addresses: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) } } : value)} /></label>
                <label><span>DoH URL</span><input type="url" placeholder="https://dns.example/dns-query" value={dnsDraft.custom?.doh_url || ""} onChange={(event) => setDnsDraft((value) => value ? { ...value, custom: { ...(value.custom || { name: "Собственный DNS", addresses: [""] }), doh_url: event.target.value } } : value)} /></label>
              </div>}
            </section>

            <section className="dnsEffectiveStrip">
              <div className="dnsSectionTitle compact"><div><p className="eyebrow">EFFECTIVE DNS</p><h3>Фактическое применение</h3></div><span>Новые конфигурации и server-side VRX</span></div>
              <div className="dnsEffectRows">
                {Object.entries(dns?.protocol_effect_details || {}).map(([protocol, effect]) => <div className={effect.matches_selected ? "matches" : "differs"} key={protocol}>
                  <strong>{protocol === "vless-reality-xhttp" ? "VRX" : protocol === "shadowsocks" ? "SS" : protocol.toUpperCase()}</strong>
                  <span>{effect.value}</span>
                  <em>{effect.matches_selected ? "MATCH" : "DIFFERS"}</em>
                </div>)}
              </div>
            </section>
          </div>
        </article>

        <div className="dnsSaveBar">
          <div><p className="eyebrow">PENDING DNS POLICY</p><strong>{dns?.providers.find((item) => item.id === dnsDraft?.selected_id)?.name || dnsDraft?.custom?.name || "DNS не выбран"}</strong><span>Активные WG/AWG-подключения и существующие конфигурации не изменяются.</span></div>
          <button type="button" onClick={() => void saveDnsSettings()} disabled={busy || !dnsDraft}>{busy ? "Применяем…" : "Применить"}</button>
        </div>
      </section>;
}
