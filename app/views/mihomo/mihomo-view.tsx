"use client";

import { formatModuleVersion } from "../../lib/format-version";
import { bytes, duration } from "../../lib/control-plane-ui";
import QRCode from "qrcode";
import Image from "next/image";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

type View = "overview" | "profiles" | "channels" | "dns" | "routing";
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
type ProfileDevice = { id: string; name: string };

type PolicySettings = {
  schema: SettingField[];
  values: Record<string, string | number | boolean>;
  presets?: ProfilePreset[];
  rule_lists?: Array<{ id: string; key: string; title: string; description: string; default_rules: string; using_default: boolean }>;
};

type ProfilePreset = { id: string; name: string; description: string; strategy: "fallback" | "url-test" | "select"; components: Array<{ id: string; cdn?: boolean; tls?: boolean; transport?: string; label?: string }> };
const presetConnectionOptions: ProfilePreset["components"] = [
  { id: "transport-reality", transport: "xhttp", label: "VLESS · XHTTP" },
  { id: "transport-reality", transport: "raw", label: "VLESS · RAW" },
  { id: "transport-reality", transport: "grpc", label: "VLESS · gRPC" },
  { id: "transport-reality", cdn: true, transport: "xhttp", label: "VLESS CDN · XHTTP" },
  { id: "transport-reality", cdn: true, transport: "websocket", label: "VLESS CDN · WS" },
  { id: "transport-reality", cdn: true, transport: "httpupgrade", label: "VLESS CDN · HTTPUpgrade" },
  { id: "transport-reality", cdn: true, transport: "grpc", label: "VLESS CDN · gRPC" },
  { id: "transport-awg", label: "AWG" }, { id: "transport-wg", label: "WG" }, { id: "transport-shadowsocks", label: "SS" },
];
type ProfileStats = { summary: { configured: number; active: number; rx_bytes: number; tx_bytes: number; last_handshake_age_s: number | null }; connections: Record<string, { active?: boolean; endpoint?: string | null; active_connections?: number; rx_bytes?: number; tx_bytes?: number; handshake_age_s?: number | null }> };

const channelShort: Record<string, string> = {
  "transport-awg": "AW",
  "transport-wg": "WG",
  "transport-reality": "VL",
  "transport-shadowsocks": "SS",
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
];

