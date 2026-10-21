import type { NetworkEndpointCheck, NetworkStatus } from "../../shared/types/control-plane";
import { endpointCheckReady } from "../../shared/lib/network-readiness";
import type { RelayHost } from "./relay-state";
import type { ServerAddress } from "./server-address-state";

export type RouteState = "present" | "verified" | "connected" | "listening" | "dns" | "unchecked" | "unknown" | "pending" | "warning" | "error" | "stale";
export type RouteSummary = { state: RouteState; label: string; detail: string; type: string; connection: string };
type Domain = NetworkStatus["domains"][number];
const kindLabels = { cdn: "CDN-домен", tls_relay: "TLS-домен", udp_relay: "UDP-домен" };

export function domainRouteSummary(domain: Domain, checks: NetworkEndpointCheck[], failed = false, origin: string[] = []): RouteSummary {
  const matching = checks.filter(check => check.domain.toLowerCase() === domain.value.toLowerCase());
  const kinds = new Set(matching.map(check => check.kind));
  if (domain.endpoint_kind) kinds.add(domain.endpoint_kind);
  const roles = domain.role.toLowerCase();
  if (roles.includes("cdn")) kinds.add("cdn");
  if (roles.includes("tls")) kinds.add("tls_relay");
  if (roles.includes("udp")) kinds.add("udp_relay");
  const types = [...(roles.includes("panel") ? ["Домен панели"] : []), ...[...kinds].map(kind => kindLabels[kind])];
  const own = new Set(origin.filter(Boolean));
  const mixed = domain.resolved.some(ip => own.has(ip)) && domain.resolved.some(ip => !own.has(ip));
  const connection = failed ? "Нет актуальных данных" : !domain.resolved.length ? "DNS не определён" : mixed ? "Смешанные DNS-адреса"
    : domain.edge?.confidence === "high" && domain.edge.provider.toLowerCase().includes("cloudflare") ? "Cloudflare · DNS"
    : domain.matches_origin ? "DNS → этот VPS" : "DNS → внешний адрес";
  const base = { type: types.join(" · ") || "Домен", connection };
  if (failed) return { ...base, state: "unknown", label: "Нет данных", detail: "Обновить состояние сети не удалось. Сохранённые сведения не подтверждают доступность." };
  if (domain.status === "stale" || matching.some(check => check.status === "stale")) return { ...base, state: "stale", label: "Устарел", detail: "Адрес удалён из маршрутов, но ещё сохранён в подключениях." };
  if (!domain.resolved.length || domain.route === "unresolved" || matching.some(check => check.status === "unresolved")) return { ...base, state: "error", label: "Нет DNS", detail: "Публичный адрес домена не определён." };
  if (matching.some(check => check.status === "error")) return { ...base, state: "error", label: "Ошибка проверки", detail: matching.filter(check => check.status === "error").map(check => check.message).join(" · ") };
  if (matching.some(check => check.status === "warning")) return { ...base, state: "warning", label: "Есть замечания", detail: matching.filter(check => check.status === "warning").map(check => check.message).join(" · ") };
  if (kinds.size && [...kinds].every(kind => matching.some(check => check.kind === kind && endpointCheckReady(check)))) {
    return { ...base, state: "verified", label: "Проверен", detail: "Серверная проверка подключения пройдена для всех типов этого адреса. Внешний клиентский доступ проверяется отдельно." };
  }
  if (kinds.size) return { ...base, state: "unchecked", label: "Не проверен", detail: "DNS определён. Успешная проверка подключения подтверждена не для всех типов этого адреса." };
  return { ...base, state: "dns", label: "DNS определён", detail: "DNS-запись найдена. Доступность панели и её сертификат этим не проверяются." };
}

export function serverAddressSummary(row: ServerAddress, failed = false, pending = false): RouteSummary {
  const base = { type: `IP сервера · ${row.family === "ipv4" ? "IPv4" : "IPv6"}`, connection: row.interfaces.join(", ") || (row.source === "configured" ? "Из настроек VPS" : "Сохранённый адрес") };
  if (pending) return { ...base, state: "pending", label: "Проверяем", detail: "Результат назначения IP ещё не подтверждён." };
  if (failed || row.presence === "unknown") return { ...base, state: "unknown", label: "Не подтверждён", detail: "Наличие IP на интерфейсе не подтверждено. Для адреса за NAT это может быть ожидаемо." };
  if (row.presence === "missing") return { ...base, state: "error", label: "Не найден", detail: "Сохранённый IP не найден на активном интерфейсе." };
  return { ...base, state: "present", label: "Назначен", detail: "IP найден на интерфейсе VPS. Это не проверка доступности извне." };
}

export function relayRouteSummary(host: RelayHost, failed = false, pending = false, reading = false): RouteSummary {
  const transports = (["tcp", "udp"] as const).map(transport => ({ transport, count: host.routes.filter(route => route.transport === transport).length })).filter(item => item.count);
  const base = { type: "Relay Agent", connection: transports.map(item => `${item.transport.toUpperCase()}: ${item.count}`).join(" · ") || (failed || pending || host.state !== "connected" ? "Порты неизвестны" : "Нет перенаправлений") };
  if (pending) return { ...base, state: "pending", label: "Проверяем", detail: "Результат изменения Relay ещё не подтверждён." };
  if (reading && host.state !== "connected") return { ...base, state: "pending", label: "Проверяем", detail: "Проверяем соединение с агентом и состояние его портов." };
  if (host.error) return { ...base, state: "error", label: "Ошибка Relay", detail: `${host.name}: ${host.error}` };
  if (failed || host.state !== "connected") return { ...base, state: "unknown", label: "Нет данных", detail: "Доступность агента и портов не подтверждена. Число перенаправлений — из сохранённых настроек." };
  if (host.routes.some(route => route.check?.cleanup_state === "failed" || route.device_test?.cleanup_state === "failed")) return { ...base, state: "error", label: "Ошибка очистки теста", detail: "Не удалось подтвердить удаление всех временных ресурсов. Раскройте адрес для сведений." };
  if (host.routes.some(route => ["failed", "missing", "drift"].includes(route.state) || route.check?.state === "failed")) return { ...base, state: "error", label: "Ошибка маршрута", detail: "Есть отсутствующие, изменённые или не прошедшие проверку перенаправления. Раскройте адрес для результатов и ошибок." };
  if (host.routes.some(route => route.state === "unknown")) return { ...base, state: "unknown", label: "Порты неизвестны", detail: "Агент доступен, но состояние части перенаправлений не подтверждено." };
  if (host.routes.some(route => route.device_test?.state === "failed")) return { ...base, state: "error", label: "Ошибка клиентского теста", detail: "Проверка с устройства не пройдена. Результат и UDP-наблюдения доступны в подробностях." };
  if (host.routes.some(route => route.device_test?.state === "warning" || ["warning", "failed"].includes(route.check?.measurement?.state || ""))) return { ...base, state: "warning", label: "Замечания теста", detail: "Медиатест и диагностический UDP-путь дали разные результаты или обнаружены потери. Раскройте адрес для сведений." };
  if (host.updates?.state === "failed") return { ...base, state: "warning", label: "Ошибка обновления", detail: "Agent доступен, но последняя попытка автообновления не удалась. Перенаправления и результаты проверок показаны в подробностях." };
  if (host.routes.length) return { ...base, state: "listening", label: "Порты слушают", detail: "Агент подтвердил слушающие порты. Это не подтверждение полного пути клиента до назначения." };
  return { ...base, state: "connected", label: "Агент доступен", detail: "Соединение с Relay Agent подтверждено; перенаправления ещё не настроены." };
}
