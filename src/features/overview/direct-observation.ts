import type { Client, ProtocolStatus } from "../../shared/types/control-plane";
const knownMetric = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;

export function directChannelObservation(status: ProtocolStatus | undefined, clients: Client[], rate: { rx: number | null; tx: number | null }, unavailable: boolean) {
  const statusFailed = unavailable || !status || Boolean(status.observation_failed);
  if (statusFailed) return { statusFailed: true, sessions: null, rx: null, tx: null, serviceActive: null, configured: null, inUse: false, avgLatency: null };
  const observed = clients.filter((client) => !client.observation_failed);
  const complete = observed.length > 0 && observed.length === clients.length;
  const clientRate = (key: "rx_bps" | "tx_bps", fallback: number | null) => complete && observed.every((client) => knownMetric(client[key]))
    ? observed.reduce((sum, client) => sum + client[key]!, 0) : fallback;
  const latencies = observed.map((client) => client.latency_ms).filter(knownMetric);
  return { statusFailed: false,
    sessions: Math.max(observed.reduce((sum, client) => sum + (client.active_connections || 0), 0), status.online_peers),
    rx: clientRate("rx_bps", rate.rx), tx: clientRate("tx_bps", rate.tx), serviceActive: status.service_active,
    configured: Boolean(status.interface || status.address || status.listen_port || status.unit), inUse: status.health?.state === "WORKS",
    avgLatency: latencies.length ? Math.round(latencies.reduce((sum, value) => sum + value, 0) / latencies.length) : null };
}
