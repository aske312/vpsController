import { SystemOperationUnknown } from "../../control-panel/system-operation";
import type { NetworkEndpointCheck, NetworkEndpointSettings, NetworkStatus, SystemAction } from "../../shared/types/control-plane";
import { networkRoutesSnapshot } from "../mihomo/workspace-refresh";
import { networkEndpointSnapshot, networkPageSnapshot } from "./network-snapshot";

export type NetworkRequest = <T = unknown>(path: string, init?: RequestInit) => Promise<T>;
export type NetworkAdmission = (path: string, init: RequestInit) => Promise<SystemAction>;

async function startNetworkMutation(admit: NetworkAdmission, path: string, init: RequestInit) {
  const id = crypto.randomUUID().replaceAll("-", "");
  const headers = new Headers(init.headers);
  headers.set("X-Operation-ID", id);
  try { return await admit(path, { ...init, headers }); }
  catch (cause) {
    // Keep the form tied to its worker even if the admission response was lost.
    // An earlier command blocking admission belongs to a different form.
    if (cause instanceof SystemOperationUnknown && cause.id === id) return { id, state: "unknown", message: cause.message };
    throw cause;
  }
}

/** Все сетевые чтения и изменения страницы «Сеть» проходят через один feature API. */
export async function readNetworkControl(request: NetworkRequest) {
  const init = { cache: "no-store" as const };
  const network = await request<NetworkStatus>("/network", init);
  return { network: networkPageSnapshot(networkRoutesSnapshot(network)) };
}

export function saveNetworkEndpoints(admit: NetworkAdmission, settings: NetworkEndpointSettings) {
  return startNetworkMutation(admit, "/network/endpoints", { method: "PUT", body: JSON.stringify({ ...settings, expected_revision: settings.revision }) });
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
  return startNetworkMutation(admit, `/network/endpoints/${kind}/${encodeURIComponent(domain)}`, { method: "DELETE", headers: revision ? { "If-Match": revision } : undefined });
}
