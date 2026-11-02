import { endpointCheckReady } from "../../shared/lib/network-readiness";
import type { NetworkEndpointCheck, NetworkStatus } from "../../shared/types/control-plane";
import type { ConnectionStats, Module, Profile, ProfileConnection } from "./types";

export type ConnectionAppearance = { state: "unchecked" | "verified" | "warning" | "error"; label: string; reason: string };
const appearance = (state: ConnectionAppearance["state"], reason: string): ConnectionAppearance => ({
  state, reason, label: { unchecked: "Не проверено", verified: "Проверено", warning: "Требует внимания", error: "Ошибка" }[state],
});
const signature = (connection: ProfileConnection) => JSON.stringify([connection.component, Object.entries(connection.settings).sort(([a], [b]) => a.localeCompare(b))]);

/** Accept measured transfer for this connection, never raw sessions or old totals. */
export function connectionAppearance(connection: ProfileConnection, context: { profile?: Profile; modules?: Module[]; network?: NetworkStatus | null; stats?: ConnectionStats; draft?: boolean } = {}): ConnectionAppearance {
  const componentModule = context.modules?.find(item => item.id === connection.component);
  if (componentModule?.installed === false) return appearance("error", `Компонент ${componentModule.name} не установлен.`);
  if (componentModule?.active === false) return appearance("error", `Служба компонента ${componentModule.name} остановлена.`);
  if (context.stats?.service_active === false) return appearance("error", "Служба этого подключения остановлена.");
  if (connection.route_state?.state === "stale") return appearance("warning", connection.route_state.reason || "Маршрут подключения требует перенастройки.");
  const saved = context.profile?.connections.find(item => item.id === connection.id);
  if (context.draft && (!saved || signature(saved) !== signature(connection))) return appearance("unchecked", "Изменения подключения ещё не сохранены и не проверены.");
  if (connection.route_state?.state === "unknown") return appearance("unchecked", connection.route_state.reason || "Состояние маршрута пока не подтверждено.");
  const unchecked = appearance("unchecked", "Подтверждённой проверки этого подключения пока нет.");
  const transferredAt = context.stats?.traffic_verified_at;
  const observedAt = context.stats?.traffic_observed_at;
  const updated = Date.parse(context.profile?.updated_at || "");
  let automatic: ConnectionAppearance | undefined;
  if (context.profile && typeof transferredAt === "number" && Number.isSafeInteger(transferredAt) && transferredAt > 0
    && typeof observedAt === "number" && Number.isSafeInteger(observedAt) && observedAt >= transferredAt
    && context.stats?.stats_available !== false && (!Number.isFinite(updated) || transferredAt * 1000 >= updated)) {
    const recent = Math.max(observedAt, Date.now() / 1000) - transferredAt <= 120;
    automatic = { state: "verified", label: recent ? "Работает" : "Проверено", reason: recent
      ? "Сервер подтвердил передачу данных именно этого подключения."
      : `Передача данных этого подключения подтверждена ${new Date(transferredAt * 1000).toLocaleString("ru-RU")}.` };
  }
  if (!context.profile || !context.network) return automatic || unchecked;
  const settings = connection.settings;
  const mode = String(settings.route_mode || (settings.cdn_enabled ? "both" : "direct"));
  const targets: Array<[NetworkEndpointCheck["kind"], string]> = [];
  if (connection.component === "transport-reality" && ["cdn", "both"].includes(mode) && settings.cdn_domain) targets.push(["cdn", String(settings.cdn_domain)]);
  if (connection.component === "transport-reality" && mode === "tls" && settings.tls_domain) targets.push(["tls_relay", String(settings.tls_domain)]);
  if (settings.udp_domain && !settings.relay_host_id) targets.push(["udp_relay", String(settings.udp_domain)]);
  if (!targets.length) return automatic || unchecked;
  const consumer = `mihomo:${context.profile.id}:${connection.id}`;
  const checks = [...Object.values(context.network.transport_endpoint_checks_by_domain || {}), ...Object.values(context.network.transport_endpoint_checks || {})];
  const matched = targets.map(([kind, domain]) => checks.filter(check => check.kind === kind && check.domain.toLowerCase() === domain.toLowerCase()
    && (check.consumer_id || check.verified_connection?.consumer_id) === consumer
    && check.binding_id && check.check_id && check.checked_revision === check.desired_revision
    && Number.isInteger(check.desired_revision) && Number(check.desired_revision) > 0
    && Number.isFinite(Date.parse(check.checked_at || "")) && (!Number.isFinite(updated) || Date.parse(check.checked_at!) >= updated))
    .sort((a, b) => Date.parse(b.checked_at!) - Date.parse(a.checked_at!))[0]);
  const failed = matched.find(check => check?.status === "error" || check?.status === "unresolved");
  if (failed && (!automatic || Date.parse(failed.checked_at!) / 1000 >= transferredAt!)) return appearance("error", failed.message || "Проверка подключения завершилась ошибкой.");
  const warning = matched.find(check => check?.status === "warning" || check?.status === "stale");
  if (warning && (!automatic || Date.parse(warning.checked_at!) / 1000 >= transferredAt!)) return appearance("warning", warning.message || "Проверка подключения выявила замечания.");
  if (automatic) return automatic;
  // A legacy combined connection also has an independently unverified direct leg.
  if (mode !== "both" && matched.every(check => endpointCheckReady(check))) return appearance("verified", "Проверка именно этого подключения успешно завершена.");
  return unchecked;
}
