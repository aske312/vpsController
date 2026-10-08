import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

test("поставка содержит установщик, образы и клиентскую документацию", async () => {
  const [bootstrap, manager, readme, awg, hysteria2, tuic, xray, relay] = await Promise.all([
    read("scripts/install-panel.sh"),
    read("scripts/vps-control.sh"),
    read("README.md"),
    read("protocol-images/amneziawg/manifest.json"),
    read("protocol-images/hysteria2/manifest.json"),
    read("protocol-images/tuic/manifest.json"),
    read("protocol-images/xray/manifest.json"),
    read("protocol-images/relay-agent/manifest.json"),
  ]);
  assert.match(bootstrap, /archive\/refs\/heads\/\$\{BRANCH\}\.tar\.gz/);
  assert.match(bootstrap, /DPkg::Lock::Timeout=300/);
  assert.match(manager, /df -Pk \/opt/);
  assert.match(manager, /doctor\)/);
  assert.match(manager, /install\)/);
  assert.match(manager, /update\)/);
  assert.match(readme, /raw\.githubusercontent\.com\/aske312\/vpsController\/installer\/install\.sh/);
  assert.match(readme, /Возможные ошибки установки/);
  assert.match(readme, /AmneziaWG/);
  assert.doesNotMatch(readme, /test-light|CI|Git-клон|Ручное обновление без сборки/);
  assert.match(readme, /установка/i);
  assert.equal(JSON.parse(awg).id, "awg");
  assert.equal(JSON.parse(hysteria2).id, "hysteria2");
  assert.equal(JSON.parse(tuic).id, "tuic");
  assert.equal(JSON.parse(xray).id, "xray");
  assert.equal(JSON.parse(relay).id, "relay-agent");
  assert.equal(JSON.parse(relay).installable, false);
  await assert.rejects(read("protocol-images/trojan/manifest.json"), { code: "ENOENT" });
});

test("fresh install generates credentials, supports a verified domain and finishes on stable Light", async () => {
  const [bootstrap, manager, config, caddy, readme] = await Promise.all([
    read("scripts/install-panel.sh"),
    read("scripts/vps-control.sh"),
    read("install.conf"),
    read("Caddyfile"),
    read("README.md"),
  ]);
  assert.match(manager, /generate_admin_password\(\)[\s\S]*?\/dev\/urandom/);
  assert.match(manager, /printf 'Пароль: '; env_value ADMIN_PASSWORD/);
  assert.doesNotMatch(manager, /^ADMIN_PASSWORD=".+"$/m);
  assert.doesNotMatch(config, /^ADMIN_PASSWORD=".+"$/m);
  assert.match(bootstrap, /--domain/);
  assert.match(bootstrap, /VPS_CONTROL_PUBLIC_DOMAIN/);
  assert.match(manager, /domain_points_to_public_ip/);
  assert.match(manager, /domain\) change_public_domain/);
  assert.match(manager, /PANEL_URL="https:\/\/\$\{confirmed_domain\}"/);
  assert.match(manager, /s\|:\{\\\$HTTP_PORT\}\|\$\{site_address\}\|g/);
  assert.match(caddy, /^:\{\$HTTP_PORT\}/);
  assert.match(manager, /ui_stage "Обновление до стабильной версии"\s+update_app/);
  assert.match(manager, /update_prebuilt_branch "\$\{PRODUCTION_BRANCH\}" "\$\{PRODUCTION_RELEASE_TAG\}"/);
  assert.match(readme, /--domain panel\.example\.com/);
  assert.match(readme, /vps-control domain panel\.example\.com/);
  assert.doesNotMatch(readme, /sudo/);
});

test("интерфейс использует фирменные метаданные и знак 312.net", async () => {
  const [layout, page, favicon, packageJson] = await Promise.all([
    read("app/layout.tsx"),
    read("app/page.tsx"),
    read("public/favicon.svg"),
    read("package.json"),
  ]);
  assert.match(layout, /title: "Infrastructure Control"/);
  assert.match(layout, /description: "Управление серверной инфраструктурой\."/);
  assert.match(page, /M4\.5 5\.5h23L16 27 4\.5 5\.5Z/);
  assert.match(favicon, /312\.net triangle mark/);
  assert.match(favicon, /M170 220h684L512 850 170 220Z/);
  assert.match(page, /Безопасность/);
  assert.equal(JSON.parse(packageJson).name, "312-net-control");
  assert.doesNotMatch(`${layout}\n${page}`, /ChatGPT|Starter Project|Codex/i);
  assert.match(page, /NEXT_PUBLIC_APP_VERSION \|\| "v1\.0\.0"/);
  assert.match(page, /NEXT_PUBLIC_RELEASE_BRANCH \|\| "light"/);
  for (const countryCode of ["de", "fi", "sg", "kz", "jp", "by", "es", "se", "us"]) {
    assert.match(page, new RegExp(`normalized === \\"${countryCode}\\"|${countryCode}: \\["`));
  }
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
  assert.match(legalUi, /\{branch\} \{version\} build:\{commit\.slice\(0, 18\)\}/);
  assert.doesNotMatch(legalUi, /EU \/ EEA|ЕС \/ ЕЭЗ|GDPR/);
  assert.match(guide, /Подключение с помощью AmneziaWG/);
  assert.match(guide, /Профиль уже содержит необходимые параметры обфускации/);
  assert.match(guide, /Один файл следует использовать только на одном устройстве/);
  assert.match(guideUi, /Create and connect a client/);
  assert.match(guideUi, /Параметры обфускации уже включены в профиль/);
  assert.match(guideUi, /storage\.googleapis\.com\/amnezia\/amnezia\.org/);
  assert.match(page, /installedProtocols\.length/);
  assert.match(page, /ConnectionDialog/);
  assert.match(page, /waitForProtocolState/);
  assert.match(await read("app/connection-dialog.tsx"), /От 2 до 48 символов/);
});

