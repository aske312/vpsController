import assert from "node:assert/strict";
import test from "node:test";
import { read, readMihomoSources, readApiSources, readStyles } from "./support.mjs";

test("Mihomo transports automatically provision DNS and routing policies", async () => {
  const [manager, page, styles, polish, dnsManifest, routingManifest] = await Promise.all([
    read("protocol-images/mihomo/manager.py"),
    readMihomoSources(),
    read("src/features/mihomo/mihomo.css"),
    read("src/shared/styles/polish.css"),
    read("protocol-images/mihomo/modules/dns-private/manifest.json").then(JSON.parse),
    read("protocol-images/mihomo/modules/routing-policy/manifest.json").then(JSON.parse),
  ]);
  assert.match(manager, /def ensure_policy_settings/);
  assert.match(manager, /atomic_json\(DNS_SETTINGS_FILE, dns_defaults\(\)\)/);
  assert.match(manager, /atomic_json\(ROUTING_SETTINGS_FILE, routing_defaults\(\)\)/);
  assert.match(manager, /ensure_policy_settings\(\)[\s\S]+value\["modules"\]\[module_id\] = True/);
  assert.match(manager, /@app\.patch\("\/api\/mihomo\/routing\/settings"/);
  assert.match(manager, /routing = \{\*\*routing_settings\(\), \*\*profile_routing\}/);
  assert.match(manager, /routing: dict\[str, Any\] = Field\(default_factory=dict\)/);
  assert.match(manager, /device\.get\("routing"\).*dict\(legacy_routing\)/s);
  assert.match(manager, /profile_routing = device_routing\(normalized, selected_device\)/);
  assert.match(manager, /return values if isinstance\(values, dict\) else profile.get\("routing", \{\}\)/);
  assert.match(manager, /DIRECT_GAME_PROCESSES/);
  assert.match(manager, /TUNNEL_GAME_PROCESSES/);
  assert.match(manager, /DEFAULT_TUNNEL_GAMES = tuple\(TUNNEL_GAME_PROCESSES\)/);
  assert.match(manager, /DEFAULT_DIRECT_GAMES = tuple\(game_id for game_id in DIRECT_GAME_PROCESSES if game_id not in TUNNEL_GAME_PROCESSES\)/);
  assert.match(manager, /"block_ads": \[/);
  assert.match(manager, /"block_privacy": \[/);
  assert.match(manager, /DOMAIN-SUFFIX,google-analytics\.com,REJECT/);
  assert.match(manager, /DOMAIN-SUFFIX,appsflyer\.com,REJECT/);
  assert.doesNotMatch(manager, /DOMAIN-SUFFIX,sentry\.io,REJECT/);
  assert.match(manager, /GEOSITE,category-ads-all,REJECT/);
  assert.match(manager, /DOMAIN-SUFFIX,smartadserver\.com,REJECT/);
  assert.match(manager, /DOMAIN-SUFFIX,buzzoola\.com,REJECT/);
  assert.match(manager, /DOMAIN-SUFFIX,freewheel\.tv,REJECT/);
  assert.match(manager, /DOMAIN-SUFFIX,springserve\.com,REJECT/);
  assert.doesNotMatch(manager, /GEOSITE,tracker,REJECT/);
  assert.doesNotMatch(manager, /DOMAIN-SUFFIX,getadmiral\.com,REJECT/);
  assert.match(manager, /"arenabreakoutinfinite": \["UAGame\.exe"\]/);
  assert.match(manager, /"marathon": \["Marathon\.exe"\]/);
  assert.match(manager, /"direct_local_network": \[/);
  assert.match(manager, /IP-CIDR,192\.168\.0\.0\/16,DIRECT,no-resolve/);
  assert.match(manager, /IP-CIDR6,fc00::\/7,DIRECT,no-resolve/);
  assert.match(manager, /"direct_downloads": \[/);
  assert.match(manager, /DOMAIN-SUFFIX,windowsupdate\.com,DIRECT/);
  assert.match(manager, /DOMAIN-SUFFIX,steamcontent\.com,DIRECT/);
  assert.match(manager, /DOMAIN-SUFFIX,gosuslugi\.ru,DIRECT/);
  assert.match(manager, /DOMAIN-SUFFIX,rutube\.ru,DIRECT/);
  assert.match(manager, /DOMAIN-SUFFIX,samokat\.ru,DIRECT/);
  assert.match(manager, /DOMAIN,assets1\.xboxlive\.com,DIRECT/);
  assert.match(manager, /"atomicheart": \["AtomicHeart-Win64-Shipping\.exe"\]/);
  assert.match(manager, /"stalcraft": \["stalcraft\.exe", "STALCRAFT\.exe"\]/);
  assert.match(manager, /DIRECT_P2P_PROCESSES/);
  assert.match(manager, /"qbittorrent": \["qbittorrent\.exe", "qbittorrent"\]/);
  assert.match(manager, /direct_p2p_rules\(routing\)/);
  assert.match(manager, /DOMAIN,settings-win\.data\.microsoft\.com,REJECT/);
  assert.match(manager, /DOMAIN,incoming\.telemetry\.mozilla\.org,REJECT/);
  const privacyDefaults = manager.slice(manager.indexOf('"block_privacy": ['), manager.indexOf('"direct_ru_sites": ['));
  assert.doesNotMatch(privacyDefaults, /settings-win\.data\.microsoft\.com/, "system telemetry must be opt-in");
  assert.match(manager, /PRIVACY_TELEMETRY_RULES/);
  assert.match(manager, /"available_rules":/);
  assert.match(manager, /direct_games_enabled/);
  assert.match(manager, /direct_games_udp_enabled/);
  assert.match(manager, /UDP_TUNNEL_EXCLUSION_CATALOG/);
  assert.match(manager, /udp_tunnel_exclusion_rules\(routing\)/);
  assert.match(manager, /tunnel_game_rules\(routing\)/);
  assert.ok(manager.indexOf("rules.extend(tunnel_game_rules(routing))") < manager.indexOf("rules.extend(direct_game_rules(routing))"), "tunnel-required games must win over direct game rules");
  assert.match(manager, /TUNNEL_GAME_PROCESSES, "GATE\.312", DEFAULT_TUNNEL_GAMES/);
  assert.match(manager, /DIRECT_GAME_PROCESSES, "DIRECT", DEFAULT_DIRECT_GAMES/);
  assert.match(manager, /\[\*udp_tunnel_exclusion_rules\(routing\), "NETWORK,UDP,DIRECT"\]/);
  assert.ok(manager.indexOf("udp_tunnel_exclusion_rules(routing)") < manager.indexOf('"NETWORK,UDP,DIRECT"'), "UDP tunnel exclusions must precede the catch-all direct rule");
  assert.match(manager, /NETWORK,UDP,DIRECT/);
  assert.doesNotMatch(manager, /AND,\(\(\{process_rule\}\),\(NETWORK,UDP\)\),DIRECT/);
  assert.match(manager, /profile_routing\.get\(key, False\)/);
  assert.match(manager, /def configured_preset_rules/);
  assert.match(manager, /"rule_lists": routing_rule_lists\(values\)/);
  assert.match(manager, /PROCESS-NAME-WILDCARD/);
  assert.match(manager, /find-process-mode: strict/);
  assert.match(manager, /def endpoint_latency_ms/);
  assert.match(manager, /"latency_ms": min\(latencies\) if latencies else None/);
  assert.match(page, /request\("\/mihomo\/dns\/settings"\)/);
  assert.match(page, /request\("\/mihomo\/routing\/schema"\)/);
  assert.match(page, /className="mihomoDnsWorkspace mihomoDnsV2"/);
  assert.match(page, /<Tab id="dns"[^>]*>DNS<\/Tab>/);
  assert.doesNotMatch(page, />DNS Mihomo<\/Tab>/);
  assert.match(page, /Clash Verge Rev/);
  assert.match(page, /apps\.apple\.com\/us\/app\/clash-mi\/id6744321968/);
  assert.match(page, /MetaCubeX\/ClashMetaForAndroid\/releases/);
  assert.match(page, /Маршруты игр/);
  assert.match(page, /setGameRoute/);
  assert.match(page, /const ruleIconGroups/);
  assert.match(page, /toggleRuleIconGroup/);
  assert.match(page, /ruleExtraLines/);
  assert.match(page, /updateRuleExtras/);
  assert.doesNotMatch(page, /mihomoHint">NETWORK,UDP,DIRECT/);
  assert.match(page, /mihomoCatalogToolbar/);
  assert.match(page, /visibleRuleGroups/);
  assert.match(page, /direct_downloads/);
  assert.match(page, /p2pClientCatalog/);
  assert.match(page, /toggleP2pClient/);
  assert.doesNotMatch(page, /<b>Дополнительные процессы через VPN<\/b>/);
  assert.match(page, /mihomoProfileRuleButton/);
  assert.match(page, /profileDirectRules/);
  assert.match(page, /const ruleCount = profileDirectRules\.filter/);
  assert.match(page, /<small>ПРАВИЛА<\/small><b>\{ruleCount\}<\/b>/);
  assert.match(page, /QRCode\.toDataURL\(subscription/);
  assert.match(page, /mihomoProfileEditorHead/);
  assert.match(page, /mihomoProfileWorkspace/);
  assert.match(page, /mihomoProfileRail/);
  assert.match(page, /aria-label="Раздел настроек"/);
  assert.match(page, /mihomoOverviewV3/);
  assert.match(page, /mihomoOverviewPulse/);
  assert.doesNotMatch(page, /mihomoOverviewShortcuts/);
  assert.match(page, /mihomoHeroArt/);
  assert.match(page, /mihomoHeroArt[\s\S]*mihomoHeroNavigation/);
  assert.doesNotMatch(page, /<small>пинг<\/small>/);
  assert.doesNotMatch(page, /<small>PING<\/small>/);
  assert.doesNotMatch(page, /item\?\.latency_ms/);
  assert.match(styles, /new-operator\/mihomo-control-v2\.png/);
  assert.match(styles, /\.mihomoCommandHero \{[^}]*border:0[^}]*box-shadow:none/s);
  assert.match(styles, /\.mihomoCommandHero::after \{[^}]*radial-gradient[^}]*box-shadow:inset/s);
  assert.match(styles, /\.mihomoCommandHero \.mihomoHeroArt \{[^}]*position:absolute[^}]*background-color:#01010e[^}]*background-image:url\("\/gate-art\/new-operator\/mihomo-control-v2\.png"\)[^}]*background-size:contain/s);
  assert.match(page, /overviewIssueTargets/);
  assert.match(page, /overviewActiveConnections/);
  assert.match(page, /overviewIssues/);
  assert.match(page, /mihomoDnsV2/);
  assert.match(page, /mihomoDnsProviders/);
  assert.match(page, /mihomoDnsAdvanced/);
  assert.match(manager, /cache-algorithm:/);
  assert.match(manager, /fake-ip-filter:/);
  assert.match(dnsManifest.settings.map((item) => item.key).join(","), /ipv6,prefer_h3,cache_algorithm,fake_ip_filter/);
  assert.match(page, /mihomoModuleCatalogV2/);
  assert.match(page, /moduleCapabilities/);
  assert.match(page, /expandedDeviceLists/);
  assert.match(page, /expandedProtocolLists/);
  assert.match(page, /mihomoProfileToggle/);
  assert.match(page, /mihomoDeviceToggle/);
  assert.doesNotMatch(page, /Скрыть устройства/);
  assert.doesNotMatch(page, /Скрыть каналы/);
  assert.doesNotMatch(page, /Получено \{bytes\(item\?\.rx_bytes/);
  assert.match(page, /mihomoProfileCanvas/);
  assert.match(styles, /\.mihomoProfileDialog \{ width:94vw; height:92dvh;/);
  assert.match(polish, /\.mihomoDialog:not\(\.mihomoProfileDialog\)/);
  assert.doesNotMatch(polish, /\.mihomoDialog\) \{\s*width:min\(620px/);
  assert.match(page, /setProfileStrategy/);
  assert.match(page, /title: "Общие"/);
  assert.match(page, /title: "Резерв"/);
  assert.match(page, /Показывать Selector в Mihomo-клиенте/);
  assert.match(page, /if \(!profileStrategyTouched\) setProfileDevices/);
  assert.match(page, /activeProfileRouting/);
  assert.match(page, /Правила устройства/);
  assert.match(page, /Профиль готов/);
  assert.match(page, /Скопировать ссылку/);
  assert.match(page, /title: "UDP"/);
  assert.match(page, /udpExclusionCatalog/);
  assert.match(page, /udp_tunnel_exclusions_rules/);
  assert.match(page, /Маршруты игр/);
  assert.match(page, /setGameRoute/);
  assert.doesNotMatch(page, /title: "Игры через VPN"/);
  assert.match(page, /EA Sports FC 26/);
  assert.match(page, /toggleProfileRule/);
  assert.match(page, /Применяются только к подписке и YAML выбранного устройства/);
  assert.match(page, /mihomoRuleStudio/);
  assert.match(page, /сохраняется автоматически/);
  assert.match(page, /routingAutosaveRef/);
  assert.match(page, /available_rules/);
  assert.match(page, /routingDraft\[selectedRuleList\.key\]/);
  assert.match(routingManifest.settings.map((item) => item.key).join(","), /direct_games/);
  assert.match(routingManifest.settings.map((item) => item.key).join(","), /direct_games_enabled/);
  assert.match(routingManifest.settings.map((item) => item.key).join(","), /direct_games_udp_enabled/);
  assert.match(routingManifest.settings.map((item) => item.key).join(","), /udp_tunnel_exclusions_rules/);
  assert.match(routingManifest.settings.map((item) => item.key).join(","), /udp_tunnel_exclusions/);
  assert.doesNotMatch(routingManifest.settings.map((item) => item.key).join(","), /tunnel_restricted_games_enabled/);
  assert.match(routingManifest.settings.map((item) => item.key).join(","), /tunnel_games/);
  assert.match(routingManifest.settings.map((item) => item.key).join(","), /direct_game_processes/);
  assert.match(routingManifest.settings.map((item) => item.key).join(","), /direct_ru_sites_rules/);
  assert.match(routingManifest.settings.map((item) => item.key).join(","), /direct_ru_banks_rules/);
  assert.match(routingManifest.settings.map((item) => item.key).join(","), /direct_ru_marketplaces_rules/);
  assert.match(page, /DNS и маршрутизация Mihomo готовы/);
  assert.match(styles, /\.mihomoPolicyPanel/);
  for (const policy of [dnsManifest, routingManifest]) {
    assert.equal(policy.installable, false);
    assert.equal(policy.automatic, true);
    assert.equal(policy.settings_only, true);
  }
});

test("Mihomo installs and updates the latest verified stable core independently", async () => {
  const [installer, manager, api, control, manifest] = await Promise.all([
    read("protocol-images/mihomo/install.sh"),
    read("protocol-images/mihomo/manager.py"),
    readApiSources(),
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

test("Mihomo QUIC transports use the release asset digest instead of a removed checksum file", async () => {
  const installer = await read("protocol-images/mihomo/modules/transport-hysteria2/install.sh");
  assert.match(installer, /asset\.get\("digest",""\)/);
  assert.match(installer, /asset\["browser_download_url"\]/);
  assert.match(installer, /sha256sum -c -/);
  assert.doesNotMatch(installer, /sing-box-\$\{version\}-checksums\.txt/);
});

test("installed Mihomo module services are discovered from manifests", async () => {
  const api = await readApiSources();
  const moduleIds = ["transport-wg", "transport-awg", "transport-shadowsocks", "transport-reality", "transport-hysteria2", "transport-tuic"];
  assert.match(api, /def mihomo_module_services/);
  assert.match(api, /module_root\.glob\("\*\/manifest\.json"\)/);
  assert.doesNotMatch(api, /MIHOMO_MODULE_SERVICES/);
  for (const moduleId of moduleIds) {
    const manifest = JSON.parse(await read(`protocol-images/mihomo/modules/${moduleId}/manifest.json`));
    assert.match(manifest.service, /\.(?:service|target)$/);
  }
});

test("Mihomo VLESS is a reusable component with profile-scoped Direct and CDN connections", async () => {
  const [manifestText, installer, manager, view] = await Promise.all([
    read("protocol-images/mihomo/modules/transport-reality/manifest.json"),
    read("protocol-images/mihomo/modules/transport-reality/install.sh"),
    read("protocol-images/mihomo/manager.py"),
    readMihomoSources(),
  ]);
  const manifest = JSON.parse(manifestText);
  const settingKeys = manifest.settings.map((item) => item.key);
  const connectionKeys = manifest.connection_settings.map((item) => item.key);
  assert.equal(manifest.name, "VLESS");
  assert.equal(manifest.version, "1.2.0");
  for (const key of ["port_start", "cdn_port_start", "dns", "loglevel"]) {
    assert.ok(settingKeys.includes(key), `missing Mihomo VLESS core setting ${key}`);
  }
  for (const key of ["port", "target", "transport", "transport_path", "xhttp_mode", "xpadding", "xmux_concurrency", "cdn_enabled", "cdn_domain", "cdn_transport", "cdn_xhttp_mode"]) {
    assert.ok(connectionKeys.includes(key), `missing Mihomo VLESS connection setting ${key}`);
  }
  assert.match(installer, /startsWith\('mihomo-vless-'\)|startswith\('mihomo-vless-'\)/);
  assert.doesNotMatch(installer, /geoip:private/, "the minimal Xray install does not include geoip.dat");
  assert.match(installer, /'10\.0\.0\.0\/8'.*'fc00::\/7'/s);
  assert.match(installer, /config\.candidate\.json/);
  assert.match(installer, /run -test -config "\$\{candidate\}"/);
  assert.match(manager, /with_name\(f"\{config_path\.stem\}\.candidate\.json"\)/);
  assert.doesNotMatch(manager, /with_suffix\("\.candidate"\)/);
  assert.match(manager, /class ProfileConnectionInput/);
  assert.match(manager, /def validate_connection_inputs/);
  assert.match(manager, /def render_vless_cdn/);
  assert.match(manager, /cdn_transport/);
  assert.match(manager, /cdn_xhttp_mode/);
  assert.match(view, /removeProfileDevice/);
  assert.match(view, /Удалить устройство/);
  assert.match(manager, /def rebuild_vless_cdn_snippet/);
  assert.match(manager, /VLESS_CDN_ROUTE_ROOT/);
  assert.match(manager, /route_id = f"\{profile_id\}-\{connection_id\}"/);
  assert.match(manager, /batch_reality = any\(definition\["component"\] == "transport-reality" for definition in definitions\)/);
  assert.match(manager, /defer_reality_restart=batch_reality/);
  assert.match(manager, /original_config = config_path\.read_bytes\(\)/);
  assert.match(manager, /component != "transport-reality" and singleton_key in used_singletons/);
  assert.match(manager, /class ProfileDeviceInput/);
  assert.match(manager, /device_id: str/);
  assert.match(manager, /render_profile\(item: dict\[str, Any\], device_id/);
  assert.match(manager, /network: \{'tcp' if transport == 'raw' else transport\}/);
  assert.match(manager, /grpc-service-name/);
  assert.match(manager, /call_module_script\(module_id, "install"\)[\s\S]*rollback failed/);
  assert.match(view, /connections: profileConnections/);
  assert.match(view, /cdn_enabled/);
  assert.match(view, /\["xhttp_mode", "xpadding", "xmux_concurrency"\]/);
  assert.match(manager, /Все VLESS/);
  assert.match(manager, /httpupgrade/);
  assert.match(manager, /render_vless_tls/);
  assert.match(manager, /mihomo-vless-tls-/);
  assert.ok(manager.indexOf('("PUBLIC_IPV4", "PUBLIC_IP", "PUBLIC_ENDPOINT")') > -1, "Mihomo TLS must compare DNS with the VPS IP before a proxied panel hostname");
  assert.match(view, /VLESS CDN · HTTPUpgrade/);
  assert.match(manager, /def default_profile_presets/);
  assert.match(manager, /\/api\/mihomo\/routing\/presets/);
  assert.match(manager, /"summary": \{"configured": len\(values\)/);
  assert.doesNotMatch(view, /downloadConfig\(createdProfile\)/);
  assert.match(view, /width=\{240\} height=\{240\}/);
  assert.match(view, /mihomoProfileSummary/);
  assert.match(view, /mihomoProfileDevices/);
  assert.match(view, /setProfileStep\(1\)[\s\S]*>Общее<\/button>/);
  assert.match(view, /mihomoProfileGeneral/);
  assert.match(view, /Название HWID-устройства/);
  assert.match(view, /mihomoConnectionQuickDelete[^>]*title="Удалить подключение"[\s\S]*<span aria-hidden="true">×<\/span><\/button>/);
  assert.match(view, /Настроить пресеты/);
  assert.match(view, /preset_cdn_domain/);
  assert.match(view, /Создать подключения из пресета/);
  assert.doesNotMatch(view, /profileDialog === "new" && <section className="mihomoPresetPicker"/);
  assert.doesNotMatch(view, /const module = modules\.find/, "Next.js reserves the local variable name module");
});

test("Mihomo profiles expose one subscription and register optional HWID devices", async () => {
  const [manager, view] = await Promise.all([
    read("protocol-images/mihomo/manager.py"), readMihomoSources(),
  ]);
  assert.match(manager, /"subscription_token": secrets\.token_urlsafe\(32\)/);
  assert.match(manager, /"obsolete" if result\["subscriptions"\]/);
  assert.match(manager, /request\.headers\.get\("x-device-id"\)/);
  assert.match(manager, /request\.headers\.get\("x-hwid"\)/);
  assert.match(manager, /request\.query_params\.get\("hwid"\)/);
  assert.match(manager, /hmac\.new\(token\.encode\(\), raw_hwid\.encode\(\), hashlib\.sha256\)/);
  assert.match(manager, /def subscription_device/);
  assert.match(manager, /provision_connections\(str\(profile\["id"\]\), definitions, privacy_enabled=bool\(inherited_routing/);
  assert.match(view, /подписка устарела, требуется новая установка/);
  assert.match(view, /Клиент с HWID появится как отдельное устройство/);
  assert.doesNotMatch(view, /subscription\?device_id=/);
});

test("Mihomo profiles have an explicit common configuration layer", async () => {
  const [manager, view] = await Promise.all([
    read("protocol-images/mihomo/manager.py"), readMihomoSources(),
  ]);
  assert.match(view, /function clientUuid\(\): string/);
  assert.match(view, /typeof crypto\.randomUUID === "function"/);
  assert.match(view, /crypto\.getRandomValues\(bytes\)/);
  assert.doesNotMatch(view, /useRef\(crypto\.randomUUID\(\)\)/);
  assert.match(manager, /result\["common_device_id"\] = common_device_id/);
  assert.match(manager, /"scope": "common" if/);
  assert.match(manager, /selected_device = device_id or str\(normalized\["common_device_id"\]\)/);
  assert.match(manager, /template_id = str\(normalized\["common_device_id"\]\)/);
  assert.match(manager, /Общие настройки профиля нельзя удалить/);
  assert.match(view, /Общие настройки профиля/);
  assert.match(view, /HWID-устройства/);
  assert.match(view, /для клиентов без HWID и новых устройств/);
  assert.doesNotMatch(view, />\+ Устройство<\/button>/);
});

test("Mihomo HWID devices retain client and platform metadata", async () => {
  const [manager, view] = await Promise.all([
    read("protocol-images/mihomo/manager.py"), readMihomoSources(),
  ]);
  for (const header of ["x-device-os", "x-device-name", "x-client-name", "x-client-version", "user-agent"]) {
    assert.match(manager, new RegExp(`request\\.headers\\.get\\("${header}"\\)`));
  }
  assert.match(manager, /device_os = "android"/);
  assert.match(manager, /device_os = "ios"/);
  assert.match(manager, /device_os = "windows"/);
  assert.match(manager, /request\.headers\.get\("x-os-version"\)/);
  assert.match(manager, /iPhone OS\|iOS/);
  assert.match(manager, /os_version = version_match\.group\(1\)\.replace\("_", "\."\)/);
  assert.match(manager, /existing\["last_seen_at"\]/);
  assert.match(manager, /class ProfileDeviceInput[\s\S]*hwid_hash[\s\S]*client_name[\s\S]*last_seen_at/);
  assert.match(view, /const devicePlatform/);
  assert.match(view, /function deviceSystemLabel/);
  assert.match(view, /deviceSystemLabel\(device\)/);
  assert.match(view, /Последний запрос/);
});

test("Mihomo UI does not count the common configuration as a device", async () => {
  const view = await readMihomoSources();
  assert.match(view, /function registeredProfileDevices/);
  assert.match(view, /device\.scope !== "common" && device\.id !== profile\.common_device_id/);
  assert.match(view, /HWID-устройства пока не зарегистрированы/);
  assert.match(view, /Параметры профиля[\s\S]*HWID-устройства/);
  assert.match(view, /profileDevices\.filter\(\(device\) => device\.scope === "common"\)/);
});

test("Mihomo deduplicates migrated common devices and keeps clients without HWID in the common pool", async () => {
  const [manager, view] = await Promise.all([
    read("protocol-images/mihomo/manager.py"), readMihomoSources(),
  ]);
  assert.match(manager, /unique_devices_by_id: dict\[str, dict\[str, Any\]\] = \{\}/);
  assert.match(manager, /device\.get\("hwid_hash"\) and not current\.get\("hwid_hash"\)/);
  assert.match(manager, /not stored_common_id\.startswith\("hwid-"\)/);
  assert.match(manager, /not str\(connection\.get\("id", ""\)\)\.startswith\("hwid-"\)/);
  assert.match(manager, /def record_common_subscription_access/);
  assert.match(manager, /"reason": "hwid_missing"/);
  assert.match(view, /\.filter\(\(device\) => device\.scope === "common"\)\.slice\(0, 1\)/);
  assert.doesNotMatch(view, /HWID не передан — персональное устройство/);
  assert.doesNotMatch(view, /mihomoCommonAccessNotice/);
});

test("Mihomo profile lifecycle is transactional, idempotent and reconciled", async () => {
  const [manager, view, uninstall] = await Promise.all([
    read("protocol-images/mihomo/manager.py"),
    readMihomoSources(),
    read("protocol-images/mihomo/uninstall.sh"),
  ]);
  assert.match(manager, /def profile_runtime_transaction/);
  assert.match(manager, /@transactional_profile_mutation/);
  assert.match(manager, /create_operation_id/);
  assert.match(view, /operation_id: profileMutationId\.current/);
  assert.match(manager, /def apply_batched_reality_runtime/);
  assert.match(manager, /def reconciliation_report/);
  assert.match(manager, /\/api\/mihomo\/reconciliation/);
  assert.match(manager, /validate_rendered_profile\(config\)/);
  assert.match(manager, /f"ipv6: \{str\(bool\(dns\.get\('ipv6', False\)\)\)\.lower\(\)\}"/);
  assert.match(manager, /Hysteria2 and TUIC must use different UDP ports/);
  assert.match(uninstall, /modules\/transport-\*/);
  assert.match(uninstall, /vps-control-mihomo-hysteria2\.service/);
  assert.match(uninstall, /vps-control-mihomo-tuic\.service/);
});

test("overview aggregates network usage for Mihomo profiles and direct protocols", async () => {
  const [manager, overview, styles] = await Promise.all([
    read("protocol-images/mihomo/manager.py"),
    read("src/features/overview/overview-view.tsx"),
    readStyles(),
  ]);
  assert.match(manager, /@app\.get\("\/api\/mihomo\/stats"/);
  assert.match(manager, /profile_stats_payload\(item\)/);
  assert.match(overview, /mihomoProfileStats\[profile\.id\]/);
  assert.match(overview, /protocolClients\.reduce\(\(sum, client\) => sum \+ \(client\.rx_bps \|\| 0\)/);
  assert.match(overview, /hasClientRates \? clientRx : rate\.rx/);
  assert.match(styles, /\.overviewManagedTraffic/);
  assert.doesNotMatch(styles, /\.overviewRoute\.direct \.overviewDirectTraffic,\.overviewRoute\.direct \.overviewDirectLatency \{ display:none/);
});

test("Mihomo provides verified Hysteria2 and TUIC v5 transports", async () => {
  const [manager, view, hysteriaManifest, tuicManifest, installer] = await Promise.all([
    read("protocol-images/mihomo/manager.py"),
    readMihomoSources(),
    read("protocol-images/mihomo/modules/transport-hysteria2/manifest.json"),
    read("protocol-images/mihomo/modules/transport-tuic/manifest.json"),
    read("protocol-images/mihomo/modules/transport-hysteria2/install.sh"),
  ]);
  assert.equal(JSON.parse(hysteriaManifest).id, "transport-hysteria2");
  assert.equal(JSON.parse(tuicManifest).id, "transport-tuic");
  assert.match(manager, /def write_quic_runtime/);
  assert.match(manager, /type: hysteria2/);
  assert.match(manager, /type: tuic/);
  assert.match(manager, /zero_rtt_handshake": False/);
  assert.match(view, /Hysteria2/);
  assert.match(view, /TUIC v5/);
  assert.match(installer, /asset_digest[\s\S]*sha256sum -c -/);
  assert.match(installer, /quic\/hysteria2\/config\.json[\s\S]*quic\/tuic\/config\.json/);
  assert.match(installer, /"\$candidate" check -c "\$existing_config"/);
});
