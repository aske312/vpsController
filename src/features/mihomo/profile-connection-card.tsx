import { ProtocolIcon } from "../../shared/components/protocol-icon";
import { duration, trafficBytes } from "../../shared/lib/control-plane-ui";
import type { ConnectionAppearance } from "./connection-status";
import type { ConnectionStats, ProfileConnection } from "./types";
import { channelShort } from "./catalog";
import { usageBytes } from "./subscription-limits";

export function ProfileConnectionCard({ connection, stats, status }: { connection: ProfileConnection; stats?: ConnectionStats; status: ConnectionAppearance }) {
  return <div data-connection-status={status.state} title={status.reason}>
    <span className={`protocol-${connection.component}`} title={channelShort[connection.component] || "CH"}><ProtocolIcon protocol={connection.component} /><i /></span>
    <p><b>{connection.name}</b><small className="mihomoConnectionStateLabel">{status.label}</small>
      <small>{stats?.used_bytes != null ? `Обмен: ${usageBytes({ used_bytes: stats.used_bytes, stats_available: stats.stats_available !== false, stats_partial: Boolean(stats.stats_partial) })}` : <>↓ {trafficBytes(stats, "rx_bytes")} · ↑ {trafficBytes(stats, "tx_bytes")}</>}</small>
      {(!stats || stats.stats_available === false) && <em>Статистика недоступна</em>}
      {stats && stats.stats_available !== false && <em>{stats.traffic_scope === "observation"
        ? stats.observed_since != null && Number.isFinite(stats.observed_since) && stats.observed_since > 0
          ? `Наблюдение с ${new Date(stats.observed_since * 1000).toLocaleString("ru-RU")}` : "Трафик текущего наблюдения"
        : "Текущие счётчики протокола · с последнего сброса"}</em>}
      {stats?.handshake_age_s != null && <em>Связь {duration(stats.handshake_age_s)}</em>}
    </p>
  </div>;
}