test("connections use a modal editor and protocol-specific one-time handoff methods", async () => {
  const [api, page, profile, dialog, inventory, packageJson] = await Promise.all([
    read("api/main.py"), read("app/page.tsx"), read("app/connection-profile.tsx"),
    read("app/connection-dialog.tsx"), read("app/connections-view.tsx"), read("package.json"),
  ]);
  assert.match(api, /def connection_profile\(/);
  assert.match(api, /hysteria2:\/\//);
  assert.match(api, /vless:\/\//);
  assert.match(api, /qr_content=client_config/);
  assert.match(api, /filename=f"\{safe_name\}-tuic\.json"/);
  assert.match(profile, /QRCode\.toDataURL/);
  assert.match(profile, /ПРОФИЛЬ СОЗДАН · ПОКАЗЫВАЕТСЯ ОДИН РАЗ/);
  assert.match(profile, /awg:.*methods: \["QR", "Файл"\]/s);
  assert.match(profile, /hysteria2:.*methods: \["QR", "Ссылка", "Файл"\]/s);
  assert.match(profile, /tuic:.*methods: \["Файл"\]/s);
  assert.match(profile, /xray:.*methods: \["QR", "Ссылка", "Файл"\]/s);
  assert.match(api, /class ClientSettings\(BaseModel\)/);
  assert.match(api, /payload\.settings\.fingerprint/);
  assert.match(page, /<ConnectionsView/);
  assert.match(page, /<ConnectionDialog/);
  assert.match(dialog, /Параметры профиля/);
  assert.match(dialog, /route_mode/);
  assert.match(dialog, /congestion_control/);
  assert.match(dialog, /fingerprint/);
  assert.match(dialog, /awg_jc/);
  assert.match(dialog, /http_proxy_enabled/);
  assert.match(dialog, /hysteria_congestion/);
  assert.match(dialog, /udp_relay_mode/);
  assert.match(dialog, /routing_domain_strategy/);
  assert.match(dialog, /local_auth_enabled/);
  assert.match(dialog, /disable_loss_compensation/);
  assert.match(dialog, /initial_packet_size/);
  assert.match(dialog, /block_bittorrent/);
  assert.match(dialog, /Общие настройки доступа для proxy-протоколов/);
  assert.match(dialog, /SNI для REALITY/);
  assert.match(dialog, /serverOptions\.awg\?\.s1/);
  assert.match(dialog, /connectionMaskingStatus/);
  assert.match(dialog, /AmneziaWG · Jc/);
  assert.match(dialog, /отдельная обфускация протоколом не предусмотрена/);
  assert.match(dialog, /XHTTP \+ REALITY/);
  assert.match(dialog, /mux_enabled/);
  assert.match(dialog, /Расширенные настройки/);
  assert.match(page, /request\("\/clients\/options"\)/);
  assert.match(api, /def client_options/);
  assert.match(api, /payload\.settings\.xray_sni/);
  assert.match(api, /payload\.settings\.fast_open/);
  assert.match(api, /payload\.settings\.udp_relay_mode/);
  assert.match(api, /payload\.settings\.routing_domain_strategy/);
  assert.match(api, /def local_proxy_fields/);
  assert.match(api, /payload\.settings\.disable_path_mtu_discovery/);
  assert.match(api, /payload\.settings\.block_bittorrent/);
  assert.match(inventory, /ACCESS INVENTORY/);
  assert.match(inventory, /Поиск подключений/);
  assert.doesNotMatch(inventory, /protocolClientGroups/);
  assert.equal(JSON.parse(packageJson).dependencies.qrcode, "^1.5.4");
});

test("protocol pages separate server health from real data-plane evidence", async () => {
  const [api, monitor, page, workspace] = await Promise.all([
    read("api/main.py"),
    read("scripts/vpn-monitor-sample"),
    read("app/page.tsx"),
    read("app/protocol-workspace.tsx"),
  ]);
  assert.match(monitor, /ping -n -q -c 5/);
  assert.match(monitor, /ping_1_1_1_jitter/);
  assert.match(monitor, /uplink_rx_dropped/);
  assert.match(monitor, /conntrack_count/);
  assert.match(api, /def network_diagnostics/);
  assert.match(api, /Path MTU/);
  assert.match(api, /diagnostics\/check/);
  assert.match(api, /connection\/check/);
  assert.match(api, /def check_protocol_connection/);
  assert.match(api, /local-protocol-roundtrip/);
  assert.match(api, /http:\/\/127\.0\.0\.1:8000\/api\/health/);
  assert.match(page, /<ProtocolWorkspace/);
  assert.match(workspace, /DATA PLANE PROOF/);
  assert.match(workspace, /HYSTERIA2 REQUEST PATH/);
  assert.match(workspace, /TUIC V5 REQUEST PATH/);
  assert.match(workspace, /REALITY IDENTITY/);
  assert.match(workspace, /AMNEZIA PARAMETERS/);
  assert.match(workspace, /Служба, listener и протокольный путь/);
  assert.doesNotMatch(page, /Туннель работает/);
  assert.doesNotMatch(api, /threading\.Thread\(target=network_diagnostics/);
});

test("primary resource metrics use CPU percent and readable RAM and disk units", async () => {
  const [api, history, page] = await Promise.all([read("api/main.py"), read("api/metrics_history.py"), read("app/page.tsx")]);
  assert.match(api, /def cpu_usage_percent/);
  assert.match(api, /def collect_system_resources/);
  assert.match(api, /@app\.get\("\/api\/metrics\/history"\)/);
  assert.match(history, /"live": \(300, 3\).*"day": \(86400, 60\).*"week": \(604800, 3600\).*"quarter": \(7776000, 3600\)/s);
  assert.match(page, /title="CPU".*cpu_percent/s);
  assert.match(page, /title="RAM" value=\{bytes\(memoryUsedBytes\)\}/);
  assert.match(page, /title="Disk" value=\{bytes\(diskUsedBytes\)\}/);
  assert.match(page, /aria-label="Период истории метрик"/);
});

test("security keeps panel access controls on the services page", async () => {
  const [api, page] = await Promise.all([read("api/main.py"), read("app/page.tsx")]);
  assert.match(api, /"panel_access": \{/);
  assert.match(api, /"publicly_accessible": panel_publicly_accessible/);
  assert.match(api, /panel_access_consistent/);
  assert.doesNotMatch(page, /title="Доступ к панели"/);
  assert.match(page, /changePanelAccess\(event\.target\.checked \? "vpn" : "external"\)/);
  assert.match(page, /className="serviceModeSwitch protectedAccessSwitch"/);
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
  assert.match(page, /<SecurityActionRow\s+status=\{vpnFirewallState\}/);
  assert.match(page, /fixSecurity\("vpn-firewall"\)/);
  assert.match(manager, /configure_vpn_firewall_policy\(\)/);
  assert.match(manager, /net\.ipv4\.ip_forward=1/);
  assert.match(manager, /ip -4 route show dev "\$\{interface\}" proto kernel scope link/);
  assert.match(manager, /vps-control-vpn-firewall\.service/);
  assert.match(manager, /iptables -C FORWARD/);
  assert.match(manager, /vpn-firewall\) configure_vpn_firewall_policy/);
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
  assert.match(api, /"protocol_scan": \{"enabled": False, "cadence": "daily"/);
  assert.match(api, /"application_update": \{"enabled": False, "cadence": "daily"/);
  assert.match(api, /"kernel_update": \{"enabled": False, "cadence": "weekly"/);
  assert.match(api, /LOG_RETENTION_DAYS", "30"/);
  assert.match(api, /INSTALL_DIR \/ "scripts" \/ "vps-control\.sh"/);
  assert.match(page, /automationDraft/);
  assert.match(page, /Проверка версий протоколов/);
  assert.match(page, /updateAutomation\("protocol_scan", patch\)/);
  assert.match(page, /Плановое обновление основной версии приложения/);
  assert.doesNotMatch(page, /Плановое обновление основной версии приложения \(light\)/);
  assert.match(page, /updateAutomation\("application_update", patch\)/);
  assert.match(page, /Плановое обновление ядра/);
  assert.match(page, /updateAutomation\("kernel_update", patch\)/);
  assert.match(manager, /install_automation_timer "protocol-scan"/);
  assert.match(manager, /protocol-version-check/);
  assert.match(manager, /ConditionPathExists=!\$\{SERVICE_MODE_FILE\}/);
  assert.match(manager, /install_automation_timer "reboot"[^\n]+ yes/);
  assert.match(manager, /install_automation_timer "cleanup"[^\n]+ yes/);
  assert.match(manager, /install_automation_timer "protocol-scan"[^\n]+ yes/);
  assert.match(manager, /install_automation_timer "application-update"[^\n]+"scheduled-app-update"/);
  assert.match(manager, /install_automation_timer "kernel-update"[^\n]+"scheduled-kernel-update"/);
  assert.match(page, /loggingDraft/);
  assert.match(page, /loggingDirty\.current/);
  assert.match(page, /Настройки записи и хранения журналов сохранены/);
  assert.match(manager, /install -m 0755 "\$\{PROJECT_DIR\}\/scripts\/vps-control\.sh" "\$\{COMMAND_PATH\}"/);
});

test("SUDO VPS-CONTROL actions map to real manager commands", async () => {
  const [api, page, manager] = await Promise.all([
    read("api/main.py"), read("app/page.tsx"), read("scripts/vps-control.sh"),
  ]);
  for (const [action, implementation] of [
    ["restart", "restart_services"],
    ["update", "update_app"],
    ["test-update", "update_test_app"],
    ["test-rollback", "restore_test_app"],
    ["network-check", "network_check"],
    ["integrity-check", "integrity_check"],
    ["identity", "refresh_server_identity"],
    ["optimize", "optimize_resources"],
    ["kernel-update", "update_kernel"],
    ["reboot", "reboot_server"],
    ["poweroff", "poweroff_server"],
  ]) {
    assert.match(api, new RegExp(`class ApplicationAction[\\s\\S]*?"${action}"`), `API action ${action}`);
    assert.match(page, new RegExp(`runApplicationAction\\("${action}"\\)`), `UI action ${action}`);
    assert.match(manager, new RegExp(`${action}\\)[\\s\\S]{0,240}${implementation}`), `manager action ${action}`);
  }
  assert.match(page, /title="Обновление ядра"/);
  assert.doesNotMatch(page, /Обновления Ubuntu|Обновить сервер/);
  assert.match(manager, /apt-get -o DPkg::Lock::Timeout=300 autoremove --purge -y/);
  assert.match(manager, /systemd-tmpfiles --clean/);
  assert.match(manager, /journalctl --vacuum-size=500M/);
  assert.doesNotMatch(manager.match(/update_kernel\(\) \{([\s\S]*?)\n\}/)?.[1] || "", /--only-upgrade/);
  assert.match(manager, /dkms autoinstall -k "\$\{kernel\}"/);
  assert.match(manager, /перезагрузка отменена, активные подключения сохранены/);
  assert.match(manager, /verify_managed_protocol_units "\$\{active_protocol_units\[@\]\}"/);
});

test("Light keeps production updates public and gates the test-light channel behind service mode", async () => {
  const [api, page, manager, styles, workflow, ciWorkflow, protocolIcon] = await Promise.all([
    read("api/main.py"), read("app/page.tsx"), read("scripts/vps-control.sh"), read("app/globals.css"),
    read(".github/workflows/release.yml"), read(".github/workflows/ci.yml"), read("app/protocol-icon.tsx"),
  ]);
  assert.match(manager, /PRODUCT_EDITION="light"/);
  assert.match(manager, /PRODUCTION_BRANCH="light"/);
  assert.match(manager, /PRODUCTION_RELEASE_TAG="light-latest"/);
  assert.doesNotMatch(manager, /SERVICE_BRANCH=/);
  assert.match(manager, /update_prebuilt_branch "\$\{PRODUCTION_BRANCH\}" "\$\{PRODUCTION_RELEASE_TAG\}"/);
  assert.match(manager, /TEST_RELEASE_TAG="light-test-latest"/);
  assert.doesNotMatch(manager, /main-latest|APP_TEST_RELEASE_URL/);
  assert.match(manager, /for attempt in \$\(seq 1 48\)/);
  assert.match(manager, /release_download_timeout\(\)/);
  assert.match(manager, /--continue-at -/);
  assert.match(manager, /--speed-limit 1024 --speed-time 120/);
  assert.match(manager, /подготовленный релиз не соответствует актуальной ревизии ветки \$\{branch\}/);
  assert.match(manager, /test-update \[архив\]/);
  assert.match(manager, /update_prebuilt_branch "\$\{TEST_BRANCH\}" "\$\{TEST_RELEASE_TAG\}" test/);
  assert.match(api, /def installed_release_branch\(\)/);
  assert.match(api, /return "test-light" if values\.get\("channel"\) == "test" else "light"/);
  assert.match(manager, /install_prebuilt_release install-release "\$\{archive\}" yes test/);
  assert.match(manager, /for timer in vps-control-auto-update\.timer apt-daily\.timer apt-daily-upgrade\.timer/);
  const serviceModeBody = manager.match(/change_service_mode\(\) \{([\s\S]*?)\n\}/)?.[1] || "";
  assert.doesNotMatch(serviceModeBody, /vpn-monitor\.timer/);
  assert.match(serviceModeBody, /systemctl stop \\\r?\n\s+vps-control-auto-reboot\.timer[\s\S]+vps-control-auto-kernel-update\.timer/);
  assert.match(serviceModeBody, /if \[\[ -r "\$\{AUTOMATION_FILE\}" \]\]; then\s+apply_automation/);
  assert.match(manager, /"ssh_service_was_active": ssh_service == "yes"/);
  assert.match(manager, /"ssh_socket_was_active": ssh_socket == "yes"/);
  assert.match(manager, /сервисный режим включён; версия приложения не изменена/);
  assert.match(manager, /переход на тестовую версию разрешён только в сервисном режиме/);
  assert.match(api, /payload\.action in \("test-update", "test-rollback"\) and not SERVICE_MODE_FILE\.exists\(\)/);
  assert.match(api, /branch = installed_release_branch\(\)/);
  assert.match(api, /expected_branch = installed_release_branch\(\)/);
  assert.match(api, /cached\.get\("current_commit"\) != installed_commit/);
  assert.match(page, /releaseBranch = release\?\.branch \|\| "light"/);
  assert.doesNotMatch(page, /autoRefreshBeforeServiceMode/);
  assert.doesNotMatch(page, /className="autoButton" disabled=\{serviceModeActive\}/);
  assert.match(page, /выполнение всех плановых сценариев заблокировано/);
  assert.match(page, /runApplicationAction\("test-update"\)/);
  assert.match(page, /application\?\.service_mode\?\.rollback_available/);
  assert.match(page, /disabled=\{busy \|\| testReleaseActive\}/);
  assert.equal([...page.matchAll(/disabled=\{busy \|\| testReleaseActive\}/g)].length, 2);
  assert.match(page, /if \(!active && testReleaseActive\)/);
  assert.match(page, /сервисный режим не создаёт отложенный запуск/);
  assert.match(api, /installed_release_branch\(\) == "test-light"/);
  assert.match(manager, /сначала вернитесь на light, затем выключите сервисный режим/);
  assert.match(styles, /\.loginPage \{[^}]*grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(styles, /\.loginCard \{[^}]*max-width: 420px; min-width: 0/);
  assert.match(page, /Вернуться на light/);
  assert.match(workflow, /branches: \[light, test-light\]/);
  assert.match(workflow, /release_tag="light-test-latest"/);
  assert.match(workflow, /GITHUB_REF_NAME" == "test-light"/);
  assert.match(workflow, /version="\$\{latest#light-\}"/);
  assert.match(workflow, /verify:\s+if: github\.ref_name == 'light'/);
  assert.match(workflow, /needs\.verify\.result == 'success' \|\| github\.ref_name == 'test-light'/);
  assert.match(workflow, /release_flags=\(--prerelease\)/);
  assert.match(workflow, /publish:\s+needs: build\s+if: always\(\) && needs\.build\.result == 'success'/);
  assert.match(ciWorkflow, /push:\s+branches: \[light\]/);
  assert.match(ciWorkflow, /pull_request:\s+branches: \[light\]/);
  assert.doesNotMatch(ciWorkflow, /branches: \[[^\]]*test-light/);
  assert.match(protocolIcon, /hysteria2: "HY2"/);
  assert.match(manager, /TEST_BACKUP_DIR="\$\{DATA_DIR\}\/test-app-backup"/);
  assert.match(manager, /restore_test_app\(\)/);
  assert.match(manager, /mv -- "\$\{rollback\}" "\$\{INSTALL_DIR\}"\s+PROJECT_DIR="\$\{INSTALL_DIR\}"\s+write_integrity_manifest/);
  assert.match(manager, /rm -rf -- "\$\{failed_install\}"\s+write_integrity_manifest/);
  assert.match(manager, /предыдущая версия восстановлена, но ещё не отвечает на проверку готовности/);
  assert.match(manager, /change_access_mode\(\)[\s\S]*127\.0\.0\.1:8000\/api\/health/);
  assert.match(manager, /PRODUCT_FILE="\$\{DATA_DIR\}\/product\.json"/);
  assert.match(manager, /архив редакции \$\{release_edition:-unknown\} нельзя установить поверх \$\{PRODUCT_EDITION\}/);
});

test("live system status refreshes below one second without overlapping heavy checks", async () => {
  const [api, page] = await Promise.all([read("api/main.py"), read("app/page.tsx")]);
  assert.match(api, /@app\.get\("\/api\/live-status"\)/);
  assert.match(api, /include_quality=False/);
  assert.match(page, /liveRequestInFlight/);
  assert.match(page, /setInterval\(\(\) => void loadLiveStatus\(\), 800\)/);
  assert.match(page, /\["overview", "security", "application"\]\.includes\(tab\)/);
  assert.match(page, /setInterval\(\(\) => void refreshCurrent\(false\), 15000\)/);
  assert.match(page, /actionRunning \? 3000 : 15000/);
  assert.match(page, /"\<1"/);
  assert.match(page, /sessionStorage\.setItem\("312-notice"/);
  assert.match(page, /window\.location\.reload\(\)/);
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
  assert.match(page, /status === "active" && !alwaysAction/);
  assert.match(api, /hmac\.compare_digest\(payload\.current_password, ADMIN_PASSWORD\)/);
  assert.match(api, /payload\.new_password != payload\.confirm_password/);
  assert.match(api, /categories < 3/);
  assert.match(page, /runApplicationAction\("identity"\)/);
  assert.match(api, /installed = bool\(service and run\("systemctl", "show", service, "--property=LoadState", "--value"\) == "loaded"\)/);
  assert.match(api, /if not available_interfaces:/);
  assert.doesNotMatch(api, /\bWG_INTERFACE\b|\bWG_CONFIG\b|["']wg["']/);
  assert.match(api, /"web": \{"name": "Web 312\.net"/);
  assert.match(api, /AmneziaWG cannot be stopped while panel access is VPN-only/);
  assert.match(manager, /vpn_interface_available="no"/);
  assert.match(manager, /set_env_value "CORS_ORIGINS" "\$\{vpn_origins\}"/);
  assert.doesNotMatch(manager, /\bWG_INTERFACE\b|\bWG_PORT\b/);
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
  assert.match(manager, /systemctl restart "\$\{APP_NAME\}-api\.service" "\$\{APP_NAME\}-web\.service"/);
  assert.match(manager, /restart_caddy_service/);
  assert.match(manager, /caddy validate --config/);
  assert.doesNotMatch(manager, /Установка Docker|compose_with_progress/);
  assert.match(manager, /cleanup_legacy_runtime\(\)/);
  assert.match(manager, /docker volume rm vps-control_app_runtime vps-control_caddy_data vps-control_caddy_config/);
  assert.match(manager, /Docker используется посторонними контейнерами; пакеты Docker сохранены/);
  assert.match(manager, /apt-get -o DPkg::Lock::Timeout=300 purge -y "\$\{docker_packages\[@\]\}"/);
  assert.doesNotMatch(api, /docker", "compose|docker", "inspect/);
  assert.match(api, /"vps-control-web\.service"/);
  assert.match(api, /"caddy\.service"/);
  assert.match(api, /ActiveEnterTimestampMonotonic/);
  assert.match(api, /"uptime_seconds": uptime_seconds/);
  assert.match(api, /"endpoint": endpoint/);
  assert.match(api, /"restarts": int\(properties\.get\("NRestarts"\) or 0\)/);
  assert.match(page, /application\.containers\.length \+ 1/);
  assert.match(page, /healthyApplicationComponents/);
  assert.match(page, /componentUptime/);
  assert.match(page, /рестарты \$\{container\.restarts \?\? 0\}/);
  assert.match(page, /runServiceAction\(container\.service_id/);
});

test("security posture exposes explicit states and keeps summary metrics compact", async () => {
  const [page, css, api, manager] = await Promise.all([
    read("app/page.tsx"), read("app/globals.css"), read("api/main.py"), read("scripts/vps-control.sh"),
  ]);
  assert.match(page, /type SecurityState = "inactive" \| "active" \| "warning" \| "critical"/);
  assert.doesNotMatch(page, /securityScore/);
  assert.match(page, /securityStateMeta\[securityPostureState\]\.label/);
  assert.match(page, /className="securityPostureStats"/);
  assert.match(page, /className=\{`securityPostureStat state-\$\{sshPostureState\}`\}/);
  assert.match(page, /className=\{`securityPostureStat state-\$\{listenerState\}`\}/);
  assert.match(page, /className=\{`securityPostureStat state-\$\{coreUpdatesState\}`\}/);
  assert.match(page, /status=\{sshTunnelsState\}/);
  assert.doesNotMatch(page, /panelAccessState|id: "panel-access"/);
  assert.doesNotMatch(page, /securityAttentionChecks|Что требует внимания|securityAttentionItem/);
  assert.match(page, /title="Системные пакеты"/);
  assert.match(page, /fixSecurity\(updates\?\.kernel_available \? "kernel-update" : "system-update"\)/);
  assert.match(page, /title="Firewall"[\s\S]*fixSecurity\("vpn-firewall"\)/);
  assert.match(page, /title="SSH · административный доступ"[\s\S]*runServiceAction\("ssh", "SSH", "start"\)/);
  assert.match(page, /title="Версия приложения"[\s\S]*runApplicationAction\(testReleaseActive \? "test-update" : "update"\)/);
  assert.match(page, /status=\{tcpProtectionState\}[\s\S]*fixSecurity\("secure"\)/);
  assert.match(page, /status=\{kernelProtectionState\}[\s\S]*fixSecurity\("secure"\)/);
  assert.match(api, /"system-update"/);
  assert.match(manager, /update_system_packages\(\)/);
  assert.match(manager, /apt-get -o DPkg::Lock::Timeout=300 upgrade -y/);
  assert.match(manager, /verify_managed_protocol_units "\$\{active_protocol_units\[@\]\}"/);
  assert.match(manager, /system-update\) update_system_packages/);
  assert.match(css, /gray=inactive, green=active, yellow=attention, red=critical/);
  assert.match(css, /\.securityPostureStats \{[\s\S]*grid-template-columns: repeat\(3/);
  assert.doesNotMatch(css, /\.securityAttention/);
});

test("Caddy updates remain compatible with old installers and roll back safely", async () => {
  const [caddyfile, manager] = await Promise.all([
    read("Caddyfile"), read("scripts/vps-control.sh"),
  ]);
  assert.match(caddyfile, /^:\{\$HTTP_PORT\} \{/);
  assert.doesNotMatch(caddyfile, /\{\$SITE_ADDRESS\}/);
  assert.match(caddyfile.replaceAll("{$HTTP_PORT}", "80"), /^:80 \{/);
  assert.match(manager, /validate_caddy_template "\$\{payload\}\/Caddyfile"/);
  assert.match(manager, /mktemp \/etc\/caddy\/\.Caddyfile\.XXXXXX/);
  assert.match(manager, /caddy validate --adapter caddyfile --config "\$\{candidate\}"/);
  assert.match(manager, /mv -f -- "\$\{candidate\}" "\$\{CADDY_CONFIG\}"/);
  assert.match(manager, /restart_caddy_service\(\)[\s\S]*restore_caddy_config/);
  assert.match(manager, /CADDY_CONFIG_BACKUP="\$\{CADDY_CONFIG\}\.vps-control-backup"/);
  assert.doesNotMatch(caddyfile, /^ {4}\S/m);
});

test("SSH hardening remains reachable under unauthenticated scanner load", async () => {
  const manager = await read("scripts/vps-control.sh");
  const secureBody = manager.match(/secure_server\(\) \{([\s\S]*?)\n\}/)?.[1] || "";
  assert.match(secureBody, /LoginGraceTime 30/);
  assert.match(secureBody, /MaxStartups 30:30:100/);
  assert.match(secureBody, /PerSourceMaxStartups 3/);
  assert.match(secureBody, /PerSourcePenalties no/);
  assert.match(secureBody, /sshd -t/);
  assert.match(secureBody, /предыдущая конфигурация восстановлена/);
});

test("Light protocol modules install and uninstall independently", async () => {
  const [api, manager, page, workspace, awgInstall, awgRemove, hysteriaInstall, hysteriaRemove, tuicInstall, tuicRemove, xrayInstall, xrayRemove, relayManifest] = await Promise.all([
    read("api/main.py"), read("scripts/vps-control.sh"),
    read("app/page.tsx"),
    read("app/protocol-workspace.tsx"),
    read("protocol-images/amneziawg/install.sh"),
    read("protocol-images/amneziawg/uninstall.sh"),
    read("protocol-images/hysteria2/install.sh"), read("protocol-images/hysteria2/uninstall.sh"),
    read("protocol-images/tuic/install.sh"), read("protocol-images/tuic/uninstall.sh"),
    read("protocol-images/xray/install.sh"), read("protocol-images/xray/uninstall.sh"),
    read("protocol-images/relay-agent/manifest.json"),
  ]);
  assert.match(api, /AmneziaWG cannot be removed while panel access is VPN-only/);
  assert.match(awgRemove, /route delete allow in on "\$\{AWG_INTERFACE\}" out on "\$\{UPLINK_INTERFACE\}" from "\$\{AWG_SUBNET\}"/);
  assert.match(awgRemove, /ufw status \| grep -Fq "\$\{AWG_SUBNET\} on \$\{AWG_INTERFACE\}"/);
  assert.match(awgRemove, /99-vps-control-amneziawg\.conf/);
  assert.equal(JSON.parse(await read("protocol-images/amneziawg/manifest.json")).requires_kernel_headers, true);
  assert.match(api, /protocol-install/);
  assert.match(api, /protocol-images\/versions\/check/);
  assert.match(api, /protocol-images\/\{image_id\}\/version\/check/);
  assert.match(api, /PROTOCOL_VERSIONS_FILE/);
  assert.match(api, /protocol-update/);
  assert.match(manager, /set_protocol_client_update_state/);
  assert.match(manager, /"paused" "Обновление протокола запущено/);
  assert.match(manager, /"incompatible" "Новая версия не прошла проверку совместимости/);
  assert.match(page, /checkProtocolVersion\(image\)/);
  assert.match(page, /tab !== "overview" && !isProtocolTab\(tab\) && <div className="gateSectionIntro">/);
  assert.match(workspace, /Обновить до/);
  assert.match(manager, /prepare_package_manager\(\)/);
  assert.match(manager, /\n  prepare_package_manager\r?\n/);
  assert.match(manager, /dpkg --audit/);
  assert.match(manager, /DPkg::Lock::Timeout=300 -f install -y/);
  assert.match(manager, /run_protocol_installer\(\)/);
  assert.match(manager, /timeout --signal=TERM --kill-after=30s/);
  assert.match(manager, /Установка \$\{image_id\} выполняется · прошло \$\{elapsed_label\}/);
  assert.match(api, /RuntimeMaxSec=\{runtime_max_seconds\}/);
  assert.match(api, /runtime_max_seconds=1260/);
  assert.match(awgInstall, /DPkg::Lock::Timeout=300/);
  assert.match(awgInstall, /if ! command -v awg.*command -v awg-quick.*modinfo amneziawg/s);
  for (const installer of [hysteriaInstall, tuicInstall, xrayInstall]) {
    assert.match(installer, /DPkg::Lock::Timeout=300/);
    assert.match(installer, /sha256sum -c -/);
    assert.match(installer, /IPAccounting=true/);
  }
  for (const uninstaller of [hysteriaRemove, tuicRemove, xrayRemove]) {
    assert.match(uninstaller, /PRESERVE_COMPONENT_DATA/);
  }
  await assert.rejects(read("protocol-images/trojan/install.sh"), { code: "ENOENT" });
  assert.match(xrayInstall, /'protocol': 'vless'/);
  assert.match(xrayInstall, /'network': 'xhttp'/);
  assert.match(xrayInstall, /'security': 'reality'/);
  assert.match(xrayInstall, /\/\^\(Password\|PublicKey\)\//);
  assert.match(api, /Unable to create Xray connection/);
  assert.doesNotMatch(api + page, /\bTrojan\b|"trojan"/);
  assert.equal(JSON.parse(relayManifest).kind, "agent");
  assert.match(page, /Недоступно/);
  assert.match(page, /protocolImages\.map/);
  assert.match(page, /image\.available_version \|\| "НЕ ПРОВЕРЕНО"/);
  assert.match(page, /image\.update_available\s*\?/);
  assert.match(workspace, />Удалить<\/button>/);
  assert.match(page, /checkingProtocolVersion === image\.id \? "Проверка…"/);
  assert.match(workspace, /onClick=\{props\.onRemove\}.*?>Удалить<\/button>/s);
  assert.match(workspace, /disabled=\{props\.busy \|\| !image\?\.update_available\}/);
  assert.match(manager, /--retry 10 --retry-connrefused --retry-delay 1/);
});

test("full uninstall removes managed protocol state without recreating application data", async () => {
  const manager = await read("scripts/vps-control.sh");
  const uninstall = manager.slice(manager.indexOf("uninstall_app()"), manager.indexOf("restart_services()"));
  assert.match(uninstall, /protocol-images\/amneziawg\/uninstall\.sh/);
  assert.match(uninstall, /for protocol_id in hysteria2 tuic xray/);
  assert.match(uninstall, /PRESERVE_COMPONENT_DATA=0/);
  assert.match(uninstall, /\/usr\/local\/sbin\/vpn-monitor-sample/);
  assert.match(uninstall, /caddy\.service/);
  assert.match(uninstall, /"\$\{CADDY_CONFIG\}"/);
  assert.match(uninstall, /CURRENT_ACTION=""/);
});

test("successful readiness retries do not print transient HTTP errors", async () => {
  const manager = await read("scripts/vps-control.sh");
  const retries = [...manager.matchAll(/curl --fail --silent[^\n]+--retry (?:6|10)[^\n]+/g)].map((match) => match[0]);
  assert.ok(retries.length >= 5);
  for (const command of retries) assert.doesNotMatch(command, /--show-error/);
  const verify = manager.slice(manager.indexOf("verify_app()"), manager.indexOf("network_check()"));
  assert.match(verify, /--retry 10 --retry-connrefused --retry-delay 1/);
});

test("planned application downtime does not surface transient gateway errors", async () => {
  const page = await read("app/page.tsx");
  assert.match(page, /const connectionInterruptingActions = new Set<ApplicationAction>/);
  assert.match(page, /const expectedDowntimeUntil = useRef\(0\)/);
  assert.match(page, /if \(Date\.now\(\) < expectedDowntimeUntil\.current\) \{\s*setError\(""\);\s*return;/);
  assert.match(page, /beginExpectedDowntime\(action\);/);
  assert.match(page, /else if \(\["succeeded", "finished", "failed"\]\.includes/);
});

test("геолокация требует согласия независимых источников", async () => {
  const [manager, resolver, config] = await Promise.all([
    read("scripts/vps-control.sh"),
    read("scripts/resolve-geolocation.py"),
    read("install.conf"),
  ]);
  assert.match(manager, /resolve-geolocation\.py/);
  assert.match(manager, /GEOLOCATION_SENARY_URL/);
  assert.match(resolver, /country_quorum = max\(2,/);
  assert.match(resolver, /if votes < country_quorum:/);
  assert.match(resolver, /"finland": "FI"/);
  assert.match(resolver, /"germany": "DE"/);
  assert.match(config, /GEOLOCATION_SENARY_URL="https:\/\/ipinfo\.io"/);
  const root = fileURLToPath(new URL("../", import.meta.url));
  const python = process.platform === "win32" ? "python" : "python3";
  const result = spawnSync(python, [
    `${root}scripts/resolve-geolocation.py`,
    `${root}tests/fixtures/geo-singapore.json`,
    `${root}tests/fixtures/geo-finland-ipwho.json`,
    `${root}tests/fixtures/geo-finland-ipinfo.json`,
  ], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.stdout.trim().split(/\r?\n/), ["85.209.155.229", "Helsinki", "Finland", "FI", "2/3"]);
});

test("the interface uses one fixed visual design without personalization", async () => {
  const [page, workspace, api, css, manager, navigation, notificationCenter, layout] = await Promise.all([
    read("app/page.tsx"), read("app/protocol-workspace.tsx"), read("api/main.py"), read("app/globals.css"), read("scripts/vps-control.sh"),
    read("src/light-navigation.tsx"), read("src/notifications/notification-center.tsx"), read("app/layout.tsx"),
  ]);
  assert.doesNotMatch(page, /personalization|data-(?:style|palette|density|theme)/i);
  assert.doesNotMatch(api, /personalization/i);
  assert.doesNotMatch(css, /personalization|data-(?:style|palette|density|theme)|task-manager/i);
  assert.match(page, /<main className="shell gateShell">/);
  assert.match(page, /<LightNavigation/);
  assert.match(page, /className="gateMasthead"/);
  assert.match(page, /const nodeHasError = application\?\.api\.active === false/);
  assert.doesNotMatch(page, /const nodeHasError =[^;]+action\?\.state === "failed"/);
  assert.match(navigation, /className="gateSidebar"/);
  assert.match(navigation, /label="WORKSPACE"/);
  assert.match(navigation, /label="TUNNELS"/);
  assert.match(navigation, /protocols\.length === 1/);
  assert.match(navigation, /label="Протоколы" badge=\{String\(protocols\.length\)\}/);
  assert.match(workspace, /props\.installed\.length > 1.*className="protocolWorkspaceRail"/s);
  assert.match(navigation, /label="SYSTEM"/);
  assert.match(layout, /<NotificationProvider>\{children\}<\/NotificationProvider>/);
  assert.match(page, /notifications\.finishOperation\(input\)/);
  assert.match(notificationCenter, /gateNotificationDock/);
  assert.doesNotMatch(page, /<aside className="operationBanner"/);
  assert.doesNotMatch(page, /className="successNotice"/);
  assert.match(css, /--accent: var\(--cyan\)/);
  assert.match(css, /\.shell \.metricCard \{ border-left: 2px solid var\(--accent\)/);
  assert.match(css, /\.primaryButton \{[^}]+background: linear-gradient\(100deg, rgba\(39, 124, 137, \.28\)/);
  assert.match(css, /\.nodeStatus\.healthy \.pulse \{ background: var\(--accent\)/);
  assert.match(css, /\.serviceOnline \{ background: var\(--accent\)/);
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
  assert.match(builder, /NEXT_PUBLIC_RELEASE_BRANCH/);
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
  assert.match(page, /Текущий канал: light · production/);
  assert.match(builder, /schema=1/);
  assert.match(builder, /RELEASE_EDITION="\$\{RELEASE_EDITION:-light\}"/);
  assert.match(builder, /RELEASE_CHANNEL="\$\{RELEASE_CHANNEL:-production\}"/);
  assert.match(builder, /RELEASE_BRANCH="\$\{RELEASE_BRANCH:-/);
  assert.match(builder, /branch=%s/);
  assert.match(manager, /NEXT_PUBLIC_RELEASE_BRANCH="\$\{RELEASE_BRANCH\}"/);
  assert.match(readme, /vps-control update/);
  assert.match(manager, /TimeoutStopSec=15/);
  assert.match(manager, /KillMode=mixed/);
  assert.match(manager, /mv -- "\$\{INSTALL_DIR\}\/venv" "\$\{rollback\}\/venv"/);
  assert.match(manager, /install -d -m 0755 "\$\{INSTALL_DIR\}"\s+chmod 0755 "\$\{INSTALL_DIR\}"/);
});
