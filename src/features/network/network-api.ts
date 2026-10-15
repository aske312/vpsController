import { networkRoutesSnapshot } from "../mihomo/workspace-refresh";
import { dnsChecksSnapshot, dnsStatusSnapshot, networkEndpointSnapshot, networkPageSnapshot } from "./network-snapshot";
import type { DnsCheck, DnsSettings, DnsStatus, NetworkEndpointCheck, NetworkEndpointSettings, NetworkStatus, SystemAction } from "../../shared/types/control-plane";

export type NetworkRequest = <T = unknown>(path: string, init?: RequestInit) => Promise<T>;
export type NetworkAdmission = (path: string, init: RequestInit) => Promise<SystemAction>;

/** Все сетевые чтения и изменения страницы «Сеть» проходят через один feature API. */
export async function readNetworkControl(request: NetworkRequest) {
  const init = { cache: "no-store" as const };
  const [network, dns] = await Promise.all([
    request<NetworkStatus>("/network", init),
    request<DnsStatus>("/dns", init),
  ]);
  return { network: networkPageSnapshot(networkRoutesSnapshot(network)), dns: dnsStatusSnapshot(dns) };
}

export function saveNetworkDns(admit: NetworkAdmission, settings: DnsSettings) {
  return admit("/dns/settings", { method: "PUT", body: JSON.stringify({ ...settings, expected_revision: settings.revision }) });
}

export function saveNetworkEndpoints(admit: NetworkAdmission, settings: NetworkEndpointSettings) {
  return admit("/network/endpoints", { method: "PUT", body: JSON.stringify({ ...settings, expected_revision: settings.revision }) });
}

export async function checkNetworkEndpoint(request: NetworkRequest, kind: NetworkEndpointCheck["kind"], domain: string, consumerId?: string) {
  const result = await request<NetworkEndpointCheck>("/network/endpoints/check", { method: "POST", body: JSON.stringify({ kind, domain, ...(consumerId ? { consumer_id: consumerId } : {}) }) });
  networkRoutesSnapshot({ transport_endpoint_checks: { [kind]: result } });
  const check = networkEndpointSnapshot(result);
  if (check.kind !== kind || check.domain.trim().toLowerCase() !== domain.trim().toLowerCase()
    || (consumerId && check.consumer_id !== consumerId)) throw new Error("Получена проверка другого маршрута");
  return check;
}

export function deleteNetworkEndpoint(admit: NetworkAdmission, kind: NetworkEndpointCheck["kind"], domain: string, revision?: string) {
  return admit(`/network/endpoints/${kind}/${encodeURIComponent(domain)}`, { method: "DELETE", headers: revision ? { "If-Match": revision } : undefined });
}

export function recoverNetworkDns(admit: NetworkAdmission) {
  return admit("/dns/recover", { method: "POST" });
}

export async function probeNetworkDns(request: NetworkRequest, providerId?: string) {
  const result = await request<{ items: DnsCheck[] }>("/dns/check", {
    method: "POST",
    body: JSON.stringify({ provider_id: providerId || null }),
  });
  return dnsChecksSnapshot(result);
}
