import assert from "node:assert/strict";
import test from "node:test";
import { read, readUiSources, readApiSources } from "./support.mjs";

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
  const ids = ["wireguard", "amneziawg", "shadowsocks", "vless-reality-xhttp", "mihomo", "hysteria2", "tuic", "trojan", "openvpn", "ikev2"];
  const manifests = await Promise.all(ids.map((id) => read(`protocol-images/${id}/manifest.json`).then(JSON.parse)));
  for (const manifest of manifests.slice(0, 10)) {
    assert.equal(manifest.installable, true);
    assert.deepEqual(manifest.supported_os, ["ubuntu", "debian"]);
    assert.ok(Array.isArray(manifest.preflight_packages));
    assert.ok(manifest.minimum_free_mb >= 128);
    assert.equal(typeof manifest.requires_kernel_headers, "boolean");
  }
  for (const [index, manifest] of manifests.slice(10).entries()) {
    assert.equal(manifest.installable, false);
    assert.equal(manifest.installer, "install.sh");
    assert.equal(manifest.uninstaller, "uninstall.sh");
    const directory = ids[index + 10];
    const [install, uninstall] = await Promise.all([
      read(`protocol-images/${directory}/install.sh`),
      read(`protocol-images/${directory}/uninstall.sh`),
    ]);
    assert.match(install, /exit 2/);
    assert.match(uninstall, /удаление не требуется/i);
  }
  const api = await readApiSources();
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

