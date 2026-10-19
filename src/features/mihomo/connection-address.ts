import type { ProfileConnection } from "./types";

/** Copy only the endpoint choice, keeping each protocol's listener and credentials. */
export function applyConnectionAddress(connections: ProfileConnection[], source: ProfileConnection): ProfileConnection[] {
  const relay = Boolean(source.settings.relay_host_id);
  const cdn = !relay && source.component === "transport-reality" && source.settings.route_mode === "cdn";
  return connections.map(connection => {
    if (connection.device_id !== source.device_id || cdn && connection.component !== "transport-reality") return connection;
    const settings: ProfileConnection["settings"] = { ...connection.settings, relay_host_id: relay ? source.settings.relay_host_id : "", relay_address: relay ? source.settings.relay_address || "" : "" };
    if (connection.component === "transport-reality") {
      if (cdn) Object.assign(settings, { route_mode: "cdn", cdn_enabled: true, cdn_domain: source.settings.cdn_domain });
      else if (settings.route_mode === "cdn" || settings.cdn_enabled) Object.assign(settings, { route_mode: "direct", cdn_enabled: false });
    }
    return { ...connection, settings };
  });
}
