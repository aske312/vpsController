import { clientCapabilities } from "./client-capabilities";
import { profileConnectionMode, profileFunctionKeys } from "./profile-modes";
import { ProfileModeToggle } from "./profile-mode-toggle";
import { profileDirectRules } from "./catalog";

type Routing = Record<string, string | number | boolean>;

export function ProfileProtection({ routing, echAvailable = false, onChange }: { routing: Routing; echAvailable?: boolean; onChange: (key: string, value: string | boolean) => void }) {
  const caps = clientCapabilities();
  const tun = profileConnectionMode(routing) === "tun";
  const mihomoFeatures = [
    ["sniffer", "SNI", "Определение доменов", "Находит домен внутри TLS/HTTP для корректных правил."],
    ["tcp_concurrent", "TCP", "Ускоренное подключение", "Параллельно проверяет адреса домена."],
    ["dns_fake_ip", "FAKE", "Fake-IP DNS", "Точный выбор правил по домену в TUN."],
    ["dns_secure", "DoH", "Защищённый DNS", "Отправляет DNS через зашифрованный DoH."],
    ["dns_ipv6", "IPv6", "Контроль IPv6", "Разрешает IPv6-адреса в этом профиле."],
    ["dns_prefer_h3", "H3", "HTTP/3 для DNS", "Использует быстрый транспорт DoH, если он доступен."],
  ] as const;
  const visibleMihomoFeatures = mihomoFeatures.filter(([key]) => tun || key !== "dns_fake_ip");
  const visibleTunnelFeatures = caps.features.filter((key) => key !== "tunnel_ech" || echAvailable);
  const trafficFunctions = profileDirectRules.filter((rule) => profileFunctionKeys.includes(rule.key) && caps.rules.includes(rule.key));
  const visibleKeys = [...visibleTunnelFeatures, ...visibleMihomoFeatures.map(([key]) => key), ...trafficFunctions.map(rule => rule.key), ...(tun ? ["dns_hijack_force", "tun_strict_route"] : [])];
  const enabledCount = visibleKeys.filter((key) => routing[key]).length;
  const totalCount = visibleKeys.length;
  return <section className="mihomoProfileRules mihomoProfileProtection">
    <header><div><b>Защита соединений</b></div><div className="mihomoProtectionActions"><ProfileModeToggle label="Режим подключения" value={tun ? "TUN" : "Proxy"} active={tun} onToggle={() => onChange("connection_mode", tun ? "proxy" : "tun")} /><span aria-label={`Включено ${enabledCount} из ${totalCount}`}>{enabledCount}/{totalCount}</span></div></header>
    <div>
      {caps.features.includes("tunnel_privacy") && <button type="button" className={`mihomoProfileRuleButton${routing.tunnel_privacy ? " is-enabled" : ""}`} aria-pressed={Boolean(routing.tunnel_privacy)} onClick={() => onChange("tunnel_privacy", !routing.tunnel_privacy)}><i>VPS</i><span><b>Шифрование до VPS</b><small>VLESS Encryption · требуется актуальное ядро клиента</small></span></button>}
      {echAvailable && caps.features.includes("tunnel_ech") && <button type="button" className={`mihomoProfileRuleButton${routing.tunnel_ech ? " is-enabled" : ""}`} aria-pressed={Boolean(routing.tunnel_ech)} onClick={() => onChange("tunnel_ech", !routing.tunnel_ech)}><i>ECH</i><span><b>Скрытие имени сервера (ECH)</b></span></button>}
      {visibleMihomoFeatures.map(([key, code, title, text]) => <button key={key} type="button" className={`mihomoProfileRuleButton${routing[key] ? " is-enabled" : ""}`} aria-pressed={Boolean(routing[key])} onClick={() => onChange(key, !routing[key])}><i>{code}</i><span><b>{title}</b><small>{text}</small></span></button>)}
      {tun && <>
        <button type="button" className={`mihomoProfileRuleButton${routing.dns_hijack_force ? " is-enabled" : ""}`} aria-pressed={Boolean(routing.dns_hijack_force)} onClick={() => onChange("dns_hijack_force", !routing.dns_hijack_force)}><i>DNS</i><span><b>DNS Hijack · принудительно</b><small>Перехватывает DNS-запросы через Mihomo.</small></span></button>
        <button type="button" className={`mihomoProfileRuleButton${routing.tun_strict_route ? " is-enabled" : ""}`} aria-pressed={Boolean(routing.tun_strict_route)} onClick={() => onChange("tun_strict_route", !routing.tun_strict_route)}><i>ROUTE</i><span><b>TUN · Strict Route</b><small>Запрещает обход маршрутизации мимо TUN.</small></span></button>
      </>}
    </div>
    <section className="mihomoTrafficFunctions"><h4>Функции трафика</h4><div>{trafficFunctions.map(rule => <button type="button" key={rule.key} className={`mihomoProfileRuleButton${routing[rule.key] ? " is-enabled" : ""}`} aria-pressed={Boolean(routing[rule.key])} onClick={() => onChange(rule.key, !routing[rule.key])}><i>{rule.code}</i><span><b>{rule.title}</b></span></button>)}</div></section>
  </section>;
}
