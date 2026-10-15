import type { LiveStatus, Protocol, ProtocolStatus } from "../shared/types/control-plane";

type Row = Record<string, unknown>;
const record = (value: unknown): value is Row => Boolean(value && typeof value === "object" && !Array.isArray(value));
const finite = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0;
const strings = (value: unknown) => Array.isArray(value) && value.every((item) => typeof item === "string");
const fields = (row: Row, keys: string[], type: "string" | "boolean") => keys.every((key) => typeof row[key] === type);
const optionalStrings = (row: Row, keys: string[]) => keys.every((key) => row[key] == null || typeof row[key] === "string");
const rows = (value: unknown, accept: (row: Row) => boolean) => Array.isArray(value) && value.every((row) => record(row) && accept(row));
const scalar = (value: unknown) => typeof value === "string" || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value));

/** Validate observations before preserving the existing optional monitoring defaults. */
export function directProtocolSnapshot(value: unknown, protocol: Protocol): ProtocolStatus {
  const fail = () => { throw new Error("Некорректные данные состояния протокола"); };
  if (!record(value) || value.protocol !== protocol || !fields(value, ["interface", "active_since", "address"], "string")
    || !fields(value, ["active", "service_active", "service_enabled"], "boolean")
    || !["listen_port", "mtu", "peers", "online_peers", "endpoints", "peer_rx_bytes", "peer_tx_bytes", "interface_rx_bytes", "interface_tx_bytes", "rx_errors", "tx_errors", "rx_dropped", "tx_dropped"].every((key) => finite(value[key]))
    || Number(value.listen_port) > 65535 || !optionalStrings(value, ["unit", "transport", "security", "target"])
    || (value.last_handshake_age_s != null && !finite(value.last_handshake_age_s))) return fail();
  if (value.settings !== undefined && (!record(value.settings) || !Object.values(value.settings).every(scalar))) return fail();
  if (value.editable_settings !== undefined && !rows(value.editable_settings, (row) => fields(row, ["key", "label"], "string")
    && ["number", "select", "boolean", "text"].includes(String(row.type)) && scalar(row.value) && optionalStrings(row, ["help"])
    && ["min", "max"].every((key) => row[key] === undefined || (typeof row[key] === "number" && Number.isFinite(row[key])))
    && (row.options === undefined || rows(row.options, (item) => fields(item, ["value", "label"], "string"))))) return fail();
  if (value.routes !== undefined) {
    if (!record(value.routes)) return fail();
    for (const key of ["direct", "tls", "cdn"]) {
      const route = value.routes[key];
      if (route === undefined) continue;
      if (!record(route) || typeof route.enabled !== "boolean" || !optionalStrings(route, ["security", "transport", "endpoint", "server_name", "path", "xhttp_mode"])
        || ["confirmed_domains", "available_domains"].some((field) => route[field] !== undefined && !strings(route[field]))) return fail();
    }
  }
  if (value.health !== undefined && (!record(value.health) || !["READY", "WORKS", "WARN", "ERROR"].includes(String(value.health.state))
    || !["READY", "WARN", "ERROR"].includes(String(value.health.base_state)) || !fields(value.health, ["reason"], "string")
    || !fields(value.health, ["checking", "traffic_now"], "boolean") || !finite(value.health.clients) || !optionalStrings(value.health, ["checked_at"]))) return fail();
  if (value.resources !== undefined && (!record(value.resources) || !optionalStrings(value.resources, ["checked_at"])
    || (value.resources.items !== undefined && !rows(value.resources.items, (row) => fields(row, ["name"], "string") && fields(row, ["available"], "boolean")
      && finite(row.latency_ms) && (row.status_code == null || finite(row.status_code)))))) return fail();
  if (value.history !== undefined) {
    if (!record(value.history)) return fail();
    for (const [key, item] of Object.entries(value.history)) {
      if (key === "events") {
        if (!rows(item, (row) => ["monitor_gap", "service_down", "peers_offline"].includes(String(row.type)) && optionalStrings(row, ["at"]) && (row.seconds == null || finite(row.seconds)))) return fail();
      } else if (item != null && !finite(item)) return fail();
    }
  }
  if (value.diagnostics !== undefined) {
    const diagnostics = value.diagnostics;
    if (!record(diagnostics) || !optionalStrings(diagnostics, ["checked_at"]) || (diagnostics.status !== undefined && !["healthy", "warning", "critical", "pending"].includes(String(diagnostics.status)))
      || (diagnostics.score != null && !finite(diagnostics.score))) return fail();
    for (const key of ["live", "network"]) {
      const observation = diagnostics[key];
      if (observation === undefined) continue;
      if (!record(observation) || Object.entries(observation).some(([field, item]) => item != null && (key === "network" && ["uplink", "gateway"].includes(field) ? typeof item !== "string" : !finite(item)))) return fail();
    }
    if ((diagnostics.checks !== undefined && !rows(diagnostics.checks, (row) => fields(row, ["id", "name", "value"], "string") && fields(row, ["ok"], "boolean")
      && (row.severity === undefined || ["critical", "warning"].includes(String(row.severity)))))
      || (diagnostics.findings !== undefined && !rows(diagnostics.findings, (row) => ["critical", "warning"].includes(String(row.severity)) && fields(row, ["code", "title", "detail", "action"], "string")))) return fail();
  }
  const raw = value as Omit<ProtocolStatus, "resources" | "history" | "diagnostics"> & {
    resources?: Partial<ProtocolStatus["resources"]>; history?: Partial<ProtocolStatus["history"]>; diagnostics?: Partial<ProtocolStatus["diagnostics"]>;
  };
  return {
    ...raw,
    observation_failed: false,
    resources: { checked_at: raw.resources?.checked_at, items: raw.resources?.items || [] },
    history: {
      period_hours: 24, samples: 0, availability_percent: raw.service_active ? 100 : 0,
      monitoring_gaps: 0, service_interruptions: 0, inactive_connection_periods: 0,
      external_loss_percent: undefined, latency_avg_ms: undefined, latency_max_ms: undefined, jitter_avg_ms: undefined,
      interface_errors: 0, interface_dropped: 0, uplink_errors: 0, uplink_dropped: 0, conntrack_peak_percent: undefined,
      received_bytes: raw.peer_rx_bytes || raw.interface_rx_bytes || 0, transmitted_bytes: raw.peer_tx_bytes || raw.interface_tx_bytes || 0,
      average_rx_bps: 0, average_tx_bps: 0, peak_rx_bps: 0, peak_tx_bps: 0, ...(raw.history || {}), events: raw.history?.events || [],
    },
    diagnostics: { checked_at: raw.diagnostics?.checked_at, status: raw.diagnostics?.status || "pending", score: raw.diagnostics?.score,
      live: raw.diagnostics?.live, network: raw.diagnostics?.network, checks: raw.diagnostics?.checks || [], findings: raw.diagnostics?.findings || [] },
  };
}

export function directProtocolUnavailable(previous: ProtocolStatus | undefined): ProtocolStatus | undefined {
  if (!previous) return undefined;
  return { ...previous, observation_failed: true, routes: undefined, health: { state: "WARN", base_state: "WARN",
    reason: "Состояние протокола не подтверждено; показаны последние полученные данные", checking: false, clients: previous.peers, traffic_now: false } };
}

/** The cheap interface sample cannot confirm service/settings/routes after a failed full read. */
export function tunnelLiveObservation(previous: ProtocolStatus | undefined, value: LiveStatus["protocols"]["wg"]): ProtocolStatus | undefined {
  if (!previous || !value) return directProtocolUnavailable(previous);
  const next = { ...previous, ...value };
  if (previous.observation_failed || previous.active !== value.active) return directProtocolUnavailable(next);
  return { ...next, health: previous.health ? { ...previous.health, state: previous.health.base_state, clients: value.peers, traffic_now: false } : undefined };
}
