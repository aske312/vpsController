"use client";

import { useEffect, useState } from "react";
import type { NetworkRequest } from "./network-api";
import { countryLocationsSnapshot, type CountryLocations } from "./resource-country";

export function useResourceCountries(request: NetworkRequest, addresses: string[]): CountryLocations {
  const key = JSON.stringify([...new Set(addresses)].sort());
  const [locations, setLocations] = useState<CountryLocations>({});
  useEffect(() => {
    const ips = JSON.parse(key) as string[];
    if (!ips.length) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      const next: CountryLocations = {};
      let pending = false;
      try {
        for (let index = 0; index < ips.length; index += 100) {
          const batch = ips.slice(index, index + 100);
          const result = countryLocationsSnapshot(await request("/network/locations", { method: "POST", body: JSON.stringify({ addresses: batch }), signal: controller.signal }), batch);
          Object.assign(next, result.locations); pending ||= result.pending;
        }
        if (!controller.signal.aborted) setLocations(next);
      } catch { /* Optional geography must not interrupt route controls or produce alerts. */ }
      if (!controller.signal.aborted) timer = setTimeout(() => void load(), pending ? 2000 : 300000);
    }
    timer = setTimeout(() => void load(), 0);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [request, key]);
  return locations;
}
