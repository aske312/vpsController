"use client";

import { useState } from "react";
import { relayAddresses, type RelayStatus } from "../network/relay-state";
import { connectionAddressModes } from "./connection-address";
import type { ProfileConnection } from "./types";

/** Protocol mode and the address of the next hop are independent choices. */
export function MihomoConnectionAddress({ connection, inventory, tlsDomains, udpDomains, cdnDomains, webTransports, supported, disabled, onChange }: {
  connection: ProfileConnection;
  inventory: { status: RelayStatus | null; failed: boolean };
  tlsDomains: string[]; udpDomains: string[]; cdnDomains: string[]; webTransports: string[];
  supported: boolean; disabled: boolean; onChange: (settings: ProfileConnection["settings"]) => void;
}) {
  const [query, setQuery] = useState("");
  const settings = connection.settings;
  const modes = connectionAddressModes(connection.component);
  const mode = modes.tls ? String(settings.route_mode || (settings.cdn_enabled ? "both" : "direct")) : modes.udp ? "udp" : "direct";
  const label = mode === "direct" && modes.tls ? "REALITY" : mode === "udp" && connection.component === "transport-shadowsocks" ? "TCP + UDP" : mode === "both" ? "Прежний режим" : mode.toUpperCase();
  const relay = String(settings.relay_host_id || "");
  const cdnRelay = mode === "cdn" && settings.relay_cdn === true;
  const tlsDomain = String(settings.tls_domain || "");
  const domain = String(mode === "tls" ? tlsDomain : mode === "cdn" || mode === "both" ? settings.cdn_domain || "" : mode === "udp" ? settings.udp_domain || "" : "");
  const domains = [...new Set([...(domain ? [domain] : []), ...(mode === "tls" ? tlsDomains : mode === "cdn" || mode === "both" ? cdnDomains : mode === "udp" ? udpDomains : [])])];
  const tlsServices = [...new Set([...(tlsDomain ? [tlsDomain] : []), ...tlsDomains])];
  const choices = inventory.status?.hosts.flatMap(host => relayAddresses(host)
    .filter(address => !host.addresses?.cdn_domains?.includes(address))
    .map(address => ({ host, address, key: JSON.stringify(["relay", host.id, address]) }))) || [];
  const savedHost = inventory.status?.hosts.find(host => host.id === relay);
  const relayAddress = String(settings.relay_address || savedHost?.client_address || (savedHost ? new URL(savedHost.agent_url).hostname.replace(/^\[|\]$/g, "") : ""));
  const selected = relay && !cdnRelay ? JSON.stringify(["relay", relay, relayAddress]) : domain ? JSON.stringify(["direct", domain]) : "";
  const canRelay = modes.relay && mode !== "cdn" && mode !== "both";
  const savedOnly = Boolean(relay && !cdnRelay && (!canRelay || !choices.some(choice => choice.key === selected)));
  const cdnPort = (target: number) => savedHost?.routes.filter(route => route.transport === "tcp" && route.target_port === target && route.target_ip === inventory.status?.origin_ip).sort((a, b) => a.listen_port - b.listen_port)[0]?.listen_port;
  const locked = disabled || !supported;
  const matches = (text: string) => text.toLowerCase().includes(query.trim().toLowerCase());
  const change = (patch: ProfileConnection["settings"]) => onChange({ ...settings, ...patch });
  function selectMode(next: "direct" | "tls" | "cdn") {
    setQuery("");
    if (next === "direct") change({ route_mode: "direct", cdn_enabled: false, udp_domain: "", relay_cdn: false });
    else {
      const configDomain = next === "tls" ? tlsDomain || tlsDomains[0] : String(settings.cdn_domain || cdnDomains[0] || "");
      const field = next === "tls" ? "tls_transport" : "cdn_transport";
      change({ route_mode: next, cdn_enabled: next === "cdn", [`${next}_domain`]: configDomain,
        [field]: webTransports.includes(String(settings[field])) ? settings[field] : webTransports[0], udp_domain: "",
        ...(next === "cdn" && !settings.relay_cdn ? { relay_host_id: "", relay_address: "" } : {}),
        ...(next !== "cdn" ? { relay_cdn: false } : {}) });
    }
  }
  return <div className="relayEndpointSelector">
    <span>Режим подключения</span>
    <span className="mihomoAddressModes" role="group" aria-label="Режим подключения">
      {modes.tls ? <>
        <button type="button" aria-pressed={mode === "direct"} disabled={locked} onClick={() => selectMode("direct")}>REALITY</button>
        {webTransports.length > 0 && <>
          <button type="button" aria-pressed={mode === "tls"} disabled={locked || !tlsServices.length} onClick={() => selectMode("tls")}>TLS</button>
          <button type="button" aria-pressed={mode === "cdn"} disabled={locked || !cdnDomains.length && !settings.cdn_domain} onClick={() => selectMode("cdn")}>CDN</button>
        </>}
      </> : modes.udp ? <button type="button" aria-pressed disabled={locked}>{label}</button> : <span>Прямое подключение</span>}
    </span>
    <input type="search" aria-label="Поиск адреса подключения" placeholder="Найти IP, домен или узел…" value={query} onChange={event => setQuery(event.target.value)} disabled={locked} />
    <label><span>Адрес подключения</span><select aria-label="Адрес подключения" value={selected} disabled={locked} onChange={event => {
      if (!event.target.value) { change({ relay_host_id: "", relay_address: "", udp_domain: "" }); return; }
      const choice = choices.find(item => item.key === event.target.value);
      if (choice && canRelay) change({ relay_host_id: choice.host.id, relay_address: choice.address, udp_domain: "" });
      else {
        const entry = domains.find(item => JSON.stringify(["direct", item]) === event.target.value);
        if (entry) change({ ...(!cdnRelay ? { relay_host_id: "", relay_address: "" } : {}), [mode === "tls" ? "tls_domain" : mode === "udp" ? "udp_domain" : "cdn_domain"]: entry });
      }
    }}>
      {(mode === "direct" || mode === "udp") && <option value="">IP этого VPS · напрямую</option>}
      {savedOnly && <option value={selected}>{relayAddress || "Сохранённый Relay"} · через Relay · сохранённый адрес</option>}
      {domains.filter(item => selected === JSON.stringify(["direct", item]) || matches(item)).map(item => <option key={item} value={JSON.stringify(["direct", item])}>{item} · {cdnRelay ? `CDN через Relay · ${savedHost?.name || "сохранённый узел"}` : mode === "cdn" ? "CDN" : "напрямую"}</option>)}
      {canRelay && choices.filter(choice => choice.key === selected || matches(`${choice.address} ${choice.host.name}`)).map(choice => <option key={choice.key} value={choice.key}>{choice.address} · через Relay · {choice.host.name}</option>)}
    </select></label>
    {mode === "cdn" && (webTransports.includes("websocket") || cdnRelay) && <><label><span>Сервер приёма CDN</span><select aria-label="Сервер приёма CDN" value={relay} disabled={locked} onChange={event => change({ relay_host_id: event.target.value, relay_address: "", relay_cdn: Boolean(event.target.value), ...(event.target.value ? { cdn_transport: "websocket" } : {}) })}>
      <option value="">Основной VPS</option>
      {relay && !savedHost && <option value={relay}>Сохранённый Relay</option>}
      {inventory.status?.hosts.map(host => <option key={host.id} value={host.id}>{host.name} · {new URL(host.agent_url).hostname}</option>)}
    </select></label>{relay && !cdnRelay && <button type="button" disabled={locked} onClick={() => change({ relay_cdn: true, relay_address: "", cdn_transport: "websocket" })}>Применить CDN через Relay</button>}
    {cdnRelay && <><small>Порты Origin Rules: HTTPS → {cdnPort(443) || "после сохранения"}; HTTP ACME → {cdnPort(80) || "после сохранения"}.</small>
      <details><summary>Настройки Cloudflare</summary><p>Для {domain} включите DNS proxy и укажите IP выбранного Relay. TLS: Full (strict). Правило для HTTPS направьте на порт HTTPS выше. Отдельное правило HTTP для /.well-known/acme-challenge/* направьте на порт ACME, без редиректа на HTTPS. Это нужно для выпуска и продления сертификата на основном VPS. Порты также доступны в Сеть → ROUTES после сохранения. Клиент подключается к {domain}:443. Правила Cloudflare не меняются автоматически; готовность подтверждает проверка маршрута.</p></details></>}</>}
    {mode === "tls" && relay && <label><span>TLS-сервис основного VPS</span><select aria-label="TLS-сервис основного VPS" value={tlsDomain} disabled={locked} onChange={event => change({ tls_domain: event.target.value })}>{tlsServices.map(item => <option key={item} value={item}>{item}</option>)}</select><small>Имя TLS-сервиса сохраняется для SNI и проверки сертификата; подключение идёт на адрес Relay.</small></label>}
    <small>{cdnRelay ? "VLESS WebSocket через CDN и Relay. Сертификат CDN-домена и обработчик протокола находятся на основном VPS." : relay ? savedOnly ? "Сохранённый маршрут оставлен без изменений. Для нового назначения выберите доступный адрес." : mode === "tls" ? "Relay пересылает TCP до TLS-сервиса основного VPS. Внешний порт будет назначен при сохранении." : `Через Relay · ${mode === "udp" ? label : "TCP"}. Порт и пересылка будут настроены при сохранении; параметры протокола сохраняются.` : mode === "cdn" ? "VLESS через CDN-домен основного VPS." : mode === "tls" ? "TLS-вход основного VPS с проверкой сертификата." : "Прямое подключение к основному VPS."}</small>
    {modes.tls && webTransports.length > 0 && !tlsServices.length && <small>Для TLS через Relay сначала добавьте рабочий TLS-домен основного VPS в Сеть → ROUTES.</small>}
    {canRelay && (inventory.failed || !inventory.status) && <small>{inventory.failed ? "Список Relay временно недоступен. Сохранённый адрес оставлен без изменений." : "Обновляем адреса подключённых Relay…"}</small>}
  </div>;
}
