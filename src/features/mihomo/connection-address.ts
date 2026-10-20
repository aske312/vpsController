import type { ProfileConnection } from "./types";

const udpComponents = new Set(["transport-wg", "transport-awg", "transport-hysteria2", "transport-tuic", "transport-shadowsocks"]);
export function connectionAddressModes(component: string) {
  return { tls: component === "transport-reality", cdn: component === "transport-reality", udp: udpComponents.has(component), relay: udpComponents.has(component) || component === "transport-reality" };
}

export function xrayProtocolSettings(settings: ProfileConnection["settings"], protocol: string, tlsDomains: string[], cdnDomains: string[]): ProfileConnection["settings"] {
  if (!["vless", "vmess", "trojan"].includes(protocol)) return settings;
  const next = { ...settings, xray_protocol: protocol };
  if (protocol !== "vless" && !["tls", "cdn"].includes(String(settings.route_mode))) {
    const mode = tlsDomains.length || !cdnDomains.length ? "tls" : "cdn";
    return { ...next, route_mode: mode, cdn_enabled: mode === "cdn", relay_cdn: false,
      [`${mode}_domain`]: settings[`${mode}_domain`] || (mode === "tls" ? tlsDomains[0] : cdnDomains[0]) || "" };
  }
  return next;
}

/** Copy only the endpoint choice, keeping each protocol's listener and credentials. */
export function applyConnectionAddress(connections: ProfileConnection[], source: ProfileConnection): ProfileConnection[] {
  const relay = Boolean(source.settings.relay_host_id);
  const cdnRelay = relay && source.component === "transport-reality" && source.settings.route_mode === "cdn" && source.settings.relay_cdn === true;
  const cdn = (!relay || cdnRelay) && source.component === "transport-reality" && source.settings.route_mode === "cdn";
  const tls = !relay && source.component === "transport-reality" && source.settings.route_mode === "tls";
  const udp = !relay && Boolean(source.settings.udp_domain);
  return connections.map(connection => {
    const modes = connectionAddressModes(connection.component);
    if (connection.component === "transport-reality" && connection.settings.xray_protocol && connection.settings.xray_protocol !== "vless" && !cdn && !tls && !(relay && source.settings.route_mode === "tls")) return connection;
    if (connection.device_id !== source.device_id || cdn && !modes.cdn || tls && !modes.tls || udp && !modes.udp || relay && !modes.relay) return connection;
    const settings: ProfileConnection["settings"] = { ...connection.settings, relay_host_id: relay ? source.settings.relay_host_id : "", relay_address: relay ? source.settings.relay_address || "" : "", udp_domain: udp ? source.settings.udp_domain : "" };
    if (cdnRelay || "relay_cdn" in settings) settings.relay_cdn = cdnRelay;
    if (cdnRelay) settings.relay_cdn_mode = source.settings.relay_cdn_mode || "after_cdn";
    if (connection.component === "transport-reality") {
      if (cdn) Object.assign(settings, { route_mode: "cdn", cdn_enabled: true, cdn_domain: source.settings.cdn_domain });
      else if (tls) Object.assign(settings, { route_mode: "tls", cdn_enabled: false, tls_domain: source.settings.tls_domain });
      else if (settings.route_mode === "cdn" || settings.cdn_enabled || !relay && settings.route_mode === "tls") Object.assign(settings, { route_mode: "direct", cdn_enabled: false });
    }
    return { ...connection, settings };
  });
}
