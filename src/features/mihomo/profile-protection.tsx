import { clientCapabilities } from "./client-capabilities";
import type { Module, ProfileConnection } from "./types";

type Routing = Record<string, string | number | boolean>;

export function ProfileProtection({ routing, connections, modules, common = false, client, echAvailable = false, onChange }: { routing: Routing; connections: ProfileConnection[]; modules: Module[]; common?: boolean; client?: string; echAvailable?: boolean; onChange: (key: string, value: string | boolean) => void }) {
  const fragment = Boolean(routing.tunnel_fragment);
  const caps = clientCapabilities(routing.client_config_format, undefined, client);
  const showFragment = caps.features.includes("tunnel_fragment");
  const showMihomo = common || String(routing.client_config_format || "mihomo") === "mihomo";
  const mihomoFeatures = [
    ["tun_enabled", "TUN", "TUN режим", "Перехватывает системный трафик устройства."],
    ["sniffer", "SNI", "Определение доменов", "Находит домен внутри TLS/HTTP для корректных правил."],
    ["tcp_concurrent", "TCP", "Ускоренное подключение", "Параллельно проверяет адреса домена."],
    ["dns_fake_ip", "FAKE", "Fake-IP DNS", "Точный выбор правил по домену в TUN."],
    ["dns_secure", "DoH", "Защищённый DNS", "Отправляет DNS через зашифрованный DoH."],
    ["dns_ipv6", "IPv6", "Контроль IPv6", "Разрешает IPv6-адреса в этом профиле."],
    ["dns_prefer_h3", "H3", "HTTP/3 для DNS", "Использует быстрый транспорт DoH, если он доступен."],
  ] as const;
  const enabledCount = [...(common ? ["tunnel_privacy", ...(echAvailable ? ["tunnel_ech"] : []), "tunnel_fragment"] : caps.features.filter((key) => key !== "tunnel_ech" || echAvailable)), ...(showMihomo ? [...mihomoFeatures.map(([key]) => key), "dns_hijack_force", "tun_strict_route"] : [])].filter((key) => routing[key]).length;
  const totalCount = (common ? 2 + (echAvailable ? 1 : 0) : caps.features.filter((key) => key !== "tunnel_ech" || echAvailable).length) + (showMihomo ? mihomoFeatures.length + 2 : 0);
  const compatible = connections.some((connection) => connection.component === "transport-reality" && modules.some((module) => module.id === connection.component && module.installed));
  return <section className="mihomoProfileRules mihomoProfileProtection">
    <header><div><b>Защита соединений</b><small>Настройки выбранного устройства. После сохранения обновите подписку в клиенте.</small></div><span>Выбрано {enabledCount} из {totalCount}</span></header>
    <div>
      {(common || caps.features.includes("tunnel_privacy")) && <button type="button" className={`mihomoProfileRuleButton${routing.tunnel_privacy ? " is-enabled" : ""}`} aria-pressed={Boolean(routing.tunnel_privacy)} onClick={() => onChange("tunnel_privacy", !routing.tunnel_privacy)}><i>VPS</i><span><b>Шифрование до VPS</b><small>VLESS Encryption · требуется актуальное ядро клиента</small></span></button>}
      {echAvailable && (common || caps.features.includes("tunnel_ech")) && <button type="button" className={`mihomoProfileRuleButton${routing.tunnel_ech ? " is-enabled" : ""}`} aria-pressed={Boolean(routing.tunnel_ech)} onClick={() => onChange("tunnel_ech", !routing.tunnel_ech)}><i>ECH</i><span><b>Скрытие имени сервера (ECH)</b></span></button>}
      {showFragment && <button type="button" className={`mihomoProfileRuleButton${fragment ? " is-enabled" : ""}`} aria-pressed={fragment} disabled={!compatible && !fragment} onClick={() => onChange("tunnel_fragment", !fragment)}><i>TLS</i><span><b>Фрагментация TLS</b>{common && <small>Только для sing-box клиентов</small>}</span></button>}
      {showMihomo && mihomoFeatures.map(([key, code, title, text]) => <button key={key} type="button" className={`mihomoProfileRuleButton${routing[key] ? " is-enabled" : ""}`} aria-pressed={Boolean(routing[key])} onClick={() => onChange(key, !routing[key])}><i>{code}</i><span><b>{title}</b><small>{text}</small></span></button>)}
      {showMihomo && <>
        <button type="button" className={`mihomoProfileRuleButton${routing.dns_hijack_force ? " is-enabled" : ""}`} aria-pressed={Boolean(routing.dns_hijack_force)} onClick={() => onChange("dns_hijack_force", !routing.dns_hijack_force)}><i>DNS</i><span><b>DNS Hijack · принудительно</b><small>Перехватывает DNS-запросы через Mihomo.</small></span></button>
        <button type="button" className={`mihomoProfileRuleButton${routing.tun_strict_route ? " is-enabled" : ""}`} aria-pressed={Boolean(routing.tun_strict_route)} onClick={() => onChange("tun_strict_route", !routing.tun_strict_route)}><i>ROUTE</i><span><b>TUN · Strict Route</b><small>Запрещает обход маршрутизации мимо TUN.</small></span></button>
      </>}
    </div>
  </section>;
}
