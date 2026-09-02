"use client";

import { formatModuleVersion } from "../../lib/format-version";
import { bytes, duration } from "../../lib/control-plane-ui";
import QRCode from "qrcode";
import Image from "next/image";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { createPortal } from "react-dom";

type View = "overview" | "profiles" | "channels" | "dns" | "rules" | "routing";
type ReadyDevice = ProfileDevice & { subscription: string; qr: string };

type ConfirmOptions = {
  title: string;
  message: string;
  confirmLabel: string;
  phrase?: string;
  danger?: boolean;
};

type Status = {
  active: boolean;
  core_version: string;
  profiles: number;
  profiles_in_use: number;
  credentials: number;
  channels_in_use: string[];
  channels_installed: number;
  modules_installed: number;
  modules_total: number;
  endpoint: string;
};

type SettingField = {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "textarea" | "boolean";
  default: string | number | boolean;
  min?: number;
  max?: number;
  options?: Array<string | { value: string; label: string }>;
  help?: string;
};

type Module = {
  id: string;
  name: string;
  description: string;
  category: "transport" | "dns" | "routing";
  category_name: string;
  installed: boolean;
  installable?: boolean;
  active: boolean;
  service?: string;
  settings?: SettingField[];
  connection_settings?: SettingField[];
  settings_values: Record<string, string | number | boolean>;
  installed_version?: string;
  available_version?: string;
  update_available?: boolean;
  update_breaking?: boolean;
};

type Profile = {
  id: string;
  name: string;
  channels: string[];
  connections: ProfileConnection[];
  routing?: Record<string, string | number | boolean>;
  devices?: ProfileDevice[];
  common_device_id?: string;
  subscription_status?: "active" | "obsolete" | "missing";
  created_at: string;
  updated_at: string;
};

type ProfileConnection = {
  id: string;
  component: string;
  name: string;
  device_id: string;
  settings: Record<string, string | number | boolean>;
};
type ProfileDevice = { id: string; name: string; scope?: "common" | "hwid" | "legacy"; hwid_hash?: string; created_at?: string; last_seen_at?: string; os?: string; os_version?: string; client_name?: string; client_version?: string; user_agent?: string; routing?: Record<string, string | number | boolean> };

type PolicySettings = {
  schema: SettingField[];
  values: Record<string, string | number | boolean>;
  presets?: ProfilePreset[];
  rule_lists?: Array<{ id: string; key: string; title: string; description: string; default_rules: string; available_rules?: string; using_default: boolean }>;
};

type ProfilePreset = { id: string; name: string; description: string; strategy: "fallback" | "url-test" | "select"; components: Array<{ id: string; cdn?: boolean; tls?: boolean; transport?: string; label?: string }> };
const presetConnectionOptions: ProfilePreset["components"] = [
  { id: "transport-reality", transport: "xhttp", label: "VLESS · XHTTP" },
  { id: "transport-reality", transport: "raw", label: "VLESS · RAW" },
  { id: "transport-reality", transport: "grpc", label: "VLESS · gRPC" },
  { id: "transport-reality", tls: true, transport: "xhttp", label: "VLESS TLS · XHTTP" },
  { id: "transport-reality", tls: true, transport: "websocket", label: "VLESS TLS · WS" },
  { id: "transport-reality", tls: true, transport: "httpupgrade", label: "VLESS TLS · HTTPUpgrade" },
  { id: "transport-reality", tls: true, transport: "grpc", label: "VLESS TLS · gRPC" },
  { id: "transport-reality", cdn: true, transport: "xhttp", label: "VLESS CDN · XHTTP" },
  { id: "transport-reality", cdn: true, transport: "websocket", label: "VLESS CDN · WS" },
  { id: "transport-reality", cdn: true, transport: "httpupgrade", label: "VLESS CDN · HTTPUpgrade" },
  { id: "transport-reality", cdn: true, transport: "grpc", label: "VLESS CDN · gRPC" },
  { id: "transport-awg", label: "AWG" }, { id: "transport-wg", label: "WG" }, { id: "transport-shadowsocks", label: "SS" },
  { id: "transport-hysteria2", label: "Hysteria2" }, { id: "transport-tuic", label: "TUIC v5" },
];
const presetOptionGroups = [
  { id: "direct", title: "VLESS REALITY", note: "Прямое подключение", options: presetConnectionOptions.filter((item) => item.id === "transport-reality" && !item.cdn && !item.tls) },
  { id: "tls", title: "VLESS TLS", note: "Прямой TLS-домен", options: presetConnectionOptions.filter((item) => item.id === "transport-reality" && item.tls) },
  { id: "cdn", title: "VLESS через CDN", note: "TLS завершается на CDN", options: presetConnectionOptions.filter((item) => item.id === "transport-reality" && item.cdn) },
  { id: "tunnels", title: "Туннели и QUIC", note: "Независимые каналы", options: presetConnectionOptions.filter((item) => item.id !== "transport-reality") },
];
type ConnectionStats = { active?: boolean; endpoint?: string | null; active_connections?: number; rx_bytes?: number; tx_bytes?: number; handshake_age_s?: number | null; latency_ms?: number | null };
type ProfileStats = { summary: { configured: number; active: number; rx_bytes: number; tx_bytes: number; last_handshake_age_s: number | null; latency_ms?: number | null }; connections: Record<string, ConnectionStats>; devices?: Record<string, { configured: number; active: number; rx_bytes: number; tx_bytes: number; last_handshake_age_s: number | null; latency_ms?: number | null }> };

const channelShort: Record<string, string> = {
  "transport-awg": "AW",
  "transport-wg": "WG",
  "transport-reality": "VL",
  "transport-shadowsocks": "SS",
  "transport-hysteria2": "HY2",
  "transport-tuic": "TU",
};
const devicePlatform: Record<string, { code: string; label: string }> = {
  ios: { code: "iOS", label: "iOS" }, android: { code: "AND", label: "Android" },
  macos: { code: "MAC", label: "macOS" }, windows: { code: "WIN", label: "Windows" },
  linux: { code: "LNX", label: "Linux" }, unknown: { code: "HW", label: "Неизвестная ОС" },
};

function devicePlatformMeta(device: ProfileDevice) {
  return devicePlatform[device.os || "unknown"] || devicePlatform.unknown;
}

function deviceSystemLabel(device: Pick<ProfileDevice, "os" | "os_version">) {
  const platform = devicePlatform[device.os || "unknown"] || devicePlatform.unknown;
  return `${platform.label}${device.os_version ? ` ${device.os_version}` : ""}`;
}

function registeredProfileDevices(profile: Profile) {
  return (profile.devices || []).filter((device) => device.scope !== "common" && device.id !== profile.common_device_id);
}
const dnsProviderMeta: Record<string, { code: string; note: string }> = {
  "https://cloudflare-dns.com/dns-query": { code: "CF", note: "Быстрый DoH без фильтрации" },
  "https://dns.google/dns-query": { code: "G", note: "Публичный DoH Google" },
  "https://dns.quad9.net/dns-query": { code: "Q9", note: "Блокировка вредоносных доменов" },
  "https://dns.adguard-dns.com/dns-query": { code: "AG", note: "Реклама и трекеры" },
  "208.67.222.222": { code: "OD", note: "Базовая защита OpenDNS" },
  "185.228.168.9": { code: "CB", note: "Фильтрация опасных ресурсов" },
  "https://common.dot.dns.yandex.net/dns-query": { code: "YA", note: "Яндекс DNS без фильтрации" },
  "77.88.8.88": { code: "YA", note: "Безопасный режим Яндекс" },
  "77.88.8.7": { code: "FAM", note: "Семейная фильтрация" },
  "193.58.251.251": { code: "SKY", note: "Фильтрация SkyDNS" },
  "195.208.4.1": { code: "NS", note: "Российский резолвер НСДИ" },
  "195.46.39.39": { code: "SAFE", note: "Категорийная фильтрация" },
};
const moduleCapabilities: Record<string, string[]> = {
  "transport-reality": ["REALITY", "TLS", "CDN", "XHTTP", "RAW", "gRPC", "WebSocket"],
  "transport-awg": ["AmneziaWG", "UDP"],
  "transport-wg": ["WireGuard", "UDP"],
  "transport-shadowsocks": ["Shadowsocks", "TCP", "UDP"],
  "transport-hysteria2": ["Hysteria2", "QUIC", "UDP", "Salamander"],
  "transport-tuic": ["TUIC v5", "QUIC", "UDP"],
};

const directGameCatalog = [
  { id: "cs2", code: "CS2", name: "Counter-Strike 2" },
  { id: "dota2", code: "DOTA", name: "Dota 2" },
  { id: "valorant", code: "VAL", name: "Valorant" },
  { id: "fortnite", code: "FN", name: "Fortnite" },
  { id: "pubg", code: "PUBG", name: "PUBG" },
  { id: "warzone", code: "COD", name: "Call of Duty / Warzone" },
  { id: "gta5", code: "GTA", name: "GTA V" },
  { id: "roblox", code: "RBX", name: "Roblox" },
  { id: "wot", code: "WOT", name: "World of Tanks" },
  { id: "tarkov", code: "EFT", name: "Escape from Tarkov" },
  { id: "apex", code: "APEX", name: "Apex Legends" },
  { id: "rainbow6", code: "R6", name: "Rainbow Six Siege" },
  { id: "overwatch2", code: "OW2", name: "Overwatch 2" },
  { id: "rocketleague", code: "RL", name: "Rocket League" },
  { id: "lol", code: "LOL", name: "League of Legends" },
  { id: "destiny2", code: "D2", name: "Destiny 2" },
  { id: "helldivers2", code: "HD2", name: "Helldivers 2" },
  { id: "rust", code: "RUST", name: "Rust" },
  { id: "warthunder", code: "WT", name: "War Thunder" },
  { id: "dbd", code: "DBD", name: "Dead by Daylight" },
  { id: "thefinals", code: "TF", name: "The Finals" },
  { id: "battlefield2042", code: "BF", name: "Battlefield 2042" },
  { id: "brawlstars", code: "BS", name: "Brawl Stars" },
  { id: "freefire", code: "FF", name: "Free Fire" },
  { id: "mobilelegends", code: "ML", name: "Mobile Legends" },
  { id: "diablo4", code: "D4", name: "Diablo IV" },
  { id: "arenabreakoutinfinite", code: "ABI", name: "Arena Breakout: Infinite" },
  { id: "marathon", code: "MAR", name: "Marathon" },
  { id: "repo", code: "REPO", name: "R.E.P.O." },
  { id: "mistfallhunter", code: "MFH", name: "Mistfall Hunter" },
  { id: "residentevilrequiem", code: "RE9", name: "Resident Evil Requiem" },
  { id: "stardewvalley", code: "SDV", name: "Stardew Valley" },
  { id: "mecchachameleon", code: "MC", name: "MECCHA CHAMELEON" },
  { id: "minecraft", code: "MC", name: "Minecraft" },
  { id: "warface", code: "WF", name: "Warface" },
  { id: "enlisted", code: "ENL", name: "Enlisted" },
  { id: "worldofwarships", code: "WOWS", name: "World of Warships" },
  { id: "pathofexile2", code: "POE", name: "Path of Exile 2" },
  { id: "warframe", code: "WF", name: "Warframe" },
  { id: "genshin", code: "GI", name: "Genshin Impact" },
  { id: "honkai", code: "HSR", name: "Honkai: Star Rail" },
  { id: "deltaforce", code: "DF", name: "Delta Force" },
  { id: "marvelrivals", code: "MR", name: "Marvel Rivals" },
  { id: "deadlock", code: "DL", name: "Deadlock" },
  { id: "atomicheart", code: "AH", name: "Atomic Heart" },
  { id: "stalcraft", code: "SCX", name: "STALCRAFT: X" },
  { id: "crossout", code: "CROSS", name: "Crossout" },
  { id: "lostark", code: "LA", name: "Lost Ark" },
  { id: "blackdesert", code: "BDO", name: "Black Desert" },
  { id: "albion", code: "AO", name: "Albion Online" },
  { id: "eveonline", code: "EVE", name: "EVE Online" },
  { id: "dayz", code: "DAYZ", name: "DayZ" },
  { id: "arma3", code: "ARMA", name: "Arma 3" },
  { id: "huntshowdown", code: "HUNT", name: "Hunt: Showdown 1896" },
  { id: "remnant2", code: "R2", name: "Remnant II" },
  { id: "palworld", code: "PAL", name: "Palworld" },
  { id: "oncehuman", code: "OH", name: "Once Human" },
  { id: "throneandliberty", code: "TL", name: "Throne and Liberty" },
  { id: "escapefromduckov", code: "EFD", name: "Escape from Duckov" },
];

const tunnelGameCatalog = [
  { id: "brawlstars", code: "BS", name: "Brawl Stars", family: "Supercell" },
  { id: "clashofclans", code: "COC", name: "Clash of Clans", family: "Supercell" },
  { id: "clashroyale", code: "CR", name: "Clash Royale", family: "Supercell" },
  { id: "boombeach", code: "BB", name: "Boom Beach", family: "Supercell" },
  { id: "hayday", code: "HD", name: "Hay Day", family: "Supercell" },
  { id: "apex", code: "APEX", name: "Apex Legends", family: "EA" },
  { id: "battlefield2042", code: "BF42", name: "Battlefield 2042", family: "EA" },
  { id: "battlefield6", code: "BF6", name: "Battlefield 6", family: "EA" },
  { id: "eafc25", code: "FC25", name: "EA Sports FC 25", family: "EA" },
  { id: "eafc26", code: "FC26", name: "EA Sports FC 26", family: "EA" },
  { id: "f125", code: "F1", name: "F1 25", family: "EA" },
  { id: "nfsunbound", code: "NFS", name: "Need for Speed Unbound", family: "EA" },
  { id: "battlefront2", code: "SW", name: "Star Wars Battlefront II", family: "EA" },
];
const gameRoutingCatalog = [...new Map([
  ...directGameCatalog.map((game) => ({ ...game, family: "Обычный маршрут" })),
  ...tunnelGameCatalog,
].map((game) => [game.id, game])).values()];
const defaultTunnelGameIds = new Set(tunnelGameCatalog.map((game) => game.id));
const defaultDirectGameIds = new Set(directGameCatalog.map((game) => game.id).filter((id) => !defaultTunnelGameIds.has(id)));

function selectedGameIds(value: unknown, defaults: Set<string>) {
  const raw = String(value ?? "").trim();
  return !raw || raw === "@default" ? new Set(defaults) : new Set(raw.split(",").map((item) => item.trim()).filter(Boolean));
}

const udpExclusionCatalog = [
  { id: "dns", code: "DNS", name: "Защищённый DNS" },
  { id: "telegram", code: "TG", name: "Telegram" },
  { id: "discord", code: "DS", name: "Discord" },
  { id: "whatsapp", code: "WA", name: "WhatsApp" },
  { id: "signal", code: "SG", name: "Signal" },
  { id: "zoom", code: "ZM", name: "Zoom" },
  { id: "meet", code: "GM", name: "Google Meet" },
  { id: "teams", code: "MS", name: "Teams / Skype" },
  { id: "apple", code: "AP", name: "FaceTime / iMessage" },
  { id: "steam", code: "ST", name: "Steam" },
  { id: "ea", code: "EA", name: "EA" },
  { id: "supercell", code: "SC", name: "Supercell" },
  { id: "riot", code: "RI", name: "Riot Games" },
  { id: "epic", code: "EP", name: "Epic / Fortnite" },
  { id: "xbox", code: "XB", name: "Xbox Live" },
  { id: "playstation", code: "PS", name: "PlayStation Network" },
  { id: "nintendo", code: "NS", name: "Nintendo" },
  { id: "battlenet", code: "BN", name: "Battle.net" },
];

const p2pClientCatalog = [
  { id: "qbittorrent", code: "QBIT", name: "qBittorrent" },
  { id: "transmission", code: "TR", name: "Transmission" },
  { id: "deluge", code: "DEL", name: "Deluge" },
  { id: "utorrent", code: "µT", name: "µTorrent" },
  { id: "bittorrent", code: "BT", name: "BitTorrent" },
  { id: "biglybt", code: "BIG", name: "BiglyBT" },
  { id: "tribler", code: "TRIB", name: "Tribler" },
  { id: "frostwire", code: "FW", name: "FrostWire" },
];

