import type { ProfileConnection } from "./types";
import { clientConnectionSupported, clientTransports } from "./client-capabilities.ts";

const udpComponents = new Set(["transport-wg", "transport-awg", "transport-hysteria2", "transport-tuic", "transport-shadowsocks"]);
export function isRelayOriginTls(settings: ProfileConnection["settings"]) {
  return settings.route_mode === "cdn" && Boolean(settings.relay_host_id) && settings.relay_cdn !== true;
}

export function relayOriginTlsSettings(settings: ProfileConnection["settings"], domain: string, hostId: string, address: string): ProfileConnection["settings"] {
  return { ...settings, route_mode: "cdn", cdn_enabled: true, cdn_domain: domain,
    relay_host_id: hostId, relay_address: address, relay_cdn: false, udp_domain: "" };
}

export function connectionAddressModes(component: string) {
  return { tls: component === "transport-reality", cdn: component === "transport-reality", udp: udpComponents.has(component), relay: udpComponents.has(component) || ["transport-reality", "transport-snell"].includes(component) };
}

export function snellCarriers(connection: ProfileConnection, connections: ProfileConnection[]) {
  return connections.filter(item => item.id !== connection.id && item.device_id === connection.device_id
    && item.component === "transport-reality" && ["cdn", "tls"].includes(String(item.settings.route_mode)) && clientConnectionSupported(item));
}

export function xrayProtocolSettings(settings: ProfileConnection["settings"], protocol: string, tlsDomains: string[], cdnDomains: string[]): ProfileConnection["settings"] {
  if (!["vless", "vmess", "trojan"].includes(protocol)) return settings;
  const next: ProfileConnection["settings"] = { ...settings, xray_protocol: protocol };
  const transports = clientTransports(protocol);
  for (const key of ["tls_transport", "cdn_transport"]) {
    if (!transports.includes(String(next[key] || (key === "tls_transport" ? "xhttp" : "websocket")))) next[key] = "websocket";
  }
  if (protocol !== "vless" && !["tls", "cdn"].includes(String(settings.route_mode))) {
    const mode = tlsDomains.length || !cdnDomains.length ? "tls" : "cdn";
    return { ...next, route_mode: mode, cdn_enabled: mode === "cdn", relay_cdn: false,
      [`${mode}_domain`]: settings[`${mode}_domain`] || (mode === "tls" ? tlsDomains[0] : cdnDomains[0]) || "" };
  }
  return next;
}
