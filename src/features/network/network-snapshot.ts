import type { DnsCheck, DnsStatus, NetworkEndpointCheck, NetworkStatus } from "../../shared/types/control-plane";

type Row = Record<string, unknown>;
const record = (value: unknown): value is Row => Boolean(value && typeof value === "object" && !Array.isArray(value));
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");
const fields = (value: Row, keys: string[], type: "string" | "boolean") => keys.every((key) => typeof value[key] === type);
const optional = (value: Row, keys: string[], type: "string" | "boolean") => keys.every((key) => value[key] === undefined || typeof value[key] === type);
const oneOf = (value: unknown, values: string[]) => typeof value === "string" && values.includes(value);
const rows = (value: unknown, accept: (row: Row) => boolean) => Array.isArray(value) && value.every((row) => record(row) && accept(row));
const stringMap = (value: unknown) => record(value) && Object.values(value).every((item) => typeof item === "string");

function ipRows(value: unknown) {
  return rows(value, (row) => fields(row, ["address", "ptr", "provider", "hoster", "asn", "network", "source"], "string"));
}

export function networkEndpointSnapshot(value: unknown): NetworkEndpointCheck {
  if (!record(value) || typeof value.matches_origin !== "boolean" || typeof value.message !== "string"
    || !oneOf(value.route, ["direct", "proxy_or_cdn", "unresolved"])
    || (value.consumers !== undefined && !rows(value.consumers, (row) => fields(row, ["id", "label"], "string")))
    || (value.verified_connection !== undefined && (!record(value.verified_connection)
      || !optional(value.verified_connection, ["consumer_id", "producer", "protocol", "transport"], "string")
      || (value.verified_connection.vantage_point !== undefined && value.verified_connection.vantage_point !== "server")))) throw new Error("Некорректный результат проверки маршрута");
  return value as NetworkEndpointCheck;
}

/** Page fields supplement the shared route-evidence validation used by selectors. */
export function networkPageSnapshot(value: unknown): NetworkStatus {
  const fail = () => { throw new Error("Некорректные данные состояния сети"); };
  if (!record(value) || typeof value.detected_at !== "string") return fail();
  const server = value.server;
  if (!record(server) || !fields(server, ["name", "public_ip"], "string") || !optional(server, ["public_ipv4", "public_ipv6"], "string")
    || (server.ip_info !== undefined && !ipRows(server.ip_info))) return fail();
  if (!rows(value.domains, (row) => {
    if (!fields(row, ["value", "role", "source"], "string") || !strings(row.resolved) || typeof row.matches_origin !== "boolean"
      || !oneOf(row.route, ["direct", "proxy_or_cdn", "unresolved"]) || (row.status !== undefined && row.status !== "stale")
      || (row.endpoint_kind !== undefined && !oneOf(row.endpoint_kind, ["cdn", "tls_relay", "udp_relay"]))
      || (row.stale_usages !== undefined && !strings(row.stale_usages)) || (row.ip_info !== undefined && !ipRows(row.ip_info))) return false;
    if (row.dns !== undefined) {
      if (!record(row.dns) || !fields(row.dns, ["provider", "source"], "string") || !strings(row.dns.nameservers)) return false;
      const records = row.dns.records;
      if (records !== undefined && (!record(records) || !["a", "aaaa", "cname"].every((key) => strings(records[key])))) return false;
    }
    return row.edge === undefined || (record(row.edge) && fields(row.edge, ["provider", "source"], "string")
      && oneOf(row.edge.confidence, ["high", "unknown"]) && strings(row.edge.cnames));
  })) return fail();
  if (!record(value.route) || !oneOf(value.route.mode, ["direct", "proxy_or_cdn", "none"]) || typeof value.route.label !== "string" || !strings(value.route.evidence)
    || !record(value.tls) || !fields(value.tls, ["mode", "certificate_source"], "string") || typeof value.tls.https_expected !== "boolean"
    || !record(value.edge) || !fields(value.edge, ["provider", "mode"], "string") || !strings(value.edge.evidence)
    || !record(value.access) || !fields(value.access, ["mode", "panel_url", "direct_url", "protected_url"], "string")
    || !strings(value.resolvers) || !rows(value.listeners, (row) => Number.isInteger(row.port) && Number(row.port) >= 1 && Number(row.port) <= 65535 && fields(row, ["protocol", "process"], "string"))) return fail();
  const endpoints = value.transport_endpoints;
  if (!record(endpoints) || !fields(endpoints, ["cdn_domain", "tls_relay_domain", "udp_relay_domain"], "string") || !optional(endpoints, ["revision"], "string")
    || ["cdn_domains", "tls_relay_domains", "udp_relay_domains"].some((key) => endpoints[key] !== undefined && !strings(endpoints[key]))) return fail();
  for (const key of ["transport_endpoint_checks", "transport_endpoint_checks_by_domain"]) {
    if (value[key] === undefined) continue;
    if (!record(value[key])) return fail();
    for (const check of Object.values(value[key])) {
      networkEndpointSnapshot(check);
    }
  }
  if (value.capabilities !== undefined && (!record(value.capabilities) || !optional(value.capabilities, ["uplink"], "string")
    || !rows(value.capabilities.checks, (row) => fields(row, ["id", "label", "value", "detail"], "string") && oneOf(row.status, ["ready", "warning", "unsupported"])))) return fail();
  return value as NetworkStatus;
}