const profileDirectRules = [
  { key: "block_ads", code: "AD", title: "Реклама", text: "Реклама и рекламные трекеры.", group: "Защита" },
  { key: "block_privacy", code: "PRIV", title: "Приватность", text: "Аналитика и профилирование.", group: "Защита" },
  { key: "direct_ru_sites", code: "RU", title: "Сайты РФ", text: "Домены и IP России.", group: "Напрямую" },
  { key: "direct_ru_banks", code: "BANK", title: "Банки", text: "Банки и платёжные сервисы.", group: "Напрямую" },
  { key: "direct_ru_marketplaces", code: "SHOP", title: "Магазины", text: "Магазины и маркетплейсы.", group: "Напрямую" },
  { key: "direct_downloads", code: "DL", title: "Загрузки", text: "Крупные файлы без VPN.", group: "Напрямую" },
  { key: "direct_local_network", code: "LAN", title: "Локальная сеть", text: "Домашние устройства напрямую.", group: "Сеть" },
  { key: "direct_games_enabled", code: "GAME", title: "Игры", text: "Выбранные игры без VPN.", group: "Сеть" },
  { key: "direct_games_udp_enabled", code: "UDP", title: "UDP", text: "Весь UDP без VPN.", group: "Сеть" },
  { key: "direct_p2p_enabled", code: "P2P", title: "P2P и торренты", text: "Выбранные клиенты напрямую.", group: "Сеть" },
];

type RuleIconGroup = { id: string; code: string; name: string; tokens: string[] };
const ruleIconGroups: Record<string, RuleIconGroup[]> = {
  block_ads: [
    { id: "base", code: "AD", name: "Базовый список рекламы", tokens: ["category-ads-all"] },
    { id: "google", code: "G ADS", name: "Google Ads", tokens: ["doubleclick", "googlesyndication", "googleadservices", "googletagservices"] },
    { id: "yandex", code: "YA AD", name: "Яндекс Реклама", tokens: ["yandexadexchange", "an.yandex.ru", "adfox"] },
    { id: "vk_ads", code: "VK AD", name: "VK / myTarget", tokens: ["mytarget"] },
    { id: "native", code: "NATIVE", name: "Taboola и Outbrain", tokens: ["taboola", "outbrain"] },
    { id: "criteo", code: "CRITEO", name: "Criteo", tokens: ["criteo"] },
    { id: "amazon", code: "AMZ", name: "Amazon Ads", tokens: ["amazon-adsystem"] },
    { id: "global_rtb", code: "RTB", name: "Международные RTB-сети", tokens: ["adnxs", "adsrvr", "adform", "adroll", "openx", "pubmatic", "rubiconproject", "casalemedia", "serving-sys", "smartadserver", "smaato", "yieldmo", "lijit", "media.net", "advertising.com"] },
    { id: "ru_rtb", code: "RU RTB", name: "Российские RTB-сети", tokens: ["between.digital", "adriver", "soloway", "buzzoola", "hybrid.ai", "otm-r", "videonow"] },
    { id: "mobile", code: "APP", name: "Реклама в приложениях", tokens: ["applovin", "applvn", "vungle", "ironsrc", "chartboost", "inmobi", "adcolony", "startappservice", "mintegral", "unityads"] },
    { id: "video", code: "VIDEO", name: "Реклама в видео", tokens: ["springserve", "spotx", "freewheel", "fwmrm", "innovid", "tremorhub", "unrulymedia", "cedato", "connatix", "aniview", "teads", "smartclip", "lkqd"] },
    { id: "metrics", code: "TRACK", name: "Рекламные измерения", tokens: ["scorecardresearch", "moatads", "adsafeprotected"] },
  ],
  block_privacy: [
    { id: "google", code: "GA", name: "Google Analytics", tokens: ["google-analytics"] },
    { id: "yandex", code: "YM", name: "Яндекс Метрика", tokens: ["mc.yandex"] },
    { id: "clarity", code: "CLARITY", name: "Microsoft Clarity", tokens: ["clarity.ms"] },
    { id: "hotjar", code: "HOTJAR", name: "Hotjar", tokens: ["hotjar"] },
    { id: "fullstory", code: "FS", name: "FullStory", tokens: ["fullstory"] },
    { id: "mixpanel", code: "MIX", name: "Mixpanel", tokens: ["mixpanel"] },
    { id: "amplitude", code: "AMP", name: "Amplitude", tokens: ["amplitude"] },
    { id: "heap", code: "HEAP", name: "Heap", tokens: ["heap.io"] },
    { id: "segment", code: "SEG", name: "Segment", tokens: ["segment.com", "segment.io"] },
    { id: "firebase", code: "FBA", name: "Firebase Analytics", tokens: ["app-measurement"] },
    { id: "appsflyer", code: "AF", name: "AppsFlyer", tokens: ["appsflyer"] },
    { id: "adjust", code: "ADJ", name: "Adjust", tokens: ["adjust.com", "adjust.net.in"] },
    { id: "branch", code: "BR", name: "Branch", tokens: ["branch.io"] },
    { id: "singular", code: "SG", name: "Singular", tokens: ["singular.net"] },
    { id: "kochava", code: "KO", name: "Kochava", tokens: ["kochava"] },
    { id: "tiktok", code: "TT", name: "TikTok Analytics", tokens: ["analytics.tiktok"] },
    { id: "social", code: "SOC", name: "Другие соцсети", tokens: ["analytics.twitter", "snap.licdn", "tr.snapchat"] },
    { id: "scorecard", code: "SCR", name: "Comscore", tokens: ["scorecardresearch"] },
    { id: "windows", code: "WIN", name: "Телеметрия Windows", tokens: ["microsoft.com"] },
    { id: "nvidia", code: "NV", name: "Телеметрия NVIDIA", tokens: ["gfe.nvidia"] },
    { id: "amd", code: "AMD", name: "Телеметрия AMD", tokens: ["metrics.amd"] },
    { id: "mozilla", code: "FF", name: "Телеметрия Firefox", tokens: ["telemetry.mozilla"] },
    { id: "jetbrains", code: "JB", name: "Статистика JetBrains", tokens: ["data.services.jetbrains"] },
    { id: "ubuntu", code: "UBU", name: "Отчёты Ubuntu", tokens: ["errors.ubuntu"] },
    { id: "unity", code: "UNITY", name: "Unity Analytics", tokens: ["cloud.unity3d"] },
  ],
  direct_ru_sites: [
    { id: "zones", code: "RU", name: "Домены и IP России", tokens: ["DOMAIN-SUFFIX,ru,", "xn--p1ai", "DOMAIN-SUFFIX,su,", "GEOIP,RU"] },
    { id: "yandex", code: "YA", name: "Яндекс", tokens: ["yandex", "yastatic"] },
    { id: "vk", code: "VK", name: "ВКонтакте", tokens: ["vk.ru", "vk.com", "userapi", "vkuseraudio"] },
    { id: "mail", code: "MAIL", name: "Mail.ru", tokens: ["mail.com"] },
    { id: "ok", code: "OK", name: "Одноклассники", tokens: ["mycdn", "ok.ru", "odnoklassniki"] },
    { id: "dzen", code: "DZEN", name: "Дзен", tokens: ["dzen.ru"] },
    { id: "rutube", code: "RUTUBE", name: "Rutube", tokens: ["rutube"] },
    { id: "smotrim", code: "TV", name: "Смотрим", tokens: ["smotrim"] },
    { id: "kinopoisk", code: "KP", name: "Кинопоиск", tokens: ["kinopoisk"] },
    { id: "twogis", code: "2GIS", name: "2ГИС", tokens: ["2gis"] },
    { id: "gosuslugi", code: "GOS", name: "Госуслуги", tokens: ["gosuslugi"] },
    { id: "mos", code: "MOS", name: "Мос.ру", tokens: ["mos.ru"] },
    { id: "nalog", code: "FNS", name: "ФНС", tokens: ["nalog.gov.ru"] },
    { id: "kaspersky", code: "KAV", name: "Лаборатория Касперского", tokens: ["kaspersky"] },
  ],
  direct_ru_banks: [
    { id: "sber", code: "SBER", name: "Сбер", tokens: ["sber"] },
    { id: "tbank", code: "TB", name: "Т-Банк", tokens: ["tinkoff", "tbank"] },
    { id: "alfa", code: "ALFA", name: "Альфа-Банк", tokens: ["alfabank", "alfa-bank"] },
    { id: "vtb", code: "VTB", name: "ВТБ", tokens: ["vtb"] },
    { id: "gazprom", code: "GPB", name: "Газпромбанк", tokens: ["gazprombank"] },
    { id: "raiffeisen", code: "RAIF", name: "Райффайзен", tokens: ["raiffeisen"] },
    { id: "psb", code: "PSB", name: "ПСБ", tokens: ["psbank"] },
    { id: "sovcom", code: "SOV", name: "Совкомбанк", tokens: ["sovcombank"] },
    { id: "other_major", code: "BANK", name: "Другие крупные банки", tokens: ["open.ru", "rshb", "pochtabank", "mkb.ru", "uralsib"] },
    { id: "payments", code: "PAY", name: "Платежи и регуляторы", tokens: ["nspk", "mironline", "unistream", "coronapay", "cbr.ru", "fincult", "banki.ru", "finuslugi"] },
    { id: "regional", code: "BANK+", name: "Региональные банки", tokens: [] },
  ],
  direct_ru_marketplaces: [
    { id: "ozon", code: "OZON", name: "Ozon", tokens: ["ozon"] },
    { id: "wildberries", code: "WB", name: "Wildberries", tokens: ["wildberries", "wbbasket", "wb.ru"] },
    { id: "yandex_market", code: "YA M", name: "Яндекс Маркет", tokens: ["market.yandex", "beru"] },
    { id: "megamarket", code: "MEGA", name: "Мегамаркет", tokens: ["megamarket", "goods.ru"] },
    { id: "aliexpress", code: "ALI", name: "AliExpress Россия", tokens: ["aliexpress"] },
    { id: "regional_market", code: "MARKET", name: "Другие маркетплейсы", tokens: ["kazanexpress", "magnitmarket"] },
    { id: "classifieds", code: "AVITO", name: "Объявления", tokens: ["avito"] },
    { id: "fashion", code: "FASHION", name: "Lamoda и Золотое Яблоко", tokens: ["lamoda", "goldapple"] },
    { id: "kids_sport", code: "LIFE", name: "Детский мир и Спортмастер", tokens: ["detmir", "sportmaster"] },
    { id: "home", code: "HOME", name: "Дом и ремонт", tokens: ["leroymerlin", "lemanapro", "vseinstrumenti", "petrovich", "hoff"] },
    { id: "electronics", code: "TECH", name: "Электроника", tokens: ["citilink", "dns-shop", "mvideo", "eldorado", "onlinetrade"] },
    { id: "delivery", code: "FOOD", name: "Продукты и доставка", tokens: ["samokat", "kuper", "vprok", "lenta.com"] },
  ],
  direct_downloads: [
    { id: "windows", code: "WIN", name: "Windows Update", tokens: ["windowsupdate", "update.microsoft", "delivery.mp.microsoft"] },
    { id: "microsoft", code: "MS", name: "Загрузки Microsoft", tokens: ["download.microsoft"] },
    { id: "apple", code: "APPLE", name: "Обновления Apple", tokens: ["swcdn.apple", "swdownload.apple", "swdist.apple", "appldnld.apple", "mesu.apple"] },
    { id: "nvidia", code: "NV", name: "Драйверы NVIDIA", tokens: ["nvidia"] },
    { id: "amd", code: "AMD", name: "Драйверы AMD", tokens: ["drivers.amd"] },
    { id: "steam", code: "STEAM", name: "Загрузки Steam", tokens: ["steamcontent", "steamserver", "steamstatic", "steamcdn"] },
    { id: "epic", code: "EPIC", name: "Загрузки Epic Games", tokens: ["epicgames"] },
    { id: "ea", code: "EA", name: "Загрузки EA", tokens: ["origin-a", "eaassets"] },
    { id: "blizzard", code: "BN", name: "Загрузки Battle.net", tokens: ["level3.blizzard", "blzddist"] },
    { id: "xbox", code: "XBOX", name: "Загрузки Xbox", tokens: ["xboxlive"] },
    { id: "playstation", code: "PS", name: "Загрузки PlayStation", tokens: ["playstation.net"] },
    { id: "nintendo", code: "NS", name: "Загрузки Nintendo", tokens: ["nintendo.net"] },
    { id: "linux", code: "LINUX", name: "Ubuntu, Debian и Kernel", tokens: ["ubuntu", "debian", "kernel.org"] },
    { id: "github", code: "GH", name: "Релизы GitHub", tokens: ["githubusercontent"] },
    { id: "docker", code: "DOCKER", name: "Образы Docker", tokens: ["docker"] },
    { id: "jetbrains", code: "JB", name: "JetBrains", tokens: ["jetbrains"] },
    { id: "nodejs", code: "NODE", name: "Node.js", tokens: ["nodejs.org"] },
  ],
  direct_local_network: [
    { id: "names", code: "LAN", name: "Локальные имена", tokens: ["localhost", "DOMAIN-SUFFIX,local", "DOMAIN-SUFFIX,lan", "home.arpa"] },
    { id: "private_v4", code: "IPv4", name: "Приватные IPv4", tokens: ["10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16"] },
    { id: "private_v6", code: "IPv6", name: "Локальные IPv6", tokens: ["::1/128", "fc00::/7", "fe80::/10"] },
    { id: "discovery", code: "CAST", name: "Обнаружение устройств", tokens: ["224.0.0.0/4", "ff00::/8"] },
  ],
};

const profileStrategies = [
  { value: "", code: "AUTO", title: "Общие", text: "Наследовать стратегию маршрутизации." },
  { value: "fallback", code: "SAFE", title: "Резерв", text: "Переключаться при недоступности канала." },
  { value: "url-test", code: "FAST", title: "Автовыбор", text: "Автоматически выбирать самый быстрый канал." },
  { value: "select", code: "MAN", title: "Вручную", text: "Показывать Selector в Mihomo-клиенте." },
];

const MIHOMO_OPERATION_EVENT = "gate312:mihomo-operation";
const TECHNICAL_ERROR = /(?:\n|traceback|systemctl|journalctl|apt(?:-get)?|dpkg|stderr|stdout|exit status|failed to start|reading package lists|building dependency tree)/i;

function publicError(message: string, status: number) {
  const value = message.trim();
  return status >= 500 || TECHNICAL_ERROR.test(value) || /^(?:bad gateway|internal server error)$/i.test(value)
    ? "Команда завершилась с ошибкой. Технические сведения сохранены в журнале."
    : value || "Команда не выполнена.";
}

function publishMihomoOperation(id: string, label: string, state: "running" | "success" | "error", message?: string) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(MIHOMO_OPERATION_EVENT, { detail: { id, label, state, message } }));
}

