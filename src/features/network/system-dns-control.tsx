import type { Dispatch, SetStateAction } from "react";
import type { DnsSettings, DnsStatus } from "../../shared/types/control-plane";

const components = [
  { id: "system", key: "apply_system", code: "VPS", title: "Сам сервер", hint: "Системное разрешение имён" },
  { id: "wg", key: "apply_wg", code: "WG", title: "WireGuard", hint: "DNS в новых конфигурациях клиентов" },
  { id: "awg", key: "apply_awg", code: "AWG", title: "AmneziaWG", hint: "DNS в новых конфигурациях клиентов" },
  { id: "shadowsocks", key: "apply_shadowsocks", code: "SS", title: "Shadowsocks", hint: "Рекомендация клиентам, без изменения серверного трафика" },
  { id: "vless-reality-xhttp", key: "apply_vrx", code: "VLESS", title: "Прямой VLESS", hint: "Применение сразу с перезапуском Xray" },
] as const;

export function SystemDnsControl({ dns, dnsDraft, setDnsDraft }: { dns: DnsStatus; dnsDraft: DnsSettings; setDnsDraft: Dispatch<SetStateAction<DnsSettings | null>> }) {
  return <section className="networkSystemControl"><div className="networkSectionHeading"><div><span className="networkCaption">Применение DNS</span><h2>Куда применять</h2><p>Отметьте компоненты и выберите для них профиль.</p></div></div><div className="networkDnsMatrix">{components.map((component) => {
    const effect = dns.protocol_effect_details?.[component.id];
    const available = component.id === "system" || Boolean(effect?.installed);
    const selected = dnsDraft.profiles?.[component.id] || dnsDraft.selected_id;
    return <div className={`networkMatrixRow ${available ? "" : "unavailable"}`} key={component.id}>
      <div className="networkComponent"><span>{component.code}</span><div><strong>{component.title}</strong><small>{available ? component.hint : "Протокол не установлен"}</small></div></div>
      <label className="networkProfileSelect"><span className="networkSrOnly">Профиль DNS: {component.title}</span><select disabled={!available} value={selected} onChange={(event) => { const id = event.target.value; setDnsDraft((current) => current ? { ...current, ...(component.id === "system" ? { selected_id: id } : {}), profiles: { ...(current.profiles || {}), [component.id]: id } } : current); }}>
        {!dns.providers.some((provider) => provider.id === selected) && selected !== "custom" && <option value={selected}>{selected || "Выберите профиль"}</option>}
        {dns.providers.filter((provider) => provider.id !== "custom").map((provider) => <option value={provider.id} key={provider.id}>{provider.name}</option>)}<option value="custom">{dnsDraft.custom?.name || "Сторонний DNS"}</option>
      </select></label>
      <label className="networkApplyToggle"><input type="checkbox" disabled={!available} checked={available && Boolean(dnsDraft[component.key])} onChange={(event) => { const checked = event.target.checked; setDnsDraft((current) => current ? { ...current, [component.key]: checked } : current); }} /><span>Применять<span className="networkSrOnly">: {component.title}</span></span></label>
      {available && <div className="networkMatrixCurrent"><span>Сейчас</span><code>{component.id === "system" ? dns.system?.addresses.join(", ") || "Нет данных" : effect?.value || "Нет данных"}</code><small>{component.id === "system" ? dns.system?.source : effect?.matches_selected ? "Соответствует сохранённому профилю" : "Отличается от сохранённого профиля"}</small></div>}
    </div>;
  })}</div></section>;
}