test("WG and AWG modules install and uninstall independently", async () => {
  const [api, manager, wgInstall, awgInstall, mihomoAwgInstall, wgRemove, awgRemove] = await Promise.all([
    readApiSources(), read("scripts/vps-control.sh"),
    read("protocol-images/wireguard/install.sh"),
    read("protocol-images/amneziawg/install.sh"),
    read("protocol-images/mihomo/modules/transport-awg/install.sh"),
    read("protocol-images/wireguard/uninstall.sh"),
    read("protocol-images/amneziawg/uninstall.sh"),
  ]);
  const baseDependencies = manager.match(/Установка системных зависимостей" apt-get install -y ([^\n]+)/)?.[1] || "";
  assert.doesNotMatch(baseDependencies, /wireguard-tools/);
  assert.match(api, /нельзя остановить последний канал подписки Mihomo/);
  assert.match(wgRemove, /delete route allow in on "\$\{WG_INTERFACE\}" out on "\$\{UPLINK_INTERFACE\}" from "\$\{WG_SUBNET\}"/);
  assert.match(awgRemove, /delete route allow in on "\$\{AWG_INTERFACE\}" out on "\$\{UPLINK_INTERFACE\}" from "\$\{AWG_SUBNET\}"/);
  assert.match(wgRemove, /99-vps-control-wireguard\.conf/);
  assert.match(awgRemove, /99-vps-control-amneziawg\.conf/);
  assert.match(api, /protocol-install/);
  assert.match(api, /AWG_CLIENT_PORT = int\(os\.getenv\("AWG_CLIENT_PORT", "53020"\)\)/);
  assert.match(api, /client_listen_port = AWG_CLIENT_PORT if payload\.protocol == "awg" else None/);
  // Address selection and preservation of saved WG/AWG endpoints are exercised
  // through client creation in test_server_addresses.py.
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
  assert.match(awgInstall, /install -y --allow-change-held-packages amneziawg amneziawg-tools amneziawg-dkms/);
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

test("direct tunnel lifecycle cannot target Mihomo-reserved identities", async () => {
  const [wgInstall, wgRemove, awgInstall, awgRemove, mihomoWgRemove, mihomoAwgRemove] = await Promise.all([
    read("protocol-images/wireguard/install.sh"),
    read("protocol-images/wireguard/uninstall.sh"),
    read("protocol-images/amneziawg/install.sh"),
    read("protocol-images/amneziawg/uninstall.sh"),
    read("protocol-images/mihomo/modules/transport-wg/uninstall.sh"),
    read("protocol-images/mihomo/modules/transport-awg/uninstall.sh"),
  ]);
  const directGuard = /\$\{(?:WG|AWG)_INTERFACE\}" != mh-\*.*basename --.*mh-\*\.conf/s;
  for (const script of [wgInstall, wgRemove, awgInstall, awgRemove]) {
    assert.match(script, directGuard, "direct WireGuard-family scripts must reject Mihomo identities");
  }
  assert.match(mihomoWgRemove, /INTERFACE="mh-wg0"/);
  assert.match(mihomoWgRemove, /CONFIG="\/etc\/wireguard\/\$\{INTERFACE\}\.conf"/);
  assert.match(mihomoAwgRemove, /INTERFACE="mh-awg0"/);
  assert.match(mihomoAwgRemove, /CONFIG="\/etc\/amnezia\/amneziawg\/\$\{INTERFACE\}\.conf"/);
  assert.doesNotMatch(wgRemove + awgRemove, /rm -rf[^\n]*\/etc\/vps-control\/mihomo/);
});

test("direct protocol lifecycle releases module and client port reservations", async () => {
  const [manager, allocator, ikev2Remove] = await Promise.all([
    read("scripts/vps-control.sh"),
    read("api/port_allocation.py"),
    read("protocol-images/ikev2/uninstall.sh"),
  ]);
  assert.match(manager, /remove_protocol_image\(\)[\s\S]*?release_protocol_ports "\$\{image_id\}"/);
  assert.match(manager, /RESERVED_PROTOCOL_IMAGE="\$\{image_id\}"/);
  assert.match(manager, /release_protocol_ports "\$\{RESERVED_PROTOCOL_IMAGE\}"/);
  assert.match(allocator, /def release_module\(module: str\)/);
  assert.match(allocator, /"panel:ss:" if module == "shadowsocks"/);
  assert.doesNotMatch(ikev2Remove, /rm -rf \/etc\/swanctl(?:\s|\/)/);
  assert.match(ikev2Remove, /swanctl\/swanctl\.conf/);
});

test("the complete protocol matrix has isolated direct and Mihomo ownership", async () => {
  const direct = {
    wg: ["wireguard", "/etc/wireguard/", "wg-quick@"],
    awg: ["amneziawg", "/etc/amnezia/amneziawg/", "awg-quick@"],
    shadowsocks: ["shadowsocks", "/etc/vps-control/shadowsocks", "vps-control-shadowsocks"],
    "vless-reality-xhttp": ["vless-reality-xhttp", "/etc/vps-control/vless-reality-xhttp", "vps-control-vless-reality-xhttp"],
    hysteria2: ["hysteria2", "/etc/vps-control/hysteria2", "vps-control-hysteria2"],
    tuic: ["tuic", "/etc/vps-control/tuic", "vps-control-tuic"],
    trojan: ["trojan", "/etc/vps-control/trojan", "vps-control-trojan"],
    openvpn: ["openvpn", "/etc/vps-control/openvpn", "vps-control-openvpn"],
    ikev2: ["ikev2", "/etc/vps-control/ikev2", "vps-control-ikev2"],
  };
  const mihomo = {
    "transport-wg": ["transport-wg", "/etc/wireguard/", 'INTERFACE="mh-wg0"'],
    "transport-awg": ["transport-awg", "/etc/amnezia/amneziawg/", 'INTERFACE="mh-awg0"'],
    "transport-shadowsocks": ["transport-shadowsocks", "/etc/vps-control/mihomo/shadowsocks", "vps-control-mihomo-ss"],
    "transport-reality": ["transport-reality", "/etc/vps-control/mihomo/reality", "vps-control-mihomo-reality"],
    "transport-hysteria2": ["transport-hysteria2", "/etc/vps-control/mihomo/quic/hysteria2", "vps-control-mihomo-hysteria2"],
    "transport-tuic": ["transport-tuic", "/etc/vps-control/mihomo/quic/tuic", "vps-control-mihomo-tuic"],
  };
  const directSources = await Promise.all(Object.values(direct).map(async ([directory]) => read(`protocol-images/${directory}/uninstall.sh`)));
  const mihomoSources = await Promise.all(Object.values(mihomo).map(async ([directory]) => read(`protocol-images/mihomo/modules/${directory}/uninstall.sh`)));
  for (const [index, [, pathMarker, unitMarker]] of Object.values(direct).entries()) {
    assert.match(directSources[index], new RegExp(pathMarker.replaceAll("/", "\\/")));
    assert.match(directSources[index], new RegExp(unitMarker));
  }
  for (const [index, [, pathMarker, unitMarker]] of Object.values(mihomo).entries()) {
    assert.match(mihomoSources[index], new RegExp(pathMarker.replaceAll("/", "\\/")));
    assert.match(mihomoSources[index], new RegExp(unitMarker));
  }
  const mihomoRemove = await read("protocol-images/mihomo/uninstall.sh");
  for (const marker of [
    "vps-control-mihomo-capabilities.service",
    "vps-control-mihomo-capabilities.timer",
    "/usr/local/lib/vps-control-mihomo",
    "/var/lib/vps-control/mihomo-capabilities.json",
  ]) assert.match(mihomoRemove, new RegExp(marker.replaceAll("/", "\\/")));
});

test("Shadowsocks and VLESS REALITY XHTTP are independent installable modules", async () => {
  const [api, manager, page, ssManifest, ssInstall, ssRemove, vlessManifest, vlessInstall, vlessRemove] = await Promise.all([
    readApiSources(), read("scripts/vps-control.sh"), readUiSources(),
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
  assert.match(vlessInstall, /source .*scripts\/xray-core\.sh/);
  assert.match(vlessInstall, /stage_xray_core "\$\{tmp_dir\}\/xray"/);
  assert.doesNotMatch(vlessInstall, /Xray-core\/releases\/latest/);
  assert.doesNotMatch(vlessInstall, /api\.github\.com/);
  assert.match(vlessInstall, /XHTTP_PATH/);
  assert.doesNotMatch(vlessInstall, /source "\$\{CONFIG_DIR\}\/reality\.env"/);
  assert.match(vlessInstall, /AmbientCapabilities=CAP_NET_BIND_SERVICE/);
  assert.match(vlessInstall, /ss -H -ltn/);
  assert.match(vlessInstall, /limitFallbackUpload/);
  assert.match(vlessInstall, /StatsService/);
  // The generated loopback listener and selected port are exercised in test_panel_ports.py.
  assert.match(vlessInstall, /statsUserUplink/);
  assert.match(api, /xray_user_stats/);
  assert.match(api, /service_bytes/);
  assert.match(ssInstall, /IPAccounting=true/);
  assert.match(vlessInstall, /xray" tls ping "\$\{TARGET\}"/);
  assert.match(vlessInstall, /certificate_length.*-le 3500/s);
  assert.match(vlessInstall, /TARGET=.*ya\.ru:443/);
  assert.match(vlessInstall, /serverNames.*ya\.ru.*yandex\.ru.*vk\.com.*mail\.ru.*rutube\.ru.*ozon\.ru/);
  assert.match(vlessInstall, /sed -i "s\|\^TARGET=\.\*\|TARGET=\$\{TARGET\}\|"/);
  assert.match(manager, /VLESS_REALITY_TARGET="ya\.ru:443"/);
  assert.doesNotMatch(vlessInstall + vlessRemove, /wg-quick|awg-quick|shadowsocks/);
  assert.match(api, /Literal\["wg", "awg", "shadowsocks", "vless-reality-xhttp"[^\]]*\]/);
  assert.match(api, /vless:\/\//);
  assert.match(api, /ss:\/\//);
  assert.match(api, /Literal\["wg", "awg", "shadowsocks", "vless-reality-xhttp"[^\]]*\]/);
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
  assert.match(api, /"no_delay": payload\.settings\.no_delay, "mtu": payload\.settings\.mtu or 1200/);
  assert.match(ssInstall, /config\["mtu"\] = 1200/);
  assert.doesNotMatch(manager, /PUBLIC_IP="\$\{PUBLIC_IP\}"/);
  assert.match(manager, /PUBLIC_IP="\$\(env_value PUBLIC_IP\)"/);
  assert.match(page, /protocolOperationOutcome\(started, status\.action, Boolean\(current\?\.installed\) === installed\)/);
  for (const operation of ["install", "remove", "update"]) {
    const body = manager.match(new RegExp(`${operation}_protocol_image\\(\\) \\{[\\s\\S]*?\\n\\}`))?.[0] || "";
    assert.match(body, /refresh_protocol_api_access/);
    assert.doesNotMatch(body, /systemctl restart "\$\{APP_NAME\}-api\.service"/);
  }
  assert.equal(JSON.parse(vlessManifest).id, "vless-reality-xhttp");
  assert.match(vlessInstall, /"\$\{candidate\}" run -test -config/);
  assert.match(vlessInstall, /mv -f -- "\$\{candidate\}" "\$\{MODULE_DIR\}\/xray"/);
  assert.match(vlessInstall, /systemctl restart vps-control-vless-reality-xhttp\.service/);
  assert.match(vlessInstall, /восстановлена предыдущая/);
  assert.match(manager, /ReadWritePaths=-\/etc\/vps-control\.env -\/etc\/vps-control /);
  assert.match(manager, /ENV_FILE="\$\{CONFIG_DIR\}\/environment"/);
  assert.match(manager, /mv "\$\{LEGACY_ENV_FILE\}" "\$\{ENV_FILE\}"/);
  assert.match(manager, /ln -sfn "\$\{ENV_FILE\}" "\$\{LEGACY_ENV_FILE\}"/);
  assert.match(manager, /install -d -m 0750 -o root -g nogroup "\$\{CONFIG_DIR\}"/);
  assert.match(manager, /grep -Fxq "\$\{expected\}" "\$\{SERVICE_FILE\}"/);
  assert.match(manager, /start_services\(\) \{[\s\S]*?ensure_api_write_access[\s\S]*?systemctl start/);
  assert.match(manager, /restart_services\(\) \{[\s\S]*?ensure_api_write_access[\s\S]*?systemctl restart/);
});

test("DNS API preserves component application and encrypted fallback boundaries", async () => {
  const api = await readApiSources();
  // Only API/configuration contracts, not provider labels or editor markup.
  assert.match(api, /@app\.get\("\/api\/dns"\)/);
  assert.match(api, /@app\.post\("\/api\/dns\/check", dependencies=\[Depends\(require_active_client_api\)\]\)/);
  for (const key of ["WG_DNS", "AWG_DNS", "SHADOWSOCKS_DNS", "VRX_DNS"]) {
    assert.ok(api.includes('env_updates["' + key + '"]'), key + " remains independently applied");
  }
  const encryptedDns = api.match(/def dns_vrx_servers\([\s\S]*?(?=\ndef )/)?.[0] || "";
  assert.match(encryptedDns, /if not settings\.get\("prefer_encrypted"\):\s+return addresses/);
  assert.match(encryptedDns, /selected\.append\(/);
  assert.match(encryptedDns, /if any\(not item\.get\("doh_url", ""\)\.startswith\("https:\/\/"\)/);
  assert.match(encryptedDns, /raise HTTPException\(status_code=422/);
  assert.match(encryptedDns, /return list\(dict\.fromkeys\(item\["doh_url"\]/);
  assert.doesNotMatch(encryptedDns, /\.insert\(|\.extend\(addresses\)/);
  assert.match(api, /setdefault\("sockopt", \{\}\)\["domainStrategy"\] = "ForceIP"/);
  assert.match(api, /apply_vrx_dns\(vrx_servers[,)]/);
  assert.match(api, /if set\(profiles\) - allowed_scopes:/);
});

test("VLESS image supports independent REALITY, TLS and CDN profiles", async () => {
  const mihomoManager = await read("protocol-images/mihomo/manager.py");
  const [bootstrap, manager, install, uninstall, api, caddy, config] = await Promise.all([
    read("scripts/install-panel.sh"), read("scripts/vps-control.sh"),
    read("protocol-images/vless-reality-xhttp/install.sh"),
    read("protocol-images/vless-reality-xhttp/uninstall.sh"), readApiSources(),
    read("Caddyfile"), read("install.conf"),
  ]);
  assert.match(manager, /set_env_value "VLESS_CDN_DOMAIN"/);
  assert.match(manager, /VLESS_CDN_PORT/);
  assert.match(manager, /python3 "\$\{policy\}" firewall/);
  assert.doesNotMatch(bootstrap, /--vless-cdn-domain/);
  assert.match(config, /VLESS_CDN_PORT="10087"/);
  assert.match(caddy, /import \/etc\/caddy\/vps-control\.d\/\*\.caddy/);
  assert.match(install, /"tag": "vless-cdn"/);
  assert.match(install, /"listen": "127\.0\.0\.1"/);
  assert.match(install, /existing_clients_by_id/);
  assert.match(install, /saved_cdn_domain/);
  assert.match(install, /CDN_ENABLED/);
  assert.match(install, /vless-cdn\.caddy/);
  assert.match(install, /cdn_security\.py rebuild/);
  const cdnPolicy = await read("api/cdn_security.py");
  assert.match(cdnPolicy, /mihomo\/reality\/caddy-routes/);
  assert.match(install, /Always rebuild it/);
  assert.match(cdnPolicy, /fcntl\.LOCK_EX/);
  assert.match(install, /caddy validate/);
  assert.match(uninstall, /cdn_security\.py rebuild/);
  assert.match(api, /def vless_reality_inbound/);
  assert.match(api, /def vless_cdn_client_query/);
  assert.match(api, /def vless_tls_client_query/);
  assert.match(api, /def validate_vless_client_route/);
  assert.match(api, /route_profiles =/);
  assert.match(api, /def configure_vless_tls/);
  assert.match(api, /DNS only.*Cloudflare/);
  assert.ok(api.indexOf('cdn_enabled = bool(supplied.get("cdn_enabled", current_cdn_enabled))') < api.indexOf('if tls_changed:'), "TLS-only updates must initialize the current CDN state");
  assert.ok(api.indexOf('if isinstance(exc, HTTPException):\n                raise exc') < api.indexOf('logger.exception("VLESS settings transaction failed")'), "validation errors must not restart a healthy VLESS service");
  assert.match(install, /VLESS_TLS_DOMAIN/);
  assert.ok(install.indexOf('CDN_PATH="${CDN_PATH:-${WS_PATH}}"') < install.indexOf('TLS_PATH=${CDN_PATH}-tls'), "TLS path must be written only after CDN_PATH is initialized");
  assert.match(api, /"key": "cdn_enabled"/);
  assert.match(api, /"key": "cdn_domain"/);
  assert.match(api, /"key": "cdn_transport"/);
  assert.match(api, /"key": "cdn_xhttp_mode"/);
  assert.match(api, /def cdn_stream/);
  assert.match(api, /def configure_vless_cdn/);
  assert.match(api, /write_vless_cdn_snippet/);
  assert.match(api, /CONTROL_COMMAND, "vless-cdn-firewall"/);
  assert.match(api, /VLESS_CDN_SNIPPET/);
  assert.match(api, /vless_routes: list\[Literal\["direct", "tls", "cdn"\]\]/);
  assert.match(api, /for inbound in selected_inbounds/);
  assert.match(api, /"profiles": profiles/);
  assert.match(manager, /configure_vless_cdn_firewall/);
  assert.match(api, /Транспорт прямого VLESS/);
  assert.match(api, /Только Direct\. После смены импортируйте Direct-профили заново/);
  assert.doesNotMatch(api, /Literal\["xhttp", "raw", "grpc", "websocket"\]/);
  assert.match(api, /"routes": routes/);
  assert.match(api, /Direct · REALITY\/\{direct_transport\}/);
  assert.match(mihomoManager, /systemctl", "reset-failed", "vps-control-mihomo-reality\.service/);
  assert.match(mihomoManager, /\/api\/mihomo\/subscriptions\/\{token\}/);
  assert.match(mihomoManager, /Profile-Update-Interval/);
  assert.match(mihomoManager, /secrets\.token_urlsafe\(32\)/);
  assert.match(install, /DynamicUser=yes/);
  const controlCenterCss = await read("src/shared/styles/control-center.css");
  assert.match(controlCenterCss, /\.shell small[^}]*font-size:11px !important[^}]*line-height:1\.5 !important/s);
  assert.match(controlCenterCss, /label small[^}]*font-size:12px !important[^}]*line-height:1\.5 !important/s);
});

test("installable protocol images are dispatched independently", async () => {
  const [api, manager] = await Promise.all([readApiSources(), read("scripts/vps-control.sh")]);
  const endpoint = api.match(/def install_protocol_image\([\s\S]*?\n\s*return action/)?.[0] || "";
  const installer = manager.match(/install_protocol_image\(\) \{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(endpoint, /if not image\.get\("installable"\)/);
  assert.match(endpoint, /CONTROL_COMMAND, "protocol-install", image_id/);
  assert.doesNotMatch(endpoint, /installed.*(?:wg|awg|mihomo|shadowsocks)/i);
  assert.match(installer, /get\("id",""\).*== "\$\{image_id\}"/s);
  assert.match(installer, /bash "\$\{image_root\}\/\$\{installer\}"/);
});

test("WG, AWG and Shadowsocks install the latest repository candidates", async () => {
  const [wg, awg, shadowsocks] = await Promise.all([
    read("protocol-images/wireguard/install.sh"),
    read("protocol-images/amneziawg/install.sh"),
    read("protocol-images/shadowsocks/install.sh"),
  ]);
  for (const [script, packageName] of [[wg, "wireguard-tools"], [shadowsocks, "shadowsocks-libev"]]) {
    assert.match(script, /apt-get -o DPkg::Lock::Timeout=300 update/);
    assert.match(script, new RegExp(`apt-get -o DPkg::Lock::Timeout=300 install -y(?: [a-z-]+)* ${packageName}`));
    assert.match(script, new RegExp(`dpkg-query -W -f='\\$\\{Version\\}' ${packageName}`));
    assert.match(script, new RegExp(`apt-cache policy ${packageName}`));
  }
  for (const [script, packageName] of [[wg, "wireguard-tools"], [shadowsocks, "shadowsocks-libev"]]) {
    assert.match(script, new RegExp(`LC_ALL=C apt-cache policy ${packageName}`));
  }
  assert.match(awg, /install -y --allow-change-held-packages amneziawg amneziawg-tools amneziawg-dkms/);
  assert.match(awg, /for awg_package in amneziawg amneziawg-tools amneziawg-dkms/);
  assert.match(awg, /LC_ALL=C apt-cache policy "\$\{awg_package\}"/);
});

test("failed protocol installs roll back partial state and shared packages have owners", async () => {
  const [control, api, directSs, mihomoWg, mihomoAwg, mihomoSs] = await Promise.all([
    read("scripts/vps-control.sh"),
    readApiSources(),
    read("protocol-images/shadowsocks/uninstall.sh"),
    read("protocol-images/mihomo/modules/transport-wg/uninstall.sh"),
    read("protocol-images/mihomo/modules/transport-awg/uninstall.sh"),
    read("protocol-images/mihomo/modules/transport-shadowsocks/uninstall.sh"),
  ]);
  assert.match(control, /Откат частично установленного образа/);
  assert.match(control, /bash "\$\{image_root\}\/\$\{uninstaller\}"/);
  assert.match(api, /modinfo", "-F", "version", "amneziawg"/);
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
  // Capture-mode combinations are exercised by test_profile_modes.py;
  // do not couple this installation contract to the YAML serializer expression.
  assert.match(mihomoManager, /dns_hijack_force/);
  assert.match(mihomoManager, /mixed-port: 7890/);
  for (const installer of [wg, awg, mihomoWg, mihomoAwg]) {
    assert.match(installer, /iptables -C INPUT -p udp --dport/);
  }
  assert.match(ss, /vps-control-shadowsocks-firewall add %i/);
  assert.match(mihomoSs, /vps-control-mihomo-ss-firewall add %i/);
  assert.match(vless, /ExecStartPre=.*iptables -C INPUT -p tcp --dport/);
  assert.match(mihomoReality, /vps-control-mihomo-vless-firewall sync/);
  assert.match(mihomoReality, /VPS_MIHOMO_VLESS/);
  assert.match(mihomoReality, /for tool in iptables ip6tables/);
  assert.match(mihomoReality, /done\nexit 0\nSH/);
});

test("protected PRO access has no standalone removal fallback", async () => {
  const [api, page] = await Promise.all([readApiSources(), readUiSources()]);
  assert.match(api, /subscription_access.available/);
  assert.doesNotMatch(api, /fallback_channels = set/);
  assert.match(page, /role=\{item.state === "error" \? "alert" : "status"\}/);
});

test("application network check covers every installed protected protocol", async () => {
  const [manager] = await Promise.all([
    read("scripts/vps-control.sh"),
  ]);
  for (const protocol of ["vless-reality-xhttp", "shadowsocks", "openvpn", "ikev2", "mihomo-manager"]) {
    assert.match(manager, new RegExp(`vps-control-${protocol}`));
  }
  assert.match(manager, /for protocol in hysteria2 tuic trojan/);
  assert.match(manager, /\/etc\/vps-control\/vless-reality-xhttp\/reality\.env/);
  assert.doesNotMatch(manager, /\/etc\/vps-control\/xray\/reality\.env/);
  assert.match(manager, /check_protocol_port/);
  assert.match(manager, /все установленные защищённые протоколы/);
});

test("standalone direct protocols report installed and available binary versions", async () => {
  const api = await readApiSources();
  assert.match(api, /HYSTERIA2_BIN = Path\("\/usr\/local\/lib\/vps-control-hysteria2\/hysteria"\)/);
  assert.match(api, /TUIC_BIN = Path\("\/usr\/local\/lib\/vps-control-tuic\/sing-box"\)/);
  assert.match(api, /TROJAN_BIN = Path\("\/usr\/local\/lib\/vps-control-trojan\/sing-box"\)/);
  assert.match(api, /image_id == "hysteria2"[\s\S]*standalone_binary_version\(HYSTERIA2_BIN, "version"\)/);
  assert.match(api, /image_id in \{"tuic", "trojan"\}[\s\S]*standalone_binary_version\(binary, "version"\)/);
  assert.match(api, /github_latest_tag\(HYSTERIA2_GITHUB_REPO\)/);
  assert.match(api, /github_latest_tag\(SING_BOX_GITHUB_REPO\)/);
});

test("direct Hysteria2 is installable and manages authenticated clients", async () => {
  const [manifestText, install, firewall, auth, api] = await Promise.all([
    read("protocol-images/hysteria2/manifest.json"),
    read("protocol-images/hysteria2/install.sh"),
    read("protocol-images/hysteria2/firewall.sh"),
    read("protocol-images/hysteria2/user-api.py"),
    readApiSources(),
  ]);
  const manifest = JSON.parse(manifestText);
  assert.equal(manifest.id, "hysteria2");
  assert.equal(manifest.installable, true);
  assert.equal(manifest.service, "vps-control-hysteria2.service");
  assert.match(install, /apernet\/hysteria\/releases\/latest/);
  assert.match(install, /trafficStats:/);
  assert.match(install, /vps-control-hysteria2-auth\.service/);
  assert.match(firewall, /vps-control-hysteria2/);
  assert.match(auth, /hmac\.compare_digest/);
  assert.match(api, /payload\.protocol == "hysteria2"/);
  assert.match(api, /def hysteria2_stats/);
  assert.match(api, /tls_mode == "acme"/);
});

test("direct TUIC v5 is installable and keeps clients isolated", async () => {
  const [manifestText, install, firewall, api] = await Promise.all([
    read("protocol-images/tuic/manifest.json"), read("protocol-images/tuic/install.sh"),
    read("protocol-images/tuic/firewall.sh"), readApiSources(),
  ]);
  const manifest = JSON.parse(manifestText);
  assert.equal(manifest.id, "tuic"); assert.equal(manifest.installable, true);
  assert.match(install, /SagerNet\/sing-box\/releases\/latest/);
  assert.match(install, /asset_digest[\s\S]*sha256sum -c -/);
  assert.match(install, /zero_rtt_handshake':False/);
  assert.match(firewall, /vps-control-tuic/);
  assert.match(api, /payload\.protocol == "tuic"/);
  assert.match(api, /"udp_relay_mode": "native"/);
});

test("direct Trojan is installable and manages isolated TLS users", async () => {
  const manifestText = await read("protocol-images/trojan/manifest.json");
  const manifest=JSON.parse(manifestText); assert.equal(manifest.id,"trojan"); assert.equal(manifest.installable,true);
});

test("direct OpenVPN is installable and uses per-client PKI with CRL revocation", async () => {
  const [manifestText, install, firewall, uninstall, api] = await Promise.all([
    read("protocol-images/openvpn/manifest.json"), read("protocol-images/openvpn/install.sh"),
    read("protocol-images/openvpn/firewall.sh"), read("protocol-images/openvpn/uninstall.sh"),
    readApiSources(),
  ]);
  const manifest = JSON.parse(manifestText);
  assert.equal(manifest.id, "openvpn"); assert.equal(manifest.installable, true);
  assert.match(install, /build-server-full server nopass/); assert.match(install, /tls-crypt/); assert.match(install, /crl-verify/);
  assert.match(firewall, /MASQUERADE/); assert.match(uninstall, /protocol.*openvpn/);
  assert.match(api, /build-client-full", client_id, "nopass"/); assert.match(api, /revoke_openvpn_certificate/); assert.match(api, /openvpn_stats/);
});

test("direct IKEv2 is installable and reloads isolated EAP users", async () => {
  const [manifestText, install, firewall, api] = await Promise.all([
    read("protocol-images/ikev2/manifest.json"), read("protocol-images/ikev2/install.sh"),
    read("protocol-images/ikev2/firewall.sh"), readApiSources(),
  ]);
  const manifest = JSON.parse(manifestText);
  assert.equal(manifest.id, "ikev2"); assert.equal(manifest.installable, true);
  assert.ok(manifest.preflight_packages.includes("libcharon-extauth-plugins"));
  assert.match(install, /libcharon-extauth-plugins/);
  assert.match(install, /Another strongSwan runtime is active/); assert.match(install, /auth = eap-mschapv2/);
  assert.match(install, /libstrongswan-standard-plugins/); assert.match(install, /SWAN=\/etc\/swanctl/); assert.match(firewall, /--dport 500/); assert.match(firewall, /--dport 4500/);
  assert.match(api, /render_ikev2_users/); assert.match(api, /reload_ikev2/); assert.match(api, /payload\.protocol == "ikev2"/);
});

test("direct protocol updates preserve client state and activate the new runtime", async () => {
  const [hy2, tuic, trojan, openvpn, ikev2] = await Promise.all([
    read("protocol-images/hysteria2/install.sh"), read("protocol-images/tuic/install.sh"),
    read("protocol-images/trojan/install.sh"), read("protocol-images/openvpn/install.sh"),
    read("protocol-images/ikev2/install.sh"),
  ]);
  assert.match(hy2, /\[\[ -s "\$\{ROOT\}\/users\.json" \]\] \|\|/);
  assert.match(tuic, /\[\[ -s "\$\{ROOT\}\/config\.json" \]\] \|\|/);
  assert.match(trojan, /\[\[ -s "\$ROOT\/config\.json" \]\] \|\|/);
  assert.match(openvpn, /if \[\[ -s "\$ROOT\/settings\.json" \]\]/);
  assert.match(ikev2, /if \[\[ ! -s "\$SWAN\/users\.conf" \]\]/);
  for (const installer of [hy2, tuic, trojan, openvpn, ikev2]) assert.match(installer, /systemctl restart/);
  assert.match(hy2, /CAP_NET_ADMIN CAP_NET_BIND_SERVICE/); assert.doesNotMatch(hy2, /server -c "\$\{CONFIG\}" --test/);
  assert.match(openvpn, /dh none/); assert.doesNotMatch(openvpn, /easyrsa gen-dh/); assert.match(openvpn, /tmp-dir \/run\/vps-control-openvpn/);
});
