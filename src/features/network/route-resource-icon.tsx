"use client";

import { RouteTypeIcon, type RouteIconKind } from "./route-type-icon";
import type { resourceCountry } from "./resource-country";
import { CountryFlag } from "../../shared/components/country-flag";

export function RouteResourceIcon({ kind, country, cdn = false }: { kind: RouteIconKind; country?: ReturnType<typeof resourceCountry>; cdn?: boolean }) {
  if (!country) return <RouteTypeIcon kind={kind} />;
  const label = cdn ? `${country.label}. Для CDN это страна входного IP, а не скрытого сервера.` : country.label;
  return <span className="routeResourceIcon" title={label}>
    <CountryFlag code={country.code || "unknown"} label={label} fallback={<RouteTypeIcon kind={kind} />} />
  </span>;
}
