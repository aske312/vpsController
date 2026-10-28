import { clientExport, type ClientExport } from "../../shared/lib/client-export";

export type RelayMeasurement = { state: "ready" | "failed" | "warning" | "unknown"; reason: string; scope?: "diagnostic_udp_path"; sent?: number; received?: number; loss_percent?: number; client_to_relay_lost?: number; relay_to_vps_lost?: number; vps_to_relay_lost?: number; relay_to_client_lost?: number; agent_local_drops?: number; origin_local_drops?: number; agent_socket_errors?: number; rtt_ms?: number | null; relay_vps_rtt_ms?: number | null; packet_size?: number };
export type RelayDeviceTest = { id: string; state: "preparing" | "awaiting_client" | "processing" | "ready" | "warning" | "failed" | "unknown" | "expired" | "cancelled"; reason: string; created_at: number; expires_at: number; finished_at?: number; consumer_id: string; cleanup_state?: "cleaned" | "failed" | "pending"; media?: { state: "ready" | "failed" | "unknown"; reason: string; verified_bytes?: number | null; seconds?: number | null }; measurement?: RelayMeasurement; vantage_point: "client"; verification: "client_report_with_udp_witness" };

export type RelayRoute = {
  cdn_domain?: string;
  id: string; host_id: string; transport: "tcp" | "udp"; listen_port: number; target_ip: string; target_port: number;
  state: "listening" | "failed" | "missing" | "drift" | "unknown"; target_verified: false;
  sent_bytes?: number; received_bytes?: number; sessions?: number; errors?: number;
  diagnostics?: { sent_packets: number; received_packets: number; dropped_packets: number; last_sent_at: number | null; last_received_at: number | null; last_error_at: number | null; last_error: string | null; error_counts: Record<string, number>; return_state?: "no_return" | "waiting" | "flowing" | "idle"; no_return_sessions?: number };
  check?: { state: "ready" | "failed" | "unknown"; consumer_id: string; checked_at: string; vantage_point: "server"; mode?: "exchange" | "media"; reason?: string; verified_bytes?: number; range_checks?: number; seconds?: number; measurement?: RelayMeasurement; cleanup_state?: "cleaned" | "failed" | "pending" };
  device_test?: RelayDeviceTest;
};
export type RelayHost = {
  id: string; name: string; agent_url: string; client_address?: string; certificate_sha256: string; api_version: 1;
  token_configured: boolean; state: "connected" | "unknown"; version?: string; agent_revision?: number;
  foreign_routes: number; routes: RelayRoute[]; error?: string;
  profile_failures?: number;
  capabilities?: string[];
  agent_release?: { state: "unknown" | "current" | "outdated" | "newer"; version?: string; digest?: string };
  agent_update?: { state: "sending" | "queued" | "running" | "updated" | "failed" | "unknown"; id?: string; version?: string; requested_at?: number; polling?: boolean };
  updates?: { enabled: boolean; interval_hours: 24; state: "unchecked" | "current" | "updated" | "disabled" | "failed" | "unknown"; checked_at?: string; version?: string };
  address_checks?: { domain: string; state: "verified" | "dns_only" | "unresolved" | "mismatch" }[];
  excluded_addresses?: string[];
  addresses?: { ipv4: string[]; ipv6: string[]; domains: string[]; cdn_domains?: string[] };
};
export type RelayProfile = { relay_failures?: { host_id: string; reason: "recovery_required" | "preparation_failed" }[]; id: string; name: string; channels: { id: string; label: string; transport: "tcp" | "udp"; host_id: string; route_ids: string[] }[] };
export type RelayStatus = {
  profiles?: RelayProfile[];
  revision: string; hosts: RelayHost[]; pending: boolean; origin_ip: string; origin_hosts: string[];
  targets: { transport: "tcp" | "udp"; port: number }[];
  consumers?: { id: string; label: string; host_id?: string }[];
  recovery?: { operation_id: string; host_id: string; action: "put" | "delete"; transport: "tcp" | "udp"; listen_port: number; target_ip: string; target_port: number; error: string };
};
export type RelayClientResult = ClientExport;
const object = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === "object" && !Array.isArray(value));
const integer = (value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= min && value <= max;
const finite = (value: unknown, max: number) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= max;
function measurementValid(value: unknown) {
  if (!object(value) || !["ready", "failed", "warning", "unknown"].includes(String(value.state)) || typeof value.reason !== "string") return false;
  if (value.scope !== undefined && value.scope !== "diagnostic_udp_path") return false;
  for (const key of ["sent", "received", "client_to_relay_lost", "relay_to_vps_lost", "vps_to_relay_lost", "relay_to_client_lost", "origin_local_drops"]) if (value[key] !== undefined && !integer(value[key], 0, 100)) return false;
  for (const key of ["rtt_ms", "relay_vps_rtt_ms"]) if (value[key] !== undefined && value[key] !== null && !finite(value[key], 120000)) return false;
  return value.loss_percent === undefined || finite(value.loss_percent, 100);
}
export function relayApiOrigin(value: unknown): value is string {
  if (typeof value !== "string" || !/^https:\/\/(?:[\d.]+|\[[a-fA-F0-9:]+\]):\d+\/?$/.test(value)) return false;
  try { const url = new URL(value); const port = Number(url.port || 443); return port >= 1024 && port <= 65535 && !(port >= 20000 && port <= 20999); }
  catch { return false; }
}
export function relayAddresses(host: RelayHost): string[] {
  return [...new Set([host.client_address || new URL(host.agent_url).hostname.replace(/^\[|\]$/g, ""), ...(host.addresses?.ipv4 || []), ...(host.addresses?.ipv6 || []), ...(host.addresses?.domains || [])])].filter(address => !host.excluded_addresses?.includes(address));
}
export function relayClientResult(value: unknown): RelayClientResult {
  try { return clientExport(value) as RelayClientResult; }
  catch { throw new Error("Некорректный результат подключения через Relay"); }
}
export function relaySnapshot(value: unknown): RelayStatus {
  const invalid = () => { throw new Error("Не удалось подтвердить сведения о relay"); };
  if (!object(value) || typeof value.revision !== "string" || !/^[0-9a-f]{32}$/.test(value.revision) || !Array.isArray(value.hosts)
    || value.hosts.length > 8 || typeof value.pending !== "boolean" || typeof value.origin_ip !== "string" || !Array.isArray(value.origin_hosts) || value.origin_hosts.some(host => typeof host !== "string")
    || !Array.isArray(value.targets) || value.targets.some(target => !object(target) || !["tcp", "udp"].includes(String(target.transport)) || !integer(target.port, 1, 65535))) return invalid();
  const ids = new Set<string>();
  if (value.recovery !== undefined) {
    const recovery = value.recovery;
    if (!value.pending || !object(recovery) || typeof recovery.operation_id !== "string" || !/^[0-9a-f]{32}$/.test(recovery.operation_id)
      || typeof recovery.host_id !== "string" || !/^[0-9a-f]{32}$/.test(recovery.host_id) || !["put", "delete"].includes(String(recovery.action))
      || !["tcp", "udp"].includes(String(recovery.transport)) || !integer(recovery.listen_port, 20000, 20999)
      || typeof recovery.target_ip !== "string" || !integer(recovery.target_port, 1, 65535)
      || typeof recovery.error !== "string" || !/^(response_unknown|agent_http_\d{3})$/.test(recovery.error)) return invalid();
  }
  for (const host of value.hosts) {
    if (!object(host) || typeof host.id !== "string" || !/^[0-9a-f]{32}$/.test(host.id) || ids.has(host.id) || typeof host.name !== "string"
      || !relayApiOrigin(host.agent_url) || typeof host.certificate_sha256 !== "string" || !/^[0-9a-fA-F]{64}$/.test(host.certificate_sha256)
      || host.client_address !== undefined && (typeof host.client_address !== "string" || !host.client_address || host.client_address.length > 253)
      || host.api_version !== 1 || host.token_configured !== true || "token" in host || !["connected", "unknown"].includes(String(host.state))
      || !integer(host.foreign_routes) || !Array.isArray(host.routes) || host.error !== undefined && typeof host.error !== "string"
      || host.state === "connected" && (!integer(host.agent_revision) || typeof host.version !== "string")) return invalid();
    if (host.profile_failures !== undefined && !integer(host.profile_failures)) return invalid();
    ids.add(host.id);
    if (host.agent_release !== undefined && (!object(host.agent_release) || !["unknown", "current", "outdated", "newer"].includes(String(host.agent_release.state)) || host.agent_release.state !== "unknown" && (typeof host.agent_release.version !== "string" || !/^\d{1,6}\.\d{1,6}\.\d{1,6}$/.test(host.agent_release.version) || typeof host.agent_release.digest !== "string" || !/^sha256:[0-9a-f]{64}$/.test(host.agent_release.digest)))) return invalid();
    if (host.agent_update !== undefined && (!object(host.agent_update) || !["sending", "queued", "running", "updated", "failed", "unknown"].includes(String(host.agent_update.state)))) return invalid();
    if (host.capabilities !== undefined && (!Array.isArray(host.capabilities) || host.capabilities.length > 16 || host.capabilities.some(item => typeof item !== "string" || !/^[a-z0-9-]{1,64}$/.test(item)))) return invalid();
    if (host.excluded_addresses !== undefined && (!Array.isArray(host.excluded_addresses) || host.excluded_addresses.some(address => typeof address !== "string" || !address || address.length > 253))) return invalid();
    if (host.updates !== undefined && (!object(host.updates) || typeof host.updates.enabled !== "boolean" || host.updates.interval_hours !== 24 || !["unchecked", "current", "updated", "disabled", "failed", "unknown"].includes(String(host.updates.state)))) return invalid();
    if (host.address_checks !== undefined && (!Array.isArray(host.address_checks) || host.address_checks.length > 64 || host.address_checks.some(item => !object(item) || typeof item.domain !== "string" || !["verified", "dns_only", "unresolved", "mismatch"].includes(String(item.state))))) return invalid();
    if (host.addresses !== undefined) {
      const addresses = host.addresses;
      if (!object(addresses) || ["ipv4", "ipv6", "domains", ...(addresses.cdn_domains === undefined ? [] : ["cdn_domains"])].some(key => {
        const items = addresses[key];
        return !Array.isArray(items) || items.length > 64 || items.some((address: unknown) => typeof address !== "string" || !address || address.length > 253 || /[\s/@?#]/.test(address));
      })) return invalid();
      if (Array.isArray(addresses.cdn_domains) && addresses.cdn_domains.some(address => !(addresses.domains as string[]).includes(address))) return invalid();
    }
    const routeIds = new Set<string>();
    for (const route of host.routes) {
      if (!object(route) || typeof route.id !== "string" || !/^pro-[0-9a-f]{32}$/.test(route.id) || routeIds.has(route.id) || route.host_id !== host.id
        || !["tcp", "udp"].includes(String(route.transport)) || !integer(route.listen_port, 20000, 20999) || !integer(route.target_port, 1, 65535) || typeof route.target_ip !== "string"
        || !["listening", "failed", "missing", "drift", "unknown"].includes(String(route.state)) || route.target_verified !== false
        || host.state === "unknown" && route.state !== "unknown") return invalid();
      for (const key of ["sent_bytes", "received_bytes", "sessions", "errors"]) if (route[key] !== undefined && !integer(route[key])) return invalid();
      if (route.diagnostics !== undefined) {
        const data = route.diagnostics;
        if (!object(data) || ["sent_packets", "received_packets", "dropped_packets"].some(key => !integer(data[key])) || ["last_sent_at", "last_received_at", "last_error_at"].some(key => data[key] !== null && !integer(data[key], 0, 253402300799)) || data.last_error !== null && typeof data.last_error !== "string" || !object(data.error_counts) || Object.values(data.error_counts).some(item => !integer(item))) return invalid();
        if (data.return_state !== undefined && (!["no_return", "waiting", "flowing", "idle"].includes(String(data.return_state)) || !integer(data.no_return_sessions, 0, 128))) return invalid();
      }
      if (object(route.check) && route.check.mode !== undefined && !["exchange", "media"].includes(String(route.check.mode))) return invalid();
      if (route.check !== undefined && (!object(route.check) || !["ready", "failed", "unknown"].includes(String(route.check.state)) || typeof route.check.consumer_id !== "string" || typeof route.check.checked_at !== "string" || route.check.vantage_point !== "server")) return invalid();
      if (object(route.check) && route.check.measurement !== undefined && !measurementValid(route.check.measurement)) return invalid();
      if (route.device_test !== undefined) {
        const data = route.device_test;
        if (!object(data) || typeof data.id !== "string" || !/^[0-9a-f]{32}$/.test(data.id) || !["preparing", "awaiting_client", "processing", "ready", "warning", "failed", "unknown", "expired", "cancelled"].includes(String(data.state)) || !finite(data.created_at, 253402300799) || !finite(data.expires_at, 253402300799) || typeof data.consumer_id !== "string" || data.vantage_point !== "client" || data.verification !== "client_report_with_udp_witness") return invalid();
        if (data.measurement !== undefined && !measurementValid(data.measurement)) return invalid();
        if (data.media !== undefined && (!object(data.media) || !["ready", "failed", "unknown"].includes(String(data.media.state)))) return invalid();
      }
      routeIds.add(route.id);
    }
  }
  if (value.consumers !== undefined && (!Array.isArray(value.consumers) || value.consumers.some(item => !object(item) || typeof item.id !== "string" || typeof item.label !== "string"))) return invalid();
  if (value.profiles !== undefined && (!Array.isArray(value.profiles) || value.profiles.some(profile => !object(profile)
    || typeof profile.id !== "string" || typeof profile.name !== "string" || !Array.isArray(profile.channels)
    || profile.relay_failures !== undefined && (!Array.isArray(profile.relay_failures) || profile.relay_failures.some(error => !object(error) || typeof error.host_id !== "string" || !["recovery_required", "preparation_failed"].includes(String(error.reason))))
    || profile.channels.some(channel => !object(channel) || typeof channel.id !== "string" || typeof channel.label !== "string"
      || !["tcp", "udp"].includes(String(channel.transport)) || typeof channel.host_id !== "string"
      || !Array.isArray(channel.route_ids) || channel.route_ids.some(id => typeof id !== "string"))))) return invalid();
  return value as RelayStatus;
}

export function relayConnection(text: string) {
  const value: unknown = JSON.parse(text);
  if (!object(value) || value.api_version !== 1 || !relayApiOrigin(value.agent_url)
    || typeof value.token !== "string" || !/^[A-Za-z0-9_-]{40,128}$/.test(value.token) || typeof value.certificate_sha256 !== "string" || !/^[a-fA-F0-9]{64}$/.test(value.certificate_sha256)) throw new Error("Нужен JSON подключения Relay Agent API v1");
  return { agent_url: value.agent_url, token: value.token, certificate_sha256: value.certificate_sha256.toLowerCase(), api_version: 1 as const };
}
