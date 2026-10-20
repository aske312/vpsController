import type { NetworkEndpointSettings } from "../../shared/types/control-plane";

export const endpointListKeys = { cdn_domain: "cdn_domains", tls_relay_domain: "tls_relay_domains", udp_relay_domain: "udp_relay_domains" } as const;
export type EndpointField = keyof typeof endpointListKeys;
export const emptyEndpointSettings = (): NetworkEndpointSettings => ({ cdn_domain: "", tls_relay_domain: "", udp_relay_domain: "" });
export const endpointAddress = (value: string) => value.trim().toLowerCase().replace(/\.+$/, "");
export function endpointValues(settings: NetworkEndpointSettings, key: EndpointField): string[] {
  return [...new Set([settings[key], ...(settings[endpointListKeys[key]] || [])].filter(Boolean))];
}

/** Adding routes must retain existing primary addresses and every other route type. */
export function appendNetworkEndpoints(existing: NetworkEndpointSettings, additions: NetworkEndpointSettings): NetworkEndpointSettings {
  const next = { ...existing };
  for (const key of Object.keys(endpointListKeys) as EndpointField[]) {
    const values = endpointValues(existing, key);
    const known = new Set(values.map(endpointAddress));
    const added = endpointValues(additions, key).map(endpointAddress).filter(value => {
      if (!value || known.has(value)) return false;
      known.add(value); return true;
    });
    if (!added.length) continue;
    next[key] = existing[key] || added[0];
    next[endpointListKeys[key]] = [...values, ...added];
  }
  return next;
}
