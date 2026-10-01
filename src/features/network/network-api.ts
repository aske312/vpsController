import { submitSystemOperation } from "../../control-panel/system-operation";
import type { DnsCheck, DnsSettings, DnsStatus, NetworkEndpointCheck, NetworkEndpointSettings, NetworkStatus } from "../../shared/types/control-plane";

export type NetworkRequest = <T = unknown>(path: string, init?: RequestInit) => Promise<T>;

/** Все сетевые чтения и изменения страницы «Сеть» проходят через один feature API. */
export async function readNetworkControl(request: NetworkRequest, force = false) {
  const init = force ? { cache: "no-store" as const } : undefined;
  const [network, dns] = await Promise.all([
    request<NetworkStatus>("/network", init),
    request<DnsStatus>("/dns", init),
  ]);
  return { network, dns };
}

export function saveNetworkDns(request: NetworkRequest, settings: DnsSettings) {
  return submitSystemOperation(request, "/dns/settings", { method: "PUT", body: JSON.stringify({ ...settings, expected_revision: settings.revision }) });
}

export function saveNetworkEndpoints(request: NetworkRequest, settings: NetworkEndpointSettings) {
  return submitSystemOperation(request, "/network/endpoints", { method: "PUT", body: JSON.stringify({ ...settings, expected_revision: settings.revision }) });
}

export function checkNetworkEndpoint(request: NetworkRequest, kind: NetworkEndpointCheck["kind"], domain: string) {
  return request<NetworkEndpointCheck>("/network/endpoints/check", { method: "POST", body: JSON.stringify({ kind, domain }) });
}

export function deleteNetworkEndpoint(request: NetworkRequest, kind: NetworkEndpointCheck["kind"], domain: string, revision?: string) {
  return submitSystemOperation(request, `/network/endpoints/${kind}/${encodeURIComponent(domain)}`, { method: "DELETE", headers: revision ? { "If-Match": revision } : undefined });
}

export async function probeNetworkDns(request: NetworkRequest, providerId?: string) {
  const result = await request<{ items: DnsCheck[] }>("/dns/check", {
    method: "POST",
    body: JSON.stringify({ provider_id: providerId || null }),
  });
  return result.items;
}
