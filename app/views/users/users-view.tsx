"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { createPortal } from "react-dom";

type Capability = {
  id: string;
  name: string;
  installed: boolean;
  active: boolean;
  kind: "transport" | "aggregator";
  version?: string;
};

type MihomoModule = {
  id: string;
  transport: string;
  name: string;
  installed: boolean;
  active: boolean;
  version?: string;
};

type RoutingPolicy = {
  mode: "rule" | "global";
  strategy: "fallback" | "url-test" | "select";
  test_url: string;
  interval: number;
  rules: string;
};

type Capabilities = {
  items: Capability[];
  installed_transports: string[];
  mihomo_installed: boolean;
  mihomo_candidate_transports: string[];
  mihomo: {
    manager_available: boolean;
    manager_error?: string;
    modules: MihomoModule[];
    installed_transports: string[];
  };
  detected_at: string;
};

type DirectConnection = {
  id: string;
  kind: "direct";
  protocol: string;
  backend_client_id: string;
  filename: string;
  created_at: string;
  backend_present: boolean;
  status: "ready" | "missing";
  has_export: boolean;
};

type SmartProfile = {
  id: string;
  backend_profile_id: string;
  transports: string[];
  channels: string[];
  strategy: "fallback" | "url-test" | "select";
  routing?: RoutingPolicy;
  created_at: string;
  backend_present: boolean;
  status: "ready" | "missing";
};

type AccessDevice = {
  id: string;
  name: string;
  platform: string;
  platform_label: string;
  enabled: boolean;
  transports: string[];
  routing: RoutingPolicy;
  available_transports: string[];
  missing_transports: string[];
  mihomo_ready: boolean;
  mihomo_supported_transports: string[];
  mihomo_missing_transports: string[];
  provisioning: "managed";
  connections: DirectConnection[];
  smart_profile: SmartProfile | null;
  connection_count: number;
  healthy_connection_count: number;
};

type AccessUser = {
  id: string;
  name: string;
  note?: string;
  enabled: boolean;
  created_at: string;
  devices: AccessDevice[];
  device_count: number;
  connection_count: number;
};

type AccessResponse = {
  beta: boolean;
  provisioning: "managed";
  users: AccessUser[];
  capabilities: Capabilities;
  recovery?: {
    state_file_exists: boolean;
    backup_file_exists: boolean;
    smart_candidates: number;
    direct_candidates: number;
    available: boolean;
  };
};

type DeviceDraft = {
  userId: string;
  deviceId?: string;
  name: string;
  platform: string;
  transports: string[];
};

type DeviceTarget = { userId: string; deviceId: string };

type ExportPreview = {
  title: string;
  filename: string;
  config: string;
};

type RuntimeClient = {
  id: string;
  protocol?: string;
  handshake_age_s?: number | null;
  rx_bytes?: number;
  tx_bytes?: number;
  rx_bps?: number;
  tx_bps?: number;
  active_connections?: number;
  quality?: "stable" | "warning" | "error" | "offline" | string;
};

type SmartChannelRuntime = {
  endpoint?: string | null;
  handshake_age_s?: number | null;
  rx_bytes?: number;
  tx_bytes?: number;
  active?: boolean;
  active_connections?: number;
};

type RuntimeState = {
  direct: Record<string, RuntimeClient>;
  smart: Record<string, { id?: string; channels?: Record<string, SmartChannelRuntime> }>;
  checked_at: string;
};

type ConnectionTone = "gray" | "yellow" | "red" | "green";

type DeviceRuntimeSummary = {
  tone: ConnectionTone;
  label: string;
  configured: boolean;
  active: number;
  channels: number;
  rx_bytes: number;
  tx_bytes: number;
  last_activity_s: number | null;
};

type ConfirmationRequest = {
  title: string;
  message: string;
  confirmLabel: string;
  phrase?: string;
  danger?: boolean;
  resolve: (confirmed: boolean) => void;
};

const defaultRouting: RoutingPolicy = {
  mode: "rule",
  strategy: "fallback",
  test_url: "https://www.gstatic.com/generate_204",
  interval: 180,
  rules: "",
};

const transportLabels: Record<string, string> = {
  awg: "AmneziaWG",
  wg: "WireGuard",
  "vless-reality-xhttp": "VLESS Reality",
  shadowsocks: "Shadowsocks",
};

const transportCodes: Record<string, string> = {
  awg: "AWG",
  wg: "WG",
  "vless-reality-xhttp": "VRX",
  shadowsocks: "SS",
};

const platforms = [
  ["windows", "Windows"],
  ["ios", "iPhone / iPad"],
  ["android", "Android"],
  ["macos", "macOS"],
  ["linux", "Linux"],
  ["router", "Router"],
  ["other", "Другое"],
] as const;

const strategyLabels: Record<RoutingPolicy["strategy"], string> = {
  fallback: "Fallback",
  "url-test": "Лучший канал",
  select: "Ручной выбор",
};

function formatError(cause: unknown, fallback: string) {
  return cause instanceof Error ? cause.message : fallback;
}

function cloneRouting(value?: Partial<RoutingPolicy> | null): RoutingPolicy {
  return {
    ...defaultRouting,
    ...(value || {}),
    interval: Number(value?.interval || defaultRouting.interval),
  };
}

function formatBytes(value: number) {
  const amount = Math.max(0, Number(value) || 0);
  if (amount < 1024) return `${Math.round(amount)} Б`;
  const units = ["КБ", "МБ", "ГБ", "ТБ"];
  let current = amount / 1024;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) { current /= 1024; index += 1; }
  const precision = current >= 100 ? 0 : current >= 10 ? 1 : 2;
  return `${current.toFixed(precision)} ${units[index]}`;
}

function formatAge(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "—";
  if (value < 60) return `${Math.max(0, Math.round(value))} с`;
  if (value < 3600) return `${Math.round(value / 60)} мин`;
  if (value < 86400) return `${Math.round(value / 3600)} ч`;
  return `${Math.round(value / 86400)} д`;
}

function runtimeChannelActive(item?: RuntimeClient | SmartChannelRuntime | null) {
  if (!item) return false;
  if (typeof item.handshake_age_s === "number") return item.handshake_age_s < 180;
  if (Number(item.active_connections || 0) > 0) return true;
  if ("quality" in item) return item.quality === "stable" || item.quality === "warning";
  return false;
}

function runtimeChannelError(item?: RuntimeClient | SmartChannelRuntime | null) {
  if (!item) return false;
  if ("quality" in item && item.quality === "error") return true;
  return "active" in item && item.active === false;
}

