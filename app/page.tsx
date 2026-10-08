"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ConnectionDialog, type ConnectionSettings } from "./connection-dialog";
import type { ConnectionProfile } from "./connection-profile";
import { ConnectionsView } from "./connections-view";
import { LegalFooter } from "./legal";
import { ProtocolIcon } from "./protocol-icon";
import { ProtocolWorkspace } from "./protocol-workspace";
import { LightNavigation } from "../src/light-navigation";
import { useNotifications } from "../src/notifications/notification-center";

export type Protocol = "wg" | "awg" | "hysteria2" | "tuic" | "xray";
type Tab = "overview" | "security" | "application" | "services" | Protocol | "clients";
type MetricsPeriod = "live" | "day" | "week" | "quarter";
type SecurityState = "inactive" | "active" | "warning" | "critical";
type ResourceHistory = { load: Array<number | null>; memory: Array<number | null>; disk: Array<number | null>; rx: Array<number | null>; tx: Array<number | null> };
type MetricsHistory = {
  period: MetricsPeriod;
  resolution_s: number;
  points: Array<{
    at: number; cpu_percent: number | null; memory_used_percent: number | null; disk_used_percent: number | null;
    rx_bps: number | null; tx_bps: number | null;
  }>;
  settings: { enabled: boolean; raw_hours: number; minute_days: number; hour_days: number; disk_limit_mb: number; used_bytes: number; trimmed_at?: number | null };
  error?: string;
};
type ApplicationAction = "restart" | "update" | "test-update" | "test-rollback" | "network-check" | "integrity-check" | "identity" | "secure" | "system-update" | "kernel-update" | "vpn-firewall" | "optimize" | "reboot" | "poweroff";
export type Client = {
  id: string; name: string; protocol: Protocol; public_key: string; endpoint?: string;
  address: string; handshake_age_s?: number; rx_bytes: number; tx_bytes: number;
  quality?: "stable" | "warning" | "error" | "offline"; latency_ms?: number; jitter_ms?: number; packet_loss_percent?: number; quality_reason?: string;
  update_state?: "paused" | "attention" | "incompatible"; update_message?: string;
  created_at?: string;
};
type Overview = {
  server: { name: string; public_ip: string; city: string; country: string; country_code: string; uptime_s: number };
  resources: { load1: number; cpu_percent: number; cpu_count: number; memory_total: number; memory_available: number; disk_total: number; disk_available: number; network_rx: number; network_tx: number; uptime_s?: number };
  protocols: Partial<Record<Protocol, { interface: string; port: number; active: boolean }>>;
};
type ApplicationStatus = {
  api: { active: boolean; enabled: boolean; service_id?: string; unit?: string; endpoint?: string; restarts?: number; uptime_seconds?: number };
  containers: Array<{
    Name?: string; Service?: string; State?: string; Status?: string; Health?: string;
    component_name?: string; purpose?: string; healthy?: boolean; status_text?: string;
    service_id?: string; unit?: string; endpoint?: string; enabled?: boolean; restarts?: number; uptime_seconds?: number; installed?: boolean;
  }>;
  action: {
    unit?: string; action?: string; state?: string; result?: string; started_at?: string; updated_at?: string;
    progress?: number; message?: string;
  };
  service_mode?: { active: boolean; rollback_available?: boolean };
  release?: { branch: "light" | "test-light"; current_commit: string; latest_commit?: string; outdated?: boolean | null; error?: string; refreshing?: boolean };
  runtime?: { mode: "systemd" | "legacy-docker" | "incomplete"; migration_required: boolean };
  checked_at?: string;
};
export type ProtocolImage = {
  id: string; name: string; version: string; description: string; category: string; category_name: string;
  kind: "tunnel" | "agent"; status: "available" | "planned"; installable: boolean;
  interface: string; installed: boolean; removable: boolean;
  installed_version?: string; available_version?: string; update_available?: boolean; update_breaking?: boolean;
  version_checked_at?: string; version_error?: string; version_channel?: "release" | "package";
};
type AutomationSchedule = {
  enabled: boolean; cadence: "daily" | "weekly" | "monthly"; weekday: string; hour: number; minute: number;
};
type AutomationKind = "reboot" | "cleanup" | "protocol_scan" | "application_update" | "kernel_update";
type ServicesStatus = {
  items: Array<{
    id: string; name: string; unit: string; installed: boolean; active: boolean; state: string; substate: string;
    enabled: boolean; unit_file_state: string; restarts: number; active_since: string; description: string;
    controls: string[]; disabled_controls?: string[];
  }>;
  failed_units: number;
  reboot_required: boolean;
  automation: Record<AutomationKind, AutomationSchedule>;
  timers: Record<AutomationKind, { installed: boolean; active: boolean; last_trigger: string; next_run: string }>;
  panel_access?: { mode: "external" | "vpn"; public: boolean; vpn_urls: string[] };
  service_mode?: { active: boolean };
  logging?: { persistent: boolean; retention_days: number; automatic_cleanup: boolean; disk_usage: string };
};
type LiveStatus = {
  resources: Overview["resources"];
  protocols: Partial<Record<Protocol, {
    active: boolean; peers: number; online_peers: number; interface_rx_bytes: number; interface_tx_bytes: number;
  }>>;
  clients: Client[];
  security: { firewall_active: boolean; fail2ban_active: boolean; ssh_listening: boolean };
};
type LoggingSettings = { persistent: boolean; retention_days: number };
type ConfirmationRequest = {
  title: string; message: string; confirmLabel: string; phrase?: string; danger?: boolean;
  resolve: (confirmed: boolean) => void;
};
const appVersion = process.env.NEXT_PUBLIC_APP_VERSION || "v1.0.0";
const buildCommit = process.env.NEXT_PUBLIC_BUILD_COMMIT || "unknown";
const buildBranch = process.env.NEXT_PUBLIC_RELEASE_BRANCH || "light";
export type ProtocolStatus = {
  protocol: Protocol; interface: string; active: boolean; service_active: boolean; service_enabled: boolean;
  active_since: string; address: string; listen_port: number; mtu: number; peers: number; online_peers: number;
  endpoints: number; last_handshake_age_s?: number; peer_rx_bytes: number; peer_tx_bytes: number;
  interface_rx_bytes: number; interface_tx_bytes: number; rx_errors: number; tx_errors: number;
  rx_dropped: number; tx_dropped: number;
  transport?: string;
  resources: {
    checked_at?: string;
    items: Array<{ name: string; available: boolean; status_code?: number; latency_ms: number }>;
  };
  history: {
    period_hours: number; samples: number; availability_percent?: number; monitoring_gaps: number;
    service_interruptions: number; inactive_connection_periods: number; external_loss_percent?: number;
    latency_avg_ms?: number; latency_max_ms?: number; jitter_avg_ms?: number;
    interface_errors: number; interface_dropped: number; uplink_errors: number; uplink_dropped: number; conntrack_peak_percent?: number;
    received_bytes: number; transmitted_bytes: number;
    average_rx_bps: number; average_tx_bps: number; peak_rx_bps: number; peak_tx_bps: number;
    events: Array<{ at?: string; type: "monitor_gap" | "service_down" | "peers_offline"; seconds?: number }>;
  };
  diagnostics: {
    checked_at?: string; status: "healthy" | "warning" | "critical" | "pending"; score?: number;
    live?: { loss_percent?: number; latency_ms?: number; jitter_ms?: number; dns_ms?: number; https_connect_ms?: number; https_total_ms?: number };
    network?: { uplink?: string; gateway?: string; uplink_mtu?: number; tunnel_mtu?: number; conntrack_count?: number; conntrack_max?: number; conntrack_percent?: number };
    checks: Array<{ id: string; name: string; state?: "passed" | "failed" | "unknown"; ok: boolean; value: string }>;
    findings: Array<{ severity: "warning" | "critical"; code: string; title: string; detail: string; action: string }>;
  };
  profile?: {
    kind: "encrypted-tunnel" | "proxy"; summary: string; accounts?: number; diagnostic_ready?: boolean;
    listener?: { unit: string; port: number; transport: string; listening: boolean };
    facts: Array<{ label: string; value: string }>;
  };
  connection_test?: {
    checked_at?: string | null; state: "confirmed" | "failed" | "unverified";
    method: "observed-client-traffic" | "local-protocol-roundtrip";
    title: string; detail: string; latency_ms?: number | null;
    bytes_received: number; bytes_sent: number; scope: string;
    identity?: "registered-client" | "managed-diagnostic";
  };
  regional_reachability?: {
    checked_at?: string | null; state: "confirmed" | "failed" | "unverified";
    region: "RU"; title: string; detail: string;
    method: "external-regional-probe";
    latency_ms?: number | null; bytes_received?: number; bytes_sent?: number;
  };
};

const labels: Record<Tab, string> = {
  overview: "Обзор", security: "Безопасность", application: "Приложение", services: "Службы", wg: "WireGuard", awg: "AmneziaWG", hysteria2: "Hysteria2", tuic: "TUIC v5", xray: "Xray", clients: "Подключения",
};
const navigationLabels: Record<Tab, string> = {
  overview: "OVERVIEW", security: "SECURITY", application: "APPLICATION", services: "SERVICES",
  wg: "WIREGUARD", awg: "AMNEZIAWG", hysteria2: "HYSTERIA2", tuic: "TUIC V5", xray: "XRAY", clients: "CONNECTIONS",
};
const protocolIds: Protocol[] = ["wg", "awg", "hysteria2", "tuic", "xray"];
const lightModuleIds: Protocol[] = ["awg", "hysteria2", "tuic", "xray"];
const isProtocolTab = (value: Tab): value is Protocol => protocolIds.includes(value as Protocol);
const actionLabels: Record<string, string> = {
  install: "Установка 312.net", start: "Запуск приложения", stop: "Остановка приложения",
  restart: "Перезапуск приложения", update: "Обновление приложения", "test-update": "Переход на тестовую версию", "test-rollback": "Возврат к рабочей версии", "network-check": "Проверка сети и туннелей", identity: "Обновление данных сервера",
  "integrity-check": "Проверка целостности",
  secure: "Настройка защиты", "system-update": "Обновление системных пакетов", "kernel-update": "Обновление ядра", "vpn-firewall": "Восстановление VPN firewall", optimize: "Оптимизация ресурсов",
  "service-mode": "Переключение сервисного режима",
  reboot: "Перезагрузка сервера", poweroff: "Выключение сервера",
  "protocol-install": "Установка протокола", "protocol-remove": "Удаление протокола", "protocol-update": "Обновление протокола",
};

const bytes = (value = 0) => {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
};
const uptime = (seconds = 0) => `${Math.floor(seconds / 86400)}д ${Math.floor((seconds % 86400) / 3600)}ч`;
const componentUptime = (seconds = 0) => seconds < 60
  ? `${seconds}с`
  : seconds < 3600
    ? `${Math.floor(seconds / 60)}м`
    : seconds < 86400
      ? `${Math.floor(seconds / 3600)}ч ${Math.floor((seconds % 3600) / 60)}м`
      : `${Math.floor(seconds / 86400)}д ${Math.floor((seconds % 86400) / 3600)}ч`;
