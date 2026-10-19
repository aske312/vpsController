import type { NetworkEndpointCheck, NetworkStatus } from "../types/control-plane";

export function endpointCheckReady(check: NetworkEndpointCheck | undefined): boolean {
  return Boolean(check && check.status === "ready" && check.ready === true
    && check.verification === "end_to_end" && check.binding_id && check.check_id && check.checked_at
    && Number.isInteger(check.desired_revision) && Number(check.desired_revision) > 0
    && Number.isInteger(check.checked_revision) && check.checked_revision === check.desired_revision);
}

export function configuredCdnDomains(status: NetworkStatus | null | undefined): string[] {
  const domains = Array.isArray(status?.domains) ? status.domains : [];
  return [...new Set([
    status?.transport_endpoints?.cdn_domain || "",
    ...(Array.isArray(status?.transport_endpoints?.cdn_domains) ? status.transport_endpoints.cdn_domains : []),
    ...domains.filter(domain => domain && (domain.endpoint_kind === "cdn" || typeof domain.role === "string" && /\bcdn\b/i.test(domain.role))).map(domain => domain.value),
  ].filter((domain): domain is string => typeof domain === "string").map(domain => domain.trim().toLowerCase()).filter(Boolean))];
}

export function endpointSelectable(check: NetworkEndpointCheck | undefined): boolean {
  return Boolean(check?.selectable === true && check.binding_id && check.desired_revision
    && check.resolved.length);
}

export function endpointCheckForConsumer(check: NetworkEndpointCheck | undefined, consumerId: string): NetworkEndpointCheck | undefined {
  if (!check || !consumerId || check.status === "unresolved" || check.status === "stale" && !check.binding_id
    || (check.consumer_id || check.verified_connection?.consumer_id) === consumerId) return check;
  return { ...check, status: "unchecked", ready: false, verification: "dns_only",
    check_id: null, checked_at: null, checked_revision: null, failed_checks: 0,
    verified_connection: undefined, message: "Выбранное подключение ещё не проверено" };
}

export function confirmedRouteAddresses(status: NetworkStatus | null | undefined, kind: "cdn" | "tls" | "udp", provision = false): string[] {
  const routeKind = { cdn: "cdn", tls: "tls_relay", udp: "udp_relay" }[kind] as NetworkEndpointCheck["kind"];
  const checks = Object.values(status?.transport_endpoint_checks_by_domain || {});
  const primary = status?.transport_endpoint_checks?.[routeKind];
  if (primary) checks.push(primary);
  return [...new Set(checks.filter((check) => check.kind === routeKind && (provision ? endpointSelectable(check) : endpointCheckReady(check)))
    .map((check) => check.domain.trim().toLowerCase()).filter(Boolean))];
}

export function directRouteAddresses(status: NetworkStatus | null | undefined, kind: "tls" | "udp"): string[] {
  const routeKind = kind === "tls" ? "tls_relay" : "udp_relay";
  const checks = [...Object.values(status?.transport_endpoint_checks_by_domain || {}), status?.transport_endpoint_checks?.[routeKind]];
  return [...new Set(checks.filter((check): check is NetworkEndpointCheck => Boolean(check && check.kind === routeKind && check.matches_origin && endpointSelectable(check))).map(check => check.domain.trim().toLowerCase()))];
}
