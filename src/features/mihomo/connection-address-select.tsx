"use client";

import { useId, useState } from "react";
import { relayAddresses, type RelayStatus } from "../network/relay-state";
import { connectionAddressModes, xrayProtocolSettings } from "./connection-address";
import type { ProfileConnection } from "./types";
import { ConnectionRouteMap, connectionPort, relayListenPort } from "./connection-route-map";

/** Protocol mode and the address of the next hop are independent choices. */
export function MihomoConnectionAddress({ connection, inventory, tlsDomains, udpDomains, cdnDomains, webTransports, supported, disabled, onChange }: {
  connection: ProfileConnection;
  inventory: { status: RelayStatus | null; failed: boolean };
  tlsDomains: string[]; udpDomains: string[]; cdnDomains: string[]; webTransports: string[];
  supported: boolean; disabled: boolean; onChange: (settings: ProfileConnection["settings"]) => void;
}) {
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const searchId = useId();
  const settings = connection.settings;
  const xrayProtocol = String(settings.xray_protocol || "vless");
  const modes = connectionAddressModes(connection.component);
  const mode = modes.tls ? String(settings.route_mode || (settings.cdn_enabled ? "both" : "direct")) : modes.udp ? "udp" : "direct";
  const relay = String(settings.relay_host_id || "");
  const cdnRelay = mode === "cdn" && settings.relay_cdn === true;
  const legacyCdnRelay = cdnRelay && settings.relay_cdn_mode !== "before_cdn";
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
  const origin = relay ? inventory.status?.origin_ip || connection.credential?.server_address : connection.credential?.server_address || inventory.status?.origin_ip;
  const originPort = mode === "cdn" || mode === "tls" ? 443 : connectionPort(settings.port) || connectionPort(connection.credential?.port);
  const transports = connection.component === "transport-shadowsocks" ? ["tcp", "udp"] : modes.udp ? ["udp"] : ["tcp"];
  const relayPort = relayListenPort(savedHost, inventory.status?.origin_ip, originPort, transports, cdnRelay && !legacyCdnRelay ? domain : undefined);
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
        ...(next === "cdn" ? { relay_cdn: Boolean(relay), relay_cdn_mode: settings.relay_cdn ? settings.relay_cdn_mode || "after_cdn" : "before_cdn" } : {}),
        ...(next !== "cdn" ? { relay_cdn: false } : {}) });
    }
  }
  return <div className="mihomoConnectionAddress">
    <div className="mihomoConnectionControls">
    {modes.tls && <><select className="mihomoConnectionProtocol" aria-label="Протокол Xray" title="Протокол Xray" value={xrayProtocol} disabled={locked} onChange={event => onChange(xrayProtocolSettings(settings, event.target.value, tlsDomains, cdnDomains))}>
      <option value="vless">VLESS</option><option value="vmess">VMess</option><option value="trojan">Trojan</option>
    </select>
    <span className="mihomoAddressModes" role="group" aria-label="Режим подключения">
        {xrayProtocol === "vless" && <button type="button" aria-pressed={mode === "direct"} disabled={locked} onClick={() => selectMode("direct")}>REALITY</button>}
        {webTransports.length > 0 && <>
          <button type="button" aria-pressed={mode === "tls"} disabled={locked || !tlsServices.length} onClick={() => selectMode("tls")}>TLS</button>
          <button type="button" aria-pressed={mode === "cdn"} disabled={locked || !cdnDomains.length && !settings.cdn_domain} onClick={() => selectMode("cdn")}>CDN</button>
        </>}
    </span></>}
    <label className="mihomoConnectionEndpoint"><span>Адрес</span><select aria-label="Адрес подключения" value={selected} disabled={locked} onChange={event => {
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
    <button type="button" className="mihomoConnectionSearch" aria-label="Поиск адреса подключения" title="Найти адрес" aria-expanded={searchOpen} aria-controls={searchId} disabled={locked} onClick={() => { setSearchOpen(!searchOpen); setQuery(""); }}><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8" cy="8" r="5" /><path d="m12 12 5 5" /></svg></button>
    </div>
    <input id={searchId} hidden={!searchOpen} type="search" aria-label="Поиск адреса подключения" placeholder="Поиск адреса…" value={query} onChange={event => setQuery(event.target.value)} disabled={locked} />
    {mode === "cdn" && (webTransports.length > 0 || cdnRelay) && <><label><span>Вход в CDN</span><select aria-label="Вход в CDN" value={relay ? JSON.stringify(["relay", relay, relayAddress]) : ""} disabled={locked} onChange={event => {
      const choice = choices.find(item => item.key === event.target.value);
      change({ relay_host_id: choice?.host.id || "", relay_address: choice?.address || "", relay_cdn: Boolean(choice), relay_cdn_mode: "before_cdn" });
    }}>
      <option value="">Напрямую к CDN</option>
      {relay && !choices.some(choice => choice.key === JSON.stringify(["relay", relay, relayAddress])) && <option value={JSON.stringify(["relay", relay, relayAddress])}>{relayAddress || "Сохранённый Relay"} · {savedHost?.name || "сохранённый узел"}</option>}
      {choices.map(choice => <option key={choice.key} value={choice.key}>{choice.address} · Relay · {choice.host.name}</option>)}
    </select></label>
    {legacyCdnRelay && <>
      <button type="button" disabled={locked} onClick={() => change({ relay_cdn_mode: "before_cdn" })}>Перенести Relay перед CDN</button>
      <small>Для перехода направьте CDN на VPS и уберите прежние Origin Rules этого подключения.</small>
    </>}</>}
    {mode === "tls" && relay && <><label><span>TLS-сервис VPS</span><select aria-label="TLS-сервис основного VPS" value={tlsDomain} disabled={locked} onChange={event => change({ tls_domain: event.target.value })}>{tlsServices.map(item => <option key={item} value={item}>{item}</option>)}</select></label><small>Имя TLS-сервиса сохраняется для SNI и проверки сертификата; подключение идёт на адрес Relay.</small></>}
    <ConnectionRouteMap route={{ mode: mode as "direct" | "tls" | "cdn" | "udp" | "both", originAddress: origin, originPort, domain,
      ...(relay ? { relay: { address: relayAddress, port: relayPort, afterCdn: legacyCdnRelay } } : {}) }} />
    {savedOnly && <small>Сохранённый маршрут оставлен без изменений. Для нового назначения выберите доступный адрес.</small>}
    {modes.tls && webTransports.length > 0 && !tlsServices.length && <small>Для TLS через Relay сначала добавьте рабочий TLS-домен основного VPS в Сеть → ROUTES.</small>}
    {canRelay && (inventory.failed || !inventory.status) && <small>{inventory.failed ? "Список Relay временно недоступен. Сохранённый адрес оставлен без изменений." : "Обновляем адреса подключённых Relay…"}</small>}
  </div>;
}
