import catalog from "../../../protocol-images/mihomo/client-capabilities.json" with { type: "json" };
import extensions from "../../../protocol-images/mihomo/client-extensions.json" with { type: "json" };
import type { ProfileConnection } from "./types";

export function clientCapabilities(format: unknown, os?: string, client?: string): { label: string; components: string[]; transports: string[]; features: string[]; strategies: string[]; rules: string[] } {
  const base = catalog[format as keyof typeof catalog] || catalog.mihomo;
  const extension = extensions[client as keyof typeof extensions];
  const caps = extension && extension.format === format ? { ...base, components: [...base.components, ...extension.components], transports: [...base.transports, ...extension.transports], features: [...base.features, ...extension.features] } : base;
  return { ...caps, rules: os !== undefined && !["windows", "macos", "linux"].includes(os) ? caps.rules.filter((key) => !["direct_games_enabled", "direct_p2p_enabled"].includes(key)) : caps.rules };
}

export function compatibleClientRouting(routing: Record<string, string | number | boolean>, format: string, os?: string, client?: string) {
  const caps = clientCapabilities(format, os, client);
  const result: Record<string, string | number | boolean> = { ...routing, client_config_format: format };
  for (const key of [...catalog.mihomo.rules, "tunnel_privacy", "tunnel_ech", "tunnel_fragment"]) {
    if (key in result && ![...caps.features, ...caps.rules].includes(key)) result[key] = false;
  }
  if (!caps.strategies.includes(String(result.strategy || ""))) result.strategy = caps.strategies[0] || "";
  return result;
}

export function clientConnectionSupported(connection: Pick<ProfileConnection, "component" | "settings">, format: unknown, client?: string) {
  const caps = clientCapabilities(format, undefined, client);
  if (!caps.components.includes(connection.component)) return false;
  if (connection.component !== "transport-reality") return true;
  const settings = connection.settings;
  const mode = String(settings.route_mode || (settings.cdn_enabled ? "both" : "direct"));
  const variants = mode === "both" ? ["direct", "cdn"] : [mode];
  return variants.every((variant) => caps.transports.includes(String(variant === "direct" ? settings.transport || "xhttp" : settings[`${variant}_transport`] || (variant === "cdn" ? "websocket" : "xhttp"))));
}
