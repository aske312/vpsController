"use client";

import { NotificationNotice } from "../../shared/notifications/notification-center";
import { useId, useState } from "react";
import { relayAddresses, type RelayStatus } from "../network/relay-state";
import { connectionAddressModes, isRelayOriginTls, relayOriginTlsSettings, snellCarriers, xrayProtocolSettings } from "./connection-address";
import type { ProfileConnection } from "./types";

/** Protocol mode and the address of the next hop are independent choices. */
export function MihomoConnectionAddress({ connection, connections = [], inventory, tlsDomains, udpDomains, cdnDomains, webTransports, supported, disabled, onChange }: {
  connection: ProfileConnection;
  connections?: ProfileConnection[];
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
  const originTls = modes.tls && isRelayOriginTls(settings);
  const legacyCdnRelay = cdnRelay && settings.relay_cdn_mode !== "before_cdn";
  const tlsDomain = String(settings.tls_domain || "");
  const domain = String(mode === "tls" ? tlsDomain : mode === "cdn" || mode === "both" ? settings.cdn_domain || "" : mode === "udp" ? settings.udp_domain || "" : "");
  const domains = [...new Set([...(domain ? [domain] : []), ...(mode === "tls" ? tlsDomains : mode === "cdn" || mode === "both" ? cdnDomains : mode === "udp" ? udpDomains : [])])];
  const tlsServices = [...new Set([...(tlsDomain ? [tlsDomain] : []), ...tlsDomains])];
  const choices = inventory.status?.hosts.filter(host => host.enabled !== false).flatMap(host => relayAddresses(host)
    .filter(address => !host.addresses?.cdn_domains?.includes(address))
    .map(address => ({ host, address, key: JSON.stringify(["relay", host.id, address]) }))) || [];
  const savedHost = inventory.status?.hosts.find(host => host.id === relay);
  const relayAddress = String(settings.relay_address || savedHost?.client_address || (savedHost ? new URL(savedHost.agent_url).hostname.replace(/^\[|\]$/g, "") : ""));
  const selected = relay && !cdnRelay ? JSON.stringify(["relay", relay, relayAddress]) : domain ? JSON.stringify(["direct", domain]) : "";
  const canRelay = modes.relay && (originTls || mode !== "cdn" && mode !== "both");
  const savedOnly = Boolean(relay && !cdnRelay && (!canRelay || !choices.some(choice => choice.key === selected)));
  const locked = disabled || !supported && !modes.tls;
  const snell = connection.component === "transport-snell";
  const carrierId = snell ? String(settings.carrier_connection_id || "") : "";
  const carriers = snellCarriers(connection, connections);
  const delivery = snell && <label className="mihomoConnectionEndpoint"><span>Доставка Snell</span><select aria-label="Доставка Snell" value={carrierId} disabled={locked} onChange={event => onChange({ ...settings,
    carrier_connection_id: event.target.value, relay_host_id: "", relay_address: "", route_mode: "direct", cdn_enabled: false, relay_cdn: false, cdn_domain: "", tls_domain: "" })}>
    <option value="">Напрямую или через Relay</option>
    {carrierId && !carriers.some(item => item.id === carrierId) && <option value={carrierId} disabled>Внешний канал недоступен · привязка сохранена</option>}
    {carriers.map(item => <option key={item.id} value={item.id}>{item.name} · Xray {String(item.settings.route_mode).toUpperCase()}</option>)}
  </select></label>;
  if (carrierId) return <div className="mihomoConnectionAddress">{delivery}<small>Snell проходит внутри выбранного Xray-канала. Домен, CDN, транспорт и Relay настраиваются в этом Xray-подключении. Обновите подписку клиента после сохранения.</small></div>;
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
    {delivery}
    <div className="mihomoConnectionControls">
    {modes.tls && <><select className="mihomoConnectionProtocol" aria-label="Протокол Xray" title="Протокол Xray" value={xrayProtocol} disabled={locked} onChange={event => onChange(xrayProtocolSettings(settings, event.target.value, tlsDomains, cdnDomains))}>
      <option value="vless">VLESS</option><option value="vmess">VMess</option><option value="trojan">Trojan</option>
    </select>
    <span className="mihomoAddressModes" role="group" aria-label="Режим подключения">
        {xrayProtocol === "vless" && <button type="button" aria-pressed={mode === "direct"} disabled={locked} onClick={() => selectMode("direct")}>REALITY</button>}
        {webTransports.length > 0 && <>
          <button type="button" aria-pressed={mode === "tls"} disabled={locked || !tlsServices.length} onClick={() => selectMode("tls")}>TLS</button>
          <button type="button" aria-pressed={mode === "cdn" && !originTls} disabled={locked || !cdnDomains.length && !settings.cdn_domain} onClick={() => selectMode("cdn")}>CDN</button>
          <button type="button" aria-pressed={originTls} disabled={locked || !choices.length || !cdnDomains.length && !settings.cdn_domain} onClick={() => {
            const choice = choices.find(item => item.host.id === relay && item.address === relayAddress) || choices[0];
            if (choice) onChange(relayOriginTlsSettings(settings, String(settings.cdn_domain || cdnDomains[0] || ""), choice.host.id, choice.address));
          }}>TLS через Relay</button>
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
      {savedOnly && <option value={selected} disabled={savedHost?.enabled === false}>{savedHost?.enabled === false ? "Relay отключён · привязка сохранена" : `${relayAddress || "Сохранённый Relay"} · через Relay · сохранённый адрес`}</option>}
      {!originTls && domains.filter(item => selected === JSON.stringify(["direct", item]) || matches(item)).map(item => <option key={item} value={JSON.stringify(["direct", item])}>{item} · {cdnRelay ? `CDN через Relay · ${savedHost?.name || "сохранённый узел"}` : mode === "cdn" ? "CDN" : "напрямую"}</option>)}
      {canRelay && choices.filter(choice => choice.key === selected || matches(`${choice.address} ${choice.host.name}`)).map(choice => <option key={choice.key} value={choice.key}>{choice.address} · через Relay · {choice.host.name}</option>)}
    </select></label>
    <button type="button" className="mihomoConnectionSearch" aria-label="Поиск адреса подключения" title="Найти адрес" aria-expanded={searchOpen} aria-controls={searchId} disabled={locked} onClick={() => { setSearchOpen(!searchOpen); setQuery(""); }}><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8" cy="8" r="5" /><path d="m12 12 5 5" /></svg></button>
    </div>
    <input id={searchId} hidden={!searchOpen} type="search" aria-label="Поиск адреса подключения" placeholder="Поиск адреса…" value={query} onChange={event => setQuery(event.target.value)} disabled={locked} />
    {mode === "cdn" && !originTls && (webTransports.length > 0 || cdnRelay) && <><label><span>Вход в CDN</span><select aria-label="Вход в CDN" value={relay ? JSON.stringify(["relay", relay, relayAddress]) : ""} disabled={locked} onChange={event => {
      const choice = choices.find(item => item.key === event.target.value);
      change({ relay_host_id: choice?.host.id || "", relay_address: choice?.address || "", relay_cdn: Boolean(choice), relay_cdn_mode: "before_cdn" });
    }}>
      <option value="">Напрямую к CDN</option>
      {relay && !choices.some(choice => choice.key === JSON.stringify(["relay", relay, relayAddress])) && <option value={JSON.stringify(["relay", relay, relayAddress])} disabled={savedHost?.enabled === false}>{savedHost?.enabled === false ? "Relay отключён · привязка сохранена" : `${relayAddress || "Сохранённый Relay"} · ${savedHost?.name || "сохранённый узел"}`}</option>}
      {choices.map(choice => <option key={choice.key} value={choice.key}>{choice.address} · Relay · {choice.host.name}</option>)}
    </select></label>
    {legacyCdnRelay && <>
      <button type="button" disabled={locked} onClick={() => change({ relay_cdn_mode: "before_cdn" })}>Перенести Relay перед CDN</button>
      <small>Для перехода направьте CDN на VPS и уберите прежние Origin Rules этого подключения.</small>
    </>}</>}
    {mode === "tls" && relay && <><label><span>TLS-сервис VPS</span><select aria-label="TLS-сервис основного VPS" value={tlsDomain} disabled={locked} onChange={event => change({ tls_domain: event.target.value })}>{tlsServices.map(item => <option key={item} value={item}>{item}</option>)}</select></label></>}
    {originTls && <><label><span>TLS-имя сервиса VPS</span><select aria-label="TLS-имя сервиса VPS через Relay" value={String(settings.cdn_domain || "")} disabled={locked} onChange={event => change({ cdn_domain: event.target.value })}>{domains.map(item => <option key={item} value={item}>{item}</option>)}</select></label></>}
    {savedOnly && <NotificationNotice id={`mihomo:saved-route:${connection.id}`} title="Адрес подключения" state="info" message="Сохранённый маршрут оставлен без изменений. Для нового назначения выберите доступный адрес." />}
    {modes.tls && webTransports.length > 0 && !tlsServices.length && !originTls && <small>Для отдельного TLS-сервиса сначала добавьте TLS-домен основного VPS в Сеть → ROUTES.</small>}
    {canRelay && inventory.failed && <NotificationNotice id="relay:inventory" title="Адреса Relay" message="Список Relay временно недоступен. Сохранённый адрес оставлен без изменений." />}
    {snell && <small>Snell v6 передаёт TCP и UDP внутри TCP. Для Relay нужен TCP-маршрут. Обычный HTTP-CDN напрямую не поддерживается: добавьте Xray TLS/CDN-подключение этого устройства и выберите его в «Доставка Snell». Требуется Mihomo 1.19.34 или новее.</small>}
    {connections.some(item => item.settings.carrier_connection_id === connection.id) && <small>Это внешний канал доставки Snell. Он исключён из общего автовыбора, чтобы соединение проходило через Snell.</small>}
  </div>;
}