function summarizeDeviceRuntime(device: AccessDevice, runtime: RuntimeState): DeviceRuntimeSummary {
  const direct = device.connections
    .map((connection) => runtime.direct[connection.backend_client_id])
    .filter((item): item is RuntimeClient => Boolean(item));
  const smart = device.smart_profile
    ? runtime.smart[device.smart_profile.backend_profile_id]?.channels || {}
    : {};
  const smartChannels = Object.values(smart);
  const channels = [...direct, ...smartChannels];
  const configured = Boolean(device.connections.length || device.smart_profile);
  const expectedChannels = device.connections.length + (device.smart_profile?.transports.length || 0);
  const missing = device.connections.some((item) => item.status === "missing") || device.smart_profile?.status === "missing";
  const errored = channels.some((item) => runtimeChannelError(item));
  const active = channels.filter((item) => runtimeChannelActive(item)).length;
  const ages = channels
    .map((item) => typeof item.handshake_age_s === "number" ? item.handshake_age_s : null)
    .filter((item): item is number => item !== null);
  const rx = channels.reduce((sum, item) => sum + Number(item.rx_bytes || 0), 0);
  const tx = channels.reduce((sum, item) => sum + Number(item.tx_bytes || 0), 0);

  if (missing || errored) return { tone: "red", label: "Нужна проверка", configured, active, channels: expectedChannels, rx_bytes: rx, tx_bytes: tx, last_activity_s: ages.length ? Math.min(...ages) : null };
  if (active > 0) return { tone: "green", label: "Онлайн", configured, active, channels: expectedChannels, rx_bytes: rx, tx_bytes: tx, last_activity_s: ages.length ? Math.min(...ages) : null };
  if (configured) return { tone: "yellow", label: "Ожидает подключения", configured, active, channels: expectedChannels, rx_bytes: rx, tx_bytes: tx, last_activity_s: ages.length ? Math.min(...ages) : null };
  return { tone: "gray", label: "Не настроено", configured, active: 0, channels: 0, rx_bytes: 0, tx_bytes: 0, last_activity_s: null };
}

function ModalPortal({ children }: { children: ReactNode }) {
  if (typeof document === "undefined") return null;
  return createPortal(children, document.body);
}