const appendSample = (values: Array<number | null>, value: number) => [...values, Math.max(0, value)].slice(-48);
const securityStateMeta: Record<SecurityState, { label: string; symbol: string }> = {
  inactive: { label: "Выключено", symbol: "—" },
  active: { label: "Активно", symbol: "✓" },
  warning: { label: "Требует внимания", symbol: "!" },
  critical: { label: "Критично", symbol: "×" },
};
const connectionInterruptingActions = new Set<ApplicationAction>(["restart", "update", "test-update", "test-rollback", "system-update", "reboot"]);
const expectedDowntimeStorageKey = "312-expected-downtime-until";
const strongestSecurityState = (states: SecurityState[]): SecurityState => {
  if (states.includes("critical")) return "critical";
  if (states.includes("warning")) return "warning";
  if (states.includes("active")) return "active";
  return "inactive";
};
export default function Home() {
  const notifications = useNotifications();
  const [tab, setTab] = useState<Tab>("overview");
  const [token, setToken] = useState("");
  const [loginUser, setLoginUser] = useState("admin");
  const [loginPassword, setLoginPassword] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [clients, setClients] = useState<Client[]>([]);
  const [security, setSecurity] = useState<Record<string, unknown> | null>(null);
  const [securityLoading, setSecurityLoading] = useState(false);
  const [securityLogSource, setSecurityLogSource] = useState<"ssh" | "firewall" | "system">("ssh");
  const [securityLogs, setSecurityLogs] = useState<string[]>([]);
  const [securityLogsUpdatedAt, setSecurityLogsUpdatedAt] = useState<Date | null>(null);
  const [securityNewLogCount, setSecurityNewLogCount] = useState(0);
  const [application, setApplication] = useState<ApplicationStatus | null>(null);
  const [services, setServices] = useState<ServicesStatus | null>(null);
  const [automationDraft, setAutomationDraft] = useState<ServicesStatus["automation"] | null>(null);
  const [loggingDraft, setLoggingDraft] = useState<LoggingSettings | null>(null);
  const [notice, setNotice] = useState("");
  const [applicationLogs, setApplicationLogs] = useState<string[]>([]);
  const [securityLogsOpen, setSecurityLogsOpen] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [networkRate, setNetworkRate] = useState({ rx: 0, tx: 0 });
  const [resourceHistory, setResourceHistory] = useState<ResourceHistory>({ load: [], memory: [], disk: [], rx: [], tx: [] });
  const [metricsPeriod, setMetricsPeriod] = useState<MetricsPeriod>("live");
  const [metricsHistory, setMetricsHistory] = useState<MetricsHistory | null>(null);
  const [metricsHistoryLoading, setMetricsHistoryLoading] = useState(false);
  const [metricsHistoryError, setMetricsHistoryError] = useState("");
  const [protocolImages, setProtocolImages] = useState<ProtocolImage[]>([]);
  const [protocolStatuses, setProtocolStatuses] = useState<Partial<Record<Protocol, ProtocolStatus>>>({});
  const [protocolRates, setProtocolRates] = useState<Partial<Record<Protocol, { rx: number; tx: number }>>>({});
  const [installingProtocol, setInstallingProtocol] = useState("");
  const [checkingProtocolVersion, setCheckingProtocolVersion] = useState("");
  const [checkingDiagnostics, setCheckingDiagnostics] = useState<Protocol | null>(null);
  const [checkingConnection, setCheckingConnection] = useState<Protocol | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [passwordDialog, setPasswordDialog] = useState(false);
  const [currentAdminPassword, setCurrentAdminPassword] = useState("");
  const [newAdminPassword, setNewAdminPassword] = useState("");
  const [confirmAdminPassword, setConfirmAdminPassword] = useState("");
  const [confirmation, setConfirmation] = useState<ConfirmationRequest | null>(null);
  const [confirmationInput, setConfirmationInput] = useState("");
  const [connectionDialog, setConnectionDialog] = useState(false);
  const networkSample = useRef<{ rx: number; tx: number; at: number } | null>(null);
  const protocolSamples = useRef<Partial<Record<Protocol, { rx: number; tx: number; at: number }>>>({});
  const securityLogHeads = useRef<Partial<Record<"ssh" | "firewall" | "system", string>>>({});
  const automationDirty = useRef(false);
  const loggingDirty = useRef(false);
  const trackedActionUnit = useRef("");
  const notifiedActionUnits = useRef(new Set<string>());
  const liveRequestInFlight = useRef(false);
  const expectedDowntimeUntil = useRef(0);

  const beginExpectedDowntime = useCallback((action: string) => {
    if (!connectionInterruptingActions.has(action as ApplicationAction)) return;
    const until = Date.now() + 120_000;
    expectedDowntimeUntil.current = until;
    sessionStorage.setItem(expectedDowntimeStorageKey, String(until));
  }, []);

  const clearExpectedDowntime = useCallback(() => {
    expectedDowntimeUntil.current = 0;
    sessionStorage.removeItem(expectedDowntimeStorageKey);
  }, []);

  const reportBackgroundError = useCallback((cause: unknown, fallback: string) => {
    if (Date.now() < expectedDowntimeUntil.current) {
      setError("");
      return;
    }
    setError(cause instanceof Error ? cause.message : fallback);
  }, []);

  useEffect(() => {
    // Restore browser-only credentials after hydration.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setToken(sessionStorage.getItem("312-token") || "");
    const savedDowntime = Number(sessionStorage.getItem(expectedDowntimeStorageKey) || 0);
    if (savedDowntime > Date.now()) expectedDowntimeUntil.current = savedDowntime;
    else sessionStorage.removeItem(expectedDowntimeStorageKey);
    const savedNotice = sessionStorage.getItem("312-notice");
    if (savedNotice) {
      setNotice(savedNotice);
      sessionStorage.removeItem("312-notice");
    }
  }, []);

  useEffect(() => {
    if (!token) {
      notifiedActionUnits.current.clear();
      notifications.reset();
      return;
    }
    if (error) notifications.upsert({ id: "light:error", source: "light", title: "Ошибка", message: error, state: "error" });
    else notifications.resolve("light:error");
  }, [error, notifications, token]);

  useEffect(() => {
    if (!token) return;
    if (notice) notifications.upsert({ id: "light:notice", source: "light", title: "Готово", message: notice, state: "success" });
    else notifications.resolve("light:notice");
  }, [notice, notifications, token]);

  const request = useCallback(async (path: string, init?: RequestInit) => {
    const response = await fetch(`/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", Authorization: `Basic ${token}`, ...(init?.headers || {}) },
    });
    if (!response.ok) {
      const raw = await response.text();
      let detail = raw;
      try { detail = (JSON.parse(raw) as { detail?: string }).detail || raw; } catch { /* Plain-text API error. */ }
      throw new Error(response.status === 401 ? "Неверный токен администратора" : detail || `Ошибка ${response.status}`);
    }
    return response.json();
  }, [token]);


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

  const loadOverview = useCallback(async () => {
    if (!token) return;
    try {
      const [next, imageData] = await Promise.all([
        request("/overview") as Promise<Overview>,
        request("/protocol-images") as Promise<{ items: ProtocolImage[] }>,
      ]);
      const now = Date.now();
      const previous = networkSample.current;
      let nextRxRate = 0;
      let nextTxRate = 0;
      const countersChanged = !previous || next.resources.network_rx !== previous.rx || next.resources.network_tx !== previous.tx;
      if (previous && countersChanged) {
        const seconds = Math.max((now - previous.at) / 1000, 0.1);
        nextRxRate = Math.max(0, (next.resources.network_rx - previous.rx) / seconds);
        nextTxRate = Math.max(0, (next.resources.network_tx - previous.tx) / seconds);
        setNetworkRate({ rx: nextRxRate, tx: nextTxRate });
      }
      const memoryUsed = next.resources.memory_total ? 100 - next.resources.memory_available / next.resources.memory_total * 100 : 0;
      const diskUsed = next.resources.disk_total ? 100 - next.resources.disk_available / next.resources.disk_total * 100 : 0;
      setResourceHistory((history) => ({
        load: appendSample(history.load, next.resources.cpu_percent || 0),
        memory: appendSample(history.memory, memoryUsed),
        disk: appendSample(history.disk, diskUsed),
        rx: previous && countersChanged ? appendSample(history.rx, nextRxRate) : history.rx,
        tx: previous && countersChanged ? appendSample(history.tx, nextTxRate) : history.tx,
      }));
      if (countersChanged) networkSample.current = { rx: next.resources.network_rx, tx: next.resources.network_tx, at: now };
      setOverview(next);
      setProtocolImages(imageData.items || []);
      if (installingProtocol && imageData.items.some((image) => image.id === installingProtocol && image.installed)) {
        setInstallingProtocol("");
      }
      setLastUpdated(new Date());
    } catch (cause) { reportBackgroundError(cause, "Ошибка соединения"); }
  }, [installingProtocol, reportBackgroundError, request, token]);

  const loadMetricsHistory = useCallback(async () => {
    if (!token) return;
    setMetricsHistoryLoading(true);
    try {
      const data = await request(`/metrics/history?period=${metricsPeriod}`) as MetricsHistory;
      setMetricsHistory(data);
      setMetricsHistoryError(data.error || "");
    } catch (cause) {
      setMetricsHistoryError(cause instanceof Error ? cause.message : "История метрик временно недоступна");
    } finally {
      setMetricsHistoryLoading(false);
    }
  }, [metricsPeriod, request, token]);

  const loadClients = useCallback(async () => {
    if (!token) return;
    try {
      const data = await request("/clients");
      setClients(data.items); setLastUpdated(new Date());
    } catch (cause) { reportBackgroundError(cause, "Не удалось обновить клиентов"); }
  }, [reportBackgroundError, request, token]);

  const loadSecurity = useCallback(async () => {
    if (!token) return;
    setSecurityLoading(true);
    try {
      setSecurity(await request("/security")); setLastUpdated(new Date());
    } catch (cause) { reportBackgroundError(cause, "Не удалось обновить состояние безопасности"); }
    finally { setSecurityLoading(false); }
  }, [reportBackgroundError, request, token]);

  const loadApplication = useCallback(async () => {
    if (!token) return;
    try {
      const next = await request("/application/status") as ApplicationStatus;
      const action = next.action?.action?.split(":")[0] || "";
      if (["active", "activating", "running"].includes(next.action?.state || "")) beginExpectedDowntime(action);
      else if (["succeeded", "finished", "failed"].includes(next.action?.state || "")) clearExpectedDowntime();
      setApplication(next); setLastUpdated(new Date());
    } catch (cause) { reportBackgroundError(cause, "Не удалось обновить приложение"); }
  }, [beginExpectedDowntime, clearExpectedDowntime, reportBackgroundError, request, token]);

  const loadServices = useCallback(async () => {
    if (!token) return;
    try {
      const next = await request("/services") as ServicesStatus;
      setServices(next);
      if (!automationDirty.current) setAutomationDraft(next.automation);
      if (!loggingDirty.current) setLoggingDraft({
        persistent: next.logging?.persistent ?? true,
        retention_days: next.logging?.retention_days ?? 30,
      });
      setLastUpdated(new Date());
    } catch (cause) { reportBackgroundError(cause, "Не удалось обновить состояние служб"); }
  }, [reportBackgroundError, request, token]);

  const loadProtocolStatus = useCallback(async (protocol: Protocol) => {
    if (!token) return;
    try {
      const next = await request(`/protocols/${protocol}/status`) as ProtocolStatus;
      const now = Date.now();
      const previous = protocolSamples.current[protocol];
      if (previous) {
        const seconds = Math.max((now - previous.at) / 1000, 0.1);
        setProtocolRates((rates) => ({ ...rates, [protocol]: {
          rx: Math.max(0, (next.interface_rx_bytes - previous.rx) / seconds),
          tx: Math.max(0, (next.interface_tx_bytes - previous.tx) / seconds),
        } }));
      }
      protocolSamples.current[protocol] = { rx: next.interface_rx_bytes, tx: next.interface_tx_bytes, at: now };
      setProtocolStatuses((statuses) => ({ ...statuses, [protocol]: next }));
      setLastUpdated(new Date());
    } catch (cause) { reportBackgroundError(cause, "Не удалось обновить состояние протокола"); }
  }, [reportBackgroundError, request, token]);

  const refreshCurrent = useCallback(async (showBusy = false) => {
    if (!token) return;
    if (showBusy) setBusy(true);
    setError("");
    try {
      if (tab === "overview") await Promise.all([loadOverview(), loadMetricsHistory(), loadClients(), loadApplication(), loadServices()]);
      else if (tab === "security") await Promise.all([loadSecurity(), loadServices()]);
      else if (tab === "application") await loadApplication();
      else if (tab === "services") await loadServices();
      else if (isProtocolTab(tab)) await Promise.all([loadClients(), loadProtocolStatus(tab)]);
      else await loadClients();
    } finally {
      if (showBusy) setBusy(false);
    }
  }, [loadApplication, loadClients, loadMetricsHistory, loadOverview, loadProtocolStatus, loadSecurity, loadServices, tab, token]);

  useEffect(() => {
    if (!token) return;
    sessionStorage.setItem("312-token", token);
    // Load the minimum shared data required to construct the first screen and navigation.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void Promise.all([loadOverview(), loadClients(), loadApplication(), loadServices()]);
  }, [loadApplication, loadClients, loadOverview, loadServices, token]);

  useEffect(() => {
    if (!token || !autoRefresh || ["overview", "security", "application"].includes(tab)) return;
    // Live telemetry owns the fast path. Full module snapshots are intentionally
    // slower because services and protocol checks spawn multiple system commands.
    const timer = window.setInterval(() => void refreshCurrent(false), 15000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, refreshCurrent, tab, token]);

  useEffect(() => {
    const actionRunning = ["active", "activating", "running"].includes(application?.action?.state || "");
    if (!token || (!autoRefresh && !actionRunning)) return;
    const timer = window.setInterval(() => void loadApplication(), actionRunning ? 3000 : 15000);
    return () => window.clearInterval(timer);
  }, [application?.action?.state, autoRefresh, loadApplication, token]);

  useEffect(() => {
    const action = application?.action;
    if (!action?.unit) return;
    const id = `operation:system:${action.unit}`;
    const active = ["queued", "running", "active", "activating", "rebooting", "powering-off"].includes(action.state || "");
    if (active) notifiedActionUnits.current.add(id);
    if (!notifiedActionUnits.current.has(id)) return;
    const failed = action.state === "failed" || Boolean(action.result && !["success", "unknown"].includes(action.result));
    const succeeded = ["succeeded", "finished"].includes(action.state || "") && (!action.result || action.result === "success");
    const input = {
      id,
      source: "system",
      title: actionLabels[(action.action || "").split(":")[0]] || action.action || "Системная операция",
      message: failed ? action.message || "Команда завершилась с ошибкой." : action.message || "",
      state: failed ? "error" as const : succeeded ? "success" as const : active ? "running" as const : "unknown" as const,
      kind: "operation" as const,
      progress: active ? action.progress : undefined,
    };
    if (failed || succeeded) notifications.finishOperation(input);
    else notifications.upsert(input);
  }, [application?.action, notifications]);

  useEffect(() => {
    const action = application?.action;
    if (!action?.unit) return;
    if (["active", "activating", "running"].includes(action.state || "")) {
      trackedActionUnit.current = action.unit;
      return;
    }
    if (trackedActionUnit.current !== action.unit || !["succeeded", "finished", "failed"].includes(action.state || "")) return;
    trackedActionUnit.current = "";
    const label = actionLabels[(action.action || "").split(":")[0]] || "Операция";
    const timer = window.setTimeout(() => {
      if (action.state === "failed" || action.result === "failed") {
        return;
      }
      const message = `${label}: успешно завершено`;
      if (action.action === "update" || action.action === "test-update" || action.action === "test-rollback") {
        sessionStorage.setItem("312-notice", `${message}. Интерфейс обновлён до установленной версии`);
        window.setTimeout(() => window.location.reload(), 600);
        return;
      }
      void refreshCurrent(false);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [application?.action, refreshCurrent]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 8000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!token || tab === "overview") return;
    // Synchronize only the newly opened module.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refreshCurrent(false);
  }, [refreshCurrent, tab, token]);

  useEffect(() => {
    if (!token || tab !== "overview") return;
    const initial = window.setTimeout(() => void loadMetricsHistory(), 0);
    const timer = window.setInterval(() => void loadMetricsHistory(), metricsPeriod === "live" ? 15000 : 60000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [loadMetricsHistory, metricsPeriod, tab, token]);

  const loadSecurityLogs = useCallback(async () => {
    if (!token) return;
    try {
      const data = await request(`/security/logs?source=${securityLogSource}&lines=160`);
      const nextLines = (data.lines || []) as string[];
      const previousHead = securityLogHeads.current[securityLogSource];
      const previousPosition = previousHead ? nextLines.indexOf(previousHead) : 0;
      setSecurityNewLogCount(previousHead && previousPosition !== 0 ? (previousPosition > 0 ? previousPosition : nextLines.length) : 0);
      securityLogHeads.current[securityLogSource] = nextLines[0] || "";
      setSecurityLogs(nextLines);
      setSecurityLogsUpdatedAt(new Date());
    } catch (cause) { reportBackgroundError(cause, "Не удалось загрузить журнал"); }
  }, [reportBackgroundError, request, securityLogSource, token]);

  const loadApplicationLogs = useCallback(async () => {
    if (!token) return;
    try {
      const data = await request("/application/logs?lines=180");
      setApplicationLogs(data.lines || []);
    } catch (cause) { reportBackgroundError(cause, "Не удалось загрузить журнал приложения"); }
  }, [reportBackgroundError, request, token]);

  const loadLiveStatus = useCallback(async () => {
    if (!token || liveRequestInFlight.current || document.visibilityState !== "visible") return;
    liveRequestInFlight.current = true;
    try {
      const next = await request("/live-status") as LiveStatus;
      setError("");
      const now = Date.now();
      const previous = networkSample.current;
      let nextRxRate = 0;
      let nextTxRate = 0;
      const countersChanged = !previous || next.resources.network_rx !== previous.rx || next.resources.network_tx !== previous.tx;
      if (previous && countersChanged) {
        const seconds = Math.max((now - previous.at) / 1000, 0.1);
        nextRxRate = Math.max(0, (next.resources.network_rx - previous.rx) / seconds);
        nextTxRate = Math.max(0, (next.resources.network_tx - previous.tx) / seconds);
        setNetworkRate({ rx: nextRxRate, tx: nextTxRate });
      }
      const memoryUsed = next.resources.memory_total ? 100 - next.resources.memory_available / next.resources.memory_total * 100 : 0;
      const diskUsed = next.resources.disk_total ? 100 - next.resources.disk_available / next.resources.disk_total * 100 : 0;
      setResourceHistory((history) => ({
        load: appendSample(history.load, next.resources.cpu_percent || 0),
        memory: appendSample(history.memory, memoryUsed),
        disk: appendSample(history.disk, diskUsed),
        rx: previous && countersChanged ? appendSample(history.rx, nextRxRate) : history.rx,
        tx: previous && countersChanged ? appendSample(history.tx, nextTxRate) : history.tx,
      }));
      if (countersChanged) networkSample.current = { rx: next.resources.network_rx, tx: next.resources.network_tx, at: now };
      setOverview((current) => current ? {
        ...current,
        server: { ...current.server, uptime_s: next.resources.uptime_s ?? current.server.uptime_s },
        resources: next.resources,
        protocols: Object.fromEntries(protocolIds.map((protocol) => [protocol, {
          ...current.protocols[protocol],
          active: next.protocols[protocol]?.active ?? current.protocols[protocol]?.active ?? false,
        }])) as Overview["protocols"],
      } : current);
      setClients((current) => next.clients.map((client) => ({
        ...current.find((existing) => existing.id === client.id && existing.protocol === client.protocol),
        ...client,
      })));
      setProtocolStatuses((current) => {
        const updated = { ...current };
        protocolIds.forEach((protocol) => {
          if (updated[protocol] && next.protocols[protocol]) updated[protocol] = { ...updated[protocol]!, ...next.protocols[protocol] };
        });
        return updated;
      });
      setSecurity((current) => current ? {
        ...current,
        firewall: { ...((current.firewall as Record<string, unknown> | undefined) || {}), active: next.security.firewall_active },
        fail2ban: { ...((current.fail2ban as Record<string, unknown> | undefined) || {}), active: next.security.fail2ban_active },
        ssh: { ...((current.ssh as Record<string, unknown> | undefined) || {}), active: next.security.ssh_listening },
      } : current);
      setLastUpdated(new Date());
    } catch {
      // Full module refresh reports persistent errors; live telemetry stays silent.
    } finally {
      liveRequestInFlight.current = false;
    }
  }, [request, token]);

  useEffect(() => {
    if (!token || !autoRefresh || !["overview", "clients", ...protocolIds, "security"].includes(tab)) return;
    const initial = window.setTimeout(() => void loadLiveStatus(), 0);
    const timer = window.setInterval(() => void loadLiveStatus(), 800);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [autoRefresh, loadLiveStatus, tab, token]);

  useEffect(() => {
    if (!token || tab !== "security" || !securityLogsOpen) return;
    const timer = window.setTimeout(() => void loadSecurityLogs(), 0);
    return () => window.clearTimeout(timer);
  }, [loadSecurityLogs, securityLogsOpen, tab, token]);

  useEffect(() => {
    if (!token || tab !== "application") return;
    const timer = window.setTimeout(() => void loadApplicationLogs(), 0);
    return () => window.clearTimeout(timer);
  }, [loadApplicationLogs, tab, token]);

  useEffect(() => {
    if (!autoRefresh || tab !== "security" || !securityLogsOpen) return;
    const timer = window.setInterval(() => void loadSecurityLogs(), 5000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, tab, securityLogsOpen, loadSecurityLogs]);

  useEffect(() => {
    if (!autoRefresh || tab !== "application") return;
    const timer = window.setInterval(() => void loadApplicationLogs(), 5000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, tab, loadApplicationLogs]);

  async function runApplicationAction(action: ApplicationAction, automatic = false) {
    if (action === "poweroff" && !await askConfirmation({
      title: "Выключить сервер?",
      message: "Сервер, VPN-каналы и панель станут недоступны до запуска через кабинет провайдера.",
      confirmLabel: "Выключить сервер", phrase: "ВЫКЛЮЧИТЬ", danger: true,
    })) return;
    const risky = action === "restart" || action === "update" || action === "test-update" || action === "test-rollback" || action === "identity" || action === "system-update" || action === "kernel-update" || action === "reboot";
    if (!automatic && risky && !await askConfirmation({
      title: actionLabels[action] || "Выполнить команду?",
      message: `Будет выполнена команда «vps-control ${action}». Во время операции возможен кратковременный перерыв в работе.`,
      confirmLabel: "Выполнить", danger: action === "reboot",
    })) return;
    setBusy(true); setError("");
    try {
      const started = await request("/application/action", { method: "POST", body: JSON.stringify({ action }) });
      beginExpectedDowntime(action);
      setApplication((current) => ({
        api: current?.api || { active: true, enabled: true },
        containers: current?.containers || [],
        action: started,
        service_mode: current?.service_mode,
        release: current?.release,
        runtime: current?.runtime,
      }));
      if (action === "reboot" || action === "poweroff") return;
      await loadApplication(); await loadApplicationLogs();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Команда не запущена"); }
    finally { setBusy(false); }
  }

  async function runServiceAction(serviceId: string, serviceName: string, action: "start" | "stop" | "restart") {
    if (action === "stop" && serviceId === "ssh") {
      if (!await askConfirmation({
        title: "Остановить SSH?",
        message: "Все SSH-соединения будут разорваны. Восстановление возможно через эту панель или после перезагрузки сервера.",
        confirmLabel: "Остановить SSH", phrase: "ОТКЛЮЧИТЬ SSH", danger: true,
      })) return;
    } else if (action === "stop") {
      if (!await askConfirmation({
        title: `Остановить «${serviceName}»?`,
        message: "Связанный функционал станет недоступен до повторного запуска службы.",
        confirmLabel: "Остановить", danger: true,
      })) return;
    } else if (action === "restart" && !await askConfirmation({
      title: `Перезапустить «${serviceName}»?`,
      message: "Во время перезапуска возможен кратковременный перерыв в работе.",
      confirmLabel: "Перезапустить",
    })) return;
    setBusy(true); setError("");
    try {
      await request(`/services/${serviceId}/action`, { method: "POST", body: JSON.stringify({ action }) });
      await Promise.all([loadServices(), loadSecurity()]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось выполнить действие со службой"); }
    finally { setBusy(false); }
  }

  async function fixSecurity(action: "secure" | "system-update" | "kernel-update" | "vpn-firewall") {
    await runApplicationAction(action);
    await loadSecurity();
  }

  function closePasswordDialog() {
    setPasswordDialog(false);
    setCurrentAdminPassword("");
    setNewAdminPassword("");
    setConfirmAdminPassword("");
  }

  async function changeAdminPassword(event: FormEvent) {
    event.preventDefault();
    if (!currentAdminPassword) { setError("Введите текущий пароль"); return; }
    if (newAdminPassword.length < 16 || newAdminPassword.length > 128) { setError("Новый пароль должен содержать от 16 до 128 символов"); return; }
    if (!/^[!-~]+$/.test(newAdminPassword)) { setError("Используйте печатные латинские символы без пробелов"); return; }
    const passwordCategories = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((pattern) => pattern.test(newAdminPassword)).length;
    if (passwordCategories < 3) { setError("Добавьте минимум три группы: строчные, заглавные, цифры и спецсимволы"); return; }
    if (newAdminPassword === currentAdminPassword) { setError("Новый пароль должен отличаться от текущего"); return; }
    if (newAdminPassword !== confirmAdminPassword) { setError("Новые пароли не совпадают"); return; }
    setBusy(true); setError("");
    try {
      await request("/security/admin-password", { method: "PUT", body: JSON.stringify({ current_password: currentAdminPassword, new_password: newAdminPassword, confirm_password: confirmAdminPassword }) });
      sessionStorage.removeItem("312-token");
      closePasswordDialog(); setToken(""); setLoginPassword("");
      setNotice("Пароль изменён. Войдите заново с новым паролем.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось изменить пароль"); }
    finally { setBusy(false); }
  }

  function updateAutomation(kind: AutomationKind, patch: Partial<AutomationSchedule>) {
    automationDirty.current = true;
    setAutomationDraft((current) => current ? {
      ...current, [kind]: { ...current[kind], ...patch },
    } : current);
  }

  async function saveAutomation() {
    if (!automationDraft) return;
    setBusy(true); setError("");
    try {
      await request("/services/automation", { method: "PUT", body: JSON.stringify(automationDraft) });
      automationDirty.current = false;
      await Promise.all([loadServices(), loadApplication()]);
      setNotice("Расписание обслуживания сохранено и применено");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось сохранить расписания"); }
    finally { setBusy(false); }
  }

  async function changePanelAccess(mode: "external" | "vpn") {
    const message = mode === "vpn"
      ? "Закрыть публичный доступ? Панель останется доступна только через защищённый туннель по локальным адресам. Текущее подключение может завершиться."
      : "Открыть публичный доступ к панели из интернета?";
    if (!await askConfirmation({
      title: mode === "vpn" ? "Ограничить доступ к панели?" : "Открыть публичный доступ?",
      message, confirmLabel: mode === "vpn" ? "Оставить защищённый доступ" : "Открыть доступ",
      danger: mode === "external",
    })) return;
    setBusy(true); setError("");
    try {
      await request("/services/panel-access", { method: "PUT", body: JSON.stringify({ mode }) });
      setServices((current) => current ? {
        ...current, panel_access: { vpn_urls: current.panel_access?.vpn_urls || [], mode, public: mode === "external" },
      } : current);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось изменить доступ к панели"); }
    finally { setBusy(false); }
  }

  async function changeServiceMode(active: boolean) {
    if (!active && testReleaseActive) {
      setError("Сначала вернитесь на light: отключить сервисный режим во время работы test-light нельзя.");
      return;
    }
    if (!await askConfirmation({
      title: active ? "Включить сервисный режим?" : "Завершить сервисный режим?",
      message: active
        ? "Панель станет публичной, SSH будет запущен, а выполнение всех сценариев планового обслуживания будет заблокировано. Расписания останутся доступны для настройки, метрики продолжат собираться. После включения станет доступен переход на test-light."
        : "Публичный доступ к панели будет закрыт, SSH вернётся к штатному режиму, а выполнение плановых сценариев снова будет разрешено.",
      confirmLabel: active ? "Включить режим" : "Завершить обслуживание",
      danger: active,
    })) return;
    setBusy(true); setError("");
    try {
      await request("/services/service-mode", { method: "PUT", body: JSON.stringify({ active }) });
      let confirmed = false;
      for (let attempt = 0; attempt < 36; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 5000));
        try {
          const [nextServices, nextApplication] = await Promise.all([
            request("/services"), request("/application/status"),
          ]);
          setServices(nextServices);
          setApplication(nextApplication);
          const modeActionFinished = nextApplication?.action?.action === "service-mode"
            && ["succeeded", "finished"].includes(nextApplication?.action?.state || "");
          if (Boolean(nextServices?.service_mode?.active) === active && modeActionFinished) {
            confirmed = true;
            break;
          }
        } catch {
          // API and gateway may briefly restart while the selected branch is deployed.
        }
      }
      if (!confirmed) throw new Error("Сервер не подтвердил завершение переключения режима");
      await loadSecurity();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось изменить сервисный режим");
    } finally { setBusy(false); }
  }

  async function waitForProtocolState(image: ProtocolImage, installed: boolean, expectedUnit = "") {
    const maxAttempts = installed ? 264 : 120;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 5000));
      try {
        const [imageData, status] = await Promise.all([
          request("/protocol-images") as Promise<{ items: ProtocolImage[] }>,
          request("/application/status") as Promise<ApplicationStatus>,
        ]);
        setProtocolImages(imageData.items || []);
        setApplication(status);
        const current = imageData.items?.find((item) => item.id === image.id);
        const actionState = status.action?.state || "";
        const actionMatches = !expectedUnit || status.action?.unit === expectedUnit;
        if (actionMatches && (actionState === "failed" || status.action?.result === "failed" || status.action?.result === "unknown")) {
          const detail = status.action?.message || "системная задача не вернула результат";
          throw new Error(`Операция с ${image.name} завершилась с ошибкой: ${detail}`);
        }
        const actionActive = actionMatches && ["active", "activating", "running"].includes(actionState);
        if (Boolean(current?.installed) === installed && !actionActive) return;
      } catch (cause) {
        if (cause instanceof Error && cause.message.includes("завершилось с ошибкой")) throw cause;
        // API may restart briefly after installing or removing a module.
      }
    }
    throw new Error(`Сервер не подтвердил ${installed ? "установку" : "удаление"} ${image.name} за ${installed ? "22" : "10"} минут`);
  }

  async function installProtocol(image: ProtocolImage) {
    if (!await askConfirmation({
      title: `Установить ${image.name}?`,
      message: `На сервер будет установлен модуль ${image.name} ${image.version}.`,
      confirmLabel: "Установить",
    })) return;
    setBusy(true); setError(""); setInstallingProtocol(image.id);
    try {
      const started = await request(`/protocol-images/${image.id}/install`, { method: "POST" }) as ApplicationStatus["action"];
      setApplication((current) => ({
        api: current?.api || { active: true, enabled: true },
        containers: current?.containers || [],
        action: started,
      }));
      await waitForProtocolState(image, true, started.unit || "");
      await Promise.all([loadOverview(), loadClients(), loadProtocolStatus(image.id as Protocol)]);
      setNotice(`${image.name} установлен и готов к работе`);
    } catch (cause) {
      setInstallingProtocol("");
      setError(cause instanceof Error ? cause.message : "Не удалось запустить установку протокола");
    } finally { setInstallingProtocol(""); setBusy(false); }
  }

  async function checkProtocolVersion(image: ProtocolImage) {
    setCheckingProtocolVersion(image.id); setError("");
    try {
      const result = await request(`/protocol-images/${image.id}/version/check`, { method: "POST" }) as { item: ProtocolImage };
      setProtocolImages((current) => current.map((entry) => entry.id === result.item.id ? result.item : entry));
      if (result.item.version_error) setError(`Не удалось проверить версию ${image.name}; повторите проверку позже`);
      else setNotice(result.item.update_available ? `Для ${image.name} доступна версия ${result.item.available_version}` : `${image.name}: установлена актуальная версия`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Не удалось проверить версию ${image.name}`);
    } finally { setCheckingProtocolVersion(""); }
  }

  async function waitForProtocolUpdate(image: ProtocolImage) {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 5000));
      try {
        const status = await request("/application/status") as ApplicationStatus;
        setApplication(status);
        const state = status.action?.state || "";
        if (state === "failed" || status.action?.result === "failed") throw new Error(`Обновление ${image.name} завершилось с ошибкой совместимости`);
        if (state === "succeeded" || status.action?.result === "success") return;
      } catch (cause) {
        if (cause instanceof Error && cause.message.includes("ошибкой совместимости")) throw cause;
      }
    }
    throw new Error(`Сервер не подтвердил обновление ${image.name} за 10 минут`);
  }

  async function updateProtocol(image: ProtocolImage) {
    if (!await askConfirmation({
      title: `Обновить ${image.name}?`,
      message: image.update_breaking
        ? `Версия ${image.available_version} меняет основную версию (сейчас ${image.installed_version || "не определена"}). Подключения будут приостановлены; после обновления может потребоваться новый профиль.`
        : image.version_channel === "package"
          ? `Доступна новая пакетная сборка ${image.available_version}. Версия протокола сейчас ${image.installed_version || "не определена"}; подключения будут приостановлены только на время проверки и перезапуска.`
        : `Будет установлена версия ${image.available_version}. На время проверки и перезапуска активные подключения этого протокола будут кратковременно приостановлены.`,
      confirmLabel: "Обновить протокол",
    })) return;
    setBusy(true); setError(""); setInstallingProtocol(`update-${image.id}`);
    try {
      const started = await request(`/protocol-images/${image.id}/update`, { method: "POST" });
      setApplication((current) => ({ api: current?.api || { active: true, enabled: true }, containers: current?.containers || [], action: started }));
      await waitForProtocolUpdate(image);
      await Promise.all([loadOverview(), loadClients(), loadProtocolStatus(image.id as Protocol)]);
      setNotice(`${image.name} обновлён. Проверьте предупреждения у подключений.`);
    } catch (cause) {
      await loadClients();
      setError(cause instanceof Error ? cause.message : "Не удалось обновить протокол");
    } finally { setInstallingProtocol(""); setBusy(false); }
  }

  async function restartProtocol(protocol: Protocol) {
    if (!await askConfirmation({
      title: `Перезапустить ${labels[protocol]}?`,
      message: "Активные VPN-соединения кратковременно прервутся.",
      confirmLabel: "Перезапустить",
    })) return;
    setBusy(true); setError("");
    try {
      await request(`/protocols/${protocol}/restart`, { method: "POST" });
      await Promise.all([loadProtocolStatus(protocol), loadOverview()]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось перезапустить протокол"); }
    finally { setBusy(false); }
  }

  function updateLoggingDraft(patch: Partial<LoggingSettings>) {
    loggingDirty.current = true;
    setLoggingDraft((current) => ({ ...(current || { persistent: true, retention_days: 30 }), ...patch }));
  }

  async function saveLoggingSettings() {
    if (!loggingDraft) return;
    if (!loggingDraft.persistent && !await askConfirmation({
      title: "Отключить постоянную запись логов?",
      message: "Новые системные журналы будут храниться только в оперативной памяти и исчезнут после перезагрузки. Диагностика прошлых событий станет ограниченной.",
      confirmLabel: "Отключить запись", danger: true,
    })) return;
    setBusy(true); setError("");
    try {
      await request("/services/logging", {
        method: "PUT",
        body: JSON.stringify(loggingDraft),
      });
      loggingDirty.current = false;
      await loadServices();
      setNotice("Настройки записи и хранения журналов сохранены");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось сохранить настройки журналов"); }
    finally { setBusy(false); }
  }

  async function clearManagedLogs() {
    if (!await askConfirmation({
      title: "Очистить все управляемые журналы?",
      message: "Будут удалены системные журналы, логи контейнеров и история мониторинга WG/AWG. Действие нельзя отменить.",
      confirmLabel: "Очистить журналы", phrase: "ОЧИСТИТЬ ЛОГИ", danger: true,
    })) return;
    setBusy(true); setError("");
    try {
      await request("/services/logging/clear", { method: "POST" });
      setSecurityLogs([]); setApplicationLogs([]);
      await loadServices();
      setNotice("Управляемые журналы очищены");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось очистить журналы"); }
    finally { setBusy(false); }
  }

  async function checkNetworkDiagnostics(protocol: Protocol) {
    setCheckingDiagnostics(protocol); setError("");
    try {
      const diagnostics = await request(`/protocols/${protocol}/diagnostics/check`, { method: "POST" });
      setProtocolStatuses((statuses) => {
        const current = statuses[protocol];
        return current ? { ...statuses, [protocol]: { ...current, diagnostics } } : statuses;
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось выполнить полную диагностику сети");
    } finally {
      setCheckingDiagnostics(null);
    }
  }

  async function checkProtocolConnection(protocol: Protocol) {
    setCheckingConnection(protocol); setError("");
    try {
      const connection_test = await request(`/protocols/${protocol}/connection/check`, { method: "POST" });
      setProtocolStatuses((statuses) => {
        const current = statuses[protocol];
        return current ? { ...statuses, [protocol]: { ...current, connection_test } } : statuses;
      });
      await loadProtocolStatus(protocol);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось проверить передачу данных через протокол");
    } finally {
      setCheckingConnection(null);
    }
  }

  async function removeProtocol(image: ProtocolImage) {
    if (!await askConfirmation({
      title: `Удалить ${image.name}?`,
      message: "Будут удалены модуль, его конфигурация и все подключения. Образ останется доступен для повторной установки.",
      confirmLabel: "Удалить модуль", phrase: "УДАЛИТЬ", danger: true,
    })) return;
    setBusy(true); setError(""); setInstallingProtocol(`remove-${image.id}`);
    try {
      const started = await request(`/protocol-images/${image.id}`, { method: "DELETE" }) as ApplicationStatus["action"];
      setApplication((current) => ({
        api: current?.api || { active: true, enabled: true },
        containers: current?.containers || [],
        action: started,
      }));
      setTab("overview");
      await waitForProtocolState(image, false, started.unit || "");
      await Promise.all([loadOverview(), loadClients(), loadServices()]);
      setNotice(`${image.name} удалён`);
    } catch (cause) {
      setInstallingProtocol("");
      setError(cause instanceof Error ? cause.message : "Не удалось запустить удаление протокола");
    } finally { setInstallingProtocol(""); setBusy(false); }
  }

  const installedProtocols = useMemo(
    () => protocolImages.filter((image) => image.installed && lightModuleIds.includes(image.id as Protocol)).map((image) => image.id as Protocol),
    [protocolImages],
  );
  async function createClient(payload: { name: string; protocol: Protocol; settings: ConnectionSettings }) {
    setError("");
    if (!installedProtocols.includes(payload.protocol)) throw new Error("Сначала установите выбранный протокол");
    const result = await request("/clients", { method: "POST", body: JSON.stringify(payload) }) as { profile: ConnectionProfile };
    return result.profile;
  }

  function downloadConfig(filename: string, config: string, mimeType = "text/plain;charset=utf-8") {
    const blob = new Blob([config], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = filename; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function downloadLogs(filename: string, lines: string[]) {
    downloadConfig(filename, `${lines.join("\n")}\n`);
  }

  async function removeClient(id: string) {
    if (!await askConfirmation({
      title: "Отозвать доступ клиента?",
      message: "Клиент будет удалён, а его VPN-доступ прекратится немедленно.",
      confirmLabel: "Отозвать доступ", danger: true,
    })) return;
    setBusy(true);
    try { await request(`/clients/${id}`, { method: "DELETE" }); await loadClients(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось удалить клиента"); }
    finally { setBusy(false); }
  }

  async function login(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    const candidateToken = btoa(`${loginUser}:${loginPassword}`);
    try {
      const response = await fetch("/api/overview", {
        headers: { Authorization: `Basic ${candidateToken}` },
      });
      if (!response.ok) {
        throw new Error(response.status === 401
          ? "Неверный логин или пароль"
          : `Не удалось проверить учётные данные (ошибка ${response.status})`);
      }
      setToken(candidateToken);
      setLoginPassword("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось войти в панель");
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return <main className="loginPage">
      <div className="loginGlow" />
      <form className="loginCard" onSubmit={login}>
        <Logo />
        <div><p className="eyebrow">INFRASTRUCTURE CONTROL</p><h1>Вход в панель управления сервером.</h1></div>
        <p className="loginCopy">Введите учётные данные администратора для доступа к панели.</p>
        <label>Логин<input type="text" value={loginUser} onChange={(event) => setLoginUser(event.target.value)} autoFocus required /></label>
        <label>Пароль<input type="password" value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} required /></label>
        <button className="primaryButton" type="submit" disabled={busy}>{busy ? "Проверка…" : "Открыть панель"} <span>→</span></button>
        {error && <div className="errorBox">{error}</div>}
      </form>
      <VersionFooter />
    </main>;
  }

  const memUsed = overview ? 100 - overview.resources.memory_available / overview.resources.memory_total * 100 : 0;
  const diskUsed = overview ? 100 - overview.resources.disk_available / overview.resources.disk_total * 100 : 0;
  const memoryUsedBytes = overview ? Math.max(0, overview.resources.memory_total - overview.resources.memory_available) : 0;
  const diskUsedBytes = overview ? Math.max(0, overview.resources.disk_total - overview.resources.disk_available) : 0;
  const activeMetricsHistory = metricsHistory?.period === metricsPeriod ? metricsHistory : null;
  const chartHistory: ResourceHistory = activeMetricsHistory ? {
    load: activeMetricsHistory.points.map((point) => point.cpu_percent),
    memory: activeMetricsHistory.points.map((point) => point.memory_used_percent),
    disk: activeMetricsHistory.points.map((point) => point.disk_used_percent),
    rx: activeMetricsHistory.points.map((point) => point.rx_bps),
    tx: activeMetricsHistory.points.map((point) => point.tx_bps),
  } : resourceHistory;
  const metricsResolution = activeMetricsHistory?.resolution_s || 1;
  const metricsStatus = metricsHistoryError
    ? "История временно недоступна · показаны текущие данные"
    : metricsHistoryLoading && !activeMetricsHistory
      ? "Загрузка истории с VPS…"
      : activeMetricsHistory?.settings.enabled === false
        ? "Запись истории выключена"
        : "История хранится локально на VPS";
  const firewall = security?.firewall as {
    active?: boolean; rules?: string[]; forwarding_enabled?: boolean; stateful_return?: boolean;
    uplink_interface?: string; vpn_policy_healthy?: boolean;
    panel_access?: {
      mode?: "external" | "vpn"; port?: number; listening?: boolean; public_rule?: boolean;
      publicly_accessible?: boolean; vpn_only?: boolean; allowed_interfaces?: string[]; consistent?: boolean;
    };
    protocol_policies?: Record<string, { installed?: boolean; route_allowed?: boolean; nat_enabled?: boolean; healthy?: boolean }>;
  } | undefined;
  const ssh = security?.ssh as { active?: boolean; password_authentication?: string; permit_root_login?: string; publicly_allowed?: boolean; active_connections?: number; max_auth_tries?: string; x11_forwarding?: string; tcp_forwarding?: string } | undefined;
  const updates = security?.updates as { available?: number; security?: number; kernel_available?: boolean; reboot_required?: boolean; automatic?: boolean; source?: string; checked_at?: string; refreshing?: boolean } | undefined;
  const applicationVersion = security?.application_version as { branch?: string; current_commit?: string; latest_commit?: string; outdated?: boolean | null; checked_at?: string; error?: string; refreshing?: boolean } | undefined;
  const securitySystem = security?.system as { kernel?: string; ipv4_forwarding?: boolean; syn_cookies?: boolean; rp_filter?: boolean; rp_filter_mode?: number; rp_filter_valid?: boolean; redirects_disabled?: boolean; source_route_disabled?: boolean; dmesg_restricted?: boolean; auditd_active?: boolean; sudo_users?: string[]; login_users?: string[]; apparmor?: { active?: boolean; profiles?: number } } | undefined;
  const fail2ban = security?.fail2ban as { active?: boolean; jail_active?: boolean; currently_banned?: number; total_banned?: number } | undefined;
  const listeners = (security?.listeners as string[]) || [];
  const listenerSummary = security?.listener_summary as { tcp?: number; udp?: number; local_only?: number } | undefined;
  const legacy = (security?.legacy_services as Record<string, { active?: boolean; enabled?: string }>) || {};
  const applicationSecurity = security?.application_security as {
    admin_password_strong?: boolean; cors_restricted?: boolean; secrets_protected?: boolean;
    secrets_mode?: string; api_local_only?: boolean; control_command_protected?: boolean; control_command_mode?: string;
  } | undefined;
  const sshProtected = Boolean(
    ssh?.active
    && fail2ban?.active
    && fail2ban?.jail_active
  );
  const securityKnown = Boolean(security) && !securityLoading;
  const serviceModeActive = Boolean(services?.service_mode?.active || application?.service_mode?.active);
  const release = application?.release || applicationVersion;
  const releaseBranch = release?.branch || "light";
  const testReleaseActive = releaseBranch === "test-light";
  const firewallState: SecurityState = !securityKnown ? "inactive" : firewall?.active ? "active" : "inactive";
  const vpnFirewallState: SecurityState = !securityKnown || !firewall?.active ? "inactive" : firewall?.vpn_policy_healthy ? "active" : "warning";
  const fail2banState: SecurityState = !securityKnown || !fail2ban?.active ? "inactive" : fail2ban.jail_active ? "active" : "warning";
  const sshState: SecurityState = !securityKnown || !ssh?.active ? "inactive" : sshProtected ? "active" : "warning";
  const sshTunnelsState: SecurityState = !securityKnown || !ssh?.active ? "inactive" : ssh.x11_forwarding === "no" ? "active" : "warning";
  const sshPostureState = strongestSecurityState([sshState, sshTunnelsState]);
  const coreUpdatesState: SecurityState = !securityKnown || updates?.refreshing || updates?.available == null
    ? "inactive"
    : Number(updates.available) > 0 || updates.kernel_available || updates.reboot_required ? "warning" : "active";
  const kernelUpdateState: SecurityState = !securityKnown || updates?.refreshing
    ? "inactive"
    : updates?.kernel_available || updates?.reboot_required ? "warning" : "active";
  const automaticUpdatesState: SecurityState = !securityKnown || serviceModeActive ? "inactive" : updates?.automatic ? "active" : "inactive";
  const applicationVersionState: SecurityState = !securityKnown || applicationVersion?.refreshing
    ? "inactive"
    : applicationVersion?.error ? "critical" : applicationVersion?.outdated == null ? "inactive" : applicationVersion.outdated ? "warning" : "active";
  const appArmorState: SecurityState = !securityKnown || !securitySystem?.apparmor?.active ? "inactive" : "active";
  const auditState: SecurityState = !securityKnown || !securitySystem?.auditd_active ? "inactive" : "active";
  const tcpProtectionState: SecurityState = !securityKnown || !securitySystem?.syn_cookies ? "inactive" : "active";
  const kernelProtectionState: SecurityState = !securityKnown || (!securitySystem?.rp_filter_valid && !securitySystem?.dmesg_restricted)
    ? "inactive"
    : securitySystem.rp_filter_valid && securitySystem.dmesg_restricted ? "active" : "warning";
  const kernelRoutingState: SecurityState = !securityKnown || (!securitySystem?.redirects_disabled && !securitySystem?.source_route_disabled)
    ? "inactive"
    : securitySystem.redirects_disabled && securitySystem.source_route_disabled ? "active" : "warning";
  const accountsState: SecurityState = !securityKnown ? "inactive" : (securitySystem?.login_users?.length || 0) <= 5 ? "active" : "warning";
  const legacyServicesState: SecurityState = !securityKnown ? "inactive" : Object.values(legacy).some((service) => service.active) ? "active" : "inactive";
  const adminPasswordState: SecurityState = !securityKnown ? "inactive" : applicationSecurity?.admin_password_strong ? "active" : "warning";
  const secretsState: SecurityState = !securityKnown ? "inactive" : applicationSecurity?.secrets_protected ? "active" : "critical";
  const apiState: SecurityState = !securityKnown ? "inactive" : applicationSecurity?.api_local_only ? "active" : "critical";
  const controlCommandState: SecurityState = !securityKnown ? "inactive" : applicationSecurity?.control_command_protected ? "active" : "critical";
  const corsState: SecurityState = !securityKnown ? "inactive" : applicationSecurity?.cors_restricted ? "active" : "critical";
  const listenerState: SecurityState = !securityKnown ? "inactive" : firewall?.active ? "active" : listeners.length ? "warning" : "inactive";
  const systemUpdatesState = strongestSecurityState([coreUpdatesState, kernelUpdateState]);
  const securityChecks: Array<{ id: string; title: string; state: SecurityState }> = [
    { id: "firewall", title: "Firewall", state: firewallState },
    { id: "vpn-firewall", title: "VPN firewall", state: vpnFirewallState },
    { id: "fail2ban", title: "Fail2ban · SSH", state: fail2banState },
    { id: "ssh", title: "SSH · административный доступ", state: sshState },
    { id: "ssh-tunnels", title: "SSH-туннели", state: sshTunnelsState },
    { id: "system-updates", title: "Системные обновления", state: systemUpdatesState },
    { id: "automatic-updates", title: "Автоматические обновления", state: automaticUpdatesState },
    { id: "application-version", title: "Версия приложения", state: applicationVersionState },
    { id: "apparmor", title: "AppArmor", state: appArmorState },
    { id: "audit", title: "Аудит действий", state: auditState },
    { id: "tcp-protection", title: "Защита TCP", state: tcpProtectionState },
    { id: "kernel-protection", title: "Защита ядра", state: kernelProtectionState },
    { id: "kernel-routing", title: "Kernel routing", state: kernelRoutingState },
    { id: "accounts", title: "Учётные записи", state: accountsState },
    { id: "legacy-services", title: "Дополнительные VPN-службы", state: legacyServicesState },
    { id: "admin-password", title: "Пароль администратора", state: adminPasswordState },
    { id: "secrets", title: "Секреты приложения", state: secretsState },
    { id: "api", title: "Локальный API", state: apiState },
    { id: "control-command", title: "Команда управления", state: controlCommandState },
    { id: "cors", title: "Доверенные источники", state: corsState },
  ];
  const securityStates = securityChecks.map((check) => check.state);
  const securityStateCounts = securityStates.reduce<Record<SecurityState, number>>((counts, state) => {
    counts[state] += 1;
    return counts;
  }, { inactive: 0, active: 0, warning: 0, critical: 0 });
  const securityPostureState = strongestSecurityState(securityStates);
  const activeProtocol = isProtocolTab(tab) ? protocolStatuses[tab] : undefined;
  const activeProtocolRate = isProtocolTab(tab) ? protocolRates[tab] || { rx: 0, tx: 0 } : { rx: 0, tx: 0 };
  const activeProtocolImage = isProtocolTab(tab) ? protocolImages.find((image) => image.id === tab) : undefined;
  const operationActive = ["queued", "running", "active", "activating", "rebooting", "powering-off"].includes(application?.action?.state || "");
  const operationName = application?.action?.action || "";
  const operationLabel = actionLabels[operationName.split(":")[0]] || operationName;
  // A failed command belongs to the operation center; it does not mean that the
  // node itself is unavailable. Only live health data may turn the node red.
  const nodeHasError = application?.api.active === false;
  const nodeDegraded = Boolean(application?.containers.some((container) => container.healthy === false || (container.State || "").toLowerCase() !== "running"));
  const applicationComponentCount = application ? application.containers.length + 1 : 0;
  const healthyApplicationComponents = application
    ? Number(application.api.active) + application.containers.filter((component) => component.healthy).length
    : 0;
  const nodeState = !application ? "checking" : nodeHasError ? "error" : serviceModeActive ? "service" : operationActive || nodeDegraded ? "working" : "healthy";
  const nodeStateLabel = nodeState === "checking" ? "ПРОВЕРКА УЗЛА"
    : nodeState === "error" ? "УЗЕЛ С ОШИБКОЙ"
    : nodeState === "service" ? "СЕРВИСНЫЙ РЕЖИМ"
    : nodeState === "working" ? "ТРЕБУЕТ ВНИМАНИЯ"
    : "УЗЕЛ В СЕТИ";
  const applicationStateTitle = nodeState === "checking"
    ? "Проверка состояния"
    : nodeState === "error"
    ? "Есть ошибки"
    : operationActive ? operationLabel
    : serviceModeActive ? "Сервисный режим"
    : nodeDegraded ? "Нарушение работы"
    : "В сети";
  const navigationState = nodeState === "checking" ? "gray" : nodeState === "error" ? "red" : nodeState === "service" ? "blue" : nodeState === "working" ? "yellow" : "green";
  const countryCode = overview?.server.country_code?.toLowerCase() || "";

  return <main className="shell gateShell">
    <LightNavigation
      activeTab={tab}
      protocolImages={protocolImages}
      clientsCount={clients.length}
      nodeState={navigationState}
      nodeStateLabel={nodeStateLabel}
      server={overview?.server}
      onNavigate={(next) => setTab(next as Tab)}
    />

    <section className="content">
      <header className="gateMasthead" aria-label="Состояние сервера">
        <div className="gateMastNode">
          <CountryFlag code={countryCode} label={overview?.server.country || "Страна не определена"} />
          <div className="gateMastIdentity"><span>PRIMARY NODE</span><h2>{overview?.server.city || overview?.server.name || "VPS"}</h2><p><span>{overview?.server.country || "—"}</span><span className="mono">{overview?.server.public_ip || "—"}</span></p></div>
          <div className={`gateMastState ${navigationState}`}>{applicationStateTitle}</div>
        </div>
        <div className="gateMastFacts" aria-label="Метрики сервера">
          <div><span>UPTIME</span><strong>{uptime(overview?.server.uptime_s)}</strong></div>
          <div><span>LOAD</span><strong>{overview?.resources.load1.toFixed(2) || "—"}</strong></div>
          <div><span>CPU</span><strong>{(overview?.resources.cpu_percent || 0).toFixed(0)}%</strong></div>
          <div><span>RAM</span><strong>{memUsed.toFixed(0)}%</strong></div>
          <div><span>NETWORK</span><strong>↓ {bytes(networkRate.rx)}/с</strong></div>
        </div>
        <div className="gateMastActions">
          <div className={`refreshControl ${autoRefresh ? "active" : ""}`} data-refresh-interval="<1" aria-label="Управление обновлением данных">
            <button className="autoButton" onClick={() => setAutoRefresh((value) => !value)} aria-label={autoRefresh ? "Остановить автообновление" : "Включить автообновление"}><i /></button>
            <button className="iconButton" onClick={() => void refreshCurrent(true)} aria-label="Обновить текущий модуль">↻</button>
          </div>
          {lastUpdated && <span className="updatedAt">{lastUpdated.toLocaleTimeString("ru-RU")}</span>}
          <button className="ghostButton" onClick={() => { sessionStorage.removeItem("312-token"); setToken(""); }}>Выйти</button>
        </div>
      </header>
      {tab !== "overview" && !isProtocolTab(tab) && <div className="gateSectionIntro"><div><p className="eyebrow">312.NET / {navigationLabels[tab]}</p><h1>{labels[tab]}</h1><p>{overview?.server.city || "Город не определён"}, {overview?.server.country || "страна не определена"} · управление инфраструктурой</p></div></div>}
      {busy && <div className="loadingLine" />}

      {tab === "overview" && <section className="overview">
        <div className="metrics">
          <header className="metricsHeader">
            <div><p className="eyebrow">SERVER MONITORING</p><h2>Ресурсы VPS</h2><small>{metricsStatus}</small></div>
            <label>Период<select value={metricsPeriod} onChange={(event) => setMetricsPeriod(event.target.value as MetricsPeriod)} aria-label="Период истории метрик">
              <option value="live">5 минут</option><option value="day">24 часа</option><option value="week">7 дней</option><option value="quarter">90 дней</option>
            </select></label>
          </header>
          <Metric title="CPU" value={`${(overview?.resources.cpu_percent || 0).toFixed(0)}%`} percent={overview?.resources.cpu_percent || 0} detail={`Load ${overview?.resources.load1.toFixed(2) || "—"} · ${overview?.resources.cpu_count || "—"} vCPU`} history={chartHistory.load} resolutionSeconds={metricsResolution} />
          <Metric title="RAM" value={bytes(memoryUsedBytes)} percent={memUsed} detail={`${memUsed.toFixed(0)}% · всего ${bytes(overview?.resources.memory_total)}`} history={chartHistory.memory} resolutionSeconds={metricsResolution} />
          <Metric title="Disk" value={bytes(diskUsedBytes)} percent={diskUsed} detail={`${diskUsed.toFixed(0)}% · всего ${bytes(overview?.resources.disk_total)}`} history={chartHistory.disk} resolutionSeconds={metricsResolution} />
          <article className="panel metricCard networkMetric">
            <div><p className="eyebrow">NETWORK</p><h2>{bytes(networkRate.rx)}<small>/с</small></h2></div>
            <TrendGraph values={chartHistory.rx} secondary={chartHistory.tx} relative resolutionSeconds={metricsResolution} formatValue={(value) => `${bytes(value)}/с`} ariaLabel="История сетевой нагрузки" />
            <div className="networkDirections">
              <span>↓ Входящая <strong>{bytes(networkRate.rx)}/с</strong><i><b style={{ width: `${networkRate.rx || networkRate.tx ? Math.max(4, networkRate.rx / Math.max(networkRate.rx, networkRate.tx) * 100) : 4}%` }} /></i></span>
              <span>↑ Исходящая <strong>{bytes(networkRate.tx)}/с</strong><i><b style={{ width: `${networkRate.rx || networkRate.tx ? Math.max(4, networkRate.tx / Math.max(networkRate.rx, networkRate.tx) * 100) : 4}%` }} /></i></span>
            </div>
          </article>
        </div>
        <article className="panel protocolSummary">
          <div className="panelHead"><div><p className="eyebrow">ADDITIONAL MODULES</p><h2>Дополнительные модули</h2></div></div>
          <div className="protocolModuleGrid">
          {protocolImages.map((image) => {
            const protocol = image.id as Protocol;
            const isTunnel = image.kind === "tunnel" && lightModuleIds.includes(protocol);
            const state = isTunnel ? overview?.protocols[protocol] : undefined;
            const moduleState = image.installed ? state?.active ? "ACTIVE" : "STOPPED" : image.status === "planned" ? "PLANNED" : "AVAILABLE";
            const version = image.installed ? image.installed_version || "UNKNOWN" : image.installable ? image.available_version || "НЕ ПРОВЕРЕНО" : "—";
            return <article className={`protocolModuleCard${image.installed ? " installed" : ""}${image.kind === "agent" ? " agent" : ""}`} key={image.id}>
              <header><button className="protocolModuleOpen" onClick={() => image.installed && isTunnel && setTab(protocol)} disabled={!image.installed || !isTunnel}><span className={`protocol ${image.id}`}><ProtocolIcon protocol={image.id} /></span><span><strong>{image.name}</strong><small>{image.kind === "agent" ? "AGENT" : "TUNNEL"}</small></span></button><em className={moduleState.toLowerCase()}>{moduleState}</em></header>
              <dl><div><dt>VERSION</dt><dd title={image.update_available ? `${version} → ${image.available_version}` : version}>{version}{image.update_available ? ` → ${image.available_version}` : ""}</dd></div><div><dt>PORT</dt><dd>{state?.port || "—"}</dd></div></dl>
              <footer>
                {image.installed && isTunnel && <button className="danger" onClick={() => void removeProtocol(image)} disabled={busy}>Удалить</button>}
                {image.installed && isTunnel && (image.update_available
                  ? <button className={image.update_breaking ? "warning" : ""} onClick={() => void updateProtocol(image)} disabled={busy}>{installingProtocol === `update-${image.id}` ? "Обновление…" : "Обновить"}</button>
                  : <button onClick={() => void checkProtocolVersion(image)} disabled={busy || Boolean(checkingProtocolVersion)}>{checkingProtocolVersion === image.id ? "Проверка…" : "Проверить"}</button>)}
                {!image.installed && <button onClick={() => image.installable && void installProtocol(image)} disabled={!image.installable || busy || Boolean(installingProtocol)}>{!image.installable ? "Недоступно" : installingProtocol === image.id ? "Установка…" : "Установить"}</button>}
              </footer>
            </article>;
          })}
          </div>
          {!protocolImages.length && <div className="protocolEmpty"><span>—</span><p><strong>Нет доступных образов</strong><small>Добавьте manifest.json в каталог protocol-images</small></p></div>}
        </article>
      </section>}

      {tab === "security" && <section className="securityGrid">
        <article className={`panel securityHero state-${securityPostureState}`}>
          <div className="securityPostureSummary">
            <div className={`securityPostureMark state-${securityPostureState}`} aria-label={securityStateMeta[securityPostureState].label}>{securityStateMeta[securityPostureState].symbol}</div>
            <div><p className="eyebrow">SECURITY POSTURE</p><h2>{securityLoading ? "Обновляем информацию…" : securityStateMeta[securityPostureState].label}</h2><span>{securityLoading ? "Идёт проверка системы и служб" : "Состояния отражают фактическую работу каждой меры защиты"}</span></div>
            <div className="securityStateSummary" aria-label="Сводка состояний безопасности">
              {(["active", "inactive", "warning", "critical"] as SecurityState[]).map((state) => <span className={`state-${state}`} key={state}><i />{securityStateMeta[state].label}: {securityStateCounts[state]}</span>)}
            </div>
          </div>
          <div className="securityPostureStats">
            <div className={`securityPostureStat state-${sshPostureState}`}><header><h3>SSH</h3><i /></header><strong>{String(security?.failed_ssh_records_24h ?? "—")}</strong><small>отклонено за 24ч</small><span>{String(ssh?.active_connections ?? "—")} активных · {String(security?.accepted_ssh_24h ?? "—")} успешных</span></div>
            <div className={`securityPostureStat state-${listenerState}`}><header><h3>NETWORK LISTENERS</h3><i /></header><strong>{listeners.length}</strong><small>активных сетевых служб</small><span>TCP {listenerSummary?.tcp ?? "—"} · UDP {listenerSummary?.udp ?? "—"} · local {listenerSummary?.local_only ?? "—"}<br />UFW · {firewall?.rules?.length || 0} правил</span></div>
            <div className={`securityPostureStat state-${coreUpdatesState}`}><header><h3>CORE UPDATES</h3><i /></header><strong>{String(updates?.available ?? "—")}</strong><small>kernel &amp; packages</small><span>{updates?.security || 0} security updates</span><time>{updates?.refreshing ? "Проверяем…" : updates?.checked_at ? new Date(updates.checked_at).toLocaleString("ru-RU") : "Нет даты проверки"}</time></div>
          </div>
        </article>
        <article className="panel systemControls">
          <div><p className="eyebrow">SYSTEM POWER & KERNEL</p><h2>Системные действия</h2><span>Команды выполняются вне процесса панели через systemd</span></div>
          <div className="systemButtons">
            <button onClick={() => void runApplicationAction("kernel-update")} disabled={busy}><strong>Обновить ядро</strong><small>{updates?.kernel_available ? "проверит модули протоколов и перезагрузит VPS" : "проверит ядро, headers и модули протоколов"}</small></button>
            <button onClick={() => void runApplicationAction("reboot")} disabled={busy}><strong>Перезагрузить сервер</strong><small>Корректно завершает службы и запускает VPS заново</small></button>
            <button className="poweroffButton" onClick={() => void runApplicationAction("poweroff")} disabled={busy}><strong>Выключить сервер</strong><small>потребуется запуск у провайдера</small></button>
          </div>
        </article>
        <article className="panel securityList compactSecurity">
          <SecurityActionRow status={firewallState} title="Firewall" text={`UFW · ${firewall?.rules?.length || 0} правил`} onAction={() => void fixSecurity("vpn-firewall")} actionLabel="Включить" disabled={busy} />
          <SecurityActionRow
            status={vpnFirewallState}
            title="VPN FIREWALL POLICY"
            text={`Forwarding: ${firewall?.forwarding_enabled ? "ON" : "OFF"} · Stateful return: ${firewall?.stateful_return ? "ON" : "OFF"} · NAT/route: ${firewall?.vpn_policy_healthy ? "confirmed" : "invalid"}`}
            onAction={() => void fixSecurity("vpn-firewall")}
            actionLabel="Исправить"
            disabled={busy}
          />
          <SecurityActionRow status={fail2banState} title="Fail2ban · SSH" text={`В бане ${fail2ban?.currently_banned || 0} · всего ${fail2ban?.total_banned || 0}`} onAction={() => void fixSecurity("secure")} disabled={busy} />
          <SecurityActionRow
            status={sshState}
            title="SSH · административный доступ"
            text={ssh?.active === false
              ? "Служба остановлена · входящие SSH-подключения не принимаются"
              : `Из интернета: ${ssh?.publicly_allowed ? "открыт по согласованной политике" : "закрыт"} · Fail2ban: ${fail2ban?.active && fail2ban?.jail_active ? "защищает" : "не защищает"} · Password: ${String(ssh?.password_authentication || "unknown")} · Root: ${String(ssh?.permit_root_login || "unknown")}`}
            onAction={() => ssh?.active === false ? void runServiceAction("ssh", "SSH", "start") : void fixSecurity("secure")}
            actionLabel={ssh?.active === false ? "Включить" : "Исправить"}
            disabled={busy}
          />
          <SecurityActionRow
            status={coreUpdatesState}
            title="Системные пакеты"
            text={`Доступно ${updates?.available ?? "—"} · security ${updates?.security || 0}${updates?.reboot_required ? " · требуется перезагрузка" : ""}`}
            onAction={() => void fixSecurity(updates?.kernel_available ? "kernel-update" : "system-update")}
            actionLabel={updates?.kernel_available ? "Обновить ядро" : "Установить"}
            disabled={busy}
          />
          <SecurityActionRow status={kernelUpdateState} title="Обновление ядра" text={updates?.reboot_required ? "Новое ядро установлено · требуется перезагрузка" : updates?.kernel_available ? "Доступна новая версия ядра" : "Установлена актуальная версия ядра"} onAction={() => void fixSecurity("kernel-update")} actionLabel="Обновить" disabled={busy} />
          <SecurityActionRow status={automaticUpdatesState} title="Автоматические обновления" text={serviceModeActive ? "Заблокированы сервисным режимом · настройки сохранены" : updates?.automatic ? "Unattended upgrades · ON" : "Unattended upgrades · OFF"} onAction={() => serviceModeActive ? setTab(testReleaseActive ? "application" : "services") : void fixSecurity("secure")} actionLabel={serviceModeActive ? "Открыть режим" : "Включить"} disabled={busy} />
          <SecurityActionRow
            status={applicationVersionState}
            title="Версия приложения"
            text={applicationVersion?.refreshing && applicationVersion?.outdated == null
              ? `Проверяется ветка ${applicationVersion?.branch || "main"}…`
              : applicationVersion?.error
                ? applicationVersion.error
                : applicationVersion?.outdated
                  ? `Устарела: ${applicationVersion.current_commit || "unknown"} · ${applicationVersion.branch || "light"}: ${applicationVersion.latest_commit || "unknown"}`
                  : `Актуальна: ${applicationVersion?.current_commit || "unknown"} · ветка ${applicationVersion?.branch || "main"}`}
            onAction={() => void runApplicationAction(testReleaseActive ? "test-update" : "update")}
            actionLabel={testReleaseActive ? "Обновить test-light" : "Обновить light"}
            disabled={busy}
          />
          <SecurityRow status={appArmorState} title="AppArmor" text={`${securitySystem?.apparmor?.profiles || 0} профилей · ${securitySystem?.apparmor?.active ? "активен" : "выключен"}`} />
          <SecurityActionRow status={auditState} title="Аудит действий" text={`auditd · ${securitySystem?.auditd_active ? "активен" : "остановлен"}`} onAction={() => void fixSecurity("secure")} disabled={busy} />
          <SecurityActionRow status={tcpProtectionState} title="Защита TCP" text={`SYN ${securitySystem?.syn_cookies ? "ON" : "OFF"} · Forwarding ${securitySystem?.ipv4_forwarding ? "ON" : "OFF"}`} onAction={() => void fixSecurity("secure")} disabled={busy} />
          <SecurityActionRow status={kernelProtectionState} title="Защита ядра" text={`RP ${securitySystem?.rp_filter_mode === 1 ? "strict" : securitySystem?.rp_filter_mode === 2 ? "loose" : securitySystem?.rp_filter_valid ? "VPN-safe" : "OFF"} · dmesg ${securitySystem?.dmesg_restricted ? "restricted" : "open"}`} onAction={() => void fixSecurity("secure")} disabled={busy} />
          <SecurityActionRow
            status={kernelRoutingState}
            title="KERNEL ROUTING"
            text={`Redirects: ${securitySystem?.redirects_disabled ? "blocked" : "allowed"} · Source route: ${securitySystem?.source_route_disabled ? "blocked" : "allowed"}`}
            onAction={() => void fixSecurity("secure")}
            disabled={busy}
          />
          <SecurityActionRow
            status={sshTunnelsState}
            title="SSH-туннели"
            text={ssh?.active === false ? "Служба остановлена · настройки туннелей не применяются" : `X11: ${ssh?.x11_forwarding || "unknown"} · TCP forwarding: ${ssh?.tcp_forwarding || "unknown"} (оставлен для административного контроля) · MaxAuthTries: ${ssh?.max_auth_tries || "unknown"}`}
            onAction={() => void fixSecurity("secure")}
            disabled={busy}
          />
          <SecurityRow status={accountsState} title="Учётные записи" text={`sudo ${securitySystem?.sudo_users?.length || 0} · login ${securitySystem?.login_users?.length || 0}`} />
          <SecurityRow
            status={legacyServicesState}
            title="Дополнительные VPN-службы"
            text={Object.values(legacy).some((service) => service.active)
              ? `Активно ${Object.values(legacy).filter((service) => service.active).length} · установлены отдельно и не управляются приложением`
              : "Не обнаружены"}
          />
          <SecurityActionRow status={adminPasswordState} title="Пароль администратора" text={applicationSecurity?.admin_password_strong ? "Достаточная длина и стойкость пароля панели" : "Стандартный пароль считается небезопасным"} onAction={() => setPasswordDialog(true)} actionLabel="Изменить пароль" alwaysAction disabled={busy} />
          <SecurityRow status={secretsState} title="Секреты приложения" text={`/etc/vps-control.env · права ${applicationSecurity?.secrets_mode || "не определены"} · владелец root`} />
          <SecurityRow status={apiState} title="Локальный API" text={applicationSecurity?.api_local_only ? "API слушает только 127.0.0.1:8000" : "API не найден локально или доступен на внешнем интерфейсе"} />
          <SecurityRow status={controlCommandState} title="Команда управления" text={`vps-control · права ${applicationSecurity?.control_command_mode || "не определены"} · запись ограничена`} />
          <SecurityRow status={corsState} title="Доверенные источники" text={applicationSecurity?.cors_restricted ? "CORS ограничен заданными адресами панели" : "CORS разрешает запросы с произвольных источников"} />
        </article>
        <article className="panel listeners"><div className="panelHead"><div><p className="eyebrow">LIVE NETWORK</p><h2>Открытые порты</h2></div><span>{listeners.length} listeners · kernel {securitySystem?.kernel || "—"}</span></div><pre>{listeners.join("\n") || "Нет данных"}</pre></article>
        <article className={`panel logDrawer ${securityLogsOpen ? "open" : ""}`}>
          <button className="logDrawerToggle" onClick={() => setSecurityLogsOpen((value) => !value)}>
            <span><strong>JOURNALCTL</strong><small>ПО ЗАПРОСУ · ЖУРНАЛЫ БЕЗОПАСНОСТИ</small></span><em>{securityLogsOpen ? "Скрыть ↑" : "Открыть ↓"}</em>
          </button>
          {securityLogsOpen && <div className="logDrawerBody"><div className="logTools"><div className="logTabs">
            {(["ssh", "firewall", "system"] as const).map((source) =>
              <button key={source} className={securityLogSource === source ? "active" : ""} onClick={() => { setSecurityLogSource(source); setSecurityLogs([]); }}>
                {source === "ssh" ? "SSH" : source === "firewall" ? "Firewall" : "Система"}
              </button>
            )}
          </div><div className="logActions"><span>{securityNewLogCount ? `${securityNewLogCount} новых · ` : ""}{autoRefresh ? `автообновление${securityLogsUpdatedAt ? ` · ${securityLogsUpdatedAt.toLocaleTimeString("ru-RU")}` : ""}` : "автообновление выключено"}</span><button className="miniButton" onClick={() => void loadSecurityLogs()}>Обновить</button><button className="miniButton" disabled={!securityLogs.length} onClick={() => downloadLogs(`security-${securityLogSource}-${new Date().toISOString().slice(0, 10)}.log`, securityLogs)}>Выгрузить</button></div></div>
          <pre>{securityLogs.join("\n") || "В журнале нет записей"}</pre></div>}
        </article>
      </section>}

      {tab === "application" && <section className="applicationGrid">
        <article className="panel applicationHero">
          <div><p className="eyebrow">VPS-CONTROL</p><h2>Управление приложением</h2><p>Команды запускаются на сервере как отдельные системные задачи. Вы не потеряете интерфейс во время обновления или перезапуска.</p></div>
          <span className={application?.api.active ? "onlinePill" : "offlinePill"}>{application?.api.active ? "API работает" : "API остановлен"}</span>
        </article>
        <article className="panel actionPanel">
          <div className="panelHead"><div><p className="eyebrow">SUDO VPS-CONTROL</p><h2>Доступные действия</h2></div></div>
          <div className="actionButtons">
            <button onClick={() => void runApplicationAction("restart")} disabled={busy}><strong>Перезапустить приложение</strong><small>Перезапускает панель и API без перезагрузки VPS</small></button>
            {!testReleaseActive && <button onClick={() => void runApplicationAction("update")} disabled={busy}><strong>{release?.outdated ? "Обновить light" : "Проверить обновление light"}</strong><small>{release?.outdated ? "Доступна новая production-версия" : "Текущий канал: light · production"}</small></button>}
            {serviceModeActive && <button onClick={() => void runApplicationAction("test-update")} disabled={busy}><strong>{testReleaseActive ? "Обновить test-light" : "Перейти на test-light"}</strong><small>{testReleaseActive ? "Устанавливает актуальную тестовую сборку" : "Сохраняет light для безопасного возврата"}</small></button>}
            {serviceModeActive && testReleaseActive && application?.service_mode?.rollback_available && <button onClick={() => void runApplicationAction("test-rollback")} disabled={busy}><strong>Вернуться на light</strong><small>Восстанавливает production-версию, сохранённую перед тестированием</small></button>}
            <button onClick={() => void runApplicationAction("network-check")} disabled={busy}><strong>Проверить подключения</strong><small>Проверяет интернет, установленные протоколы и доступность портов</small></button>
            <button onClick={() => void runApplicationAction("integrity-check")} disabled={busy}><strong>Проверить целостность</strong><small>Проверяет файлы, права доступа и настройки компонентов</small></button>
            <button onClick={() => void runApplicationAction("identity")} disabled={busy}><strong>Обновить данные сервера</strong><small>Повторно определяет публичный IP и географические данные VPS</small></button>
            <button onClick={() => void runApplicationAction("optimize")} disabled={busy}><strong>Освободить ресурсы</strong><small>Удаляет неиспользуемые пакеты, кэши, временные файлы и старые журналы</small></button>
          </div>
        </article>
        <article className="panel systemControls">
          <div><p className="eyebrow">SYSTEM POWER &amp; KERNEL</p><h2>Системные действия</h2><span>Команды выполняются через systemd и не блокируют интерфейс панели.</span></div>
          <div className="systemButtons">
            <button onClick={() => void runApplicationAction("kernel-update")} disabled={busy}><strong>Обновить ядро</strong><small>{updates?.kernel_available ? "Проверит модули протоколов и перезагрузит VPS" : "Проверит ядро, headers и модули протоколов"}</small></button>
            <button onClick={() => void runApplicationAction("reboot")} disabled={busy}><strong>Перезагрузить сервер</strong><small>Корректно завершает службы и запускает VPS заново</small></button>
            <button className="poweroffButton" onClick={() => void runApplicationAction("poweroff")} disabled={busy}><strong>Выключить сервер</strong><small>Потребуется запуск у провайдера</small></button>
          </div>
        </article>
        <article className="panel panelAccess">
          <div>
            <p className="eyebrow">APPLICATION MODE</p>
            <h3>{services?.panel_access?.public ? "Публичный доступ открыт" : "Доступ через защищённую сеть"}</h3>
            <small>{services?.panel_access?.public ? "Панель доступна по публичному адресу сервера." : `Локальные адреса: ${services?.panel_access?.vpn_urls.join(" · ") || "недоступны"}`}</small>
          </div>
          <div className="panelAccessActions">
            <label className="serviceModeSwitch">
              <span><strong>Сервисный режим</strong><small>{testReleaseActive ? "сначала вернитесь на light" : serviceModeActive ? "обслуживание выполняется" : "обычная работа"}</small></span>
              <input type="checkbox" checked={serviceModeActive} onChange={(event) => void changeServiceMode(event.target.checked)} disabled={busy || testReleaseActive} /><i />
            </label>
            <label className="serviceModeSwitch protectedAccessSwitch">
              <span><strong>Защищённый доступ</strong><small>{services?.panel_access?.public ? "публичный адрес открыт" : "только локальная сеть"}</small></span>
              <input type="checkbox" checked={!services?.panel_access?.public} onChange={(event) => void changePanelAccess(event.target.checked ? "vpn" : "external")} disabled={busy || !services || serviceModeActive} /><i />
            </label>
          </div>
        </article>
        <article className="panel statusPanel">
          <div className="panelHead"><div><p className="eyebrow">RUNTIME</p><h2>Состояние компонентов</h2></div><span>{application
            ? `${healthyApplicationComponents}/${applicationComponentCount} · ${application.runtime?.mode || "unknown"}${application.checked_at ? ` · ${new Date(application.checked_at).toLocaleTimeString("ru-RU")}` : ""}`
            : "проверка…"}</span></div>
          <div className="runtimeRows">
            {!application
              ? <SecurityRow status="inactive" title="API панели" text="Состояние проверяется" />
              : <SecurityActionRow
                  status={application.api.active ? "active" : "critical"}
                  title="API панели"
                  text={`${application.api.active ? `Работает ${componentUptime(application.api.uptime_seconds)}` : "Не принимает команды интерфейса"} · рестарты ${application.api.restarts ?? 0} · автозапуск ${application.api.enabled ? "включён" : "отключён"} · ${application.api.endpoint || "127.0.0.1:8000"}`}
                  onAction={() => void runServiceAction("api", "API панели", "restart")}
                  actionLabel="Перезапустить"
                  disabled={busy}
                />}
            {(application?.containers || []).map((container, index) =>
              <SecurityActionRow
                key={`${container.Name || container.Service}-${index}`}
                status={container.healthy ? "active" : (container.State || "").toLowerCase() === "running" ? "warning" : "critical"}
                title={container.component_name || container.Service || `Компонент ${index + 1}`}
                text={`${container.purpose || "Компонент приложения"} · ${container.healthy ? `работает ${componentUptime(container.uptime_seconds)}` : container.status_text || container.Status || container.State || "состояние неизвестно"} · рестарты ${container.restarts ?? 0} · автозапуск ${container.enabled ? "включён" : "отключён"} · ${container.endpoint || "адрес не определён"}`}
                onAction={() => void runServiceAction(container.service_id || container.Service || "web", container.component_name || container.Service || "Компонент", "restart")}
                actionLabel="Перезапустить"
                disabled={busy}
              />
            )}
            {application?.action?.action && <SecurityRow status={application.action.state === "failed" || application.action.result === "failed" ? "critical" : operationActive || application.action.result === "success" ? "active" : "inactive"} title={`Последняя команда: ${actionLabels[application.action.action.split(":")[0]] || application.action.action}`} text={application.action.state === "running" ? "Команда выполняется системной службой" : application.action.result === "success" ? "Команда завершена без ошибок" : application.action.message || "Результат выполнения уточняется"} />}
          </div>
        </article>
        <article className="panel logPanel applicationLogs">
          <div className="panelHead"><div><p className="eyebrow">SYSTEMD JOURNAL · СВЕЖИЕ СНАЧАЛА</p><h2>Журнал приложения</h2></div><div className="logActions"><button className="miniButton" onClick={() => void loadApplicationLogs()}>Обновить</button><button className="miniButton" disabled={!applicationLogs.length} onClick={() => downloadLogs(`application-${new Date().toISOString().slice(0, 10)}.log`, applicationLogs)}>Выгрузить</button></div></div>
          <pre>{applicationLogs.join("\n") || "В журнале нет записей"}</pre>
        </article>
      </section>}

      {tab === "services" && <section className="servicesGrid">
        <article className="panel servicesHero">
          <div><p className="eyebrow">SYSTEMD CONTROL</p><h2>Службы и обслуживание</h2></div>
          <div className="serviceSummary">
            <span className={services?.failed_units ? "offlinePill" : "onlinePill"}>{services?.failed_units || 0} аварийных служб</span>
            {services?.reboot_required && <span className="warningPill">Требуется перезагрузка</span>}
          </div>
        </article>

        <article className="panel panelAccess">
          <div>
            <p className="eyebrow">APPLICATION MODE</p>
            <h3>{services?.panel_access?.public ? "Публичный доступ открыт" : "Доступ через защищённый туннель"}</h3>
            <small>{services?.panel_access?.public
              ? "Панель доступна по публичному IP сервера."
              : `Локальные адреса: ${services?.panel_access?.vpn_urls.join(" · ") || "недоступны"}`}</small>
          </div>
          <div className="panelAccessActions">
            <label className="serviceModeSwitch">
              <span><strong>Сервисный режим</strong><small>{testReleaseActive ? "сначала вернитесь на light" : serviceModeActive ? "обслуживание выполняется" : "обычная работа"}</small></span>
              <input type="checkbox" checked={serviceModeActive} onChange={(event) => void changeServiceMode(event.target.checked)} disabled={busy || testReleaseActive} />
              <i />
            </label>
            <label className="serviceModeSwitch protectedAccessSwitch">
              <span><strong>Защищённый доступ</strong><small>{services?.panel_access?.public ? "публичный адрес открыт" : "только локальная сеть"}</small></span>
              <input
                type="checkbox"
                checked={!services?.panel_access?.public}
                onChange={(event) => void changePanelAccess(event.target.checked ? "vpn" : "external")}
                disabled={busy || !services || serviceModeActive}
              />
              <i />
            </label>
          </div>
        </article>

        <article className="panel servicesPanel">
          <div className="panelHead"><div><p className="eyebrow">MANAGED SERVICES</p><h2>Системные службы</h2></div><span>{services?.items.filter((item) => item.active).length || 0} активных</span></div>
          <div className="serviceRows">
            {(services?.items || []).map((service) => <div className="serviceRow" key={service.id}>
              <i className={service.active ? "serviceOnline" : "serviceOffline"} />
              <div><strong>{service.name}</strong><small>{service.unit} · {service.substate} · автозапуск: {service.enabled ? "да" : "нет"}</small></div>
              <dl><div><dt>Перезапуски</dt><dd>{service.restarts}</dd></div><div><dt>Активна с</dt><dd>{service.active_since || "—"}</dd></div></dl>
              <div className="serviceActions">
                {service.controls.includes(service.active ? "restart" : "start") && <button onClick={() => void runServiceAction(service.id, service.name, service.active ? "restart" : "start")} disabled={busy}>{service.active ? "Перезапустить" : "Запустить"}</button>}
                {service.active && (service.controls.includes("stop") || service.disabled_controls?.includes("stop")) && <button
                  className="serviceStop"
                  onClick={() => void runServiceAction(service.id, service.name, "stop")}
                  disabled={busy || service.disabled_controls?.includes("stop")}
                  title={service.disabled_controls?.includes("stop") ? "Остановка отключит панель управления и доступ к восстановлению" : undefined}
                >Остановить</button>}
              </div>
            </div>)}
          </div>
        </article>

        <article className="panel loggingControl">
          <div>
            <p className="eyebrow">LOG MANAGEMENT</p>
            <h2>Запись и хранение журналов</h2>
            <small>Системные службы, приложение, контейнеры и история мониторинга · {services?.logging?.disk_usage || "объём уточняется"}</small>
          </div>
          <div className="loggingSettings">
            <label className="serviceModeSwitch protectedAccessSwitch">
              <span><strong>Запись логов</strong><small>{loggingDraft?.persistent ? "сохраняются после перезагрузки" : "только временно, до перезагрузки"}</small></span>
              <input
                type="checkbox"
                checked={loggingDraft?.persistent ?? true}
                onChange={(event) => updateLoggingDraft({ persistent: event.target.checked })}
                disabled={busy || !services}
              />
              <i />
            </label>
            <label className="loggingRetention">
              <span><strong>Автоматическая очистка</strong><small>Срок хранения системных журналов</small></span>
              <select
                value={loggingDraft?.retention_days ?? 30}
                onChange={(event) => updateLoggingDraft({ retention_days: Number(event.target.value) })}
                disabled={busy || !services}
              >
                <option value={1}>1 день</option>
                <option value={7}>7 дней</option>
                <option value={14}>14 дней</option>
                <option value={30}>30 дней</option>
                <option value={90}>90 дней</option>
                <option value={0}>Не очищать автоматически</option>
              </select>
            </label>
            <button className="primaryButton logSaveButton" onClick={() => void saveLoggingSettings()} disabled={busy || !loggingDraft}>Сохранить</button>
            <button className="dangerButton" onClick={() => void clearManagedLogs()} disabled={busy}>Очистить журналы</button>
          </div>
        </article>

        <article className="panel automationCenter">
          <div className="automationCenterHead">
            <div><p className="eyebrow">MAINTENANCE SCHEDULE</p><h2>Плановое обслуживание</h2><small>Системные операции, обновления приложения и ядра по расписанию</small></div>
            <button onClick={() => void saveAutomation()} disabled={busy || !services}>Сохранить изменения</button>
          </div>
          <div className="automationRows"><AutomationEditor
            title="Перезагрузка"
            description="Плановая перезагрузка VPS. Используйте ночное окно с минимальной активностью."
            value={automationDraft?.reboot}
            timer={services?.timers.reboot}
            onChange={(patch) => updateAutomation("reboot", patch)}
          />
          <AutomationEditor
            title="Очистка"
            description="Очистка apt-кэша, временных файлов и старых системных журналов."
            value={automationDraft?.cleanup}
            timer={services?.timers.cleanup}
            onChange={(patch) => updateAutomation("cleanup", patch)}
          />
          <AutomationEditor
            title="Проверка версий протоколов"
            description="Сканирует официальные источники модулей и отмечает доступные версии. Обновления не устанавливаются автоматически."
            value={automationDraft?.protocol_scan}
            timer={services?.timers.protocol_scan}
            onChange={(patch) => updateAutomation("protocol_scan", patch)}
          />
          <AutomationEditor
            title="Плановое обновление основной версии приложения"
            description="Устанавливает последний подготовленный production-релиз ветки light. Тестовая ветка test-light не используется."
            value={automationDraft?.application_update}
            timer={services?.timers.application_update}
            onChange={(patch) => updateAutomation("application_update", patch)}
          />
          <AutomationEditor
            title="Плановое обновление ядра"
            description="Обновляет ядро и headers, проверяет модули протоколов и при необходимости перезагружает VPS."
            value={automationDraft?.kernel_update}
            timer={services?.timers.kernel_update}
            onChange={(patch) => updateAutomation("kernel_update", patch)}
          />
          </div>
          {serviceModeActive && <div className="automationNote">Сервисный режим · выполнение всех плановых сценариев заблокировано · настройки расписания доступны · метрики продолжают собираться</div>}
          <div className="automationNote">Persistent=true · пропуск из-за выключенного сервера выполняется после запуска; сервисный режим не создаёт отложенный запуск</div>
        </article>
      </section>}

      {isProtocolTab(tab) && activeProtocol && <ProtocolWorkspace
        protocol={tab}
        status={activeProtocol}
        image={activeProtocolImage}
        rate={activeProtocolRate}
        installed={installedProtocols}
        busy={busy}
        checkingConnection={checkingConnection === tab}
        checkingDiagnostics={checkingDiagnostics === tab}
        checkingVersion={checkingProtocolVersion === activeProtocolImage?.id}
        updating={installingProtocol === `update-${activeProtocolImage?.id}`}
        onNavigate={setTab}
        onRestart={() => void restartProtocol(tab)}
        onCheckVersion={() => activeProtocolImage && void checkProtocolVersion(activeProtocolImage)}
        onUpdate={() => activeProtocolImage && void updateProtocol(activeProtocolImage)}
        onRemove={() => activeProtocolImage && void removeProtocol(activeProtocolImage)}
        onCheckConnection={() => void checkProtocolConnection(tab)}
        onCheckDiagnostics={() => void checkNetworkDiagnostics(tab)}
      />}

      {tab === "clients" && (installedProtocols.length ? <ConnectionsView clients={clients} protocols={installedProtocols} busy={busy} onNew={() => setConnectionDialog(true)} onRemove={(id) => void removeClient(id)} /> : <section className="clientsLayout"><article className="panel noConnectionProtocols"><span>◎</span><h2>Нет установленных протоколов</h2><p>Установите хотя бы один сетевой модуль на странице «Обзор», после чего здесь появится создание персональных подключений.</p><button type="button" className="primaryButton" onClick={() => setTab("overview")}>Перейти к модулям <span>→</span></button></article></section>)}
      {connectionDialog && installedProtocols.length > 0 && <ConnectionDialog protocols={installedProtocols} onClose={() => setConnectionDialog(false)} onCreate={createClient} onCreated={loadClients} onError={setError} onDownload={downloadConfig} />}
      {passwordDialog && <div className="confirmBackdrop" role="presentation" onMouseDown={closePasswordDialog}>
        <form className="confirmDialog" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()} onSubmit={changeAdminPassword}>
          <p className="eyebrow">ADMINISTRATOR ACCESS</p><h2>Изменить пароль администратора</h2>
          <p>Новый пароль сохраняется на сервере с закрытыми правами доступа. После смены потребуется войти заново.</p>
          <label>Текущий пароль<input autoFocus type="password" autoComplete="current-password" maxLength={256} value={currentAdminPassword} onChange={(event) => setCurrentAdminPassword(event.target.value)} required /></label>
          <label>Новый пароль<input type="password" autoComplete="new-password" minLength={16} maxLength={128} value={newAdminPassword} onChange={(event) => setNewAdminPassword(event.target.value)} required /></label>
          <label>Повторите новый пароль<input type="password" autoComplete="new-password" minLength={16} maxLength={128} value={confirmAdminPassword} onChange={(event) => setConfirmAdminPassword(event.target.value)} required /></label>
          <div className="confirmActions"><button type="button" onClick={closePasswordDialog}>Отмена</button><button className="confirmPrimary" type="submit" disabled={busy || !currentAdminPassword || newAdminPassword.length < 16 || newAdminPassword !== confirmAdminPassword}>Сохранить пароль</button></div>
        </form>
      </div>}
      {confirmation && <div className="confirmBackdrop" role="presentation" onMouseDown={() => closeConfirmation(false)}>
        <form className={`confirmDialog ${confirmation.danger ? "danger" : ""}`} role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" onMouseDown={(event) => event.stopPropagation()} onSubmit={(event) => {
          event.preventDefault();
          if (!confirmation.phrase || confirmationInput === confirmation.phrase) closeConfirmation(true);
        }}>
          <div className="confirmMark">{confirmation.danger ? "!" : "✓"}</div>
          <p className="eyebrow">ACTION CONFIRMATION</p>
          <h2 id="confirm-title">{confirmation.title}</h2>
          <p>{confirmation.message}</p>
          {confirmation.phrase && <label>Для подтверждения введите <strong>{confirmation.phrase}</strong>
            <input autoFocus value={confirmationInput} onChange={(event) => setConfirmationInput(event.target.value)} autoComplete="off" />
          </label>}
          <div className="confirmActions">
            <button type="button" onClick={() => closeConfirmation(false)}>Отмена</button>
            <button className="confirmPrimary" type="submit" disabled={Boolean(confirmation.phrase && confirmationInput !== confirmation.phrase)}>{confirmation.confirmLabel}</button>
          </div>
        </form>
      </div>}
      <VersionFooter />
    </section>
  </main>;
}

function Logo() {
  return <div className="brand"><span className="brandMark"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M4.5 5.5h23L16 27 4.5 5.5Z" /><path d="m16 10 6 11H10l6-11Z" /></svg></span><div><strong>312<span>.net</span></strong><small>INFRASTRUCTURE</small></div></div>;
}
function AutomationEditor({
  title, description, value, timer, onChange,
}: {
  title: string;
  description: string;
  value?: AutomationSchedule;
  timer?: { installed: boolean; active: boolean; last_trigger: string; next_run: string };
  onChange: (patch: Partial<AutomationSchedule>) => void;
}) {
  const weekdays = [
    ["Mon", "Понедельник"], ["Tue", "Вторник"], ["Wed", "Среда"], ["Thu", "Четверг"],
    ["Fri", "Пятница"], ["Sat", "Суббота"], ["Sun", "Воскресенье"],
  ];
  if (!value) return <div className="automationRow"><div><h3>{title}</h3><small>Загрузка параметров…</small></div></div>;
  const time = `${String(value.hour).padStart(2, "0")}:${String(value.minute).padStart(2, "0")}`;
  return <div className={`automationRow ${value.enabled ? "enabled" : ""}`}>
    <div className="automationIdentity"><h3>{title}</h3><small>{description}</small></div>
    <div className="automationFields">
      <label>Период<select value={value.cadence} onChange={(event) => onChange({ cadence: event.target.value as AutomationSchedule["cadence"] })}>
        <option value="daily">Ежедневно</option><option value="weekly">Еженедельно</option><option value="monthly">Ежемесячно, 1-го числа</option>
      </select></label>
      {value.cadence === "weekly" && <label>День недели<select value={value.weekday} onChange={(event) => onChange({ weekday: event.target.value })}>
        {weekdays.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
      </select></label>}
      <label>Время сервера<input type="time" value={time} onChange={(event) => {
        const [hour, minute] = event.target.value.split(":").map(Number);
        if (Number.isInteger(hour) && Number.isInteger(minute)) onChange({ hour, minute });
      }} /></label>
    </div>
    <div className="automationRun"><small>Следующий запуск</small><strong>{timer?.next_run || "—"}</strong><span>{timer?.last_trigger ? `последний: ${timer.last_trigger}` : "ещё не запускалось"}</span></div>
    <label className="automationSwitch"><input type="checkbox" checked={value.enabled} onChange={(event) => onChange({ enabled: event.target.checked })} /><span /><em>{value.enabled ? "Вкл" : "Выкл"}</em></label>
  </div>;
}
function TrendGraph({ values, secondary, relative = false, resolutionSeconds = 1, formatValue = (value) => `${Math.round(value)}%`, ariaLabel }: {
  values: Array<number | null>; secondary?: Array<number | null>; relative?: boolean; resolutionSeconds?: number; formatValue?: (value: number) => string; ariaLabel: string;
}) {
  const width = 240;
  const height = 72;
  const known = (value: number | null): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
  const all = (secondary ? [...values, ...secondary] : values).filter(known);
  const ceiling = relative ? Math.max(1, ...all) : 100;
  const coordinates = (series: Array<number | null>) => {
    const segments: Array<Array<{ x: number; y: number }>> = [];
    let segment: Array<{ x: number; y: number }> = [];
    series.forEach((value, index) => {
      if (!known(value)) {
        if (segment.length) segments.push(segment);
        segment = [];
        return;
      }
      const x = series.length > 1 ? index / (series.length - 1) * width : width;
      segment.push({ x, y: height - Math.min(value / ceiling, 1) * height });
    });
    if (segment.length) segments.push(segment);
    return segments;
  };
  const primarySegments = coordinates(values);
  const secondarySegments = secondary ? coordinates(secondary) : [];
  const stepPath = (coordinatesList: Array<{ x: number; y: number }>) => coordinatesList.reduce((path, point, index) => {
    if (!index) return `M ${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
    return `${path} H ${point.x.toFixed(1)} V ${point.y.toFixed(1)}`;
  }, "");
  const primaryValues = values.filter(known);
  const secondaryValues = secondary?.filter(known) || [];
  const primaryLast = primarySegments.at(-1)?.at(-1);
  const secondaryLast = secondarySegments.at(-1)?.at(-1);
  const primaryPeak = primaryValues.length ? Math.max(...primaryValues) : 0;
  const secondaryPeak = secondaryValues.length ? Math.max(...secondaryValues) : 0;
  const elapsedSeconds = Math.max(0, (values.length - 1) * resolutionSeconds);
  const elapsedLabel = elapsedSeconds >= 86400 ? `${Math.round(elapsedSeconds / 86400)} д` : elapsedSeconds >= 3600 ? `${Math.round(elapsedSeconds / 3600)} ч` : elapsedSeconds >= 60 ? `${Math.round(elapsedSeconds / 60)} мин` : `${elapsedSeconds} сек`;
  const intervalLabel = resolutionSeconds >= 3600 ? `${Math.round(resolutionSeconds / 3600)} ч` : resolutionSeconds >= 60 ? `${Math.round(resolutionSeconds / 60)} мин` : `${resolutionSeconds} сек`;
  return <div className={`trendGraph ${secondary ? "dual" : ""}`} role="img" aria-label={ariaLabel}>
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      {primarySegments.map((segment, index) => segment.length > 1 && <path key={`area-${index}`} className="primaryArea" d={`${stepPath(segment)} V ${height} H ${segment[0].x.toFixed(1)} Z`} />)}
      {primarySegments.map((segment, index) => segment.length > 1 && <path key={`primary-${index}`} className="primaryTrend" d={stepPath(segment)} />)}
      {secondarySegments.map((segment, index) => segment.length > 1 && <path key={`secondary-${index}`} className="secondaryTrend" d={stepPath(segment)} />)}
      {primaryLast && primaryValues.length > 1 && <circle className="primaryPoint" cx={primaryLast.x} cy={primaryLast.y} r="2.8" />}
      {secondaryLast && secondaryValues.length > 1 && <circle className="secondaryPoint" cx={secondaryLast.x} cy={secondaryLast.y} r="2.4" />}
    </svg>
    <span className="trendYAxis"><b>{formatValue(ceiling)}</b><b>{formatValue(0)}</b></span>
    <span className="trendXAxis"><b>−{elapsedLabel}</b><b>сейчас</b></span>
    <span className="trendSummary">
      <b>Сейчас {formatValue(primaryValues.at(-1) || 0)}</b>
      <b>Пик {formatValue(primaryPeak)}</b>
      {secondary && <b>TX пик {formatValue(secondaryPeak)}</b>}
    </span>
    {secondary && <span className="trendLegend"><i /> RX <i /> TX</span>}
    <small>{primaryValues.length < 2 ? "Сбор данных…" : `${primaryValues.length} замеров · интервал ${intervalLabel}`}</small>
  </div>;
}
function Metric({ title, value, percent, detail, history, resolutionSeconds }: { title: string; value: string; percent: number; detail: string; history: Array<number | null>; resolutionSeconds: number }) {
  const normalized = Math.max(0, Math.min(100, percent));
  return <article className="panel metricCard">
    <div className="metricCopy"><p className="eyebrow">{title.toUpperCase()}</p><h2>{value}</h2><small>{detail}</small></div>
    <TrendGraph values={history} resolutionSeconds={resolutionSeconds} ariaLabel={`${title}: ${value}, ${Math.round(normalized)} процентов`} />
  </article>;
}
function SecurityRow({ status, title, text }: { status: SecurityState; title: string; text: string }) {
  const meta = securityStateMeta[status];
  return <div className={`securityStateRow state-${status}`}><span className="securityStateMark">{meta.symbol}</span><p><strong>{title}</strong><small>{text}</small></p><em className="securityStatePill">{meta.label}</em></div>;
}
function SecurityActionRow({ status, title, text, onAction, actionLabel = "Исправить", alwaysAction = false, disabled = false }: { status: SecurityState; title: string; text: string; onAction: () => void; actionLabel?: string; alwaysAction?: boolean; disabled?: boolean }) {
  const meta = securityStateMeta[status];
  return <div className={`securityStateRow state-${status}`}><span className="securityStateMark">{meta.symbol}</span><p><strong>{title}</strong><small>{text}</small></p>{status === "active" && !alwaysAction ? <em className="securityStatePill">{meta.label}</em> : <button className="securityFixButton" onClick={onAction} disabled={disabled}>{actionLabel}</button>}</div>;
}
function CountryFlag({ code, label }: { code: string; label: string }) {
  const normalized = code.trim().toLowerCase();
  const horizontal: Record<string, [string, string, string]> = {
    de: ["#000000", "#dd0000", "#ffce00"], es: ["#aa151b", "#f1bf00", "#aa151b"],
    lv: ["#9e3039", "#ffffff", "#9e3039"], nl: ["#ae1c28", "#ffffff", "#21468b"],
    ru: ["#ffffff", "#1c57a7", "#d52b1e"],
  };
  let flag: React.ReactNode = null;
  if (horizontal[normalized]) {
    const stripes = horizontal[normalized];
    flag = <><rect width="27" height="18" fill={stripes[0]} />{normalized === "lv" ? <rect y="8" width="27" height="2" fill={stripes[1]} /> : normalized === "es" ? <rect y="4.5" width="27" height="9" fill={stripes[1]} /> : <><rect y="6" width="27" height="6" fill={stripes[1]} /><rect y="12" width="27" height="6" fill={stripes[2]} /></>}</>;
  } else if (normalized === "fi" || normalized === "se") {
    const background = normalized === "fi" ? "#ffffff" : "#006aa7";
    const cross = normalized === "fi" ? "#003580" : "#fecc00";
    flag = <><rect width="27" height="18" fill={background} /><rect x="8" width="3" height="18" fill={cross} /><rect y="7.5" width="27" height="3" fill={cross} /></>;
  } else if (normalized === "jp") {
    flag = <><rect width="27" height="18" fill="#ffffff" /><circle cx="13.5" cy="9" r="4.5" fill="#bc002d" /></>;
  } else if (normalized === "sg") {
    flag = <><rect width="27" height="9" fill="#ef3340" /><rect y="9" width="27" height="9" fill="#ffffff" /><circle cx="7" cy="4.7" r="3.1" fill="#ffffff" /><circle cx="8.2" cy="4.7" r="2.6" fill="#ef3340" />{[[10.6, 2.2], [12, 4], [11.5, 6.2], [9.5, 6.8], [9.2, 3.4]].map(([x, y]) => <circle key={`${x}-${y}`} cx={x} cy={y} r=".45" fill="#ffffff" />)}</>;
  } else if (normalized === "kz") {
    flag = <><rect width="27" height="18" fill="#00afca" /><path d="M3 1v16M5 1v16" stroke="#f6c600" strokeWidth=".7" strokeDasharray="1 1" /><circle cx="16" cy="6.5" r="2.2" fill="#f6c600" /><path d="M10 11.5q6 4 12 0-6 2-12 0Z" fill="#f6c600" /></>;
  } else if (normalized === "by") {
    flag = <><rect width="27" height="12" fill="#ce1720" /><rect y="12" width="27" height="6" fill="#007c30" /><rect width="4" height="18" fill="#ffffff" /><path d="M.5 1.5 3.5 4.5.5 7.5l3 3-3 3 3 3" stroke="#ce1720" strokeWidth="1" fill="none" /></>;
  } else if (normalized === "us") {
    flag = <><rect width="27" height="18" fill="#ffffff" />{[0, 4, 8, 12, 16].map((y) => <rect key={y} y={y} width="27" height="2" fill="#b22234" />)}<rect width="12" height="9.8" fill="#3c3b6e" />{[[2, 2], [5, 2], [8, 2], [3.5, 5], [6.5, 5], [9.5, 5], [2, 8], [5, 8], [8, 8]].map(([x, y]) => <circle key={`${x}-${y}`} cx={x} cy={y} r=".45" fill="#ffffff" />)}</>;
  }
  if (!flag) return <span className="gateCountryFlag unknown" role="img" aria-label={label}>◎</span>;
  return <span className="gateCountryFlag" role="img" aria-label={label}><svg viewBox="0 0 27 18" aria-hidden="true"><g clipPath="url(#country-flag-clip)">{flag}</g><defs><clipPath id="country-flag-clip"><rect width="27" height="18" rx="2" /></clipPath></defs></svg></span>;
}
function VersionFooter() {
  return <LegalFooter version={appVersion} branch={buildBranch} commit={buildCommit} />;
}
