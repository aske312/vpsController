"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { formatModuleVersion } from "../../lib/format-version";

type ProtocolId = "wg" | "awg" | "shadowsocks" | "vless-reality-xhttp" | "hysteria2" | "tuic";
type ResourceHistory = { load: number[]; memory: number[]; disk: number[]; rx: number[]; tx: number[] };

type OverviewData = {
  server: {
    name: string;
    public_ip: string;
    public_ipv4?: string;
    public_ipv6?: string;
    public_domain?: string;
    public_endpoint?: string;
    city: string;
    country: string;
    country_code: string;
    uptime_s: number;
  };
  resources: {
    load1: number;
    cpu_percent: number;
    cpu_count: number;
    memory_total: number;
    memory_available: number;
    disk_total: number;
    disk_available: number;
    network_rx: number;
    network_tx: number;
  };
  protocols: Record<"wg" | "awg", { interface: string; port: number; active: boolean }>;
};

type ProtocolImage = {
  id: string;
  name: string;
  version: string;
  description: string;
  category: string;
  category_name: string;
  interface: string;
  installed: boolean;
  active?: boolean;
  installable: boolean;
  removable: boolean;
  installed_version?: string;
  available_version?: string;
  update_available?: boolean;
  update_breaking?: boolean;
  update_via_release?: boolean;
};

type Client = {
  id: string;
  name: string;
  protocol: ProtocolId;
  address: string;
  quality?: "stable" | "warning" | "error" | "offline";
  latency_ms?: number;
  rx_bytes: number;
  tx_bytes: number;
  rx_bps?: number;
  tx_bps?: number;
  active_connections?: number;
};

