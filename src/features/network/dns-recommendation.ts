import type { DnsCheck, DnsProvider, DnsSettings } from "../../shared/types/control-plane";

type Requirements = { udp: boolean; doh: boolean };

/** Rank only compatible resolvers; never alter filtering, encryption or exceptions. */
export function recommendDns(settings: DnsSettings, providers: DnsProvider[], checks: Record<string, DnsCheck>, required: Requirements) {
  const primary = providers.find((item) => item.id === settings.selected_id);
  if (!primary || primary.id === "custom") return null;
  const backup = settings.fallback_id ? providers.find((item) => item.id === settings.fallback_id) : primary;
  if (settings.fallback_enabled && (!backup || backup.id === "custom")) return null;
  const score = (provider: DnsProvider) => {
    const check = checks[provider.id];
    if (!check || provider.id === "custom") return Infinity;
    const times: number[] = [];
    if (required.udp) {
      if (!check.udp_ok || !Number.isFinite(check.udp_ms) || check.udp_ms! < 0) return Infinity;
      times.push(check.udp_ms!);
    }
    if (required.doh) {
      if (!provider.doh_url || !check.doh_ok || !Number.isFinite(check.doh_ms) || check.doh_ms! < 0) return Infinity;
      times.push(check.doh_ms!);
    }
    return times.length ? Math.max(...times) : Infinity;
  };
  const ranked = providers.map((provider) => ({ provider, ms: score(provider) }))
    .filter((item) => Number.isFinite(item.ms))
    .sort((a, b) => a.ms - b.ms || a.provider.id.localeCompare(b.provider.id));
  const selected = ranked.find((item) => item.provider.filter === primary.filter);
  if (!selected) return null;
  const family = (id: string) => id.split("-")[0];
  const reserve = settings.fallback_enabled ? ranked.find((item) => item.provider.filter === backup?.filter && family(item.provider.id) !== family(selected.provider.id)) : undefined;
  if (settings.fallback_enabled && !reserve) return null;
  return { primary: selected.provider, backup: reserve?.provider ?? null, primary_ms: selected.ms, backup_ms: reserve?.ms ?? null };
}
