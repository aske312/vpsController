export const routeCheck = { kind: "udp_relay", domain: "relay.example", resolved: ["203.0.113.20"], matches_origin: false,
  route: "proxy_or_cdn", status: "ready", ready: true, verification: "end_to_end", binding_id: "binding", check_id: "nonce",
  checked_at: "2026-10-12T10:00:00Z", desired_revision: 1, checked_revision: 1, message: "Verified",
  consumers: [{ id: "wg", label: "WireGuard" }], verified_connection: { protocol: "wg", vantage_point: "server" } };
export const network = { detected_at: "2026-10-12T10:00:00Z", server: { name: "Fixture", public_ip: "203.0.113.1" },
  domains: [{ value: "relay.example", role: "UDP relay", source: "settings", resolved: ["203.0.113.20"], matches_origin: false, route: "proxy_or_cdn",
    dns: { provider: "Fixture", nameservers: [], records: { a: ["203.0.113.20"], aaaa: [], cname: [] }, source: "DNS" },
    edge: { provider: "Unknown", confidence: "unknown", cnames: [], source: "DNS" } }],
  route: { mode: "proxy_or_cdn", label: "Relay", evidence: [] }, tls: { mode: "ACME", certificate_source: "Caddy", https_expected: true },
  edge: { provider: "Unknown", mode: "proxy/CDN", evidence: [] }, access: { mode: "external", panel_url: "https://panel.example", direct_url: "", protected_url: "" },
  listeners: [{ port: 443, protocol: "tcp", process: "Caddy" }], resolvers: ["1.1.1.1"],
  transport_endpoints: { revision: "revision-1", cdn_domain: "", tls_relay_domain: "", udp_relay_domain: "relay.example", udp_relay_domains: ["relay.example"] },
  transport_endpoint_checks_by_domain: { "udp_relay:relay.example": routeCheck }, capabilities: { checks: [] } };
export const dns = { settings: { revision: "revision-1", selected_id: "cloudflare", apply_wg: true, apply_awg: false, apply_shadowsocks: false,
  apply_vrx: false, prefer_encrypted: true, fallback_enabled: false, fallback_id: null, apply_system: false, custom: null },
  providers: [{ id: "cloudflare", name: "Cloudflare", country: "Global", addresses: ["1.1.1.1"], filter: "None", doh_url: "https://cloudflare-dns.com/dns-query" },
    { id: "google", name: "Google", country: "Global", addresses: ["8.8.8.8"], filter: "None" }],
  protocol_effect: { wg: "1.1.1.1" }, protocol_effect_details: { wg: { installed: true, value: "1.1.1.1", scope: "new_profiles", changes_existing: false, matches_selected: true } },
  system: { addresses: ["1.1.1.1"], source: "resolved", managed: true } };
