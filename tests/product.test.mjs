import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

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
  assert.match(manager, /doctor\)/);
  assert.match(manager, /install\)/);
  assert.match(manager, /update\)/);
  assert.match(readme, /raw\.githubusercontent\.com\/aske312\/vpsController\/stabl\/scripts\/install-panel\.sh/);
  assert.match(readme, /Возможные ошибки установки/);
  assert.match(readme, /установка/i);
  assert.equal(JSON.parse(wg).id, "wg");
  assert.equal(JSON.parse(awg).id, "awg");
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
  assert.equal(JSON.parse(packageJson).version, "1.0.0");
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
  assert.match(legalUi, /Уведомление о приватности/);
  assert.match(legalUi, /Privacy Notice/);
  assert.match(legalUi, /MIT LICENSE/);
  assert.doesNotMatch(legalUi, /EU \/ EEA|ЕС \/ ЕЭЗ|GDPR/);
  assert.match(guide, /Как понять, какой конфиг вам дали/);
  assert.match(guide, /имя-wg\.conf.*WireGuard/s);
  assert.match(guide, /имя-awg\.conf.*AmneziaWG/s);
  assert.match(guide, /параметры обфускации.*Jc.*Jmin.*Jmax/s);
  assert.match(guide, /одно приложение для WG и AWG/i);
  assert.match(guide, /AmneziaWG.*storage\.googleapis\.com\/amnezia\/amnezia\.org/s);
  assert.doesNotMatch(guide, /wireguard\.com\/install/);
  assert.match(guideUi, /How to create and share a new connection/);
  assert.match(guideUi, /Одно подключение — один ключ/);
  assert.match(guideUi, /Каждое подключение создаётся для одного конкретного устройства/);
  assert.match(guideUi, /Передайте конфигурацию/);
  assert.doesNotMatch(guideUi, /PROTOCOL INSTRUCTIONS/);
  assert.match(guideUi, /storage\.googleapis\.com\/amnezia\/amnezia\.org/);
  assert.match(page, /QRCode\.toDataURL\(generated, \{ errorCorrectionLevel: "L", margin: 4, width: 768 \}\)/);
  assert.match(page, /Отсканируйте код в приложении/);
  assert.doesNotMatch(page, /Показать техническое содержимое/);
  assert.doesNotMatch(page, /Копировать содержимое/);
  assert.match(page, /clientDialog && <div className="confirmBackdrop"/);
  assert.match(page, /Новое подключение/);
  assert.match(page, /data-tooltip="Пошаговая инструкция/);
  assert.match(page, /const CLIENTS_PER_PAGE = 10/);
  assert.match(page, /visibleClients\.map/);
  assert.match(page, /Показаны \{visibleClientStart\}–\{visibleClientEnd\} из \{protocolClients\.length\}/);
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
  assert.match(page, /NETWORK DIAGNOSTICS/);
  assert.match(page, /Причины нестабильности сети и подключений/);
  assert.match(page, /toggleNetworkDiagnostics/);
  assert.match(page, /diagnosticsOpen\[tab\]/);
  assert.doesNotMatch(api, /threading\.Thread\(target=network_diagnostics/);
});

test("primary resource metrics use CPU percent and readable RAM and disk units", async () => {
  const [api, page] = await Promise.all([read("api/main.py"), read("app/page.tsx")]);
  assert.match(api, /def cpu_usage_percent/);
  assert.match(api, /"cpu_percent": cpu_percent/);
  assert.match(page, /title="CPU · СЕРВЕР".*cpu_percent/s);
  assert.match(page, /title="RAM · СЕРВЕР" value=\{bytes\(memoryUsedBytes\)\}/);
  assert.match(page, /title="ДИСК · СЕРВЕР" value=\{bytes\(diskUsedBytes\)\}/);
  assert.match(page, /свободно.*memory_available/s);
  assert.match(page, /свободно.*disk_available/s);
});

