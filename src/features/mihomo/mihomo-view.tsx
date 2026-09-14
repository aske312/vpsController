"use client";

import type { View, ReadyDevice, ConfirmOptions, Status, Module, Profile, ProfileConnection, ProfileDevice, PolicySettings, ProfilePreset, ProfileStats, RuleIconGroup, PersonalRule } from "./types";
import { presetConnectionOptions, presetOptionGroups, channelShort, dnsProviderMeta, gameRoutingCatalog, defaultTunnelGameIds, defaultDirectGameIds, udpExclusionCatalog, p2pClientCatalog, profileDirectRules, ruleIconGroups, profileStrategies } from "./catalog";
import { clientUuid, deviceClientShortName, clientConfigFormat, clientImportUrl, devicePlatformMeta, deviceSystemLabel, registeredProfileDevices, selectedGameIds } from "./profile-utils";
import { Tab, HeroFact, ModuleCatalog, Empty } from "./components";
import { clientCapabilities, compatibleClientRouting, clientConnectionSupported } from "./client-capabilities";
import { ProfileProtection } from "./profile-protection";
import { checkProfileMutation, profileTransitionMessage, ProfileResultUnknown, submitProfileMutation, type ProfileMutation } from "./profile-operation";

import { useNotifier, useFailureNotifications } from "../../shared/notifications/notification-center";
import { notificationFailure as refreshFailure, type NotificationFailure } from "../../shared/notifications/store";
import { createApiClient } from "../../shared/lib/api-request";
import { bytes, duration } from "../../shared/lib/control-plane-ui";
import QRCode from "qrcode";
import Image from "next/image";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { createPortal } from "react-dom";

function DeviceClientBadge({ device }: { device: ProfileDevice }) {
  if (device.scope === "common" || device.manual || device.scope === "manual") return null;
  return <span className="mihomoDeviceClientBadge" title={device.client_name ? `Определено приложение: ${device.client_name}` : "Приложение пока не определено"}>{deviceClientShortName(device)}</span>;
}

const TECHNICAL_ERROR = /(?:\n|traceback|systemctl|journalctl|apt(?:-get)?|dpkg|stderr|stdout|exit status|failed to start|reading package lists|building dependency tree)/i;

function publicError(message: string, status: number) {
  const value = message.trim();
  return status >= 500 || TECHNICAL_ERROR.test(value) || /^(?:bad gateway|internal server error)$/i.test(value)
    ? "Команда завершилась с ошибкой. Технические сведения сохранены в журнале."
    : value || "Команда не выполнена.";
}

