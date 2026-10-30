import type { RelayHost, RelayStatus } from "../network/relay-state";
import { connectionAddressModes } from "./connection-address";
import type { ProfileConnection } from "./types";

type RouteHop = { label: string; address?: string; port?: number; pendingPort?: boolean };
export type ConnectionRoute = {
  mode: "direct" | "tls" | "udp" | "cdn" | "both";
  originAddress?: string;
  originPort?: number;
  domain?: string;
  relay?: { address?: string; port?: number; afterCdn?: boolean };
};

export function profileConnectionRoute(connection: ProfileConnection, inventory: { status: RelayStatus | null }): ConnectionRoute {
  const settings = connection.settings;
  const modes = connectionAddressModes(connection.component);
  const mode = (modes.tls ? String(settings.route_mode || (settings.cdn_enabled ? "both" : "direct")) : modes.udp ? "udp" : "direct") as ConnectionRoute["mode"];
  const relay = String(settings.relay_host_id || "");
  const host = inventory.status?.hosts.find(item => item.id === relay);
  const address = String(settings.relay_address || host?.client_address || (host ? new URL(host.agent_url).hostname.replace(/^\[|\]$/g, "") : ""));
  const domain = String(mode === "tls" ? settings.tls_domain || "" : mode === "cdn" || mode === "both" ? settings.cdn_domain || "" : mode === "udp" ? settings.udp_domain || "" : "");
  const origin = relay ? inventory.status?.origin_ip || connection.credential?.server_address : connection.credential?.server_address || inventory.status?.origin_ip;
  const port = mode === "cdn" || mode === "tls" ? 443 : connectionPort(settings.port) || connectionPort(connection.credential?.port);
  const transports = connection.component === "transport-shadowsocks" ? ["tcp", "udp"] : modes.udp ? ["udp"] : ["tcp"];
  const cdnRelay = mode === "cdn" && settings.relay_cdn === true;
  const afterCdn = cdnRelay && settings.relay_cdn_mode !== "before_cdn";
  return { mode, originAddress: origin, originPort: port, domain,
    ...(relay ? { relay: { address, port: relayListenPort(host, inventory.status?.origin_ip, port, transports, cdnRelay && !afterCdn ? domain : undefined), afterCdn } } : {}) };
}

export function connectionPort(value: unknown): number | undefined {
  if (typeof value !== "number" && typeof value !== "string") return;
  const port = Number(value);
  return Number.isInteger(port) && port > 0 && port <= 65535 ? port : undefined;
}

export function relayListenPort(host: RelayHost | undefined, origin: string | undefined, port: number | undefined, transports: string[], cdnDomain?: string): number | undefined {
  if (!host || !port) return;
  const routes = host.routes.filter(route => cdnDomain
    ? route.cdn_domain === cdnDomain && route.target_port === 443 && route.transport === "tcp"
    : !route.cdn_domain && route.target_ip === origin && route.target_port === port && transports.includes(route.transport));
  return routes.filter(route => cdnDomain || transports.every(transport => routes.some(other => other.transport === transport && other.listen_port === route.listen_port)))
    .sort((left, right) => left.listen_port - right.listen_port)[0]?.listen_port;
}

function hopDetail(hop: RouteHop): string {
  const address = hop.address?.includes(":") ? `[${hop.address.replace(/^\[|\]$/g, "")}]` : hop.address;
  const endpoint = address ? `${address}${hop.port ? `:${hop.port}` : ""}` : hop.port ? `порт ${hop.port}` : "";
  return [endpoint, hop.pendingPort ? "порт после сохранения" : ""].filter(Boolean).join(" · ");
}

function hopText(hop: RouteHop): string {
  const detail = hopDetail(hop);
  return hop.label + (detail ? ` (${detail})` : "");
}

/** Endpoint information only: never infer a forwarding port from an unrelated rule. */
export function ConnectionRouteMap({ route }: { route: ConnectionRoute }) {
  const origin: RouteHop = { label: "VPS", address: route.originAddress, port: route.originPort };
  const relay: RouteHop | undefined = route.relay && { label: "Relay", address: route.relay.address, port: route.relay.port, pendingPort: !route.relay.port };
  const cdn: RouteHop = { label: "CDN", address: route.domain, port: 443 };
  const hops = route.mode === "cdn"
    ? route.relay?.afterCdn ? [cdn, relay!, origin] : [relay, cdn, origin]
    : [relay, { ...origin, address: !relay && route.domain && ["tls", "udp"].includes(route.mode) ? route.domain : origin.address }];
  const paths = [[{ label: "Клиент" }, ...hops.filter((hop): hop is RouteHop => Boolean(hop))]];
  if (route.mode === "both") paths.push([{ label: "Клиент" }, cdn, { ...origin, port: 443 }]);
  const text = paths.map(path => path.map(hopText).join(" → ")).join("; ");
  return <div className="mihomoConnectionRoute" data-connection-route role="img" aria-label={text}>
    {paths.map((path, pathIndex) => <span className="mihomoRouteLine" key={pathIndex} aria-hidden="true">
      {path.map((hop, index) => <span className="mihomoRouteStep" key={index}>
        {index > 0 && <span className="mihomoRouteArrow">→</span>}
        <span className="mihomoRouteHop" data-hop={hop.label.toLowerCase()}>
          <b>{hop.label}</b>{hopDetail(hop) && <code>{hopDetail(hop)}</code>}
        </span>
      </span>)}
    </span>)}
  </div>;
}
