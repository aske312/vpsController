import type { DnsStatus } from "../../shared/types/control-plane";

export const dnsComponents = [
  { id: "system", key: "apply_system", code: "SYS", title: "Система", hint: "DNS системы и использующих его служб" },
  { id: "wg", key: "apply_wg", code: "WG", title: "WireGuard", hint: "Новые конфигурации клиентов" },
  { id: "awg", key: "apply_awg", code: "AWG", title: "AmneziaWG", hint: "Новые конфигурации клиентов" },
  { id: "shadowsocks", key: "apply_shadowsocks", code: "SS", title: "Shadowsocks", hint: "Рекомендация клиенту; сервер использует DNS системы" },
  { id: "vless-reality-xhttp", key: "apply_vrx", code: "VLS", title: "Vless", hint: "DNS Xray, с перезапуском службы" },
  { id: "hysteria2", key: "apply_system", code: "HY2", title: "Hysteria2", hint: "Вместе с системой, общий переключатель DNS" },
  { id: "tuic", key: "apply_system", code: "TUIC", title: "TUIC", hint: "Вместе с системой, общий переключатель DNS" },
  { id: "trojan", key: "apply_system", code: "TRJ", title: "Trojan", hint: "Вместе с системой, общий переключатель DNS" },
  { id: "openvpn", key: "apply_openvpn", code: "OVPN", title: "OpenVPN", hint: "Перезапуск службы и переподключение клиентов" },
  { id: "ikev2", key: "apply_ikev2", code: "IKE", title: "IKEv2", hint: "DNS при следующем подключении клиентов" },
] as const;

export function installedDnsComponents(dns: DnsStatus) {
  return dnsComponents.filter((component) => component.id === "system" || dns.protocol_effect_details?.[component.id]?.installed);
}

export function SystemDnsControl({ dns }: { dns: DnsStatus }) {
  const name = (id: string) => dns.providers.find((provider) => provider.id === id)?.name || (id === "custom" ? dns.settings.custom?.name || "Сторонний DNS" : id);
  return <section className="networkSystemControl">
    <header className="networkSectionHeading"><div><h2>Применение DNS</h2><p>Сохранённый профиль и текущее значение только для установленных компонентов.</p></div></header>
    <div className="networkTableWrap"><table className="networkDnsMatrix"><thead><tr><th>Компонент</th><th>Профиль</th><th>Текущее значение</th><th>Применение</th></tr></thead><tbody>{installedDnsComponents(dns).map((component) => {
      const effect = dns.protocol_effect_details?.[component.id];
      const inherited = component.id !== "system" && component.key === "apply_system";
      const scope = inherited ? "system" : component.id;
      const profile = dns.settings.profiles?.[scope] || dns.settings.selected_id;
      const enabled = Boolean(dns.settings[component.key]);
      const actual = component.id === "system" ? dns.system?.addresses.join(", ") : effect?.value;
      return <tr key={component.id}>
        <td><div className="networkComponent"><span>{component.code}</span><div><strong>{component.title}</strong><small>{component.hint}</small></div></div></td>
        <td><strong>{name(profile)}</strong><small>{inherited ? "Через систему" : dns.settings.profiles?.[scope] ? "Исключение" : "Общий профиль"}{component.id === "vless-reality-xhttp" && dns.settings.prefer_encrypted ? " · DoH" : ""}</small></td>
        <td><code>{actual || "Нет данных"}</code></td>
        <td><span className={enabled ? "networkBadge direct" : "networkBadge"}>{enabled ? "Включено" : "Не применяется"}</span>{enabled && effect && !inherited && !effect.matches_selected && <small>Значение отличается от профиля</small>}</td>
      </tr>;
    })}</tbody></table></div>
  </section>;
}