const profileDirectRules = [
  { key: "block_ads", code: "AD", title: "Блокировка рекламы", text: "Реклама и трекеры." },
  { key: "direct_ru_sites", code: "RU", title: "Российские сайты", text: "Домены и IP России." },
  { key: "direct_ru_banks", code: "BANK", title: "Банки и платежи", text: "Банки и платёжные сервисы." },
  { key: "direct_ru_marketplaces", code: "SHOP", title: "Магазины", text: "Магазины и маркетплейсы." },
  { key: "direct_games_enabled", code: "GAME", title: "Игры полностью", text: "Выбранные игры без VPN." },
  { key: "direct_games_udp_enabled", code: "UDP", title: "UDP напрямую", text: "Весь UDP без VPN." },
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
  const [activeRuleList, setActiveRuleList] = useState("direct_ru_sites");
  const routingDirtyRef = useRef(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<Module | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<Record<string, string | number | boolean>>({});
  const [profileDialog, setProfileDialog] = useState<Profile | "new" | null>(null);
  const [profileStep, setProfileStep] = useState(1);
  const [profileName, setProfileName] = useState("");
  const [profileConnections, setProfileConnections] = useState<ProfileConnection[]>([]);
  const [profileRouting, setProfileRouting] = useState<Record<string, string | number | boolean>>({});
  const [profileDevices, setProfileDevices] = useState<ProfileDevice[]>([{ id: "device-1", name: "Основное устройство" }]);
  const [activeDeviceId, setActiveDeviceId] = useState("device-1");
  const [profileStats, setProfileStats] = useState<Record<string, ProfileStats>>({});
  const [createdProfile, setCreatedProfile] = useState<Profile | null>(null);
  const [readyDevices, setReadyDevices] = useState<ReadyDevice[]>([]);
  const [presetDialog, setPresetDialog] = useState(false);
  const [presetDraft, setPresetDraft] = useState<ProfilePreset[]>([]);

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
      if (!routingDirtyRef.current) setRoutingDraft({ ...(nextRouting as PolicySettings).values });
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
    const devices = createdProfile.devices?.length ? createdProfile.devices : [{ id: "device-1", name: "Устройство" }];
    void Promise.all(devices.map(async (device) => {
      const result = await request(`/mihomo/profiles/${createdProfile.id}/subscription?device_id=${encodeURIComponent(device.id)}`) as { path: string };
      const subscription = new URL(result.path, window.location.origin).toString();
      const qr = await QRCode.toDataURL(subscription, { errorCorrectionLevel: "M", margin: 2, width: 360 });
      return { ...device, subscription, qr };
    })).then((items) => { if (!cancelled) setReadyDevices(items); }).catch((cause) => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "Не удалось подготовить QR-коды подписок");
    });
    return () => { cancelled = true; };
  }, [createdProfile, request]);

  function updateRoutingDraft(key: string, value: string | number | boolean) {
    setRoutingDraft((current) => ({ ...current, [key]: value }));
    routingDirtyRef.current = true;
    setRoutingDirty(true);
  }

  function toggleDirectGame(gameId: string) {
    const selected = new Set(String(routingDraft.direct_games || "").split(",").map((value) => value.trim()).filter(Boolean));
    if (selected.has(gameId)) selected.delete(gameId); else selected.add(gameId);
    updateRoutingDraft("direct_games", [...selected].join(","));
  }

  function toggleProfileRule(key: string, checked: boolean) {
    setProfileRouting((current) => ({ ...current, [key]: checked }));
  }

  async function saveRoutingWorkspace(event: FormEvent) {
    event.preventDefault();
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
    setProfileStep(1);
    setProfileDialog("new");
    setProfileName("");
    setProfileConnections([]);
    setProfileRouting({});
    setProfileDevices([{ id: "device-1", name: "Основное устройство" }]);
    setActiveDeviceId("device-1");
  }

  function editProfile(profile: Profile) {
    setProfileStep(1);
    setProfileDialog(profile);
    setProfileName(profile.name);
    setProfileConnections((profile.connections || []).map((connection) => ({
      ...connection,
      settings: connection.component === "transport-reality"
        ? { ...connection.settings, route_mode: connection.settings.route_mode || (connection.settings.cdn_enabled ? "both" : "direct") }
        : { ...connection.settings },
    })));
    setProfileRouting({ ...(profile.routing || {}) });
    const devices = profile.devices?.length ? profile.devices : [{ id: "device-1", name: "Основное устройство" }];
    setProfileDevices(devices);
    setActiveDeviceId(devices[0].id);
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
    setProfileRouting((current) => ({ ...current, strategy: preset.strategy }));
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
          body: JSON.stringify({ name: profileName, devices: profileDevices, connections: profileConnections, routing: profileRouting }),
        }) as Profile;
        setReadyDevices([]);
        setCreatedProfile(created);
      } else if (profileDialog) {
        const updated = await request(`/mihomo/profiles/${profileDialog.id}`, {
          method: "PATCH",
          body: JSON.stringify({ name: profileName, devices: profileDevices, connections: profileConnections, routing: profileRouting }),
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

  async function copySubscription(profile: Profile, device?: ProfileDevice) {
    setBusy(`subscription:${profile.id}:${device?.id || "default"}`);
    setError("");
    try {
      const result = await request(`/mihomo/profiles/${profile.id}/subscription${device ? `?device_id=${encodeURIComponent(device.id)}` : ""}`) as { path: string };
      await navigator.clipboard.writeText(new URL(result.path, window.location.origin).toString());
      setNotice(`Ссылка подписки для «${device?.name || profile.name}» скопирована. Добавьте её в клиент один раз — дальнейшие изменения придут при обновлении подписки.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось получить ссылку подписки");
    } finally {
      setBusy("");
    }
  }

  function openPresetSettings() {
    setPresetDraft((routingPolicy?.presets || []).map((preset) => ({ ...preset, components: preset.components.map((item) => ({ ...item })) })));
    setPresetDialog(true);
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

  return (
    <section className="mihomoPage mihomoWorkspace" aria-label="Mihomo Manager">
      <nav className="mihomoHost__tabs mihomoTabs" aria-label="Разделы Mihomo">
        <Tab id="overview" current={view} onSelect={setView}>Обзор</Tab>
        <Tab id="profiles" current={view} onSelect={setView} badge={profiles.length}>Профили</Tab>
        <Tab id="channels" current={view} onSelect={setView} badge={installedChannels.length}>Компоненты</Tab>
        <Tab id="dns" current={view} onSelect={setView}>DNS</Tab>
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
            <HeroFact label="COMPONENTS" value={`${status?.channels_installed ?? installedChannels.length}/4`} note={`${status?.channels_in_use?.length || 0} in use`} />
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
        <div className="mihomoOverview">
          <article className="mihomoStatusBoard">
            <header className="mihomoSectionHead">
              <div>
                <p className="eyebrow">MIHOMO WORKSPACE</p>
                <h2>Состояние внутреннего контура</h2>
                <p>Mihomo изолирован от одноимённых Direct-модулей. Здесь отображаются только его собственные sub-modules и профили.</p>
              </div>
              <span className={status?.active ? "mihomoPill is-online" : "mihomoPill"}>
                <i /> {status?.active ? "Runtime работает" : "Нет runtime"}
              </span>
            </header>

            <div className="mihomoTopology">
              <div className="mihomoTopologyNode core">
                <small>CORE</small>
                <strong>Mihomo {status?.core_version || ""}</strong>
                <span>{status?.endpoint || "endpoint определяется"}</span>
              </div>
              <div className="mihomoTopologyArrow" aria-hidden="true" />
              <div className="mihomoTopologyNode">
                <small>PROFILES</small>
                <strong>{status?.profiles ?? profiles.length}</strong>
                <span>{status?.profiles_in_use || 0} используются</span>
              </div>
              <div className="mihomoTopologyArrow" aria-hidden="true" />
              <div className="mihomoTopologyNode">
                <small>COMPONENTS</small>
                <strong>{status?.channels_installed ?? installedChannels.length} / 4</strong>
                <span>{status?.channels_in_use?.length || 0} используются</span>
              </div>
              <div className="mihomoTopologyArrow" aria-hidden="true" />
              <div className="mihomoTopologyNode policy">
                <small>POLICY</small>
                <strong>{policiesReady ? "ACTIVE" : "WAIT"}</strong>
                <span>DNS + routing активируются с первым компонентом</span>
              </div>
            </div>
          </article>

          <article className="mihomoChannelsBoard">
            <header className="mihomoSectionHead compact">
              <div>
                <p className="eyebrow">PROTOCOL COMPONENTS</p>
                <h2>Компоненты Mihomo</h2>
                <p>Здесь устанавливаются ядра протоколов. Конкретные подключения создаются внутри профиля.</p>
              </div>
              <button className="ghostButton" type="button" onClick={() => setView("channels")}>Каталог компонентов</button>
            </header>
            <div className="mihomoChannelGrid">
              {transportModules.map((module) => (
                <div key={module.id} className={`mihomoChannelCard ${module.installed ? "installed" : "empty"} ${module.active ? "active" : ""}`}>
                  <span className="mihomoChannelCode">{channelShort[module.id] || "CH"}</span>
                  <div>
                    <strong>{module.name}</strong>
                    <small>{module.installed ? module.service || "внутренний модуль установлен" : "не установлен"}</small>
                  </div>
                  <em>{module.active ? "ACTIVE" : module.installed ? "READY" : "EMPTY"}</em>
                </div>
              ))}
            </div>
          </article>

          <article className="mihomoQuickState">
            <div>
              <small>DNS MODULE</small>
              <strong>{policiesReady ? "Активен" : "Ожидает компонент"}</strong>
              <span>Общая DNS-политика всех профилей</span>
            </div>
            <div>
              <small>ROUTING MODULE</small>
              <strong>{policiesReady ? "Активна" : "Ожидает компонент"}</strong>
              <span>Базовая стратегия всех профилей</span>
            </div>
            <div>
              <small>PROFILES IN USE</small>
              <strong>{status?.profiles_in_use || 0}</strong>
              <span>{status?.credentials || 0} credentials</span>
            </div>
          </article>
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
            {profiles.map((profile) => { const devices = profile.devices?.length ? profile.devices : [{ id: "device-1", name: "Устройство" }]; const stats = profileStats[profile.id]?.summary; const ruleCount = profileDirectRules.filter((rule) => Boolean(profile.routing?.[rule.key])).length; return (
              <section className="mihomoProfileCard" key={profile.id}>
                <header className="mihomoProfileHeader">
                  <div className="mihomoProfileIdentity"><span className="mihomoProfileIcon">M</span><p><b>{profile.name}</b><small>{devices.length} устройств · {profile.connections.length} подключений</small><em>ID {profile.id} · обновлён {new Date(profile.updated_at || profile.created_at).toLocaleString("ru-RU")}</em></p></div>
                  <div className="mihomoProfileSummary">
                    <div><small>Состояние</small><strong className={stats?.active ? "is-online" : ""}><i />{stats ? `${stats.active} из ${stats.configured}` : "—"}</strong><span>активных каналов</span></div>
                    <div><small>Трафик</small><strong>↓ {bytes(stats?.rx_bytes || 0)}</strong><span>↑ {bytes(stats?.tx_bytes || 0)}</span></div>
                    <div><small>Последняя связь</small><strong>{stats?.last_handshake_age_s != null ? duration(stats.last_handshake_age_s) : "—"}</strong><span>{stats?.last_handshake_age_s != null ? "назад" : "подключений нет"}</span></div>
                  </div>
                  <div className="mihomoRowActions"><button onClick={() => editProfile(profile)}>Настроить</button><button className="dangerButton" onClick={() => void removeProfile(profile)} disabled={busy === `profile:${profile.id}`}>Удалить</button></div>
                </header>
                <div className="mihomoProfileDevices">
                  {devices.map((device, deviceIndex) => {
                    const connections = profile.connections.filter((connection) => (connection.device_id || devices[0].id) === device.id);
                    const connectionStats = connections.map((connection) => profileStats[profile.id]?.connections?.[connection.id]);
                    const onlineCount = connectionStats.filter((item) => Boolean(item?.active || item?.endpoint || Number(item?.active_connections || 0))).length;
                    const deviceRx = connectionStats.reduce((sum, item) => sum + Number(item?.rx_bytes || 0), 0);
                    const deviceTx = connectionStats.reduce((sum, item) => sum + Number(item?.tx_bytes || 0), 0);
                    const deleting = busy === `device:${profile.id}:${device.id}`;
                    return <section key={device.id} className="mihomoProfileDevice">
                      <header className="mihomoDeviceHeader">
                        <div className="mihomoDeviceIdentity"><span>{String(deviceIndex + 1).padStart(2, "0")}</span><p><b>{device.name}</b><small>Отдельная конфигурация и постоянная подписка</small></p></div>
                        <div className="mihomoDeviceTotals"><span><small>КАНАЛЫ</small><b>{onlineCount}/{connections.length}</b></span><span><small>ПРАВИЛА</small><b>{ruleCount}</b></span><span><small>ТРАФИК</small><b>↓ {bytes(deviceRx)} · ↑ {bytes(deviceTx)}</b></span></div>
                        <nav className="mihomoDeviceActions"><button className="primaryButton" onClick={() => void copySubscription(profile, device)} disabled={busy === `subscription:${profile.id}:${device.id}`}>Скопировать подписку</button><button onClick={() => void downloadConfig(profile, device)} disabled={busy === `download:${profile.id}`}>Скачать YAML</button><button onClick={() => void copyConfig(profile, device)} disabled={busy === `config:${profile.id}`}>Копировать YAML</button><button className="dangerButton" onClick={() => void removeProfileDevice(profile, device)} disabled={deleting || devices.length <= 1} title={devices.length <= 1 ? "Нельзя удалить единственное устройство профиля" : "Удалить это устройство и его подключения"}>{deleting ? "Удаление…" : "Удалить устройство"}</button></nav>
                      </header>
                      <div className="mihomoProfileProtocolStats">{connections.map((connection) => { const item = profileStats[profile.id]?.connections?.[connection.id]; const online = Boolean(item?.active || item?.endpoint || Number(item?.active_connections || 0)); return <div key={connection.id}><span className={online ? "online" : ""}>{channelShort[connection.component] || "CH"}<i /></span><p><b>{connection.name}</b><small>Получено {bytes(item?.rx_bytes || 0)} · Отдано {bytes(item?.tx_bytes || 0)}</small>{item?.handshake_age_s != null && <em>Связь {duration(item.handshake_age_s)} назад</em>}</p></div>; })}{!connections.length && <p className="mihomoConnectionEmpty">Для устройства пока нет подключений.</p>}</div>
                    </section>;
                  })}
                </div>
              </section>
            ); })}
            {!profiles.length && <Empty title="Профилей пока нет" text="Установите компонент и соберите первое подключение в профиле." />}
          </div>
          <section className="mihomoClientGuide">
            <header><p className="eyebrow">CLIENT SETUP</p><h3>Настройка и подключение</h3><p>Создайте профиль и устройство, скопируйте ссылку подписки и добавьте её в клиент как удалённый профиль. После изменений обновляйте подписку в приложении.</p></header>
            <ol><li><b>1</b><span>Нажмите «Скопировать подписку» у нужного устройства.</span></li><li><b>2</b><span>В клиенте добавьте профиль по URL и вставьте ссылку.</span></li><li><b>3</b><span>Обновите профиль, выберите канал и включите подключение.</span></li></ol>
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
          description="Установите ядра нужных протоколов. Порты, транспорт, credentials и CDN задаются отдельно для каждого подключения в профиле."
          modules={transportModules}
          busy={busy}
          onToggle={toggleModule}
          onUpdate={updateModule}
          onSettings={openSettings}
        />
      )}

      {view === "dns" && (
        <form className="mihomoDnsWorkspace" onSubmit={saveDnsWorkspace}>
          <header className="mihomoDnsHeader"><div><p className="eyebrow">MIHOMO DNS</p><h2>DNS</h2><p>Настройте разрешение доменов для всех соединений через Mihomo. Эти параметры входят в конфигурации профилей и не меняют системный DNS VPS.</p></div><span className={policiesReady ? "mihomoPill is-online" : "mihomoPill"}><i />{policiesReady ? "АКТИВЕН" : "ОЖИДАНИЕ"}</span></header>
          <section className="mihomoDnsFields">
            {(dnsPolicy?.schema || []).map((field) => <label key={field.key}><span>{field.label}</span>{field.type === "select" ? <select value={String(dnsDraft[field.key] ?? field.default)} onChange={(event) => updateDnsDraft(field.key, event.target.value)}>{(field.options || []).map((option) => { const value = typeof option === "string" ? option : option.value; const label = typeof option === "string" ? option : option.label; return <option key={value} value={value}>{label}</option>; })}</select> : <input type={field.type === "number" ? "number" : "text"} min={field.min} max={field.max} value={String(dnsDraft[field.key] ?? field.default)} onChange={(event) => updateDnsDraft(field.key, field.type === "number" ? Number(event.target.value) : event.target.value)} />}{field.help && <small>{field.help}</small>}</label>)}
          </section>
          <aside className="mihomoDnsNote"><b>Как применяется DNS</b><span>Основной сервер используется первым, резервный — при недоступности или неподходящем ответе. После сохранения обновите подписку на устройствах.</span></aside>
          <footer className="mihomoDnsFooter"><span>{dnsDirty ? "Есть несохранённые изменения" : "Настройки синхронизированы"}</span><button className="primaryButton" type="submit" disabled={!dnsDirty || busy === "settings:dns-private"}>{busy === "settings:dns-private" ? "Сохранение…" : "Сохранить DNS"}</button></footer>
        </form>
      )}

      {view === "routing" && (
        <form className="mihomoRoutingWorkspace" onSubmit={saveRoutingWorkspace}>
          <header className="mihomoRoutingHeader">
            <div><p className="eyebrow">MIHOMO ROUTING STUDIO</p><h2>Маршрутизация</h2><p>Редактируйте готовые наборы правил, игровой каталог и поведение защищённых каналов. Применение каждого набора включается отдельно в профиле.</p></div>
            <span className={policiesReady ? "mihomoPill is-online" : "mihomoPill"}><i />{policiesReady ? "АКТИВНА" : "ОЖИДАНИЕ"}</span>
          </header>

          <section className="mihomoRuleStudio">
            <aside><header><b>Библиотека правил</b><small>Выберите набор для редактирования</small></header>{profileDirectRules.map((item) => { const profileCount = profiles.filter((profile) => Boolean(profile.routing?.[item.key])).length; return <button type="button" key={item.key} className={activeRuleList === item.key ? "is-active" : ""} onClick={() => setActiveRuleList(item.key)}><span>{item.code}</span><p><b>{item.title}</b><small>{item.text}</small></p><i>{profileCount} проф.</i></button>; })}</aside>
            <article>
              {activeRuleList === "direct_games_udp_enabled" ? <>
                <header className="mihomoRuleEditorHead"><div><p className="eyebrow">NETWORK RULE</p><h3>UDP напрямую</h3><p>Весь UDP-трафик профиля направляется напрямую одним правилом, без определения процесса и выбора игры.</p></div><span>1 правило</span></header>
                <div className="mihomoHint">NETWORK,UDP,DIRECT</div>
              </> : activeRuleList === "direct_games_enabled" ? <>
                <header className="mihomoRuleEditorHead"><div><p className="eyebrow">PROCESS RULES</p><h3>Игры полностью без VPN</h3><p>Весь сетевой трафик выбранных игровых процессов направляется напрямую. Каталог не зависит от магазина или лаунчера и может включать ещё не установленные игры.</p></div><span>{String(routingDraft.direct_games || "").split(",").filter(Boolean).length} выбрано</span></header>
                <div className="mihomoGameCatalog">
                  {directGameCatalog.map((game) => { const selected = String(routingDraft.direct_games || "").split(",").includes(game.id); return <button type="button" key={game.id} className={selected ? "is-selected" : ""} aria-pressed={selected} onClick={() => toggleDirectGame(game.id)}><span>{game.code}</span><b>{game.name}</b><i>{selected ? "Напрямую" : "Через VPN"}</i></button>; })}
                </div>
                <label className="mihomoCustomGames"><span><b>Дополнительные процессы</b><small>По одному имени процесса или package name на строку.</small></span><textarea rows={5} value={String(routingDraft.direct_game_processes || "")} placeholder={"mygame.exe\ncom.publisher.game"} onChange={(event) => updateRoutingDraft("direct_game_processes", event.target.value)} /></label>
                <p className="mihomoGamesHint">Для определения процесса нужен TUN-режим. На iPhone сопоставление по приложению может быть недоступно.</p>
              </> : selectedRuleList ? <>
                <header className="mihomoRuleEditorHead"><div><p className="eyebrow">DOMAIN / IP RULES</p><h3>{selectedRuleList.title}</h3><p>{selectedRuleList.description} По одному правилу Mihomo на строку.</p></div><span>{selectedRuleText.split("\n").filter(Boolean).length} правил</span></header>
                <label className="mihomoRuleTextarea"><textarea rows={16} value={selectedRuleText} spellCheck={false} onChange={(event) => updateRoutingDraft(selectedRuleList.key, event.target.value)} /></label>
                <footer className="mihomoRuleEditorActions"><span>{selectedRuleValue === "@default" ? "Используется стандартный список" : "Список изменён вручную"}</span><button type="button" className="ghostButton" disabled={selectedRuleValue === "@default"} onClick={() => updateRoutingDraft(selectedRuleList.key, "@default")}>Восстановить стандартный</button></footer>
              </> : <div className="mihomoHint">Списки правил загружаются…</div>}
            </article>
          </section>

          <section className="mihomoRoutingSection mihomoRoutingBehavior">
            <div className="mihomoRoutingSectionTitle"><div><b>Поведение соединения</b><small>Общие параметры, которые входят во все профили Mihomo.</small></div></div>
            <div className="mihomoRoutingColumns">
              <article><div className="mihomoRoutingSectionTitle"><div><b>Выбор защищённого канала</b><small>Как Mihomo выбирает соединение внутри профиля.</small></div></div><div className="mihomoRoutingFields">
                <label><span>Режим</span><select value={String(routingDraft.mode || "rule")} onChange={(event) => updateRoutingDraft("mode", event.target.value)}><option value="rule">По правилам</option><option value="global">Весь трафик через VPN</option></select></label>
                <label><span>Стратегия</span><select value={String(routingDraft.strategy || "fallback")} onChange={(event) => updateRoutingDraft("strategy", event.target.value)}><option value="fallback">Надёжный канал + резерв</option><option value="url-test">Самый быстрый канал</option><option value="select">Выбирать вручную</option></select></label>
                <label><span>Адрес проверки</span><input value={String(routingDraft.test_url || "")} onChange={(event) => updateRoutingDraft("test_url", event.target.value)} /></label>
                <label><span>Проверять каждые, сек.</span><input type="number" min={30} max={3600} value={Number(routingDraft.interval || 180)} onChange={(event) => updateRoutingDraft("interval", Number(event.target.value))} /></label>
              </div></article>
              <article><div className="mihomoRoutingSectionTitle"><div><b>Новые профили</b><small>Транспорты и домены для быстрых пресетов.</small></div><button type="button" className="ghostButton" onClick={openPresetSettings}>Пресеты</button></div><div className="mihomoRoutingFields">
                <label><span>Основной транспорт</span><select value={String(routingDraft.preset_primary || "transport-reality")} onChange={(event) => updateRoutingDraft("preset_primary", event.target.value)}>{[{v:"transport-reality",l:"VLESS"},{v:"transport-awg",l:"AmneziaWG"},{v:"transport-wg",l:"WireGuard"},{v:"transport-shadowsocks",l:"Shadowsocks"}].map((item) => <option key={item.v} value={item.v}>{item.l}</option>)}</select></label>
                <label><span>Резервный транспорт</span><select value={String(routingDraft.preset_fallback || "transport-awg")} onChange={(event) => updateRoutingDraft("preset_fallback", event.target.value)}>{[{v:"transport-awg",l:"AmneziaWG"},{v:"transport-reality",l:"VLESS"},{v:"transport-wg",l:"WireGuard"},{v:"transport-shadowsocks",l:"Shadowsocks"}].map((item) => <option key={item.v} value={item.v}>{item.l}</option>)}</select></label>
                <label className="is-wide"><span>CDN-домен</span><input value={String(routingDraft.preset_cdn_domain || "")} onChange={(event) => updateRoutingDraft("preset_cdn_domain", event.target.value)} /></label>
                <label className="is-wide"><span>Прямой TLS-домен</span><input value={String(routingDraft.preset_tls_domain || "")} onChange={(event) => updateRoutingDraft("preset_tls_domain", event.target.value)} /></label>
              </div></article>
            </div>
          </section>

          <details className="mihomoAdvancedRules"><summary><span><b>Дополнительные правила</b><small>Для опытных пользователей</small></span><i>Открыть редактор</i></summary><label><span>По одному правилу Mihomo на строку</span><textarea rows={7} value={String(routingDraft.rules || "")} placeholder={"DOMAIN-SUFFIX,example.com,DIRECT\nDOMAIN,api.example.com,DIRECT"} onChange={(event) => updateRoutingDraft("rules", event.target.value)} /></label></details>
          <footer className="mihomoRoutingFooter"><span>{routingDirty ? "Есть несохранённые изменения" : "Настройки сохранены. После изменения обновите подписку в клиенте."}</span><button className="primaryButton" type="submit" disabled={!routingDirty || busy === "settings:routing-policy"}>{busy === "settings:routing-policy" ? "Сохранение…" : "Сохранить маршрутизацию"}</button></footer>
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
        <div className="mihomoDialogBackdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setProfileDialog(null);
        }}>
          <form className={`mihomoDialog mihomoProfileDialog is-step-${profileStep}`} onSubmit={saveProfile}>
            <header>
              <div><p className="eyebrow">MIHOMO PROFILE</p><h2>{profileDialog === "new" ? "Новый профиль" : "Настройка профиля"}</h2></div>
              <button type="button" className="iconButton" onClick={() => setProfileDialog(null)}>x</button>
            </header>
            <nav className="mihomoProfileSteps" aria-label="Этапы настройки профиля">
              {[{ id: 1, title: "Профиль", note: "Название и правила" }, { id: 2, title: "Устройства", note: "Кому выдаём доступ" }, { id: 3, title: "Подключения", note: "Каналы и параметры" }].map((step) => <button key={step.id} type="button" className={profileStep === step.id ? "is-active" : profileStep > step.id ? "is-done" : ""} disabled={step.id > 1 && !profileName.trim()} onClick={() => setProfileStep(step.id)}><i>{profileStep > step.id ? "✓" : step.id}</i><span><b>{step.title}</b><small>{step.note}</small></span></button>)}
            </nav>
            <label className="mihomoProfileName"><span>Название профиля</span><input value={profileName} onChange={(event) => setProfileName(event.target.value)} required maxLength={80} /></label>
            <section className="mihomoProfileRules"><header><div><b>Правила профиля</b><small>Каждое правило применяется только к этому профилю. Состав списков задаётся в разделе «Настройки».</small></div><span>{profileDirectRules.filter((rule) => Boolean(profileRouting[rule.key])).length} из {profileDirectRules.length}</span></header><div>{profileDirectRules.map((rule) => <label key={rule.key} className={`mihomoProfileRuleSwitch${Boolean(profileRouting[rule.key]) ? " is-enabled" : ""}`}><span><b>{rule.title}</b><small>{rule.text}</small></span><input type="checkbox" checked={Boolean(profileRouting[rule.key])} onChange={(event) => toggleProfileRule(rule.key, event.target.checked)} /></label>)}</div></section>
            <section className="mihomoDeviceBuilder"><header><div><b>Устройства профиля</b><small>У каждого устройства собственные credentials и отдельный YAML.</small></div><button type="button" onClick={() => { const id = `device-${crypto.randomUUID()}`; setProfileDevices((current) => [...current, { id, name: `Устройство ${current.length + 1}` }]); setActiveDeviceId(id); }}>+ Устройство</button></header><div>{profileDevices.map((device) => <button key={device.id} type="button" className={activeDeviceId === device.id ? "active" : ""} onClick={() => setActiveDeviceId(device.id)}><input value={device.name} maxLength={80} onClick={(event) => event.stopPropagation()} onChange={(event) => setProfileDevices((current) => current.map((item) => item.id === device.id ? { ...item, name: event.target.value } : item))} />{profileDevices.length > 1 && <span onClick={(event) => { event.stopPropagation(); const next = profileDevices.filter((item) => item.id !== device.id); setProfileDevices(next); setProfileConnections((current) => current.filter((connection) => connection.device_id !== device.id)); if (activeDeviceId === device.id) setActiveDeviceId(next[0].id); }}>×</span>}</button>)}</div></section>
            <section className="mihomoPresetPicker">
              <header><div><b>Пресет для {profileDevices.find((device) => device.id === activeDeviceId)?.name || "устройства"}</b><small>Пресет заменит подключения только выбранного устройства. Остальные устройства профиля не изменятся.</small></div><button type="button" onClick={() => { setProfileDialog(null); setView("routing"); }}>Настройки пресетов</button></header>
              <div>{profilePresets.map((preset) => {
                const needsCdn = preset.components.some((item) => item.cdn);
                const unavailable = needsCdn && !String(routingPolicy?.values.preset_cdn_domain || "").trim();
                return <button key={preset.id} type="button" disabled={unavailable} onClick={() => applyProfilePreset(preset)}><b>{preset.name}</b><small>{unavailable ? "Укажите CDN-домен в маршрутизации" : preset.description}</small></button>;
              })}</div>
            </section>
            <section className="mihomoConnectionBuilder">
              <header><div><b>Подключения профиля</b><small>Один профиль может содержать несколько VLESS с разными маршрутами и CDN.</small></div></header>
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
                  return <article key={connection.id} className={`mihomoConnectionCard${vlessRoute ? ` is-vless-${vlessRoute}` : ""}`}>
                    <header>
                      <span>{channelShort[connection.component] || "CH"}</span>
                      <div><b>{vlessRoute === "cdn" ? "VLESS CDN" : vlessRoute === "tls" ? "VLESS TLS" : vlessRoute === "direct" ? "VLESS REALITY" : vlessRoute === "both" ? "VLESS + CDN · прежний формат" : protocolModule?.name || connection.component}</b><small>{vlessRoute === "cdn" ? "Через CDN-домен" : vlessRoute === "tls" ? "Прямой домен с TLS" : vlessRoute === "direct" ? "Прямое REALITY-подключение" : vlessRoute === "both" ? "Можно заменить двумя независимыми подключениями" : `Подключение ${index + 1}`}</small></div>
                      <button type="button" className="dangerButton" onClick={() => setProfileConnections((current) => current.filter((item) => item.id !== connection.id))}>Удалить</button>
                    </header>
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
                  </article>;
                })}
                {!profileConnections.some((connection) => connection.device_id === activeDeviceId) && <p className="mihomoConnectionEmpty">Добавьте хотя бы одно подключение для выбранного устройства.</p>}
              </div>
            </section>
            <aside>Компонент устанавливает ядро протокола один раз. Каждая карточка выше создаёт независимые параметры и credential только для этого профиля.</aside>
            <footer><button type="button" className="ghostButton" onClick={() => profileStep === 1 ? setProfileDialog(null) : setProfileStep((current) => current - 1)}>{profileStep === 1 ? "Отмена" : "Назад"}</button>{profileStep < 3 ? <button type="button" className="primaryButton" disabled={!profileName.trim() || profileDevices.some((device) => !device.name.trim())} onClick={() => setProfileStep((current) => current + 1)}>Далее</button> : <button type="submit" className="primaryButton" disabled={busy === "profile" || !profileName.trim() || profileDevices.some((device) => !profileConnections.some((connection) => connection.device_id === device.id))}>{profileDialog === "new" ? "Создать профиль" : "Сохранить изменения"}</button>}</footer>
          </form>
        </div>
      )}
      {createdProfile && <div className="mihomoDialogBackdrop"><div className="mihomoDialog mihomoCreatedProfile"><header><div><p className="eyebrow">PROFILE READY</p><h2>Профиль готов</h2></div><button className="iconButton" onClick={() => setCreatedProfile(null)}>x</button></header><p>Отсканируйте QR в Mihomo-клиенте или скопируйте постоянную ссылку подписки. Изменения профиля появятся после обновления подписки.</p><div className="mihomoReadySummary"><b>{createdProfile.name}</b><span>{createdProfile.connections.length} подключений · {createdProfile.devices?.length || 1} устройств · {profileDirectRules.filter((rule) => Boolean(createdProfile.routing?.[rule.key])).length} правил</span></div><div className="mihomoReadyDevices">{readyDevices.map((device) => <article key={device.id}><Image src={device.qr} alt={`QR подписки ${device.name}`} width={148} height={148} unoptimized /><div><b>{device.name}</b><small>Постоянная подписка Mihomo</small><code>{device.subscription}</code><nav><button className="primaryButton" onClick={() => void navigator.clipboard.writeText(device.subscription)}>Скопировать ссылку</button><button className="ghostButton" onClick={() => void downloadConfig(createdProfile, device)}>Скачать YAML</button></nav></div></article>)}{!readyDevices.length && <div className="mihomoHint">Подготавливаем QR-коды подписок…</div>}</div><footer><button className="primaryButton" onClick={() => setCreatedProfile(null)}>Готово</button></footer></div></div>}
      {presetDialog && <div className="mihomoDialogBackdrop"><form className="mihomoDialog mihomoPresetDialog" onSubmit={savePresetSettings}><header><div><p className="eyebrow">PRESET ROUTING</p><h2>Настройки быстрых профилей</h2></div><button type="button" className="iconButton" onClick={() => setPresetDialog(false)}>x</button></header><p>Для каждого пресета отдельно выберите стратегию и соединения. VLESS и VLESS CDN независимы.</p><div className="mihomoPresetEditor">{presetDraft.map((preset, index) => <article key={preset.id}><label><span>Название</span><input value={preset.name} onChange={(event) => setPresetDraft((current) => current.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} /></label><label><span>Стратегия</span><select value={preset.strategy} onChange={(event) => setPresetDraft((current) => current.map((item, i) => i === index ? { ...item, strategy: event.target.value as ProfilePreset["strategy"] } : item))}><option value="fallback">Fallback</option><option value="url-test">Автовыбор по задержке</option><option value="select">Ручной выбор</option></select></label><div>{presetConnectionOptions.map((option) => { const selected = preset.components.some((item) => item.id === option.id && Boolean(item.cdn) === Boolean(option.cdn) && String(item.transport || "") === String(option.transport || "")); return <label key={`${option.id}-${option.cdn ? "cdn" : "direct"}-${option.transport || "default"}`}><input type="checkbox" checked={selected} onChange={(event) => setPresetDraft((current) => current.map((item, i) => i !== index ? item : { ...item, components: event.target.checked ? [...item.components, option] : item.components.filter((component) => !(component.id === option.id && Boolean(component.cdn) === Boolean(option.cdn) && String(component.transport || "") === String(option.transport || ""))) }))} /><span>{option.label}</span></label>; })}</div></article>)}</div><footer><button type="button" className="ghostButton" onClick={() => setPresetDialog(false)}>Отмена</button><button type="submit" className="primaryButton" disabled={busy === "presets" || presetDraft.some((item) => !item.components.length)}>Сохранить пресеты</button></footer></form></div>}
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
      <div className="mihomoModuleCatalog">
        {modules.map((module) => (
          <div key={module.id} className={module.installed ? "is-installed" : ""}>
            <span className="mihomoModuleCode">{channelShort[module.id] || (module.category === "dns" ? "DNS" : "RT")}</span>
            <p>
              <b>{module.name}</b>
              <small>{module.description}</small>
              <code>{module.service || "config-only module"}</code>
              {module.installed && module.installed_version && (
                <span className="mihomoModuleVersionRow">
                  <i>{formatModuleVersion(module.installed_version)}</i>
                  {module.update_available && <em className={module.update_breaking ? "breaking" : ""}>→ {module.id === "transport-awg" ? "репозиторий" : formatModuleVersion(module.available_version)}{module.update_breaking ? "  major" : ""}</em>}
                </span>
              )}
            </p>
            <span className={module.active ? "onlinePill" : module.installed ? "warningPill" : "disabledPill"}>{module.active ? "Активен" : module.installed ? "Установлен" : "Не установлен"}</span>
            <span className="mihomoModuleActions">
              <button className="ghostButton" onClick={() => onSettings(module)}>Настройки</button>
              {module.installed && module.update_available && (
                <button className={`ghostButton${module.update_breaking ? " breaking" : ""}`} disabled={Boolean(busy)} onClick={() => void onUpdate(module)}>{busy === `update:${module.id}` ? "Обновление…" : "Обновить"}</button>
              )}
              <button className={module.installed ? "dangerButton" : "primaryButton"} disabled={module.installable === false || Boolean(busy)} onClick={() => void onToggle(module)}>{module.installable === false ? "В разработке" : busy === module.id ? "Выполняется…" : module.installed ? "Удалить" : "Установить"}</button>
            </span>
          </div>
        ))}
        {!modules.length && <Empty title="Каталог пуст" text="Mihomo Manager не получил manifest внутренних модулей." />}
      </div>
    </article>
  );
}

function Empty({ title, text }: { title: string; text: string }) {
  return <div className="mihomoEmpty"><p><b>{title}</b><small>{text}</small></p></div>;
}
