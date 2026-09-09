import assert from "node:assert/strict";
import test from "node:test";
import { X509Certificate } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileText, read, readUiSources, readApiSources, ROOT } from "./support.mjs";

test("release includes a tracked, valid public Cloudflare CA", async () => {
  const path = "api/resources/cloudflare-origin-pull-ca.pem";
  const tracked = execFileSync("git", ["ls-files", "--error-unmatch", path], { cwd: ROOT, encoding: "utf8" });
  assert.equal(tracked.trim(), path);
  const pem = await readFileText(path);
  assert.doesNotMatch(pem, /PRIVATE KEY/);
  const cert = new X509Certificate(pem);
  assert.equal(cert.ca, true);
  assert.match(cert.subject, /CloudFlare|Cloudflare/);
  assert.ok(Date.parse(cert.validTo) > Date.now() + 30 * 86400000);
});

test("successful release readiness retries do not print transient connection errors", async () => {
  const installer = await readFileText("scripts/vps-control.sh");
  const updatePaths = [
    installer.slice(installer.indexOf("install_prebuilt_release()"), installer.indexOf("update_prebuilt_branch()")),
    installer.slice(installer.indexOf("restore_test_app()"), installer.indexOf("change_access_mode()")),
  ].join("\n");
  const readiness = [...updatePaths.matchAll(/curl --fail --silent[^\n]+--retry 10[^\n]+/g)].map((match) => match[0]);
  assert.ok(readiness.length >= 2);
  for (const command of readiness) assert.doesNotMatch(command, /--show-error/);
});

test("Unix shell entrypoints are protected from Windows line endings", async () => {
  const [attributes, manager, bootstrap, releaseBuilder] = await Promise.all([
    read(".gitattributes"),
    read("scripts/vps-control.sh"),
    read("scripts/install-panel.sh"),
    read("scripts/build-release.sh"),
  ]);
  assert.match(attributes, /^\*\.sh text eol=lf$/m);
  assert.doesNotMatch(manager, /\r/);
  assert.doesNotMatch(bootstrap, /\r/);
  assert.match(releaseBuilder, /Shell script contains CRLF line endings/);
});

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
  assert.match(manager, /GEOLOCATION_QUATERNARY_URL="https:\/\/ipapi\.co"/);
  assert.match(manager, /GEOLOCATION_QUINARY_URL="https:\/\/free\.freeipapi\.com\/api\/json"/);
  assert.match(manager, /GEOLOCATION_SENARY_URL="https:\/\/ipinfo\.io"/);
  assert.match(manager, /PUBLIC_IP_DISCOVERY_URL="https:\/\/api64\.ipify\.org"/);
  assert.match(manager, /if ip and re\.fullmatch\(r"\[A-Z\]\{2\}", code\):/);
  assert.match(manager, /\/run\/cloud-init\/instance-data\.json/);
  assert.match(manager, /code_votes = Counter/);
  assert.match(manager, /country_quorum = max\(2, len\(records\) \/\/ 2 \+ 1\)/);
  assert.match(manager, /city_quorum = max\(2, len\(matching\) \/\/ 2 \+ 1\)/);
  assert.match(manager, /if city_count >= city_quorum:[\s\S]+else:[\s\S]+city = nearest_major_city\(code, cluster_points\)/);
  assert.match(manager, /MAX_CITY_CLUSTER_KM = 50\.0/);
  assert.match(manager, /def distance_km\(first, second\)/);
  assert.match(manager, /distance_km\(matching\[left\]\[4\], matching\[right\]\[4\]\) <= MAX_CITY_CLUSTER_KM/);
  assert.match(manager, /MAJOR_CITY_ANCHORS/);
  assert.match(manager, /\("NL", "Amsterdam", 52\.3676, 4\.9041, 55\)/);
  assert.match(manager, /city = nearest_major_city\(code, cluster_points\)/);
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