type MihomoStatus = {
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

type MihomoModule = {
  id: string;
  name: string;
  description: string;
  category: "transport" | "dns" | "routing";
  category_name: string;
  installed: boolean;
  active: boolean;
};

type MihomoProfile = {
  id: string;
  name: string;
  channels: string[];
  connections?: Array<{ id: string; component: string; name: string }>;
  created_at: string;
  updated_at: string;
};

type MihomoProfileStats = {
  id: string;
  summary: { rx_bytes: number; tx_bytes: number };
};

type DirectProtocolStatus = {
  protocol: ProtocolId;
  interface?: string;
  active?: boolean;
  service_active?: boolean;
  service_enabled?: boolean;
  active_since?: string;
  address?: string;
  listen_port?: number;
  peers?: number;
  online_peers?: number;
  endpoints?: number;
  last_handshake_age_s?: number;
  interface_rx_bytes?: number;
  interface_tx_bytes?: number;
  unit?: string;
};

type Props = {
  token: string;
  overview: OverviewData | null;
  memUsed: number;
  diskUsed: number;
  memoryUsedBytes: number;
  diskUsedBytes: number;
  networkRate: { rx: number; tx: number };
  resourceHistory: ResourceHistory;
  clients: Client[];
  protocolImages: ProtocolImage[];
  installingProtocol: string;
  busy: boolean;
  onInstallProtocol: (image: ProtocolImage) => void;
  onUpdateProtocol: (image: ProtocolImage) => void;
};

const channelShort: Record<string, string> = {
  "transport-awg": "AWG",
  "transport-wg": "WG",
  "transport-reality": "VL",
  "transport-shadowsocks": "SS",
  "transport-hysteria2": "HY2",
  "transport-tuic": "TUIC",
};

const directShort: Record<ProtocolId, string> = {
  wg: "WG",
  awg: "AWG",
  shadowsocks: "SS",
  "vless-reality-xhttp": "VLESS",
  hysteria2: "HY2",
  tuic: "TUIC",
};

const directName: Record<ProtocolId, string> = {
  wg: "WireGuard",
  awg: "AmneziaWG",
  shadowsocks: "Shadowsocks",
  "vless-reality-xhttp": "VLESS Reality",
  hysteria2: "Hysteria2",
  tuic: "TUIC v5",
};

const bytes = (value = 0) => {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
};

const duration = (seconds = 0) => {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}д ${hours}ч`;
  if (hours) return `${hours}ч ${minutes}м`;
  return `${minutes}м`;
};

const protocolMark = (id: string) => id === "mihomo" ? "M" : id === "vless-reality-xhttp" ? "VLESS" : id === "shadowsocks" ? "SS" : id.toUpperCase();
const normalize = (value = "") => value.toLowerCase().replace(/[^a-z0-9]+/g, "");

const channelAliases: Record<string, string[]> = {
  "transport-awg": ["transport-awg", "awg", "amneziawg"],
  "transport-wg": ["transport-wg", "wg", "wireguard"],
  "transport-reality": ["transport-reality", "vrx", "reality", "vlessreality", "vlessrealityxhttp"],
  "transport-shadowsocks": ["transport-shadowsocks", "ss", "shadowsocks"],
  "transport-hysteria2": ["transport-hysteria2", "hysteria2", "hy2"],
  "transport-tuic": ["transport-tuic", "tuic"],
};

function valueMatchesChannel(value: string, module: MihomoModule) {
  const candidate = normalize(value);
  const aliases = channelAliases[module.id] || [module.id, module.name];
  return aliases.some((alias) => {
    const normalizedAlias = normalize(alias);
    return candidate === normalizedAlias || candidate.includes(normalizedAlias) || normalizedAlias.includes(candidate);
  });
}

function prettyMax(value: number) {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const exponent = Math.floor(Math.log10(value));
  const base = 10 ** exponent;
  const fraction = value / base;
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return nice * base;
}

export function OverviewDashboard({
  token,
  overview,
  memUsed,
  diskUsed,
  memoryUsedBytes,
  diskUsedBytes,
  networkRate,
  resourceHistory,
  clients,
  protocolImages,
  installingProtocol,
  busy,
  onInstallProtocol,
  onUpdateProtocol,
}: Props) {
  const mihomoImage = protocolImages.find((item) => item.id === "mihomo");
  const mihomoInstalled = Boolean(mihomoImage?.installed);
  const [mihomoStatus, setMihomoStatus] = useState<MihomoStatus | null>(null);
  const [mihomoModules, setMihomoModules] = useState<MihomoModule[]>([]);
  const [mihomoProfiles, setMihomoProfiles] = useState<MihomoProfile[]>([]);
  const [mihomoProfileStats, setMihomoProfileStats] = useState<Record<string, MihomoProfileStats["summary"]>>({});
  const [mihomoSummaryError, setMihomoSummaryError] = useState("");
  const [directStatuses, setDirectStatuses] = useState<Partial<Record<ProtocolId, DirectProtocolStatus>>>({});
  const [directStatusFailures, setDirectStatusFailures] = useState<Partial<Record<ProtocolId, boolean>>>({});
  const [directRates, setDirectRates] = useState<Partial<Record<ProtocolId, { rx: number; tx: number }>>>({});
  const directSamples = useRef<Partial<Record<ProtocolId, { rx: number; tx: number; at: number }>>>({});

  const directChannels = useMemo(
    () => protocolImages.filter((item) =>
      item.installed &&
      item.id !== "mihomo" &&
      (["wg", "awg", "shadowsocks", "vless-reality-xhttp", "hysteria2", "tuic"] as string[]).includes(item.id),
    ),
    [protocolImages],
  );
  const loadMihomoSummary = useCallback(async () => {
    if (!token || !mihomoInstalled) {
      setMihomoStatus(null);
      setMihomoModules([]);
      setMihomoProfiles([]);
      setMihomoProfileStats({});
      setMihomoSummaryError("");
      return;
    }
    try {
      const headers = { Authorization: `Basic ${token}` };
      const [statusResponse, modulesResponse, profilesResponse, statsResponse] = await Promise.all([
        fetch("/api/mihomo/status", { headers }),
        fetch("/api/mihomo/modules", { headers }),
        fetch("/api/mihomo/profiles", { headers }),
        fetch("/api/mihomo/stats", { headers }),
      ]);
      if (!statusResponse.ok || !modulesResponse.ok || !profilesResponse.ok) throw new Error("summary unavailable");
      const [status, modules, profiles, stats] = await Promise.all([
        statusResponse.json() as Promise<MihomoStatus>,
        modulesResponse.json() as Promise<{ items: MihomoModule[] }>,
        profilesResponse.json() as Promise<{ items: MihomoProfile[] }>,
        statsResponse.ok ? statsResponse.json() as Promise<{ items: MihomoProfileStats[] }> : Promise.resolve({ items: [] }),
      ]);
      setMihomoStatus(status);
      setMihomoModules(modules.items || []);
      setMihomoProfiles(profiles.items || []);
      setMihomoProfileStats(Object.fromEntries((stats.items || []).map((item) => [item.id, item.summary])));
      setMihomoSummaryError("");
    } catch {
      setMihomoSummaryError("Сводка Mihomo временно недоступна");
    }
  }, [mihomoInstalled, token]);

  const loadDirectStatuses = useCallback(async () => {
    if (!token || !directChannels.length) {
      setDirectStatuses({});
      setDirectStatusFailures({});
      setDirectRates({});
      directSamples.current = {};
      return;
    }
    const headers = { Authorization: `Basic ${token}` };
    const results = await Promise.allSettled(
      directChannels.map(async (image) => {
        const protocol = image.id as ProtocolId;
        const response = await fetch(`/api/protocols/${protocol}/status`, { headers });
        if (!response.ok) throw new Error(`status ${response.status}`);
        return response.json() as Promise<DirectProtocolStatus>;
      }),
    );

    const now = Date.now();
    const statusUpdates: Partial<Record<ProtocolId, DirectProtocolStatus>> = {};
    const failureUpdates: Partial<Record<ProtocolId, boolean>> = {};
    const rateUpdates: Partial<Record<ProtocolId, { rx: number; tx: number }>> = {};
    results.forEach((result, index) => {
      const protocol = directChannels[index]?.id as ProtocolId | undefined;
      if (!protocol) return;
      failureUpdates[protocol] = result.status !== "fulfilled";
      if (result.status !== "fulfilled") return;
      statusUpdates[protocol] = result.value;
      const rx = result.value.interface_rx_bytes || 0;
      const tx = result.value.interface_tx_bytes || 0;
      const previous = directSamples.current[protocol];
      if (previous) {
        const seconds = Math.max((now - previous.at) / 1000, 0.1);
        rateUpdates[protocol] = {
          rx: Math.max(0, (rx - previous.rx) / seconds),
          tx: Math.max(0, (tx - previous.tx) / seconds),
        };
      }
      directSamples.current[protocol] = { rx, tx, at: now };
    });
    setDirectStatuses((current) => ({ ...current, ...statusUpdates }));
    setDirectStatusFailures((current) => ({ ...current, ...failureUpdates }));
    if (Object.keys(rateUpdates).length) setDirectRates((current) => ({ ...current, ...rateUpdates }));
  }, [directChannels, token]);

  useEffect(() => {
    const initial = window.setTimeout(() => void loadMihomoSummary(), 0);
    if (!mihomoInstalled) return () => window.clearTimeout(initial);
    const timer = window.setInterval(() => void loadMihomoSummary(), 12000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [loadMihomoSummary, mihomoInstalled]);

  useEffect(() => {
    const initial = window.setTimeout(() => void loadDirectStatuses(), 0);
    if (!directChannels.length) return () => window.clearTimeout(initial);
    const timer = window.setInterval(() => void loadDirectStatuses(), 6000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [directChannels, loadDirectStatuses]);

  const installedMihomoChannels = useMemo(
    () => mihomoModules.filter((item) => item.category === "transport" && item.installed),
    [mihomoModules],
  );

  const installedServerModules = useMemo(() => protocolImages.filter((item) => item.installed), [protocolImages]);
  const installableModules = useMemo(() => protocolImages.filter((item) => !item.installed && item.installable), [protocolImages]);
  const componentImages = useMemo(() => [...protocolImages].sort((left, right) => {
    const rank = (item: ProtocolImage) => item.installed ? 0 : item.installable ? 1 : 2;
    return rank(left) - rank(right) || left.name.localeCompare(right.name, "ru");
  }), [protocolImages]);
  const stableDirectClients = clients.filter((client) => client.quality === "stable").length;
  const attentionDirectClients = clients.filter((client) => client.quality === "warning" || client.quality === "error").length;
  const offlineDirectClients = clients.filter((client) => client.quality === "offline").length;

  const profileCount = mihomoProfiles.length || mihomoStatus?.profiles || 0;
  const totalAccessObjects = (mihomoStatus?.credentials || 0) + clients.length;
  const memoryTotal = overview?.resources.memory_total || 0;
  const diskTotal = overview?.resources.disk_total || 0;
  const memoryFree = overview?.resources.memory_available || 0;
  const diskFree = overview?.resources.disk_available || 0;
  const networkTotal = (overview?.resources.network_rx || 0) + (overview?.resources.network_tx || 0);

  const mihomoChannelStates = useMemo(() => installedMihomoChannels.map((module) => {
    const profileRefs = mihomoProfiles.filter((profile) => profile.channels.some((channel) => valueMatchesChannel(channel, module))).length;
    const inUse = (mihomoStatus?.channels_in_use || []).some((channel) => valueMatchesChannel(channel, module));
    return { module, profileRefs, inUse, runtimeReady: module.active };
  }), [installedMihomoChannels, mihomoProfiles, mihomoStatus?.channels_in_use]);

  const mihomoInUseCount = mihomoChannelStates.filter((item) => item.inUse).length;
  const mihomoReadyCount = mihomoChannelStates.filter((item) => item.runtimeReady && !item.inUse).length;

  const directChannelStates = useMemo(() => directChannels.map((image) => {
    const protocol = image.id as ProtocolId;
    const status = directStatuses[protocol];
    const statusFailed = Boolean(directStatusFailures[protocol]);
    const protocolClients = clients.filter((client) => client.protocol === protocol);
    const clientSessions = protocolClients.reduce((sum, client) => sum + (client.active_connections || 0), 0);
    const onlinePeers = status?.online_peers || 0;
    const sessions = Math.max(clientSessions, onlinePeers);
    const rate = directRates[protocol] || { rx: 0, tx: 0 };
    const clientRx = protocolClients.reduce((sum, client) => sum + (client.rx_bps || 0), 0);
    const clientTx = protocolClients.reduce((sum, client) => sum + (client.tx_bps || 0), 0);
    const hasClientRates = protocolClients.some((client) => client.rx_bps !== undefined || client.tx_bps !== undefined);
    const rx = hasClientRates ? clientRx : rate.rx;
    const tx = hasClientRates ? clientTx : rate.tx;
    const serviceActive = statusFailed ? null : status ? Boolean(status.service_active ?? status.active) : null;
    const configured = statusFailed ? null : status ? Boolean(status.interface || status.address || status.listen_port || status.unit) : null;
    const trafficNow = rx + tx > 64;
    const inUse = serviceActive === true && (sessions > 0 || trafficNow);
    const avgLatencyValues = protocolClients.map((client) => client.latency_ms).filter((value): value is number => typeof value === "number");
    const avgLatency = avgLatencyValues.length ? Math.round(avgLatencyValues.reduce((sum, value) => sum + value, 0) / avgLatencyValues.length) : null;
    return { image, protocol, status, statusFailed, protocolClients, sessions, rx, tx, serviceActive, configured, inUse, avgLatency };
  }), [clients, directChannels, directRates, directStatusFailures, directStatuses]);

  const directInUseCount = directChannelStates.filter((item) => item.inUse).length;
  const directReadyCount = directChannelStates.filter((item) => item.serviceActive === true && !item.inUse).length;
  const directStoppedCount = directChannelStates.filter((item) => item.serviceActive === false).length;
  const routesInUse = mihomoInUseCount + directInUseCount;
  const routesReady = mihomoReadyCount + directReadyCount;

  return (
    <section className="overview" aria-label="Обзор инфраструктуры">
      <article className="overviewTopology">
        <header className="overviewTopologyHead">
          <div>
            <p className="eyebrow">ROUTING TOPOLOGY</p>
            <h1>Доступ и маршруты</h1>
          </div>
          <div className="overviewTopologyStats">
            <span><small>ACCESS OBJECTS</small><strong>{totalAccessObjects}</strong></span>
            {mihomoInstalled && <span className="violet"><small>PROFILES</small><strong>{profileCount}</strong></span>}
            {mihomoInstalled && <span className="violet"><small>CHANNELS</small><strong>{installedMihomoChannels.length}</strong></span>}
            <span className={routesInUse ? "cyan" : ""}><small>ROUTES IN USE</small><strong>{routesInUse}</strong></span>
          </div>
        </header>

        <div className="overviewFlow">
          <aside className="overviewAccess">
            <p className="eyebrow">CLIENT ACCESS</p>
            <div className="overviewAccessGlyph"><SourceGlyph /></div>
            <strong>Клиенты</strong>
            <small>{totalAccessObjects ? `${totalAccessObjects} настроенных объектов доступа` : "Объекты доступа ещё не созданы"}</small>
            <dl>
              <div><dt>Mihomo credentials</dt><dd>{mihomoStatus?.credentials || 0}</dd></div>
              <div><dt>Direct clients</dt><dd>{clients.length}</dd></div>
              <div><dt>Routes in use</dt><dd className={routesInUse ? "ok" : ""}>{routesInUse}</dd></div>
              <div><dt>Ready / idle</dt><dd>{routesReady}</dd></div>
            </dl>
          </aside>

          <div className="overviewRouteStack">
            {mihomoInstalled && (
              <section className="overviewRoute mihomo">
                <header>
                  <div className="overviewRouteIdentity">
                    <span className="overviewRouteMark">M</span>
                    <div>
                      <p className="eyebrow">MANAGED ROUTING</p>
                      <h2>Mihomo</h2>
                      <small>{mihomoStatus?.core_version ? `core ${mihomoStatus.core_version}` : `package ${mihomoImage?.version || "—"}`}</small>
                    </div>
                  </div>
                  <span className={`overviewState ${mihomoStatus?.active ? "online" : "idle"}`}>
                    {mihomoStatus?.active ? (mihomoInUseCount ? "CORE ONLINE  IN USE" : "CORE ONLINE  IDLE") : "CORE STOPPED"}
                  </span>
                </header>

                <div className="overviewMihomoBody">
                  <section className="overviewManagedProfiles">
                    <div className="overviewManagedHead"><div className="overviewSectionLabel"><b>Профили</b><span>{profileCount}</span></div></div>
                    <div className="overviewManagedProfileList">
                      {mihomoProfiles.map((profile) => {
                        const assignedComponents = [...new Set(profile.connections?.length ? profile.connections.map((connection) => connection.component) : profile.channels)];
                        const traffic = mihomoProfileStats[profile.id];
                        return <div className="overviewManagedProfileRow" key={profile.id}>
                          <span className="profileDot" />
                          <p><b>{profile.name}</b><small>{profile.connections?.length || profile.channels.length} подключений</small></p>
                          <div className="overviewManagedProtocolSet">
                            {assignedComponents.map((component) => <span key={component} title={component.replace("transport-", "")}>{component === "transport-reality" ? "VLESS" : channelShort[component] || component.replace("transport-", "").toUpperCase()}</span>)}
                            {!assignedComponents.length && <em>—</em>}
                          </div>
                          <span className="overviewManagedTraffic"><b>↓ {traffic ? bytes(traffic.rx_bytes) : "—"}</b><small>↑ {traffic ? bytes(traffic.tx_bytes) : "—"}</small></span>
                        </div>;
                      })}
                      {!mihomoProfiles.length && <p className="overviewEmpty">Профили ещё не созданы.</p>}
                    </div>
                  </section>
                </div>

                <footer>
                  <span>{mihomoStatus?.credentials || 0} credentials</span>
                  <span>{mihomoStatus?.endpoint || overview?.server.public_endpoint || overview?.server.public_ip || "—"}</span>
                </footer>
                {mihomoSummaryError && <div className="overviewInlineWarning">{mihomoSummaryError}</div>}
              </section>
            )}

            {directChannels.length > 0 && (
              <section className="overviewRoute direct">
                <header>
                  <div className="overviewRouteIdentity">
                    <span className="overviewRouteMark">↗</span>
                    <div><p className="eyebrow">INDEPENDENT</p><h2>Direct Channels</h2><small>Прямые подключения вне Mihomo</small></div>
                  </div>
                  <span className={`overviewState ${directInUseCount ? "online" : "idle"}`}>
                    {directInUseCount ? `${directInUseCount} IN USE ${directReadyCount} READY` : `${directReadyCount} READY ${directStoppedCount} STOPPED`}
                  </span>
                </header>

                <div className="overviewDirectList">
                  {directChannelStates.map(({ image, protocol, status, statusFailed, protocolClients, sessions, rx, tx, serviceActive, configured, inUse, avgLatency }) => {
                    const stateClass = statusFailed ? "unavailable" : inUse ? "inuse" : serviceActive === true ? "ready" : serviceActive === false ? "stopped" : "checking";
                    const stateLabel = statusFailed ? "NO STATUS" : inUse ? "IN USE" : serviceActive === true ? "READY" : serviceActive === false ? "STOPPED" : "CHECKING";
                    return (
                      <div key={image.id}>
                        <span className="overviewProtocolMark cyan">{directShort[protocol]}</span>
                        <p>
                          <b>{directName[protocol]}</b>
                          <small>{statusFailed ? "runtime status недоступен" : configured === null ? "проверяем конфигурацию" : configured ? `${protocolClients.length} clients  ${sessions} sessions` : "установлен, конфигурация не обнаружена"}</small>
                        </p>
                        <span className="overviewDirectTraffic"><b>↓ {bytes(rx)}/с</b><small>↑ {bytes(tx)}/с</small></span>
                        <span className="overviewDirectLatency"><b>{avgLatency !== null ? `${avgLatency} ms` : "—"}</b><small>{status?.online_peers ?? 0} peers online</small></span>
                        <em className={stateClass}>{stateLabel}</em>
                      </div>
                    );
                  })}
                </div>
              </section>
            )}

            {!mihomoInstalled && directChannels.length === 0 && (
              <div className="overviewNoRoutes"><span>◇</span><div><h2>Защищённые каналы не установлены</h2><p>Доступные компоненты показаны ниже в каталоге.</p></div></div>
            )}
          </div>

          <aside className="overviewExit">
            {mihomoInstalled && <div className="violet"><ExitGlyph /><strong>Internet</strong><small>{mihomoInUseCount ? "Mihomo route active" : "Mihomo ready / idle"}</small></div>}
            {directChannels.length > 0 && <div className="cyan"><ExitGlyph /><strong>Internet</strong><small>{directInUseCount ? "direct traffic active" : "direct routes idle"}</small></div>}
          </aside>
        </div>
      </article>

      <article className="overviewNodeWorkspace">
      <section className="overviewTelemetry">
        <header>
          <div><p className="eyebrow">LIVE SYSTEM</p><h2>Телеметрия VPS</h2></div>
          <small>Шкала времени формируется из текущей сессии панели  шаг ~3 сек</small>
        </header>

        <div className="overviewTaskGraphs">
          <TaskGraph
            label="CPU"
            value={`${(overview?.resources.cpu_percent || 0).toFixed(0)}%`}
            detail={`load ${overview?.resources.load1?.toFixed(2) || "—"}  ${overview?.resources.cpu_count || 0} cores`}
            series={[{ values: resourceHistory.load, tone: "blue" }]}
            maxValue={100}
            yFormatter={(value) => `${Math.round(value)}%`}
          />
          <TaskGraph
            label="MEMORY"
            value={`${memUsed.toFixed(0)}%`}
            detail={`${bytes(memoryUsedBytes)} / ${bytes(memoryTotal)}  free ${bytes(memoryFree)}`}
            series={[{ values: resourceHistory.memory, tone: "green" }]}
            maxValue={100}
            yFormatter={(value) => `${Math.round(value)}%`}
          />
          <TaskGraph
            label="NETWORK"
            value={`↓ ${bytes(networkRate.rx)}/с  ↑ ${bytes(networkRate.tx)}/с`}
            detail={`interface total ↓ ${bytes(overview?.resources.network_rx || 0)}  ↑ ${bytes(overview?.resources.network_tx || 0)}`}
            series={[
              { values: resourceHistory.rx, tone: "blue" },
              { values: resourceHistory.tx, tone: "green" },
            ]}
            maxValue={prettyMax(Math.max(networkRate.rx, networkRate.tx, ...resourceHistory.rx, ...resourceHistory.tx, 1))}
            yFormatter={(value) => `${bytes(value)}/с`}
            wide
            legend={["RX", "TX"]}
          />
        </div>

        <div className="overviewSystemFacts">
          <FactCard label="DISK USED" value={`${diskUsed.toFixed(0)}%`} detail={`${bytes(diskUsedBytes)} / ${bytes(diskTotal)}  free ${bytes(diskFree)}`} />
          <FactCard label="UPTIME" value={duration(overview?.server.uptime_s || 0)} detail={`${overview?.server.city || "Город не определён"}  ${overview?.server.country || "—"}`} />
          <FactCard label="LOAD 1M" value={overview?.resources.load1?.toFixed(2) || "—"} detail={`${overview?.resources.cpu_count || 0} CPU cores`} />
          <FactCard label="TRAFFIC TOTAL" value={bytes(networkTotal)} detail={`↓ ${bytes(overview?.resources.network_rx || 0)}  ↑ ${bytes(overview?.resources.network_tx || 0)}`} />
          <FactCard label="DIRECT CLIENTS" value={`${clients.length}`} detail={`${stableDirectClients} stable  ${attentionDirectClients} attention  ${offlineDirectClients} offline`} />
          <FactCard label="MIHOMO CREDENTIALS" value={`${mihomoStatus?.credentials || 0}`} detail={`${profileCount} profiles  ${mihomoStatus?.profiles_in_use || 0} in use`} />
          <FactCard label="ROUTES IN USE" value={`${routesInUse}`} detail={`${routesReady} ready / idle`} />
          <FactCard label="FREE MEMORY" value={bytes(memoryFree)} detail={`${memUsed.toFixed(0)}% currently used`} />
        </div>
      </section>

      <section className="overviewComponents">
        <header>
          <div><p className="eyebrow">NODE COMPONENTS</p><h2>Компоненты</h2><small>Версия берётся из фактического каталога установки; для Mihomo после запуска показывается версия core.</small></div>
          <div className="overviewComponentCounters"><span><b>{installedServerModules.length}</b> installed</span><span><b>{installableModules.length}</b> available</span></div>
        </header>

        <section className="overviewComponentRegistry" aria-label="Управление модулями узла">
          <header>
            <span>МОДУЛЬ</span>
            <span>ВЕРСИЯ</span>
            <span>СОСТОЯНИЕ</span>
            <span>ДЕЙСТВИЕ</span>
          </header>
          <div>
            {componentImages.map((image) => {
              const protocol = image.id as ProtocolId;
              const isDirect = (["wg", "awg", "shadowsocks", "vless-reality-xhttp"] as string[]).includes(image.id);
              const directStatus = isDirect ? directStatuses[protocol] : undefined;
              const statusFailed = isDirect && Boolean(directStatusFailures[protocol]);
              const statusKnown = image.id === "mihomo"
                ? Boolean(mihomoStatus) || Boolean(mihomoSummaryError)
                : isDirect
                  ? Boolean(directStatus) || statusFailed
                  : typeof image.active === "boolean";
              const running = image.id === "mihomo"
                ? Boolean(mihomoStatus?.active)
                : directStatus
                  ? Boolean(directStatus.service_active ?? directStatus.active)
                  : Boolean(image.active);
              const installedVersion = image.id === "mihomo" && mihomoStatus?.core_version
                ? formatModuleVersion(mihomoStatus.core_version)
                : image.installed_version
                  ? formatModuleVersion(image.installed_version)
                  : image.installed ? "НЕ ОПРЕДЕЛЕНА" : "—";
              const displayedVersion = image.installed
                ? installedVersion
                : image.installable ? "АКТУАЛЬНАЯ" : "—";
              const availableVersion = image.available_version
                ? formatModuleVersion(image.available_version)
                : "НЕ ОПРЕДЕЛЕНА";
              const state = !image.installed && !image.installable
                ? { className: "unavailable", label: "НЕДОСТУПЕН" }
                : !image.installed
                ? { className: "available", label: "ДОСТУПЕН" }
                : !statusKnown
                  ? { className: "checking", label: "ПРОВЕРКА" }
                  : statusFailed || (image.id === "mihomo" && Boolean(mihomoSummaryError))
                    ? { className: "unavailable", label: "НЕТ ДАННЫХ" }
                    : running
                      ? { className: "online", label: "РАБОТАЕТ" }
                      : { className: "stopped", label: "ОСТАНОВЛЕН" };

              return (
                <div className="overviewComponentRow" key={image.id}>
                  <div className="overviewComponentIdentity">
                    <span className={`overviewProtocolMark ${image.id === "mihomo" ? "violet" : "cyan"}`}>{protocolMark(image.id)}</span>
                    <p><b>{image.name}</b><small>{image.description || image.category_name}</small></p>
                  </div>
                  <div className="overviewComponentVersion">
                    <b>{displayedVersion}</b>
                    <small>{image.installed ? image.update_available || image.update_via_release ? `новая: ${availableVersion}` : "фактическая версия" : image.installable ? "версия для установки" : "образ не готов"}</small>
                  </div>
                  <span className={`overviewComponentState ${state.className}`}>{state.label}</span>
                  <div className="overviewComponentAction">
                    {!image.installed ? (
                      <button type="button" disabled={!image.installable || busy || Boolean(installingProtocol)} onClick={() => onInstallProtocol(image)}>
                        {!image.installable ? "В разработке" : installingProtocol === image.id ? "Установка…" : "Установить"}
                      </button>
                    ) : image.update_available ? (
                      <button type="button" className={image.update_breaking ? "warning" : ""} disabled={busy || Boolean(installingProtocol)} onClick={() => onUpdateProtocol(image)}>
                        {installingProtocol === `update-${image.id}` ? "Обновление…" : "Обновить"}
                      </button>
                    ) : (
                      <small>Актуальная версия</small>
                    )}
                  </div>
                </div>
              );
            })}
            {!protocolImages.length && <p className="overviewEmpty">Каталог модулей временно недоступен.</p>}
          </div>
        </section>
      </section>
      </article>
    </section>
  );
}

type GraphTone = "blue" | "green";
type GraphSeries = { values: number[]; tone: GraphTone };

function TaskGraph({
  label,
  value,
  detail,
  series,
  maxValue,
  yFormatter,
  wide = false,
  legend,
}: {
  label: string;
  value: string;
  detail: string;
  series: GraphSeries[];
  maxValue: number;
  yFormatter: (value: number) => string;
  wide?: boolean;
  legend?: string[];
}) {
  const dataLength = Math.max(...series.map((item) => item.values.length), 1);
  const sampleWindow = Math.max((dataLength - 1) * 3, 3);
  const yTicks = [1, .75, .5, .25, 0];
  const xTicks = [1, .75, .5, .25, 0];
  const top = Math.max(maxValue, 1);

  return (
    <section className={`taskGraphCard ${wide ? "wide" : ""}`}>
      <header>
        <div><span>{label}</span><strong>{value}</strong></div>
        {legend && <div className="taskGraphLegend">{legend.map((item, index) => <span key={item} className={index ? "green" : "blue"}>{item}</span>)}</div>}
      </header>
      <div className="taskGraphPlot">
        <svg viewBox="0 0 100 50" preserveAspectRatio="none" aria-hidden="true">
          <g className="taskGrid">
            {[0, 12.5, 25, 37.5, 50].map((y) => <line key={`y-${y}`} x1="0" x2="100" y1={y} y2={y} />)}
            {[0, 20, 40, 60, 80, 100].map((x) => <line key={`x-${x}`} x1={x} x2={x} y1="0" y2="50" />)}
          </g>
          {series.map((item, index) => <polyline key={index} className={`taskSeries ${item.tone}`} points={graphPoints(item.values, top)} />)}
        </svg>
        <div className="taskYAxis">
          {yTicks.map((ratio) => <span key={ratio}>{yFormatter(top * ratio)}</span>)}
        </div>
        <div className="taskXAxis">
          {xTicks.map((ratio) => <span key={ratio}>{ratio === 0 ? "now" : `-${Math.round(sampleWindow * ratio)}s`}</span>)}
        </div>
      </div>
      <small>{detail}</small>
    </section>
  );
}

function graphPoints(values: number[], maxValue: number) {
  const data = values.slice(-48);
  if (!data.length) return "0,50 100,50";
  const max = Math.max(maxValue, 1);
  return data.map((value, index) => {
    const x = data.length === 1 ? 100 : (index / (data.length - 1)) * 100;
    const y = 50 - Math.min(50, Math.max(0, value / max * 50));
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
}

function FactCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="overviewFactCard">
      <span>{label}</span><strong>{value}</strong><small>{detail}</small>
    </div>
  );
}

function SourceGlyph() {
  return <svg viewBox="0 0 48 48" aria-hidden="true"><circle cx="17" cy="17" r="6"/><circle cx="34" cy="20" r="5"/><path d="M7 39c1.2-8 5.6-12 10-12s8.8 4 10 12M27 39c.8-6.6 4-10 7.5-10S41 32.4 42 39"/></svg>;
}

function ExitGlyph() {
  return <svg viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="18"/><path d="M6 24h36M24 6c6 6.5 6 29.5 0 36M24 6c-6 6.5-6 29.5 0 36"/></svg>;
}
