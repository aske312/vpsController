import { ProtocolIcon } from "../../shared/components/protocol-icon";
import { duration, trafficBytes } from "../../shared/lib/control-plane-ui";
import type { ConnectionAppearance } from "./connection-status";
import type { ConnectionStats, ProfileConnection } from "./types";
import { channelShort } from "./catalog";

export function ProfileConnectionCard({ connection, stats, status }: { connection: ProfileConnection; stats?: ConnectionStats; status: ConnectionAppearance }) {
  return <div data-connection-status={status.state} title={status.reason}>
    <span className={`protocol-${connection.component}`} title={channelShort[connection.component] || "CH"}><ProtocolIcon protocol={connection.component} /><i /></span>
    <p><b>{connection.name}</b><small className="mihomoConnectionStateLabel">{status.label}</small>
      <small>↓ {trafficBytes(stats, "rx_bytes")} · ↑ {trafficBytes(stats, "tx_bytes")}</small>
      {stats?.stats_available === false && <em>Статистика недоступна</em>}
      {stats?.traffic_scope === "observation" && <em>Учтённый трафик текущего наблюдения</em>}
      {stats?.handshake_age_s != null && <em>Связь {duration(stats.handshake_age_s)} назад</em>}
    </p>
  </div>;
}
