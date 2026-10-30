/* eslint-disable @next/next/no-img-element -- Local vector icons use the same rendering as protocol icons. */
import catalog from "../../../protocol-images/mihomo/vpn-services.json" with { type: "json" };
import { type ProfileRouting } from "./profile-modes";
import { ProfileModeToggle } from "./profile-mode-toggle";

export function ProfileRoutingMode({ routing, onChange }: { routing: ProfileRouting; onChange: (key: string, value: string | boolean) => void }) {
  const selectedOnly = routing.routing_scope === "selected";
  const services = new Set(String(routing.vpn_services || "").split(",").filter(Boolean));
  return <section className="mihomoRoutingMode">
    <header><b>Что направлять через VPN</b><ProfileModeToggle label="Маршрутизация" value={selectedOnly ? "Только выбранное" : "По правилам"} active={selectedOnly} onToggle={() => onChange("routing_scope", selectedOnly ? "all" : "selected")} /></header>
    <header><b>DNS</b><ProfileModeToggle label="DNS" value={routing.route_dns ? "DNS по маршруту" : "Общий DNS"} active={Boolean(routing.route_dns)} onToggle={() => onChange("route_dns", !routing.route_dns)} /></header>
    {selectedOnly && <div className="mihomoVpnServices" role="group" aria-label="Сервисы через VPN">{catalog.services.map(service => <button className="mihomoVpnService" key={service.id} type="button" aria-pressed={services.has(service.id)} onClick={() => {
        const next = new Set(services);
        if (next.has(service.id)) next.delete(service.id); else next.add(service.id);
        onChange("vpn_services", [...next].join(","));
      }}><span className={`mihomoVpnServiceIcon${service.id === "snapchat" ? " is-snapchat" : ""}`}><img src={service.icon} alt="" width={28} height={28} aria-hidden="true" /></span><b>{service.name}</b><span className="mihomoVpnServiceState" aria-hidden="true">{services.has(service.id) ? "✓" : "+"}</span></button>)}</div>}
  </section>;
}