test("service mode deploys main from an isolated preview while stabl remains the production channel", async () => {
  const [api, page, manager, stablWorkflow] = await Promise.all([
    readApiSources(), readUiSources(), read("scripts/vps-control.sh"),
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
  assert.match(manager, /MAIN_RELEASE_WAIT_ATTEMPTS=90/);
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
  assert.match(stablWorkflow, /npm run typecheck/);
  assert.match(stablWorkflow, /npm test/);
});

test("main preview is built off-VPS and interrupted updates cannot report success", async () => {
  const [workflow, manager, api] = await Promise.all([
    read(".github/workflows/stabl-release.yml"),
    read("scripts/vps-control.sh"),
    readApiSources(),
  ]);
  const previewWorkflow = workflow.split("  preview:")[1] || "";
  assert.match(workflow, /Build main preview package/);
  assert.match(previewWorkflow, /npm run lint/);
  assert.match(previewWorkflow, /npm run typecheck/);
  assert.match(previewWorkflow, /npm test/);
  assert.match(workflow, /vps-control-main\.tar\.gz/);
  assert.match(workflow, /gh release create main-latest/);
  assert.doesNotMatch(manager, /BUILD_COMMIT="\$\{latest\}".*build-release/s);
  assert.match(manager, /rollback_interrupted_update/);
  assert.match(manager, /Update was interrupted after the application swap; restoring the previous release/);
  assert.match(manager, /UPDATE_SWAP_ACTIVE="yes"/);
  assert.match(manager, /trap 'exit 124' TERM INT/);
  assert.match(api, /RuntimeMaxSec=.*60min.*safe-update/);
  assert.match(api, /--property=TimeoutStopSec=45s/);
  assert.match(api, /Операция прервана перезагрузкой/);
  assert.match(api, /int\(action\.get\("progress"/);
  assert.match(api, /awaiting_final_status[\s\S]*total_seconds\(\) < 30/);
  assert.match(manager, /write_action_status "succeeded" 100 "Тестовое обновление установлено и проверено"/);
});

test("missing main preview is reported as not installed rather than a broken server update", async () => {
  const manager = await read("scripts/vps-control.sh");
  assert.match(manager, /Тестовая сборка main ещё не опубликована; установка не выполнялась, рабочая версия не изменена/);
});

test("application updates reuse unchanged components", async () => {
  const manager = await read("scripts/vps-control.sh");
  assert.match(manager, /requirements_marker="\$\{INSTALL_DIR\}\/venv\/\.requirements\.sha256"/);
  assert.match(manager, /Python-зависимости не изменились/);
  assert.match(manager, /Re-evaluate the hosting location from the current public IP on every deploy[\s\S]*refresh_server_identity/);
  const deployBody = manager.match(/deploy\(\) \{([\s\S]*?)\n\}/)?.[1] || "";
  assert.match(deployBody, /build_web/);
  assert.match(deployBody, /install_web/);
  assert.doesNotMatch(deployBody, /compose_with_progress|force-recreate gateway/);
});

test("web and gateway run as systemd services without Docker", async () => {
  const [manager, api, page] = await Promise.all([
    read("scripts/vps-control.sh"), readApiSources(), readUiSources(),
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

test("installation discovers dual-stack endpoints and reserves 443 for HTTPS", async () => {
  const [manager, api, config, caddy] = await Promise.all([
    read("scripts/vps-control.sh"), readApiSources(), read("install.conf"), read("Caddyfile"),
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

test("manual releases are prebuilt and installed without Docker or package upgrades", async () => {
  const [builder, api, page, manager, readme] = await Promise.all([
    read("scripts/build-release.sh"), readApiSources(), readUiSources(),
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
  assert.match(releaseInstall, /PROJECT_DIR="\$\{INSTALL_DIR\}"\s+if ! write_caddy_config/);
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
  assert.match(manager, /ensure_mihomo_profile_runtimes/);
  assert.match(manager, /shadowsocks-libev/);
  assert.match(manager, /vps-control-mihomo-ss\.target/);
  assert.match(manager, /install -d -m 0755 "\$\{INSTALL_DIR\}"\s+chmod 0755 "\$\{INSTALL_DIR\}"/);
});

test("manual full update and conservative automatic update use separate policies", async () => {
  const [manager, api, page, view] = await Promise.all([
    read("scripts/vps-control.sh"), readApiSources(), readUiSources(), read("src/features/application/application-view.tsx"),
  ]);
  assert.match(manager, /safe_update_server\(\)/);
  assert.match(manager, /openssl enc -aes-256-cbc -salt -pbkdf2/);
  assert.match(manager, /apt-get full-upgrade --no-remove -y/);
  assert.match(manager, /auto_safe_update_server\(\)/);
  assert.match(manager, /apt-get install --only-upgrade --no-remove/);
  assert.match(manager, /no kernel, bootloader, libc, systemd, SSH, major or package removal/);
  assert.match(manager, /"auto-safe-update" "\$\{update_enabled\}"/);
  assert.doesNotMatch(manager, /update_enabled="false"/);
  assert.match(manager, /restore_recovery_point "\$\{archive\}"/);
  assert.match(api, /@app\.get\("\/api\/application\/update-report"\)/);
  assert.match(page, /downloadUpdateReport/);
  assert.match(view, /runApplicationAction\("safe-update"\)/);
  assert.match(view, /Скачать отчёт и логи/);
});