export function MihomoPage({
  token,
  confirmAction,
  coreBusy,
  onRemoveCore,
  onCommandComplete,
}: {
  token: string;
  confirmAction: (options: ConfirmOptions) => Promise<boolean>;
  coreBusy: boolean;
  onRemoveCore: () => Promise<void>;
  onCommandComplete: () => void;
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
  const { error: notifyError, success: notifySuccess, operation: notifyOperation } = useNotifier("mihomo", "Mihomo");
  const [refreshErrors, setRefreshErrors] = useState<Record<string, NotificationFailure>>({});
  useFailureNotifications("mihomo", "Обновление Mihomo", refreshErrors);
  const refreshInFlight = useRef<Promise<void> | null>(null);
  const [editing, setEditing] = useState<Module | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<Record<string, string | number | boolean>>({});
  const [profileDialog, setProfileDialog] = useState<Profile | "new" | null>(null);
  const profileMutationId = useRef(clientUuid());
  const [profileStep, setProfileStep] = useState(1);
  const profileCanvasRef = useRef<HTMLElement>(null);
  const [profileName, setProfileName] = useState("");
  const [profileConnections, setProfileConnections] = useState<ProfileConnection[]>([]);
  const [profileRouting, setProfileRouting] = useState<Record<string, string | number | boolean>>({});
  const [profileStrategyTouched, setProfileStrategyTouched] = useState(false);
  const [profileDevices, setProfileDevices] = useState<ProfileDevice[]>([{ id: "profile-common", name: "Общие настройки профиля", scope: "common" }]);
  const [activeDeviceId, setActiveDeviceId] = useState("profile-common");
  const activeProfileDevice = profileDevices.find((device) => device.id === activeDeviceId);
  const activeProfileRouting = activeProfileDevice?.routing || profileRouting;
  const activeCapabilities = clientCapabilities(activeProfileRouting.client_config_format, activeProfileDevice?.scope === "common" ? undefined : activeProfileDevice?.os || "unknown", activeProfileDevice?.manual ? undefined : activeProfileDevice?.client_name);
  const commonDevice = activeProfileDevice?.scope === "common";
  const visibleProfileStrategies = profileStrategies.filter((strategy) => commonDevice || activeCapabilities.strategies.includes(strategy.value));
  const visibleProfileRules = profileDirectRules.filter((rule) => commonDevice || activeCapabilities.rules.includes(rule.key));
  // Poll live delivery/cleanup status without replacing unsaved form fields.
  const liveDialogProfile = profileDialog && profileDialog !== "new"
    ? profiles.find((profile) => profile.id === profileDialog.id) || profileDialog : null;
  const transitionMessage = liveDialogProfile ? profileTransitionMessage(liveDialogProfile, activeDeviceId) : "";
  const [profileStats, setProfileStats] = useState<Record<string, ProfileStats>>({});
  const [expandedDeviceLists, setExpandedDeviceLists] = useState<Set<string>>(() => new Set());
  const [expandedProtocolLists, setExpandedProtocolLists] = useState<Set<string>>(() => new Set());
  const [createdProfile, setCreatedProfile] = useState<Profile | null>(null);
  const [readyDevices, setReadyDevices] = useState<ReadyDevice[]>([]);
  const [readyFailed, setReadyFailed] = useState(false);
  const [presetDialog, setPresetDialog] = useState(false);
  const [presetDraft, setPresetDraft] = useState<ProfilePreset[]>([]);
  const [presetEditorIndex, setPresetEditorIndex] = useState(0);
  const [personalRuleDialog, setPersonalRuleDialog] = useState<PersonalRule | "new" | null>(null);
  const [personalRuleDraft, setPersonalRuleDraft] = useState({ code: "PERS", title: "", description: "", kind: "domain" as PersonalRule["kind"], entries: "", target: "GATE.312" as PersonalRule["target"] });

  useEffect(() => {
    profileCanvasRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [profileStep, profileDialog]);

  const request = useMemo(() => createApiClient(token, {
    formatHttpError: (detail, status) => status === 401 ? "Сессия панели завершена. Войдите заново." : publicError(detail, status),
  }), [token]);

  const refresh = useCallback(async (afterAction = true): Promise<void> => {
    if (refreshInFlight.current) {
      if (!afterAction) return refreshInFlight.current;
      await refreshInFlight.current;
      if (refreshInFlight.current) return refreshInFlight.current;
    }
    const job = (async () => {
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
        setRefreshErrors({});
      } catch (cause) {
        setRefreshErrors({ overview: refreshFailure(cause, "Не удалось обновить данные Mihomo") });
      }
    })().finally(() => { refreshInFlight.current = null; });
    refreshInFlight.current = job;
    return job;
  }, [request]);

  function updateDnsDraft(key: string, value: string | number | boolean) {
    setDnsDraft((current) => ({ ...current, [key]: value }));
    dnsDirtyRef.current = true;
    setDnsDirty(true);
  }

  async function saveDnsWorkspace(event: FormEvent) {
    event.preventDefault();
    const operationId = "settings:dns-private";
    notifyOperation(operationId, "DNS Mihomo", "running", "Сохраняем DNS для профилей Mihomo…");
    setBusy(operationId);
    try {
      await request("/mihomo/dns/settings", { method: "PATCH", body: JSON.stringify({ values: dnsDraft }) });
      dnsDirtyRef.current = false;
      setDnsDirty(false);
      await refresh();
      notifySuccess("DNS для соединений Mihomo сохранён");
      notifyOperation(operationId, "DNS Mihomo", "success", "DNS-настройки сохранены");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "DNS-настройки не сохранены";
      notifyOperation(operationId, "DNS Mihomo", "error", message);
      notifyError(message);
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(false), 0);
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void refresh(false); }, 15000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [refresh]);

  useEffect(() => {
    if (!createdProfile) return;
    let cancelled = false;
    void (async () => {
      setReadyFailed(false);
      const devices = createdProfile.devices?.length ? createdProfile.devices : [{ id: createdProfile.common_device_id || "profile-common", name: createdProfile.name }];
      const items: ReadyDevice[] = [];
      for (const device of devices) {
        const result = await request(`/mihomo/profiles/${createdProfile.id}/subscription${device.manual ? `?device_id=${encodeURIComponent(device.id)}` : ""}`) as { path: string; url?: string };
        const url = new URL(result.url || result.path, window.location.origin);
        const format = clientConfigFormat(createdProfile, device);
        const subscription = url.toString();
        const importUrl = clientImportUrl(subscription, `${createdProfile.name} · ${device.name}`, device.scope === "common" ? "mihomo" : format, device.scope === "common" ? "" : `${device.client_name || ""} ${device.user_agent || ""}`);
        const qr = await QRCode.toDataURL(importUrl, { errorCorrectionLevel: "M", margin: 2, width: 360 });
        items.push({ ...device, subscription, qr });
      }
      return items;
    })().then((items) => { if (!cancelled) setReadyDevices(items); }).catch((cause) => {
      if (!cancelled) {
        setReadyFailed(true);
        notifyError(cause instanceof Error ? cause.message : "Не удалось подготовить QR-коды подписок");
      }
    });
    return () => { cancelled = true; };
  }, [createdProfile, request, notifyError]);

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
        .catch((cause) => notifyError(cause instanceof Error ? cause.message : "Не удалось сохранить выбор"))
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

  function setProfileRoutingValue(key: string, value: string | boolean) {
    setProfileDevices((current) => current.map((device) => device.id === activeDeviceId
      ? { ...device, routing: { ...(device.routing || profileRouting), [key]: value } }
      : device));
  }

  function toggleProfileRule(key: string, checked: boolean) {
    setProfileRoutingValue(key, checked);
  }

  function togglePersonalRule(ruleId: string) {
    setProfileDevices((current) => current.map((device) => {
      if (device.id !== activeDeviceId) return device;
      const selected = new Set(device.personal_rule_ids || []);
      if (selected.has(ruleId)) selected.delete(ruleId); else selected.add(ruleId);
      return { ...device, personal_rule_ids: [...selected] };
    }));
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
    notifyOperation(operationId, "Маршрутизация Mihomo", "running", "Сохраняем правила маршрутизации…");
    setBusy(operationId);

    try {
      await request("/mihomo/routing/settings", { method: "PATCH", body: JSON.stringify({ values: routingDraft }) });
      routingDirtyRef.current = false;
      setRoutingDirty(false);
      await refresh();
      notifyOperation(operationId, "Маршрутизация Mihomo", "success", "Правила сохранены. Обновите подписку в клиенте.");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Маршрутизация не сохранена";
      notifyOperation(operationId, "Маршрутизация Mihomo", "error", message);
    } finally {
      setBusy("");
    }
  }

  async function requestCoreRemoval() {

    let current: Status;
    try {
      current = (await request("/mihomo/status")) as Status;
      setStatus(current);
    } catch (cause) {
      notifyError(
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
    notifyOperation(operationId, operationLabel, "running", "Mihomo Manager применяет изменение…");
    setBusy(module.id);

    try {
      await request(`/mihomo/modules/${module.id}${install ? "/install" : ""}`, {
        method: install ? "POST" : "DELETE",
      });
      await refresh();
      notifyOperation(operationId, operationLabel, "success", `${module.name}: ${install ? "установлен; DNS и маршрутизация Mihomo готовы" : "удалён из Mihomo"}.`);
      onCommandComplete();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Операция Mihomo не выполнена";
      notifyOperation(operationId, operationLabel, "error", message);
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
    notifyOperation(operationId, operationLabel, "running", "Mihomo Manager применяет обновление…");
    setBusy(`update:${module.id}`);

    try {
      await request(`/mihomo/modules/${module.id}/update`, { method: "POST" });
      await refresh();
      notifyOperation(operationId, operationLabel, "success", `${module.name}: обновлён до ${module.available_version}.`);
      onCommandComplete();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Обновление Mihomo-модуля не выполнено";
      notifyOperation(operationId, operationLabel, "error", message);
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
    notifyOperation(operationId, operationLabel, "running", "Сохраняем настройки внутреннего модуля…");
    setBusy(operationId);

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
      setEditing(null);
      await refresh();
      notifyOperation(operationId, operationLabel, "success", "Настройки сохранены");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Настройки не сохранены";
      notifyOperation(operationId, operationLabel, "error", message);
    } finally {
      setBusy("");
    }
  }

  function newProfile() {
    profileMutationId.current = clientUuid();
    setProfileStep(1);
    setProfileStrategyTouched(false);
    setProfileDialog("new");
    setProfileName("");
    setProfileConnections([]);
    setProfileRouting({});
    setProfileDevices([{ id: "profile-common", name: "Общие настройки профиля", scope: "common", routing: {} }]);
    setActiveDeviceId("profile-common");
  }

  function editProfile(profile: Profile) {
    profileMutationId.current = clientUuid();
    setProfileStep(1);
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
    const devices = (profile.devices?.length ? profile.devices : [{ id: "profile-common", name: "Общие настройки профиля", scope: "common" as const }]).map((device) => ({ ...device, routing: device.scope === "common" ? { ...(device.routing || profile.routing || {}) } : compatibleClientRouting(device.routing || profile.routing || {}, String(device.routing?.client_config_format || "mihomo"), device.os || "unknown", device.manual ? undefined : device.client_name) }));
    setProfileDevices(devices);
    setActiveDeviceId(profile.common_device_id || devices.find((device) => device.scope === "common")?.id || devices[0].id);
  }

  function addProfileConnection(module: Module, vlessRoute: "direct" | "tls" | "cdn" = "direct") {
    const settings = Object.fromEntries((module.connection_settings || []).map((field) => [field.key, field.default]));
    if (module.id === "transport-reality") {
      if (!commonDevice) {
        for (const key of ["transport", "cdn_transport", "tls_transport"]) {
          if (!activeCapabilities.transports.includes(String(settings[key]))) settings[key] = key === "transport" ? "raw" : "websocket";
        }
      }
      settings.route_mode = vlessRoute;
      settings.cdn_enabled = vlessRoute === "cdn";
      if (vlessRoute === "cdn") settings.cdn_domain = String(routingPolicy?.values.preset_cdn_domain || "").trim();
      if (vlessRoute === "tls") settings.tls_domain = String(routingPolicy?.values.preset_tls_domain || "").trim();
    }
    setProfileConnections((current) => [...current, {
      id: `connection-${clientUuid()}`,
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
      connections.push({ id: `connection-${clientUuid()}`, component: componentId, name: definition.label || `${componentModule.name}${definition.cdn ? " · CDN" : ""}`, device_id: activeDeviceId, settings });
    }
    setProfileConnections((current) => [...current.filter((connection) => connection.device_id !== activeDeviceId), ...connections]);
    if (!profileStrategyTouched) setProfileDevices((current) => current.map((device) => device.id === activeDeviceId ? { ...device, routing: commonDevice ? { ...(device.routing || profileRouting), strategy: preset.strategy } : compatibleClientRouting({ ...(device.routing || profileRouting), strategy: preset.strategy }, String(activeProfileRouting.client_config_format || "mihomo"), activeProfileDevice?.os || "unknown", activeProfileDevice?.client_name) } : device));
    if (!profileName.trim()) setProfileName(preset.name);
  }

  function updateConnectionSetting(id: string, key: string, value: string | number | boolean) {
    setProfileConnections((current) => current.map((connection) => connection.id === id
      ? { ...connection, settings: { ...connection.settings, [key]: value } }
      : connection));
  }

  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    if (!profileDialog) return;
    const creating = profileDialog === "new";
    const operationId = creating ? "profile:new" : `profile:${profileDialog ? profileDialog.id : "edit"}`;
    const operationLabel = creating ? "Создание Mihomo-профиля" : `Сохранение профиля ${profileName}`;
    const mutation: ProfileMutation = {
      profileId: profileDialog === "new" ? undefined : profileDialog.id,
      payload: { name: profileName, devices: profileDevices, connections: profileConnections, routing: profileDevices.find((device) => device.scope === "common")?.routing || profileRouting, operation_id: profileMutationId.current },
    };
    const report = (message: string) => notifyOperation(operationId, operationLabel, "running", message);
    const finish = async (profile: Profile) => {
      setProfileDialog(null);
      await refresh();
      const transition = profileTransitionMessage(profile);
      notifyOperation(operationId, operationLabel, "success", transition || "Сохранение подтверждено. Обновите подписку в VPN-клиенте.");
    };
    const fail = (cause: unknown) => {
      const message = cause instanceof Error ? cause.message : "Профиль не сохранён";
      notifyOperation(operationId, operationLabel, cause instanceof ProfileResultUnknown ? "unknown" : "error", message, () => {
        setBusy("profile");

        void checkProfileMutation(request, mutation, report).then(finish).catch(fail).finally(() => setBusy(""));
      });
    };
    notifyOperation(operationId, operationLabel, "running", "Обновляем профиль и credentials…");
    setBusy("profile");

    try {
      await finish(await submitProfileMutation(request, mutation, report));
    } catch (cause) {
      fail(cause);
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
    notifyOperation(operationId, operationLabel, "running", "Удаляем профиль и связанные credentials…");
    setBusy(operationId);

    try {
      await request(`/mihomo/profiles/${profile.id}`, { method: "DELETE" });
      await refresh();
      notifyOperation(operationId, operationLabel, "success", "Профиль удалён");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Профиль не удалён";
      notifyOperation(operationId, operationLabel, "error", message);
    } finally {
      setBusy("");
    }
  }

  async function removeProfileDevice(profile: Profile, device: ProfileDevice) {
    if (device.scope === "common" || device.id === profile.common_device_id) {
      notifyError("Общие настройки профиля нельзя удалить");
      return;
    }
    const devices = profile.devices?.length ? profile.devices : [{ id: "device-1", name: "Устройство" }];
    if (devices.length <= 1) {
      notifyError("В профиле должно остаться хотя бы одно устройство. Удалите профиль целиком, если он больше не нужен.");
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
    notifyOperation(operationId, `Удаление устройства ${device.name}`, "running", "Удаляем устройство и его credentials…");
    setBusy(operationId);

    try {
      await request(`/mihomo/profiles/${profile.id}/devices/${encodeURIComponent(device.id)}`, { method: "DELETE" });
      await refresh();
      notifyOperation(operationId, `Удаление устройства ${device.name}`, "success", "Устройство удалено");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Устройство не удалено";
      notifyOperation(operationId, `Удаление устройства ${device.name}`, "error", message);
    } finally {
      setBusy("");
    }
  }

  async function downloadConfig(profile: Profile, device?: ProfileDevice) {
    setBusy(`download:${profile.id}`);

    try {
      const config = (await request(`/mihomo/profiles/${profile.id}/config${device ? `?device_id=${encodeURIComponent(device.id)}` : ""}`)) as string;
      const link = document.createElement("a");
      const format = clientConfigFormat(profile, device);
      const singbox = format === "singbox" || format === "xray";
      link.href = URL.createObjectURL(new Blob([config], { type: format === "uri" ? "text/plain;charset=utf-8" : singbox ? "application/json;charset=utf-8" : "application/yaml;charset=utf-8" }));
      link.download = format === "uri" ? profile.export_filename.replace(/\.yaml$/, ".txt") : singbox ? profile.export_filename.replace(/\.yaml$/, ".json") : profile.export_filename;
      document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(link.href);
      notifySuccess(`Профиль «${profile.name}» скачан.`);
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось скачать профиль"); }
    finally { setBusy(""); }
  }

  async function copyText(value: string) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const area = document.createElement("textarea");
    area.value = value;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    if (!copied) throw new Error("Браузер не разрешил копирование. Скопируйте ссылку вручную.");
  }

  async function copySubscription(profile: Profile) {
    setBusy(`subscription:${profile.id}`);

    try {
      const result = await request(`/mihomo/profiles/${profile.id}/subscription`) as { path: string; url?: string };
      await copyText(result.url || new URL(result.path, window.location.origin).toString());
      notifySuccess(`Единая ссылка профиля «${profile.name}» скопирована. Клиент с HWID появится как отдельное устройство, без HWID получит общие правила.`);
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "Не удалось получить ссылку подписки");
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
    const suffix = clientUuid().slice(0, 8);
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
    event.preventDefault(); setBusy("presets");
    try {
      const result = await request("/mihomo/routing/presets", { method: "PATCH", body: JSON.stringify({ presets: presetDraft }) }) as { presets: ProfilePreset[] };
      setRoutingPolicy((current) => current ? { ...current, presets: result.presets } : current);
      setPresetDialog(false); notifySuccess("Настройки быстрых профилей сохранены.");
    } catch (cause) { notifyError(cause instanceof Error ? cause.message : "Не удалось сохранить пресеты"); }
    finally { setBusy(""); }
  }

  function openPersonalRuleEditor(rule?: PersonalRule) {
    setPersonalRuleDraft(rule ? { code: rule.code, title: rule.title, description: rule.description, kind: rule.kind, entries: rule.entries.join("\n"), target: rule.target } : { code: "PERS", title: "", description: "", kind: "domain", entries: "", target: "GATE.312" });
    setPersonalRuleDialog(rule || "new");
  }

  async function savePersonalRule(event: FormEvent) {
    event.preventDefault();
    setBusy("personal-rule");
    try {
      const payload = { ...personalRuleDraft, entries: personalRuleDraft.entries.replace(/\r/g, "").split("\n").map((entry) => entry.trim()).filter(Boolean) };
      const ruleId = personalRuleDialog && personalRuleDialog !== "new" ? `/${encodeURIComponent(personalRuleDialog.id)}` : "";
      await request(`/mihomo/routing/personal-rules${ruleId}`, { method: ruleId ? "PATCH" : "POST", body: JSON.stringify(payload) });
      setPersonalRuleDialog(null);
      await refresh();
      notifySuccess(ruleId ? "Персональное правило обновлено" : "Персональное правило создано");
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "Не удалось сохранить персональное правило");
    } finally {
      setBusy("");
    }
  }

  async function removePersonalRule(rule: PersonalRule) {
    if (!await confirmAction({ title: `Удалить правило «${rule.title}»?`, message: "Правило будет убрано из всех устройств, где оно включено.", confirmLabel: "Удалить правило", danger: true })) return;
    setBusy(`personal-rule:${rule.id}`);
    try {
      await request(`/mihomo/routing/personal-rules/${encodeURIComponent(rule.id)}`, { method: "DELETE" });
      if (activeRuleList === `personal:${rule.id}`) setActiveRuleList("direct_ru_sites");
      await refresh();
      notifySuccess("Персональное правило удалено");
    } catch (cause) {
      notifyError(cause instanceof Error ? cause.message : "Не удалось удалить персональное правило");
    } finally {
      setBusy("");
    }
  }

  const transportModules = useMemo(
    () => modules.filter((item) => item.category === "transport"),
    [modules],
  );
  const installedChannels = transportModules.filter((item) => item.installed);
  const profilePresets = routingPolicy?.presets || [];
  const policiesReady = installedChannels.length > 0 && Boolean(dnsPolicy && routingPolicy);
  const editableRuleLists = routingPolicy?.rule_lists || [];
  const personalRules = routingPolicy?.personal_rules || [];
  const selectedRuleList = editableRuleLists.find((item) => item.id === activeRuleList) || editableRuleLists[0];
  const selectedPersonalRule = personalRules.find((rule) => activeRuleList === `personal:${rule.id}`);
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
  const visibleRuleGroups = [...new Set(visibleProfileRules.map((rule) => rule.group))].map((group) => ({
    group,
    rules: profileDirectRules.filter((rule) => rule.group === group && (!normalizedRuleSearch || `${rule.title} ${rule.text} ${rule.code}`.toLocaleLowerCase("ru").includes(normalizedRuleSearch))),
  })).filter((section) => section.rules.length);
  const visiblePersonalRules = personalRules.filter((rule) => !normalizedRuleSearch || `${rule.title} ${rule.description} ${rule.code} ${rule.entries.join(" ")}`.toLocaleLowerCase("ru").includes(normalizedRuleSearch));
  const overviewDevices = profiles.reduce((sum, profile) => sum + registeredProfileDevices(profile).length, 0);
  const overviewConnections = profiles.reduce((sum, profile) => sum + profile.connections.length, 0);
  const overviewActiveConnections = Object.values(profileStats).reduce((sum, item) => sum + Number(item.summary.active || 0), 0);
  const overviewRx = Object.values(profileStats).reduce((sum, item) => sum + Number(item.summary.rx_bytes || 0), 0);
  const overviewTx = Object.values(profileStats).reduce((sum, item) => sum + Number(item.summary.tx_bytes || 0), 0);
  const overviewActiveProfiles = profiles.filter((profile) => Number(profileStats[profile.id]?.summary.active || 0) > 0).length;
  const overviewIssues = [
    !status?.active ? { title: "Ядро Mihomo не отвечает", text: "Проверьте состояние сервиса перед выдачей профилей.", view: "channels" as View } : null,
    !installedChannels.length ? { title: "Нет компонентов подключения", text: "Установите хотя бы один транспорт.", view: "channels" as View } : null,
    installedChannels.length > 0 && !policiesReady ? { title: "Политики ещё не готовы", text: "Проверьте настройки маршрутизации на странице «Сеть».", view: "routing" as View } : null,
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
      <article className="mihomoCommandHero">
        <div className="mihomoHeroContent">
          <div className="mihomoHeroIntro">
            <p className="eyebrow">MIHOMO CONTROL</p>
            <div className="mihomoHeroTitleLine">
              <h1>Центр управления</h1>
              <span className={status?.active ? "mihomoCoreState online" : "mihomoCoreState"}>
                {status?.active ? "В СЕТИ" : "НУЖНА ПРОВЕРКА"}
              </span>
            </div>
            <p className="mihomoHeroLead">
              Профили, защищённые маршруты и правила доступа в одном контуре.
            </p>
          </div>

          <div className="mihomoHeroFacts">
            <HeroFact label="ЯДРО" value={status?.core_version || "—"} note={status?.active ? "работает штатно" : "статус недоступен"} />
            <HeroFact label="ПРОФИЛИ" value={String(status?.profiles ?? profiles.length)} note={`${status?.profiles_in_use || 0} используются`} />
            <HeroFact label="АКТИВНЫЕ КАНАЛЫ" value={String(overviewActiveConnections)} note={`${overviewConnections} настроено`} />
            <HeroFact label="КОМПОНЕНТЫ" value={`${status?.channels_installed ?? installedChannels.length}/${status?.modules_total ?? modules.length}`} note={`${status?.channels_in_use?.length || 0} используются`} />
          </div>

          <div className="mihomoHeroActions">
            <button className="primaryButton" type="button" onClick={() => setView("profiles")}>Управление профилями</button>
            <button className="ghostButton" type="button" onClick={() => void refresh()} disabled={Boolean(busy)}>Обновить данные</button>
            <button
              className="dangerButton"
              type="button"
              onClick={() => void requestCoreRemoval()}
              disabled={coreBusy || Boolean(busy)}
            >
              {coreBusy ? "Удаляется…" : "Удалить Mihomo"}
            </button>
          </div>
          <div className="mihomoHeroEndpoint"><span>ENDPOINT</span><b>{status?.endpoint || "Не определён"}</b></div>
        </div>
        <div className="mihomoHeroArt" aria-hidden="true" />
        <nav className="mihomoHost__tabs mihomoTabs mihomoHeroNavigation" aria-label="Разделы Mihomo">
          <Tab id="overview" current={view} onSelect={setView}>Обзор</Tab>
          <Tab id="profiles" current={view} onSelect={setView} badge={profiles.length}>Профили</Tab>
          <Tab id="channels" current={view} onSelect={setView} badge={installedChannels.length}>Компоненты</Tab>
          <Tab id="dns" current={view} onSelect={setView}>DNS</Tab>
          <Tab id="rules" current={view} onSelect={setView}>Правила</Tab>
          <Tab id="routing" current={view} onSelect={setView}>Настройки</Tab>
        </nav>
      </article>

      {view === "overview" && (
        <div className="mihomoOverviewV3">
          <section className="mihomoOverviewPulse">
            <header><div><p className="eyebrow">СОСТОЯНИЕ СЕТИ</p><h2>{overviewActiveConnections ? `${overviewActiveConnections} подключений активно` : "Ожидание подключений"}</h2><span>{status?.active ? "Ядро Mihomo принимает конфигурации и обслуживает маршруты" : "Проверьте состояние ядра перед выдачей профилей"}</span></div><i className={status?.active ? "is-online" : ""} /></header>
            <div className="mihomoOverviewPulseGrid">
              <article><small>ПРОФИЛИ В СЕТИ</small><strong>{overviewActiveProfiles}<i> / {profiles.length}</i></strong><span>с активными клиентами</span></article>
              <article><small>HWID-УСТРОЙСТВА</small><strong>{overviewDevices}</strong><span>зарегистрировано</span></article>
              <article><small>ВХОДЯЩИЙ ТРАФИК</small><strong>↓ {bytes(overviewRx)}</strong><span>↑ {bytes(overviewTx)} исходящего</span></article>
              <article><small>НАСТРОЕННЫЕ КАНАЛЫ</small><strong>{overviewConnections}</strong><span>во всех профилях</span></article>
            </div>
          </section>

          <section className="mihomoOverviewDesk">
            <article className="mihomoOverviewLiveProfiles">
              <header><div><p className="eyebrow">ПРОФИЛИ</p><h3>Текущая активность</h3></div>{profiles.length > 0 && <button type="button" onClick={() => setView("profiles")}>Все профили</button>}</header>
              <div>{profiles.slice(0, 8).map((profile) => { const item = profileStats[profile.id]?.summary; const devices = registeredProfileDevices(profile).length; return <div className="mihomoOverviewLiveRow" key={profile.id}><i className={item?.active ? "is-online" : ""} /><p><b>{profile.name}</b><small>{devices} устройств · {profile.connections.length} каналов</small></p><strong>{item ? `${item.active}/${item.configured}` : "—"}<small>активно</small></strong><em>↓ {bytes(item?.rx_bytes || 0)}<small>↑ {bytes(item?.tx_bytes || 0)}</small></em></div>; })}</div>
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
                  <button type="button" className="mihomoProfileToggle" aria-expanded={expandedDeviceLists.has(profile.id)} onClick={() => toggleCollapsed(setExpandedDeviceLists, profile.id)}><div className="mihomoProfileIdentity"><span className="mihomoProfileIcon">M</span><p><b>{profile.name}</b><small>{devices.length} устройств · общий пул {profile.connections.filter((connection) => connection.device_id === commonDevice.id).length} каналов{profile.subscription_status === "obsolete" ? " · подписка устарела, требуется новая установка" : ""}</small><em>ID {profile.id} · обновлён {new Date(profile.updated_at || profile.created_at).toLocaleString("ru-RU")}</em></p></div><div className="mihomoProfileSummary"><div><small>Состояние</small><strong className={stats?.active ? "is-online" : ""}><i />{stats ? `${stats.active} из ${stats.configured}` : "—"}</strong><span>активных каналов</span></div><div><small>Трафик</small><strong>↓ {bytes(stats?.rx_bytes || 0)}</strong><span>↑ {bytes(stats?.tx_bytes || 0)}</span></div></div><i className="mihomoCollapseChevron" aria-hidden="true" /></button>
                  <div className="mihomoRowActions"><button className="primaryButton" onClick={() => void copySubscription(profile)} disabled={busy === `subscription:${profile.id}`}>{profile.subscription_status === "obsolete" ? "Обновить подписку" : "Скопировать подписку"}</button><button onClick={() => { setReadyDevices([]); setCreatedProfile({ ...profile, devices: [commonDevice] }); }}>QR</button><button onClick={() => editProfile(profile)}>Настроить</button><button className="dangerButton" onClick={() => void removeProfile(profile)} disabled={busy === `profile:${profile.id}`}>Удалить</button></div>
                </header>
                {expandedDeviceLists.has(profile.id) && <div className="mihomoProfileDevices">
                  {devices.map((device) => {
                    const connections = profile.connections.filter((connection) => connection.device_id === device.id);
                    const connectionStats = connections.map((connection) => profileStats[profile.id]?.connections?.[connection.id]);
                    const onlineCount = connectionStats.filter((item) => Boolean(item?.active || item?.endpoint || Number(item?.active_connections || 0))).length;
                    const deviceRx = connectionStats.reduce((sum, item) => sum + Number(item?.rx_bytes || 0), 0);
                    const deviceTx = connectionStats.reduce((sum, item) => sum + Number(item?.tx_bytes || 0), 0);
                    const protocolListKey = `${profile.id}:${device.id}`;
                    const protocolsExpanded = expandedProtocolLists.has(protocolListKey);
                    const deleting = busy === `device:${profile.id}:${device.id}`;
                    return <section key={device.id} className="mihomoProfileDevice">
                      <header className="mihomoDeviceHeader">
                        <button type="button" className="mihomoDeviceToggle" aria-expanded={protocolsExpanded} onClick={() => toggleCollapsed(setExpandedProtocolLists, protocolListKey)}><div className="mihomoDeviceIdentity"><span>{device.scope === "common" ? "ALL" : devicePlatformMeta(device).code}</span><p><b><DeviceClientBadge device={device} />{device.scope === "common" ? "Общие настройки профиля" : device.name}</b><small>{connections.length} каналов · {device.scope === "hwid" ? deviceSystemLabel(device) : device.scope === "common" ? "для клиентов без HWID и новых устройств" : device.scope === "manual" ? "Общий sing-box для всех клиентов" : "устаревшее устройство"}</small>{device.scope === "hwid" && <em>{device.last_seen_at ? `Последний запрос ${new Date(device.last_seen_at).toLocaleString("ru-RU")}` : "HWID зарегистрирован"}</em>}</p></div><div className="mihomoDeviceTotals"><span><small>КАНАЛЫ</small><b>{onlineCount}/{connections.length}</b></span><span><small>ПРАВИЛА</small><b>{ruleCount}</b></span><span><small>ТРАФИК</small><b>↓ {bytes(deviceRx)} · ↑ {bytes(deviceTx)}</b></span></div><i className="mihomoCollapseChevron" aria-hidden="true" /></button>
                        <nav className="mihomoDeviceActions"><button onClick={() => void downloadConfig(profile, device)} disabled={busy === `download:${profile.id}`}>{clientConfigFormat(profile, device) === "uri" ? "Скачать TXT" : clientConfigFormat(profile, device) !== "mihomo" ? "Скачать JSON" : "Скачать YAML"}</button>{device.scope !== "common" && <button className="dangerButton" onClick={() => void removeProfileDevice(profile, device)} disabled={deleting} title="Удалить это устройство и его подключения">{deleting ? "Удаление…" : "Удалить устройство"}</button>}</nav>
                      </header>
                    {protocolsExpanded && <div className="mihomoProfileProtocolStats">{connections.map((connection) => { const item = profileStats[profile.id]?.connections?.[connection.id]; const online = Boolean(item?.active || item?.endpoint || Number(item?.active_connections || 0)); return <div key={connection.id}><span className={`protocol-${connection.component}${online ? " online" : ""}`}>{channelShort[connection.component] || "CH"}<i /></span><p><b>{connection.name}</b><small>↓ {bytes(item?.rx_bytes || 0)} · ↑ {bytes(item?.tx_bytes || 0)}</small>{item?.handshake_age_s != null && <em>Связь {duration(item.handshake_age_s)} назад</em>}</p></div>; })}{!connections.length && <p className="mihomoConnectionEmpty">Для устройства пока нет подключений.</p>}</div>}
                    </section>;
                  })}
                  {!devices.length && <p className="mihomoConnectionEmpty">Подключите приложение с HWID по ссылке профиля или добавьте общий sing-box для клиентов без HWID.</p>}
                </div>}
              </section>
            ); })}
            {!profiles.length && <Empty title="Профилей пока нет" text="Установите компонент и соберите первое подключение в профиле." />}
          </div>
          <section className="mihomoClientGuide">
            <header><p className="eyebrow">CLIENT SETUP</p><h3>Настройка и подключение</h3><p>Одна подписка поддерживает несколько устройств. Каждое приложение с включённой отправкой HWID регистрирует отдельное устройство; без HWID используется общий набор. Формат определяется автоматически, а для Karing и Hiddify доступен выбор YAML/JSON в настройках зарегистрированного устройства.</p></header>
            <ol><li><b>1</b><span>Нажмите «Скопировать подписку» у нужного профиля.</span></li><li><b>2</b><span>В клиенте добавьте удалённый профиль по URL.</span></li><li><b>3</b><span>После изменений обновите профиль в клиенте.</span></li></ol>
            <details className="mihomoClientAppsDetails"><summary>Приложения для подключения</summary><div className="mihomoClientApps">
              <a href="https://github.com/clash-verge-rev/clash-verge-rev/releases" target="_blank" rel="noreferrer"><small>PC · WINDOWS / LINUX</small><strong>Clash Verge Rev</strong><span>Официальные релизы ↗</span></a>
              <a href="https://apps.apple.com/us/app/clash-mi/id6744321968" target="_blank" rel="noreferrer"><small>IPHONE / IPAD</small><strong>Clash Mi</strong><span>Скачать в App Store ↗</span></a>
              <a href="https://github.com/MetaCubeX/ClashMetaForAndroid/releases" target="_blank" rel="noreferrer"><small>ANDROID</small><strong>Clash Meta for Android</strong><span>Официальные APK-релизы ↗</span></a>
              <a href="https://github.com/clash-verge-rev/clash-verge-rev/releases" target="_blank" rel="noreferrer"><small>MAC · INTEL / APPLE SILICON</small><strong>Clash Verge Rev</strong><span>Скачать DMG ↗</span></a>
              <a href="https://apps.apple.com/us/app/karing/id6472431552" target="_blank" rel="noreferrer"><small>IPHONE / IPAD · YAML / JSON</small><strong>Karing</strong><span>Для отдельного устройства включите X-HWID ↗</span></a>
              <a href="https://github.com/hiddify/hiddify-app" target="_blank" rel="noreferrer"><small>МОБИЛЬНЫЕ / ПК · YAML / JSON</small><strong>Hiddify</strong><span>Официальные загрузки ↗</span></a>
              <a href="https://apps.apple.com/us/app/sing-box-mt/id6785326793" target="_blank" rel="noreferrer"><small>IPHONE / IPAD · JSON</small><strong>sing-box MT</strong><span>Общий профиль без HWID ↗</span></a>
              <a href="https://sing-box.sagernet.org/clients/" target="_blank" rel="noreferrer"><small>ANDROID / APPLE / ПК · JSON</small><strong>sing-box</strong><span>Официальные клиенты ↗</span></a>
              <a href="https://github.com/KaringX/karing/releases" target="_blank" rel="noreferrer"><small>Windows / macOS / Linux · YAML / JSON</small><strong>Karing</strong><span>Для отдельного устройства включите X-HWID ↗</span></a>
              <a href="https://github.com/pluralplay/FlClashX/releases" target="_blank" rel="noreferrer"><small>Windows / macOS / Linux · YAML · HWID</small><strong>FlClashX</strong><span>Оставьте включённым «Отправлять HWID» ↗</span></a>
              <a href="https://github.com/legiz-ru/Prizrak-Box/releases" target="_blank" rel="noreferrer"><small>Windows / macOS / Linux · YAML · HWID</small><strong>Prizrak-Box</strong><span>Оставьте включённой отправку HWID ↗</span></a>
              <a href="https://github.com/chen08209/FlClash/releases" target="_blank" rel="noreferrer"><small>ANDROID / ПК · YAML</small><strong>FlClash</strong><span>Официальные релизы ↗</span></a>
              <a href="https://github.com/mihomo-party-org/clash-party/releases" target="_blank" rel="noreferrer"><small>ПК · YAML</small><strong>Clash Party</strong><span>Ранее Mihomo Party ↗</span></a>
              <a href="https://github.com/libnyanpasu/clash-nyanpasu/releases" target="_blank" rel="noreferrer"><small>ПК · YAML · ЯДРО MIHOMO</small><strong>Clash Nyanpasu</strong><span>Официальные релизы ↗</span></a>
              <a href="https://github.com/coolcoala/koala-clash/releases" target="_blank" rel="noreferrer"><small>Windows / macOS / Linux · YAML · HWID</small><strong>Koala Clash</strong><span>Официальные релизы ↗</span></a>
              <a href="https://github.com/xishang0128/sparkle/releases" target="_blank" rel="noreferrer"><small>ПК · YAML</small><strong>Sparkle</strong><span>Официальные релизы ↗</span></a>
              <a href="https://happ.info/" target="_blank" rel="noreferrer"><small>МОБИЛЬНЫЕ / ПК · XRAY JSON · HWID</small><strong>Happ</strong><span>VLESS и Shadowsocks ↗</span></a>
              <a href="https://github.com/Leadaxe/singbox-launcher" target="_blank" rel="noreferrer"><small>ПК · JSON</small><strong>Sing-Box Launcher</strong><span>Отправка HWID настраивается в клиенте ↗</span></a>
            </div></details>
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
          <section className="mihomoDnsAdvanced"><header><div><b>Дополнительная обработка</b><small>Параметры попадут непосредственно в DNS-секцию профилей.</small></div></header><div>{([{ key: "ipv6", code: "IPv6", title: "IPv6", hint: "Возвращать записи AAAA" }, { key: "prefer_h3", code: "H3", title: "HTTP/3", hint: "Для совместимых DoH-серверов" }] as const).map((feature) => {
            const enabled = Boolean(dnsDraft[feature.key]);
            return <button type="button" key={feature.key} aria-pressed={enabled} className={enabled ? "is-selected" : ""} onClick={() => updateDnsDraft(feature.key, !enabled)}><span>{feature.code}</span><p><b>{feature.title}</b><small>{feature.hint}</small></p><i>{enabled ? "Включено" : "Выключено"}</i></button>;
          })}<label><span><b>Кэш DNS</b><small>Алгоритм вытеснения записей</small></span><select value={String(dnsDraft.cache_algorithm || "lru")} onChange={(event) => updateDnsDraft("cache_algorithm", event.target.value)}><option value="lru">LRU · совместимый</option><option value="arc">ARC · адаптивный</option></select></label></div>{String(dnsDraft.enhanced_mode || "fake-ip") === "fake-ip" && <label className="mihomoDnsFakeIp"><span><b>Исключения Fake IP</b><small>По одному домену или маске на строку. Для них клиент получит реальный IP.</small></span><textarea rows={4} value={String(dnsDraft.fake_ip_filter || "")} spellCheck={false} placeholder={"*.lan\n*.local"} onChange={(event) => updateDnsDraft("fake_ip_filter", event.target.value)} /></label>}</section>
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
            <aside><header><b>Библиотека правил</b><small>Выберите набор для редактирования</small><input aria-label="Поиск правил" value={ruleSearch} placeholder="Найти правило…" onChange={(event) => setRuleSearch(event.target.value)} /></header><div className="mihomoRuleLibrary">{visibleRuleGroups.map((section) => <section key={section.group}><h4>{section.group}</h4>{section.rules.map((item) => { const profileCount = profiles.filter((profile) => Boolean(profile.routing?.[item.key])).length; return <button type="button" key={item.key} className={activeRuleList === item.key ? "is-active" : ""} onClick={() => setActiveRuleList(item.key)}><span>{item.code}</span><p><b>{item.title}</b><small>{item.text}</small></p><i>{profileCount} проф.</i></button>; })}</section>)}{(visiblePersonalRules.length > 0 || !normalizedRuleSearch) && <section className="mihomoPersonalRuleLibrary"><h4>Персональные</h4>{visiblePersonalRules.map((rule) => { const profileCount = profiles.reduce((count, profile) => count + (profile.devices || []).filter((device) => (device.personal_rule_ids || []).includes(rule.id)).length, 0); return <button type="button" key={rule.id} className={activeRuleList === `personal:${rule.id}` ? "is-active" : ""} onClick={() => setActiveRuleList(`personal:${rule.id}`)}><span>{rule.code}</span><p><b>{rule.title}</b><small>{rule.kind === "process" ? "Приложения в TUN" : "Сайты"} · {rule.entries.length} знач.</small></p><i>{profileCount} устр.</i></button>; })}<button type="button" className="mihomoRuleLibraryAdd" onClick={() => openPersonalRuleEditor()}>+ Создать персональное правило</button></section>}</div></aside>
            <article>
              {selectedPersonalRule ? <>
                <header className="mihomoRuleEditorHead"><div><p className="eyebrow">PERSONAL RULE</p><h3>{selectedPersonalRule.title}</h3><p>{selectedPersonalRule.description || (selectedPersonalRule.kind === "process" ? "Приложения, перечисленные ниже, будут направлены через выбранный маршрут в TUN." : "Домены, перечисленные ниже, будут направлены через выбранный маршрут.")}</p></div><span>{selectedPersonalRule.entries.length} знач.</span></header>
                <div className="mihomoPersonalRuleMeta"><span>{selectedPersonalRule.kind === "process" ? "Приложения в TUN" : "Сайты"}</span><span>{selectedPersonalRule.target}</span></div>
                <div className="mihomoPersonalRuleEntries">{selectedPersonalRule.entries.map((entry) => <code key={entry}>{entry}</code>)}</div>
                <footer className="mihomoRuleEditorActions"><span>Правило доступно для включения в настройках каждого устройства.</span><nav><button type="button" className="ghostButton" onClick={() => openPersonalRuleEditor(selectedPersonalRule)}>Изменить</button><button type="button" className="dangerButton" disabled={busy === `personal-rule:${selectedPersonalRule.id}`} onClick={() => void removePersonalRule(selectedPersonalRule)}>Удалить</button></nav></footer>
              </> : activeRuleList === "direct_games_udp_enabled" ? <>
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

          <details className="mihomoAdvancedRules"><summary><span><b>Глобальные дополнительные правила</b><small>Для всех конфигураций Mihomo</small></span><i>Открыть редактор</i></summary><label><span>По одному правилу Mihomo на строку</span><small>Эти правила применяются ко всем профилям. Для выборочного включения создайте правило в категории «Персональные».</small><textarea rows={7} value={String(routingDraft.rules || "")} placeholder={"DOMAIN-SUFFIX,example.com,DIRECT\nDOMAIN,api.example.com,DIRECT"} onChange={(event) => updateRoutingDraft("rules", event.target.value)} /></label></details>
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
                      {(field.options || []).filter((option) => commonDevice || !["transport", "cdn_transport", "tls_transport"].includes(field.key) || activeCapabilities.transports.includes(typeof option === "string" ? option : option.value)).map((option) => {
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
              <div className="mihomoProfileHeading"><div><p className="eyebrow">MIHOMO PROFILE</p><h2>{profileDialog === "new" ? "Новый профиль" : "Настройка профиля"}</h2></div></div>
              <button type="button" className="iconButton" onClick={() => setProfileDialog(null)}>x</button>
            </header>
            <div className="mihomoProfileWorkspace">
              <aside className="mihomoProfileRail">
                <header><small>КОНФИГУРАЦИЯ</small><b>Параметры профиля</b></header>
                <section className="mihomoDeviceBuilder">
                  <div className="mihomoCommonSettings">{profileDevices.filter((device) => device.scope === "common").slice(0, 1).map((device) => <button key={device.id} type="button" className={activeDeviceId === device.id ? "active" : ""} onClick={() => { setProfileStrategyTouched(Boolean(device.routing?.strategy)); setActiveDeviceId(device.id); }}><i>ALL</i><span><b>Параметры профиля</b></span></button>)}</div>
                  <header><div><b>Устройства</b><small>Устройства определяются по HWID и приложению. Без HWID — общий профиль.</small></div></header>
                  <div>{profileDevices.filter((device) => device.scope !== "common").map((device) => <button key={device.id} type="button" className={activeDeviceId === device.id ? "active" : ""} onClick={() => { setProfileStrategyTouched(Boolean(device.routing?.strategy)); setActiveDeviceId(device.id); }}><i>{devicePlatformMeta(device).code}</i><span><b><DeviceClientBadge device={device} />{device.name}</b><small>{profileConnections.filter((connection) => connection.device_id === device.id).length} подключений · {deviceSystemLabel(device)}</small></span><em title="Удалить устройство" onClick={(event) => { event.stopPropagation(); const next = profileDevices.filter((item) => item.id !== device.id); setProfileDevices(next); setProfileConnections((current) => current.filter((connection) => connection.device_id !== device.id)); if (activeDeviceId === device.id) setActiveDeviceId(next.find((item) => item.scope === "common")?.id || next[0].id); }}>×</em></button>)}</div>
                  {!profileDevices.some((device) => device.scope !== "common") && <p className="mihomoConnectionEmpty">Подключите клиент с HWID по ссылке профиля или добавьте общий sing-box.</p>}
                </section>
              </aside>
              <main ref={profileCanvasRef} className="mihomoProfileCanvas">
            <section className="mihomoProfileEditorHead"><div><small>ВЫБРАННАЯ КОНФИГУРАЦИЯ</small><b>{profileDevices.find((device) => device.id === activeDeviceId)?.scope === "common" ? "Параметры профиля" : profileDevices.find((device) => device.id === activeDeviceId)?.name}</b></div><nav aria-label="Раздел настроек"><button type="button" className={profileStep === 1 ? "is-active" : ""} onClick={() => setProfileStep(1)}>Общее</button><button type="button" className={profileStep === 2 ? "is-active" : ""} onClick={() => setProfileStep(2)}>Маршрутизация</button><button type="button" className={profileStep === 3 ? "is-active" : ""} onClick={() => setProfileStep(3)}>Подключения <i>{profileConnections.filter((connection) => connection.device_id === activeDeviceId).length}</i></button></nav></section>
            <section className="mihomoProfileName mihomoProfileGeneral">
              {profileDevices.find((device) => device.id === activeDeviceId)?.scope === "common" ? <>
                <header><div><b>Название профиля</b><small>Отображается в панели и помогает отличать подписки.</small></div></header>
                <label><span>Название профиля</span><input value={profileName} onChange={(event) => setProfileName(event.target.value)} required maxLength={80} placeholder="Например, Основной" /></label>
              </> : <>
                <header><div><b>Устройство</b><small>Индивидуальные подключения, маршрутизация и файл для клиента.</small></div></header>
                <p>{activeProfileDevice?.manual ? "Общая конфигурация sing-box для всех устройств этого профиля. Без привязки к HWID; трафик и подключения общие." : "Устройство зарегистрировано по HWID. Приложение: " + (activeProfileDevice?.client_name || "не определено")}</p>
                <label><span>Название устройства</span><input value={profileDevices.find((device) => device.id === activeDeviceId)?.name || ""} maxLength={80} onChange={(event) => setProfileDevices((current) => current.map((device) => device.id === activeDeviceId ? { ...device, name: event.target.value } : device))} /></label>
              </>}
            </section>
            {visibleProfileStrategies.length > 0 && <section className="mihomoProfileStrategy"><header><div><b>Стратегия устройства</b><small>Отдельная группа подключений для выбранного устройства.</small></div><span>{profileStrategies.find((item) => item.value === String(activeProfileRouting.strategy || ""))?.title}</span></header><div>{visibleProfileStrategies.map((strategy) => { const selected = String(activeProfileRouting.strategy || "") === strategy.value; return <button key={strategy.value || "inherit"} type="button" className={selected ? "is-selected" : ""} onClick={() => setProfileStrategy(strategy.value)}><i>{strategy.code}</i><span><b>{strategy.title}</b><small>{strategy.text}</small></span></button>; })}</div></section>}
            {activeProfileDevice?.scope !== "common" && <section className="mihomoProfileName mihomoProfileGeneral"><header><div><b>Формат при следующей загрузке</b><small>{activeProfileDevice?.manual ? "Общий sing-box JSON, без HWID." : "Формат сохраняется для сочетания HWID и приложения."}</small></div></header>
              {activeProfileDevice?.scope === "hwid" && (activeProfileDevice.supported_formats?.length || 0) > 1 ? <label><span>Файл для клиента</span><select value={String(activeProfileRouting.client_config_format || "mihomo")} onChange={(event) => setProfileDevices((current) => current.map((device) => device.id === activeDeviceId ? { ...device, routing: compatibleClientRouting(device.routing || {}, event.target.value, device.os || "unknown", device.manual ? undefined : device.client_name) } : device))}>{activeProfileDevice.supported_formats?.map((format) => <option key={format} value={format}>{clientCapabilities(format).label}</option>)}</select><small>После сохранения обновите подписку. Если клиент не принимает новый формат — удалите подписку и добавьте ту же ссылку заново. Устройство сохранится, если приложение и HWID не изменятся.</small></label> : <p>{activeCapabilities.label}</p>}
            </section>}
            <ProfileProtection client={activeProfileDevice?.manual ? undefined : activeProfileDevice?.client_name} routing={activeProfileRouting} connections={profileConnections.filter((connection) => connection.device_id === activeDeviceId)} modules={modules} common={activeProfileDevice?.scope === "common"} onChange={setProfileRoutingValue} />
            {liveDialogProfile?.protection_status?.[activeDeviceId]?.encryption_pending && <p className="mihomoMessage is-error" role="status">Сохранённая настройка шифрования ещё не применена к подключениям. Сохраните профиль, затем обновите подписку в клиенте.</p>}
            {transitionMessage && <p className="mihomoMessage" role="status">{transitionMessage}{activeDeviceId === liveDialogProfile?.common_device_id && " Для клиентов без HWID переход общий: обновление подписки завершает ожидание для всего общего пула."}</p>}
            <section className="mihomoProfileRules"><header><div><b>Правила устройства</b><small>Включаются только в конфигурации выбранного устройства.</small></div><span>{visibleProfileRules.filter((rule) => Boolean(activeProfileRouting[rule.key])).length + (activeProfileDevice?.personal_rule_ids || []).length} из {visibleProfileRules.length + personalRules.length}</span></header><div>{visibleProfileRules.map((rule) => { const selected = Boolean(activeProfileRouting[rule.key]); return <button type="button" key={rule.key} aria-pressed={selected} className={`mihomoProfileRuleButton${selected ? " is-enabled" : ""}`} onClick={() => toggleProfileRule(rule.key, !selected)}><i>{rule.code}</i><span><b>{rule.title}</b><small>{rule.text}</small></span></button>; })}</div>{personalRules.length > 0 && <div className="mihomoPersonalProfileRules"><h4>Персональные</h4>{personalRules.map((rule) => { const selected = (activeProfileDevice?.personal_rule_ids || []).includes(rule.id); return <button type="button" key={rule.id} aria-pressed={selected} className={`mihomoProfileRuleButton${selected ? " is-enabled" : ""}`} onClick={() => togglePersonalRule(rule.id)}><i>{rule.code}</i><span><b>{rule.title}</b><small>{rule.kind === "process" ? "Приложения в TUN" : "Сайты"} · {rule.entries.length} знач.</small></span></button>; })}</div>}</section>
            <section className="mihomoPresetPicker">
              <header><div><b>Создать подключения из пресета</b><small>Готовый набор заменит подключения выбранной конфигурации.</small></div><button type="button" onClick={() => { setProfileDialog(null); setView("routing"); }}>Настроить пресеты</button></header>
              <div>{profilePresets.filter((preset) => commonDevice || preset.components.every((item) => {
                const id = item.id === "$primary" ? String(routingPolicy?.values.preset_primary || "transport-reality") : item.id === "$fallback" ? String(routingPolicy?.values.preset_fallback || "transport-awg") : item.id;
                const protocolModule = modules.find((entry) => entry.id === id);
                const settings = Object.fromEntries((protocolModule?.connection_settings || []).map((field) => [field.key, field.default]));
                settings.route_mode = item.tls ? "tls" : item.cdn ? "cdn" : "direct";
                if (item.transport) settings[item.tls ? "tls_transport" : item.cdn ? "cdn_transport" : "transport"] = item.transport;
                return clientConnectionSupported({ component: id, settings }, activeProfileRouting.client_config_format, activeProfileDevice?.manual ? undefined : activeProfileDevice?.client_name);
              })).map((preset) => {
                const needsCdn = preset.components.some((item) => item.cdn);
                const needsTls = preset.components.some((item) => item.tls);
                const missingCdn = needsCdn && !String(routingPolicy?.values.preset_cdn_domain || "").trim();
                const missingTls = needsTls && !String(routingPolicy?.values.preset_tls_domain || "").trim();
                const missingModules = preset.components.filter((definition) => !modules.some((item) => item.id === definition.id && item.installed));
                const unavailable = missingCdn || missingTls || missingModules.length > 0;
                return <button key={preset.id} type="button" disabled={unavailable} onClick={() => applyProfilePreset(preset)}><i>+</i><span><b>{preset.name}</b><small>{missingModules.length ? `Сначала установите: ${missingModules.map((item) => item.id.replace("transport-", "")).join(", ")}` : missingCdn && missingTls ? "Укажите CDN- и TLS-домены в Настройках" : missingCdn ? "Укажите CDN-домен в Настройках" : missingTls ? "Укажите TLS-домен в Настройках" : "Создать готовый набор"}</small></span></button>;
              })}</div>
            </section>
            <section className="mihomoConnectionBuilder">
              <header><div><b>Подключения устройства</b><small>Каналы попадут только в подписку выбранного устройства.</small></div><span className="mihomoStrategyBadge">{profileStrategies.find((item) => item.value === String(activeProfileRouting.strategy || ""))?.code} · {profileStrategies.find((item) => item.value === String(activeProfileRouting.strategy || ""))?.title}</span></header>
              <div className="mihomoConnectionAdd">
                {installedChannels.filter((module) => commonDevice || activeCapabilities.components.includes(module.id)).flatMap((module) => {
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
                  const supported = commonDevice || clientConnectionSupported(connection, activeProfileRouting.client_config_format, activeProfileDevice?.manual ? undefined : activeProfileDevice?.client_name);
                  const schema = commonDevice || activeCapabilities.components.includes(connection.component) ? protocolModule?.connection_settings || [] : [];
                  const vlessRoute = connection.component === "transport-reality" ? String(connection.settings.route_mode || (connection.settings.cdn_enabled ? "both" : "direct")) : "";
                  return <details key={connection.id} className={`mihomoConnectionCard protocol-${connection.component}${vlessRoute ? ` is-vless-${vlessRoute}` : ""}`}>
                    <summary>
                      <span className={`protocol-${connection.component}`}>{channelShort[connection.component] || "CH"}</span>
                      <div><b>{vlessRoute === "cdn" ? "VLESS CDN" : vlessRoute === "tls" ? "VLESS TLS" : vlessRoute === "direct" ? "VLESS REALITY" : vlessRoute === "both" ? "VLESS + CDN · прежний формат" : protocolModule?.name || connection.component}</b><small>{vlessRoute === "cdn" ? "Через CDN-домен" : vlessRoute === "tls" ? "Прямой домен с TLS" : vlessRoute === "direct" ? "Прямое REALITY-подключение" : vlessRoute === "both" ? "Можно заменить двумя независимыми подключениями" : `Подключение ${index + 1}`}</small></div>
                      <button type="button" className="mihomoConnectionQuickDelete" title="Удалить подключение" aria-label={`Удалить ${connection.name}`} onClick={(event) => { event.preventDefault(); event.stopPropagation(); setProfileConnections((current) => current.filter((item) => item.id !== connection.id)); }}><span aria-hidden="true">×</span></button><span className="mihomoConnectionChevron">›</span>
                    </summary>
                    <label><span>Название в профиле</span><input value={connection.name} maxLength={80} onChange={(event) => updateProfileConnection(connection.id, { name: event.target.value })} /></label>
                    {!supported && <p>Подключение несовместимо с форматом клиента. Удалите его или выберите поддерживаемый формат.</p>}
                    <div className="mihomoConnectionFields">
                      {schema.filter((field) => {
                        if (["route_mode", "cdn_enabled", "privacy_mode", "cdn_ech"].includes(field.key)) return false;
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
                          {(field.options || []).filter((option) => commonDevice || !["transport", "cdn_transport", "tls_transport"].includes(field.key) || activeCapabilities.transports.includes(typeof option === "string" ? option : option.value)).map((option) => { const value = typeof option === "string" ? option : option.value; const label = typeof option === "string" ? option : option.label; return <option key={value} value={value}>{label}</option>; })}
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
      {createdProfile && <div className="mihomoDialogBackdrop"><div className="mihomoDialog mihomoCreatedProfile">
        <header><div><p className="eyebrow">PROFILE READY</p><h2>Профиль «{createdProfile.name}» готов</h2></div><button className="iconButton" onClick={() => setCreatedProfile(null)}>x</button></header>
        <div className="mihomoReadyDevices">{readyDevices.map((device) => {
          const format = clientConfigFormat(createdProfile, device);
          const singbox = format === "singbox";
          const happ = format === "xray";
          const karing = device.scope !== "common" && /karing/i.test(`${device.client_name || ""} ${device.user_agent || ""}`);
          const hiddify = device.scope !== "common" && /hiddify/i.test(`${device.client_name || ""} ${device.user_agent || ""}`);
          return <article key={device.id}>
            <Image src={device.qr} alt={`QR импорта ${device.name}`} width={240} height={240} unoptimized />
            <div><b>{device.name} · {device.scope === "common" ? "Универсальная подписка" : clientCapabilities(format).label}</b>
              {device.scope !== "common" && <small>{device.manual ? "Общий JSON для всех устройств профиля, без привязки к HWID." : "Устройство привязано к HWID."}</small>}
              <small>{device.scope === "common" ? "Сканируйте QR в клиенте: новые устройства зарегистрируются, существующие сохранят свои настройки. Формат файла определяется по приложению." : happ ? "Добавьте ссылку подписки в Happ или скачайте Xray JSON." : singbox ? "Отсканируйте QR камерой iPhone или откройте в sing-box. Для импорта из файла скачайте JSON." : "Отсканируйте QR-код в приложении Mihomo или скачайте YAML."}</small>
              <nav>{(singbox || karing || hiddify) && <a className="primaryButton" href={clientImportUrl(device.subscription, `${createdProfile.name} · ${device.name}`, singbox ? "singbox" : "mihomo", karing ? "Karing" : hiddify ? "Hiddify" : "")}>{karing ? "Открыть в Karing" : hiddify ? "Открыть в Hiddify" : "Открыть в sing-box"}</a>}
                <button className="primaryButton" disabled={busy === `download:${createdProfile.id}`} onClick={() => void downloadConfig(createdProfile, device)}>{format === "uri" ? "Скачать TXT" : singbox || happ ? "Скачать JSON" : "Скачать YAML"}</button>
                <button onClick={() => void copyText(device.subscription)}>Скопировать ссылку на файл</button>
              </nav>
            </div>
          </article>;
        })}{!readyDevices.length && <div className="mihomoHint">{readyFailed ? <><span>Не удалось подготовить ссылки устройств.</span><button onClick={() => setCreatedProfile({ ...createdProfile })}>Повторить</button></> : "Подготавливаем QR-коды устройств…"}</div>}</div>
        <footer><button className="primaryButton" onClick={() => setCreatedProfile(null)}>Готово</button></footer>
      </div></div>}
      {personalRuleDialog && <div className="mihomoDialogBackdrop mihomoPersonalRuleBackdrop">
        <form className="mihomoDialog mihomoPersonalRuleDialog" onSubmit={savePersonalRule}>
          <header><div><p className="eyebrow">PERSONAL RULE</p><h2>{personalRuleDialog === "new" ? "Новое персональное правило" : "Изменить персональное правило"}</h2><small>Правило появится отдельной иконкой в категории «Персональные» и будет доступно всем устройствам.</small></div><button type="button" className="iconButton" aria-label="Закрыть" onClick={() => setPersonalRuleDialog(null)}>×</button></header>
          <div className="mihomoPersonalRuleForm">
            <label><span>Код и название</span><div className="mihomoPersonalRuleFormRow"><input value={personalRuleDraft.code} maxLength={8} placeholder="GAME" onChange={(event) => setPersonalRuleDraft((current) => ({ ...current, code: event.target.value }))} required /><input value={personalRuleDraft.title} maxLength={80} placeholder="Например, Игровой трафик" onChange={(event) => setPersonalRuleDraft((current) => ({ ...current, title: event.target.value }))} required /></div></label>
            <label><span>Тип правила</span><select value={personalRuleDraft.kind} onChange={(event) => setPersonalRuleDraft((current) => ({ ...current, kind: event.target.value as PersonalRule["kind"] }))}><option value="domain">Сайты / домены</option><option value="process">Приложения в TUN</option></select></label>
            <label><span>Маршрут</span><select value={personalRuleDraft.target} onChange={(event) => setPersonalRuleDraft((current) => ({ ...current, target: event.target.value as PersonalRule["target"] }))}><option value="GATE.312">Через VPN (GATE.312)</option><option value="DIRECT">Напрямую</option><option value="REJECT">Заблокировать</option></select></label>
            <label><span>Значения — по одному в строке</span><textarea rows={8} value={personalRuleDraft.entries} placeholder={personalRuleDraft.kind === "process" ? "game.exe\ncom.example.game" : "example.com\napi.example.com"} onChange={(event) => setPersonalRuleDraft((current) => ({ ...current, entries: event.target.value }))} required /></label>
            <label><span>Описание <small>необязательно</small></span><input value={personalRuleDraft.description} maxLength={160} placeholder="Когда применять это правило" onChange={(event) => setPersonalRuleDraft((current) => ({ ...current, description: event.target.value }))} /></label>
          </div>
          <footer><button type="button" className="ghostButton" onClick={() => setPersonalRuleDialog(null)}>Отмена</button><span>После сохранения правило включается отдельно для каждого устройства.</span><button type="submit" className="primaryButton" disabled={busy === "personal-rule"}>Сохранить правило</button></footer>
        </form>
      </div>}
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
