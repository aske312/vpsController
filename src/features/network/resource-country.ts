import type { RouteEntry } from "./route-library-state";
import { relayAddresses } from "./relay-state";

export type CountryLocations = Record<string, string | null>;
let countryNames: Intl.DisplayNames | undefined;

export function resourceIp(value: string): string | null {
  if (/^(?:\d{1,3}\.){3}\d{1,3}$/.test(value)) return value.split(".").every(part => Number(part) <= 255 && String(Number(part)) === part) ? value : null;
  if (!/^[0-9a-f:]+$/i.test(value) || !value.includes(":")) return null;
  try { return new URL(`http://[${value}]/`).hostname.slice(1, -1); } catch { return null; }
}

export function entryCountryAddresses(entry: RouteEntry): string[] {
  if (entry.kind === "domain") return [...new Set(entry.domain.resolved.map(resourceIp).filter((value): value is string => Boolean(value)))];
  const primary = resourceIp(entry.address);
  if (primary) return [primary];
  const addresses = relayAddresses(entry.host).map(resourceIp).filter((value): value is string => Boolean(value));
  if (!addresses.length) {
    try { const ip = resourceIp(new URL(entry.host.agent_url).hostname.replace(/^\[|\]$/g, "")); if (ip && !entry.host.excluded_addresses?.includes(ip)) addresses.push(ip); } catch { /* Unknown node address keeps the type icon. */ }
  }
  return [...new Set(addresses)];
}

export function resourceCountry(addresses: string[], locations: CountryLocations) {
  const codes = addresses.map(address => locations[resourceIp(address) || address]);
  const known = new Set(codes.filter((code): code is string => Boolean(code)));
  if (known.size > 1) return { code: null, label: "IP-адреса относятся к нескольким странам" };
  if (!codes.length || codes.some(code => !code)) return { code: null, label: "Страна IP не определена" };
  const code = codes[0]!;
  let name = code;
  try { countryNames ||= new Intl.DisplayNames(["ru"], { type: "region" }); name = countryNames.of(code) || code; } catch { /* The ISO code remains readable. */ }
  return { code, label: `Страна IP: ${name}` };
}

export function countryLocationsSnapshot(value: unknown, expected: string[]): { locations: CountryLocations; pending: boolean } {
  const fail = () => { throw new Error("Некорректные данные стран IP"); };
  if (!value || typeof value !== "object" || !("items" in value) || !Array.isArray(value.items) || !("pending" in value) || typeof value.pending !== "boolean") return fail();
  const locations: CountryLocations = {};
  for (const item of value.items) {
    if (!item || typeof item !== "object" || typeof item.address !== "string" || !expected.includes(item.address) || item.address in locations
      || (item.country_code !== null && (typeof item.country_code !== "string" || !/^[A-Z]{2}$/.test(item.country_code)))) return fail();
    locations[item.address] = item.country_code;
  }
  if (Object.keys(locations).length !== expected.length) return fail();
  return { locations, pending: value.pending };
}