export function dnsStatusSnapshot(value: unknown): DnsStatus {
  const fail = () => { throw new Error("Некорректные данные настроек DNS"); };
  if (!record(value) || !optional(value, ["recovery_required"], "boolean")) return fail();
  const settings = value.settings;
  if (!record(settings) || typeof settings.selected_id !== "string" || !optional(settings, ["revision"], "string")
    || !fields(settings, ["apply_wg", "apply_awg", "apply_shadowsocks", "apply_vrx", "prefer_encrypted", "fallback_enabled", "apply_system"], "boolean")
    || !optional(settings, ["apply_openvpn", "apply_ikev2"], "boolean")
    || (settings.bootstrap_id !== undefined && !oneOf(settings.bootstrap_id, ["cloudflare", "google", "dns-sb"]))
    || (settings.fallback_id != null && typeof settings.fallback_id !== "string")
    || (settings.profiles !== undefined && !stringMap(settings.profiles))
    || (settings.custom != null && (!record(settings.custom) || !fields(settings.custom, ["name", "doh_url"], "string") || !strings(settings.custom.addresses)))) return fail();
  if (!rows(value.providers, (row) => fields(row, ["id", "name", "country", "filter"], "string") && strings(row.addresses) && optional(row, ["doh_url"], "string"))
    || !stringMap(value.protocol_effect)) return fail();
  const providers = value.providers as DnsStatus["providers"];
  if (providers.some((row) => !row.id) || new Set(providers.map((row) => row.id)).size !== providers.length) return fail();
  if (value.protocol_effect_details !== undefined && (!record(value.protocol_effect_details) || Object.values(value.protocol_effect_details).some((row) => !record(row)
    || !fields(row, ["installed", "changes_existing", "matches_selected"], "boolean") || typeof row.value !== "string"
    || !oneOf(row.scope, ["new_profiles", "client_recommendation", "server_xray", "server_system", "reconnect"])))) return fail();
  if (value.system !== undefined && (!record(value.system) || !strings(value.system.addresses) || typeof value.system.source !== "string" || typeof value.system.managed !== "boolean")) return fail();
  return value as DnsStatus;
}

export function dnsChecksSnapshot(value: unknown): DnsCheck[] {
  if (!record(value) || !rows(value.items, (row) => fields(row, ["id"], "string") && fields(row, ["available", "udp_ok", "tcp_ok", "doh_ok"], "boolean")
    && ["udp_ms", "tcp_ms", "doh_ms", "latency_ms"].every((key) => row[key] === undefined || (typeof row[key] === "number" && Number.isFinite(row[key]) && row[key] >= 0)))) throw new Error("Некорректный результат проверки DNS");
  const items = value.items as DnsCheck[];
  if (items.some((item) => !item.id) || new Set(items.map((item) => item.id)).size !== items.length) throw new Error("Неоднозначный результат проверки DNS");
  return items;
}
