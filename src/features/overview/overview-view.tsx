"use client";
import { trafficBytes, uptime } from "../../shared/lib/control-plane-ui";

import type { Client, MetricsPeriod, Overview as OverviewData, ProtocolImage, ProtocolStatus, ResourceHistory } from "../../shared/types/control-plane";
import { graphSegments, knownMetric, networkSample } from "../../shared/lib/resource-metrics";
import { directProtocolSnapshot, directProtocolUnavailable } from "../../control-panel/protocol-snapshot";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createApiClient } from "../../shared/lib/api-request";
import { useFailureNotifications } from "../../shared/notifications/notification-center";
import { formatModuleVersion } from "../../shared/lib/format-version";
import { ProtocolHealthBadge } from "../../shared/components/protocol-health-badge";
import { ProtocolIcon } from "../../shared/components/protocol-icon";
import type { Module as MihomoModule } from "../mihomo/types";
import { createMihomoSummaryStore, EMPTY_MIHOMO_SUMMARY } from "./mihomo-summary";
import { componentPresentation } from "./component-state";
import { useMetricsHistory } from "./use-metrics-history";
import { directChannelObservation } from "./direct-observation";

type ProtocolId = "wg" | "awg" | "shadowsocks" | "vless-reality-xhttp" | "hysteria2" | "tuic" | "trojan" | "openvpn" | "ikev2";

let mihomoSummaryCache: { token: string; store: ReturnType<typeof createMihomoSummaryStore> } | null = null;
function mihomoSummaryStore(token: string) {
  if (mihomoSummaryCache?.token !== token) {
    mihomoSummaryCache = { token, store: createMihomoSummaryStore(createApiClient(token)) };
  }
  return mihomoSummaryCache.store;
}


type Props = {
  token: string;
  observationVersion: number;
  overview: OverviewData | null;
  memUsed: number | null;
  diskUsed: number | null;
  memoryUsedBytes: number | null;
  diskUsedBytes: number | null;
  networkRate: { rx: number | null; tx: number | null };
  resourceHistory: ResourceHistory;
  clients: Client[];
  protocolImages: ProtocolImage[];
  installingProtocol: string;
  busy: boolean;
  onInstallProtocol: (image: ProtocolImage) => void;
  onUpdateProtocol: (image: ProtocolImage) => void;
  onOpenProtocol: (image: ProtocolImage) => void;
  onAdoptProtocol: (image: ProtocolImage) => void;
  onPurgeProtocol: (image: ProtocolImage) => void;
};

const directShort: Record<ProtocolId, string> = {
  wg: "WG",
  awg: "AWG",
  shadowsocks: "SS",
  "vless-reality-xhttp": "VLESS",
  hysteria2: "HY2",
  tuic: "TUIC",
  trojan: "TRJ",
  openvpn: "OVPN",
  ikev2: "IKE",
};

const directName: Record<ProtocolId, string> = {
  wg: "WireGuard",
  awg: "AmneziaWG",
  shadowsocks: "Shadowsocks",
  "vless-reality-xhttp": "VLESS Reality",
  hysteria2: "Hysteria2",
  tuic: "TUIC v5",
  trojan: "Trojan",
  openvpn: "OpenVPN",
  ikev2: "IKEv2",
};