export function AccessProfilesBeta({ token }: { token: string }) {
  const [data, setData] = useState<AccessResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busyKey, setBusyKey] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [createUserOpen, setCreateUserOpen] = useState(false);
  const [newUser, setNewUser] = useState({ name: "", note: "" });
  const [deviceDraft, setDeviceDraft] = useState<DeviceDraft | null>(null);
  const [deviceTarget, setDeviceTarget] = useState<DeviceTarget | null>(null);
  const [routingDraft, setRoutingDraft] = useState<RoutingPolicy>(cloneRouting());
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [exportPreview, setExportPreview] = useState<ExportPreview | null>(null);
  const [confirmation, setConfirmation] = useState<ConfirmationRequest | null>(null);
  const [confirmationInput, setConfirmationInput] = useState("");
  const [runtimeStats, setRuntimeStats] = useState<RuntimeState>({ direct: {}, smart: {}, checked_at: "" });

  const api = useCallback(async (path: string, init?: RequestInit) => {
    const response = await fetch(`/api/access-beta${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Basic ${token}`,
        ...(init?.headers || {}),
      },
    });
    if (!response.ok) {
      const raw = await response.text();
      let detail = raw;
      try { detail = (JSON.parse(raw) as { detail?: string }).detail || raw; } catch { /* plain API error */ }
      throw new Error(detail || `Ошибка ${response.status}`);
    }
    return response.json();
  }, [token]);

  const apiText = useCallback(async (path: string) => {
    const response = await fetch(`/api/access-beta${path}`, {
      headers: { Authorization: `Basic ${token}` },
    });
    if (!response.ok) {
      const raw = await response.text();
      let detail = raw;
      try { detail = (JSON.parse(raw) as { detail?: string }).detail || raw; } catch { /* plain API error */ }
      throw new Error(detail || `Ошибка ${response.status}`);
    }
    return response.text();
  }, [token]);

  const panelApi = useCallback(async (path: string) => {
    const response = await fetch(`/api${path}`, { headers: { Authorization: `Basic ${token}` } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }, [token]);

  const loadRuntime = useCallback(async (source: AccessResponse) => {
    if (!token) return;
    const next: RuntimeState = { direct: {}, smart: {}, checked_at: new Date().toISOString() };
    try {
      const directPayload = await panelApi("/clients") as { items?: RuntimeClient[] };
      next.direct = Object.fromEntries((directPayload.items || []).filter((item) => item?.id).map((item) => [String(item.id), item]));
    } catch {
      // Runtime statistics are optional; profile management must keep working if monitoring is unavailable.
    }

    const profiles = source.users.flatMap((user) => user.devices)
      .map((device) => device.smart_profile?.backend_profile_id)
      .filter((id): id is string => Boolean(id));
    const uniqueProfiles = [...new Set(profiles)];
    const smartRows = await Promise.all(uniqueProfiles.map(async (profileId) => {
      try { return [profileId, await panelApi(`/mihomo/profiles/${encodeURIComponent(profileId)}/stats`)] as const; }
      catch { return [profileId, null] as const; }
    }));
    next.smart = Object.fromEntries(smartRows.filter((entry) => entry[1]));
    setRuntimeStats(next);
  }, [panelApi, token]);

  function askConfirmation(options: Omit<ConfirmationRequest, "resolve">): Promise<boolean> {
    setConfirmationInput("");
    return new Promise((resolve) => setConfirmation({ ...options, resolve }));
  }

  function closeConfirmation(confirmed: boolean) {
    const current = confirmation;
    if (!current) return;
    setConfirmation(null);
    setConfirmationInput("");
    current.resolve(confirmed);
  }

  const load = useCallback(async (quiet = false) => {
    if (!token) return;
    if (!quiet) setLoading(true);
    try {
      const next = await api("/users") as AccessResponse;
      setData(next);
      setError("");
    } catch (cause) {
      setError(formatError(cause, "Не удалось загрузить beta-профили"));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [api, token]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(true), 20000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!data) return;
    void loadRuntime(data);
  }, [data, loadRuntime]);

  const installedTransports = useMemo(
    () => data?.capabilities.items.filter((item) => item.kind === "transport" && item.installed) || [],
    [data],
  );
  const mihomo = data?.capabilities.items.find((item) => item.id === "mihomo");
  const mihomoModules = data?.capabilities.mihomo.modules || [];
  const totalDevices = data?.users.reduce((sum, user) => sum + user.devices.length, 0) || 0;
  const totalConnections = data?.users.reduce(
    (sum, user) => sum + user.devices.reduce((count, device) => count + device.connections.length, 0),
    0,
  ) || 0;
  const totalSmart = data?.users.reduce(
    (sum, user) => sum + user.devices.reduce((count, device) => count + (device.smart_profile ? 1 : 0), 0),
    0,
  ) || 0;

  const targetContext = useMemo(() => {
    if (!deviceTarget || !data) return null;
    const user = data.users.find((item) => item.id === deviceTarget.userId);
    const device = user?.devices.find((item) => item.id === deviceTarget.deviceId);
    return user && device ? { user, device } : null;
  }, [data, deviceTarget]);

  const anyModalOpen = createUserOpen || !!deviceDraft || !!targetContext || !!exportPreview;
  useEffect(() => {
    if (!anyModalOpen || typeof document === "undefined") return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, [anyModalOpen]);

  function openControl(userId: string, device: AccessDevice) {
    setRoutingDraft(cloneRouting(device.routing));
    setAdvancedOpen(false);
    setDeviceTarget({ userId, deviceId: device.id });
  }

  async function createUser(event: FormEvent) {
    event.preventDefault();
    if (!newUser.name.trim()) return;
    setSaving(true);
    try {
      await api("/users", { method: "POST", body: JSON.stringify(newUser) });
      setNewUser({ name: "", note: "" });
      setCreateUserOpen(false);
      setNotice("Пользователь создан. Добавь ему устройство.");
      await load(true);
    } catch (cause) {
      setError(formatError(cause, "Не удалось создать пользователя"));
    } finally {
      setSaving(false);
    }
  }

  function openNewDevice(userId: string) {
    setDeviceDraft({
      userId,
      name: "",
      platform: "windows",
      transports: installedTransports.map((item) => item.id),
    });
  }

  function openEditDevice(userId: string, device: AccessDevice) {
    setDeviceDraft({
      userId,
      deviceId: device.id,
      name: device.name,
      platform: device.platform,
      transports: [...device.transports],
    });
  }

  function toggleTransport(id: string) {
    setDeviceDraft((current) => {
      if (!current) return current;
      return {
        ...current,
        transports: current.transports.includes(id)
          ? current.transports.filter((item) => item !== id)
          : [...current.transports, id],
      };
    });
  }

  async function saveDevice(event: FormEvent) {
    event.preventDefault();
    if (!deviceDraft?.name.trim()) return;
    setSaving(true);
    const wasNew = !deviceDraft.deviceId;
    try {
      const path = deviceDraft.deviceId
        ? `/users/${deviceDraft.userId}/devices/${deviceDraft.deviceId}`
        : `/users/${deviceDraft.userId}/devices`;
      const result = await api(path, {
        method: deviceDraft.deviceId ? "PATCH" : "POST",
        body: JSON.stringify({
          name: deviceDraft.name,
          platform: deviceDraft.platform,
          transports: deviceDraft.transports,
        }),
      }) as { device: AccessDevice };
      const userId = deviceDraft.userId;
      setDeviceDraft(null);
      await load(true);
      if (wasNew) {
        setRoutingDraft(cloneRouting(result.device.routing));
        setAdvancedOpen(false);
        setDeviceTarget({ userId, deviceId: result.device.id });
        setNotice("Устройство создано. Теперь настрой маршрутизацию и разверни контур.");
      } else {
        setNotice("Параметры устройства обновлены.");
      }
    } catch (cause) {
      setError(formatError(cause, "Не удалось сохранить устройство"));
    } finally {
      setSaving(false);
    }
  }

  async function deleteDevice(userId: string, device: AccessDevice) {
    const channelCount = device.connections.length + (device.smart_profile ? 1 : 0);
    const confirmed = await askConfirmation({
      title: `Удалить устройство «${device.name}»?`,
      message: channelCount
        ? `Устройство и все его подключения (${channelCount}) будут удалены с сервера.`
        : "Устройство будет удалено.",
      confirmLabel: "Удалить всё",
      danger: true,
    });
    if (!confirmed) return;
    const key = `device-delete:${device.id}`;
    setBusyKey(key);
    try {
      await api(`/users/${userId}/devices/${device.id}`, { method: "DELETE" });
      if (deviceTarget?.deviceId === device.id) setDeviceTarget(null);
      setNotice("Устройство и его подключения удалены.");
      await load(true);
    } catch (cause) {
      setError(formatError(cause, "Не удалось полностью удалить устройство"));
      await load(true);
    } finally {
      setBusyKey("");
    }
  }

  async function deleteUser(user: AccessUser) {
    const deviceCount = user.devices.length;
    const channelCount = user.devices.reduce(
      (total, device) => total + device.connections.length + (device.smart_profile ? 1 : 0),
      0,
    );
    const confirmed = await askConfirmation({
      title: `Удалить пользователя «${user.name}»?`,
      message: deviceCount || channelCount
        ? `Будут удалены все устройства (${deviceCount}) и их подключения (${channelCount}).`
        : "Пользователь будет удалён.",
      confirmLabel: "Удалить всё",
      danger: true,
    });
    if (!confirmed) return;
    const key = `user-delete:${user.id}`;
    setBusyKey(key);
    try {
      await api(`/users/${user.id}`, { method: "DELETE" });
      if (deviceTarget?.userId === user.id) setDeviceTarget(null);
      setNotice("Пользователь, устройства и подключения удалены.");
      await load(true);
    } catch (cause) {
      setError(formatError(cause, "Не удалось полностью удалить пользователя"));
      await load(true);
    } finally {
      setBusyKey("");
    }
  }

  async function saveRoutingOnly() {
    if (!targetContext) return;
    const key = `routing:${targetContext.device.id}`;
    setBusyKey(key);
    try {
      await api(`/users/${targetContext.user.id}/devices/${targetContext.device.id}`, {
        method: "PATCH",
        body: JSON.stringify({ routing: routingDraft }),
      });
      setNotice(targetContext.device.smart_profile ? "Маршрутизация сохранена и передана в Smart / Mihomo." : "Маршрутизация устройства сохранена.");
      await load(true);
    } catch (cause) {
      setError(formatError(cause, "Не удалось сохранить маршрутизацию"));
    } finally {
      setBusyKey("");
    }
  }

  async function createDirect(protocol: string) {
    if (!targetContext) return;
    const key = `direct:${targetContext.device.id}:${protocol}`;
    setBusyKey(key);
    try {
      await api(`/users/${targetContext.user.id}/devices/${targetContext.device.id}/connections`, {
        method: "POST",
        body: JSON.stringify({ protocol }),
      });
      setNotice(`${transportLabels[protocol] || protocol}: прямое подключение создано.`);
      await load(true);
    } catch (cause) {
      setError(formatError(cause, "Не удалось создать подключение"));
    } finally {
      setBusyKey("");
    }
  }

  async function provisionSelected() {
    if (!targetContext) return;
    const key = `all:${targetContext.device.id}`;
    setBusyKey(key);
    try {
      await api(`/users/${targetContext.user.id}/devices/${targetContext.device.id}/connections/provision-selected`, {
        method: "POST",
        body: JSON.stringify({ transports: targetContext.device.transports }),
      });
      setNotice("Прямые туннели устройства синхронизированы.");
      await load(true);
    } catch (cause) {
      setError(formatError(cause, "Не удалось развернуть прямые подключения"));
    } finally {
      setBusyKey("");
    }
  }

  async function createSmart(routing = routingDraft) {
    if (!targetContext) return;
    const smartTransports = targetContext.device.mihomo_supported_transports;
    if (!smartTransports.length) {
      setError("Для выбранных транспортов нет установленных Mihomo-модулей.");
      return;
    }
    const key = `smart:${targetContext.device.id}`;
    setBusyKey(key);
    try {
      await api(`/users/${targetContext.user.id}/devices/${targetContext.device.id}/smart`, {
        method: "POST",
        body: JSON.stringify({ transports: smartTransports, strategy: routing.strategy, routing }),
      });
      setNotice("Smart / Mihomo профиль создан с отдельным набором ключей.");
      await load(true);
    } catch (cause) {
      setError(formatError(cause, "Не удалось создать Smart-профиль"));
    } finally {
      setBusyKey("");
    }
  }

  async function deployContour() {
    if (!targetContext) return;
    const key = `deploy:${targetContext.device.id}`;
    setBusyKey(key);
    try {
      await api(`/users/${targetContext.user.id}/devices/${targetContext.device.id}`, {
        method: "PATCH",
        body: JSON.stringify({ routing: routingDraft }),
      });

      if (targetContext.device.transports.length) {
        await api(`/users/${targetContext.user.id}/devices/${targetContext.device.id}/connections/provision-selected`, {
          method: "POST",
          body: JSON.stringify({ transports: targetContext.device.transports }),
        });
      }

      if (!targetContext.device.smart_profile && data?.capabilities.mihomo.manager_available && targetContext.device.mihomo_supported_transports.length) {
        await api(`/users/${targetContext.user.id}/devices/${targetContext.device.id}/smart`, {
          method: "POST",
          body: JSON.stringify({
            transports: targetContext.device.mihomo_supported_transports,
            strategy: routingDraft.strategy,
            routing: routingDraft,
          }),
        });
      }

      setNotice("Контур устройства развернут: direct синхронизирован, Smart использует выбранную маршрутизацию.");
      await load(true);
    } catch (cause) {
      setError(formatError(cause, "Контур применён не полностью. Проверь статусы подключения."));
      await load(true);
    } finally {
      setBusyKey("");
    }
  }

  async function deleteDirect(connection: DirectConnection) {
    if (!targetContext) return;
    const transportName = transportLabels[connection.protocol] || connection.protocol;
    const confirmed = await askConfirmation({
      title: `Удалить ${transportName}?`,
      message: connection.status === "missing"
        ? "Реальный ресурс уже не найден. Будет очищена связанная запись этого устройства."
        : `Будет удалено прямое подключение ${transportName} и его отдельные credentials для этого устройства.`,
      confirmLabel: connection.status === "missing" ? "Очистить запись" : "Удалить подключение",
      danger: true,
    });
    if (!confirmed) return;
    const key = `delete:${connection.id}`;
    setBusyKey(key);
    try {
      await api(`/users/${targetContext.user.id}/devices/${targetContext.device.id}/connections/${connection.id}`, { method: "DELETE" });
      setNotice("Прямое подключение удалено.");
      await load(true);
    } catch (cause) {
      setError(formatError(cause, "Не удалось удалить подключение"));
    } finally {
      setBusyKey("");
    }
  }

  async function showDirectExport(connection: DirectConnection) {
    if (!targetContext) return;
    const key = `export:${connection.id}`;
    setBusyKey(key);
    try {
      const result = await api(`/users/${targetContext.user.id}/devices/${targetContext.device.id}/connections/${connection.id}/export`) as { filename: string; config: string };
      setExportPreview({
        title: `${targetContext.device.name} · ${transportLabels[connection.protocol] || connection.protocol}`,
        filename: result.filename,
        config: result.config,
      });
    } catch (cause) {
      setError(formatError(cause, "Не удалось получить конфигурацию"));
    } finally {
      setBusyKey("");
    }
  }

  async function deleteSmart() {
    if (!targetContext?.device.smart_profile) return;
    const confirmed = await askConfirmation({
      title: "Удалить Smart / Mihomo профиль?",
      message: targetContext.device.smart_profile.status === "missing"
        ? "Профиль не найден в Mihomo Manager. Будет очищена связанная beta-запись."
        : "Smart-профиль и созданные для него отдельные transport credentials будут удалены. Прямые подключения устройства останутся без изменений.",
      confirmLabel: targetContext.device.smart_profile.status === "missing" ? "Очистить запись" : "Удалить Smart-профиль",
      danger: true,
    });
    if (!confirmed) return;
    const key = `smart-delete:${targetContext.device.id}`;
    setBusyKey(key);
    try {
      await api(`/users/${targetContext.user.id}/devices/${targetContext.device.id}/smart`, { method: "DELETE" });
      setNotice("Smart-профиль удалён.");
      await load(true);
    } catch (cause) {
      setError(formatError(cause, "Не удалось удалить Smart-профиль"));
    } finally {
      setBusyKey("");
    }
  }

  async function showSmartExport() {
    if (!targetContext?.device.smart_profile) return;
    const key = `smart-export:${targetContext.device.id}`;
    setBusyKey(key);
    try {
      const config = await apiText(`/users/${targetContext.user.id}/devices/${targetContext.device.id}/smart/export`);
      setExportPreview({
        title: `${targetContext.device.name} · Smart / Mihomo`,
        filename: `${targetContext.device.name.replace(/[^A-Za-z0-9_.-]+/g, "-") || "device"}-mihomo.yaml`,
        config,
      });
    } catch (cause) {
      setError(formatError(cause, "Не удалось получить Smart-конфигурацию"));
    } finally {
      setBusyKey("");
    }
  }

  async function cleanupDevice() {
    if (!targetContext) return;
    const confirmed = await askConfirmation({
      title: `Очистить контур «${targetContext.device.name}»?`,
      message: "Будут удалены все управляемые direct-подключения устройства и Smart / Mihomo профиль вместе с их отдельными credentials. Само устройство останется в профиле пользователя.",
      confirmLabel: "Удалить все подключения",
      danger: true,
    });
    if (!confirmed) return;
    const key = `cleanup:${targetContext.device.id}`;
    setBusyKey(key);
    try {
      await api(`/users/${targetContext.user.id}/devices/${targetContext.device.id}/cleanup`, { method: "POST" });
      setNotice("Все управляемые подключения устройства удалены.");
      await load(true);
    } catch (cause) {
      setError(formatError(cause, "Часть ресурсов не удалось удалить. Статусы обновлены по факту."));
      await load(true);
    } finally {
      setBusyKey("");
    }
  }

  function downloadExport(preview: ExportPreview) {
    const blob = new Blob([preview.config], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = preview.filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  const isBusy = busyKey.length > 0;
  const readyDirectConnections = targetContext?.device.connections.filter((item) => item.status === "ready") || [];
  const smartReady = targetContext?.device.smart_profile?.status === "ready";
  const contourReady = Boolean(smartReady || readyDirectConnections.length);
  const contourHasProblem = Boolean(
    targetContext?.device.connections.some((item) => item.status === "missing") ||
    targetContext?.device.smart_profile?.status === "missing",
  );
  const targetRuntime = targetContext ? summarizeDeviceRuntime(targetContext.device, runtimeStats) : null;

  async function openRecommendedProfile() {
    if (!targetContext) return;
    if (targetContext.device.smart_profile?.status === "ready") {
      await showSmartExport();
      return;
    }
    const first = targetContext.device.connections.find((item) => item.status === "ready" && item.has_export);
    if (first) await showDirectExport(first);
  }

  async function recoverRegistry() {
    setBusyKey("recover-registry");
    setError("");
    try {
      const result = await api("/recover", { method: "POST" }) as { recovered?: { users?: number; devices?: number; smart_profiles?: number } };
      const recovered = result.recovered || {};
      setNotice(`Контур восстановлен: ${recovered.users || 0} пользователей · ${recovered.devices || 0} устройств · ${recovered.smart_profiles || 0} Smart`);
      await load(true);
    } catch (cause) {
      setError(formatError(cause, "Не удалось восстановить контур"));
    } finally {
      setBusyKey("");
    }
  }

  return (
    <section className="accessBetaWorkspace">
      <header className="accessBetaPageHead">
        <div>
          <div className="accessBetaTitleLine"><span className="accessBetaBeta">BETA</span><span>Доступ</span></div>
          <h1>Пользователи</h1>
        </div>
        <button className="primaryButton" type="button" onClick={() => setCreateUserOpen(true)}>+ Пользователь</button>
      </header>

      <div className="accessBetaServer">
        <div className="accessBetaServerState">
          <i className={installedTransports.length ? "ready" : ""} />
          <strong>{installedTransports.length ? "Сервер готов" : "Нет доступных протоколов"}</strong>
          <div className="accessBetaServerProtocols">
            {installedTransports.map((item) => <span key={item.id}>{transportCodes[item.id] || item.id.toUpperCase()}</span>)}
            {data?.capabilities.mihomo.manager_available && <span className="smart">SMART</span>}
          </div>
        </div>
        <button className="miniButton" type="button" onClick={() => void load()} disabled={loading}>{loading ? "Проверка…" : "Обновить"}</button>
      </div>

      {notice && <div className="accessBetaNotice"><span>✓</span><p>{notice}</p><button type="button" onClick={() => setNotice("")}>×</button></div>}
      {error && <div className="accessBetaError"><span>!</span><p>{error}</p><button type="button" onClick={() => setError("")}>×</button></div>}

      {!loading && !data?.users.length && data?.recovery?.available ? (
        <section className="accessBetaRecovery">
          <div className="accessBetaRecoveryIcon">↺</div>
          <div>
            <h2>Найден существующий контур</h2>
            <p>{data.recovery.smart_candidates} Smart · {data.recovery.direct_candidates} direct</p>
          </div>
          <button className="primaryButton" type="button" disabled={isBusy} onClick={() => void recoverRegistry()}>
            {busyKey === "recover-registry" ? "Восстановление…" : "Восстановить"}
          </button>
        </section>
      ) : !loading && !data?.users.length ? (
        <section className="accessBetaEmpty">
          <div className="accessBetaEmptyIcon">+</div>
          <h2>Добавь первого пользователя</h2>
          <button className="primaryButton" type="button" onClick={() => setCreateUserOpen(true)}>Создать пользователя</button>
        </section>
      ) : null}

      <div className="accessBetaUsers">
        {(data?.users || []).map((user) => (
          <section className="accessBetaUser" key={user.id}>
            <header className="accessBetaUserHead">
              <div className="accessBetaUserIdentity">
                <span className="accessBetaAvatar">{user.name.slice(0, 2).toUpperCase()}</span>
                <div><h2>{user.name}</h2>{user.note && <p>{user.note}</p>}</div>
              </div>
              <div className="accessBetaUserActions">
                <span>{user.device_count} {user.device_count === 1 ? "устройство" : "устройств"}</span>
                <button className="miniButton" type="button" onClick={() => openNewDevice(user.id)}>+ Устройство</button>
                <button className="accessBetaIconButton danger" type="button" onClick={() => void deleteUser(user)} aria-label="Удалить пользователя">×</button>
              </div>
            </header>

            <div className="accessBetaDeviceList">
              {user.devices.map((device) => {
                const readyDirect = device.connections.filter((item) => item.status === "ready").length;
                const readySmart = device.smart_profile?.status === "ready";
                const ready = readySmart || readyDirect > 0;
                const runtime = summarizeDeviceRuntime(device, runtimeStats);
                return (
                  <article className={`accessBetaDevice status-${runtime.tone} ${runtime.tone === "red" ? "broken" : ready ? "ready" : "new"}`} key={device.id}>
                    <div className="accessBetaDeviceMain">
                      <span className="accessBetaDeviceIcon">{device.platform === "ios" || device.platform === "android" ? "M" : device.platform === "router" ? "R" : "PC"}</span>
                      <div className="accessBetaDeviceName"><strong>{device.name}</strong><small>{device.platform_label}</small></div>
                    </div>

                    <div className="accessBetaDeviceChannels">
                      {device.transports.map((transport) => <span key={transport}>{transportCodes[transport] || transport}</span>)}
                    </div>

                    <div className="accessBetaDeviceHealth">
                      <div className={`accessBetaDeviceStatus ${runtime.tone}`}>
                        <i />
                        <span>{runtime.label}</span>
                      </div>
                      {runtime.configured && <small>↓ {formatBytes(runtime.rx_bytes)} · ↑ {formatBytes(runtime.tx_bytes)}</small>}
                    </div>

                    <div className="accessBetaDeviceButtons">
                      <button className={ready ? "primaryButton" : "ghostButton"} type="button" onClick={() => openControl(user.id, device)}>{ready ? "Подключить" : "Настроить"}</button>
                      <button className="accessBetaIconButton" type="button" onClick={() => openEditDevice(user.id, device)} aria-label="Изменить устройство">⋯</button>
                      <button className="accessBetaIconButton danger" type="button" onClick={() => void deleteDevice(user.id, device)} aria-label="Удалить устройство">×</button>
                    </div>
                  </article>
                );
              })}

              {!user.devices.length && (
                <button className="accessBetaAddDevice" type="button" onClick={() => openNewDevice(user.id)}><span>+</span> Добавить устройство</button>
              )}
            </div>
          </section>
        ))}
      </div>

      <details className="accessBetaDiagnostics">
        <summary><span>Диагностика сервера</span><small>{data?.capabilities.detected_at ? new Date(data.capabilities.detected_at).toLocaleTimeString("ru-RU") : "—"}</small></summary>
        <div className="accessBetaDiagnosticsBody">
          {(data?.capabilities.items || []).map((item) => (
            <div key={item.id}><span><i className={item.installed ? "ready" : ""} />{item.name}</span><small>{item.installed ? item.active ? "Активен" : "Установлен" : "Не установлен"}{item.version ? ` · ${item.version}` : ""}</small></div>
          ))}
          {mihomo?.installed && <div className="wide"><span>Mihomo transports</span><small>{mihomoModules.filter((item) => item.installed).map((item) => transportCodes[item.transport] || item.transport).join(" · ") || "нет модулей"}</small></div>}
        </div>
      </details>

      {createUserOpen && (
        <ModalPortal>
          <div className="accessBetaModalBackdrop" onMouseDown={() => !saving && setCreateUserOpen(false)}>
            <form className="accessBetaDialog accessBetaSmallDialog" onMouseDown={(event) => event.stopPropagation()} onSubmit={createUser}>
              <header><div><p className="eyebrow">ПОЛЬЗОВАТЕЛЬ</p><h2>Новый пользователь</h2></div><button type="button" onClick={() => setCreateUserOpen(false)}>×</button></header>
              <div className="accessBetaDialogBody">
                <label><span>Имя</span><input autoFocus value={newUser.name} onChange={(event) => setNewUser((current) => ({ ...current, name: event.target.value }))} placeholder="Ivan" maxLength={80} /></label>
                <label><span>Комментарий <small>необязательно</small></span><textarea value={newUser.note} onChange={(event) => setNewUser((current) => ({ ...current, note: event.target.value }))} placeholder="Например: личный доступ" maxLength={300} /></label>
              </div>
              <footer><button className="ghostButton" type="button" onClick={() => setCreateUserOpen(false)}>Отмена</button><button className="primaryButton" disabled={saving || !newUser.name.trim()} type="submit">{saving ? "Создание…" : "Создать"}</button></footer>
            </form>
          </div>
        </ModalPortal>
      )}

      {deviceDraft && (
        <ModalPortal>
          <div className="accessBetaModalBackdrop" onMouseDown={() => !saving && setDeviceDraft(null)}>
            <form className="accessBetaDialog accessBetaDeviceDialog" onMouseDown={(event) => event.stopPropagation()} onSubmit={saveDevice}>
              <header><div><p className="eyebrow">УСТРОЙСТВО</p><h2>{deviceDraft.deviceId ? "Изменить устройство" : "Новое устройство"}</h2></div><button type="button" onClick={() => setDeviceDraft(null)}>×</button></header>
              <div className="accessBetaDialogBody">
                <div className="accessBetaTwoFields">
                  <label><span>Название</span><input autoFocus value={deviceDraft.name} onChange={(event) => setDeviceDraft((current) => current ? ({ ...current, name: event.target.value }) : current)} placeholder="iPhone 15 Pro" maxLength={80} /></label>
                  <label><span>Платформа</span><select value={deviceDraft.platform} onChange={(event) => setDeviceDraft((current) => current ? ({ ...current, platform: event.target.value }) : current)}>{platforms.map(([id, label]) => <option value={id} key={id}>{label}</option>)}</select></label>
                </div>

                <div className="accessBetaPicker">
                  <div className="accessBetaPickerTitle"><strong>Каналы подключения</strong><span>Доступны на сервере</span></div>
                  <div className="accessBetaPickerGrid">
                    {installedTransports.map((item) => {
                      const selected = deviceDraft.transports.includes(item.id);
                      const smartCapable = mihomoModules.some((module) => module.transport === item.id && module.installed);
                      return (
                        <button key={item.id} type="button" className={selected ? "selected" : ""} onClick={() => toggleTransport(item.id)}>
                          <i />
                          <span><strong>{item.name}</strong><small>{smartCapable ? "Direct + Smart" : "Direct"}</small></span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
              <footer><button className="ghostButton" type="button" onClick={() => setDeviceDraft(null)}>Отмена</button><button className="primaryButton" disabled={saving || !deviceDraft.name.trim() || !deviceDraft.transports.length} type="submit">{saving ? "Сохранение…" : deviceDraft.deviceId ? "Сохранить" : "Продолжить"}</button></footer>
            </form>
          </div>
        </ModalPortal>
      )}

      {targetContext && (
        <ModalPortal>
          <div className="accessBetaModalBackdrop" onMouseDown={() => !isBusy && setDeviceTarget(null)}>
            <div className="accessBetaDialog accessBetaConnectDialog" onMouseDown={(event) => event.stopPropagation()}>
              <header>
                <div><p className="eyebrow">{targetContext.user.name}</p><h2>{targetContext.device.name}</h2><p>{targetContext.device.platform_label}</p></div>
                <button type="button" onClick={() => setDeviceTarget(null)} disabled={isBusy}>×</button>
              </header>

              <div className="accessBetaDialogBody accessBetaConnectBody">
                <div className={`accessBetaConnectState tone-${targetRuntime?.tone || "gray"}`}>
                  <span className="accessBetaConnectStateIcon"><i /></span>
                  <div>
                    <strong>{targetRuntime?.label || "Проверяем состояние"}</strong>
                    <small>{targetRuntime?.tone === "green" ? "Подключение активно" : targetRuntime?.tone === "yellow" ? "Профили готовы, но активного трафика сейчас нет" : targetRuntime?.tone === "red" ? "Один из каналов требует проверки" : "Подготовь подключение и забери профиль"}</small>
                  </div>
                  {contourReady && <button className="primaryButton" type="button" disabled={isBusy} onClick={() => void openRecommendedProfile()}>Получить профиль</button>}
                </div>

                {targetRuntime?.configured && (
                  <div className="accessBetaRuntimeStats">
                    <span><b>↓</b><strong>{formatBytes(targetRuntime.rx_bytes)}</strong><small>получено</small></span>
                    <span><b>↑</b><strong>{formatBytes(targetRuntime.tx_bytes)}</strong><small>отправлено</small></span>
                    <span><b>●</b><strong>{targetRuntime.active}/{targetRuntime.channels || 0}</strong><small>активно</small></span>
                    <span><b>↻</b><strong>{formatAge(targetRuntime.last_activity_s)}</strong><small>последняя активность</small></span>
                  </div>
                )}

                {!contourReady && !contourHasProblem && (
                  <div className="accessBetaSteps"><span className="done">Устройство ✓</span><i>→</i><span>Маршрутизация</span><i>→</i><span>Профиль</span></div>
                )}

                <section className="accessBetaRouting">
                  <div className="accessBetaSectionTitle"><div><span>Маршрутизация</span><strong>Как вести трафик</strong></div>{targetContext.device.smart_profile && <span className="onlinePill">Smart активен</span>}</div>

                  <div className="accessBetaRouteMode">
                    <button type="button" className={routingDraft.mode === "global" ? "selected" : ""} onClick={() => setRoutingDraft((current) => ({ ...current, mode: "global" }))}><i /><span><strong>Весь трафик</strong><small>Через зарубежный Gate</small></span></button>
                    <button type="button" className={routingDraft.mode === "rule" ? "selected" : ""} onClick={() => setRoutingDraft((current) => ({ ...current, mode: "rule" }))}><i /><span><strong>По правилам</strong><small>Можно добавить исключения</small></span></button>
                  </div>

                  <div className="accessBetaStrategy">
                    <span>Выбор канала</span>
                    <div>
                      <button type="button" className={routingDraft.strategy === "fallback" ? "selected" : ""} onClick={() => setRoutingDraft((current) => ({ ...current, strategy: "fallback" }))}>Надёжно</button>
                      <button type="button" className={routingDraft.strategy === "url-test" ? "selected" : ""} onClick={() => setRoutingDraft((current) => ({ ...current, strategy: "url-test" }))}>Быстрее</button>
                      <button type="button" className={routingDraft.strategy === "select" ? "selected" : ""} onClick={() => setRoutingDraft((current) => ({ ...current, strategy: "select" }))}>Вручную</button>
                    </div>
                  </div>

                  {routingDraft.mode === "rule" && (
                    <details className="accessBetaRules">
                      <summary>Правила маршрутизации <span>{routingDraft.rules.trim() ? "настроены" : "не заданы"}</span></summary>
                      <textarea value={routingDraft.rules} onChange={(event) => setRoutingDraft((current) => ({ ...current, rules: event.target.value }))} placeholder={"DOMAIN-SUFFIX,example.com,DIRECT\nDOMAIN,service.example,GATE.312"} />
                    </details>
                  )}

                  <details className="accessBetaTechRouting">
                    <summary>Технические параметры</summary>
                    <div><label><span>URL проверки</span><input value={routingDraft.test_url} onChange={(event) => setRoutingDraft((current) => ({ ...current, test_url: event.target.value }))} /></label><label><span>Интервал, сек.</span><input type="number" min={30} max={3600} value={routingDraft.interval} onChange={(event) => setRoutingDraft((current) => ({ ...current, interval: Math.max(30, Math.min(3600, Number(event.target.value) || 180)) }))} /></label></div>
                  </details>

                  <div className="accessBetaRoutingActions">
                    {targetContext.device.smart_profile && <button className="ghostButton" type="button" disabled={isBusy} onClick={() => void saveRoutingOnly()}>{busyKey === `routing:${targetContext.device.id}` ? "Сохранение…" : "Сохранить"}</button>}
                    {!contourReady && <button className="primaryButton" type="button" disabled={isBusy || !targetContext.device.transports.length} onClick={() => void deployContour()}>{busyKey === `deploy:${targetContext.device.id}` ? "Подготовка…" : "Подготовить подключение"}</button>}
                    {contourReady && <button className="ghostButton" type="button" disabled={isBusy} onClick={() => void deployContour()}>{busyKey === `deploy:${targetContext.device.id}` ? "Синхронизация…" : "Синхронизировать"}</button>}
                  </div>
                </section>

                {contourReady && (
                  <section className="accessBetaProfiles">
                    <div className="accessBetaSectionTitle"><div><span>Профили</span><strong>Что импортировать в приложение</strong></div></div>

                    {targetContext.device.smart_profile?.status === "ready" && (
                      <article className="accessBetaRecommended">
                        <div className="accessBetaRecommendedMark">SMART</div>
                        <div><div><strong>Умный профиль</strong><span>Рекомендуется</span></div><small>{targetContext.device.smart_profile.transports.map((item) => transportCodes[item] || item).join(" · ")} · {strategyLabels[targetContext.device.smart_profile.strategy]}</small></div>
                        <button className="primaryButton" type="button" disabled={isBusy} onClick={() => void showSmartExport()}>Открыть профиль</button>
                      </article>
                    )}

                    <div className="accessBetaConnectSteps"><span><b>1</b>Открой профиль</span><i>→</i><span><b>2</b>Импортируй</span><i>→</i><span><b>3</b>Подключись</span></div>

                    {!!readyDirectConnections.length && (
                      <details className="accessBetaDirectProfiles">
                        <summary>Прямые подключения <span>{readyDirectConnections.length}</span></summary>
                        <div>
                          {readyDirectConnections.map((connection) => (
                            <button type="button" key={connection.id} onClick={() => void showDirectExport(connection)} disabled={isBusy || !connection.has_export}>
                              <span><b>{transportCodes[connection.protocol] || connection.protocol}</b><strong>{transportLabels[connection.protocol] || connection.protocol}</strong></span><em>Открыть</em>
                            </button>
                          ))}
                        </div>
                      </details>
                    )}
                  </section>
                )}

                {contourHasProblem && (
                  <section className="accessBetaRepair"><div><strong>Есть потерянные ресурсы</strong><small>Открой техническое управление, чтобы очистить запись или пересоздать подключение.</small></div></section>
                )}

                <details className="accessBetaAdvanced" open={advancedOpen} onToggle={(event) => setAdvancedOpen((event.currentTarget as HTMLDetailsElement).open)}>
                  <summary><span>Техническое управление</span><small>{advancedOpen ? "Скрыть" : "Открыть"}</small></summary>
                  <div className="accessBetaAdvancedBody">
                    <div className="accessBetaAdvancedHead"><strong>Direct</strong><button className="miniButton" type="button" disabled={isBusy || !targetContext.device.transports.length} onClick={() => void provisionSelected()}>{busyKey === `all:${targetContext.device.id}` ? "Создание…" : "Создать недостающие"}</button></div>
                    {targetContext.device.transports.map((protocol) => {
                      const connection = targetContext.device.connections.find((item) => item.protocol === protocol);
                      const installed = data?.capabilities.installed_transports.includes(protocol);
                      return (
                        <div className="accessBetaTechRow" key={protocol}>
                          <span className="code">{transportCodes[protocol] || protocol}</span>
                          <div><strong>{transportLabels[protocol] || protocol}</strong><small>{connection?.status === "ready" ? "Готово" : connection ? "Ресурс не найден" : "Не создано"}</small></div>
                          <div className="actions">
                            {!connection && <button className="miniButton" type="button" disabled={isBusy || !installed} onClick={() => void createDirect(protocol)}>Создать</button>}
                            {connection && <button className="miniButton" type="button" disabled={isBusy || !connection.has_export} onClick={() => void showDirectExport(connection)}>Конфиг</button>}
                            {connection && <button className="miniButton danger" type="button" disabled={isBusy} onClick={() => void deleteDirect(connection)}>{connection.status === "missing" ? "Очистить" : "Удалить"}</button>}
                          </div>
                        </div>
                      );
                    })}

                    <div className="accessBetaAdvancedHead smart"><strong>Smart / Mihomo</strong><span>{targetContext.device.mihomo_supported_transports.map((item) => transportCodes[item] || item).join(" · ") || "нет модулей"}</span></div>
                    <div className="accessBetaTechRow smart">
                      <span className="code">M</span>
                      <div><strong>Умный профиль</strong><small>{targetContext.device.smart_profile ? targetContext.device.smart_profile.status === "ready" ? "Готово" : "Ресурс не найден" : "Не создано"}</small></div>
                      <div className="actions">
                        {!targetContext.device.smart_profile && <button className="miniButton" type="button" disabled={isBusy || !targetContext.device.mihomo_ready} onClick={() => void createSmart()}>Создать</button>}
                        {targetContext.device.smart_profile && <button className="miniButton" type="button" disabled={isBusy || targetContext.device.smart_profile.status !== "ready"} onClick={() => void showSmartExport()}>YAML</button>}
                        {targetContext.device.smart_profile && <button className="miniButton danger" type="button" disabled={isBusy} onClick={() => void deleteSmart()}>{targetContext.device.smart_profile.status === "missing" ? "Очистить" : "Удалить"}</button>}
                      </div>
                    </div>

                    <div className="accessBetaAdvancedDanger"><button type="button" disabled={isBusy || (!targetContext.device.connections.length && !targetContext.device.smart_profile)} onClick={() => void cleanupDevice()}>Удалить все подключения устройства</button></div>
                  </div>
                </details>
              </div>

              <footer><button className="ghostButton" type="button" disabled={isBusy} onClick={() => setDeviceTarget(null)}>Готово</button></footer>
            </div>
          </div>
        </ModalPortal>
      )}

      {exportPreview && (
        <ModalPortal>
          <div className="accessBetaModalBackdrop" onMouseDown={() => setExportPreview(null)}>
            <div className="accessBetaDialog accessBetaExportDialog" onMouseDown={(event) => event.stopPropagation()}>
              <header><div><p className="eyebrow">ПРОФИЛЬ ПОДКЛЮЧЕНИЯ</p><h2>{exportPreview.title}</h2><p>{exportPreview.filename}</p></div><button type="button" onClick={() => setExportPreview(null)}>×</button></header>
              <div className="accessBetaDialogBody"><pre>{exportPreview.config}</pre></div>
              <footer><button className="ghostButton" type="button" onClick={() => navigator.clipboard?.writeText(exportPreview.config)}>Копировать</button><button className="primaryButton" type="button" onClick={() => downloadExport(exportPreview)}>Скачать файл</button></footer>
            </div>
          </div>
        </ModalPortal>
      )}

      {confirmation && (
        <ModalPortal>
          <div className="confirmBackdrop accessBetaConfirmBackdrop" role="presentation" onMouseDown={() => closeConfirmation(false)}>
            <form
              className={`confirmDialog accessBetaConfirmDialog ${confirmation.danger ? "danger" : ""}`}
              role="alertdialog"
              aria-modal="true"
              aria-labelledby="access-beta-confirm-title"
              onMouseDown={(event) => event.stopPropagation()}
              onSubmit={(event) => {
                event.preventDefault();
                if (!confirmation.phrase || confirmationInput === confirmation.phrase) closeConfirmation(true);
              }}
            >
              <div className="confirmMark">{confirmation.danger ? "!" : "✓"}</div>
              <p className="eyebrow">ПОДТВЕРЖДЕНИЕ ДЕЙСТВИЯ</p>
              <h2 id="access-beta-confirm-title">{confirmation.title}</h2>
              <p>{confirmation.message}</p>
              {confirmation.phrase && (
                <label>
                  Для подтверждения введите <strong>{confirmation.phrase}</strong>
                  <input autoFocus value={confirmationInput} onChange={(event) => setConfirmationInput(event.target.value)} autoComplete="off" />
                </label>
              )}
              <div className="confirmActions">
                <button type="button" autoFocus={!confirmation.phrase} onClick={() => closeConfirmation(false)}>Отмена</button>
                <button className="confirmPrimary" type="submit" disabled={Boolean(confirmation.phrase && confirmationInput !== confirmation.phrase)}>{confirmation.confirmLabel}</button>
              </div>
            </form>
          </div>
        </ModalPortal>
      )}
    </section>
  );
}
