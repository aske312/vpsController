import assert from "node:assert/strict";
import test from "node:test";
import { readFileText, read, readUiSources, readMihomoSources, readApiSources } from "./support.mjs";

test("security distinguishes public SSH from public panel access", async () => {
  const [api, page, section] = await Promise.all([readApiSources(), readUiSources(), read("src/features/security/security-view.tsx")]);
  assert.match(api, /"panel_access": \{/);
  assert.match(api, /"publicly_accessible": panel_publicly_accessible/);
  assert.match(api, /panel_access_consistent/);
  assert.match(page, /ssh\?\.password_authentication === "no"/);
  assert.match(page, /ssh\?\.permit_root_login !== "yes"/);
  assert.match(page, /Безопасный доступ по SSH/);
  assert.match(page, /Панель ничего не закроет автоматически/);
  assert.match(page, /PasswordAuthentication no · Root только по ключу/);
  assert.match(page, /\/security\/ssh-access\/key/);
  assert.match(page, /\/security\/ssh-access\/\$\{action\}/);
  assert.match(page, /автоматический откат на 5 минут/);
  assert.match(page, /sshRollbackCountdown/);
  assert.match(page, /До автоматического восстановления настроек/);
  assert.match(page, /now >= deadline.*loadSshAccess/s);
  assert.match(section, /title="SSH  административный доступ"[\s\S]*onAction=\{openSshAdminDialog\}[\s\S]*actionLabel="Настроить"/);
  assert.match(page, /title="Доступ к панели"/);
  assert.match(page, /SSH  административный доступ/);
  assert.match(page, /title="Дополнительные VPN-службы"/);
  assert.match(page, /вне управления панели/);
});

test("VPN firewall diagnostics accept module rules and offer a persistent repair", async () => {
  const [api, page, manager] = await Promise.all([
    readApiSources(), readUiSources(), read("scripts/vps-control.sh"),
  ]);
  assert.match(api, /"iptables", "-C", "FORWARD", "-i", interface, "-j", "ACCEPT"/);
  assert.match(api, /"iptables", "-C", "FORWARD", "-o", interface, "-m", "conntrack"/);
  assert.match(api, /"vpn-firewall"/);
  assert.match(page, /<SecurityActionRow\s+ok=\{Boolean\(firewall\?\.vpn_policy_healthy\)\}/);
  assert.match(page, /fixSecurity\("vpn-firewall"\)/);
  assert.match(manager, /configure_vpn_firewall_policy\(\)/);
  assert.match(manager, /net\.ipv4\.ip_forward=1/);
  assert.match(manager, /ip -4 route show dev "\$\{interface\}" proto kernel scope link/);
  assert.match(manager, /vps-control-vpn-firewall\.service/);
  assert.match(manager, /iptables -C FORWARD/);
  assert.match(manager, /vpn-firewall\) configure_vpn_firewall_policy/);
});

test("every security posture item has a safe repair or review action", async () => {
  const [section, manager] = await Promise.all([readFileText("src/features/security/security-view.tsx"), read("scripts/vps-control.sh")]);
  assert.doesNotMatch(section, /<SecurityRow\b/);
  assert.ok((section.match(/<SecurityActionRow\b/g) || []).length >= 20);
  assert.match(section, /title="Firewall".*fixSecurity\("secure"\)/s);
  assert.match(section, /title="Версия приложения".*applicationVersion\?\.branch === "main" \? "test-update" : "update"/s);
  assert.match(section, /title="Учётные записи".*runApplicationAction\("integrity-check"\)/s);
  assert.match(section, /title="Дополнительные VPN-службы".*runApplicationAction\("network-check"\)/s);
  assert.match(manager, /apt-get -o DPkg::Lock::Timeout=300 install -y apparmor apparmor-utils auditd fail2ban unattended-upgrades ufw/);
  assert.match(manager, /configure_fail2ban\(\)/);
  assert.match(manager, /backend = systemd/);
  assert.doesNotMatch(manager, /integrity_check[\s\S]*ReadWritePaths=.*resolved\.conf\.d/);
  assert.match(manager, /net\.ipv4\.tcp_syncookies = 1/);
  assert.match(manager, /kernel\.dmesg_restrict = 1/);
  assert.match(manager, /chmod 0600 "\$\{ENV_FILE\}"/);
  assert.match(manager, /configure_firewall "panel-only"/);
  assert.doesNotMatch(manager, /systemctl (?:disable|stop).*strongswan|systemctl (?:disable|stop).*xl2tpd/);
});

