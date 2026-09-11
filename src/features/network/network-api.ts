import type { DnsCheck, DnsSettings, DnsStatus, MihomoDnsStatus, NetworkStatus } from "../../shared/types/control-plane";

export type NetworkRequest = <T = unknown>(path: string, init?: RequestInit) => Promise<T>;

/** Все сетевые чтения и изменения страницы «Сеть» проходят через один feature API. */
export async function readNetworkControl(request: NetworkRequest) {
  const [network, dns, mihomoDns] = await Promise.all([
    request<NetworkStatus>("/network"),
    request<DnsStatus>("/dns"),
    request<MihomoDnsStatus>("/mihomo/dns/settings").catch(() => null),
  ]);
  return { network, dns, mihomoDns };
}

export function saveNetworkDns(request: NetworkRequest, settings: DnsSettings) {
  return request<DnsStatus>("/dns/settings", { method: "PUT", body: JSON.stringify(settings) });
}

export async function probeNetworkDns(request: NetworkRequest, providerId?: string) {
  const result = await request<{ items: DnsCheck[] }>("/dns/check", {
    method: "POST",
    body: JSON.stringify({ provider_id: providerId || null }),
  });
  return result.items;
}

export function saveNetworkMihomoDns(request: NetworkRequest, values: Record<string, string | number | boolean>) {
  return request<MihomoDnsStatus>("/mihomo/dns/settings", {
    method: "PATCH",
    body: JSON.stringify({ values }),
  });
}
