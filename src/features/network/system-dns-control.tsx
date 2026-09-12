import type { Dispatch, SetStateAction } from "react";
import type { DnsSettings, DnsStatus } from "../../shared/types/control-plane";

const components = [
  { id: "system", key: "apply_system", code: "VPS", title: "Сам сервер", hint: "Системное разрешение имён" },
  { id: "wg", key: "apply_wg", code: "WG", title: "WireGuard", hint: "DNS в новых конфигурациях клиентов" },
  { id: "awg", key: "apply_awg", code: "AWG", title: "AmneziaWG", hint: "DNS в новых конфигурациях клиентов" },
  { id: "shadowsocks", key: "apply_shadowsocks", code: "SS", title: "Shadowsocks", hint: "Рекомендация, без изменения серверного трафика" },
  { id: "vless-reality-xhttp", key: "apply_vrx", code: "VLESS", title: "Прямой VLESS", hint: "Сразу, с перезапуском Xray" },
] as const;

export function SystemDnsControl({ dns, dnsDraft, setDnsDraft }: { dns: DnsStatus; dnsDraft: DnsSettings; setDnsDraft: Dispatch<SetStateAction<DnsSettings | null>> }) {
  return <section className="networkSystemControl"><header className="networkSectionHeading"><div><h2>Куда применять</h2><p>Основной профиль можно изменить отдельно для каждого компонента. Резервный DNS общий.</p></div></header><div className="networkTableWrap"><table className="networkDnsMatrix"><thead><tr><th>Компонент</th><th>Основной профиль</th><th>DNS сейчас</th><th>Применять</th></tr></thead><tbody>{components.map((component) => {
    const effect = dns.protocol_effect_details?.[component.id];
    const available = component.id === "system" || Boolean(effect?.installed);
    const selected = dnsDraft.profiles?.[component.id] || dnsDraft.selected_id;
    return <tr className={available ? "" : "unavailable"} key={component.id}><td><div className="networkComponent"><span>{component.code}</span><div><strong>{component.title}</strong><small>{available ? component.hint : "Протокол не установлен"}</small></div></div></td><td><select aria-label={`Основной DNS: ${component.title}`} disabled={!available} value={selected} onChange={(event) => { const id = event.target.value; setDnsDraft((current) => current ? { ...current, ...(component.id === "system" ? { selected_id: id } : {}), profiles: { ...(current.profiles || {}), [component.id]: id } } : current); }}>{!dns.providers.some((provider) => provider.id === selected) && selected !== "custom" && <option value={selected}>{selected || "Выберите профиль"}</option>}{dns.providers.filter((provider) => provider.id !== "custom").map((provider) => <option value={provider.id} key={provider.id}>{provider.name}</option>)}<option value="custom">{dnsDraft.custom?.name || "Сторонний DNS"}</option></select></td><td><code>{available ? component.id === "system" ? dns.system?.addresses.join(", ") || "Нет данных" : effect?.value || "Нет данных" : "Не установлен"}</code></td><td><input aria-label={`Применять DNS: ${component.title}`} type="checkbox" disabled={!available} checked={available && Boolean(dnsDraft[component.key])} onChange={(event) => { const checked = event.target.checked; setDnsDraft((current) => current ? { ...current, [component.key]: checked } : current); }} /></td></tr>;
  })}</tbody></table></div></section>;
}