test("security and services expose current logs and retention controls", async () => {
  const [api, page, manager] = await Promise.all([
    readApiSources(), readUiSources(), read("scripts/vps-control.sh"),
  ]);
  assert.match(api, /"active_connections": ssh_active_connections/);
  assert.match(api, /\["journalctl", "-r"/);
  assert.match(page, /LIVE DIAGNOSTICS/);
  assert.match(page, /securityNewLogCount/);
  assert.match(page, /downloadLogs/);
  assert.match(page, /Хранение и автоматическая очистка systemd journal/);
  assert.match(page, /Без автоочистки/);
  assert.match(manager, /configure_logging/);
  assert.match(manager, /clear_managed_logs/);
});

test("SSH key hardening is transactional and automatically rolls back", async () => {
  const [api, manager] = await Promise.all([readApiSources(), read("scripts/vps-control.sh")]);
  assert.match(api, /class SshPublicKeyInstall/);
  assert.match(api, /@app\.post\("\/api\/security\/ssh-access\/key"\)/);
  assert.match(api, /@app\.post\("\/api\/security\/ssh-access\/key\/reset"\)/);
  assert.match(api, /@app\.post\("\/api\/security\/ssh-access\/begin"\)/);
  assert.match(api, /@app\.post\("\/api\/security\/ssh-access\/confirm"\)/);
  assert.match(api, /@app\.post\("\/api\/security\/ssh-access\/rollback"\)/);
  assert.match(api, /Accepted publickey for .*re\.escape\(fingerprint\)/);
  assert.match(manager, /ssh-keygen -lf "\$\{temporary\}" -E sha256/);
  assert.match(manager, /PasswordAuthentication no\\nKbdInteractiveAuthentication no\\nPermitRootLogin prohibit-password/);
  assert.match(manager, /systemd-run --unit=vps-control-ssh-rollback --on-active=5m/);
  assert.match(manager, /SSH_ACCESS_DROPIN="\/etc\/ssh\/sshd_config\.d\/00-vps-control-admin-access\.conf"/);
  assert.match(manager, /mv "\$\{LEGACY_SSH_ACCESS_DROPIN\}" "\$\{SSH_ACCESS_DROPIN\}"/);
  assert.match(manager, /ssh_access_write_state "awaiting-confirmation"/);
  assert.match(manager, /ssh_access_write_state "hardened"/);
  assert.match(manager, /ssh_access_write_state "rolled-back"/);
  assert.match(manager, /ssh_access_reset_key\(\)/);
  assert.match(manager, /fingerprint == expected/);
  assert.match(manager, /остальные ключи не изменены/);
  assert.match(manager, /\[\[ "\$\{phase\}" == "awaiting-confirmation" \]\]/);
});

test("authentication and VPN controls preserve consistent UI states", async () => {
  const [api, page, eslint, manager] = await Promise.all([
    readApiSources(), readUiSources(), read("eslint.config.mjs"), read("scripts/vps-control.sh"),
  ]);
  assert.match(page, /async function login\(event: FormEvent\)/);
  assert.match(page, /const response = await fetch\("\/api\/overview"/);
  assert.match(page, /onSubmit=\{login\}/);
  assert.match(page, /current_password: currentAdminPassword, new_password: newAdminPassword, confirm_password: confirmAdminPassword/);
  assert.match(page, /Текущий пароль/);
  assert.match(page, /Повторите новый пароль/);
  assert.match(page, /actionLabel="Изменить пароль" alwaysAction/);
  assert.match(page, /ok && !alwaysAction/);
  assert.match(api, /hmac\.compare_digest\(payload\.current_password, ADMIN_PASSWORD\)/);
  assert.match(api, /payload\.new_password != payload\.confirm_password/);
  assert.match(api, /categories < 3/);
  assert.match(page, /runApplicationAction\("identity"\)/);
  assert.match(api, /"installed": properties\.get\("LoadState"\) == "loaded"/);
  assert.match(api, /channels = configured_panel_channels\(\)/);
  assert.match(api, /if not channels:/);
  assert.doesNotMatch(api, /for interface in \(WG_INTERFACE, AWG_INTERFACE\):\s+if not Path\(f"\/sys\/class\/net/);
  assert.match(api, /"web": \{"name": "Web 312\.net"/);
  assert.match(api, /The last active VPN cannot be stopped while panel access is VPN-only/);
  assert.match(manager, /vpn_interface_available="no"/);
  assert.match(manager, /set_env_value "CORS_ORIGINS" "\$\{vpn_origins\}"/);
  assert.doesNotMatch(manager, /ip link show "\$\{WG_INTERFACE\}"[^\n]+\|\| die[^\n]+\n\s*ip link show "\$\{AWG_INTERFACE\}"[^\n]+\|\| die/);
  assert.match(eslint, /"\.runtime\/\*\*"/);
});

test("the panel has direct private addresses inside WG and AWG tunnels", async () => {
  const [caddy, manager, api] = await Promise.all([
    read("Caddyfile"), read("scripts/vps-control.sh"), readApiSources(),
  ]);
  assert.match(caddy, /http:\/\/{WG_PANEL_ADDRESS}, http:\/\/{AWG_PANEL_ADDRESS}/);
  assert.match(manager, /ipaddress\.ip_network\(sys\.argv\[1\] or "10\.72\.0\.0\/24"\)/);
  assert.match(manager, /s\|{WG_PANEL_ADDRESS}\|\$\{wg_panel_address\}\|g/);
  assert.match(api, /vpn_urls\.append\(f"https:\/\/\{PUBLIC_DOMAIN\}"\)/);
  assert.match(api, /vpn_urls\.append\(f"http:\/\/\{address\}:/);
  assert.match(manager, /HTTPS panel via WG/);
  assert.match(manager, /HTTPS panel via AWG/);
});

test("expanded security journal stays beside the live port list", async () => {
  const [view, styles] = await Promise.all([
    read("src/features/security/security-view.tsx"), read("src/features/security/security.css"),
  ]);
  assert.match(view, /securityLogsOpen \? "open" : ""/);
  assert.match(styles, /\.securityDiagnosticsGrid \{[^}]*grid-template-columns:minmax\(0,\.88fr\) minmax\(0,1\.12fr\)[^}]*align-items:start/s);
  assert.doesNotMatch(styles, /\.securityLogs\.open\s*\{[^}]*grid-column/);
  assert.match(styles, /\.securityLogs\.open \.securityLogsBody pre\{height:clamp\(260px,42vh,520px\);overflow:auto\}/);
});

test("protected panel access uses one stable host through every configured channel", async () => {
  const [api, page, view, manager, caddy] = await Promise.all([
    readApiSources(), readUiSources(), read("src/features/application/application-view.tsx"),
    read("scripts/vps-control.sh"), read("Caddyfile"),
  ]);
  assert.match(api, /INTERNAL_PANEL_HOST = "admin\.312\.net"/);
  assert.match(api, /return f"http:\/\/\{INTERNAL_PANEL_HOST\}"/);
  assert.match(api, /"can_enable": bool\(panel_channels\)/);
  assert.match(page, /Панель будет доступна по адресу/);
  assert.match(view, /panel_access\?\.can_enable === false/);
  assert.match(manager, /configured_panel_channel_count/);
  assert.match(manager, /127\.0\.0\.1 %s # 312\.net internal panel/);
  assert.match(manager, /admin host via WG/);
  assert.match(manager, /admin host via OpenVPN/);
  assert.match(manager, /http:\/\/localhost:\$\{HTTP_PORT\}/);
  assert.match(manager, /PUBLIC_PANEL_ADDRESS/);
  assert.match(manager, /vpn_public_ip/);
  assert.match(manager, /env_value PUBLIC_IP_ENDPOINT/);
  assert.match(manager, /vpn_origin%:\$\{HTTP_PORT\}/);
  assert.match(manager, /handle \/api\/mihomo\/subscriptions\/\*/);
  assert.match(manager, /No panel UI or/);
  assert.doesNotMatch(manager, /set_config_value "\$\{INSTALL_DIR\}\/install\.conf" "ACCESS_MODE"/);
  assert.match(caddy, /http:\/\/{INTERNAL_PANEL_HOST}, http:\/\/{PUBLIC_PANEL_ADDRESS} \{/);
  assert.match(caddy, /not remote_ip 127\.0\.0\.0\/8 ::1\/128 10\.0\.0\.0\/8/);
  assert.match(api, /"systemd-run", f"--unit=\{unit\}", "--wait", "--pipe", "--collect"/);
  assert.match(api, /access_mode != "vpn" and ufw_enabled/);
  assert.match(page, /Promise\.all\(\[loadServices\(\), loadSecurity\(\)\]\)/);
});

test("VPN-only mode publishes token subscriptions without exposing Mihomo administration", async () => {
  const [manager, view, control] = await Promise.all([
    read("protocol-images/mihomo/manager.py"), readMihomoSources(), read("scripts/vps-control.sh"),
  ]);
  assert.match(manager, /result\["url"\] = f"https:\/\/\{public_domain\}\{path\}"/);
  assert.match(view, /result\.url \|\| new URL\(result\.path, window\.location\.origin\)/);
  assert.match(control, /handle \/api\/mihomo\/subscriptions\/\*/);
  assert.match(control, /respond 404/);
});

test("protected panel access follows every routable managed VPN", async () => {
  const [api, manager, securityView] = await Promise.all([
    readApiSources(), read("scripts/vps-control.sh"), read("src/features/security/security-view.tsx"),
  ]);
  assert.match(api, /panel_allowed_channels/);
  assert.match(api, /panel_allowed_channels\.add\("OpenVPN"\)/);
  assert.match(api, /panel_allowed_channels\.add\("IKEv2"\)/);
  assert.match(api, /"allowed_channels": sorted\(panel_allowed_channels\)/);
  assert.match(manager, /panel via OpenVPN/);
  assert.match(manager, /panel via IKEv2/);
  assert.match(securityView, /allowed_channels/);
  assert.doesNotMatch(securityView, /"WG \/ AWG"/);
});

test("SSH management does not start socket activation and the daemon together", async () => {
  const [api, manager] = await Promise.all([
    readApiSources(), read("scripts/vps-control.sh"),
  ]);
  assert.doesNotMatch(api, /"start",\s*"ssh\.socket",\s*"ssh\.service"/);
  assert.doesNotMatch(manager, /systemctl start ssh\.socket ssh\.service/);
  assert.match(api, /def manage_ssh_units/);
  assert.match(manager, /start_preferred_ssh/);
});

test("backend keeps sensitive technical command errors in journals", async () => {
  const [api, mihomo] = await Promise.all([
    readApiSources(),
    read("protocol-images/mihomo/manager.py"),
  ]);
  for (const backend of [api, mihomo]) {
    assert.match(backend, /PUBLIC_COMMAND_ERROR/);
    assert.match(backend, /public_http_exception_handler/);
    assert.match(backend, /Suppressed technical/);
  }
  assert.match(mihomo, /if state == "failed":[\s\S]*message = PUBLIC_COMMAND_ERROR/);
});

test("channel DNS follows installed protected channels and security lives under system", async () => {
  const navigation = await read("src/control-panel/components/gate-navigation.tsx");
  assert.match(navigation, /import \{ directProtocolOrder \} from "\.\.\/\.\.\/shared\/lib\/control-plane-ui"/);
  assert.match(navigation, /const transports = directProtocolOrder/);
  assert.doesNotMatch(navigation, /const protocolOrder =/);
  assert.match(navigation, /activeTab === "channels" \|\| activeTab === "dns"/);
  assert.doesNotMatch(navigation, /label="DNS"/);
  assert.match(navigation, /<NavGroup label="SYSTEM">[\s\S]*label="Безопасность"[\s\S]*label="Приложение"/);
  assert.doesNotMatch(navigation, /<NavGroup label="INFRASTRUCTURE">/);
});
