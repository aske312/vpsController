import type { Client, LiveStatus, Overview, ResourceMetrics } from "../shared/types/control-plane";

type Row = Record<string, unknown>;
const record = (value: unknown): value is Row => Boolean(value && typeof value === "object" && !Array.isArray(value));
const metric = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0;
const strings = (value: unknown) => Array.isArray(value) && value.every((item) => typeof item === "string");
const protocols = ["wg", "awg", "shadowsocks", "vless-reality-xhttp", "hysteria2", "tuic", "trojan", "openvpn", "ikev2"];
const resourceGroups = {
  cpu: ["cpu_percent", "cpu_count"], load: ["load1", "load5", "load15"],
  memory: ["memory_total", "memory_available"], disk: ["disk_total", "disk_available"],
  network: ["network_rx", "network_tx"], uptime: ["uptime_s"],
};

export function resourceSnapshot(value: unknown): ResourceMetrics {
  const fail = () => { throw new Error("Некорректные данные телеметрии VPS"); };
  if (!record(value)) return fail();
  const required = ["load1", "cpu_percent", "cpu_count", "memory_total", "memory_available", "disk_total", "disk_available", "network_rx", "network_tx"];
  if (required.some((key) => value[key] !== null && !metric(value[key]))
    || ["load5", "load15", "uptime_s"].some((key) => value[key] != null && !metric(value[key]))
    || (value.stale !== undefined && typeof value.stale !== "boolean")) return fail();
  if (value.observations !== undefined && (!record(value.observations) || Object.values(value.observations).some((row) => !record(row)
    || typeof row.available !== "boolean" || typeof row.checked_at !== "string" || typeof row.reason !== "string"))) return fail();
  const next = { ...value };
  for (const [group, keys] of Object.entries(resourceGroups)) {
    const observations = value.observations as Record<string, { available: boolean }> | undefined;
    if (value.stale === true || observations?.[group]?.available === false) for (const key of keys) next[key] = null;
  }
  return next as ResourceMetrics;
}

export function clientSnapshot(value: unknown): Client[] {
  if (!Array.isArray(value)) throw new Error("Некорректные данные подключений");
  const identities = new Set<string>();
  return value.map((row) => {
    if (!record(row) || !["id", "name", "public_key", "address"].every((key) => typeof row[key] === "string")
      || !row.id || !protocols.includes(String(row.protocol)) || !metric(row.rx_bytes) || !metric(row.tx_bytes)
      || ["handshake_age_s", "rx_bps", "tx_bps", "active_connections", "latency_ms", "jitter_ms", "packet_loss_percent", "sample_size"].some((key) => row[key] != null && !metric(row[key]))
      || ["endpoint", "quality_reason"].some((key) => row[key] != null && typeof row[key] !== "string")
      || (row.quality !== undefined && !["stable", "warning", "error", "offline"].includes(String(row.quality)))
      || (row.channel_mode !== undefined && !["direct", "tls_relay", "udp_relay"].includes(String(row.channel_mode)))
      || (row.latency_source !== undefined && !["server_icmp_tunnel_ip", "not_supported"].includes(String(row.latency_source)))
      || (row.active_sources !== undefined && !strings(row.active_sources))) throw new Error("Некорректное состояние подключения");
    if (row.route_state !== undefined && (!record(row.route_state) || !["current", "stale", "unknown"].includes(String(row.route_state.state))
      || typeof row.route_state.reason !== "string" || !strings(row.route_state.addresses) || !strings(row.route_state.bindings))) throw new Error("Некорректное состояние маршрута подключения");
    const identity = `${row.protocol}:${row.id}`;
    if (identities.has(identity)) throw new Error("Повторяющийся идентификатор подключения");
    identities.add(identity);
    return { ...row, quality: row.quality ?? "unknown", observation_failed: false } as Client;
  });
}

