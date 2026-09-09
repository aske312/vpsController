import assert from "node:assert/strict";
import test from "node:test";
import { readFileText, read, readUiSources, readApiSources, readStyles } from "./support.mjs";

test("control surfaces share compact headers, telemetry and modal language", async () => {
  const [styles, overview, versions] = await Promise.all([
    readStyles(), read("src/features/overview/overview-view.tsx"), read("src/shared/lib/format-version.ts"),
  ]);
  assert.match(overview, /overviewNodeWorkspace/);
  assert.match(overview, /const availableVersion = image\.available_version/);
  assert.match(styles, /grid-template-columns:minmax\(0,8fr\) minmax\(260px,2fr\)/);
  assert.match(styles, /\.gateMastMetric/);
  assert.match(styles, /\.confirmBackdrop,.accessBetaModalBackdrop,.mihomoDialogBackdrop,.legalBackdrop/);
  assert.match(versions, /slice\(0, 3\)/);
});

test("operational pages keep their artwork, 70/30 workspace and real country flags", async () => {
  const [workspace, styles] = await Promise.all([
    read("src/control-panel/components/app-workspace.tsx"), readStyles(),
  ]);
  for (const asset of ["overview.webp", "network_1.webp", "security.webp", "services.webp", "application.webp", "mihomo.webp"]) {
    assert.match(styles, new RegExp(asset.replace(".", "\\.")));
  }
  assert.match(styles, /\.overviewNodeWorkspace\s*\{[\s\S]*?grid-template-columns:minmax\(0,8fr\) minmax\(260px,2fr\)/);
  assert.match(styles, /\.overviewFlow \{ width:min\(1180px,100%\)/);
  assert.match(styles, /\.overviewRoute\.mihomo \.overviewChannelList \{ grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  const overview = await read("src/features/overview/overview-view.tsx");
  assert.match(overview, /"transport-hysteria2": "HY2"/);
  assert.match(overview, /"transport-tuic": "TUIC"/);
  assert.match(workspace, /function CountryFlag/);
  assert.match(workspace, /<svg viewBox="0 0 27 18"/);
  assert.match(workspace, /country === "nl"/);
  assert.match(workspace, /country === "lv"/);
  assert.match(workspace, /country === "ru"/);
});

test("интерфейс относится к 312.net, публичные метаданные нейтральны", async () => {
  const [layout, page, packageJson] = await Promise.all([
    read("app/layout.tsx"),
    readUiSources(),
    read("package.json"),
  ]);
  assert.match(layout, /title: "Infrastructure Control"/);
  assert.doesNotMatch(layout, /VPN|WireGuard|Amnezia|Mihomo|VLESS|Shadowsocks|Hysteria|TUIC|Trojan|IKEv2/i);
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
    read("src/shared/components/legal-footer.tsx"),
    read("docs/CONNECTION_GUIDE.md"),
    read("src/features/connections/connection-guide.tsx"),
    readUiSources(),
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
  assert.match(page, /const nextProtocol = image\.id === "mihomo"/);
  assert.match(page, /loadProtocolStatus\(nextProtocol\)/);
  assert.match(page, /setSelectedChannel\(nextProtocol\)[\s\S]*setTab\("channels"\)/);
  assert.match(page, /2–48 символов/);
});

test("network diagnostics measure loss, jitter, MTU and server path health", async () => {
  const [api, monitor, page] = await Promise.all([
    readApiSources(),
    read("scripts/vpn-monitor-sample"),
    readUiSources(),
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
  const [api, page, overview] = await Promise.all([readApiSources(), readUiSources(), read("src/features/overview/overview-view.tsx")]);
  assert.match(api, /def cpu_usage_percent/);
  assert.match(api, /"cpu_percent": cpu_percent/);
  assert.match(page, /label="CPU"[\s\S]*cpu_percent/);
  assert.match(page, /label="MEMORY"[\s\S]*bytes\(memoryUsedBytes\)/);
  assert.match(page, /label="DISK USED"[\s\S]*bytes\(diskUsedBytes\)/);
  assert.match(page, /bytes\(memoryFree\)/);
  assert.match(page, /bytes\(diskFree\)/);
  assert.match(overview, /Number\.isFinite\(value\) \|\| value <= 0/);
  assert.match(overview, /Math\.max\(0, Math\.min\(Math\.floor\(Math\.log\(value\)/);
});

test("service settings are staged, saved explicitly and survive background refresh", async () => {
  const [api, page, manager] = await Promise.all([
    readApiSources(), readUiSources(), read("scripts/vps-control.sh"),
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

test("live monitoring uses stable low-load cadence and detailed server metrics", async () => {
  const [api, page] = await Promise.all([readApiSources(), readUiSources()]);
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
  assert.match(page, /\["update", "test-update", "test-rollback", "safe-update", "kernel-update"\]\.includes/);
  assert.match(page, /Сервисный режим \$\{active \? "включён" : "выключен"\}\. Кэш интерфейса сброшен/);
  assert.match(page, /успешно завершено/);
});

test("log management runs bundled control safely and permits disabled retention", async () => {
  const [api, manager] = await Promise.all([readApiSources(), read("scripts/vps-control.sh")]);
  assert.match(api, /\["\/bin\/bash", str\(bundled_command\)/);
  assert.match(manager, /if \(\( retention > 0 \)\); then\s+printf 'MaxRetentionSec/s);
  assert.match(manager, /chmod 0755 "\$\{INSTALL_DIR\}\/scripts\/vps-control\.sh"/);
});

test("the interface uses one fixed visual design without personalization", async () => {
  const [page, api, css, manager] = await Promise.all([
    readUiSources(), readApiSources(), readStyles(), read("scripts/vps-control.sh"),
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

test("DNS and connection screens describe real effects and provide safe filtering", async () => {
  const [page, dnsView, api, css] = await Promise.all([readUiSources(), read("src/features/dns/dns-view.tsx"), readApiSources(), readStyles()]);
  assert.match(dnsView, /Изменения применяются только к отмеченным каналам/);
  assert.doesNotMatch(page, /DNS самого VPS|system_resolver|apply_system/);
  assert.match(dnsView, /Только новые конфиги клиентов/);
  assert.match(dnsView, /Xray получит выбранные resolver-ы и перезапустится/);
  assert.match(dnsView, /серверный трафик не изменяется/);
  assert.match(page, /clientProtocolFilter/);
  assert.match(page, /clientStateFilter/);
  assert.match(page, /clientSearch/);
  assert.match(page, /НЕСТАБИЛЬНО/);
  assert.match(api, /protocol_effect_details/);
  assert.match(api, /"installed": installed\["wg"\]/);
  assert.match(dnsView, /filter\(\(\[, effect\]\) => effect\.installed\)/);
  assert.match(dnsView, /Нет установленных протоколов/);
  assert.match(api, /matches_selected/);
  assert.match(css, /\.connectionsWorkspace/);
  assert.match(css, /\.connectionsFilters/);
});

test("connection latency labels identify the real measurement source", async () => {
  const [api, page] = await Promise.all([readApiSources(), readUiSources()]);
  assert.match(api, /"latency_source": "server_icmp_tunnel_ip"/);
  assert.match(page, /РАЗБРОС RTT/);
  assert.match(page, /VPS → device/);
  assert.match(page, /latency_source === "server_icmp_tunnel_ip"/);
});

test("node components distinguish installable images and refresh real versions", async () => {
  const page = await readUiSources();
  assert.match(page, /!item\.installed && item\.installable/);
  assert.match(page, /!image\.installed && !image\.installable/);
  assert.match(page, /displayedVersion = image\.installed[\s\S]*?installedVersion[\s\S]*?image\.installable \? "АКТУАЛЬНАЯ" : "—"/);
  assert.match(page, /await loadOverview\(\)/);
});

test("legacy users beta surface and orchestration are removed", async () => {
  const [ui, api, navigation, globals] = await Promise.all([
    readUiSources(), readApiSources(), read("src/control-panel/components/gate-navigation.tsx"), read("app/globals.css"),
  ]);
  for (const source of [ui, api, navigation, globals]) {
    assert.doesNotMatch(source, /access-beta|access_beta|AccessProfilesBeta|pages\/users\.css/);
  }
  await assert.rejects(readFileText("api/access_beta.py"), { code: "ENOENT" });
  await assert.rejects(readFileText("src/features/users/users-view.tsx"), { code: "ENOENT" });
});

test("managed services artwork fills the block without distortion", async () => {
  const servicesCss = await read("src/features/services/services.css");
  assert.match(servicesCss, /\.servicesManagedBackdrop[^}]*background-size:cover[^}]*background-repeat:no-repeat/s);
});