const bytes = (value?: number | null) => {
  if (!knownMetric(value)) return "—";
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.max(0, Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1));
  return `${(value / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
};

const protocolMark = (id: string) => id === "mihomo" ? "M" : directShort[id as ProtocolId] || id.toUpperCase();
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
  observationVersion,
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
  onOpenProtocol,
  onAdoptProtocol,
  onPurgeProtocol,
}: Props) {
  const [metricsPeriod, setMetricsPeriod] = useState<MetricsPeriod>("live");
  const metricsHistory = useMetricsHistory(token, metricsPeriod);
  const chartHistory = metricsHistory.data ? {
    load: metricsHistory.data.points.map((point) => point.cpu_percent),
    memory: metricsHistory.data.points.map((point) => point.memory_used_percent),
    rx: metricsHistory.data.points.map((point) => point.rx_bps),
    tx: metricsHistory.data.points.map((point) => point.tx_bps),
  } : metricsPeriod === "live" ? resourceHistory : { load: [], memory: [], rx: [], tx: [] };
  const resolution = metricsHistory.data?.resolution_s ?? 3;
  const mihomoImage = protocolImages.find((item) => item.id === "mihomo");
  const mihomoInstalled = Boolean(mihomoImage?.installed);
  const summaryStore = useMemo(() => mihomoSummaryStore(mihomoInstalled ? token : ""), [mihomoInstalled, token]);
  const summary = useSyncExternalStore(summaryStore.subscribe, summaryStore.getSnapshot, () => EMPTY_MIHOMO_SUMMARY);
  const mihomoStatus = summary.status;
  const mihomoProfiles = summary.profiles;
  const mihomoProfileStats = summary.profileStats || {};
  const summaryFailures = useMemo(() => Object.fromEntries(Object.entries(summary.errors).map(([key, message]) => [key, { message: message!, network: summary.networkErrors?.[key as keyof typeof summary.errors] }])), [summary]);
  const retrySummary = useMemo(() => ({ label: "Повторить проверку", run: () => summaryStore.refresh(true) }), [summaryStore]);
  useFailureNotifications("overview-mihomo", "Mihomo на Обзоре", summaryFailures, false, retrySummary);
  const [directStatuses, setDirectStatuses] = useState<Partial<Record<ProtocolId, ProtocolStatus>>>({});
  const [directStatusFailures, setDirectStatusFailures] = useState<Partial<Record<ProtocolId, boolean>>>({});
  const [directSnapshotKey, setDirectSnapshotKey] = useState("");
  const [directRates, setDirectRates] = useState<Partial<Record<ProtocolId, { rx: number | null; tx: number | null }>>>({});
  const directSamples = useRef<Partial<Record<ProtocolId, { rx: number; tx: number; at: number }>>>({});
  const directRequest = useMemo(() => createApiClient(token), [token]);
  const directReadInFlight = useRef(false);
  const directReadGeneration = useRef(0);

  const directChannels = useMemo(
    () => protocolImages.filter((item) =>
      item.installed &&
      item.id !== "mihomo" &&
      (["wg", "awg", "shadowsocks", "vless-reality-xhttp", "hysteria2", "tuic", "trojan", "openvpn", "ikev2"] as string[]).includes(item.id),
    ),
    [protocolImages],
  );
  const directIds = directChannels.map((image) => image.id).join(",");
  const directKey = `${token}:${observationVersion}:${directIds}`;
  const directProtocols = useMemo(() => directIds ? directIds.split(",") as ProtocolId[] : [], [directIds]);
  const loadDirectStatuses = useCallback(async () => {
    if (!token || !directProtocols.length) {
      setDirectStatuses({});
      setDirectStatusFailures({});
      setDirectRates({});
      directSamples.current = {};
      return;
    }
    if (directReadInFlight.current) return;
    directReadInFlight.current = true;
    const generation = directReadGeneration.current;
    try {
      const results = await Promise.allSettled(directProtocols.map(async (protocol) =>
        directProtocolSnapshot(await directRequest(`/protocols/${protocol}/status`, { cache: "no-store" }), protocol)));
      if (generation !== directReadGeneration.current) return;

      const now = Date.now();
      const statusUpdates: Partial<Record<ProtocolId, ProtocolStatus>> = {};
      const failureUpdates: Partial<Record<ProtocolId, boolean>> = {};
      const rateUpdates: Partial<Record<ProtocolId, { rx: number | null; tx: number | null }>> = {};
      results.forEach((result, index) => {
        const protocol = directProtocols[index];
        if (!protocol) return;
        failureUpdates[protocol] = result.status !== "fulfilled";
        if (result.status !== "fulfilled") {
          delete directSamples.current[protocol];
          rateUpdates[protocol] = { rx: null, tx: null };
          return;
        }
        statusUpdates[protocol] = result.value;
        const sampled = networkSample({ network_rx: result.value.interface_rx_bytes, network_tx: result.value.interface_tx_bytes }, directSamples.current[protocol] || null, now);
        rateUpdates[protocol] = sampled.rate;
        if (sampled.sample) directSamples.current[protocol] = sampled.sample;
      });
      setDirectStatuses((current) => {
        const next = { ...current, ...statusUpdates };
        for (const protocol of directProtocols) if (failureUpdates[protocol]) next[protocol] = directProtocolUnavailable(current[protocol]);
        return next;
      });
      setDirectSnapshotKey(directKey);
      setDirectStatusFailures((current) => ({ ...current, ...failureUpdates }));
      if (Object.keys(rateUpdates).length) setDirectRates((current) => ({ ...current, ...rateUpdates }));
    } finally { directReadInFlight.current = false; }
  }, [directKey, directProtocols, directRequest, token]);

  useEffect(() => {
    if (!token || !mihomoInstalled) return;
    const initial = window.setTimeout(() => void summaryStore.refresh(), 0);
    const timer = window.setInterval(() => void summaryStore.refresh(true), 12000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [summaryStore, mihomoInstalled, token]);

  useEffect(() => {
    directReadGeneration.current += 1;
    directSamples.current = {};
    const initial = window.setTimeout(() => void loadDirectStatuses(), 0);
    const timer = directProtocols.length ? window.setInterval(() => void loadDirectStatuses(), 6000) : null;
    return () => { directReadGeneration.current += 1; window.clearTimeout(initial); if (timer !== null) window.clearInterval(timer); };
  }, [directProtocols, loadDirectStatuses, observationVersion]);

  const installedMihomoChannels = useMemo(
    () => (summary.modules || []).filter((item) => item.category === "transport" && item.installed),
    [summary.modules],
  );

  const installedServerModules = useMemo(() => protocolImages.filter((item) => item.component_state?.installation.state === "installed"), [protocolImages]);
  const installableModules = useMemo(() => protocolImages.filter((item) => componentPresentation(item).canInstall), [protocolImages]);
  const componentImages = useMemo(() => [...protocolImages].sort((left, right) => {
    const rank = (item: ProtocolImage) => item.installed ? 0 : item.installable ? 1 : 2;
    return rank(left) - rank(right) || left.name.localeCompare(right.name, "ru");
  }), [protocolImages]);
  const stableDirectClients = clients.filter((client) => client.quality === "stable").length;
  const attentionDirectClients = clients.filter((client) => !client.quality || ["warning", "error", "unknown"].includes(client.quality)).length;
  const offlineDirectClients = clients.filter((client) => client.quality === "offline").length;

  const profileCount = mihomoProfiles?.length ?? mihomoStatus?.profiles ?? (mihomoInstalled ? "—" : 0);
  const credentialsCount = mihomoProfiles?.reduce((count, profile) => count + profile.connections.length, 0) ?? mihomoStatus?.credentials;
  const totalAccessObjects = mihomoInstalled && credentialsCount === undefined ? null : (credentialsCount || 0) + clients.length;
  const memoryTotal = overview?.resources.memory_total;
  const diskTotal = overview?.resources.disk_total;
  const memoryFree = overview?.resources.memory_available;
  const diskFree = overview?.resources.disk_available;
  const networkRx = overview?.resources.network_rx;
  const networkTx = overview?.resources.network_tx;
  const networkTotal = knownMetric(networkRx) && knownMetric(networkTx) ? networkRx + networkTx : null;
  const missingMetrics = Object.entries(overview?.resources.observations || {}).filter(([, sample]) => !sample.available).map(([name]) => name);

  const mihomoChannelStates = useMemo(() => installedMihomoChannels.map((module) => {
    const profileRefs = (mihomoProfiles || []).filter((profile) => profile.channels.some((channel) => valueMatchesChannel(channel, module))).length;
    const inUse = (mihomoStatus?.channels_in_use || []).some((channel) => valueMatchesChannel(channel, module));
    return { module, profileRefs, inUse, runtimeReady: module.active };
  }), [installedMihomoChannels, mihomoProfiles, mihomoStatus?.channels_in_use]);

  const mihomoInUseCount = mihomoProfiles
    ? new Set(mihomoProfiles.flatMap((profile) => profile.connections.map((connection) => connection.component))).size
    : mihomoStatus?.channels_in_use.length ?? 0;
  const mihomoReadyCount = mihomoChannelStates.filter((item) => item.runtimeReady && !item.inUse).length;

  const directChannelStates = useMemo(() => directChannels.map((image) => {
    const protocol = image.id as ProtocolId;
    const status = directStatuses[protocol];
    const protocolClients = clients.filter((client) => client.protocol === protocol);
    const observation = directChannelObservation(status, protocolClients, directRates[protocol] || { rx: null, tx: null },
      directSnapshotKey !== directKey || Boolean(directStatusFailures[protocol]));
    return { image, protocol, status, protocolClients, ...observation };
  }), [clients, directChannels, directKey, directRates, directSnapshotKey, directStatusFailures, directStatuses]);

  const directInUseCount = directChannelStates.filter((item) => item.inUse).length;
  const directReadyCount = directChannelStates.filter((item) => !item.statusFailed && item.status?.health?.state === "READY").length;
  const directStoppedCount = directChannelStates.filter((item) => !item.statusFailed && item.status?.health?.state === "ERROR").length;
  const directUnknownCount = directChannelStates.filter((item) => item.statusFailed).length;
  const routesInUse = mihomoInUseCount + directInUseCount;
  const routesReady = mihomoReadyCount + directReadyCount;
  const mihomoUsageKnown = !mihomoInstalled || mihomoProfiles !== null || mihomoStatus !== null;
  const routesInUseLabel = mihomoUsageKnown && !directUnknownCount ? routesInUse : "—";
  const routesReadyLabel = mihomoUsageKnown && !directUnknownCount && (!mihomoInstalled || summary.modules !== null) ? routesReady : "—";

  return (
    <section className="overview" aria-label="Обзор инфраструктуры">
      <article className="overviewTopology">
        <header className="overviewTopologyHead">
          <div>
            <p className="eyebrow">ROUTING TOPOLOGY</p>
            <h1>Доступ и маршруты</h1>
          </div>
          <div className="overviewTopologyStats">
            <span><small>ACCESS OBJECTS</small><strong>{totalAccessObjects ?? "—"}</strong></span>
            {mihomoInstalled && <span className="violet"><small>PROFILES</small><strong>{profileCount}</strong></span>}
            {mihomoInstalled && <span className="violet"><small>CHANNELS</small><strong>{summary.modules ? installedMihomoChannels.length : mihomoStatus?.channels_installed ?? "—"}</strong></span>}
            <span className={routesInUse ? "cyan" : ""}><small>ROUTES IN USE</small><strong>{routesInUseLabel}</strong></span>
          </div>
        </header>

        <div className="overviewFlow">
          <aside className="overviewAccess">
            <p className="eyebrow">CLIENT ACCESS</p>
            <div className="overviewAccessGlyph"><SourceGlyph /></div>
            <strong>Клиенты</strong>
            <small>{totalAccessObjects === null ? "Загружаются данные доступа…" : totalAccessObjects ? `${totalAccessObjects} настроенных объектов доступа` : "Объекты доступа ещё не созданы"}</small>
            <dl>
              <div><dt>Mihomo credentials</dt><dd>{credentialsCount ?? (mihomoInstalled ? "—" : 0)}</dd></div>
              <div><dt>Direct clients</dt><dd>{clients.length}</dd></div>
              <div><dt>Routes in use</dt><dd className={routesInUse ? "ok" : ""}>{routesInUseLabel}</dd></div>
              <div><dt>Ready / idle</dt><dd>{routesReadyLabel}</dd></div>
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
                    {!mihomoStatus ? (summary.errors.status ? "СТАТУС НЕДОСТУПЕН" : "ПРОВЕРКА СОСТОЯНИЯ") : mihomoStatus.active ? (mihomoInUseCount ? "CORE ONLINE  IN USE" : "CORE ONLINE  IDLE") : "CORE STOPPED"}
                  </span>
                </header>

                <div className="overviewMihomoBody">
                  <section className="overviewManagedProfiles">
                    <div className="overviewManagedHead"><div className="overviewSectionLabel"><b>Профили</b><span>{profileCount}</span></div></div>
                    <div className="overviewManagedProfileList">
                      {(mihomoProfiles || []).map((profile) => {
                        const assignedComponents = [...new Set(profile.connections?.length ? profile.connections.map((connection) => connection.component) : profile.channels)];
                        const traffic = mihomoProfileStats[profile.id];
                        return <div className="overviewManagedProfileRow" key={profile.id}>
                          <span className="profileDot" />
                          <p><b>{profile.name}</b><small>{profile.connections?.length || profile.channels.length} подключений</small></p>
                          <div className="overviewManagedProtocolSet">
                            {assignedComponents.map((component) => <span key={component} title={component.replace("transport-", "")}><ProtocolIcon protocol={component} /></span>)}
                            {!assignedComponents.length && <em>—</em>}
                          </div>
                          <span className="overviewManagedTraffic"><b>↓ {trafficBytes(traffic, "rx_bytes")}</b><small>↑ {trafficBytes(traffic, "tx_bytes")}</small></span>
                        </div>;
                      })}
                      {mihomoProfiles === null && <p className="overviewEmpty" role="status">{summary.errors.profiles ? "Список профилей временно недоступен." : "Загрузка профилей…"}</p>}
                      {mihomoProfiles?.length === 0 && <p className="overviewEmpty">Профили ещё не созданы.</p>}
                    </div>
                  </section>
                </div>

                <footer>
                  <span>{credentialsCount ?? "—"} credentials</span>
                  <span>{mihomoStatus?.endpoint || overview?.server.public_endpoint || overview?.server.public_ip || "—"}</span>
                </footer>
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
                    {directUnknownCount ? `${directInUseCount} WORKS ${directReadyCount} READY ${directUnknownCount} UNKNOWN` : directInUseCount ? `${directInUseCount} WORKS ${directReadyCount} READY` : `${directReadyCount} READY ${directStoppedCount} ERROR`}
                  </span>
                </header>

                <div className="overviewDirectList">
                  {directChannelStates.map(({ image, protocol, status, statusFailed, protocolClients, sessions, rx, tx, configured, avgLatency }) => {
                    return (
                      <div key={image.id}>
                        <span className={`overviewProtocolMark protocol-${protocol}`} title={directShort[protocol]}><ProtocolIcon protocol={protocol} /></span>
                        <p>
                          <b>{directName[protocol]}</b>
                          <small>{statusFailed ? "runtime status недоступен" : configured === null ? "проверяем конфигурацию" : configured ? `${protocolClients.length} clients  ${sessions} sessions` : "установлен, конфигурация не обнаружена"}</small>
                        </p>
                        <span className="overviewDirectTraffic"><b>↓ {bytes(rx)}/с</b><small>↑ {bytes(tx)}/с</small></span>
                        <span className="overviewDirectLatency"><b>{avgLatency !== null ? `${avgLatency} ms` : "—"}</b><small>{statusFailed ? "—" : status?.online_peers ?? "—"} peers online</small></span>
                        <ProtocolHealthBadge health={statusFailed ? undefined : directStatuses[protocol]?.health} />
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
            {mihomoInstalled && <div className="violet"><ExitGlyph /><strong>Internet</strong><small>{!mihomoUsageKnown ? "Ожидание данных Mihomo" : mihomoInUseCount ? "Mihomo route active" : "Mihomo ready / idle"}</small></div>}
            {directChannels.length > 0 && <div className="cyan"><ExitGlyph /><strong>Internet</strong><small>{directInUseCount ? "direct traffic active" : "direct routes idle"}</small></div>}
          </aside>
        </div>
      </article>

      <article className="overviewNodeWorkspace">
      <section className="overviewTelemetry">
        <header>
          <div><p className="eyebrow">LIVE SYSTEM</p><h2>Телеметрия VPS</h2></div>
          <label>Период <select value={metricsPeriod} onChange={(event) => setMetricsPeriod(event.target.value as MetricsPeriod)} aria-label="Период истории метрик">
            <option value="live">5 минут</option><option value="day">24 часа</option><option value="week">7 дней</option><option value="quarter">90 дней</option>
          </select></label>
        </header>
        <p className="overviewTelemetryNote">{overview?.resources.stale || metricsHistory.stale ? "Данные временно не обновляются" : missingMetrics.length ? `Данные недоступны: ${missingMetrics.join(", ")}` : metricsHistory.pending ? "Загрузка серверной истории…" : "История хранится на VPS · пропуски показаны разрывами"}
          {metricsHistory.data?.settings.enabled === false && " · запись истории выключена в Службах"}
          {metricsHistory.data?.settings.trimmed_at && " · старые данные сокращены по лимиту места"}
        </p>

        <div className="overviewTaskGraphs">
          <TaskGraph
            label="CPU"
            value={overview?.resources.cpu_percent == null ? "—" : `${overview.resources.cpu_percent.toFixed(0)}%`}
            detail={`load ${overview?.resources.load1?.toFixed(2) ?? "—"}  ${overview?.resources.cpu_count ?? "—"} cores`}
            series={[{ values: chartHistory.load, tone: "blue" }]}
            resolution={resolution}
            maxValue={100}
            yFormatter={(value) => `${Math.round(value)}%`}
          />
          <TaskGraph
            label="MEMORY"
            value={memUsed == null ? "—" : `${memUsed.toFixed(0)}%`}
            detail={`${bytes(memoryUsedBytes)} / ${bytes(memoryTotal)}  free ${bytes(memoryFree)}`}
            series={[{ values: chartHistory.memory, tone: "green" }]}
            resolution={resolution}
            maxValue={100}
            yFormatter={(value) => `${Math.round(value)}%`}
          />
          <TaskGraph
            label="NETWORK"
            value={networkRate.rx == null || networkRate.tx == null ? "—" : `↓ ${bytes(networkRate.rx)}/с  ↑ ${bytes(networkRate.tx)}/с`}
            detail={`interface total ↓ ${bytes(networkRx)}  ↑ ${bytes(networkTx)}`}
            series={[
              { values: chartHistory.rx, tone: "blue" },
              { values: chartHistory.tx, tone: "green" },
            ]}
            resolution={resolution}
            maxValue={prettyMax(Math.max(...[networkRate.rx, networkRate.tx, ...chartHistory.rx, ...chartHistory.tx, 1].filter(knownMetric)))}
            yFormatter={(value) => `${bytes(value)}/с`}
            wide
            legend={["RX", "TX"]}
          />
        </div>

        <div className="overviewSystemFacts">
          <FactCard label="DISK USED" value={diskUsed == null ? "—" : `${diskUsed.toFixed(0)}%`} detail={`${bytes(diskUsedBytes)} / ${bytes(diskTotal)}  free ${bytes(diskFree)}`} />
          <FactCard label="UPTIME" value={uptime(overview?.server.uptime_s)} detail={`${overview?.server.city || "Город не определён"}  ${overview?.server.country || "—"}`} />
          <FactCard label="LOAD 1M" value={overview?.resources.load1?.toFixed(2) ?? "—"} detail={`${overview?.resources.cpu_count ?? "—"} CPU cores`} />
          <FactCard label="TRAFFIC TOTAL" value={bytes(networkTotal)} detail={`↓ ${bytes(networkRx)}  ↑ ${bytes(networkTx)}`} />
          <FactCard label="DIRECT CLIENTS" value={`${clients.length}`} detail={`${stableDirectClients} stable  ${attentionDirectClients} attention  ${offlineDirectClients} offline`} />
          <FactCard label="MIHOMO CREDENTIALS" value={`${credentialsCount ?? (mihomoInstalled ? "—" : 0)}`} detail={`${profileCount} profiles  ${mihomoProfiles?.filter((profile) => profile.connections.length > 0).length ?? mihomoStatus?.profiles_in_use ?? (mihomoInstalled ? "—" : 0)} in use`} />
          <FactCard label="ROUTES IN USE" value={`${routesInUseLabel}`} detail={`${routesReadyLabel} ready / idle`} />
          <FactCard label="FREE MEMORY" value={bytes(memoryFree)} detail={memUsed == null ? "Unknown" : `${memUsed.toFixed(0)}% currently used`} />
        </div>
      </section>

      <section className="overviewComponents">
        <header>
          <div><p className="eyebrow">NODE COMPONENTS</p><h2>Компоненты</h2></div>
          <div className="overviewComponentCounters"><span><b>{installedServerModules.length}</b> установлено</span><span><b>{installableModules.length}</b> доступно</span></div>
        </header>

        <section className="overviewComponentRegistry" aria-label="Управление модулями узла">
          <div>
            {componentImages.map((image) => {
              const observation = directChannelStates.find((item) => item.image.id === image.id);
              const healthState = observation?.status?.health?.base_state;
              const matchingHealth = image.component_state?.health.state === "possible_issues" && healthState === "WARN"
                || image.component_state?.health.state === "error" && healthState === "ERROR";
              const state = componentPresentation(image, matchingHealth && !observation?.statusFailed ? observation?.status?.health?.reason : undefined);
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

              return (
                <div className="overviewComponentRow" key={image.id}>
                  <span className={`overviewComponentLogo overviewProtocolMark protocol-${image.id}`} title={protocolMark(image.id)}><ProtocolIcon protocol={image.id} /></span>
                  <div className="overviewComponentIdentity">
                    <p><b>{image.name}</b></p>
                  </div>
                  <div className={`overviewComponentState ${state.summary.tone}`}>
                    <span title={`Установка: ${state.installation}`} aria-label={`Установка: ${state.installation}`}>{image.component_state?.installation.state === "installed" ? "УСТ" : image.component_state?.installation.state === "not_installed" ? "НЕТ" : image.component_state?.installation.state === "incomplete" ? "НЕПОЛН" : "УСТ ?"}</span>
                    <span title={`Служба: ${state.runtime}`} aria-label={`Служба: ${state.runtime}`}>{image.component_state?.runtime.state === "running" ? "RUN" : image.component_state?.runtime.state === "stopped" ? "STOP" : image.component_state?.runtime.state === "error" ? "ERR" : "RUN ?"}</span>
                    <span title={`Проверка: ${state.health}. ${state.summary.description}`} aria-label={`Проверка: ${state.health}`}>{image.component_state?.health.state === "healthy" ? "OK" : image.component_state?.health.state === "possible_issues" ? "WARN" : image.component_state?.health.state === "error" ? "ERR" : image.component_state?.health.state === "unchecked" ? "НЕ ПРОВ" : "CHECK ?"}</span>
                    {state.operation && <span>{state.operation}</span>}
                    {image.management?.retained && <span title="Настройки и подключения сохранены">ДАННЫЕ</span>}
                    {image.installed && state.readOnly && <span title={image.management?.reason}>READ ONLY</span>}
                  </div>
                  <div className="overviewComponentFooter">
                  <div className="overviewComponentVersion" title={`Версия: ${displayedVersion}`}>
                    <b>{displayedVersion}</b>
                    {(image.update_available || image.update_via_release) && <small title="Доступна новая версия">↑ {availableVersion}</small>}
                  </div>
                  <div className="overviewComponentAction">
                    {image.management?.retained && <button type="button" title="Очистить сохранённые данные" disabled={busy || Boolean(installingProtocol) || Boolean(image.component_state?.operation)} onClick={() => onPurgeProtocol(image)}>Очистить</button>}
                    {image.installed && image.management && image.management.state !== "managed" ? (
                      <button type="button" title="Принять под управление" disabled={busy || !state.canAdopt} onClick={() => onAdoptProtocol(image)}>Принять</button>
                    ) : !image.installed ? (
                      <button type="button" disabled={!state.canInstall || busy || Boolean(installingProtocol)} onClick={() => onInstallProtocol(image)}>
                        {!image.installable ? "Недоступен" : installingProtocol === image.id ? "Установка…" : "Установить"}
                      </button>
                    ) : image.update_available && image.id !== "mihomo" ? (
                      <button type="button" title="Открыть компонент: доступно обновление" onClick={() => onOpenProtocol(image)}>Обновление</button>
                    ) : image.update_available ? (
                      <button type="button" className={image.update_breaking ? "warning" : ""} disabled={busy || !state.canUpdate || Boolean(installingProtocol)} onClick={() => onUpdateProtocol(image)}>
                        {installingProtocol === `update-${image.id}` ? "Обновление…" : "Обновить"}
                      </button>
                    ) : (
                      image.id !== "mihomo" && ["warning", "error"].includes(state.summary.tone) && <button type="button" onClick={() => onOpenProtocol(image)}>Проверить</button>
                    )}
                  </div>
                  </div>
                  {["warning", "error"].includes(state.summary.tone) && <small className="overviewComponentIssue">{state.summary.description}</small>}
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
type GraphSeries = { values: (number | null)[]; tone: GraphTone };

function TaskGraph({
  label,
  value,
  detail,
  series,
  maxValue,
  yFormatter,
  wide = false,
  legend,
  resolution = 3,
}: {
  label: string;
  value: string;
  detail: string;
  series: GraphSeries[];
  maxValue: number;
  yFormatter: (value: number) => string;
  wide?: boolean;
  legend?: string[];
  resolution?: number;
}) {
  const dataLength = Math.max(...series.map((item) => item.values.length), 1);
  const sampleWindow = Math.max((dataLength - 1) * resolution, resolution);
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
          {series.flatMap((item, index) => graphSegments(item.values, top).map((points, segment) => <polyline key={`${index}-${segment}`} className={`taskSeries ${item.tone}`} points={points} />))}
        </svg>
        <div className="taskYAxis">
          {yTicks.map((ratio) => <span key={ratio}>{yFormatter(top * ratio)}</span>)}
        </div>
        <div className="taskXAxis">
          {xTicks.map((ratio) => {
            const seconds = sampleWindow * ratio;
            const unit = seconds >= 86400 ? [86400, "д"] as const : seconds >= 3600 ? [3600, "ч"] as const : seconds >= 60 ? [60, "м"] as const : [1, "с"] as const;
            return <span key={ratio}>{ratio === 0 ? "now" : `−${Math.round(seconds / unit[0])}${unit[1]}`}</span>;
          })}
        </div>
      </div>
      <small>{detail}</small>
    </section>
  );
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
