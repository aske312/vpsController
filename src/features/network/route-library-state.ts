import type { NetworkStatus, NetworkEndpointCheck } from "../../shared/types/control-plane";
import type { RelayHost } from "./relay-state";
import { relayAddresses } from "./relay-state";
import { domainRouteSummary, relayRouteSummary, type RouteSummary } from "./route-summary";

export type RouteEntry = { id: string; kind: "relay"; title: string; address: string; subtitle: string; search: string; summary: RouteSummary; host: RelayHost }
  | { id: string; kind: "domain"; title: string; address: string; subtitle: string; search: string; summary: RouteSummary; domain: NetworkStatus["domains"][number]; checks: NetworkEndpointCheck[] };
export type RouteFilter = "all" | "relay" | "domain" | "attention";
export const needsAttention = (summary: RouteSummary) => ["error", "warning", "stale", "unknown"].includes(summary.state);

export function routeEntries(status: NetworkStatus | null, hosts: RelayHost[], failed: boolean, relayFailed: boolean, pending: boolean, reading: boolean): RouteEntry[] {
  const entries: RouteEntry[] = hosts.map(host => {
    const addresses = relayAddresses(host);
    const address = addresses.includes(host.client_address || "") ? host.client_address! : addresses[0] || "Нет адреса";
    return { id: `relay-${host.id}`, kind: "relay", title: host.name, address,
      subtitle: `Relay${addresses.length > 1 ? ` · адресов: ${addresses.length}` : ""}`,
      search: [host.name, host.agent_url, ...addresses].join(" ").toLowerCase(),
      summary: relayRouteSummary(host, relayFailed, pending, reading), host };
  });
  const domains = new Map<string, NetworkStatus["domains"][number]>();
  for (const domain of status?.domains || []) {
    const key = domain.value.toLowerCase(), previous = domains.get(key);
    if (!previous) domains.set(key, domain);
    else domains.set(key, { ...previous, role: [...new Set([previous.role, domain.role])].join(" · "), resolved: [...new Set([...previous.resolved, ...domain.resolved])], status: previous.status === "stale" && domain.status === "stale" ? "stale" : undefined });
  }
  for (const domain of domains.values()) {
    const checks = [...new Map([...Object.values(status?.transport_endpoint_checks || {}), ...Object.values(status?.transport_endpoint_checks_by_domain || {})]
      .filter(check => check.domain.toLowerCase() === domain.value.toLowerCase()).map(check => [check.kind, check])).values()];
    const summary = domainRouteSummary(domain, checks, failed, [status?.server.public_ipv4 || status?.server.public_ip || "", status?.server.public_ipv6 || ""]);
    entries.push({ id: `domain-${domain.value.toLowerCase()}`, kind: "domain", title: domain.value, address: domain.value, subtitle: summary.type, search: [domain.value, domain.role, ...domain.resolved].join(" ").toLowerCase(), summary, domain, checks });
  }
  return entries;
}

export function filterEntries(entries: RouteEntry[], query: string, filter: RouteFilter) {
  return entries.filter(entry => entry.search.includes(query.trim().toLowerCase()) && (filter === "all" || filter === "attention" && needsAttention(entry.summary) || entry.kind === filter));
}
