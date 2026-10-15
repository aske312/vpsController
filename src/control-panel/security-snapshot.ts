type Row = Record<string, unknown>;
const record = (value: unknown): value is Row => Boolean(value && typeof value === "object" && !Array.isArray(value));
const string = (value: unknown) => typeof value === "string";
const bool = (value: unknown) => typeof value === "boolean";
const count = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0;
const strings = (value: unknown) => Array.isArray(value) && value.every(string);
const nullableBool = (value: unknown) => value === null || bool(value);
const nullableCount = (value: unknown) => value === null || count(value);

function fields(row: Row, keys: string[], valid: (value: unknown) => boolean, required = false) {
  if (keys.some((key) => !(row[key] === undefined && !required) && !valid(row[key]))) throw new Error("Некорректные данные полной проверки безопасности");
}

export function securitySnapshot(value: unknown): Row {
  if (!record(value)) throw new Error("Некорректное состояние безопасности");
  for (const key of ["firewall", "fail2ban", "ssh", "system", "updates", "application_version", "application_security", "listener_summary", "legacy_services"]) {
    if (!record(value[key])) throw new Error("Некорректные разделы проверки безопасности");
  }
  fields(value, ["failed_ssh_records_24h", "accepted_ssh_24h"], count, true);
  fields(value, ["listeners"], strings, true);
  fields(value, ["fail2ban_active"], nullableBool);
  const firewall = value.firewall as Row;
  fields(firewall, ["active"], nullableBool, true);
  fields(firewall, ["rules"], strings, true);
  fields(firewall, ["forwarding_enabled", "stateful_return", "vpn_policy_healthy"], bool);
  fields(firewall, ["backend", "uplink_interface"], string);
  if (firewall.panel_access !== undefined) {
    if (!record(firewall.panel_access)) throw new Error("Некорректная проверка доступа панели");
    const access = firewall.panel_access;
    fields(access, ["mode"], (value) => value === "external" || value === "vpn");
    fields(access, ["port"], count);
    fields(access, ["listening", "public_rule", "publicly_accessible", "vpn_only", "consistent"], bool);
    fields(access, ["allowed_interfaces", "allowed_channels"], strings);
    fields(access, ["internal_url"], string);
  }
  if (firewall.protocol_policies !== undefined) {
    if (!record(firewall.protocol_policies)) throw new Error("Некорректные политики протоколов");
    for (const policy of Object.values(firewall.protocol_policies)) {
      if (!record(policy)) throw new Error("Некорректная политика протокола");
      fields(policy, ["installed", "route_allowed", "return_allowed", "nat_enabled", "healthy"], bool);
    }
  }
  const fail2ban = value.fail2ban as Row;
  fields(fail2ban, ["active"], nullableBool, true);
  fields(fail2ban, ["jail_active"], nullableBool, true);
  fields(fail2ban, ["currently_banned", "total_banned"], nullableCount, true);
  const ssh = value.ssh as Row;
  fields(ssh, ["active"], nullableBool, true);
  fields(ssh, ["password_authentication", "permit_root_login"], string, true);
  fields(ssh, ["publicly_allowed"], nullableBool, true);
  fields(ssh, ["active_connections"], count);
  fields(ssh, ["listen_addresses"], strings);
  fields(ssh, ["max_auth_tries", "x11_forwarding", "tcp_forwarding"], string);
  const system = value.system as Row;
  fields(system, ["kernel"], string);
  fields(system, ["ipv4_forwarding", "syn_cookies", "rp_filter", "rp_filter_valid", "redirects_disabled", "source_route_disabled", "dmesg_restricted"], bool);
  fields(system, ["auditd_active"], nullableBool);
  fields(system, ["rp_filter_mode"], count);
  fields(system, ["sudo_users", "login_users"], strings);
  if (system.apparmor !== undefined) {
    if (!record(system.apparmor)) throw new Error("Некорректная проверка AppArmor");
    fields(system.apparmor, ["active"], bool);
    fields(system.apparmor, ["profiles"], count);
  }
  fields(value.listener_summary as Row, ["tcp", "udp", "local_only"], count);
  const updates = value.updates as Row;
  fields(updates, ["available", "security"], nullableCount);
  fields(updates, ["kernel_available"], nullableBool);
  fields(updates, ["automatic"], nullableBool);
  fields(updates, ["reboot_required", "refreshing"], bool);
  fields(updates, ["source", "error"], string);
  fields(updates, ["checked_at"], (value) => value === null || string(value));
  const version = value.application_version as Row;
  fields(version, ["branch", "current_commit", "latest_commit", "error"], string);
  fields(version, ["checked_at"], (value) => value === null || string(value));
  fields(version, ["outdated"], nullableBool);
  fields(version, ["refreshing"], bool);
  const application = value.application_security as Row;
  fields(application, ["admin_password_strong", "cors_restricted", "secrets_protected", "api_local_only", "control_command_protected"], bool);
  fields(application, ["secrets_mode", "control_command_mode"], string);
  for (const service of Object.values(value.legacy_services as Row)) {
    if (!record(service)) throw new Error("Некорректная проверка дополнительных служб");
    fields(service, ["active"], nullableBool);
    fields(service, ["enabled"], string);
  }
  const serviceUnknown = Object.values(value.legacy_services as Row).some((service) => (service as Row).active === null || (service as Row).enabled === "unknown");
  return { ...value, full_observation_failed: firewall.active === null || fail2ban.active === null || ssh.active === null || fail2ban.jail_active === null
    || system.auditd_active === null || updates.automatic === null || serviceUnknown,
    live_observation_failed: firewall.active === null || fail2ban.active === null || ssh.active === null };
}

export function securityUnavailable(current: Row | null): Row | null {
  if (!current) return null;
  return { ...current, full_observation_failed: true };
}
