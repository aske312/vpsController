import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

const readFileText = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");
const readUiSources = async () => {
  const files = await readdir(new URL("../app", import.meta.url), { recursive: true });
  const sources = files
    .map((path) => String(path).replaceAll("\\", "/"))
    .filter((path) => /\.(?:ts|tsx)$/.test(path))
    .map((path) => readFileText(`app/${path}`));
  return (await Promise.all(sources)).join("\n");
};
// Product assertions intentionally inspect the complete UI surface. Keeping the
// legacy page.tsx alias here makes those assertions resilient to view extraction.
const read = (path) => path === "app/page.tsx" ? readUiSources() : readFileText(path);
const STYLE_FILES = [
  "app/globals.css",
  "app/styles/theme.css",
  "app/styles/base.css",
  "app/styles/app.css",
  "app/styles/pages/auth.css",
  "app/styles/pages/overview.css",
  "app/styles/pages/dns.css",
  "app/styles/pages/security.css",
  "app/styles/pages/application.css",
  "app/styles/pages/services.css",
  "app/styles/pages/connections.css",
  "app/styles/pages/protocols.css",
  "app/styles/pages/mihomo.css",
  "app/styles/pages/users.css",
  "app/styles/polish.css",
  "app/styles/control-center.css",
];
const readStyles = async () => (await Promise.all(STYLE_FILES.map(read))).join("\n");

test("поставка содержит установщик, образы и русскую документацию", async () => {
  const [bootstrap, manager, readme, wg, awg] = await Promise.all([
    read("scripts/install-panel.sh"),
    read("scripts/vps-control.sh"),
    read("README.md"),
    read("protocol-images/wireguard/manifest.json"),
    read("protocol-images/amneziawg/manifest.json"),
  ]);
  assert.match(bootstrap, /archive\/refs\/heads\/\$\{BRANCH\}\.tar\.gz/);
  assert.match(bootstrap, /DPkg::Lock::Timeout=300/);
  assert.match(bootstrap, /ID=\(ubuntu\|debian\)/);
  assert.match(bootstrap, /SCRIPT_PATH="\$\{BASH_SOURCE\[0\]:-\}"/);
  assert.match(bootstrap, /VPS_CONTROL_PREFLIGHT_ONLY/);
  assert.match(bootstrap, /--domain DOMAIN/);
  assert.match(bootstrap, /export VPS_CONTROL_PUBLIC_DOMAIN=/);
  assert.match(bootstrap, /valid_domain/);
  assert.match(bootstrap, /bash -s -- \[параметры\]/);
  assert.match(bootstrap, /--location-city CITY/);
  assert.match(bootstrap, /VPS_CONTROL_SERVER_COUNTRY_CODE/);
  assert.match(manager, /set_env_value "SERVER_CITY_OVERRIDE"/);
  assert.match(manager, /set_env_value "SERVER_COUNTRY_OVERRIDE"/);
  assert.match(manager, /GEOLOCATION_TERTIARY_URL="https:\/\/ip\.guide"/);
  assert.match(manager, /\/run\/cloud-init\/instance-data\.json/);
  assert.match(manager, /code_votes = Counter/);
  assert.match(manager, /if votes < 2/);
  assert.match(manager, /if city_count >= 2/);
  assert.match(manager, /MAX_CITY_CLUSTER_KM = 50\.0/);
  assert.match(manager, /def distance_km\(first, second\)/);
  assert.match(manager, /distance_km\(matching\[left\]\[4\], matching\[right\]\[4\]\) <= MAX_CITY_CLUSTER_KM/);
  assert.match(manager, /Upgrade the legacy ipwho/);
  assert.match(manager, /country_code,latitude,longitude/);
  assert.match(bootstrap, /command -v vps-control/);
  assert.match(bootstrap, /vps-control update/);
  assert.match(manager, /doctor\)/);
  assert.match(manager, /install\)/);
  assert.match(manager, /update\)/);
  assert.match(manager, /ubuntu:22\.04\|ubuntu:24\.04\|ubuntu:26\.04/);
  assert.match(manager, /debian:12\|debian:13/);
  assert.match(manager, /check_supported_os/);
  assert.match(manager, /distro_node_packages=\(nodejs npm\)/);
  assert.match(manager, /command -v node.*command -v npm[\s\S]*distro_node_packages=\(nodejs\)/);
  assert.match(manager, /Candidate:\/ && !found/);
  assert.match(manager, /Node\.js \$\{node_major\} доступен в репозитории Ubuntu/);
  assert.match(manager, /VPS_CONTROL_ADMIN_USER/);
  assert.match(manager, /VPS_CONTROL_ADMIN_PASSWORD/);
  assert.match(manager, /printf 'Пароль: '; env_value ADMIN_PASSWORD/);
  assert.match(manager, /show_credentials/);
  assert.match(manager, /VPS_CONTROL_PUBLIC_DOMAIN/);
  assert.match(manager, /VPS_CONTROL_ACCESS_MODE/);
  assert.match(manager, /VPS_CONTROL_HTTP_PORT/);
  assert.match(manager, /VPS_CONTROL_VLESS_PORT/);
  assert.match(manager, /od -An -N18 -tx1 \/dev\/urandom/);
  assert.match(manager, /tail -n 1 \| tr -d '\\r'/);
  assert.match(manager, /--retry 10 --retry-connrefused --retry-delay 2/);
  assert.match(manager, /value="\$\{value:1:\$\{#value\}-2\}"/);
  assert.match(manager, /--retry 18 --retry-all-errors --retry-delay 5/);
  assert.match(readme, /Ubuntu Server 22\.04, 24\.04, 26\.04 или Debian 12\/13/);
  assert.match(readme, /apt-get install -y ca-certificates curl/);
  assert.match(readme, /raw\.githubusercontent\.com\/aske312\/vpsController\/stabl\/scripts\/install-panel\.sh/);
  assert.match(readme, /Отдельно запускать `vps-control update` не требуется/);
  assert.match(readme, /Возможные ошибки установки/);
  assert.match(readme, /установка/i);
  assert.equal(JSON.parse(wg).id, "wg");
  assert.equal(JSON.parse(awg).id, "awg");
});

test("protocol installers declare OS support and keep per-module diagnostics", async () => {
  const [manager, awgInstall, awgRemove, wgInstall, wgRemove, ...manifests] = await Promise.all([
    read("scripts/vps-control.sh"),
    read("protocol-images/amneziawg/install.sh"),
    read("protocol-images/amneziawg/uninstall.sh"),
    read("protocol-images/wireguard/install.sh"),
    read("protocol-images/wireguard/uninstall.sh"),
    read("protocol-images/wireguard/manifest.json"),
    read("protocol-images/amneziawg/manifest.json"),
    read("protocol-images/shadowsocks/manifest.json"),
    read("protocol-images/vless-reality-xhttp/manifest.json"),
  ]);
  for (const manifest of manifests.map(JSON.parse)) {
    assert.deepEqual(manifest.supported_os, ["ubuntu", "debian"]);
  }
  assert.match(manager, /apt-get -o DPkg::Lock::Timeout=300 check/);
  assert.match(manager, /protocol-\$\{image_id\}\.log/);
  assert.match(manager, /write_action_status "failed" "\$\{ACTION_PROGRESS\}"/);
  assert.match(manager, /installer_status=\$\?/);
  assert.match(manager, /installer_status\}" -eq 75/);
  assert.match(awgInstall, /"\$\{ID\}" == "ubuntu"/);
  assert.match(awgInstall, /add-apt-repository -y ppa:amnezia\/ppa/);
  assert.match(awgInstall, /signed-by=\/usr\/share\/keyrings\/amnezia-ppa\.gpg/);
  assert.match(awgInstall, /75C9DD72C799870E310542E24166F2C257290828/);
  assert.match(awgInstall, /readlink -f \/vmlinuz/);
  assert.match(awgInstall, /headers are ready\. Reboot the VPS once/);
  assert.match(awgInstall, /QUICK_CONFIG="\/etc\/amnezia\/\$\{AWG_INTERFACE\}\.conf"/);
  assert.match(awgInstall, /value="\$\{value:1:\$\{#value\}-2\}"/);
  assert.match(wgInstall, /value="\$\{value:1:\$\{#value\}-2\}"/);
  assert.match(wgRemove, /WG_SUBNET="\$\(env_value WG_SUBNET\)"/);
  assert.match(awgRemove, /AWG_SUBNET="\$\(env_value AWG_SUBNET\)"/);
  assert.match(awgRemove, /amnezia-ppa\.list/);
});

