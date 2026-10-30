export type ProfileRouting = Record<string, string | number | boolean>;
export const profileFunctionKeys: readonly string[] = ["block_malware", "block_privacy", "quic_compatibility"];

export function profileRoutingSettings(defaults: ProfileRouting, profile: ProfileRouting): ProfileRouting {
  const result = { ...defaults, ...profile };
  // Traffic presets are opt-in per profile, matching the YAML generator.
  for (const key of profileFunctionKeys) result[key] = Boolean(profile[key]);
  return result;
}

export function profileConnectionMode(routing: ProfileRouting): "tun" | "proxy" {
  if (routing.connection_mode === "tun" || routing.connection_mode === "proxy") return routing.connection_mode;
  return routing.tun_enabled || routing.tun_force ? "tun" : "proxy";
}

export function profileModeChange(key: string, value: string | number | boolean): ProfileRouting {
  if (key === "connection_mode") return { connection_mode: value, tun_enabled: value === "tun", tun_force: false };
  if (key === "routing_scope") return { routing_scope: value, mode: "rule" };
  return { [key]: value };
}

export function profileRuleVisible(key: string, routing: ProfileRouting) {
  if (profileFunctionKeys.includes(key)) return false;
  if (profileConnectionMode(routing) === "proxy" && ["windows_geolocation", "direct_games_enabled", "direct_p2p_enabled"].includes(key)) return false;
  return routing.routing_scope !== "selected" || key === "direct_local_network";
}