/** Keep identities and cumulative counters, without publishing previous activity as current. */
export function clientsUnavailable(previous: Client[]): Client[] {
  return previous.map((client) => ({ ...client, observation_failed: true, quality: "unknown", quality_reason: "Состояние подключения не подтверждено",
    handshake_age_s: undefined, active_connections: undefined, active_sources: undefined, rx_bps: undefined, tx_bps: undefined,
    latency_ms: undefined, jitter_ms: undefined, packet_loss_percent: undefined, latency_source: undefined, sample_size: undefined,
    route_state: client.route_state ? { ...client.route_state, state: "unknown", reason: "Состояние маршрута не подтверждено" } : undefined }));
}

export function liveStatusSnapshot(value: unknown): LiveStatus {
  if (!record(value) || !record(value.protocols) || (value.unavailable !== undefined && !strings(value.unavailable))) throw new Error("Некорректные данные live-телеметрии");
  const unavailable = new Set(value.unavailable as string[] | undefined);
  const read = <T>(name: string, accept: () => T): T | null => {
    if (unavailable.has(name)) return null;
    try { return accept(); } catch { unavailable.add(name); return null; }
  };
  const protocolRows = value.protocols;
  const tunnel = (name: "wg" | "awg") => read(name, () => {
    const row = protocolRows[name];
    if (!record(row) || typeof row.active !== "boolean"
      || !["peers", "online_peers", "interface_rx_bytes", "interface_tx_bytes"].every((key) => metric(row[key]))
      || Number(row.online_peers) > Number(row.peers)) throw new Error("Некорректные счётчики протокола");
    return row as NonNullable<LiveStatus["protocols"]["wg"]>;
  });
  return { resources: resourceSnapshot(value.resources), protocols: { wg: tunnel("wg"), awg: tunnel("awg") },
    clients: read("clients", () => clientSnapshot(value.clients)),
    security: read("security", () => {
      const row = value.security;
      if (!record(row) || !["firewall_active", "fail2ban_active", "ssh_listening"].every((key) => typeof row[key] === "boolean")) throw new Error("Некорректные данные защиты");
      return row as NonNullable<LiveStatus["security"]>;
    }), unavailable: [...unavailable] };
}

export function overviewSnapshot(value: unknown): Overview {
  if (!record(value) || !record(value.server) || !record(value.protocols)
    || !["name", "public_ip", "city", "country", "country_code"].every((key) => typeof (value.server as Row)[key] === "string")
    || ["public_ipv4", "public_ipv6", "public_domain", "public_endpoint"].some((key) => (value.server as Row)[key] != null && typeof (value.server as Row)[key] !== "string")
    || (value.server.uptime_s !== null && !metric(value.server.uptime_s))) throw new Error("Некорректные данные обзора VPS");
  for (const key of ["wg", "awg"]) {
    const row = value.protocols[key];
    if (!record(row) || typeof row.interface !== "string" || typeof row.active !== "boolean" || !metric(row.port) || Number(row.port) > 65535) throw new Error("Некорректные данные протоколов обзора");
  }
  return { ...value, resources: resourceSnapshot(value.resources) } as Overview;
}

export function overviewUnavailable(previous: Overview | null): Overview | null {
  return previous ? { ...previous, server: { ...previous.server, uptime_s: null }, resources: resourceSnapshot({ ...previous.resources, stale: true }), protocols: {
    wg: { ...previous.protocols.wg, active: null }, awg: { ...previous.protocols.awg, active: null },
  } } : null;
}

export function securityLiveObservation(previous: Row | null, value: LiveStatus["security"]): Row | null {
  if (!previous) return null;
  return { ...previous, live_observation_failed: value === null,
    firewall: { ...(record(previous.firewall) ? previous.firewall : {}), active: value?.firewall_active ?? null },
    fail2ban: { ...(record(previous.fail2ban) ? previous.fail2ban : {}), active: value?.fail2ban_active ?? null },
    ssh: { ...(record(previous.ssh) ? previous.ssh : {}), active: value?.ssh_listening ?? null } };
}
