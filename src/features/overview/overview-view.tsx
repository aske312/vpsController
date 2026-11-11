"use client";

import { uptime } from "../../shared/lib/control-plane-ui";
import { subscriptionTraffic, usageBytes, usagePeriodLabel } from "../mihomo/subscription-limits";
import type { MetricsPeriod, Overview as OverviewData, ProtocolImage, ResourceHistory } from "../../shared/types/control-plane";
import { graphSegments, knownMetric } from "../../shared/lib/resource-metrics";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { createApiClient } from "../../shared/lib/api-request";
import { useFailureNotifications } from "../../shared/notifications/notification-center";
import { ProtocolIcon } from "../../shared/components/protocol-icon";
import type { Module as MihomoModule } from "../mihomo/types";
import { createMihomoSummaryStore, EMPTY_MIHOMO_SUMMARY } from "./mihomo-summary";
import { componentPresentation } from "./component-state";
import { NodeComponentCard } from "./node-component-card";
import { useMetricsHistory } from "./use-metrics-history";


let mihomoSummaryCache: { token: string; observationVersion: number; store: ReturnType<typeof createMihomoSummaryStore> } | null = null;
function mihomoSummaryStore(token: string, observationVersion: number) {
  if (mihomoSummaryCache?.token !== token || mihomoSummaryCache.observationVersion !== observationVersion) {
    mihomoSummaryCache = { token, observationVersion, store: createMihomoSummaryStore(createApiClient(token)) };
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
  protocolImages: ProtocolImage[];
  installingProtocol: string;
  busy: boolean;
  onInstallProtocol: (image: ProtocolImage) => void;
  onUpdateProtocol: (image: ProtocolImage) => void;
  onAdoptProtocol: (image: ProtocolImage) => void;
  onPurgeProtocol: (image: ProtocolImage) => void;
  onRemoveProtocol: (image: ProtocolImage) => void;
  onOpenComponent: (image: ProtocolImage) => void;
};

const bytes = (value?: number | null) => {
  if (!knownMetric(value)) return "—";
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.max(0, Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1));
  return `${(value / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
};

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
  protocolImages,
  installingProtocol,
  busy,
  onInstallProtocol,
  onUpdateProtocol,
  onAdoptProtocol,
  onPurgeProtocol,
  onRemoveProtocol,
  onOpenComponent,
}: Props) {
  const [metricsPeriod, setMetricsPeriod] = useState<MetricsPeriod>("live");
  const metricsHistory = useMetricsHistory(token, metricsPeriod);
  const chartHistory = metricsHistory.data ? {
    load: metricsHistory.data.points.map((point) => point.cpu_percent),
    memory: metricsHistory.data.points.map((point) => point.memory_used_percent),
    rx: metricsHistory.data.points.map((point) => point.rx_bps),
    tx: metricsHistory.data.points.map((point) => point.tx_bps),
  } : { load: [], memory: [], rx: [], tx: [] };
  const resolution = metricsHistory.data?.resolution_s ?? 3;
  const historyPoints = metricsHistory.data?.points;
  const historyEnd = historyPoints?.at(-1)?.at;
  const mihomoImage = protocolImages.find((item) => item.id === "mihomo");
  const mihomoInstalled = Boolean(mihomoImage?.installed);
  const summaryStore = useMemo(() => mihomoSummaryStore(mihomoInstalled ? token : "", observationVersion), [mihomoInstalled, observationVersion, token]);
  const summary = useSyncExternalStore(summaryStore.subscribe, summaryStore.getSnapshot, () => EMPTY_MIHOMO_SUMMARY);
  const mihomoStatus = summary.status;
  const mihomoProfiles = summary.profiles;
  const mihomoProfileStats = summary.profileStats || {};
  const summaryFailures = useMemo(() => Object.fromEntries(Object.entries(summary.errors).map(([key, message]) => [key, { message: message!, network: summary.networkErrors?.[key as keyof typeof summary.errors] }])), [summary]);
  const retrySummary = useMemo(() => ({ label: "Повторить проверку", run: () => summaryStore.refresh(true) }), [summaryStore]);
  useFailureNotifications("overview-mihomo", "Mihomo на Обзоре", summaryFailures, false, retrySummary);
  useEffect(() => {
    if (!token || !mihomoInstalled) return;
    const initial = window.setTimeout(() => void summaryStore.refresh(), 0);
    const timer = window.setInterval(() => void summaryStore.refresh(true), 12000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [summaryStore, mihomoInstalled, token]);

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
  const profileCount = mihomoProfiles?.length ?? mihomoStatus?.profiles ?? (mihomoInstalled ? "—" : 0);
  const credentialsCount = mihomoProfiles?.reduce((count, profile) => count + profile.connections.length, 0) ?? mihomoStatus?.credentials;
  const totalAccessObjects = mihomoInstalled && credentialsCount === undefined ? null : (credentialsCount || 0);
  const memoryTotal = overview?.resources.memory_total;
  const diskTotal = overview?.resources.disk_total;
  const memoryFree = overview?.resources.memory_available;
  const diskFree = overview?.resources.disk_available;
  const networkRx = overview?.resources.network_rx;
  const networkTx = overview?.resources.network_tx;
  const networkTotal = knownMetric(networkRx) && knownMetric(networkTx) ? networkRx + networkTx : null;

  const mihomoChannelStates = useMemo(() => installedMihomoChannels.map((module) => {
    const profileRefs = (mihomoProfiles || []).filter((profile) => profile.channels.some((channel) => valueMatchesChannel(channel, module))).length;
    const inUse = (mihomoStatus?.channels_in_use || []).some((channel) => valueMatchesChannel(channel, module));
    return { module, profileRefs, inUse, runtimeReady: module.active };
  }), [installedMihomoChannels, mihomoProfiles, mihomoStatus?.channels_in_use]);

  const mihomoInUseCount = mihomoProfiles
    ? new Set(mihomoProfiles.flatMap((profile) => profile.connections.map((connection) => connection.component))).size
    : mihomoStatus?.channels_in_use.length ?? 0;
  const mihomoReadyCount = mihomoChannelStates.filter((item) => item.runtimeReady && !item.inUse).length;

  const mihomoUsageKnown = !mihomoInstalled || mihomoProfiles !== null || mihomoStatus !== null;
  const routesInUseLabel = mihomoUsageKnown ? mihomoInUseCount : "—";
  const routesReadyLabel = mihomoUsageKnown && (!mihomoInstalled || summary.modules !== null) ? mihomoReadyCount : "—";

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
            <span className={mihomoInUseCount ? "cyan" : ""}><small>ROUTES IN USE</small><strong>{routesInUseLabel}</strong></span>
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
              <div><dt>Routes in use</dt><dd className={mihomoInUseCount ? "ok" : ""}>{routesInUseLabel}</dd></div>
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
                      <small>{(mihomoImage?.installed_version || mihomoStatus?.core_version) ? `core ${mihomoImage?.installed_version || mihomoStatus?.core_version}` : "Версия ядра недоступна"}</small>
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
                          <span className="overviewManagedTraffic"><b>{usageBytes(subscriptionTraffic(traffic))}</b><small>{summary.errors.profileStats ? "Последний учтённый расход" : usagePeriodLabel(subscriptionTraffic(traffic))}</small></span>
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

            {!mihomoInstalled && (
              <div className="overviewNoRoutes"><span>◇</span><div><h2>Защищённые каналы не установлены</h2><p>Доступные компоненты показаны ниже в каталоге.</p></div></div>
            )}
          </div>

          <aside className="overviewExit">
            {mihomoInstalled && <div className="violet"><ExitGlyph /><strong>Internet</strong><small>{!mihomoUsageKnown ? "Ожидание данных Mihomo" : mihomoInUseCount ? "Mihomo route active" : "Mihomo ready / idle"}</small></div>}
          </aside>
        </div>
      </article>

      <article className="nodeOverviewWorkspace">
      <section className="nodeLiveSystem">
        <header>
          <div><p className="eyebrow">LIVE SYSTEM</p><h2>Телеметрия VPS</h2></div>
          <label>Период <select value={metricsPeriod} onChange={(event) => setMetricsPeriod(event.target.value as MetricsPeriod)} aria-label="Период истории метрик">
            <option value="live">5 минут</option><option value="hour">1 час</option><option value="day">24 часа</option><option value="week">7 дней</option><option value="quarter">90 дней</option>
          </select></label>
        </header>

        <div className="nodeLiveGraphs">
          <TaskGraph
            label="CPU"
            value={overview?.resources.cpu_percent == null ? "—" : `${overview.resources.cpu_percent.toFixed(0)}%`}
            detail={`Load ${overview?.resources.load1?.toFixed(2) ?? "—"} · ${overview?.resources.cpu_count ?? "—"} CPU`}
            series={[{ values: chartHistory.load, tone: "blue" }]}
            resolution={resolution}
            endAt={historyEnd}
            maxValue={100}
            yFormatter={(value) => `${Math.round(value)}%`}
          />
          <TaskGraph
            label="MEMORY"
            value={memUsed == null ? "—" : `${memUsed.toFixed(0)}%`}
            detail={`${bytes(memoryUsedBytes)} / ${bytes(memoryTotal)} · своб. ${bytes(memoryFree)}`}
            series={[{ values: chartHistory.memory, tone: "green" }]}
            resolution={resolution}
            endAt={historyEnd}
            maxValue={100}
            yFormatter={(value) => `${Math.round(value)}%`}
          />
          <TaskGraph
            label="NETWORK"
            value={networkRate.rx == null || networkRate.tx == null ? "—" : `↓ ${bytes(networkRate.rx)}/с  ↑ ${bytes(networkRate.tx)}/с`}
            detail={`Счётчики интерфейсов: ↓ ${bytes(networkRx)} · ↑ ${bytes(networkTx)}`}
            series={[
              { values: chartHistory.rx, tone: "blue" },
              { values: chartHistory.tx, tone: "green" },
            ]}
            resolution={resolution}
            endAt={historyEnd}
            maxValue={prettyMax(Math.max(...[networkRate.rx, networkRate.tx, ...chartHistory.rx, ...chartHistory.tx, 1].filter(knownMetric)))}
            yFormatter={(value) => `${bytes(value)}/с`}
            wide
            legend={["RX", "TX"]}
          />
        </div>

        <div className="nodeLiveFacts">
          <FactCard label="Диск" value={diskUsed == null ? "—" : `${diskUsed.toFixed(0)}%`} detail={`${bytes(diskUsedBytes)} / ${bytes(diskTotal)} · своб. ${bytes(diskFree)}`} />
          <FactCard label="Время работы" value={uptime(overview?.server.uptime_s)} detail={`${overview?.server.city || "Город не определён"}, ${overview?.server.country || "—"}`} />
          <FactCard label="Load · 1 мин" value={overview?.resources.load1?.toFixed(2) ?? "—"} detail={`${overview?.resources.cpu_count ?? "—"} CPU`} />
          <FactCard label="Счётчики сети" value={bytes(networkTotal)} detail={`С запуска интерфейсов · ↓ ${bytes(networkRx)} · ↑ ${bytes(networkTx)}`} />
          <FactCard label="Mihomo · доступы" value={`${credentialsCount ?? (mihomoInstalled ? "—" : 0)}`} detail={`Профили: ${profileCount} · используются: ${mihomoProfiles?.filter((profile) => profile.connections.length > 0).length ?? mihomoStatus?.profiles_in_use ?? (mihomoInstalled ? "—" : 0)}`} />
          <FactCard label="Маршруты" value={`${routesInUseLabel}`} detail={`${routesReadyLabel} готовы / ожидают`} />
          <FactCard label="RAM · свободно" value={bytes(memoryFree)} detail={memUsed == null ? "Нет данных" : `${memUsed.toFixed(0)}% занято`} />
        </div>
      </section>

      <section className="nodeComponents" aria-label="Компоненты узла">
        <header className="nodeComponentsHeader">
          <div><p className="eyebrow">NODE COMPONENTS</p><h2>Компоненты</h2></div>
          <div className="nodeComponentsCounters"><span><b>{installedServerModules.length}</b> установлено</span><span><b>{installableModules.length}</b> доступно</span></div>
        </header>
        <div className="nodeComponentsList">
          {componentImages.map(image => <NodeComponentCard key={image.id} image={image}
            coreVersion={image.id === "mihomo" ? mihomoStatus?.core_version : undefined}
            busy={busy} installingProtocol={installingProtocol}
            onInstall={onInstallProtocol} onUpdate={onUpdateProtocol}
            onOpen={onOpenComponent}
            onAdopt={onAdoptProtocol} onPurge={onPurgeProtocol} onRemove={onRemoveProtocol} />)}
          {!protocolImages.length && <p className="overviewEmpty">Каталог модулей временно недоступен.</p>}
        </div>
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
  endAt,
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
  endAt?: number;
}) {
  const dataLength = Math.max(...series.map((item) => item.values.length), 1);
  const hasSamples = series.some(item => item.values.some(knownMetric));
  const sampleWindow = Math.max((dataLength - 1) * resolution, resolution);
  const yTicks = [1, .75, .5, .25, 0];
  const xTicks = [1, .75, .5, .25, 0];
  const top = Math.max(maxValue, 1);

  return (
    <section className="nodeMetricCard" data-wide={wide}>
      <header>
        <div><span>{label}</span><strong>{value}</strong></div>
        {legend && <div className="nodeMetricLegend">{legend.map((item, index) => <span key={item} data-tone={index ? "green" : "blue"}>{item}</span>)}</div>}
      </header>
      <div className="nodeMetricPlot">
        <svg viewBox="0 0 100 50" preserveAspectRatio="none" aria-hidden="true">
          <g className="nodeMetricGrid">
            {[0, 12.5, 25, 37.5, 50].map((y) => <line key={`y-${y}`} x1="0" x2="100" y1={y} y2={y} />)}
            {[0, 20, 40, 60, 80, 100].map((x) => <line key={`x-${x}`} x1={x} x2={x} y1="0" y2="50" />)}
          </g>
          {series.flatMap((item, index) => graphSegments(item.values, top).map((points, segment) => <polyline key={`${index}-${segment}`} className="nodeMetricSeries" data-tone={item.tone} points={points} />))}
        </svg>
        <div className="nodeMetricYAxis">
          {yTicks.map((ratio) => <span key={ratio}>{yFormatter(top * ratio)}</span>)}
        </div>
        {hasSamples && <div className="nodeMetricXAxis">
          {xTicks.map((ratio) => {
            const seconds = sampleWindow * ratio;
            const unit = seconds >= 86400 ? [86400, "д"] as const : seconds >= 3600 ? [3600, "ч"] as const : seconds >= 60 ? [60, "м"] as const : [1, "с"] as const;
            return <span key={ratio}>{ratio === 0 ? endAt == null ? "конец" : new Date(endAt * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) : `−${Math.round(seconds / unit[0])}${unit[1]}`}</span>;
          })}
        </div>}
      </div>
      <small>{hasSamples ? detail : `Нет измерений за выбранный период · ${detail}`}</small>
    </section>
  );
}

function FactCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="nodeLiveFact">
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
