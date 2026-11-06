import type { RelayRoute } from "./relay-state";

const errors: Record<string, string> = {
  tcp_connect_timeout: "Таймаут подключения к VPS", tcp_connect_error: "Ошибка подключения к VPS",
  tcp_transfer_timeout: "Таймаут передачи TCP", tcp_transfer_error: "Ошибка передачи TCP",
  udp_upstream_error: "Ошибка UDP до VPS", udp_listener_error: "Ошибка приёма UDP",
  udp_buffer_full: "Переполнение очереди UDP", udp_receive_buffer_unavailable: "Недостаточно памяти UDP",
  udp_pending_full: "Переполнение очереди новой сессии", udp_session_error: "Ошибка создания UDP-сессии", session_limit: "Недостаточно ресурсов для новой сессии",
};

export function RelayLiveDiagnostics({ route, stale }: { route: RelayRoute; stale: boolean }) {
  const value = route.diagnostics;
  if (stale || !value) return <p>Нет данных от Relay Agent.</p>;
  const interval = value.interval;
  const mib = (bytes: number) => (bytes / 1048576).toLocaleString("ru-RU", { maximumFractionDigits: 1 });
  return <section className="routeDetailsCheck" aria-label="Текущая передача Relay">
    <p>Обычная пересылка · {route.transport.toUpperCase()} :{route.listen_port}</p>
    <strong>{interval ? `За последние ${Math.max(1, interval.seconds)} с` : "Текущая передача"}</strong>
    {interval ? <p>Ошибок: {interval.errors} · Отброшено Agent: {interval.dropped_packets} пакетов · Новых сессий: {interval.new_sessions}</p> : <p>Статистика за минуту: нет данных от Relay Agent.</p>}
    {interval && Object.entries(interval.error_counts).length > 0 && <p>{Object.entries(interval.error_counts).map(([code, count]) => `${errors[code] || code}: ${count}`).join(" · ")}</p>}
    <p>Активных сессий маршрута: {route.sessions ?? "нет данных"}{value.route_session_limit !== undefined ? ` / ${value.route_session_limit}` : ""} · Без ответа более 5 с: {value.no_return_sessions ?? "нет данных"}</p>
    <p>Сессий на всём Relay: {value.session_budget_used ?? "нет данных"} / {value.session_budget_limit ?? "нет данных"}</p>
    <p>Буферы UDP на всём Relay: {value.udp_receive_budget_used_bytes !== undefined && value.udp_receive_budget_bytes !== undefined ? <>{mib(value.udp_receive_budget_used_bytes)} / {mib(value.udp_receive_budget_bytes)} МиБ · {Math.round(value.udp_receive_budget_used_bytes / Math.max(1, value.udp_receive_budget_bytes) * 100)}%</> : "нет данных"}</p>
    {route.transport === "udp" && <p>Буферы UDP этого маршрута: {value.udp_receive_allocated_bytes !== undefined ? `${mib(value.udp_receive_allocated_bytes)} МиБ` : "нет данных"}</p>}
    {value.statistics_since !== undefined && <small>Ошибок с {new Date(value.statistics_since * 1000).toLocaleString("ru-RU")}: {route.errors ?? "нет данных"}. Очистка счётчиков не разрывает соединения.</small>}
  </section>;
}