export function MihomoPage({
  token,
  confirmAction,
  coreBusy,
  onRemoveCore,
}: {
  token: string;
  confirmAction: (options: ConfirmOptions) => Promise<boolean>;
  coreBusy: boolean;
  onRemoveCore: () => Promise<void>;
}) {
  const [view, setView] = useState<View>("overview");
  const [status, setStatus] = useState<Status | null>(null);
  const [modules, setModules] = useState<Module[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [dnsPolicy, setDnsPolicy] = useState<PolicySettings | null>(null);
  const [dnsDraft, setDnsDraft] = useState<Record<string, string | number | boolean>>({});
  const [dnsDirty, setDnsDirty] = useState(false);
  const dnsDirtyRef = useRef(false);
  const [routingPolicy, setRoutingPolicy] = useState<PolicySettings | null>(null);
  const [routingDraft, setRoutingDraft] = useState<Record<string, string | number | boolean>>({});
  const [routingDirty, setRoutingDirty] = useState(false);
  const [routingAutosaving, setRoutingAutosaving] = useState(false);
  const [activeRuleList, setActiveRuleList] = useState("direct_ru_sites");
  const [ruleSearch, setRuleSearch] = useState("");
  const [gameSearch, setGameSearch] = useState("");
  const [gameFilter, setGameFilter] = useState<"all" | "direct" | "vpn" | "restricted">("all");
  const routingDirtyRef = useRef(false);
  const routingDraftRef = useRef<Record<string, string | number | boolean>>({});
  const routingAutosaveRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<Module | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<Record<string, string | number | boolean>>({});
  const [profileDialog, setProfileDialog] = useState<Profile | "new" | null>(null);
  const profileMutationId = useRef(crypto.randomUUID());
  const [profileStep, setProfileStep] = useState(2);
  const profileCanvasRef = useRef<HTMLElement>(null);
  const [profileName, setProfileName] = useState("");
  const [profileConnections, setProfileConnections] = useState<ProfileConnection[]>([]);
  const [profileRouting, setProfileRouting] = useState<Record<string, string | number | boolean>>({});
  const [profileStrategyTouched, setProfileStrategyTouched] = useState(false);
  const [profileDevices, setProfileDevices] = useState<ProfileDevice[]>([{ id: "profile-common", name: "Общие настройки профиля", scope: "common" }]);
  const [activeDeviceId, setActiveDeviceId] = useState("profile-common");
  const activeProfileRouting = profileDevices.find((device) => device.id === activeDeviceId)?.routing || profileRouting;
  const [profileStats, setProfileStats] = useState<Record<string, ProfileStats>>({});
  const [expandedDeviceLists, setExpandedDeviceLists] = useState<Set<string>>(() => new Set());
  const [expandedProtocolLists, setExpandedProtocolLists] = useState<Set<string>>(() => new Set());
  const [createdProfile, setCreatedProfile] = useState<Profile | null>(null);
  const [readyDevices, setReadyDevices] = useState<ReadyDevice[]>([]);
  const [presetDialog, setPresetDialog] = useState(false);
  const [presetDraft, setPresetDraft] = useState<ProfilePreset[]>([]);
  const [presetEditorIndex, setPresetEditorIndex] = useState(0);

  useEffect(() => {
    profileCanvasRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [profileStep, profileDialog]);

  const request = useCallback(async (path: string, init?: RequestInit) => {
    if (!token) throw new Error("Сессия панели завершена. Войдите заново.");
    const response = await fetch(`/api${path}`, {
      ...init,
      headers: {
        Authorization: `Basic ${token}`,
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(init?.headers || {}),
      },
    });
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        message = body?.detail || body?.message || message;
        if (body?.operation_id) message += ` · операция ${body.operation_id}`;
      } catch {
        // Preserve status text for non-JSON responses.
      }
      throw new Error(publicError(message, response.status));
    }
    if ((response.headers.get("content-type") || "").includes("text/plain")) {
      return response.text();
    }
    return response.status === 204 ? null : response.json();
  }, [token]);

  const refresh = useCallback(async () => {
    setError("");
    try {
      const [nextStatus, nextModules, nextProfiles, nextDns, nextRouting] = await Promise.all([
        request("/mihomo/status"),
        request("/mihomo/modules"),
        request("/mihomo/profiles"),
        request("/mihomo/dns/settings"),
        request("/mihomo/routing/schema"),
      ]);
      setStatus(nextStatus as Status);
      setModules((nextModules as { items: Module[] }).items || []);
      setProfiles((nextProfiles as { items: Profile[] }).items || []);
      setDnsPolicy(nextDns as PolicySettings);
      if (!dnsDirtyRef.current) setDnsDraft({ ...(nextDns as PolicySettings).values });
      setRoutingPolicy(nextRouting as PolicySettings);
      if (!routingDirtyRef.current) {
        const values = { ...(nextRouting as PolicySettings).values };
        routingDraftRef.current = values;
        setRoutingDraft(values);
      }
      const profileItems = (nextProfiles as { items: Profile[] }).items || [];
      const statsEntries = await Promise.all(profileItems.map(async (profile) => {
        try { return [profile.id, await request(`/mihomo/profiles/${profile.id}/stats`)] as const; }
        catch { return [profile.id, null] as const; }
      }));
      setProfileStats(Object.fromEntries(statsEntries.filter((entry) => entry[1])) as Record<string, ProfileStats>);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить Mihomo Manager");
    }
  }, [request]);

  function updateDnsDraft(key: string, value: string | number | boolean) {
    setDnsDraft((current) => ({ ...current, [key]: value }));
    dnsDirtyRef.current = true;
    setDnsDirty(true);
  }

  async function saveDnsWorkspace(event: FormEvent) {
    event.preventDefault();
    const operationId = "settings:dns-private";
    publishMihomoOperation(operationId, "DNS Mihomo", "running", "Сохраняем DNS для профилей Mihomo…");
    setBusy(operationId);
    setError("");
    try {
      await request("/mihomo/dns/settings", { method: "PATCH", body: JSON.stringify({ values: dnsDraft }) });
      dnsDirtyRef.current = false;
      setDnsDirty(false);
      await refresh();
      setNotice("DNS для соединений Mihomo сохранён. Обновите подписку в клиенте.");
      publishMihomoOperation(operationId, "DNS Mihomo", "success", "DNS-настройки сохранены");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "DNS-настройки не сохранены";
      setError(message);
      publishMihomoOperation(operationId, "DNS Mihomo", "error", message);
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [refresh]);

  useEffect(() => {
    if (!createdProfile) return;
    let cancelled = false;
    void (async () => {
      const result = await request(`/mihomo/profiles/${createdProfile.id}/subscription`) as { path: string };
      const subscription = new URL(result.path, window.location.origin).toString();
      const qr = await QRCode.toDataURL(subscription, { errorCorrectionLevel: "M", margin: 2, width: 360 });
      return [{ id: "profile", name: createdProfile.name, subscription, qr }];
    })().then((items) => { if (!cancelled) setReadyDevices(items); }).catch((cause) => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "Не удалось подготовить QR-коды подписок");
    });
    return () => { cancelled = true; };
  }, [createdProfile, request]);

  useEffect(() => () => {
    if (routingAutosaveRef.current) clearTimeout(routingAutosaveRef.current);
  }, []);

  function updateRoutingDraft(key: string, value: string | number | boolean, autosave = false) {
    const next = { ...routingDraftRef.current, [key]: value };
    routingDraftRef.current = next;
    setRoutingDraft(next);
    routingDirtyRef.current = true;
    setRoutingDirty(true);
    if (!autosave) return;
    if (routingAutosaveRef.current) clearTimeout(routingAutosaveRef.current);
    setRoutingAutosaving(true);
    routingAutosaveRef.current = setTimeout(() => {
      routingAutosaveRef.current = null;
      void request("/mihomo/routing/settings", { method: "PATCH", body: JSON.stringify({ values: next }) })
        .then((result) => {
          const saved = { ...((result as PolicySettings).values || next) };
          if (routingDraftRef.current === next) {
            routingDraftRef.current = saved;
            setRoutingDraft(saved);
            routingDirtyRef.current = false;
            setRoutingDirty(false);
          }
          setRoutingPolicy(result as PolicySettings);
        })
        .catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось сохранить выбор"))
        .finally(() => setRoutingAutosaving(false));
    }, 350);
  }

  function ruleGroupLines(ruleList: NonNullable<PolicySettings["rule_lists"]>[number], group: RuleIconGroup) {
    const groups = ruleIconGroups[ruleList.id] || [];
    const lines = String(ruleList.available_rules || ruleList.default_rules).split("\n").map((line) => line.trim()).filter(Boolean);
    const matches = (line: string, candidate: RuleIconGroup) => candidate.tokens.some((token) => line.toLowerCase().includes(token.toLowerCase()));
    return group.tokens.length ? lines.filter((line) => matches(line, group)) : lines.filter((line) => !groups.some((candidate) => candidate.tokens.length && matches(line, candidate)));
  }

  function toggleRuleIconGroup(ruleList: NonNullable<PolicySettings["rule_lists"]>[number], group: RuleIconGroup) {
    const defaults = ruleList.default_rules.split("\n").map((line) => line.trim()).filter(Boolean);
    const available = String(ruleList.available_rules || ruleList.default_rules).split("\n").map((line) => line.trim()).filter(Boolean);
    const current = (String(routingDraft[ruleList.key] ?? "@default").trim() === "@default" ? defaults : String(routingDraft[ruleList.key] || "").split("\n").map((line) => line.trim()).filter(Boolean));
    const groupLines = ruleGroupLines(ruleList, group);
    const selected = new Set(current);
    const enabled = groupLines.length > 0 && groupLines.every((line) => selected.has(line));
    for (const line of groupLines) {
      if (enabled) selected.delete(line);
      else selected.add(line);
    }
    const custom = current.filter((line) => !available.includes(line) && selected.has(line));
    const selectedAvailable = available.filter((line) => selected.has(line));
    const isDefault = selectedAvailable.length === defaults.length && defaults.every((line) => selected.has(line)) && !custom.length;
    updateRoutingDraft(ruleList.key, isDefault ? "@default" : [...selectedAvailable, ...custom].join("\n"), true);
  }

  function ruleExtraLines(ruleList: NonNullable<PolicySettings["rule_lists"]>[number]) {
    const defaults = new Set(String(ruleList.available_rules || ruleList.default_rules).split("\n").map((line) => line.trim()).filter(Boolean));
    const current = String(routingDraft[ruleList.key] ?? "@default").trim() === "@default" ? [] : String(routingDraft[ruleList.key] || "").split("\n").map((line) => line.trim()).filter(Boolean);
    return current.filter((line) => !defaults.has(line));
  }

  function updateRuleExtras(ruleList: NonNullable<PolicySettings["rule_lists"]>[number], value: string) {
    const defaults = ruleList.default_rules.split("\n").map((line) => line.trim()).filter(Boolean);
    const available = String(ruleList.available_rules || ruleList.default_rules).split("\n").map((line) => line.trim()).filter(Boolean);
    const raw = String(routingDraft[ruleList.key] ?? "@default").trim();
    const current = raw === "@default" ? new Set(defaults) : new Set(raw.split("\n").map((line) => line.trim()).filter(Boolean));
    const selectedDefaults = available.filter((line) => current.has(line));
    const extras = value.replace(/\r/g, "").split("\n").map((line) => line.trim()).filter(Boolean);
    const isDefault = selectedDefaults.length === defaults.length && defaults.every((line) => current.has(line)) && !extras.length;
    updateRoutingDraft(ruleList.key, isDefault ? "@default" : [...selectedDefaults, ...extras].join("\n"));
  }

  function setGameRoute(gameId: string, route: "direct" | "tunnel" | "off") {
    const direct = selectedGameIds(routingDraft.direct_games, defaultDirectGameIds);
    const tunnel = selectedGameIds(routingDraft.tunnel_games, defaultTunnelGameIds);
    direct.delete(gameId);
    tunnel.delete(gameId);
    if (route === "direct") direct.add(gameId);
    if (route === "tunnel") tunnel.add(gameId);
    updateRoutingDraft("direct_games", [...direct].join(","));
    updateRoutingDraft("tunnel_games", [...tunnel].join(","), true);
  }

  function toggleUdpExclusion(resourceId: string) {
    const selected = new Set(String(routingDraft.udp_tunnel_exclusions || "").split(",").map((value) => value.trim()).filter(Boolean));
    if (selected.has(resourceId)) selected.delete(resourceId); else selected.add(resourceId);
    updateRoutingDraft("udp_tunnel_exclusions", [...selected].join(","), true);
  }

  function toggleP2pClient(clientId: string) {
    const selected = new Set(String(routingDraft.direct_p2p_clients || "").split(",").map((value) => value.trim()).filter(Boolean));
    if (selected.has(clientId)) selected.delete(clientId); else selected.add(clientId);
    updateRoutingDraft("direct_p2p_clients", [...selected].join(","), true);
  }

  function toggleProfileRule(key: string, checked: boolean) {
    setProfileDevices((current) => current.map((device) => device.id === activeDeviceId
      ? { ...device, routing: { ...(device.routing || profileRouting), [key]: checked } }
      : device));
  }

  function toggleCollapsed(setter: Dispatch<SetStateAction<Set<string>>>, key: string) {
    setter((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  function setProfileStrategy(value: string) {
    setProfileStrategyTouched(true);
    setProfileDevices((current) => current.map((device) => {
      if (device.id !== activeDeviceId) return device;
      const routing = { ...(device.routing || profileRouting) };
      if (value) routing.strategy = value; else delete routing.strategy;
      return { ...device, routing };
    }));
  }

  async function saveRoutingWorkspace(event: FormEvent) {
    event.preventDefault();
    if (routingAutosaveRef.current) {
      clearTimeout(routingAutosaveRef.current);
      routingAutosaveRef.current = null;
      setRoutingAutosaving(false);
    }
    const operationId = "settings:routing-policy";
    publishMihomoOperation(operationId, "Маршрутизация Mihomo", "running", "Сохраняем правила маршрутизации…");
    setBusy(operationId);
    setError("");
    try {
      await request("/mihomo/routing/settings", { method: "PATCH", body: JSON.stringify({ values: routingDraft }) });
      routingDirtyRef.current = false;
      setRoutingDirty(false);
      await refresh();
      setNotice("Маршрутизация Mihomo сохранена. Обновите подписку в клиенте.");
      publishMihomoOperation(operationId, "Маршрутизация Mihomo", "success", "Правила сохранены");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Маршрутизация не сохранена";
      setError(message);
      publishMihomoOperation(operationId, "Маршрутизация Mihomo", "error", message);
    } finally {
      setBusy("");
    }
  }

  async function requestCoreRemoval() {
    setError("");
    let current: Status;
    try {
      current = (await request("/mihomo/status")) as Status;
      setStatus(current);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? `Не удалось проверить зависимости Mihomo перед удалением: ${cause.message}`
          : "Не удалось проверить зависимости Mihomo перед удалением",
      );
      return;
    }

    const usedProfiles = current.profiles_in_use || 0;
    const credentials = current.credentials || 0;
    if (usedProfiles > 0 || credentials > 0) {
      const channels = (current.channels_in_use || [])
        .map((id) => channelShort[id] || id)
        .join(", ");
      const confirmed = await confirmAction({
        title: "Mihomo используется подключениями",
        message:
          `Найдено профилей с подключениями: ${usedProfiles}. Credentials: ${credentials}.` +
          (channels ? ` Используемые компоненты: ${channels}.` : "") +
          " Удаление каскадно отзовёт эти credentials, удалит профили, компоненты, DNS и маршрутизацию Mihomo.",
        confirmLabel: "Продолжить удаление",
        phrase: "УДАЛИТЬ MIHOMO",
        danger: true,
      });
      if (!confirmed) return;
    }

    await onRemoveCore();
  }

  async function toggleModule(module: Module) {
    const install = !module.installed;
    const confirmed = await confirmAction({
      title: `${install ? "Установить" : "Удалить"} ${module.name}?`,
      message:
        install
          ? "Будет установлено ядро протокола Mihomo. С первым компонентом активируются базовые DNS и маршрутизация."
          : "Будет удалён только компонент Mihomo. Прямые подключения GATE.312 не изменяются.",
      confirmLabel: install ? "Установить" : "Удалить модуль",
      danger: !install,
    });
    if (!confirmed) return;
    const operationId = `module:${module.id}`;
    const operationLabel = `${install ? "Установка" : "Удаление"} ${module.name}`;
    publishMihomoOperation(operationId, operationLabel, "running", "Mihomo Manager применяет изменение…");
    setBusy(module.id);
    setError("");
    setNotice("");
    try {
      await request(`/mihomo/modules/${module.id}${install ? "/install" : ""}`, {
        method: install ? "POST" : "DELETE",
      });
      await refresh();
      setNotice(`${module.name}: ${install ? "установлен; DNS и маршрутизация Mihomo готовы" : "удалён из Mihomo"}.`);
      publishMihomoOperation(operationId, operationLabel, "success", "Изменение применено");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Операция Mihomo не выполнена";
      setError(message);
      publishMihomoOperation(operationId, operationLabel, "error", message);
    } finally {
      setBusy("");
    }
  }

  async function updateModule(module: Module) {
    const confirmed = await confirmAction({
      title: `Обновить ${module.name}?`,
      message: module.update_breaking
        ? `Доступна версия ${module.available_version} (сейчас ${module.installed_version || "—"}). Это смена старшей версии протокола — после обновления может понадобиться пересоздать профили или подключения, использующие этот канал.`
        : module.id === "transport-awg"
          ? "В репозитории Amnezia доступна новая сборка AWG. Пакеты будут обновлены без изменения конфигурации канала."
        : `Доступна версия ${module.available_version} (сейчас ${module.installed_version || "—"}). Активный канал и его подключения не будут разорваны.`,
      confirmLabel: "Обновить",
      danger: module.update_breaking,
    });
    if (!confirmed) return;
    const operationId = `module-update:${module.id}`;
    const operationLabel = `Обновление ${module.name}`;
    publishMihomoOperation(operationId, operationLabel, "running", "Mihomo Manager применяет обновление…");
    setBusy(`update:${module.id}`);
    setError("");
    setNotice("");
    try {
      await request(`/mihomo/modules/${module.id}/update`, { method: "POST" });
      await refresh();
      setNotice(`${module.name}: обновлён до ${module.available_version}.`);
      publishMihomoOperation(operationId, operationLabel, "success", "Обновление применено");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Обновление Mihomo-модуля не выполнено";
      setError(message);
      publishMihomoOperation(operationId, operationLabel, "error", message);
    } finally {
      setBusy("");
    }
  }

  function openSettings(module: Module) {
    setEditing(module);
    setSettingsDraft({ ...module.settings_values });
  }

  async function saveSettings(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    const operationId = `settings:${editing.id}`;
    const operationLabel = `Настройки ${editing.name}`;
    publishMihomoOperation(operationId, operationLabel, "running", "Сохраняем настройки внутреннего модуля…");
    setBusy(operationId);
    setError("");
    try {
      const settingsPath = editing.category === "dns"
        ? "/mihomo/dns/settings"
        : editing.category === "routing"
          ? "/mihomo/routing/settings"
          : `/mihomo/modules/${editing.id}/settings`;
      await request(settingsPath, {
        method: "PATCH",
        body: JSON.stringify({ values: settingsDraft }),
      });
      const editedName = editing.name;
      setEditing(null);
      await refresh();
      setNotice(`Настройки ${editedName} сохранены.`);
      publishMihomoOperation(operationId, operationLabel, "success", "Настройки сохранены");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Настройки не сохранены";
      setError(message);
      publishMihomoOperation(operationId, operationLabel, "error", message);
    } finally {
      setBusy("");
    }
  }

  function newProfile() {
    profileMutationId.current = crypto.randomUUID();
    setProfileStep(2);
    setProfileStrategyTouched(false);
    setProfileDialog("new");
    setProfileName("");
    setProfileConnections([]);
    setProfileRouting({});
    setProfileDevices([{ id: "profile-common", name: "Общие настройки профиля", scope: "common", routing: {} }]);
    setActiveDeviceId("profile-common");
  }

  function editProfile(profile: Profile) {
    profileMutationId.current = crypto.randomUUID();
    setProfileStep(2);
    setProfileStrategyTouched(true);
    setProfileDialog(profile);
    setProfileName(profile.name);
    setProfileConnections((profile.connections || []).map((connection) => ({
      ...connection,
      settings: connection.component === "transport-reality"
        ? { ...connection.settings, route_mode: connection.settings.route_mode || (connection.settings.cdn_enabled ? "both" : "direct") }
        : { ...connection.settings },
    })));
    setProfileRouting({ ...(profile.routing || {}) });
    const devices = (profile.devices?.length ? profile.devices : [{ id: "profile-common", name: "Общие настройки профиля", scope: "common" as const }]).map((device) => ({ ...device, routing: { ...(device.routing || profile.routing || {}) } }));
    setProfileDevices(devices);
    setActiveDeviceId(profile.common_device_id || devices.find((device) => device.scope === "common")?.id || devices[0].id);
  }

  function addProfileConnection(module: Module, vlessRoute: "direct" | "tls" | "cdn" = "direct") {
    const settings = Object.fromEntries((module.connection_settings || []).map((field) => [field.key, field.default]));
    if (module.id === "transport-reality") {
      settings.route_mode = vlessRoute;
      settings.cdn_enabled = vlessRoute === "cdn";
      if (vlessRoute === "cdn") settings.cdn_domain = String(routingPolicy?.values.preset_cdn_domain || "").trim();
      if (vlessRoute === "tls") settings.tls_domain = String(routingPolicy?.values.preset_tls_domain || "").trim();
    }
    setProfileConnections((current) => [...current, {
      id: `connection-${crypto.randomUUID()}`,
      component: module.id,
      name: module.id === "transport-reality" ? (vlessRoute === "cdn" ? "VLESS CDN" : vlessRoute === "tls" ? "VLESS TLS" : "VLESS REALITY") : module.name,
      device_id: activeDeviceId,
      settings,
    }]);
  }

  function updateProfileConnection(id: string, patch: Partial<ProfileConnection>) {
    setProfileConnections((current) => current.map((connection) => connection.id === id ? { ...connection, ...patch } : connection));
  }

  function applyProfilePreset(preset: ProfilePreset) {
    const cdnDomain = String(routingPolicy?.values.preset_cdn_domain || "").trim();
    const tlsDomain = String(routingPolicy?.values.preset_tls_domain || "").trim();
    const usedSingletons = new Set<string>();
    const connections: ProfileConnection[] = [];
    for (const definition of preset.components) {
      const componentId = definition.id === "$primary" ? String(routingPolicy?.values.preset_primary || "transport-reality")
        : definition.id === "$fallback" ? String(routingPolicy?.values.preset_fallback || "transport-awg") : definition.id;
      const componentModule = modules.find((item) => item.installed && item.id === componentId);
      if (!componentModule || (componentId !== "transport-reality" && usedSingletons.has(componentId))) continue;
      usedSingletons.add(componentId);
      const settings = Object.fromEntries((componentModule.connection_settings || []).map((field) => [field.key, field.default]));
      if (componentId === "transport-reality" && definition.tls) {
        settings.route_mode = "tls";
        settings.tls_domain = tlsDomain;
        settings.tls_transport = definition.transport || "xhttp";
      } else if (componentId === "transport-reality" && definition.cdn) {
        settings.route_mode = "cdn";
        settings.cdn_enabled = true;
        settings.cdn_domain = cdnDomain;
        if (definition.transport) settings.cdn_transport = definition.transport;
      } else if (componentId === "transport-reality") {
        settings.route_mode = "direct";
        settings.cdn_enabled = false;
        if (definition.transport) settings.transport = definition.transport;
      }
      connections.push({ id: `connection-${crypto.randomUUID()}`, component: componentId, name: definition.label || `${componentModule.name}${definition.cdn ? " · CDN" : ""}`, device_id: activeDeviceId, settings });
    }
    setProfileConnections((current) => [...current.filter((connection) => connection.device_id !== activeDeviceId), ...connections]);
    if (!profileStrategyTouched) setProfileDevices((current) => current.map((device) => device.id === activeDeviceId ? { ...device, routing: { ...(device.routing || profileRouting), strategy: preset.strategy } } : device));
    if (!profileName.trim()) setProfileName(preset.name);
  }

  function updateConnectionSetting(id: string, key: string, value: string | number | boolean) {
    setProfileConnections((current) => current.map((connection) => connection.id === id
      ? { ...connection, settings: { ...connection.settings, [key]: value } }
      : connection));
  }

  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    const creating = profileDialog === "new";
    const operationId = creating ? "profile:new" : `profile:${profileDialog && profileDialog !== "new" ? profileDialog.id : "edit"}`;
    const operationLabel = creating ? "Создание Mihomo-профиля" : `Сохранение профиля ${profileName}`;
    publishMihomoOperation(operationId, operationLabel, "running", "Обновляем профиль и credentials…");
    setBusy("profile");
    setError("");
    try {
      if (profileDialog === "new") {
        const created = await request("/mihomo/profiles", {
          method: "POST",
          body: JSON.stringify({ name: profileName, devices: profileDevices, connections: profileConnections, routing: profileDevices.find((device) => device.scope === "common")?.routing || profileRouting, operation_id: profileMutationId.current }),
        }) as Profile;
        setReadyDevices([]);
        setCreatedProfile(created);
      } else if (profileDialog) {
        const updated = await request(`/mihomo/profiles/${profileDialog.id}`, {
          method: "PATCH",
          body: JSON.stringify({ name: profileName, devices: profileDevices, connections: profileConnections, routing: profileDevices.find((device) => device.scope === "common")?.routing || profileRouting, operation_id: profileMutationId.current }),
        }) as Profile;
        setReadyDevices([]);
        setCreatedProfile(updated);
      }
      setProfileDialog(null);
      await refresh();
      setNotice("Mihomo-профиль сохранён.");
      publishMihomoOperation(operationId, operationLabel, "success", "Профиль готов");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Профиль не сохранён";
      setError(message);
      publishMihomoOperation(operationId, operationLabel, "error", message);
    } finally {
      setBusy("");
    }
  }

  async function removeProfile(profile: Profile) {
    const confirmed = await confirmAction({
      title: `Удалить профиль «${profile.name}»?`,
      message: "Будут удалены профиль Mihomo и его внутренние credentials. Direct-подключения GATE.312 не изменяются.",
      confirmLabel: "Удалить профиль",
      danger: true,
    });
    if (!confirmed) return;
    const operationId = `profile:${profile.id}`;
    const operationLabel = `Удаление профиля ${profile.name}`;
    publishMihomoOperation(operationId, operationLabel, "running", "Удаляем профиль и связанные credentials…");
    setBusy(operationId);
    setError("");
    try {
      await request(`/mihomo/profiles/${profile.id}`, { method: "DELETE" });
      await refresh();
      setNotice(`Профиль «${profile.name}» удалён.`);
      publishMihomoOperation(operationId, operationLabel, "success", "Профиль удалён");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Профиль не удалён";
      setError(message);
      publishMihomoOperation(operationId, operationLabel, "error", message);
    } finally {
      setBusy("");
    }
  }

  async function copyConfig(profile: Profile, device?: ProfileDevice) {
    setBusy(`config:${profile.id}`);
    setError("");
    try {
      const config = (await request(`/mihomo/profiles/${profile.id}/config${device ? `?device_id=${encodeURIComponent(device.id)}` : ""}`)) as string;
      await navigator.clipboard.writeText(config);
      setNotice(`config.yaml для «${device?.name || profile.name}» скопирован в буфер обмена.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось получить config.yaml");
    } finally {
      setBusy("");
    }
  }

  async function removeProfileDevice(profile: Profile, device: ProfileDevice) {
    if (device.scope === "common" || device.id === profile.common_device_id) {
      setError("Общие настройки профиля нельзя удалить");
      return;
    }
    const devices = profile.devices?.length ? profile.devices : [{ id: "device-1", name: "Устройство" }];
    if (devices.length <= 1) {
      setError("В профиле должно остаться хотя бы одно устройство. Удалите профиль целиком, если он больше не нужен.");
      return;
    }
    const deviceConnections = profile.connections.filter((connection) => (connection.device_id || devices[0].id) === device.id);
    const confirmed = await confirmAction({
      title: `Удалить устройство «${device.name}»?`,
      message: `Будут удалены устройство и ${deviceConnections.length} связанных подключений. Остальные устройства профиля не изменятся.`,
      confirmLabel: "Удалить устройство",
      danger: true,
    });
    if (!confirmed) return;
    const operationId = `device:${profile.id}:${device.id}`;
    publishMihomoOperation(operationId, `Удаление устройства ${device.name}`, "running", "Удаляем устройство и его credentials…");
    setBusy(operationId);
    setError("");
    try {
      await request(`/mihomo/profiles/${profile.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          devices: devices.filter((item) => item.id !== device.id),
          connections: profile.connections.filter((connection) => (connection.device_id || devices[0].id) !== device.id),
        }),
      });
      await refresh();
      setNotice(`Устройство «${device.name}» удалено из профиля «${profile.name}».`);
      publishMihomoOperation(operationId, `Удаление устройства ${device.name}`, "success", "Устройство удалено");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Устройство не удалено";
      setError(message);
      publishMihomoOperation(operationId, `Удаление устройства ${device.name}`, "error", message);
    } finally {
      setBusy("");
    }
  }

  async function downloadConfig(profile: Profile, device?: ProfileDevice) {
    setBusy(`download:${profile.id}`);
    setError("");
    try {
      const config = (await request(`/mihomo/profiles/${profile.id}/config${device ? `?device_id=${encodeURIComponent(device.id)}` : ""}`)) as string;
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob([config], { type: "application/yaml;charset=utf-8" }));
      link.download = `${[profile.name, device?.name].filter(Boolean).join("-").trim().replace(/[^a-zA-Z0-9а-яА-Я._-]+/g, "-") || "mihomo"}.yaml`;
      document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(link.href);
      setNotice(`Профиль «${profile.name}» скачан.`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось скачать профиль"); }
    finally { setBusy(""); }
  }

  async function copySubscription(profile: Profile) {
    setBusy(`subscription:${profile.id}`);
    setError("");
    try {
      const result = await request(`/mihomo/profiles/${profile.id}/subscription`) as { path: string };
      await navigator.clipboard.writeText(new URL(result.path, window.location.origin).toString());
      setNotice(`Единая ссылка профиля «${profile.name}» скопирована. Клиент с HWID появится как отдельное устройство, без HWID получит общие правила.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось получить ссылку подписки");
    } finally {
      setBusy("");
    }
  }

  function openPresetSettings() {
    setPresetDraft((routingPolicy?.presets || []).map((preset) => ({ ...preset, components: preset.components.map((item) => ({ ...item })) })));
    setPresetEditorIndex(0);
    setPresetDialog(true);
  }

  function addPresetDraft() {
    if (presetDraft.length >= 12) return;
    const suffix = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID().slice(0, 8) : String(Date.now()).slice(-8);
    setPresetEditorIndex(presetDraft.length);
    setPresetDraft((current) => [...current, {
      id: `preset-${suffix}`,
      name: `Новый пресет ${current.length + 1}`,
      description: "Пользовательская схема подключений",
      strategy: "select",
      components: [{ ...presetConnectionOptions[0] }],
    }]);
  }

  function removePresetDraft(index: number) {
    if (presetDraft.length <= 1) return;
    setPresetEditorIndex((active) => Math.max(0, active > index ? active - 1 : Math.min(active, presetDraft.length - 2)));
    setPresetDraft((current) => current.filter((_, itemIndex) => itemIndex !== index));
  }

  function togglePresetComponent(presetIndex: number, option: ProfilePreset["components"][number], checked: boolean) {
    setPresetDraft((current) => current.map((preset, index) => {
      if (index !== presetIndex) return preset;
      const matches = (component: ProfilePreset["components"][number]) => component.id === option.id
        && Boolean(component.cdn) === Boolean(option.cdn)
        && Boolean(component.tls) === Boolean(option.tls)
        && String(component.transport || "") === String(option.transport || "");
      return { ...preset, components: checked ? [...preset.components.filter((item) => !matches(item)), { ...option }] : preset.components.filter((item) => !matches(item)) };
    }));
  }

  async function savePresetSettings(event: FormEvent) {
    event.preventDefault(); setBusy("presets"); setError("");
    try {
      const result = await request("/mihomo/routing/presets", { method: "PATCH", body: JSON.stringify({ presets: presetDraft }) }) as { presets: ProfilePreset[] };
      setRoutingPolicy((current) => current ? { ...current, presets: result.presets } : current);
      setPresetDialog(false); setNotice("Настройки быстрых профилей сохранены.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось сохранить пресеты"); }
    finally { setBusy(""); }
  }

  const transportModules = useMemo(
    () => modules.filter((item) => item.category === "transport"),
    [modules],
  );
  const installedChannels = transportModules.filter((item) => item.installed);
  const profilePresets = routingPolicy?.presets || [];
  const policiesReady = installedChannels.length > 0 && Boolean(dnsPolicy && routingPolicy);
  const editableRuleLists = routingPolicy?.rule_lists || [];
  const selectedRuleList = editableRuleLists.find((item) => item.id === activeRuleList) || editableRuleLists[0];
  const selectedRuleValue = selectedRuleList ? String(routingDraft[selectedRuleList.key] ?? "@default") : "";
  const selectedRuleText = selectedRuleList ? (selectedRuleValue === "@default" ? selectedRuleList.default_rules : selectedRuleValue) : "";
  const selectedUdpExclusions = new Set(String(routingDraft.udp_tunnel_exclusions || "").split(",").filter(Boolean));
  const selectedP2pClients = new Set(String(routingDraft.direct_p2p_clients || "").split(",").filter(Boolean));
  const directGames = selectedGameIds(routingDraft.direct_games, defaultDirectGameIds);
  const normalizedGameSearch = gameSearch.trim().toLocaleLowerCase("ru");
  const visibleGames = gameRoutingCatalog.filter((game) => {
    const direct = directGames.has(game.id);
    if (normalizedGameSearch && !`${game.name} ${game.code} ${game.family}`.toLocaleLowerCase("ru").includes(normalizedGameSearch)) return false;
    if (gameFilter === "direct") return direct;
    if (gameFilter === "vpn") return !direct;
    if (gameFilter === "restricted") return game.family !== "Обычный маршрут";
    return true;
  });
  const normalizedRuleSearch = ruleSearch.trim().toLocaleLowerCase("ru");
  const visibleRuleGroups = [...new Set(profileDirectRules.map((rule) => rule.group))].map((group) => ({
    group,
    rules: profileDirectRules.filter((rule) => rule.group === group && (!normalizedRuleSearch || `${rule.title} ${rule.text} ${rule.code}`.toLocaleLowerCase("ru").includes(normalizedRuleSearch))),
  })).filter((section) => section.rules.length);
  const overviewDevices = profiles.reduce((sum, profile) => sum + registeredProfileDevices(profile).length, 0);
  const overviewConnections = profiles.reduce((sum, profile) => sum + profile.connections.length, 0);
  const overviewActiveConnections = Object.values(profileStats).reduce((sum, item) => sum + Number(item.summary.active || 0), 0);
  const overviewRx = Object.values(profileStats).reduce((sum, item) => sum + Number(item.summary.rx_bytes || 0), 0);
  const overviewTx = Object.values(profileStats).reduce((sum, item) => sum + Number(item.summary.tx_bytes || 0), 0);
  const overviewActiveProfiles = profiles.filter((profile) => Number(profileStats[profile.id]?.summary.active || 0) > 0).length;
  const overviewLatencies = Object.values(profileStats).map((item) => item.summary.latency_ms).filter((value): value is number => typeof value === "number");
  const overviewLatency = overviewLatencies.length ? Math.min(...overviewLatencies) : null;
  const overviewIssues = [
    !status?.active ? { title: "Ядро Mihomo не отвечает", text: "Проверьте состояние сервиса перед выдачей профилей.", view: "channels" as View } : null,
    !installedChannels.length ? { title: "Нет компонентов подключения", text: "Установите хотя бы один транспорт.", view: "channels" as View } : null,
    installedChannels.length > 0 && !policiesReady ? { title: "Политики ещё не готовы", text: "Проверьте DNS и настройки маршрутизации.", view: "dns" as View } : null,
    installedChannels.length > 0 && !profiles.length ? { title: "Нет профилей", text: "Создайте профиль и добавьте устройство.", view: "profiles" as View } : null,
    profiles.length > 0 && overviewActiveConnections === 0 ? { title: "Нет активных подключений", text: "Профили созданы, но клиенты сейчас не подключены.", view: "profiles" as View } : null,
  ].filter(Boolean) as Array<{ title: string; text: string; view: View }>;
  const overviewIssueTargets = overviewIssues.filter((issue, index, items) => items.findIndex((item) => item.view === issue.view) === index);
  const dnsModeField = dnsPolicy?.schema.find((field) => field.key === "enhanced_mode");
  const dnsPrimaryField = dnsPolicy?.schema.find((field) => field.key === "nameserver");
  const dnsFallbackField = dnsPolicy?.schema.find((field) => field.key === "fallback");
  const dnsOptions = (dnsPrimaryField?.options || []).map((option) => typeof option === "string" ? { value: option, label: option } : option);

  return (
    <section className="mihomoPage mihomoWorkspace" aria-label="Mihomo Manager">
      <nav className="mihomoHost__tabs mihomoTabs" aria-label="Разделы Mihomo">
        <Tab id="overview" current={view} onSelect={setView}>Обзор</Tab>
        <Tab id="profiles" current={view} onSelect={setView} badge={profiles.length}>Профили</Tab>
        <Tab id="channels" current={view} onSelect={setView} badge={installedChannels.length}>Компоненты</Tab>
        <Tab id="dns" current={view} onSelect={setView}>DNS</Tab>
        <Tab id="rules" current={view} onSelect={setView}>Правила</Tab>
        <Tab id="routing" current={view} onSelect={setView}>Настройки</Tab>
      </nav>

      <article className="mihomoCommandHero">
        <div className="mihomoHeroContent">
          <div className="mihomoHeroIntro">
            <p className="eyebrow">MIHOMO CONTROL</p>
            <div className="mihomoHeroTitleLine">
              <h1>Mihomo</h1>
              <span className={status?.active ? "mihomoCoreState online" : "mihomoCoreState"}>
                {status?.active ? "CORE ONLINE" : "CORE CHECK"}
              </span>
            </div>
            <p className="mihomoHeroLead">
              Контроль и управление профилями, компонентами подключений, DNS и политиками маршрутизации.
            </p>
          </div>

          <div className="mihomoHeroFacts">
            <HeroFact label="CORE" value={status?.core_version || "—"} note={status?.active ? "runtime active" : "status unavailable"} />
            <HeroFact label="PROFILES" value={String(status?.profiles ?? profiles.length)} note={`${status?.profiles_in_use || 0} in use`} />
            <HeroFact label="CREDENTIALS" value={String(status?.credentials || 0)} note="internal only" />
            <HeroFact label="COMPONENTS" value={`${status?.channels_installed ?? installedChannels.length}/${status?.modules_total ?? modules.length}`} note={`${status?.channels_in_use?.length || 0} in use`} />
            <HeroFact label="ENDPOINT" value={status?.endpoint || "—"} note="Mihomo node" wide />
          </div>

          <div className="mihomoHeroActions">
            <button className="ghostButton" type="button" onClick={() => void refresh()} disabled={Boolean(busy)}>
              Обновить
            </button>
            <button
              className="dangerButton"
              type="button"
              onClick={() => void requestCoreRemoval()}
              disabled={coreBusy || Boolean(busy)}
            >
              {coreBusy ? "Удаляется…" : "Удалить Mihomo"}
            </button>
          </div>
        </div>
      </article>

      {error && <div className="mihomoMessage is-error">{error}</div>}
      {notice && <div className="mihomoMessage is-ok">{notice}</div>}

      {view === "overview" && (
        <div className="mihomoOverviewV3">
          <section className="mihomoOverviewPulse">
            <header><div><p className="eyebrow">LIVE</p><h2>{overviewActiveConnections ? `${overviewActiveConnections} подключений активно` : "Активных подключений нет"}</h2></div><button type="button" className="ghostButton" onClick={() => void refresh()} disabled={Boolean(busy)}>Обновить</button></header>
            <div className="mihomoOverviewPulseGrid">
              <article><small>ПРОФИЛИ</small><strong>{overviewActiveProfiles}<i> / {profiles.length}</i></strong><span>с активными клиентами</span></article>
              <article><small>УСТРОЙСТВА</small><strong>{overviewDevices}</strong><span>{overviewConnections} каналов настроено</span></article>
              <article><small>ТРАФИК</small><strong>↓ {bytes(overviewRx)}</strong><span>↑ {bytes(overviewTx)}</span></article>
              <article><small>ЛУЧШИЙ ПИНГ</small><strong>{overviewLatency != null ? `${Math.round(overviewLatency)} мс` : "—"}</strong><span>{overviewLatency != null ? "по активным профилям" : "нет измерений"}</span></article>
            </div>
          </section>

          <section className="mihomoOverviewDesk">
            <article className="mihomoOverviewLiveProfiles">
              <header><div><p className="eyebrow">ПРОФИЛИ</p><h3>Текущая активность</h3></div>{profiles.length > 0 && <button type="button" onClick={() => setView("profiles")}>Все профили</button>}</header>
              <div>{profiles.slice(0, 8).map((profile) => { const item = profileStats[profile.id]?.summary; const devices = registeredProfileDevices(profile).length; return <div className="mihomoOverviewLiveRow" key={profile.id}><i className={item?.active ? "is-online" : ""} /><p><b>{profile.name}</b><small>{devices} устройств · {profile.connections.length} каналов</small></p><strong>{item ? `${item.active}/${item.configured}` : "—"}<small>активно</small></strong><span>{item?.latency_ms != null ? `${Math.round(item.latency_ms)} мс` : "—"}<small>пинг</small></span><em>↓ {bytes(item?.rx_bytes || 0)}<small>↑ {bytes(item?.tx_bytes || 0)}</small></em></div>; })}</div>
              {!profiles.length && <div className="mihomoOverviewBlank"><b>Здесь появится активность профилей</b><span>{installedChannels.length ? "Создайте профиль и добавьте первое устройство." : "Сначала установите компонент подключения."}</span></div>}
            </article>

            <aside className={`mihomoOverviewHealth${overviewIssueTargets.length ? " has-issues" : ""}`}>
              <header><p className="eyebrow">СОСТОЯНИЕ</p><h3>{overviewIssueTargets.length ? "Нужна настройка" : "Система готова"}</h3><span>{overviewIssueTargets.length || "OK"}</span></header>
              <div>{overviewIssueTargets.length ? overviewIssueTargets.map((issue) => <button type="button" key={issue.view} onClick={() => setView(issue.view)}><i /><p><b>{issue.title}</b><small>{issue.text}</small></p><em>→</em></button>) : <><div className="mihomoOverviewCheck"><i /><p><b>Ядро</b><small>{status?.core_version || "Работает"}</small></p></div><div className="mihomoOverviewCheck"><i /><p><b>Компоненты</b><small>{installedChannels.length} установлено</small></p></div><div className="mihomoOverviewCheck"><i /><p><b>DNS и правила</b><small>Доступны профилям</small></p></div></>}</div>
              <footer>{!installedChannels.length ? <button type="button" className="primaryButton" onClick={() => setView("channels")}>Установить компонент</button> : !profiles.length ? <button type="button" className="primaryButton" onClick={newProfile}>Создать профиль</button> : <small>Дополнительных действий не требуется</small>}</footer>
            </aside>
          </section>
        </div>
      )}

      {view === "profiles" && (
        <article className="mihomoWorkspacePanel">
          <header className="mihomoSectionHead">
            <div>
              <p className="eyebrow">MIHOMO PROFILES</p>
              <h2>Профили устройств</h2>
              <p>Каждый профиль собирается из независимых подключений на базе установленных компонентов.</p>
            </div>
            <button className="primaryButton" onClick={newProfile} disabled={!installedChannels.length}>Новый профиль</button>
          </header>
          {!installedChannels.length && (
            <div className="mihomoHint">Сначала установите хотя бы один компонент протокола Mihomo.</div>
          )}
          <div className="mihomoProfiles">
            {profiles.map((profile) => { const allDevices = profile.devices?.length ? profile.devices : [{ id: "profile-common", name: "Общие настройки профиля", scope: "common" as const }]; const devices = registeredProfileDevices(profile); const commonDevice = allDevices.find((device) => device.scope === "common" || device.id === profile.common_device_id) || allDevices[0]; const stats = profileStats[profile.id]?.summary; const ruleCount = profileDirectRules.filter((rule) => Boolean(profile.routing?.[rule.key])).length; return (
              <section className="mihomoProfileCard" key={profile.id}>
                <header className="mihomoProfileHeader">
                  <button type="button" className="mihomoProfileToggle" aria-expanded={expandedDeviceLists.has(profile.id)} onClick={() => toggleCollapsed(setExpandedDeviceLists, profile.id)}><div className="mihomoProfileIdentity"><span className="mihomoProfileIcon">M</span><p><b>{profile.name}</b><small>{devices.length} HWID-устройств · общий пул {profile.connections.filter((connection) => connection.device_id === commonDevice.id).length} каналов{profile.subscription_status === "obsolete" ? " · подписка устарела, требуется новая установка" : ""}</small><em>ID {profile.id} · обновлён {new Date(profile.updated_at || profile.created_at).toLocaleString("ru-RU")}</em></p></div><div className="mihomoProfileSummary"><div><small>Состояние</small><strong className={stats?.active ? "is-online" : ""}><i />{stats ? `${stats.active} из ${stats.configured}` : "—"}</strong><span>активных каналов</span></div><div><small>Трафик</small><strong>↓ {bytes(stats?.rx_bytes || 0)}</strong><span>↑ {bytes(stats?.tx_bytes || 0)}</span></div><div><small>Последняя связь</small><strong>{stats?.last_handshake_age_s != null ? duration(stats.last_handshake_age_s) : "—"}</strong><span>{stats?.last_handshake_age_s != null ? "назад" : "подключений нет"}</span></div></div><i className="mihomoCollapseChevron" aria-hidden="true" /></button>
                  <div className="mihomoRowActions"><button className="primaryButton" onClick={() => void copySubscription(profile)} disabled={busy === `subscription:${profile.id}`}>{profile.subscription_status === "obsolete" ? "Обновить подписку" : "Скопировать подписку"}</button><button onClick={() => editProfile(profile)}>Настроить</button><button className="dangerButton" onClick={() => void removeProfile(profile)} disabled={busy === `profile:${profile.id}`}>Удалить</button></div>
                </header>
                {expandedDeviceLists.has(profile.id) && <div className="mihomoProfileDevices">
                  {devices.map((device) => {
                    const connections = profile.connections.filter((connection) => connection.device_id === device.id);
                    const connectionStats = connections.map((connection) => profileStats[profile.id]?.connections?.[connection.id]);
                    const onlineCount = connectionStats.filter((item) => Boolean(item?.active || item?.endpoint || Number(item?.active_connections || 0))).length;
                    const deviceRx = connectionStats.reduce((sum, item) => sum + Number(item?.rx_bytes || 0), 0);
                    const deviceTx = connectionStats.reduce((sum, item) => sum + Number(item?.tx_bytes || 0), 0);
                    const deviceLatency = profileStats[profile.id]?.devices?.[device.id]?.latency_ms;
                    const protocolListKey = `${profile.id}:${device.id}`;
                    const protocolsExpanded = expandedProtocolLists.has(protocolListKey);
                    const deleting = busy === `device:${profile.id}:${device.id}`;
                    return <section key={device.id} className="mihomoProfileDevice">
                      <header className="mihomoDeviceHeader">
                        <button type="button" className="mihomoDeviceToggle" aria-expanded={protocolsExpanded} onClick={() => toggleCollapsed(setExpandedProtocolLists, protocolListKey)}><div className="mihomoDeviceIdentity"><span>{device.scope === "common" ? "ALL" : devicePlatformMeta(device).code}</span><p><b>{device.scope === "common" ? "Общие настройки профиля" : device.name}</b><small>{connections.length} каналов · {device.scope === "hwid" ? deviceSystemLabel(device) : device.scope === "common" ? "для клиентов без HWID и новых устройств" : "устаревшее устройство"}</small>{device.scope === "hwid" && <em>{device.last_seen_at ? `Последний запрос ${new Date(device.last_seen_at).toLocaleString("ru-RU")}` : "HWID зарегистрирован"}</em>}</p></div><div className="mihomoDeviceTotals"><span><small>КАНАЛЫ</small><b>{onlineCount}/{connections.length}</b></span><span><small>ПРАВИЛА</small><b>{ruleCount}</b></span><span><small>ТРАФИК</small><b>↓ {bytes(deviceRx)} · ↑ {bytes(deviceTx)}</b></span><span><small>PING</small><b>{deviceLatency != null ? `${Math.round(deviceLatency)} ms` : "—"}</b></span></div><i className="mihomoCollapseChevron" aria-hidden="true" /></button>
                        <nav className="mihomoDeviceActions"><button onClick={() => void downloadConfig(profile, device)} disabled={busy === `download:${profile.id}`}>Скачать YAML</button><button onClick={() => void copyConfig(profile, device)} disabled={busy === `config:${profile.id}`}>Копировать YAML</button>{device.scope !== "common" && <button className="dangerButton" onClick={() => void removeProfileDevice(profile, device)} disabled={deleting} title="Удалить это устройство и его подключения">{deleting ? "Удаление…" : "Удалить устройство"}</button>}</nav>
                      </header>
                    {protocolsExpanded && <div className="mihomoProfileProtocolStats">{connections.map((connection) => { const item = profileStats[profile.id]?.connections?.[connection.id]; const online = Boolean(item?.active || item?.endpoint || Number(item?.active_connections || 0)); return <div key={connection.id}><span className={`protocol-${connection.component}${online ? " online" : ""}`}>{channelShort[connection.component] || "CH"}<i /></span><p><b>{connection.name}</b><small>↓ {bytes(item?.rx_bytes || 0)} · ↑ {bytes(item?.tx_bytes || 0)}{item?.latency_ms != null ? ` · ${Math.round(item.latency_ms)} ms` : ""}</small>{item?.handshake_age_s != null && <em>Связь {duration(item.handshake_age_s)} назад</em>}</p></div>; })}{!connections.length && <p className="mihomoConnectionEmpty">Для устройства пока нет подключений.</p>}</div>}
                    </section>;
                  })}
                  {!devices.length && <p className="mihomoConnectionEmpty">HWID-устройства пока не зарегистрированы. Клиенты без HWID используют общий пул и не отображаются как устройства.</p>}
                </div>}
              </section>
            ); })}
            {!profiles.length && <Empty title="Профилей пока нет" text="Установите компонент и соберите первое подключение в профиле." />}
          </div>
          <section className="mihomoClientGuide">
            <header><p className="eyebrow">CLIENT SETUP</p><h3>Настройка и подключение</h3><p>Скопируйте единую ссылку профиля и добавьте её в клиент. Клиент без HWID использует общий пул; поддерживаемое приложение автоматически зарегистрирует устройство.</p></header>
            <ol><li><b>1</b><span>Нажмите «Скопировать подписку» у нужного профиля.</span></li><li><b>2</b><span>В клиенте добавьте удалённый профиль по URL.</span></li><li><b>3</b><span>После изменений обновите профиль в клиенте.</span></li></ol>
            <div className="mihomoClientApps">
              <a href="https://github.com/clash-verge-rev/clash-verge-rev/releases" target="_blank" rel="noreferrer"><small>PC · WINDOWS / LINUX</small><strong>Clash Verge Rev</strong><span>Официальные релизы ↗</span></a>
              <a href="https://apps.apple.com/us/app/clash-mi/id6744321968" target="_blank" rel="noreferrer"><small>IPHONE / IPAD</small><strong>Clash Mi</strong><span>Скачать в App Store ↗</span></a>
              <a href="https://github.com/MetaCubeX/ClashMetaForAndroid/releases" target="_blank" rel="noreferrer"><small>ANDROID</small><strong>Clash Meta for Android</strong><span>Официальные APK-релизы ↗</span></a>
              <a href="https://github.com/clash-verge-rev/clash-verge-rev/releases" target="_blank" rel="noreferrer"><small>MAC · INTEL / APPLE SILICON</small><strong>Clash Verge Rev</strong><span>Скачать DMG ↗</span></a>
            </div>
          </section>
        </article>
      )}

      {view === "channels" && (
        <ModuleCatalog
          title="Компоненты подключений Mihomo"
          description="Установка и обновление серверных компонентов для подключений Mihomo."
          modules={transportModules}
          busy={busy}
          onToggle={toggleModule}
          onUpdate={updateModule}
          onSettings={openSettings}
        />
      )}

      {view === "dns" && (
        <form className="mihomoDnsWorkspace mihomoDnsV2" onSubmit={saveDnsWorkspace}>
          <header className="mihomoDnsHeader"><div><p className="eyebrow">DNS ПРОФИЛЕЙ</p><h2>Разрешение доменов</h2><p>Выберите режим и два независимых резолвера для подписок Mihomo.</p></div><span className={policiesReady ? "mihomoPill is-online" : "mihomoPill"}><i />{policiesReady ? "ГОТОВ" : "ОЖИДАНИЕ"}</span></header>
          <section className="mihomoDnsSummary"><div><small>РЕЖИМ</small><b>{String(dnsDraft.enhanced_mode || "fake-ip") === "fake-ip" ? "Fake IP" : "Redir host"}</b><span>{String(dnsDraft.enhanced_mode || "fake-ip") === "fake-ip" ? "Быстрее и точнее для правил" : "Максимальная совместимость"}</span></div><div><small>ОСНОВНОЙ</small><b>{dnsOptions.find((item) => item.value === String(dnsDraft.nameserver || ""))?.label || "Не выбран"}</b><span>{dnsProviderMeta[String(dnsDraft.nameserver || "")]?.note || "DNS профиля"}</span></div><div><small>РЕЗЕРВНЫЙ</small><b>{dnsOptions.find((item) => item.value === String(dnsDraft.fallback || ""))?.label || "Не выбран"}</b><span>{dnsProviderMeta[String(dnsDraft.fallback || "")]?.note || "Используется при сбое"}</span></div></section>
          <section className="mihomoDnsMode"><header><div><b>Режим обработки</b><small>Как Mihomo сопоставляет домены с правилами маршрутизации.</small></div></header><div>{(dnsModeField?.options || ["fake-ip", "redir-host"]).map((option) => { const value = typeof option === "string" ? option : option.value; const selected = String(dnsDraft.enhanced_mode || dnsModeField?.default || "fake-ip") === value; return <button type="button" key={value} className={selected ? "is-selected" : ""} onClick={() => updateDnsDraft("enhanced_mode", value)}><span>{value === "fake-ip" ? "FAST" : "COMPAT"}</span><p><b>{value === "fake-ip" ? "Fake IP" : "Redir host"}</b><small>{value === "fake-ip" ? "Рекомендуется для TUN и правил по доменам" : "Для приложений, несовместимых с Fake IP"}</small></p><i>{selected ? "Выбран" : ""}</i></button>; })}</div></section>
          <section className="mihomoDnsAdvanced"><header><div><b>Дополнительная обработка</b><small>Параметры попадут непосредственно в DNS-секцию профилей.</small></div></header><div><label className={Boolean(dnsDraft.ipv6) ? "is-enabled" : ""}><span><b>IPv6</b><small>Возвращать записи AAAA</small></span><input type="checkbox" checked={Boolean(dnsDraft.ipv6)} onChange={(event) => updateDnsDraft("ipv6", event.target.checked)} /></label><label className={Boolean(dnsDraft.prefer_h3) ? "is-enabled" : ""}><span><b>HTTP/3</b><small>Для совместимых DoH-серверов</small></span><input type="checkbox" checked={Boolean(dnsDraft.prefer_h3)} onChange={(event) => updateDnsDraft("prefer_h3", event.target.checked)} /></label><label><span><b>Кэш DNS</b><small>Алгоритм вытеснения записей</small></span><select value={String(dnsDraft.cache_algorithm || "lru")} onChange={(event) => updateDnsDraft("cache_algorithm", event.target.value)}><option value="lru">LRU · совместимый</option><option value="arc">ARC · адаптивный</option></select></label></div>{String(dnsDraft.enhanced_mode || "fake-ip") === "fake-ip" && <label className="mihomoDnsFakeIp"><span><b>Исключения Fake IP</b><small>По одному домену или маске на строку. Для них клиент получит реальный IP.</small></span><textarea rows={4} value={String(dnsDraft.fake_ip_filter || "")} spellCheck={false} placeholder={"*.lan\n*.local"} onChange={(event) => updateDnsDraft("fake_ip_filter", event.target.value)} /></label>}</section>
          {([['nameserver', 'Основной DNS', 'Используется для обычных запросов.'], ['fallback', 'Резервный DNS', 'Подхватывает запросы при недоступности основного.']] as const).map(([key, title, note]) => <section className="mihomoDnsProviders" key={key}><header><div><b>{title}</b><small>{note}</small></div></header><div>{dnsOptions.map((option) => { const selected = String(dnsDraft[key] || (key === 'nameserver' ? dnsPrimaryField?.default : dnsFallbackField?.default) || "") === option.value; const meta = dnsProviderMeta[option.value] || { code: "DNS", note: "Пользовательский резолвер" }; return <button type="button" key={option.value} className={selected ? "is-selected" : ""} onClick={() => updateDnsDraft(key, option.value)}><span>{meta.code}</span><p><b>{option.label}</b><small>{meta.note}</small></p><i>{selected ? "Выбран" : ""}</i></button>; })}</div></section>)}
          <aside className="mihomoDnsNote"><b>Применение настроек</b><span>Основной и резервный DNS должны отличаться. После сохранения обновите подписку в клиенте, чтобы устройство получило новую конфигурацию.</span></aside>
          <footer className="mihomoDnsFooter"><span>{dnsDirty ? "Есть несохранённые изменения" : "Настройки синхронизированы"}</span><button className="primaryButton" type="submit" disabled={!dnsDirty || busy === "settings:dns-private" || dnsDraft.nameserver === dnsDraft.fallback}>{busy === "settings:dns-private" ? "Сохранение…" : dnsDraft.nameserver === dnsDraft.fallback ? "Выберите разные DNS" : "Сохранить DNS"}</button></footer>
        </form>
      )}

      {(view === "routing" || view === "rules") && (
        <form className="mihomoRoutingWorkspace" onSubmit={saveRoutingWorkspace}>
          <header className="mihomoRoutingHeader">
            {view === "rules" ? <div><p className="eyebrow">MIHOMO RULE STUDIO</p><h2>Правила</h2><p>Домены, игровые маршруты, UDP и P2P вынесены в отдельную библиотеку. Каждый набор включается отдельно в профиле.</p></div> : <div><p className="eyebrow">MIHOMO SETTINGS</p><h2>Настройки Mihomo</h2><p>Общее поведение профилей, проверка каналов, домены TLS/CDN и редактируемые шаблоны подключений.</p></div>}
            <span className={policiesReady ? "mihomoPill is-online" : "mihomoPill"}><i />{policiesReady ? "АКТИВНА" : "ОЖИДАНИЕ"}</span>
          </header>

          {view === "rules" && <>
          <section className="mihomoRuleStudio">
            <aside><header><b>Библиотека правил</b><small>Выберите набор для редактирования</small><input aria-label="Поиск правил" value={ruleSearch} placeholder="Найти правило…" onChange={(event) => setRuleSearch(event.target.value)} /></header><div className="mihomoRuleLibrary">{visibleRuleGroups.map((section) => <section key={section.group}><h4>{section.group}</h4>{section.rules.map((item) => { const profileCount = profiles.filter((profile) => Boolean(profile.routing?.[item.key])).length; return <button type="button" key={item.key} className={activeRuleList === item.key ? "is-active" : ""} onClick={() => setActiveRuleList(item.key)}><span>{item.code}</span><p><b>{item.title}</b><small>{item.text}</small></p><i>{profileCount} проф.</i></button>; })}</section>)}</div></aside>
            <article>
              {activeRuleList === "direct_games_udp_enabled" ? <>
                <header className="mihomoRuleEditorHead"><div><p className="eyebrow">NETWORK RULE</p><h3>UDP напрямую</h3><p>Выбранные исключения остаются в GATE.312, остальной UDP идёт напрямую.</p></div><span>{selectedUdpExclusions.size} искл.</span></header>
                <div className="mihomoGameCatalog">
                  {udpExclusionCatalog.map((resource) => { const selected = selectedUdpExclusions.has(resource.id); return <button type="button" key={resource.id} className={selected ? "is-selected" : ""} aria-pressed={selected} onClick={() => toggleUdpExclusion(resource.id)}><span>{resource.code}</span><b>{resource.name}</b><i>{selected ? "Через VPN" : "Напрямую"}</i></button>; })}
                </div>
                <label className="mihomoCustomGames"><span><b>Дополнительные исключения</b><small>Полные правила Mihomo через GATE.312, по одному на строку.</small></span><textarea rows={5} value={String(routingDraft.udp_tunnel_exclusions_rules || "")} placeholder={"DOMAIN-SUFFIX,example.com,GATE.312\nDST-PORT,3478,GATE.312"} onChange={(event) => updateRoutingDraft("udp_tunnel_exclusions_rules", event.target.value)} /></label>
              </> : activeRuleList === "direct_p2p_enabled" ? <>
                <header className="mihomoRuleEditorHead"><div><p className="eyebrow">PROCESS RULES</p><h3>P2P и торренты напрямую</h3><p>Выбранные клиенты обходят GATE.312. Используйте только если провайдер разрешает P2P.</p></div><span>{selectedP2pClients.size} напрямую</span></header>
                <div className="mihomoMessage is-error">Прямой маршрут раскрывает ваш публичный IP участникам раздачи и не обходит ограничения провайдера.</div>
                <div className="mihomoGameCatalog">
                  {p2pClientCatalog.map((client) => { const selected = selectedP2pClients.has(client.id); return <button type="button" key={client.id} className={selected ? "is-selected" : ""} aria-pressed={selected} onClick={() => toggleP2pClient(client.id)}><span>{client.code}</span><b>{client.name}</b><i>{selected ? "Напрямую" : "Через VPN"}</i></button>; })}
                </div>
                <label className="mihomoCustomGames"><span><b>Дополнительные процессы</b><small>Имена P2P-процессов напрямую, по одному на строку.</small></span><textarea rows={5} value={String(routingDraft.direct_p2p_processes || "")} placeholder={"client.exe\np2p-client"} onChange={(event) => updateRoutingDraft("direct_p2p_processes", event.target.value)} /></label>
              </> : activeRuleList === "direct_games_enabled" ? <>
                <header className="mihomoRuleEditorHead"><div><p className="eyebrow">PROCESS RULES</p><h3>Маршруты игр</h3><p>Игры без ограничений для российских IP идут напрямую. Игры с известными ограничениями заранее направлены через GATE.312; любое направление можно изменить.</p></div><span>{directGames.size} напрямую</span></header>
                <div className="mihomoCatalogToolbar"><input aria-label="Поиск игр" value={gameSearch} placeholder="Найти игру…" onChange={(event) => setGameSearch(event.target.value)} /><nav>{([['all', 'Все'], ['direct', 'Напрямую'], ['vpn', 'VPN'], ['restricted', 'Ограничения РФ']] as const).map(([value, label]) => <button type="button" key={value} className={gameFilter === value ? "is-active" : ""} onClick={() => setGameFilter(value)}>{label}</button>)}</nav><span>{visibleGames.length} из {gameRoutingCatalog.length}</span></div>
                <div className="mihomoGameCatalog mihomoLargeCatalog">
                  {visibleGames.map((game) => { const direct = directGames.has(game.id); return <button type="button" key={game.id} className={direct ? "is-selected" : ""} aria-pressed={direct} title={game.family} onClick={() => setGameRoute(game.id, direct ? "tunnel" : "direct")}><span>{game.code}</span><b>{game.name}</b><i>{direct ? "Напрямую" : "Через VPN"}</i></button>; })}
                </div>
                {!visibleGames.length && <div className="mihomoHint">Игры по этому запросу не найдены.</div>}
                <p className="mihomoGamesHint">Для определения процесса нужен TUN-режим. На iPhone сопоставление по приложению может быть недоступно.</p>
                <label className="mihomoCustomGames"><span><b>Дополнительные процессы</b><small>По одному имени процесса или package name на строку.</small></span><textarea rows={5} value={String(routingDraft.direct_game_processes || "")} placeholder={"mygame.exe\ncom.publisher.game"} onChange={(event) => updateRoutingDraft("direct_game_processes", event.target.value)} /></label>
              </> : selectedRuleList ? <>
                <header className="mihomoRuleEditorHead"><div><p className="eyebrow">DOMAIN / IP RULES</p><h3>{selectedRuleList.title}</h3><p>{selectedRuleList.description} Выберите нужные группы.</p></div><span>{selectedRuleText.split("\n").filter(Boolean).length} правил</span></header>
                {ruleIconGroups[selectedRuleList.id] && <div className={`mihomoGameCatalog${ruleIconGroups[selectedRuleList.id].length > 12 ? " mihomoLargeCatalog" : ""}`}>{ruleIconGroups[selectedRuleList.id].map((group) => { const lines = ruleGroupLines(selectedRuleList, group); const current = new Set(selectedRuleText.split("\n").map((line) => line.trim()).filter(Boolean)); const enabled = lines.length > 0 && lines.every((line) => current.has(line)); return <button type="button" key={group.id} className={enabled ? "is-selected" : ""} aria-pressed={enabled} onClick={() => toggleRuleIconGroup(selectedRuleList, group)}><span>{group.code}</span><b>{group.name}</b><i>{enabled ? "Включено" : "Выключено"}</i></button>; })}</div>}
                <footer className="mihomoRuleEditorActions"><span>{routingAutosaving ? "Сохраняем выбор…" : selectedRuleValue === "@default" ? "Стандартный набор · сохраняется автоматически" : "Выбор сохранён автоматически"}</span><nav><button type="button" className="ghostButton" disabled={selectedRuleValue === "@default"} onClick={() => updateRoutingDraft(selectedRuleList.key, "@default", true)}>По умолчанию</button></nav></footer>
                <label className="mihomoCustomGames"><span><b>Дополнительные исключения</b><small>Собственные правила Mihomo, по одному на строку. Они дополняют выбранные выше группы.</small></span><textarea rows={5} value={ruleExtraLines(selectedRuleList).join("\n")} spellCheck={false} placeholder={"DOMAIN-SUFFIX,example.com,DIRECT\nIP-CIDR,203.0.113.0/24,DIRECT,no-resolve"} onChange={(event) => updateRuleExtras(selectedRuleList, event.target.value)} /></label>
              </> : <div className="mihomoHint">Списки правил загружаются…</div>}
            </article>
          </section>

          <details className="mihomoAdvancedRules"><summary><span><b>Дополнительные правила</b><small>Для опытных пользователей</small></span><i>Открыть редактор</i></summary><label><span>По одному правилу Mihomo на строку</span><textarea rows={7} value={String(routingDraft.rules || "")} placeholder={"DOMAIN-SUFFIX,example.com,DIRECT\nDOMAIN,api.example.com,DIRECT"} onChange={(event) => updateRoutingDraft("rules", event.target.value)} /></label></details>
          </>}

          {view === "routing" && <>
          <section className="mihomoSettingsBoard">
            <div className="mihomoSettingsSummary">
              <div><small>РЕЖИМ</small><b>{routingDraft.mode === "global" ? "Global" : "Rule"}</b><span>маршрутизация трафика</span></div>
              <div><small>СТРАТЕГИЯ</small><b>{routingDraft.strategy === "url-test" ? "Latency" : routingDraft.strategy === "select" ? "Manual" : "Fallback"}</b><span>выбор защищённого канала</span></div>
              <div><small>ПРЕСЕТЫ</small><b>{profilePresets.length}</b><span>{profilePresets.reduce((sum, item) => sum + item.components.length, 0)} каналов в шаблонах</span></div>
              <button type="button" className="primaryButton" onClick={openPresetSettings}>Редактировать пресеты</button>
            </div>
            <div className="mihomoSettingsGrid">
              <article><header><span>01</span><div><b>Поведение трафика</b><small>Базовый режим всех профилей</small></div></header><div className="mihomoRoutingFields">
                <label><span>Режим</span><select value={String(routingDraft.mode || "rule")} onChange={(event) => updateRoutingDraft("mode", event.target.value)}><option value="rule">По правилам</option><option value="global">Весь трафик через VPN</option></select></label>
                <label><span>Стратегия по умолчанию</span><select value={String(routingDraft.strategy || "fallback")} onChange={(event) => updateRoutingDraft("strategy", event.target.value)}><option value="fallback">Надёжный канал + резерв</option><option value="url-test">Самый быстрый канал</option><option value="select">Выбирать вручную</option></select></label>
              </div></article>
              <article><header><span>02</span><div><b>Проверка доступности</b><small>Health check защищённых каналов</small></div></header><div className="mihomoRoutingFields">
                <label className="is-wide"><span>Адрес проверки</span><input value={String(routingDraft.test_url || "")} onChange={(event) => updateRoutingDraft("test_url", event.target.value)} /></label>
                <label className="is-wide"><span>Интервал, секунд</span><input type="number" min={30} max={3600} value={Number(routingDraft.interval || 180)} onChange={(event) => updateRoutingDraft("interval", Number(event.target.value))} /></label>
              </div></article>
              <article className="is-wide"><header><span>03</span><div><b>Домены VLESS</b><small>Используются каналами прямого TLS и CDN внутри пресетов</small></div></header><div className="mihomoRoutingFields mihomoDomainFields">
                <label><span>CDN-домен</span><input value={String(routingDraft.preset_cdn_domain || "")} placeholder="cdn.example.com" onChange={(event) => updateRoutingDraft("preset_cdn_domain", event.target.value)} /><small>{routingDraft.preset_cdn_domain ? "Готов для CDN-транспортов" : "Нужен для CDN-каналов"}</small></label>
                <label><span>Прямой TLS-домен</span><input value={String(routingDraft.preset_tls_domain || "")} placeholder="tls.example.com" onChange={(event) => updateRoutingDraft("preset_tls_domain", event.target.value)} /><small>{routingDraft.preset_tls_domain ? "Готов для прямого TLS" : "Нужен для TLS-каналов"}</small></label>
              </div></article>
            </div>
          </section>

          <section className="mihomoPresetCatalogSettings">
            <header><div><p className="eyebrow">PROFILE BLUEPRINTS</p><h3>Шаблоны новых профилей</h3><small>Встроенные пресеты не заблокированы: их можно переименовать, изменить или удалить.</small></div><button type="button" className="ghostButton" onClick={openPresetSettings}>Управлять</button></header>
            <div>{profilePresets.map((preset, index) => <article key={preset.id}><span>{String(index + 1).padStart(2, "0")}</span><p><b>{preset.name}</b><small>{preset.description || "Без описания"}</small></p><em>{preset.components.length} каналов</em><i>{preset.strategy === "url-test" ? "по задержке" : preset.strategy === "select" ? "вручную" : "резерв"}</i></article>)}</div>
          </section>
          </>}

          <footer className="mihomoRoutingFooter"><span>{routingDirty ? "Есть несохранённые изменения" : "Настройки сохранены. После изменения обновите подписку в клиенте."}</span><button className="primaryButton" type="submit" disabled={!routingDirty || busy === "settings:routing-policy"}>{busy === "settings:routing-policy" ? "Сохранение…" : view === "rules" ? "Сохранить правила" : "Сохранить настройки"}</button></footer>
        </form>
      )}

      {editing && createPortal(
        <div className="mihomoDialogBackdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setEditing(null);
        }}>
          <form className="mihomoDialog" onSubmit={saveSettings}>
            <header>
              <div><p className="eyebrow">MODULE SETTINGS</p><h2>{editing.name}</h2></div>
              <button type="button" className="iconButton" onClick={() => setEditing(null)}>x</button>
            </header>
            <p>{editing.description}</p>
            <div className="mihomoFields">
              {(editing.settings || []).filter((field) => editing.id !== "transport-reality" || String(settingsDraft.transport || "xhttp") === "xhttp" || !["xhttp_mode", "xpadding", "xmux_concurrency"].includes(field.key)).map((field) => (
                <label key={field.key}>
                  <span>{field.label}</span>
                  {field.type === "select" ? (
                    <select value={String(settingsDraft[field.key] ?? field.default)} onChange={(event) => setSettingsDraft((current) => ({ ...current, [field.key]: event.target.value }))}>
                      {(field.options || []).map((option) => {
                        const value = typeof option === "string" ? option : option.value;
                        const label = typeof option === "string" ? option : option.label;
                        return <option key={value} value={value}>{label}</option>;
                      })}
                    </select>
                  ) : field.type === "textarea" ? (
                    <textarea rows={6} value={String(settingsDraft[field.key] ?? field.default)} placeholder={"DOMAIN-SUFFIX,example.com,DIRECT\nGEOIP,PRIVATE,DIRECT"} onChange={(event) => setSettingsDraft((current) => ({ ...current, [field.key]: event.target.value }))} />
                  ) : field.type === "boolean" ? (
                    <input type="checkbox" checked={Boolean(settingsDraft[field.key] ?? field.default)} onChange={(event) => setSettingsDraft((current) => ({ ...current, [field.key]: event.target.checked }))} />
                  ) : (
                    <input type={field.type === "number" ? "number" : "text"} min={field.min} max={field.max} value={String(settingsDraft[field.key] ?? field.default)} onChange={(event) => setSettingsDraft((current) => ({ ...current, [field.key]: field.type === "number" ? Number(event.target.value) : event.target.value }))} />
                  )}
                  {field.help && <small>{field.help}</small>}
                </label>
              ))}
            </div>
            <aside>{editing.installed ? "Сохранение настроек перезапустит только этот Mihomo sub-module. Одноимённый Direct-модуль не затрагивается." : "Настройки сохранятся заранее и будут применены при установке sub-module."}</aside>
            <footer><button type="button" className="ghostButton" onClick={() => setEditing(null)}>Отмена</button><button type="submit" className="primaryButton" disabled={busy === `settings:${editing.id}`}>Сохранить</button></footer>
          </form>
        </div>,
        document.body,
      )}

      {profileDialog && (
        <div className="mihomoDialogBackdrop mihomoProfileBackdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setProfileDialog(null);
        }}>
          <form className={`mihomoDialog mihomoProfileDialog is-step-${profileStep}`} onSubmit={saveProfile}>
            <header>
              <div className="mihomoProfileHeading"><p className="eyebrow">MIHOMO PROFILE</p><h2>{profileDialog === "new" ? "Новый профиль" : "Настройка профиля"}</h2><label><span>Название</span><input value={profileName} onChange={(event) => setProfileName(event.target.value)} required maxLength={80} placeholder="Название профиля" /></label></div>
              <button type="button" className="iconButton" onClick={() => setProfileDialog(null)}>x</button>
            </header>
            <div className="mihomoProfileWorkspace">
              <aside className="mihomoProfileRail">
                <header><small>УРОВЕНЬ НАСТРОЕК</small><b>Общий пул и устройства</b></header>
                <section className="mihomoDeviceBuilder">
                  <div className="mihomoCommonSettings">{profileDevices.filter((device) => device.scope === "common").slice(0, 1).map((device) => <button key={device.id} type="button" className={activeDeviceId === device.id ? "active" : ""} onClick={() => { setProfileStrategyTouched(Boolean(device.routing?.strategy)); setActiveDeviceId(device.id); }}><i>ALL</i><span><b>Общий пул</b><small>{profileConnections.filter((connection) => connection.device_id === device.id).length} подключений · {profileDirectRules.filter((rule) => Boolean(device.routing?.[rule.key])).length} правил</small></span></button>)}</div>
                  <header><div><b>HWID-устройства</b><small>Регистрируются автоматически.</small></div></header>
                  <div>{profileDevices.filter((device) => device.scope !== "common").map((device) => <button key={device.id} type="button" className={activeDeviceId === device.id ? "active" : ""} onClick={() => { setProfileStrategyTouched(Boolean(device.routing?.strategy)); setActiveDeviceId(device.id); }}><i>{devicePlatformMeta(device).code}</i><span><input aria-label={`Название устройства ${device.name}`} value={device.name} maxLength={80} onClick={(event) => event.stopPropagation()} onChange={(event) => setProfileDevices((current) => current.map((item) => item.id === device.id ? { ...item, name: event.target.value } : item))} /><small>{profileConnections.filter((connection) => connection.device_id === device.id).length} подключений · {deviceSystemLabel(device)}</small></span><em title="Удалить устройство" onClick={(event) => { event.stopPropagation(); const next = profileDevices.filter((item) => item.id !== device.id); setProfileDevices(next); setProfileConnections((current) => current.filter((connection) => connection.device_id !== device.id)); if (activeDeviceId === device.id) setActiveDeviceId(next.find((item) => item.scope === "common")?.id || next[0].id); }}>×</em></button>)}</div>
                  {!profileDevices.some((device) => device.scope !== "common") && <p className="mihomoConnectionEmpty">Нет зарегистрированных устройств.</p>}
                </section>
              </aside>
              <main ref={profileCanvasRef} className="mihomoProfileCanvas">
            <section className="mihomoProfileEditorHead"><div><small>ВЫБРАННЫЙ УРОВЕНЬ</small><b>{profileDevices.find((device) => device.id === activeDeviceId)?.scope === "common" ? "Общий пул" : profileDevices.find((device) => device.id === activeDeviceId)?.name}</b></div><nav aria-label="Раздел настроек"><button type="button" className={profileStep === 2 ? "is-active" : ""} onClick={() => setProfileStep(2)}>Маршрутизация</button><button type="button" className={profileStep === 3 ? "is-active" : ""} onClick={() => setProfileStep(3)}>Подключения <i>{profileConnections.filter((connection) => connection.device_id === activeDeviceId).length}</i></button></nav></section>
            <section className="mihomoProfileStrategy"><header><div><b>Стратегия устройства</b><small>Отдельная группа GATE.312 для YAML выбранного устройства.</small></div><span>{profileStrategies.find((item) => item.value === String(activeProfileRouting.strategy || ""))?.title}</span></header><div>{profileStrategies.map((strategy) => { const selected = String(activeProfileRouting.strategy || "") === strategy.value; return <button key={strategy.value || "inherit"} type="button" className={selected ? "is-selected" : ""} onClick={() => setProfileStrategy(strategy.value)}><i>{strategy.code}</i><span><b>{strategy.title}</b><small>{strategy.text}</small></span></button>; })}</div></section>
            <section className="mihomoProfileRules"><header><div><b>Правила устройства</b><small>Применяются только к подписке и YAML выбранного устройства.</small></div><span>{profileDirectRules.filter((rule) => Boolean(activeProfileRouting[rule.key])).length} из {profileDirectRules.length}</span></header><div>{profileDirectRules.map((rule) => <label key={rule.key} className={`mihomoProfileRuleSwitch${Boolean(activeProfileRouting[rule.key]) ? " is-enabled" : ""}`}><span><b>{rule.title}</b><small>{rule.text}</small></span><input type="checkbox" checked={Boolean(activeProfileRouting[rule.key])} onChange={(event) => toggleProfileRule(rule.key, event.target.checked)} /></label>)}</div></section>
            <section className="mihomoPresetPicker">
              <header><div><b>Пресет для {profileDevices.find((device) => device.id === activeDeviceId)?.name || "устройства"}</b><small>Пресет заменит подключения только выбранного устройства. Остальные устройства профиля не изменятся.</small></div><button type="button" onClick={() => { setProfileDialog(null); setView("routing"); }}>Настройки пресетов</button></header>
              <div>{profilePresets.map((preset) => {
                const needsCdn = preset.components.some((item) => item.cdn);
                const needsTls = preset.components.some((item) => item.tls);
                const missingCdn = needsCdn && !String(routingPolicy?.values.preset_cdn_domain || "").trim();
                const missingTls = needsTls && !String(routingPolicy?.values.preset_tls_domain || "").trim();
                const missingModules = preset.components.filter((definition) => !modules.some((item) => item.id === definition.id && item.installed));
                const unavailable = missingCdn || missingTls || missingModules.length > 0;
                return <button key={preset.id} type="button" disabled={unavailable} onClick={() => applyProfilePreset(preset)}><b>{preset.name}</b><small>{missingModules.length ? `Сначала установите: ${missingModules.map((item) => item.id.replace("transport-", "")).join(", ")}` : missingCdn && missingTls ? "Укажите CDN- и TLS-домены в Настройках" : missingCdn ? "Укажите CDN-домен в Настройках" : missingTls ? "Укажите TLS-домен в Настройках" : preset.description}</small></button>;
              })}</div>
            </section>
            <section className="mihomoConnectionBuilder">
              <header><div><b>Подключения устройства</b><small>Каналы попадут только в подписку выбранного устройства.</small></div><span className="mihomoStrategyBadge">{profileStrategies.find((item) => item.value === String(activeProfileRouting.strategy || ""))?.code} · {profileStrategies.find((item) => item.value === String(activeProfileRouting.strategy || ""))?.title}</span></header>
              <div className="mihomoConnectionAdd">
                {installedChannels.flatMap((module) => {
                  if (module.id === "transport-reality") return [
                    <button key="vless-direct" type="button" onClick={() => addProfileConnection(module, "direct")}>+ VLESS</button>,
                    <button key="vless-tls" type="button" disabled={!String(routingPolicy?.values.preset_tls_domain || "").trim()} title={!String(routingPolicy?.values.preset_tls_domain || "").trim() ? "Сначала укажите прямой TLS-домен в Настройках" : undefined} onClick={() => addProfileConnection(module, "tls")}>+ VLESS TLS</button>,
                    <button key="vless-cdn" type="button" disabled={!String(routingPolicy?.values.preset_cdn_domain || "").trim()} title={!String(routingPolicy?.values.preset_cdn_domain || "").trim() ? "Сначала укажите CDN-домен в маршрутизации" : undefined} onClick={() => addProfileConnection(module, "cdn")}>+ VLESS CDN</button>,
                  ];
                  const singletonUsed = profileConnections.some((item) => item.device_id === activeDeviceId && item.component === module.id);
                  return [<button key={module.id} type="button" disabled={singletonUsed} onClick={() => addProfileConnection(module)}>+ {module.name}</button>];
                })}
              </div>
              <div className="mihomoConnectionList">
                {profileConnections.filter((connection) => connection.device_id === activeDeviceId).map((connection, index) => {
                  const protocolModule = modules.find((item) => item.id === connection.component);
                  const schema = protocolModule?.connection_settings || [];
                  const vlessRoute = connection.component === "transport-reality" ? String(connection.settings.route_mode || (connection.settings.cdn_enabled ? "both" : "direct")) : "";
                  return <details key={connection.id} className={`mihomoConnectionCard${vlessRoute ? ` is-vless-${vlessRoute}` : ""}`}>
                    <summary>
                      <span className={`protocol-${connection.component}`}>{channelShort[connection.component] || "CH"}</span>
                      <div><b>{vlessRoute === "cdn" ? "VLESS CDN" : vlessRoute === "tls" ? "VLESS TLS" : vlessRoute === "direct" ? "VLESS REALITY" : vlessRoute === "both" ? "VLESS + CDN · прежний формат" : protocolModule?.name || connection.component}</b><small>{vlessRoute === "cdn" ? "Через CDN-домен" : vlessRoute === "tls" ? "Прямой домен с TLS" : vlessRoute === "direct" ? "Прямое REALITY-подключение" : vlessRoute === "both" ? "Можно заменить двумя независимыми подключениями" : `Подключение ${index + 1}`}</small></div>
                      <span className="mihomoConnectionChevron">›</span>
                    </summary>
                    <button type="button" className="dangerButton mihomoConnectionDelete" onClick={() => setProfileConnections((current) => current.filter((item) => item.id !== connection.id))}>Удалить подключение</button>
                    <label><span>Название в профиле</span><input value={connection.name} maxLength={80} onChange={(event) => updateProfileConnection(connection.id, { name: event.target.value })} /></label>
                    <div className="mihomoConnectionFields">
                      {schema.filter((field) => {
                        if (field.key === "route_mode" || field.key === "cdn_enabled") return false;
                        if (connection.component === "transport-reality" && vlessRoute === "cdn" && ["port", "target", "transport", "transport_path", "xhttp_mode", "xpadding", "xmux_concurrency"].includes(field.key)) return false;
                        if (connection.component === "transport-reality" && vlessRoute === "direct" && ["cdn_domain", "cdn_transport", "cdn_xhttp_mode"].includes(field.key)) return false;
                        if (connection.component === "transport-reality" && vlessRoute !== "tls" && ["tls_domain", "tls_transport", "tls_xhttp_mode"].includes(field.key)) return false;
                        if (connection.component === "transport-reality" && vlessRoute === "tls" && !["route_mode", "tls_domain", "tls_transport", "tls_xhttp_mode"].includes(field.key)) return false;
                        if (["xhttp_mode", "xpadding", "xmux_concurrency"].includes(field.key)) return connection.settings.transport === "xhttp";
                        if (["cdn_domain", "cdn_transport"].includes(field.key)) return Boolean(connection.settings.cdn_enabled);
                        if (field.key === "cdn_xhttp_mode") return Boolean(connection.settings.cdn_enabled) && connection.settings.cdn_transport === "xhttp";
                        return true;
                      }).map((field) => <label key={field.key} className={field.type === "boolean" ? "is-toggle" : ""}>
                        <span>{field.label}</span>
                        {field.type === "select" ? <select value={String(connection.settings[field.key] ?? field.default)} onChange={(event) => updateConnectionSetting(connection.id, field.key, event.target.value)}>
                          {(field.options || []).map((option) => { const value = typeof option === "string" ? option : option.value; const label = typeof option === "string" ? option : option.label; return <option key={value} value={value}>{label}</option>; })}
                        </select> : field.type === "boolean" ? <input type="checkbox" checked={Boolean(connection.settings[field.key] ?? field.default)} onChange={(event) => updateConnectionSetting(connection.id, field.key, event.target.checked)} />
                          : <input type={field.type === "number" ? "number" : "text"} min={field.min} max={field.max} value={String(connection.settings[field.key] ?? field.default)} onChange={(event) => updateConnectionSetting(connection.id, field.key, field.type === "number" ? Number(event.target.value) : event.target.value)} />}
                        {field.help && <small>{field.help}</small>}
                      </label>)}
                    </div>
                  </details>;
                })}
                {!profileConnections.some((connection) => connection.device_id === activeDeviceId) && <p className="mihomoConnectionEmpty">Добавьте хотя бы одно подключение для выбранного устройства.</p>}
              </div>
            </section>
                <aside className="mihomoProfileTechnicalNote">Компонент устанавливает ядро протокола один раз. Каждая карточка создаёт независимые параметры и credential только для этого профиля.</aside>
              </main>
            </div>
            <footer><button type="button" className="ghostButton" onClick={() => setProfileDialog(null)}>Отмена</button><span>{profileDevices.some((device) => !profileConnections.some((connection) => connection.device_id === device.id)) ? "Для каждого уровня нужно хотя бы одно подключение" : "Изменения применятся после сохранения"}</span><button type="submit" className="primaryButton" disabled={busy === "profile" || !profileName.trim() || profileDevices.some((device) => !device.name.trim() || !profileConnections.some((connection) => connection.device_id === device.id))}>{profileDialog === "new" ? "Создать профиль" : "Сохранить изменения"}</button></footer>
          </form>
        </div>
      )}
      {createdProfile && <div className="mihomoDialogBackdrop"><div className="mihomoDialog mihomoCreatedProfile"><header><div><p className="eyebrow">PROFILE READY</p><h2>Профиль готов</h2></div><button className="iconButton" onClick={() => setCreatedProfile(null)}>x</button></header><p>Одна ссылка работает для всего профиля. Клиенты с HWID появятся отдельными устройствами, остальные получат общий набор правил.</p><div className="mihomoReadySummary"><b>{createdProfile.name}</b><span>{createdProfile.connections.length} подключений · единая подписка · {profileDirectRules.filter((rule) => Boolean(createdProfile.routing?.[rule.key])).length} правил</span></div><div className="mihomoReadyDevices">{readyDevices.map((device) => <article key={device.id}><Image src={device.qr} alt={`QR подписки ${device.name}`} width={148} height={148} unoptimized /><div><b>{device.name}</b><small>Единая постоянная подписка Mihomo</small><code>{device.subscription}</code><nav><button className="primaryButton" onClick={() => void navigator.clipboard.writeText(device.subscription)}>Скопировать ссылку</button><button className="ghostButton" onClick={() => void downloadConfig(createdProfile)}>Скачать общий YAML</button></nav></div></article>)}{!readyDevices.length && <div className="mihomoHint">Подготавливаем QR-код подписки…</div>}</div><footer><button className="primaryButton" onClick={() => setCreatedProfile(null)}>Готово</button></footer></div></div>}
      {presetDialog && <div className="mihomoDialogBackdrop mihomoPresetBackdrop">
        <form className="mihomoDialog mihomoPresetDialog" onSubmit={savePresetSettings}>
          <header>
            <div><p className="eyebrow">PROFILE BLUEPRINTS</p><h2>Пресеты Mihomo</h2><small>Готовые схемы подключений для новых устройств и профилей</small></div>
            <div className="mihomoPresetHeaderStats"><span><b>{presetDraft.length}</b> пресета</span><span><b>{presetDraft.reduce((sum, item) => sum + item.components.length, 0)}</b> каналов</span></div>
            <button type="button" className="iconButton" aria-label="Закрыть" onClick={() => setPresetDialog(false)}>×</button>
          </header>
          <div className="mihomoPresetIntro"><span>01</span><p><b>Каждый пресет — независимый шаблон.</b><small>Название отображается при создании профиля. Стратегия управляет выбором канала, а список ниже определяет состав подключений.</small></p></div>
          <div className="mihomoPresetEditor">
            <aside className="mihomoPresetRail">
              <header><b>Пресеты</b><small>Выберите шаблон</small></header>
              <div>{presetDraft.map((preset, index) => <button type="button" key={preset.id} className={presetEditorIndex === index ? "is-active" : ""} onClick={() => setPresetEditorIndex(index)}><span>{String(index + 1).padStart(2, "0")}</span><p><b>{preset.name}</b><small>{preset.components.length} каналов · {preset.strategy === "url-test" ? "по задержке" : preset.strategy === "select" ? "вручную" : "резерв"}</small></p><i>›</i></button>)}</div>
              <button type="button" className="mihomoPresetRailAdd" onClick={addPresetDraft} disabled={presetDraft.length >= 12}>+ Добавить пресет</button>
            </aside>
            {presetDraft[presetEditorIndex] && ((preset, index) => <article key={preset.id} className="mihomoPresetCard mihomoPresetCanvas">
              <header>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <label><small>Название пресета</small><input value={preset.name} onChange={(event) => setPresetDraft((current) => current.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} /></label>
                <label><small>Логика переключения</small><select value={preset.strategy} onChange={(event) => setPresetDraft((current) => current.map((item, i) => i === index ? { ...item, strategy: event.target.value as ProfilePreset["strategy"] } : item))}><option value="fallback">Резервирование по порядку</option><option value="url-test">Лучший по задержке</option><option value="select">Ручной выбор</option></select></label>
                <em>{preset.components.length} выбрано</em>
                <button type="button" className="mihomoPresetDelete" aria-label={`Удалить пресет ${preset.name}`} title={presetDraft.length <= 1 ? "Нужен хотя бы один пресет" : "Удалить пресет"} disabled={presetDraft.length <= 1} onClick={() => removePresetDraft(index)}>×</button>
              </header>
              <label className="mihomoPresetDescription"><small>Описание</small><input value={preset.description} placeholder="Когда использовать этот пресет" onChange={(event) => setPresetDraft((current) => current.map((item, i) => i === index ? { ...item, description: event.target.value } : item))} /></label>
              <div className="mihomoPresetGroups">
                {presetOptionGroups.map((group) => <section key={group.id}>
                  <header><b>{group.title}</b><small>{group.note}</small></header>
                  <div>{group.options.map((option) => {
                    const selected = preset.components.some((item) => item.id === option.id && Boolean(item.cdn) === Boolean(option.cdn) && Boolean(item.tls) === Boolean(option.tls) && String(item.transport || "") === String(option.transport || ""));
                    return <label key={`${option.id}-${option.cdn ? "cdn" : option.tls ? "tls" : "direct"}-${option.transport || "default"}`} className={selected ? "is-selected" : ""}><input type="checkbox" checked={selected} onChange={(event) => togglePresetComponent(index, option, event.target.checked)} /><span>{option.label}</span><i>{selected ? "ON" : "OFF"}</i></label>;
                  })}</div>
                </section>)}
              </div>
            </article>)(presetDraft[presetEditorIndex], presetEditorIndex)}
          </div>
          <footer><p>{presetDraft.some((item) => !item.components.length) ? "В каждом пресете нужен хотя бы один канал" : "Изменения применятся только к новым конфигурациям"}</p><button type="button" className="ghostButton" onClick={() => setPresetDialog(false)}>Отмена</button><button type="submit" className="primaryButton" disabled={busy === "presets" || presetDraft.some((item) => !item.components.length)}>Сохранить пресеты</button></footer>
        </form>
      </div>}
    </section>
  );
}

function Tab({ id, current, onSelect, badge, children }: { id: View; current: View; onSelect: (id: View) => void; badge?: number; children: React.ReactNode }) {
  return <button type="button" className={current === id ? "active" : ""} aria-current={current === id ? "page" : undefined} onClick={() => onSelect(id)}><b>{children}</b>{badge !== undefined && <em>{badge}</em>}</button>;
}

function HeroFact({ label, value, note, wide = false }: { label: string; value: string; note: string; wide?: boolean }) {
  return <div className={wide ? "wide" : ""}><small>{label}</small><strong>{value}</strong><span>{note}</span></div>;
}

function ModuleCatalog({ title, description, modules, busy, onToggle, onUpdate, onSettings }: { title: string; description: string; modules: Module[]; busy: string; onToggle: (module: Module) => void; onUpdate: (module: Module) => void; onSettings: (module: Module) => void }) {
  return (
    <article className="mihomoWorkspacePanel mihomoCatalogPanel">
      <header className="mihomoSectionHead">
        <div><p className="eyebrow">MIHOMO SUB-MODULES</p><h2>{title}</h2><p>{description}</p></div>
        <span className="mihomoCatalogCount">{modules.filter((item) => item.installed).length} / {modules.length} установлено</span>
      </header>
      <div className="mihomoModuleCatalog mihomoModuleCatalogV2">
        {modules.map((module) => (
          <article key={module.id} className={module.installed ? "is-installed" : ""}>
            <header><span className={`mihomoModuleCode protocol-${module.id}`}>{channelShort[module.id] || (module.category === "dns" ? "DNS" : "RT")}</span><div><b>{module.name}</b><small>{module.description}</small></div><i className={module.active ? "is-online" : module.installed ? "is-ready" : ""} /></header>
            <div className="mihomoModuleCapabilities">{(moduleCapabilities[module.id] || []).map((capability) => <span key={capability}>{capability}</span>)}</div>
            <dl><div><dt>Состояние</dt><dd>{module.active ? "Работает" : module.installed ? "Готов" : "Не установлен"}</dd></div><div><dt>Версия</dt><dd>{module.installed_version ? formatModuleVersion(module.installed_version) : "—"}</dd></div><div><dt>Сервис</dt><dd>{module.service || "Внутренний"}</dd></div></dl>
            <footer className="mihomoModuleActions">
              <button className="ghostButton" onClick={() => onSettings(module)}>Настройки</button>
              {module.installed && module.update_available && (
                <button className={`ghostButton${module.update_breaking ? " breaking" : ""}`} disabled={Boolean(busy)} onClick={() => void onUpdate(module)}>{busy === `update:${module.id}` ? "Обновление…" : "Обновить"}</button>
              )}
              <button className={module.installed ? "dangerButton" : "primaryButton"} disabled={module.installable === false || Boolean(busy)} onClick={() => void onToggle(module)}>{module.installable === false ? "В разработке" : busy === module.id ? "Выполняется…" : module.installed ? "Удалить" : "Установить"}</button>
            </footer>
          </article>
        ))}
        {!modules.length && <Empty title="Каталог пуст" text="Mihomo Manager не получил manifest внутренних модулей." />}
      </div>
    </article>
  );
}

function Empty({ title, text }: { title: string; text: string }) {
  return <div className="mihomoEmpty"><p><b>{title}</b><small>{text}</small></p></div>;
}