test("security distinguishes public SSH from public panel access", async () => {
  const [api, page] = await Promise.all([read("api/main.py"), read("app/page.tsx")]);
  assert.match(api, /"panel_access": \{/);
  assert.match(api, /"publicly_accessible": panel_publicly_accessible/);
  assert.match(api, /panel_access_consistent/);
  assert.match(page, /title="Доступ к панели"/);
  assert.match(page, /SSH · административный доступ/);
  assert.match(page, /открыт по согласованной политике/);
  assert.match(page, /title="Дополнительные VPN-службы"/);
  assert.match(page, /установлены отдельно и не управляются приложением/);
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
  const [page, manager] = await Promise.all([read("app/page.tsx"), read("scripts/vps-control.sh")]);
  const start = page.indexOf('{tab === "security"');
  const end = page.indexOf('{tab === "application"', start);
  const section = page.slice(start, end);
  assert.ok(start >= 0 && end > start);
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
  assert.match(page, /<h3>CORE UPDATES<\/h3>/);
  assert.match(page, /Новые записи автоматически|автообновление/);
  assert.match(page, /downloadLogs/);
  assert.match(page, /Запись и хранение журналов/);
  assert.match(page, /Не очищать автоматически/);
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

test("service mode tests main without publishing it while stabl remains the only release branch", async () => {
  const [api, page, manager, stablWorkflow] = await Promise.all([
    read("api/main.py"), read("app/page.tsx"), read("scripts/vps-control.sh"),
    read(".github/workflows/stabl-release.yml"),
  ]);
  assert.match(manager, /PRODUCTION_BRANCH="stabl"/);
  assert.doesNotMatch(manager, /SERVICE_BRANCH=/);
  assert.match(manager, /update_prebuilt_branch "\$\{PRODUCTION_BRANCH\}" "stabl-latest"/);
  assert.match(manager, /update_test_branch\(\)/);
  assert.match(manager, /archive\/refs\/heads\/main\.tar\.gz/);
  assert.match(manager, /BUILD_COMMIT="\$\{latest\}" RELEASE_VERSION="\$\{test_version\}"/);
  assert.match(page, /тестовую ветку main без публикации Release/);
  assert.match(manager, /for attempt in \$\(seq 1 48\)/);
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
  assert.match(page, /applicationVersion\.branch \|\| "stabl"/);
  assert.match(page, /setAutoRefresh\(false\)/);
  assert.match(page, /const autoRefreshAfterChange = autoRefresh/);
  assert.match(page, /setAutoRefresh\(autoRefreshAfterChange\)/);
  assert.match(page, /className=\{`autoButton \$\{autoRefresh \? "active" : ""\}`\} disabled=\{busy\}/);
  assert.doesNotMatch(page, /disabled=\{serviceModeActive\}.*Авто · выкл/);
  assert.match(page, /serviceModeActive && <button onClick=\{\(\) => void runApplicationAction\("test-update"\)\}/);
  assert.match(page, /Переход на тестовую версию/);
  assert.match(page, /application\?\.service_mode\?\.rollback_available/);
  assert.match(page, /Вернуться к рабочей версии/);
  assert.match(manager, /TEST_BACKUP_DIR="\$\{DATA_DIR\}\/test-app-backup"/);
  assert.match(manager, /restore_test_app\(\)/);
  assert.match(manager, /if \[\[ -d "\$\{TEST_BACKUP_DIR\}" \]\]; then\s+info "возврат к сохранённой стабильной версии перед выключением сервисного режима"\s+restore_test_app/s);
  assert.match(page, /Будет восстановлена стабильная версия stabl/);
  assert.match(stablWorkflow, /branches: \[stabl\]/);
  assert.match(stablWorkflow, /gh release create stabl-latest/);
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
  assert.match(page, /\["wg", "awg", "shadowsocks", "vless-reality-xhttp", "clients"\]\.includes\(tab\) \? 15000/);
  assert.doesNotMatch(page, /protocolTrafficHistory/);
  assert.match(page, /CPU · СЕРВЕР/);
  assert.match(page, /RAM · СЕРВЕР/);
  assert.match(page, /ДИСК · СЕРВЕР/);
  assert.match(page, /Всего получено/);
  assert.match(page, /Среднее \{formatValue\(primaryAverage\)\}/);
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
  assert.match(api, /"installed": bool\(service and run\("systemctl", "show", service, "--property=LoadState", "--value"\) == "loaded"\)/);
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
  assert.match(page, /службы<\/span>/);
});

test("WG and AWG modules install and uninstall independently", async () => {
  const [api, manager, wgInstall, awgInstall, wgRemove, awgRemove] = await Promise.all([
    read("api/main.py"), read("scripts/vps-control.sh"),
    read("protocol-images/wireguard/install.sh"),
    read("protocol-images/amneziawg/install.sh"),
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
  assert.match(manager, /prepare_package_manager\(\)/);
  assert.match(manager, /\n  prepare_package_manager\r?\n/);
  assert.match(manager, /dpkg --audit/);
  assert.match(manager, /DPkg::Lock::Timeout=300 -f install -y/);
  assert.match(wgInstall, /DPkg::Lock::Timeout=300/);
  assert.match(awgInstall, /DPkg::Lock::Timeout=300/);
  assert.match(wgInstall, /if ! command -v wg.*command -v wg-quick/s);
  assert.match(awgInstall, /if ! command -v awg.*command -v awg-quick.*modinfo amneziawg/s);
});

test("Shadowsocks and VLESS REALITY XHTTP are independent installable modules", async () => {
  const [api, manager, page, css, ssManifest, ssInstall, ssRemove, vlessManifest, vlessInstall, vlessRemove] = await Promise.all([
    read("api/main.py"), read("scripts/vps-control.sh"), read("app/page.tsx"), read("app/globals.css"),
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
  assert.match(vlessInstall, /XHTTP_PATH/);
  assert.doesNotMatch(vlessInstall, /source "\$\{CONFIG_DIR\}\/reality\.env"/);
  assert.match(vlessInstall, /AmbientCapabilities=CAP_NET_BIND_SERVICE/);
  assert.match(vlessInstall, /ss -H -ltn/);
  assert.match(vlessInstall, /limitFallbackUpload/);
  assert.doesNotMatch(vlessInstall + vlessRemove, /wg-quick|awg-quick|shadowsocks/);
  assert.match(api, /Literal\["wg", "awg", "shadowsocks", "vless-reality-xhttp"\]/);
  assert.match(api, /vless:\/\//);
  assert.match(api, /ss:\/\//);
  assert.match(api, /Literal\["wg", "awg", "shadowsocks", "vless-reality-xhttp"\]/);
  assert.match(api, /vps-control-shadowsocks@/);
  assert.match(api, /client_mutation_lock/);
  assert.match(api, /restore_vless_config/);
  assert.match(api, /Не удалось создать VLESS-подключение: \{stage\}/);
  assert.match(api, /"transport": "TCP \+ UDP"/);
  assert.match(manager, /SHADOWSOCKS_PORT_START/);
  assert.match(manager, /VLESS_REALITY_TARGET/);
  assert.doesNotMatch(manager, /PUBLIC_IP="\$\{PUBLIC_IP\}"/);
  assert.match(manager, /PUBLIC_IP="\$\(env_value PUBLIC_IP\)"/);
  assert.match(page, /image\.id === "shadowsocks" \? "SS" : "V"/);
  assert.match(page, /setTab\(image\.id as Protocol\)/);
  assert.match(page, /LIVE TUNNEL/);
  assert.match(page, /client\.protocol === "shadowsocks" \? "SS" : "V"/);
  assert.match(page, /tab === "shadowsocks" \|\| tab === "vless-reality-xhttp"/);
  assert.match(page, /TRANSPORT &amp; SECURITY/);
  assert.match(manager, /ReadWritePaths=-\/etc\/vps-control\.env -\/etc\/vps-control /);
  assert.match(manager, /grep -Fxq "\$\{expected\}" "\$\{SERVICE_FILE\}"/);
  assert.match(manager, /start_services\(\) \{[\s\S]*?ensure_api_write_access[\s\S]*?systemctl start/);
  assert.match(manager, /restart_services\(\) \{[\s\S]*?ensure_api_write_access[\s\S]*?systemctl restart/);
  assert.match(css, /\.protocol\.shadowsocks/);
  assert.match(css, /\.protocol\.vless-reality-xhttp/);
});

test("the interface uses one fixed visual design without personalization", async () => {
  const [page, api, css, manager] = await Promise.all([
    read("app/page.tsx"), read("api/main.py"), read("app/globals.css"), read("scripts/vps-control.sh"),
  ]);
  assert.doesNotMatch(page, /personalization|data-(?:style|palette|density|theme)/i);
  assert.doesNotMatch(api, /personalization/i);
  assert.doesNotMatch(css, /personalization|data-(?:style|palette|density|theme)|task-manager/i);
  assert.match(page, /<main className="shell">/);
  assert.match(css, /\.shell \.metricCard \{ border-left: 2px solid var\(--green\)/);
  assert.match(manager, /rm -f -- "\$\{DATA_DIR\}\/personalization\.json"/);
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
  assert.match(manager, /контрольные суммы подготовленного релиза не совпали/);
  assert.match(manager, /новый релиз не прошёл проверку; выполняется откат/);
  assert.match(manager, /install_prebuilt_release install-release "\$\{archive\}"/);
  assert.doesNotMatch(manager.match(/install_prebuilt_release\(\) \{([\s\S]*?)\n\}/)?.[1] || "", /apt-get|npm |docker (build|compose)/);
  assert.doesNotMatch(api, /Application updates require a prepared release archive/);
  assert.match(page, /runApplicationAction\("update"\)/);
  assert.match(page, /основной ветки stabl/);
  assert.match(readme, /Ручное обновление без сборки на VPS/);
  assert.match(manager, /TimeoutStopSec=15/);
  assert.match(manager, /KillMode=mixed/);
  assert.match(manager, /mv -- "\$\{INSTALL_DIR\}\/venv" "\$\{rollback\}\/venv"/);
  assert.match(manager, /install -d -m 0755 "\$\{INSTALL_DIR\}"\s+chmod 0755 "\$\{INSTALL_DIR\}"/);
});
