import type { DnsStatus } from "../../shared/types/control-plane";

export const dnsComponents = [
  { id: "system", key: "apply_system", code: "SYS", title: "Система", hint: "Системное разрешение имён" },
  { id: "wg", key: "apply_wg", code: "WG", title: "WireGuard", hint: "DNS в новых конфигурациях клиентов" },
  { id: "awg", key: "apply_awg", code: "AWG", title: "AmneziaWG", hint: "DNS в новых конфигурациях клиентов" },
  { id: "shadowsocks", key: "apply_shadowsocks", code: "SS", title: "Shadowsocks", hint: "Рекомендация, без изменения серверного трафика" },
  { id: "vless-reality-xhttp", key: "apply_vrx", code: "VLESS", title: "Прямой VLESS", hint: "Сразу, с перезапуском Xray" },
  { id: "openvpn", key: "apply_openvpn", code: "OVPN", title: "OpenVPN", hint: "С перезапуском службы и переподключением клиентов" },
  { id: "ikev2", key: "apply_ikev2", code: "IKE", title: "IKEv2", hint: "DNS при следующем подключении клиентов" },
] as const;

export function SystemDnsControl({ dns }: { dns: DnsStatus }) {
  const name = (id: string) => dns.providers.find((provider) => provider.id === id)?.name || (id === "custom" ? dns.settings.custom?.name || "Сторонний DNS" : id);
  const backup = !dns.settings.fallback_enabled ? "Без резерва" : dns.settings.fallback_id ? name(dns.settings.fallback_id) : "Резерв основного провайдера";
  return <section className="networkSystemControl"><header className="networkSectionHeading"><div><h2>Применение DNS</h2><p>Сохранённые настройки и фактические адреса. Общий резерв: {backup}.</p></div></header><div className="networkTableWrap"><table className="networkDnsMatrix"><thead><tr><th>Компонент</th><th>Сохранённый профиль</th><th>Текущее значение</th><th>Состояние</th></tr></thead><tbody>{dnsComponents.map((component) => {
    const effect = dns.protocol_effect_details?.[component.id];
    const available = component.id === "system" || Boolean(effect?.installed);
    const profile = dns.settings.profiles?.[component.id] || dns.settings.selected_id;
    const enabled = available && Boolean(dns.settings[component.key]);
    return <tr className={available ? "" : "unavailable"} key={component.id}><td><div className="networkComponent"><span>{component.code}</span><div><strong>{component.title}</strong><small>{available ? component.hint : "Протокол не установлен"}</small></div></div></td><td>{available ? <><strong>{name(profile)}</strong><small>{dns.settings.profiles?.[component.id] ? "Исключение" : "Общий профиль"}{component.id === "vless-reality-xhttp" && dns.settings.prefer_encrypted ? " · Только DoH" : ""}</small></> : <span>Не настроен</span>}</td><td><code>{available ? component.id === "system" ? dns.system?.addresses.join(", ") || "Нет данных" : effect?.value || "Нет данных" : "—"}</code></td><td><span className={`networkBadge ${enabled ? "direct" : ""}`}>{!available ? "Не установлен" : enabled ? "Применение включено" : "Не применяется"}</span>{available && component.id !== "system" && enabled && !effect?.matches_selected && <small>Фактический DNS отличается</small>}</td></tr>;
  })}{["hysteria2", "tuic", "trojan"].map((id) => {
    const effect = dns.protocol_effect_details?.[id];
    if (!effect?.installed) return null;
    return <tr key={id}><td><strong>{id === "hysteria2" ? "Hysteria2" : id === "tuic" ? "TUIC" : "Trojan"}</strong><small>Системное разрешение имён на сервере</small></td><td><strong>{dns.settings.apply_system ? name(dns.settings.profiles?.system || dns.settings.selected_id) : "Настройки ОС"}</strong><small>Наследует DNS системы, без отдельного исключения</small></td><td><code>{effect.value || "Нет данных"}</code></td><td><span className="networkBadge">Через систему</span></td></tr>;
  })}</tbody></table></div></section>;
}
