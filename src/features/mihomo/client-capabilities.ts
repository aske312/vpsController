import catalog from "../../../protocol-images/mihomo/client-capabilities.json" with { type: "json" };
import type { ProfileConnection } from "./types";

export function clientCapabilities(os?: string): { label: string; components: string[]; transports: string[]; features: string[]; strategies: string[]; rules: string[] } {
  const caps = catalog.mihomo;
  const platformRules = os !== undefined && os !== "windows" ? caps.rules.filter((key) => key !== "windows_geolocation") : caps.rules;
  return { ...caps, rules: os !== undefined && !["windows", "macos", "linux"].includes(os) ? platformRules.filter((key) => !["direct_games_enabled", "direct_p2p_enabled"].includes(key)) : platformRules };
}

export function compatibleClientRouting(routing: Record<string, string | number | boolean>, os?: string) {
  const caps = clientCapabilities(os);
  const result: Record<string, string | number | boolean> = Object.fromEntries(Object.entries(routing).filter(([key]) => key !== "client_config_format" && !key.startsWith("block_ads") && !key.startsWith("tunnel_fragment")));
  for (const key of [...catalog.mihomo.rules, "tunnel_privacy", "tunnel_ech"]) {
    if (key in result && ![...caps.features, ...caps.rules].includes(key)) result[key] = false;
  }
  if (!caps.strategies.includes(String(result.strategy || ""))) result.strategy = caps.strategies[0] || "";
  return result;
}

export function clientConnectionSupported(connection: Pick<ProfileConnection, "component" | "settings">) {
  const caps = clientCapabilities();
  if (!caps.components.includes(connection.component)) return false;
  if (connection.component !== "transport-reality") return true;
  const settings = connection.settings;
  const mode = String(settings.route_mode || (settings.cdn_enabled ? "both" : "direct"));
  const variants = mode === "both" ? ["direct", "cdn"] : [mode];
  return variants.every((variant) => caps.transports.includes(String(variant === "direct" ? settings.transport || "xhttp" : settings[`${variant}_transport`] || (variant === "cdn" ? "websocket" : "xhttp"))));
}