test("protocol catalog distinguishes installable modules from safe placeholders", async () => {
  const ids = ["wireguard", "amneziawg", "shadowsocks", "vless-reality-xhttp", "mihomo", "hysteria2", "ikev2", "openvpn", "trojan"];
  const manifests = await Promise.all(ids.map((id) => read(`protocol-images/${id}/manifest.json`).then(JSON.parse)));
  for (const manifest of manifests.slice(0, 5)) {
    assert.equal(manifest.installable, true);
    assert.deepEqual(manifest.supported_os, ["ubuntu", "debian"]);
    assert.ok(Array.isArray(manifest.preflight_packages));
    assert.ok(manifest.minimum_free_mb >= 128);
    assert.equal(typeof manifest.requires_kernel_headers, "boolean");
  }
  for (const [index, manifest] of manifests.slice(5).entries()) {
    assert.equal(manifest.installable, false);
    assert.equal(manifest.installer, "install.sh");
    assert.equal(manifest.uninstaller, "uninstall.sh");
    const directory = ids[index + 5];
    const [install, uninstall] = await Promise.all([
      read(`protocol-images/${directory}/install.sh`),
      read(`protocol-images/${directory}/uninstall.sh`),
    ]);
    assert.match(install, /exit 2/);
    assert.match(uninstall, /удаление не требуется/i);
  }
  const api = await read("api/main.py");
  assert.match(api, /manifest\.get\("installable", True\) is True/);
  assert.match(api, /Protocol image is not available for installation/);
  const [manager, mihomoManager, ...mihomoManifests] = await Promise.all([
    read("scripts/vps-control.sh"),
    read("protocol-images/mihomo/manager.py"),
    ...["transport-awg", "transport-wg", "transport-shadowsocks", "transport-reality"]
      .map((id) => read(`protocol-images/mihomo/modules/${id}/manifest.json`).then(JSON.parse)),
  ]);
  assert.match(manager, /preflight_protocol_image "\$\{manifest\}"/);
  assert.match(manager, /shutil\.disk_usage\("\/opt"\)/);
  assert.match(mihomoManager, /def preflight_module/);
  assert.match(mihomoManager, /apt-get", "-o", "DPkg::Lock::Timeout=300", "check"/);
  for (const manifest of mihomoManifests) {
    assert.deepEqual(manifest.supported_os, ["ubuntu", "debian"]);
    assert.ok(Array.isArray(manifest.preflight_packages));
    assert.ok(manifest.minimum_free_mb >= 128);
    assert.equal(typeof manifest.requires_kernel_headers, "boolean");
  }
});

test("control surfaces share compact headers, telemetry and modal language", async () => {
  const [styles, overview, versions] = await Promise.all([
    readStyles(), read("app/views/overview/overview-view.tsx"), read("app/lib/format-version.ts"),
  ]);
  assert.match(overview, /overviewNodeWorkspace/);
  assert.match(styles, /grid-template-columns:minmax\(0,8fr\) minmax\(260px,2fr\)/);
  assert.match(styles, /\.gateMastMetric/);
  assert.match(styles, /\.confirmBackdrop,.accessBetaModalBackdrop,.mihomoDialogBackdrop,.legalBackdrop/);
  assert.match(versions, /slice\(0, 3\)/);
});

test("operational pages keep their artwork, 70/30 workspace and real country flags", async () => {
  const [workspace, styles] = await Promise.all([
    read("app/components/layout/app-workspace.tsx"), readStyles(),
  ]);
  for (const asset of ["overview.webp", "network_1.webp", "security.webp", "services.webp", "application.webp", "mihomo.webp"]) {
    assert.match(styles, new RegExp(asset.replace(".", "\\.")));
  }
  assert.match(styles, /\.overviewNodeWorkspace\s*\{[\s\S]*?grid-template-columns:minmax\(0,8fr\) minmax\(260px,2fr\)/);
  assert.match(workspace, /function CountryFlag/);
  assert.match(workspace, /<svg viewBox="0 0 27 18"/);
  assert.match(workspace, /country === "nl"/);
  assert.match(workspace, /country === "lv"/);
  assert.match(workspace, /country === "ru"/);
});

test("Mihomo transports automatically provision DNS and routing policies", async () => {
  const [manager, page, styles, dnsManifest, routingManifest] = await Promise.all([
    read("protocol-images/mihomo/manager.py"),
    read("app/views/mihomo/mihomo-view.tsx"),
    read("app/styles/pages/mihomo.css"),
    read("protocol-images/mihomo/modules/dns-private/manifest.json").then(JSON.parse),
    read("protocol-images/mihomo/modules/routing-policy/manifest.json").then(JSON.parse),
  ]);
  assert.match(manager, /def ensure_policy_settings/);
  assert.match(manager, /atomic_json\(DNS_SETTINGS_FILE, dns_defaults\(\)\)/);
  assert.match(manager, /atomic_json\(ROUTING_SETTINGS_FILE, routing_defaults\(\)\)/);
  assert.match(manager, /ensure_policy_settings\(\)[\s\S]+value\["modules"\]\[module_id\] = True/);
  assert.match(manager, /@app\.patch\("\/api\/mihomo\/routing\/settings"/);
  assert.match(manager, /routing = \{\*\*routing_settings\(\), \*\*item\.get\("routing", \{\}\)\}/);
  assert.match(page, /request\("\/mihomo\/dns\/settings"\)/);
  assert.match(page, /request\("\/mihomo\/routing\/schema"\)/);
  assert.match(page, /<PolicyPanel[\s\S]+code="DNS"/);
  assert.match(page, /DNS и маршрутизация Mihomo готовы/);
  assert.match(styles, /\.mihomoPolicyPanel/);
  for (const policy of [dnsManifest, routingManifest]) {
    assert.equal(policy.installable, false);
    assert.equal(policy.automatic, true);
    assert.equal(policy.settings_only, true);
  }
});

test("интерфейс и метаданные относятся к продукту 312.net", async () => {
  const [layout, page, packageJson] = await Promise.all([
    read("app/layout.tsx"),
    read("app/page.tsx"),
    read("package.json"),
  ]);
  assert.match(layout, /312\.net/);
  assert.match(page, /Безопасность/);
  assert.equal(JSON.parse(packageJson).name, "312-net-control");
  assert.doesNotMatch(`${layout}\n${page}`, /ChatGPT|Starter Project|Codex/i);
  assert.match(page, /NEXT_PUBLIC_APP_VERSION \|\| "v1\.0\.0"/);
  assert.match(JSON.parse(packageJson).version, /^\d+\.\d+\.\d+$/);
});

test("MIT license, privacy notice and connection guide are included and exposed in RU and EN", async () => {
  const [privacy, terms, legalUi, guide, guideUi, page] = await Promise.all([
    read("docs/PRIVACY_POLICY.md"),
    read("docs/TERMS_OF_USE.md"),
    read("app/legal.tsx"),
    read("docs/CONNECTION_GUIDE.md"),
    read("app/connection-guide.tsx"),
    read("app/page.tsx"),
  ]);
  assert.match(privacy, /Уведомление о приватности 312\.net/);
  assert.match(privacy, /Privacy Notice/);
  assert.match(terms, /Свободная лицензия и условия 312\.net/);
  assert.match(terms, /Free Software Terms/);
  assert.match(privacy, /не требует указания имени, адреса/);
  assert.match(privacy, /does not require an author.s legal name/);
  assert.match(terms, /лицензии MIT/);
  assert.match(terms, /MIT License/);
  assert.match(legalUi, /Приватность \/ Privacy/);
  assert.match(legalUi, /Privacy/);
  assert.match(legalUi, /Лицензия \/ License/);
  assert.doesNotMatch(legalUi, /EU \/ EEA|ЕС \/ ЕЭЗ|GDPR/);
  assert.match(guide, /Как понять, какой конфиг вам дали/);
  assert.match(guide, /имя-wg\.conf.*WireGuard/s);
  assert.match(guide, /имя-awg\.conf.*AmneziaWG/s);
  assert.match(guide, /параметры обфускации.*Jc.*Jmin.*Jmax/s);
  assert.match(guide, /одно приложение для WG и AWG/i);
  assert.match(guide, /AmneziaWG.*storage\.googleapis\.com\/amnezia\/amnezia\.org/s);
  assert.doesNotMatch(guide, /wireguard\.com\/install/);
  assert.match(guideUi, /Sharing a new connection/);
  assert.match(page, /Одно подключение соответствует одному устройству и отдельному ключу/);
  assert.match(guideUi, /Create a separate connection with a clear device name/);
  assert.match(guideUi, /Передайте конфигурацию/);
  assert.doesNotMatch(guideUi, /PROTOCOL INSTRUCTIONS/);
  assert.match(guideUi, /storage\.googleapis\.com\/amnezia\/amnezia\.org/);
  assert.match(page, /QRCode\.toDataURL\(generated, \{ errorCorrectionLevel: "L", margin: 4, width: 768 \}\)/);
  assert.match(page, /Откройте клиент протокола на устройстве и отсканируйте код/);
  assert.doesNotMatch(page, /Показать техническое содержимое/);
  assert.doesNotMatch(page, /Копировать содержимое/);
  assert.match(page, /clientDialog && <div className="confirmBackdrop"/);
  assert.match(page, /Новое подключение/);
  assert.match(page, /Скачать гайд PDF/);
  assert.match(page, /const CLIENTS_PER_PAGE = 10/);
  assert.match(page, /visibleClients\.map/);
  assert.match(page, /\{visibleClientStart\}–\{visibleClientEnd\} из \{protocolClients\.length\}/);
  assert.match(page, /connection-guide-wg-awg\.pdf/);
  assert.match(page, /installedProtocols\.length > 0/);
  assert.match(page, /waitForProtocolState/);
  assert.match(page, /2–48 символов/);
});

test("network diagnostics measure loss, jitter, MTU and server path health", async () => {
  const [api, monitor, page] = await Promise.all([
    read("api/main.py"),
    read("scripts/vpn-monitor-sample"),
    read("app/page.tsx"),
  ]);
  assert.match(monitor, /ping -n -q -c 5/);
  assert.match(monitor, /ping_1_1_1_jitter/);
  assert.match(monitor, /uplink_rx_dropped/);
  assert.match(monitor, /conntrack_count/);
  assert.match(api, /def network_diagnostics/);
  assert.match(api, /Path MTU/);
  assert.match(api, /diagnostics\/check/);
  assert.match(page, /DIAGNOSTICS/);
  assert.match(page, /Диагностика и события/);
  assert.match(page, /toggleNetworkDiagnostics/);
  assert.match(page, /diagnosticsOpen\[protocolTab\]/);
  assert.doesNotMatch(api, /threading\.Thread\(target=network_diagnostics/);
});

test("primary resource metrics use CPU percent and readable RAM and disk units", async () => {
  const [api, page] = await Promise.all([read("api/main.py"), read("app/page.tsx")]);
  assert.match(api, /def cpu_usage_percent/);
  assert.match(api, /"cpu_percent": cpu_percent/);
  assert.match(page, /label="CPU"[\s\S]*cpu_percent/);
  assert.match(page, /label="MEMORY"[\s\S]*bytes\(memoryUsedBytes\)/);
  assert.match(page, /label="DISK USED"[\s\S]*bytes\(diskUsedBytes\)/);
  assert.match(page, /bytes\(memoryFree\)/);
  assert.match(page, /bytes\(diskFree\)/);
});

test("security distinguishes public SSH from public panel access", async () => {
  const [api, page] = await Promise.all([read("api/main.py"), read("app/page.tsx")]);
  assert.match(api, /"panel_access": \{/);
  assert.match(api, /"publicly_accessible": panel_publicly_accessible/);
  assert.match(api, /panel_access_consistent/);
  assert.match(page, /title="Доступ к панели"/);
  assert.match(page, /SSH  административный доступ/);
  assert.match(page, /title="Дополнительные VPN-службы"/);
  assert.match(page, /вне управления панели/);
});

test("VPN firewall diagnostics accept module rules and offer a persistent repair", async () => {
  const [api, page, manager] = await Promise.all([
    read("api/main.py"), read("app/page.tsx"), read("scripts/vps-control.sh"),
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
  const [section, manager] = await Promise.all([readFileText("app/views/security/security-view.tsx"), read("scripts/vps-control.sh")]);
  assert.doesNotMatch(section, /<SecurityRow\b/);
  assert.ok((section.match(/<SecurityActionRow\b/g) || []).length >= 20);
  assert.match(section, /title="Firewall".*fixSecurity\("secure"\)/s);
  assert.match(section, /title="Версия приложения".*applicationVersion\?\.branch === "main" \? "test-update" : "update"/s);
  assert.match(section, /title="Учётные записи".*runApplicationAction\("integrity-check"\)/s);
  assert.match(section, /title="Дополнительные VPN-службы".*runApplicationAction\("network-check"\)/s);
  assert.match(manager, /apt-get -o DPkg::Lock::Timeout=300 install -y apparmor apparmor-utils auditd fail2ban unattended-upgrades ufw/);
  assert.match(manager, /net\.ipv4\.tcp_syncookies = 1/);
  assert.match(manager, /kernel\.dmesg_restrict = 1/);
  assert.match(manager, /chmod 0600 "\$\{ENV_FILE\}"/);
  assert.match(manager, /configure_firewall "panel-only"/);
  assert.doesNotMatch(manager, /systemctl (?:disable|stop).*strongswan|systemctl (?:disable|stop).*xl2tpd/);
});

test("security and services expose current logs and retention controls", async () => {
  const [api, page, manager] = await Promise.all([
    read("api/main.py"), read("app/page.tsx"), read("scripts/vps-control.sh"),
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

test("service settings are staged, saved explicitly and survive background refresh", async () => {
  const [api, page, manager] = await Promise.all([
    read("api/main.py"), read("app/page.tsx"), read("scripts/vps-control.sh"),
  ]);
  assert.match(api, /"cleanup": \{"enabled": False/);
  assert.match(api, /LOG_RETENTION_DAYS", "30"/);
  assert.match(api, /INSTALL_DIR \/ "scripts" \/ "vps-control\.sh"/);
  assert.match(page, /automationDraft/);
  assert.match(page, /loggingDraft/);
  assert.match(page, /loggingDirty\.current/);
  assert.match(page, /Настройки записи и хранения журналов сохранены/);
  assert.match(manager, /install -m 0755 "\$\{PROJECT_DIR\}\/scripts\/vps-control\.sh" "\$\{COMMAND_PATH\}"/);
});

test("service mode deploys main from an isolated preview while stabl remains the production channel", async () => {
  const [api, page, manager, stablWorkflow] = await Promise.all([
    read("api/main.py"), read("app/page.tsx"), read("scripts/vps-control.sh"),
    read(".github/workflows/stabl-release.yml"),
  ]);
  assert.match(manager, /PRODUCTION_BRANCH="stabl"/);
  assert.doesNotMatch(manager, /SERVICE_BRANCH=/);
  assert.match(manager, /update_prebuilt_branch "\$\{PRODUCTION_BRANCH\}" "stabl-latest"/);
  assert.match(manager, /update_test_branch\(\)/);
  assert.doesNotMatch(manager, /archive\/refs\/heads\/main\.tar\.gz/);
  assert.doesNotMatch(manager, /archive\/\$\{latest\}\.tar\.gz/);
  assert.match(manager, /mktemp -d "\$\{DATA_DIR\}\/tmp\/update\.XXXXXX"/);
  assert.match(manager, /releases\/download\/main-latest\/vps-control-main\.tar\.gz/);
  assert.match(manager, /refs\/tags\/main-latest/);
  assert.match(manager, /curl --fail --location --silent --show-error --range 0-0/);
  assert.doesNotMatch(manager, /curl[^\n]*--head/);
  assert.match(manager, /release_commit.*== "\$\{latest\}"/s);
  assert.match(manager, /STABL_RELEASE_WAIT_ATTEMPTS=30/);
  assert.match(manager, /MAIN_RELEASE_WAIT_ATTEMPTS=18/);
  assert.match(manager, /for attempt in \$\(seq 1 "\$\{MAIN_RELEASE_WAIT_ATTEMPTS\}"\)/);
  assert.match(manager, /--max-time "\$\{UPDATE_DOWNLOAD_TIMEOUT\}"/);
  assert.match(manager, /подготовленный релиз не соответствует актуальной ревизии ветки \$\{branch\}/);
  assert.match(manager, /vpn-monitor\.timer vps-control-auto-reboot\.timer/);
  assert.match(manager, /"ssh_service_was_active": ssh_service == "yes"/);
  assert.match(manager, /"ssh_socket_was_active": ssh_socket == "yes"/);
  assert.match(manager, /сервисный режим включён; версия приложения не изменена/);
  assert.match(manager, /переход на тестовую версию разрешён только в сервисном режиме/);
  assert.match(api, /payload\.action in \("test-update", "test-rollback"\) and not SERVICE_MODE_FILE\.exists\(\)/);
  assert.match(api, /def expected_application_branch\(\)/);
  assert.match(api, /return "main" if SERVICE_MODE_FILE\.exists\(\) and TEST_BACKUP_DIR\.is_dir\(\) else "stabl"/);
  assert.match(api, /branch = expected_application_branch\(\)/);
  assert.match(api, /expected_branch = expected_application_branch\(\)/);
  assert.match(api, /cached\.get\("current_commit"\) != installed_commit/);
  assert.match(page, /applicationVersion\?\.branch \|\| "—"/);
  assert.match(page, /setAutoRefresh\(false\)/);
  assert.match(page, /const autoRefreshAfterChange = autoRefresh/);
  assert.match(page, /setAutoRefresh\(autoRefreshAfterChange\)/);
  assert.match(page, /className=\{`refreshControl \$\{autoRefresh \? "active" : ""\}`\}/);
  assert.match(page, /className="autoButton" disabled=\{busy\} onClick=\{onToggleAutoRefresh\}/);
  assert.match(page, /className="iconButton" onClick=\{onRefresh\}/);
  assert.doesNotMatch(page, /autoRefreshLabel/);
  assert.doesNotMatch(page, /disabled=\{serviceModeActive\}.*Авто · выкл/);
  assert.match(page, /serviceModeActive && <button onClick=\{\(\) => void runApplicationAction\("test-update"\)\}/);
  assert.match(page, /Переход на тестовую версию/);
  assert.match(page, /application\?\.service_mode\?\.rollback_available/);
  assert.match(page, /Rollback/);
  assert.match(manager, /TEST_BACKUP_DIR="\$\{DATA_DIR\}\/test-app-backup"/);
  assert.match(manager, /restore_test_app\(\)/);
  assert.match(manager, /if \[\[ -d "\$\{TEST_BACKUP_DIR\}" \]\]; then\s+info "возврат к сохранённой стабильной версии перед выключением сервисного режима"\s+restore_test_app/s);
  assert.match(page, /Будет восстановлена стабильная версия stabl/);
  assert.match(stablWorkflow, /branches: \[stabl, main\]/);
  assert.match(stablWorkflow, /gh release create stabl-latest/);
  assert.match(stablWorkflow, /gh release create main-latest/);
  assert.match(stablWorkflow, /npm run lint/);
  assert.match(stablWorkflow, /node --test tests\/product\.test\.mjs/);
});

test("main preview is built off-VPS and interrupted updates cannot report success", async () => {
  const [workflow, manager, api] = await Promise.all([
    read(".github/workflows/stabl-release.yml"),
    read("scripts/vps-control.sh"),
    read("api/main.py"),
  ]);
  const previewWorkflow = workflow.split("  preview:")[1] || "";
  assert.match(workflow, /Build main preview package/);
  assert.match(previewWorkflow, /npm run lint/);
  assert.match(previewWorkflow, /node --test tests\/product\.test\.mjs/);
  assert.match(workflow, /vps-control-main\.tar\.gz/);
  assert.match(workflow, /gh release create main-latest/);
  assert.doesNotMatch(manager, /BUILD_COMMIT="\$\{latest\}".*build-release/s);
  assert.match(manager, /rollback_interrupted_update/);
  assert.match(manager, /Update was interrupted after the application swap; restoring the previous release/);
  assert.match(manager, /UPDATE_SWAP_ACTIVE="yes"/);
  assert.match(manager, /trap 'exit 124' TERM INT/);
  assert.match(api, /--property=RuntimeMaxSec=20min/);
  assert.match(api, /--property=TimeoutStopSec=45s/);
  assert.match(api, /Операция прервана перезагрузкой/);
  assert.match(api, /int\(action\.get\("progress"/);
});

test("live monitoring uses stable low-load cadence and detailed server metrics", async () => {
  const [api, page] = await Promise.all([read("api/main.py"), read("app/page.tsx")]);
  assert.match(api, /@app\.get\("\/api\/live-status"\)/);
  assert.match(api, /include_quality=False/);
  assert.match(page, /liveRequestInFlight/);
  assert.match(page, /const LIVE_SAMPLE_SECONDS = 3/);
  assert.match(page, /const HISTORY_SAMPLES = 100/);
  assert.match(page, /setInterval\(\(\) => void loadLiveStatus\(\), LIVE_SAMPLE_SECONDS \* 1000\)/);
  assert.match(page, /tab === "overview" \? 30000/);
  assert.match(page, /\["channels", "wg", "awg", "shadowsocks", "vless-reality-xhttp", "clients"\]\.includes\(tab\) \? 15000/);
  assert.doesNotMatch(page, /protocolTrafficHistory/);
  assert.match(page, /label="CPU"/);
  assert.match(page, /label="MEMORY"/);
  assert.match(page, /label="DISK USED"/);
  assert.match(page, /TRAFFIC TOTAL/);
  assert.match(page, /<TaskGraph/);
  assert.doesNotMatch(page, /loadLiveStatus\(\), 800/);
  assert.match(page, /function reloadWithoutCache\(message: string\)/);
  assert.match(page, /target\.searchParams\.set\("_refresh", Date\.now\(\)\.toString\(\)\)/);
  assert.match(page, /window\.location\.replace\(target\.toString\(\)\)/);
  assert.match(page, /\["update", "test-update", "test-rollback", "kernel-update"\]\.includes/);
  assert.match(page, /Сервисный режим \$\{active \? "включён" : "выключен"\}\. Кэш интерфейса сброшен/);
  assert.match(page, /успешно завершено/);
});

test("log management runs bundled control safely and permits disabled retention", async () => {
  const [api, manager] = await Promise.all([read("api/main.py"), read("scripts/vps-control.sh")]);
  assert.match(api, /\["\/bin\/bash", str\(bundled_command\)/);
  assert.match(manager, /if \(\( retention > 0 \)\); then\s+printf 'MaxRetentionSec/s);
  assert.match(manager, /chmod 0755 "\$\{INSTALL_DIR\}\/scripts\/vps-control\.sh"/);
});

test("authentication and VPN controls preserve consistent UI states", async () => {
  const [api, page, eslint, manager] = await Promise.all([
    read("api/main.py"), read("app/page.tsx"), read("eslint.config.mjs"), read("scripts/vps-control.sh"),
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
  assert.match(api, /if not available_interfaces:/);
  assert.doesNotMatch(api, /for interface in \(WG_INTERFACE, AWG_INTERFACE\):\s+if not Path\(f"\/sys\/class\/net/);
  assert.match(api, /"web": \{"name": "Web 312\.net"/);
  assert.match(api, /The last active VPN cannot be stopped while panel access is VPN-only/);
  assert.match(manager, /vpn_interface_available="no"/);
  assert.match(manager, /set_env_value "CORS_ORIGINS" "\$\{vpn_origins\}"/);
  assert.doesNotMatch(manager, /ip link show "\$\{WG_INTERFACE\}"[^\n]+\|\| die[^\n]+\n\s*ip link show "\$\{AWG_INTERFACE\}"[^\n]+\|\| die/);
  assert.match(eslint, /"\.runtime\/\*\*"/);
});

test("application updates reuse unchanged components", async () => {
  const manager = await read("scripts/vps-control.sh");
  assert.match(manager, /requirements_marker="\$\{INSTALL_DIR\}\/venv\/\.requirements\.sha256"/);
  assert.match(manager, /Python-зависимости не изменились/);
  assert.match(manager, /if \[\[ -z "\$\(env_value PUBLIC_IP\)" \]\]; then\s+refresh_server_identity\s+else\s+.*configure_access/s);
  const deployBody = manager.match(/deploy\(\) \{([\s\S]*?)\n\}/)?.[1] || "";
  assert.match(deployBody, /build_web/);
  assert.match(deployBody, /install_web/);
  assert.doesNotMatch(deployBody, /compose_with_progress|force-recreate gateway/);
});

test("web and gateway run as systemd services without Docker", async () => {
  const [manager, api, page] = await Promise.all([
    read("scripts/vps-control.sh"), read("api/main.py"), read("app/page.tsx"),
  ]);
  assert.match(manager, /\$\{APP_NAME\}-web\.service/);
  assert.match(manager, /systemctl restart "\$\{APP_NAME\}-api\.service" "\$\{APP_NAME\}-web\.service" caddy\.service/);
  assert.match(manager, /caddy validate --config/);
  assert.doesNotMatch(manager, /Установка Docker|compose_with_progress/);
  assert.match(manager, /cleanup_legacy_runtime\(\)/);
  assert.match(manager, /docker volume rm vps-control_app_runtime vps-control_caddy_data vps-control_caddy_config/);
  assert.match(manager, /Docker используется посторонними контейнерами; пакеты Docker сохранены/);
  assert.match(manager, /apt-get -o DPkg::Lock::Timeout=300 purge -y "\$\{docker_packages\[@\]\}"/);
  assert.doesNotMatch(api, /docker", "compose|docker", "inspect/);
  assert.match(api, /"vps-control-web\.service"/);
  assert.match(api, /"caddy\.service"/);
  assert.match(page, /Контур служб узла/);
  assert.match(api, /"installed": properties\.get\("LoadState"\) == "loaded"/);
  assert.match(api, /"active": properties\.get\("ActiveState"\) == "active"/);
});

test("the panel has direct private addresses inside WG and AWG tunnels", async () => {
  const [caddy, manager, api] = await Promise.all([
    read("Caddyfile"), read("scripts/vps-control.sh"), read("api/main.py"),
  ]);
  assert.match(caddy, /http:\/\/{WG_PANEL_ADDRESS}, http:\/\/{AWG_PANEL_ADDRESS}/);
  assert.match(manager, /ipaddress\.ip_network\(sys\.argv\[1\] or "10\.72\.0\.0\/24"\)/);
  assert.match(manager, /s\|{WG_PANEL_ADDRESS}\|\$\{wg_panel_address\}\|g/);
  assert.match(api, /vpn_urls\.append\(f"https:\/\/\{PUBLIC_DOMAIN\}"\)/);
  assert.match(api, /vpn_urls\.append\(f"http:\/\/\{address\}:/);
  assert.match(manager, /HTTPS panel via WG/);
  assert.match(manager, /HTTPS panel via AWG/);
});

test("WG and AWG modules install and uninstall independently", async () => {
  const [api, manager, wgInstall, awgInstall, mihomoAwgInstall, wgRemove, awgRemove] = await Promise.all([
    read("api/main.py"), read("scripts/vps-control.sh"),
    read("protocol-images/wireguard/install.sh"),
    read("protocol-images/amneziawg/install.sh"),
    read("protocol-images/mihomo/modules/transport-awg/install.sh"),
    read("protocol-images/wireguard/uninstall.sh"),
    read("protocol-images/amneziawg/uninstall.sh"),
  ]);
  const baseDependencies = manager.match(/Установка системных зависимостей" apt-get install -y ([^\n]+)/)?.[1] || "";
  assert.doesNotMatch(baseDependencies, /wireguard-tools/);
  assert.match(api, /The last active VPN module cannot be removed while panel access is VPN-only/);
  assert.match(wgRemove, /delete route allow in on "\$\{WG_INTERFACE\}" out on "\$\{UPLINK_INTERFACE\}" from "\$\{WG_SUBNET\}"/);
  assert.match(awgRemove, /delete route allow in on "\$\{AWG_INTERFACE\}" out on "\$\{UPLINK_INTERFACE\}" from "\$\{AWG_SUBNET\}"/);
  assert.match(wgRemove, /99-vps-control-wireguard\.conf/);
  assert.match(awgRemove, /99-vps-control-amneziawg\.conf/);
  assert.match(api, /protocol-install/);
  assert.match(api, /AWG_CLIENT_PORT = int\(os\.getenv\("AWG_CLIENT_PORT", "53020"\)\)/);
  assert.match(api, /client_listen_port = AWG_CLIENT_PORT if payload\.protocol == "awg" else None/);
  assert.match(api, /endpoint_host = PUBLIC_IP_ENDPOINT or PUBLIC_DOMAIN_ENDPOINT or PUBLIC_ENDPOINT/);
  assert.match(manager, /prepare_package_manager\(\)/);
  assert.match(manager, /\n  prepare_package_manager\r?\n/);
  assert.match(manager, /dpkg --audit/);
  assert.match(manager, /repair_unconfigured_grub_pc/);
  assert.match(manager, /grub-pc\/install_devices multiselect/);
  assert.match(manager, /lsblk -ndo PKNAME/);
  assert.match(manager, /DPkg::Lock::Timeout=300 -f install -y/);
  assert.match(wgInstall, /DPkg::Lock::Timeout=300/);
  assert.match(awgInstall, /DPkg::Lock::Timeout=300/);
  assert.match(wgInstall, /install -y iptables wireguard-tools/);
  assert.match(awgInstall, /install -y amneziawg/);
  assert.match(manager, /AWG_PORT="51822"/);
  assert.match(awgInstall, /AWG_PORT="\$\{AWG_PORT:-51822\}"/);
  for (const installer of [awgInstall, mihomoAwgInstall]) {
    assert.match(installer, /apt-cache show "\$\{header_package\}"/);
    assert.match(installer, /\*-cloud-\$\{architecture\}/);
    assert.match(installer, /image_meta="linux-image-cloud-\$\{architecture\}"/);
    assert.match(installer, /headers_meta="linux-headers-cloud-\$\{architecture\}"/);
    assert.match(installer, /image_meta="linux-image-\$\{architecture\}"/);
    assert.match(installer, /headers_meta="linux-headers-\$\{architecture\}"/);
    assert.match(installer, /linux-generic linux-headers-generic/);
    assert.match(installer, /Reboot the VPS/);
    assert.doesNotMatch(installer, /apt-get[^\n]+"linux-headers-\$\(uname -r\)"/);
  }
});

test("Shadowsocks and VLESS REALITY XHTTP are independent installable modules", async () => {
  const [api, manager, page, css, ssManifest, ssInstall, ssRemove, vlessManifest, vlessInstall, vlessRemove] = await Promise.all([
    read("api/main.py"), read("scripts/vps-control.sh"), read("app/page.tsx"), readStyles(),
    read("protocol-images/shadowsocks/manifest.json"),
    read("protocol-images/shadowsocks/install.sh"),
    read("protocol-images/shadowsocks/uninstall.sh"),
    read("protocol-images/vless-reality-xhttp/manifest.json"),
    read("protocol-images/vless-reality-xhttp/install.sh"),
    read("protocol-images/vless-reality-xhttp/uninstall.sh"),
  ]);
  assert.equal(JSON.parse(ssManifest).id, "shadowsocks");
  assert.equal(JSON.parse(vlessManifest).id, "vless-reality-xhttp");
  assert.equal(JSON.parse(ssManifest).category, "secure-tunnels");
  assert.equal(JSON.parse(vlessManifest).category, "secure-tunnels");
  assert.match(ssInstall, /vps-control-shadowsocks@\.service/);
  assert.match(ssInstall, /shadowsocks-libev/);
  assert.match(api, /CONTROL_COMMAND, "client-firewall", action, str\(port\)/);
  assert.match(manager, /client_firewall\(\)/);
  assert.match(manager, /ufw allow "\$\{port\}\/tcp"/);
  assert.match(manager, /ufw allow "\$\{port\}\/udp"/);
  assert.doesNotMatch(ssInstall + ssRemove, /wg-quick|awg-quick/);
  assert.match(vlessInstall, /VLESS \+ REALITY \+ XHTTP/);
  assert.match(vlessInstall, /"network": "xhttp"/);
  assert.match(vlessInstall, /"security": "reality"/);
  assert.match(vlessInstall, /Password.*PublicKey/);
  assert.match(vlessInstall, /ca-certificates curl openssl unzip/);
  assert.match(vlessInstall, /github\.com\/XTLS\/Xray-core\/releases\/latest/);
  assert.match(vlessInstall, /releases\/download\/\$\{release_tag\}\/\$\{asset\}/);
  assert.match(vlessInstall, /digest_url="\$\{download_url\}\.dgst"/);
  assert.doesNotMatch(vlessInstall, /api\.github\.com/);
  assert.match(vlessInstall, /XHTTP_PATH/);
  assert.doesNotMatch(vlessInstall, /source "\$\{CONFIG_DIR\}\/reality\.env"/);
  assert.match(vlessInstall, /AmbientCapabilities=CAP_NET_BIND_SERVICE/);
  assert.match(vlessInstall, /ss -H -ltn/);
  assert.match(vlessInstall, /limitFallbackUpload/);
  assert.match(vlessInstall, /StatsService/);
  assert.match(vlessInstall, /\"listen\": \"127\.0\.0\.1:10085\"/);
  assert.match(vlessInstall, /statsUserUplink/);
  assert.match(api, /xray_user_stats/);
  assert.match(api, /service_bytes/);
  assert.match(ssInstall, /IPAccounting=true/);
  assert.match(vlessInstall, /xray" tls ping "\$\{TARGET\}"/);
  assert.match(vlessInstall, /certificate_length.*-le 3500/s);
  assert.match(vlessInstall, /TARGET.*www\.microsoft\.com:443.*www\.apple\.com:443.*TARGET="www\.intel\.com:443"/);
  assert.match(vlessInstall, /sed -i "s\|\^TARGET=\.\*\|TARGET=\$\{TARGET\}\|"/);
  assert.match(manager, /VLESS_REALITY_TARGET="www\.intel\.com:443"/);
  assert.doesNotMatch(vlessInstall + vlessRemove, /wg-quick|awg-quick|shadowsocks/);
  assert.match(api, /Literal\["wg", "awg", "shadowsocks", "vless-reality-xhttp"\]/);
  assert.match(api, /vless:\/\//);
  assert.match(api, /ss:\/\//);
  assert.match(api, /Literal\["wg", "awg", "shadowsocks", "vless-reality-xhttp"\]/);
  assert.match(api, /vps-control-shadowsocks@/);
  assert.match(api, /client_mutation_lock/);
  assert.match(api, /restore_vless_config/);
  assert.match(api, /\.config-\{client_id\}\.tmp\.json/);
  assert.match(api, /Не удалось создать VLESS-подключение: \{stage\}/);
  assert.match(api, /"transport": "TCP \+ UDP"/);
  assert.match(manager, /SHADOWSOCKS_PORT_START/);
  assert.match(manager, /VLESS_REALITY_TARGET/);
  assert.match(manager, /\(update\|test-update\)\\\./);
  assert.match(api, /def recent_xray_activity/);
  assert.match(api, /def shadowsocks_connections/);
  assert.match(api, /def shadowsocks_connection_details/);
  assert.match(api, /"type": "xhttp", "host": target_host/);
  assert.match(api, /raw byte deltas also include unauthenticated scans/);
  assert.match(api, /"rx_bps": rx_bps, "tx_bps": tx_bps/);
  assert.match(api, /"no_delay": True, "mtu": 1200/);
  assert.match(ssInstall, /config\["mtu"\] = 1200/);
  assert.match(page, /АКТИВНОСТЬ/);
  assert.doesNotMatch(manager, /PUBLIC_IP="\$\{PUBLIC_IP\}"/);
  assert.match(manager, /PUBLIC_IP="\$\(env_value PUBLIC_IP\)"/);
  assert.match(page, /protocolTab === "shadowsocks" \? "SS" : protocolTab === "vless-reality-xhttp" \? "VLESS"/);
  assert.match(page, /image\.description \|\| image\.category_name/);
  assert.match(page, /activeProtocolImage/);
  assert.match(page, /setTab\(protocol\)/);
  assert.match(page, /const channelProfiles: Record<Protocol/);
  assert.match(page, /family: "KERNEL TUNNEL"/);
  assert.match(page, /family: "STEALTH TUNNEL"/);
  assert.match(page, /family: "ENCRYPTED PROXY"/);
  assert.match(page, /family: "MODULAR TRANSPORT"/);
  assert.match(page, /client\.protocol === "shadowsocks" \? "SS" : "VLESS"/);
  assert.match(page, /if \(Boolean\(current\?\.installed\) === installed\) return;/);
  assert.match(manager.match(/install_protocol_image\(\) \{[\s\S]*?\n\}/)?.[0] || "", /ensure_api_write_access[\s\S]*?systemctl restart "\$\{APP_NAME\}-api\.service"/);
  assert.match(manager.match(/remove_protocol_image\(\) \{[\s\S]*?\n\}/)?.[0] || "", /ensure_api_write_access[\s\S]*?systemctl restart "\$\{APP_NAME\}-api\.service"/);
  assert.match(vlessManifest, /"name": "VLESS"/);
  assert.match(vlessInstall, /"\$\{candidate\}" run -test -config/);
  assert.match(vlessInstall, /mv -f -- "\$\{candidate\}" "\$\{MODULE_DIR\}\/xray"/);
  assert.match(vlessInstall, /systemctl restart vps-control-vless-reality-xhttp\.service/);
  assert.match(vlessInstall, /восстановлена предыдущая/);
  assert.match(page, /\["wg", "awg", "shadowsocks", "vless-reality-xhttp"\].*includes\(tab\)/);
  assert.match(page, /CHANNEL CONFIGURATION/);
  assert.match(manager, /ReadWritePaths=-\/etc\/vps-control\.env -\/etc\/vps-control /);
  assert.match(manager, /grep -Fxq "\$\{expected\}" "\$\{SERVICE_FILE\}"/);
  assert.match(manager, /start_services\(\) \{[\s\S]*?ensure_api_write_access[\s\S]*?systemctl start/);
  assert.match(manager, /restart_services\(\) \{[\s\S]*?ensure_api_write_access[\s\S]*?systemctl restart/);
  assert.match(css, /\.protocol\.shadowsocks/);
  assert.match(css, /\.protocol\.vless-reality-xhttp/);
});

test("the interface uses one fixed visual design without personalization", async () => {
  const [page, api, css, manager] = await Promise.all([
    read("app/page.tsx"), read("api/main.py"), readStyles(), read("scripts/vps-control.sh"),
  ]);
  assert.doesNotMatch(page, /personalization|data-(?:style|palette|density|theme)/i);
  assert.doesNotMatch(page, /gate-art\/alternatives\/security-alt\.webp/);
  assert.doesNotMatch(api, /personalization/i);
  assert.doesNotMatch(css, /personalization|data-(?:style|palette|density|theme)|task-manager/i);
  assert.match(page, /<AppWorkspace/);
  assert.match(css, /\.shell\.gateShell/);
  assert.match(css, /--status-green/);
  assert.match(manager, /rm -f -- "\$\{DATA_DIR\}\/personalization\.json"/);
});

test("protocol pages safely edit channel settings and VLESS links select HTTP2", async () => {
  const [page, api, css] = await Promise.all([
    read("app/page.tsx"), read("api/main.py"), readStyles(),
  ]);
  assert.match(api, /@app\.patch\("\/api\/protocols\/\{protocol\}\/settings"\)/);
  assert.match(api, /class ProtocolSettingsUpdate/);
  assert.match(api, /persist_tunnel_mtu/);
  assert.match(api, /XRAY_BIN, "run", "-test"/);
  assert.match(api, /originals = \{path: path\.read_bytes\(\) for path in paths\}/);
  assert.match(api, /"alpn": "h2"/);
  assert.match(api, /def configure_vless_transport/);
  assert.match(api, /def vless_client_query/);
  assert.match(api, /Literal\["xhttp", "raw", "grpc"\]/);
  assert.match(api, /values\["type"\] = "tcp"/);
  assert.match(api, /"maxConcurrency": "8-16"/);
  assert.match(api, /"xmux_concurrency"/);
  assert.match(api, /"dns", "keepalive"/);
  assert.match(api, /"loglevel", "xpadding"/);
  assert.match(api, /protocol: Literal\["wg", "awg", "shadowsocks", "vless-reality-xhttp"\]/);
  assert.match(api, /@app\.post\("\/api\/protocols\/\{protocol\}\/resources\/check"\)/);
  assert.match(api, /allow_methods=\["GET", "POST", "PUT", "PATCH", "DELETE"\]/);
  assert.match(api, /"editable_settings": editable_settings/);
  assert.match(page, /function ProtocolSettingsEditor/);
  assert.match(page, /function ProtocolSettingsPanel/);
  assert.match(page, /saveProtocolSettings/);
  assert.match(page, /method: "PATCH"/);
  assert.match(page, /CHANNEL CONFIGURATION/);
  assert.match(css, /\.protocolSettingsEditor/);
  assert.match(css, /\.protocolConfiguration/);
});

test("DNS control provides Russian resolvers, live checks and protocol application", async () => {
  const [page, api, css, manager] = await Promise.all([read("app/page.tsx"), read("api/main.py"), readStyles(), read("scripts/vps-control.sh")]);
  assert.match(page, /type Tab = "overview" \| "channels" \| "dns"/);
  assert.match(page, /onNavigate\("channels"\)/);
  assert.match(page, /onNavigate\("overview"\)/);
  assert.match(page, /onNavigate\("clients"\)/);
  assert.match(page, /onNavigate\("dns"\)/);
  assert.match(page, /onNavigate\("security"\)/);
  assert.match(page, /onNavigate\("application"\)/);
  assert.match(page, /onNavigate\("services"\)/);
  assert.match(page, /DNS POLICY/);
  assert.match(page, /Проверить все/);
  assert.match(page, /Сторонний DNS/);
  assert.match(api, /DNS_PROVIDERS = \(/);
  assert.ok((api.match(/"country": "RU"/g) || []).length >= 5);
  assert.ok((api.match(/"id": "[a-z0-9-]+", "name":/g) || []).length >= 10);
  assert.match(api, /@app\.get\("\/api\/dns"\)/);
  assert.match(api, /@app\.put\("\/api\/dns\/settings"\)/);
  assert.match(api, /@app\.post\("\/api\/dns\/check"\)/);
  assert.match(api, /def dns_wire_query/);
  assert.match(api, /env_updates\["WG_DNS"\]/);
  assert.match(api, /env_updates\["AWG_DNS"\]/);
  assert.match(api, /env_updates\["SHADOWSOCKS_DNS"\]/);
  assert.match(api, /env_updates\["VRX_DNS"\]/);
  assert.match(api, /vrx_servers\.insert\(0, selected\["doh_url"\]\)/);
  assert.match(page, /DoH для VLESS/);
  assert.match(page, /DNS самого VPS/);
  assert.match(api, /def apply_system_dns/);
  assert.match(api, /systemd-resolved/);
  assert.match(api, /restore_system_dns_state/);
  assert.doesNotMatch(api, /ENV_FILE\.with_suffix\("\.settings\.tmp"\)/);
  assert.match(api, /def apply_vrx_dns/);
  assert.match(api, /content-type: application\/dns-message/);
  assert.doesNotMatch(api, /application\/dns-json/);
  assert.match(api, /status_code = exc\.status_code if isinstance\(exc, HTTPException\) else 500/);
  assert.match(manager, /ReadWritePaths=.*-\/etc\/systemd\/resolved\.conf\.d/);
  assert.match(manager, /grep -Eq '\^ReadWritePaths=.*resolved\\\.conf\\\.d/);
  assert.match(api, /"scope": "new_profiles"/);
  assert.match(api, /"scope": "client_recommendation"/);
  assert.match(api, /"scope": "server_xray"/);
  assert.match(api, /"changes_existing": False/);
  assert.match(api, /def validate_reality_sni/);
  assert.match(api, /reality_settings\["serverNames"\] = \[supplied\["sni"\]\]/);
  assert.match(api, /persist_vrx_target\(supplied\["sni"\]\)/);
  assert.doesNotMatch(api, /urllib\.parse\.urlencode\(\{"dns": ss_dns\}\)/);
  assert.match(page, /apply_shadowsocks/);
  assert.match(page, /apply_vrx/);
  assert.match(css, /\.dnsWorkspace/);
  assert.match(css, /\.dnsApplyDock/);
});

test("installation discovers dual-stack endpoints and reserves 443 for HTTPS", async () => {
  const [manager, api, config, caddy] = await Promise.all([
    read("scripts/vps-control.sh"), read("api/main.py"), read("install.conf"), read("Caddyfile"),
  ]);
  assert.match(manager, /detect_public_endpoints\(\)/);
  assert.match(manager, /curl -6 .*api64\.ipify\.org/);
  assert.match(manager, /socket\.gethostbyaddr/);
  assert.match(manager, /socket\.getaddrinfo/);
  assert.match(manager, /set_env_value "PUBLIC_ENDPOINT"/);
  assert.match(manager, /set_env_value "PUBLIC_IP_ENDPOINT"/);
  assert.match(manager, /set_env_value "PUBLIC_DOMAIN_ENDPOINT"/);
  assert.match(manager, /set_env_value "PUBLIC_ENDPOINTS"/);
  assert.match(manager, /set_env_value "DOMAIN_ROUTE_MODE"/);
  assert.match(manager, /"direct" if expected & resolved else "cdn"/);
  assert.match(manager, /ufw allow 443\/tcp comment '312\.net HTTPS panel'/);
  assert.match(api, /PUBLIC_ENDPOINT = os\.getenv/);
  assert.match(api, /PUBLIC_IP_ENDPOINT = os\.getenv/);
  assert.match(api, /PUBLIC_ENDPOINTS = tuple/);
  assert.match(api, /PUBLIC_IP_ENDPOINT or PUBLIC_ENDPOINT/);
  assert.match(config, /HTTP_PORT="8080"/);
  assert.match(config, /VLESS_REALITY_PORT="8443"/);
  assert.match(caddy, /\{\$SITE_ADDRESS\}/);
  assert.match(caddy, /X-Content-Type-Options "nosniff"/);
  assert.match(caddy, /X-Frame-Options "DENY"/);
  assert.match(caddy, /Referrer-Policy "no-referrer"/);
  assert.doesNotMatch(caddy, /bind 0\.0\.0\.0/);
  assert.match(manager, /df -Pk \/opt/);
  assert.match(manager, /configure_firewall "panel-only"\s+verify_app/);
  assert.match(manager, /--retry 18 --retry-all-errors --retry-delay 5/);
});

test("VLESS image supports independent direct and CDN profiles", async () => {
  const [bootstrap, manager, install, uninstall, api, caddy, config, page, protocolView, protocolCss] = await Promise.all([
    read("scripts/install-panel.sh"), read("scripts/vps-control.sh"),
    read("protocol-images/vless-reality-xhttp/install.sh"),
    read("protocol-images/vless-reality-xhttp/uninstall.sh"), read("api/main.py"),
    read("Caddyfile"), read("install.conf"), read("app/page.tsx"),
    read("app/views/protocols/protocol-view.tsx"), read("app/styles/pages/protocols.css"),
  ]);
  assert.match(manager, /set_env_value "VLESS_CDN_DOMAIN"/);
  assert.match(manager, /VLESS_CDN_PORT/);
  assert.match(manager, /VLESS CDN certificate/);
  assert.doesNotMatch(bootstrap, /--vless-cdn-domain/);
  assert.match(config, /VLESS_CDN_PORT="10087"/);
  assert.match(caddy, /import \/etc\/caddy\/vps-control\.d\/\*\.caddy/);
  assert.match(install, /"tag": "vless-cdn-websocket"/);
  assert.match(install, /"listen": "127\.0\.0\.1"/);
  assert.match(install, /existing_clients_by_id/);
  assert.match(install, /saved_cdn_domain/);
  assert.match(install, /CDN_ENABLED/);
  assert.match(install, /vless-cdn\.caddy/);
  assert.match(install, /caddy validate/);
  assert.match(uninstall, /vless-cdn\.caddy/);
  assert.match(api, /def vless_reality_inbound/);
  assert.match(api, /def vless_cdn_client_query/);
  assert.match(api, /"key": "cdn_enabled"/);
  assert.match(api, /"key": "cdn_domain"/);
  assert.match(api, /def configure_vless_cdn/);
  assert.match(api, /write_vless_cdn_snippet/);
  assert.match(api, /CONTROL_COMMAND, "vless-cdn-firewall"/);
  assert.match(api, /VLESS_CDN_SNIPPET/);
  assert.match(api, /for inbound in vless_inbounds/);
  assert.match(api, /"profiles": profiles/);
  assert.match(page, /generatedProfiles/);
  assert.match(page, /profile\.id === "cdn"/);
  assert.match(manager, /configure_vless_cdn_firewall/);
  assert.match(api, /Транспорт прямого VLESS/);
  assert.match(api, /Только Direct\. После смены импортируйте Direct-профили заново/);
  assert.doesNotMatch(api, /Literal\["xhttp", "raw", "grpc", "websocket"\]/);
  assert.match(protocolView, /ПРЯМОЕ ПОДКЛЮЧЕНИЕ/);
  assert.match(protocolView, /XHTTP, RAW или gRPC/);
  assert.match(protocolView, /ДОПОЛНИТЕЛЬНЫЙ МАРШРУТ/);
  assert.match(protocolView, /CDN · TLS\/WebSocket/);
  assert.match(protocolView, /function VlessCommandCenter/);
  assert.match(protocolView, /ROUTE BLUEPRINT/);
  assert.match(protocolView, /<strong>Маршруты<\/strong>/);
  assert.match(protocolView, /vlessPulse/);
  assert.match(protocolView, /vlessOperations/);
  assert.match(protocolCss, /\.vlessSettingsGroup\.cdn/);
  assert.match(protocolCss, /\.vlessCommandHero/);
  assert.match(protocolCss, /\.vlessRouteBoard/);
  assert.match(protocolCss, /\.vlessCommandArt/);
  const controlCenterCss = await read("app/styles/control-center.css");
  assert.match(controlCenterCss, /\.shell small[^}]*font-size:11px !important[^}]*line-height:1\.5 !important/s);
  assert.match(controlCenterCss, /label small[^}]*font-size:12px !important[^}]*line-height:1\.5 !important/s);
  assert.match(controlCenterCss, /\.protocolSettingsFields label:has\(small\)[^}]*min-height:92px !important/s);
});

test("DNS and connection screens describe real effects and provide safe filtering", async () => {
  const [page, api, css] = await Promise.all([read("app/page.tsx"), read("api/main.py"), readStyles()]);
  assert.match(page, /Локальный resolver VPS настраивается автоматически/);
  assert.match(page, /DNS самого VPS будет настроен автоматически с проверкой и откатом/);
  assert.match(page, /DNS новых профилей/);
  assert.match(page, /clientProtocolFilter/);
  assert.match(page, /clientStateFilter/);
  assert.match(page, /clientSearch/);
  assert.match(page, /НЕСТАБИЛЬНО/);
  assert.match(api, /protocol_effect_details/);
  assert.match(api, /"installed": installed\["wg"\]/);
  assert.match(page, /filter\(\(\[, effect\]\) => effect\.installed\)/);
  assert.match(page, /Нет установленных протоколов/);
  assert.match(api, /matches_selected/);
  assert.match(css, /\.connectionsWorkspace/);
  assert.match(css, /\.connectionsFilters/);
});

test("connection latency labels identify the real measurement source", async () => {
  const [api, page] = await Promise.all([read("api/main.py"), read("app/page.tsx")]);
  assert.match(api, /"latency_source": "server_icmp_tunnel_ip"/);
  assert.match(page, /РАЗБРОС RTT/);
  assert.match(page, /VPS → device/);
  assert.match(page, /latency_source === "server_icmp_tunnel_ip"/);
});

test("manual releases are prebuilt and installed without Docker or package upgrades", async () => {
  const [builder, api, page, manager, readme] = await Promise.all([
    read("scripts/build-release.sh"), read("api/main.py"), read("app/page.tsx"),
    read("scripts/vps-control.sh"), read("README.md"),
  ]);
  assert.match(builder, /Release must be built on Linux/);
  assert.match(builder, /release\.sha256/);
  assert.match(builder, /npm install --include=optional/);
  assert.match(builder, /await import\('rolldown'\)/);
  assert.match(builder, /NEXT_PUBLIC_BUILD_COMMIT/);
  assert.doesNotMatch(builder, /npm install --omit=optional/);
  assert.match(manager, /install_prebuilt_release\(\)/);
  assert.match(manager, /systemctl stop "\$\{APP_NAME\}-web\.service" "\$\{APP_NAME\}-api\.service" 2>\/dev\/null \|\| true/);
  assert.match(manager, /legacy_runtime="no"/);
  assert.match(manager, /start_legacy_containers\(\)/);
  assert.match(manager, /cleanup_legacy_runtime/);
  assert.match(manager, /requirements_changed="no"/);
  assert.match(manager, /LC_ALL=C apt-cache policy nodejs/);
  assert.match(manager, /cp -a -- "\$\{INSTALL_DIR\}\/venv" "\$\{payload\}\/venv"/);
  assert.match(manager, /"\$\{candidate_python\}" -m pip install/);
  assert.doesNotMatch(manager, /Python-зависимости изменились; подготовьте полный системный релиз/);
  assert.match(manager, /контрольные суммы подготовленного релиза не совпали/);
  assert.match(manager, /новый релиз не прошёл проверку; выполняется откат/);
  assert.match(manager, /install_prebuilt_release install-release "\$\{archive\}"/);
  assert.doesNotMatch(manager.match(/install_prebuilt_release\(\) \{([\s\S]*?)\n\}/)?.[1] || "", /apt-get|npm |docker (build|compose)/);
  assert.doesNotMatch(api, /Application updates require a prepared release archive/);
  const releaseInstall = manager.match(/install_prebuilt_release\(\) \{([\s\S]*?)\n\}/)?.[1] || "";
  assert.match(releaseInstall, /PROJECT_DIR="\$\{INSTALL_DIR\}"\s+write_caddy_config/);
  assert.match(releaseInstall, /http:\/\/127\.0\.0\.1:3000\//);
  assert.match(page, /runApplicationAction\("update"\)/);
  assert.match(page, /стабильная версия stabl/);
  assert.match(readme, /Ручное обновление без сборки на VPS/);
  assert.match(manager, /TimeoutStopSec=15/);
  assert.match(manager, /KillMode=mixed/);
  assert.match(
    manager,
    /mv -- "\$\{INSTALL_DIR\}\/venv" "\$\{rollback\}\/venv"/,
  );
  assert.match(
    manager,
    /PYTHONPATH="\$\{payload\}" "\$\{candidate_python\}" -c 'import api\.main'/,
  );
  assert.match(manager, /http:\/\/127\.0\.0\.1:8000\/api\/health/);
  assert.match(manager, /install -d -m 0755 "\$\{INSTALL_DIR\}"\s+chmod 0755 "\$\{INSTALL_DIR\}"/);
});

test("installable protocol images are dispatched independently", async () => {
  const [api, manager] = await Promise.all([read("api/main.py"), read("scripts/vps-control.sh")]);
  const endpoint = api.match(/def install_protocol_image\([\s\S]*?\n\s*return action/)?.[0] || "";
  const installer = manager.match(/install_protocol_image\(\) \{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(endpoint, /if not image\.get\("installable"\)/);
  assert.match(endpoint, /CONTROL_COMMAND, "protocol-install", image_id/);
  assert.doesNotMatch(endpoint, /installed.*(?:wg|awg|mihomo|shadowsocks)/i);
  assert.match(installer, /get\("id",""\).*== "\$\{image_id\}"/s);
  assert.match(installer, /bash "\$\{image_root\}\/\$\{installer\}"/);
});

test("node components distinguish installable images and refresh real versions", async () => {
  const page = await read("app/page.tsx");
  assert.match(page, /!item\.installed && item\.installable/);
  assert.match(page, /!image\.installed && !image\.installable/);
  assert.match(page, /displayedVersion = image\.installed[\s\S]*?installedVersion[\s\S]*?image\.installable \? "АКТУАЛЬНАЯ" : "—"/);
  assert.match(page, /await loadOverview\(\)/);
});

test("Mihomo installs and updates the latest verified stable core independently", async () => {
  const [installer, manager, api, control, manifest] = await Promise.all([
    read("protocol-images/mihomo/install.sh"),
    read("protocol-images/mihomo/manager.py"),
    read("api/main.py"),
    read("scripts/vps-control.sh"),
    read("protocol-images/mihomo/manifest.json"),
  ]);
  assert.match(installer, /repos\/MetaCubeX\/mihomo\/releases\/latest/);
  assert.match(installer, /asset\.get\("digest", ""\)/);
  assert.match(installer, /sha256:\[0-9a-fA-F\]\{64\}/);
  assert.match(installer, /sha256sum -c -/);
  assert.match(installer, /candidate="\$\{CORE_DIR\}\/\.mihomo\.\$\$\.tmp"/);
  assert.match(installer, /mv -f -- "\$\{candidate\}" "\$\{CORE\}"/);
  assert.match(installer, /MIHOMO_UPDATE_ONLY/);
  assert.match(manager, /RUNTIME_CORE_BIN/);
  assert.match(api, /MIHOMO_RUNTIME_CORE_BIN/);
  assert.doesNotMatch(api, /live_update = image_id != "mihomo"/);
  assert.match(control, /MIHOMO_UPDATE_ONLY=1/);
  assert.deepEqual(JSON.parse(manifest).preflight_packages, ["ca-certificates", "curl", "gzip"]);
});

test("WG, AWG and Shadowsocks install the latest repository candidates", async () => {
  const [wg, awg, shadowsocks] = await Promise.all([
    read("protocol-images/wireguard/install.sh"),
    read("protocol-images/amneziawg/install.sh"),
    read("protocol-images/shadowsocks/install.sh"),
  ]);
  for (const [script, packageName] of [[wg, "wireguard-tools"], [awg, "amneziawg"], [shadowsocks, "shadowsocks-libev"]]) {
    assert.match(script, /apt-get -o DPkg::Lock::Timeout=300 update/);
    assert.match(script, new RegExp(`apt-get -o DPkg::Lock::Timeout=300 install -y(?: [a-z-]+)* ${packageName}`));
    assert.match(script, new RegExp(`dpkg-query -W -f='\\$\\{Version\\}' ${packageName}`));
    assert.match(script, new RegExp(`apt-cache policy ${packageName}`));
  }
  for (const [script, packageName] of [[wg, "wireguard-tools"], [awg, "amneziawg"], [shadowsocks, "shadowsocks-libev"]]) {
    assert.match(script, new RegExp(`LC_ALL=C apt-cache policy ${packageName}`));
  }
});

test("failed protocol installs roll back partial state and shared packages have owners", async () => {
  const [control, api, overview, directSs, mihomoWg, mihomoAwg, mihomoSs] = await Promise.all([
    read("scripts/vps-control.sh"),
    read("api/main.py"),
    read("app/views/overview/overview-view.tsx"),
    read("protocol-images/shadowsocks/uninstall.sh"),
    read("protocol-images/mihomo/modules/transport-wg/uninstall.sh"),
    read("protocol-images/mihomo/modules/transport-awg/uninstall.sh"),
    read("protocol-images/mihomo/modules/transport-shadowsocks/uninstall.sh"),
  ]);
  assert.match(control, /Откат частично установленного образа/);
  assert.match(control, /bash "\$\{image_root\}\/\$\{uninstaller\}"/);
  assert.match(api, /modinfo", "-F", "version", "amneziawg"/);
  assert.doesNotMatch(overview, /formatModuleVersion\(image\.available_version \|\| image\.version\)/);
  assert.match(overview, /image\.installed[\s\S]*?installedVersion[\s\S]*?image\.installable \? "АКТУАЛЬНАЯ" : "—"/);
  assert.match(directSs, /mihomo\/shadowsocks[\s\S]*?purge -y shadowsocks-libev/);
  assert.match(mihomoWg, /wireguard[\s\S]*?purge -y wireguard-tools/);
  assert.match(mihomoAwg, /amneziawg[\s\S]*?purge -y amneziawg amneziawg-tools amneziawg-dkms/);
  assert.match(mihomoSs, /vps-control\/shadowsocks[\s\S]*?purge -y shadowsocks-libev/);
});

test("successful protocol installs are immediately reachable and health-checked", async () => {
  const [manager, wg, awg, ss, vless, mihomoInstall, mihomoWg, mihomoAwg, mihomoSs, mihomoReality] = await Promise.all([
    read("scripts/vps-control.sh"),
    read("protocol-images/wireguard/install.sh"),
    read("protocol-images/amneziawg/install.sh"),
    read("protocol-images/shadowsocks/install.sh"),
    read("protocol-images/vless-reality-xhttp/install.sh"),
    read("protocol-images/mihomo/install.sh"),
    read("protocol-images/mihomo/modules/transport-wg/install.sh"),
    read("protocol-images/mihomo/modules/transport-awg/install.sh"),
    read("protocol-images/mihomo/modules/transport-shadowsocks/install.sh"),
    read("protocol-images/mihomo/modules/transport-reality/install.sh"),
  ]);
  assert.match(manager, /verify_protocol_image_ready/);
  assert.match(manager, /wait_protocol_image_ready/);
  assert.match(manager, /sleep 0\.5/);
  assert.match(manager, /Post-install health-check failed/);
  assert.match(mihomoInstall, /ss -Hltn \| grep -Eq '127\\\.0\\\.0\\\.1:8791/);
  const mihomoManager = await read("protocol-images/mihomo/manager.py");
  assert.match(mihomoManager, /"  enable: false"/);
  assert.match(mihomoManager, /mixed-port: 7890/);
  for (const installer of [wg, awg, mihomoWg, mihomoAwg]) {
    assert.match(installer, /iptables -C INPUT -p udp --dport/);
  }
  assert.match(ss, /vps-control-shadowsocks-firewall add %i/);
  assert.match(mihomoSs, /vps-control-mihomo-ss-firewall add %i/);
  for (const installer of [vless, mihomoReality]) {
    assert.match(installer, /ExecStartPre=.*iptables -C INPUT -p tcp --dport/);
  }
});

test("SSH management does not start socket activation and the daemon together", async () => {
  const [api, manager] = await Promise.all([
    read("api/main.py"), read("scripts/vps-control.sh"),
  ]);
  assert.doesNotMatch(api, /"start",\s*"ssh\.socket",\s*"ssh\.service"/);
  assert.doesNotMatch(manager, /systemctl start ssh\.socket ssh\.service/);
  assert.match(api, /def manage_ssh_units/);
  assert.match(manager, /start_preferred_ssh/);
});
