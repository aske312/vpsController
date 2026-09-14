"use client";

import type { NetworkEndpointSettings } from "../../shared/types/control-plane";

export function NetworkEndpoints({
  draft, busy, dirty, onChange, onSave,
}: {
  draft: NetworkEndpointSettings;
  busy: boolean;
  dirty: boolean;
  onChange: (key: keyof NetworkEndpointSettings, value: string) => void;
  onSave: () => void;
}) {
  return <section className="networkPanel networkEndpointPanel" aria-label="Домены защищённых каналов">
    <header className="networkSectionHeading"><div><h2>Домены защищённых каналов</h2><p>Общие точки входа для будущих CDN/ECH и relay-маршрутов.</p></div><span className="networkEndpointStatus">{dirty ? "Есть изменения" : "Сохранено"}</span></header>
    <div className="networkEndpointGrid">
      <label><span>CDN / ECH домен</span><input type="text" value={draft.cdn_domain} onChange={(event) => onChange("cdn_domain", event.target.value)} placeholder="cdn.example.com" autoComplete="off" /><small>Общий reference для CDN. Рабочий VLESS CDN настраивается отдельно.</small></label>
      <label><span>TLS relay домен</span><input type="text" value={draft.tls_relay_domain} onChange={(event) => onChange("tls_relay_domain", event.target.value)} placeholder="tls-relay.example.com" autoComplete="off" /><small>Точка TCP/TLS relay; порт исходного протокола сохраняется.</small></label>
      <label><span>UDP relay домен</span><input type="text" value={draft.udp_relay_domain} onChange={(event) => onChange("udp_relay_domain", event.target.value)} placeholder="udp-relay.example.com" autoComplete="off" /><small>Точка UDP relay; порт исходного протокола сохраняется.</small></label>
    </div>
    <div className="networkEndpointFooter"><p>После настройки домена выберите relay-режим у протокола на странице «Защищённые каналы». Relay-сервисы и их DNS-маршрутизация подготавливаются отдельно.</p><button type="button" className="networkPrimaryButton" onClick={onSave} disabled={busy || !dirty}>{busy ? "Сохраняем…" : "Сохранить домены"}</button></div>
  </section>;
}
