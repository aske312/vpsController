export type RelayRoute = {
  id: string; host_id: string; transport: "tcp" | "udp"; listen_port: number; target_ip: string; target_port: number;
  state: "listening" | "failed" | "missing" | "drift" | "unknown"; target_verified: false;
  sent_bytes?: number; received_bytes?: number; sessions?: number; errors?: number;
  check?: { state: "ready" | "failed" | "unknown"; consumer_id: string; checked_at: string; vantage_point: "server" };
};
export type RelayHost = {
  id: string; name: string; agent_url: string; certificate_sha256: string; api_version: 1;
  token_configured: boolean; state: "connected" | "unknown"; version?: string; agent_revision?: number;
  foreign_routes: number; routes: RelayRoute[]; error?: string;
};
export type RelayStatus = {
  revision: string; hosts: RelayHost[]; pending: boolean; origin_ip: string; origin_hosts: string[];
  targets: { transport: "tcp" | "udp"; port: number }[];
  consumers?: { id: string; label: string }[];
};
const object = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === "object" && !Array.isArray(value));
const integer = (value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= min && value <= max;
export function relaySnapshot(value: unknown): RelayStatus {
  const invalid = () => { throw new Error("Не удалось подтвердить сведения о relay"); };
  if (!object(value) || typeof value.revision !== "string" || !/^[0-9a-f]{32}$/.test(value.revision) || !Array.isArray(value.hosts)
    || value.hosts.length > 8 || typeof value.pending !== "boolean" || typeof value.origin_ip !== "string" || !Array.isArray(value.origin_hosts) || value.origin_hosts.some(host => typeof host !== "string")
    || !Array.isArray(value.targets) || value.targets.some(target => !object(target) || !["tcp", "udp"].includes(String(target.transport)) || !integer(target.port, 1, 65535))) return invalid();
  const ids = new Set<string>();
  for (const host of value.hosts) {
    if (!object(host) || typeof host.id !== "string" || !/^[0-9a-f]{32}$/.test(host.id) || ids.has(host.id) || typeof host.name !== "string"
      || typeof host.agent_url !== "string" || !/^https:\/\/[\d.]+:9443\/?$/.test(host.agent_url) || typeof host.certificate_sha256 !== "string" || !/^[0-9a-fA-F]{64}$/.test(host.certificate_sha256)
      || host.api_version !== 1 || host.token_configured !== true || "token" in host || !["connected", "unknown"].includes(String(host.state))
      || !integer(host.foreign_routes) || !Array.isArray(host.routes) || host.error !== undefined && typeof host.error !== "string"
      || host.state === "connected" && (!integer(host.agent_revision) || typeof host.version !== "string")) return invalid();
    ids.add(host.id);
    const routeIds = new Set<string>();
    for (const route of host.routes) {
      if (!object(route) || typeof route.id !== "string" || !/^pro-[0-9a-f]{32}$/.test(route.id) || routeIds.has(route.id) || route.host_id !== host.id
        || !["tcp", "udp"].includes(String(route.transport)) || !integer(route.listen_port, 20000, 20999) || !integer(route.target_port, 1, 65535) || typeof route.target_ip !== "string"
        || !["listening", "failed", "missing", "drift", "unknown"].includes(String(route.state)) || route.target_verified !== false
        || host.state === "unknown" && route.state !== "unknown") return invalid();
      for (const key of ["sent_bytes", "received_bytes", "sessions", "errors"]) if (route[key] !== undefined && !integer(route[key])) return invalid();
      if (route.check !== undefined && (!object(route.check) || !["ready", "failed", "unknown"].includes(String(route.check.state)) || typeof route.check.consumer_id !== "string" || typeof route.check.checked_at !== "string" || route.check.vantage_point !== "server")) return invalid();
      routeIds.add(route.id);
    }
  }
  if (value.consumers !== undefined && (!Array.isArray(value.consumers) || value.consumers.some(item => !object(item) || typeof item.id !== "string" || typeof item.label !== "string"))) return invalid();
  return value as RelayStatus;
}

export function relayConnection(text: string) {
  const value: unknown = JSON.parse(text);
  if (!object(value) || value.api_version !== 1 || typeof value.agent_url !== "string" || !/^https:\/\/[\d.]+:9443\/?$/.test(value.agent_url)
    || typeof value.token !== "string" || !/^[A-Za-z0-9_-]{40,128}$/.test(value.token) || typeof value.certificate_sha256 !== "string" || !/^[a-fA-F0-9]{64}$/.test(value.certificate_sha256)) throw new Error("Нужен JSON подключения Relay Agent API v1");
  return { agent_url: value.agent_url, token: value.token, certificate_sha256: value.certificate_sha256.toLowerCase(), api_version: 1 as const };
}
