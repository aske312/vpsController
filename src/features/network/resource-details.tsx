import type { RouteEntry } from "./route-library-state";
import { connectionCount, ipProvider, resourceProvider, resourceUsageLabel, type ResourceConsumer } from "./resource-usage";
import type { NetworkIpIdentity } from "../../shared/types/control-plane";
import { addressRoleLabels, type ServerAddress } from "./server-address-state";

export function ServerResourceDetails({ row, info }: { row: ServerAddress; info?: NetworkIpIdentity }) {
  return <section className="routeResourceDetails" aria-label="Сведения об IP"><dl className="routeResourceFacts">
    <div><dt>Провайдер IP</dt><dd>{ipProvider(info) || "Не определён"}</dd></div>
    <div><dt>Интерфейс</dt><dd>{row.interfaces.join(", ") || "Не подтверждён"}</dd></div>
    <div><dt>Роль адреса</dt><dd>{addressRoleLabels[row.role]}</dd></div>
  </dl>{info && <details className="routeResourceTechnical"><summary>Сведения о сети</summary><dl>
    <div><dt>ASN / сеть</dt><dd>{[info.asn, info.network].filter(Boolean).join(" · ") || "Не определены"}</dd></div>
    <div><dt>Обратный DNS</dt><dd>{info.ptr || "Не определён"}</dd></div>
  </dl></details>}</section>;
}

export function ResourceDetails({ entry, consumers }: { entry: RouteEntry; consumers: ResourceConsumer[] | null }) {
  const domain = entry.kind === "domain" ? entry.domain : null;
  const groups = new Map<string, number>();
  for (const consumer of consumers || []) {
    const label = `${consumer.owner === "mihomo" ? "Mihomo · " : ""}${consumer.label}${consumer.retained ? " · архив привязок" : ""}`;
    groups.set(label, (groups.get(label) || 0) + 1);
  }
  return <section className="routeResourceDetails" aria-label="Сведения о ресурсе">
    <dl className="routeResourceFacts">
      <div><dt>{domain ? "Провайдер IP / CDN" : "Relay Agent"}</dt><dd>{resourceProvider(entry)}</dd></div>
      <div><dt>Использование</dt><dd>{resourceUsageLabel(entry, consumers)}</dd></div>
      {domain && <div><dt>DNS-провайдер</dt><dd>{domain.dns?.provider && domain.dns.provider !== "Unknown" ? domain.dns.provider : "Не определён"}</dd></div>}
    </dl>
    {groups.size > 0 && <details className="routeUsageConnections"><summary>Связанные подключения <span>{consumers?.length}</span></summary><ul>{[...groups].map(([label, count]) => <li key={label}><strong>{label}</strong><span>{connectionCount(count)}</span></li>)}</ul></details>}
    {consumers?.some(item => item.retained) && <p>Архив содержит привязки на момент удаления адреса. Их текущее использование не подтверждено.</p>}
    {domain && <details className="routeResourceTechnical"><summary>DNS и сеть</summary><dl>
      <div><dt>IP в DNS</dt><dd>{domain.resolved.join(" · ") || "Не определён"}</dd></div>
      <div><dt>Серверы DNS</dt><dd>{domain.dns?.nameservers.join(" · ") || "Не определены"}</dd></div>
      {!!domain.dns?.records?.cname.length && <div><dt>CNAME</dt><dd>{domain.dns.records.cname.join(" · ")}</dd></div>}
      {(domain.ip_info || []).map(ip => <div key={ip.address}><dt>{ip.address}</dt><dd>{[ipProvider(ip), ip.asn, ip.network, ip.ptr].filter(value => value && value !== "Unknown").join(" · ") || "Сведения о сети не получены"}</dd></div>)}
    </dl><p>DNS-провайдер обслуживает записи домена. Провайдер IP / CDN относится к входным адресам и может отличаться от хостинга VPS.</p></details>}
  </section>;
}
