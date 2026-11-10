import type { RouteEntry } from "./route-library-state";
import type { RelayStatus } from "./relay-state";
import type { NetworkIpIdentity } from "../../shared/types/control-plane";

export type ResourceConsumer = { id: string; label: string; owner?: string; retained?: boolean };
export type ResourceBinding = { address: string; kind: "cdn" | "tls_relay" | "udp_relay"; state: "active" | "retired"; consumers: ResourceConsumer[] };

const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === "object" && !Array.isArray(value));
export function resourceBindingsSnapshot(value: unknown): ResourceBinding[] {
  const fail = () => { throw new Error("Не удалось подтвердить использование ресурсов"); };
  if (!record(value) || value.schema !== 1 || !Array.isArray(value.bindings)) return fail();
  return value.bindings.map(binding => {
    if (!record(binding) || typeof binding.address !== "string" || !binding.address.trim()
      || !["cdn", "tls_relay", "udp_relay"].includes(String(binding.kind)) || !["active", "retired"].includes(String(binding.state))
      || !Array.isArray(binding.consumers)) return fail();
    const consumers = binding.consumers.map(consumer => {
      if (!record(consumer) || typeof consumer.id !== "string" || !consumer.id
        || consumer.label !== undefined && typeof consumer.label !== "string" || consumer.owner !== undefined && typeof consumer.owner !== "string") return fail();
      return { id: consumer.id, label: consumer.label || "Подключение", ...(consumer.owner ? { owner: consumer.owner } : {}) } as ResourceConsumer;
    });
    return { address: binding.address.trim().toLowerCase(), kind: binding.kind, state: binding.state, consumers } as ResourceBinding;
  });
}

export function resourceConsumers(entry: RouteEntry, bindings: ResourceBinding[] | null, relay: RelayStatus | null): ResourceConsumer[] | null {
  if (entry.kind === "relay") {
    if (!relay?.profiles) return null;
    return [...new Map(relay.profiles.flatMap(profile => profile.channels.filter(channel => channel.host_id === entry.host.id)
      .map(channel => ({ id: `${profile.id}:${channel.id}`, label: profile.name, owner: "mihomo" }))).map(item => [item.id, item])).values()];
  }
  if (!bindings) return null;
  const consumers = new Map<string, ResourceConsumer>();
  for (const binding of bindings.filter(binding => binding.address === entry.address.toLowerCase())) {
    for (const item of binding.consumers) {
      const consumer = { ...item, retained: binding.state === "retired" };
      if (!consumers.has(item.id) || !consumer.retained) consumers.set(item.id, consumer);
    }
  }
  return [...consumers.values()];
}

export function connectionCount(count: number) {
  const mod = count % 100, last = count % 10;
  return `${count} ${mod >= 11 && mod <= 14 ? "подключений" : last === 1 ? "подключение" : last >= 2 && last <= 4 ? "подключения" : "подключений"}`;
}

export function resourceUsageLabel(entry: RouteEntry, consumers: ResourceConsumer[] | null) {
  const panel = entry.kind === "domain" && /\bpanel\b/i.test(entry.domain.role);
  if (consumers === null) return panel ? "Панель · подключения неизвестны" : "Данные не получены";
  const active = consumers.filter(item => !item.retained).length;
  const archived = consumers.length - active;
  if (consumers.length) return `${panel ? "Панель · " : ""}${active ? connectionCount(active) : ""}${archived ? `${active ? " · " : ""}Архив привязок: ${archived}` : ""}`;
  if (entry.kind === "domain" && entry.domain.status === "stale") return "Сохранён в старых настройках";
  return panel ? "Панель управления" : "Нет связанных подключений";
}

export function resourceProvider(entry: RouteEntry) {
  if (entry.kind === "relay") return entry.host.version ? `Agent ${entry.host.version}` : "Версия Agent неизвестна";
  const domain = entry.domain;
  const edge = domain.edge?.confidence === "high" && domain.edge.provider !== "Unknown" ? domain.edge.provider : "";
  const providers = [...new Set((domain.ip_info || []).map(ipProvider).filter(Boolean))];
  return edge || providers.join(" · ") || "Провайдер не определён";
}

export function ipProvider(info: Pick<NetworkIpIdentity, "hoster" | "provider"> | undefined) {
  return [info?.hoster, info?.provider].find(value => value && value.toLowerCase() !== "unknown") || "";
}
